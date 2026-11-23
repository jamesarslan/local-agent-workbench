"""Launch the pinned Electron desktop with no Node or npm prerequisite."""

import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

from filelock import FileLock
import requests
from runtime import DATA, ROOT

DESKTOP = ROOT / "desktop"
STARTUP_SECONDS = 20
# SHA-256 of the release archives, from the electron npm package's checksums.json for the same version.
ELECTRON_SHA256 = {
    "45.4.1": {
        "darwin-arm64": "d080c3b05a01bd16137f2c12ab9ebf083c577757fe846844ba325bafa8e8bdf4",
        "linux-x64": "e1d077c7a087d7e344350ea0c5cd2b7dc5af3d9bf7979474f38e0fc1a560b875",
        "win32-x64": "2ba6223afa9121f42831fefe92c434334907540165294fc56697449d12e7cfe9",
    }
}
USERNS = (
    ("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", "1"),
    ("/proc/sys/kernel/unprivileged_userns_clone", "0"),
    ("/proc/sys/user/max_user_namespaces", "0"),
)
APPARMOR = Path("/etc/apparmor.d")
OFFLINE = (
    "Agent Workbench could not download its desktop runtime from github.com. "
    "Check your internet connection and any proxy, then start it again."
)


def executable(progress=None):
    version = json.loads((DESKTOP / "package.json").read_text())["dependencies"][
        "electron"
    ]
    machine = platform.machine().lower()
    target = sys.platform
    if target == "darwin" and machine == "arm64":
        arch, leaf = "arm64", "Electron.app/Contents/MacOS/Electron"
    elif target in ("win32", "linux") and machine in ("amd64", "x86_64"):
        arch, leaf = "x64", "electron.exe" if target == "win32" else "electron"
    else:
        raise ValueError(
            "Choose x64 Windows or Linux, or native arm64 Python on Apple Silicon."
        )
    directory = DATA / "desktop-runtime" / (target + "-" + version)
    dev = DESKTOP / "node_modules/electron/dist"
    if (
        (dev / leaf).is_file()
        and (dev / "version").is_file()
        and (dev / "version").read_text().strip().lstrip("v") == version
    ):
        return dev / leaf
    directory.parent.mkdir(exist_ok=True)
    with FileLock(str(directory) + ".lock", timeout=300):
        if (directory / leaf).is_file():
            return directory / leaf
        expected = ELECTRON_SHA256.get(version, {}).get(f"{target}-{arch}")
        if not expected:
            raise ValueError(
                f"Electron {version} has no pinned checksum. Add its SHA-256 values to ELECTRON_SHA256 in desktop_app.py, then start again."
            )
        archive = f"electron-v{version}-{target}-{arch}.zip"
        base = f"https://github.com/electron/electron/releases/download/v{version}/"
        with tempfile.TemporaryDirectory(dir=directory.parent) as tmp:
            pack = Path(tmp) / "electron.zip"
            digest = hashlib.sha256()
            try:
                with (
                    requests.get(base + archive, stream=True, timeout=(15, 120)) as r,
                    pack.open("wb") as out,
                ):
                    r.raise_for_status()
                    total = int(r.headers.get("Content-Length", 0))
                    downloaded = 0
                    for chunk in r.iter_content(1 << 20):
                        out.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        if progress:
                            progress(downloaded, total)
            except requests.RequestException:
                raise ValueError(OFFLINE) from None
            if digest.hexdigest() != expected:
                raise ValueError(
                    "Electron download failed its release checksum. Relaunch to retry."
                )
            stage = Path(tmp) / "runtime"
            if target == "darwin":
                # Framework bundles require symlinks and executable mode bits.
                subprocess.run(
                    ["/usr/bin/ditto", "-x", "-k", str(pack), str(stage)], check=True
                )
            else:
                with zipfile.ZipFile(pack) as z:
                    z.extractall(stage)
                    if target == "linux":
                        for info in z.infolist():
                            if info.external_attr >> 16 & 0o111:
                                (stage / info.filename).chmod(0o755)
            if not (stage / leaf).is_file():
                raise ValueError(
                    "The Electron archive is missing its desktop executable."
                )
            if directory.exists():
                try:
                    shutil.rmtree(directory)
                except OSError:
                    raise ValueError(
                        f"An incomplete desktop runtime is in the way. Delete the folder {directory}, then start Agent Workbench again."
                    ) from None
            stage.rename(directory)
    return directory / leaf


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def _helper_ready(helper):
    try:
        info = helper.stat()
    except OSError:
        return None
    return info.st_uid == 0 and bool(info.st_mode & 0o4000)


def _nosuid(folder):
    try:
        return bool(os.statvfs(folder).f_flag & os.ST_NOSUID)
    except (AttributeError, OSError):
        return False


def _profiled(folder):
    """True when an AppArmor profile names this runtime folder or any runtime version under the data folder."""
    wanted = (str(folder), str(DATA / "desktop-runtime") + "/")
    try:
        files = [f for f in APPARMOR.iterdir() if f.is_file()]
    except OSError:
        return False
    for f in files:
        try:
            if f.stat().st_size > 65536:
                continue
            text = f.read_text(errors="replace")
        except OSError:
            continue
        if any(needle in text for needle in wanted):
            return True
    return False


def sandbox_problem(engine, system=None):
    """One sentence with the exact fix when this Linux system will refuse Electron's sandbox, else ''."""
    if (system or sys.platform) != "linux":
        return ""
    if not any(_read(path) == value for path, value in USERNS):
        return ""
    folder = Path(engine).parent
    helper = folder / "chrome-sandbox"
    ready = _helper_ready(helper)
    if ready is None or _profiled(folder):
        return ""
    if _nosuid(folder):
        return (
            f"Linux is blocking the desktop sandbox, and {folder} is on a disk mounted nosuid where the fix cannot work. "
            "Set WORKBENCH_DATA to a folder on another disk, then start Agent Workbench again."
        )
    if ready:
        return ""
    quoted = shlex.quote(str(helper))
    return (
        "Linux is blocking the desktop sandbox on this system. Run this once in a terminal, then start Agent Workbench again: "
        f"sudo chown root:root {quoted} && sudo chmod 4755 {quoted}"
    )


def exit_text(code):
    how = f"signal {-code}" if code < 0 else f"exit code {code}"
    return f"The desktop window closed unexpectedly ({how}). Start Agent Workbench again; if it repeats, run cli.py doctor in the Agent Workbench folder."


def launch(engine):
    env = dict(os.environ, WORKBENCH_PYTHON=sys.executable, PYTHONUTF8="1")
    env.pop("ELECTRON_RUN_AS_NODE", None)
    if (
        sys.platform == "linux"
        and env.get("WAYLAND_DISPLAY")
        and "ELECTRON_OZONE_PLATFORM_HINT" not in env
    ):
        env["ELECTRON_OZONE_PLATFORM_HINT"] = "auto"
    started = time.monotonic()
    code = subprocess.run([str(engine), str(DESKTOP)], env=env).returncode
    if code == 0:
        return 0
    if time.monotonic() - started < STARTUP_SECONDS:
        raise ValueError(sandbox_problem(engine) or exit_text(code))
    print(exit_text(code), file=sys.stderr, flush=True)
    return code


def prepare():
    """Download the desktop runtime without opening a window; exit status 3 means the Linux sandbox needs the printed fix."""
    shown = [0.0]

    def report(current, total):
        if time.monotonic() - shown[0] >= 1:
            shown[0] = time.monotonic()
            print(f"Downloading the desktop runtime: {current // 1048576} MiB", file=sys.stderr, flush=True)

    engine = executable(report)
    print(engine, flush=True)
    problem = sandbox_problem(engine)
    if problem:
        print(problem, file=sys.stderr, flush=True)
        return 3
    return 0


def main(argv=None):
    import queue
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--prepare"]:
        return prepare()
    events = queue.SimpleQueue()
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(
            executable, lambda current, total: events.put((current, total))
        )
        try:
            engine = future.result(timeout=0.45)
        except TimeoutError:
            try:
                import tkinter as tk
                from tkinter import ttk
            except ImportError:
                print("Preparing the desktop runtime…", flush=True)
                return launch(future.result())

            splash = tk.Tk()
            splash.title("Preparing Agent Workbench")
            frame = ttk.Frame(splash, padding=20)
            frame.pack()
            note = tk.StringVar(value="Preparing the desktop runtime…")
            ttk.Label(frame, textvariable=note).pack(anchor="w")
            bar = ttk.Progressbar(frame, length=380)
            bar.pack(pady=(12, 0))

            def update():
                while not events.empty():
                    current, total = events.get()
                    bar["value"] = 100 * current / total if total else 0
                    note.set(
                        f"Downloading the desktop runtime · {current // 1048576} MiB"
                    )
                if future.done():
                    splash.destroy()
                else:
                    splash.after(100, update)

            splash.after(0, update)
            splash.mainloop()
            engine = future.result()
    return launch(engine)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as err:
        print(str(err), file=sys.stderr, flush=True)
        if sys.argv[1:] == ["--prepare"]:
            raise SystemExit(1) from None
        try:
            from tkinter import messagebox
        except ImportError:
            raise SystemExit(1) from None

        messagebox.showerror(
            "Agent Workbench", str(err) + "\nThe classic interface will open."
        )
        subprocess.run([sys.executable, str(ROOT / "workbench.py")], check=True)
