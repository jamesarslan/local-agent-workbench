"""Build portable installer archives from explicitly reviewed Git objects."""

import argparse
import gzip
import hashlib
import io
import os
from pathlib import Path
import platform
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from urllib.request import urlopen
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "release/public-files.txt"
VERSION = "0.4.0"
REPOSITORY = "jamesarslan/local-agent-workbench"
INSTALLERS = ("install.sh", "install.ps1")
EXCLUDED = ("private/", "docs/launch-posts.md", "docs/.nojekyll")
GENERATED = ("VERSION", "python")
PYTHON = "3.12.16"
BUILD = "20261110"
TARGETS = {
    "windows-x64": (
        "x86_64-pc-windows-msvc",
        "e23fe5b16926de1d81661b1a97352e89fb167549c55a6825796ccd0f32ec7f1b",
    ),
    "linux-x64": (
        "x86_64-unknown-linux-gnu",
        "c2f87d4b4e8db3abddb0f116c6ccfc503d0cbdbb9ee8faacddad1b679ec12e99",
    ),
    "macos-arm64": (
        "aarch64-apple-darwin",
        "e956e60bc9465d577ef8062059aa05cd3a2665b4b8678a2ed24e432368330b8f",
    ),
}
PREFIX = "Agent-Workbench/"
REFERENCES = {
    ".html": (re.compile(r"""\b(?:src|href)\s*=\s*["']([^"']+)["']"""),),
    ".css": (
        re.compile(r"""@import\s+["']([^"']+)["']"""),
        re.compile(r"""\burl\(\s*["']?([^"')\s]+)"""),
    ),
    ".js": (
        re.compile(r"""\brequire\(\s*["'](\.\.?/[^"']+)["']\s*\)"""),
        re.compile(r"""\bimport\s*(?:[^"';]*?\bfrom\s*)?\(?\s*["'](\.\.?/[^"']+)["']"""),
    ),
}
REFERENCES[".cjs"] = REFERENCES[".mjs"] = REFERENCES[".js"]
SCANNED = (*REFERENCES, ".py")
PYTHON_FROM = re.compile(r"^[ \t]*from[ \t]+([A-Za-z_]\w*)[\w.]*[ \t]+import\b", re.M)
PYTHON_NAME = r"[A-Za-z_][\w.]*(?:[ \t]+as[ \t]+\w+)?"
PYTHON_IMPORT = re.compile(
    rf"^[ \t]*import[ \t]+({PYTHON_NAME}(?:[ \t]*,[ \t]*{PYTHON_NAME})*)", re.M
)


def git(*args, env=None):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], env=env)


def excluded(path):
    return any(path == e or (e.endswith("/") and path.startswith(e)) for e in EXCLUDED)


def hidden(path):
    """True when any part of path starts with a dot, apart from .github/ and the two Git attribute files."""
    parts = path.split("/")
    if parts[0] == ".github" or path in (".gitignore", ".gitattributes"):
        parts = parts[1:]
    return any(p.startswith(".") for p in parts)


def manifest_errors(paths):
    """Return one sentence for each problem in a release file list."""
    errors = []
    if not paths or paths != sorted(set(paths)):
        errors.append(
            f"{MANIFEST} must be sorted by code point (LC_ALL=C sort) and hold each path once."
        )
    if ".gitattributes" not in paths:
        errors.append(
            f"{MANIFEST} must list .gitattributes; it gives the Windows scripts their CRLF line endings."
        )
    for p in paths:
        if excluded(p):
            errors.append(f"{p} is private and must not be listed in {MANIFEST}.")
        elif p.split("/")[0] in GENERATED:
            errors.append(f"{p} is written by the builder and must not be listed in {MANIFEST}.")
        elif hidden(p):
            errors.append(f"{p} is a hidden path and must not be listed in {MANIFEST}.")
        elif (
            not p
            or p.startswith("/")
            or "\\" in p
            or posixpath.normpath(p) != p
            or ".." in p.split("/")
        ):
            errors.append(f"{p!r} is not a plain relative file path; list one tracked file per line.")
    return errors


def tree_files():
    """Return every file in the checkout that Git does not ignore, minus EXCLUDED, sorted by code point."""
    listing = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted(
        {
            p
            for p in listing.decode().split("\0")
            if p and (ROOT / p).is_file() and not excluded(p)
        }
    )


def write_manifest(path=None):
    """Write the file list from the checkout; return the paths added and removed."""
    path = path or ROOT / MANIFEST
    paths = tree_files()
    errors = manifest_errors(paths)
    if errors:
        raise ValueError(
            " ".join(errors) + " Add a file that must stay private to EXCLUDED in release/build.py."
        )
    old = path.read_text(encoding="utf8").splitlines() if path.exists() else []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(p + "\n" for p in paths), encoding="utf8", newline="\n")
    return sorted(set(paths) - set(old)), sorted(set(old) - set(paths))


def python_imports(source):
    """Return the top-level module names a Python file imports by absolute name, at any indentation."""
    text = source.decode("utf8", "replace")
    found = set(PYTHON_FROM.findall(text))
    for names in PYTHON_IMPORT.findall(text):
        found.update(n.split()[0].split(".")[0] for n in names.split(","))
    return found


def dependency_errors(files, shipped, known):
    """Return one sentence for each import or file reference in files (path -> bytes) that shipped does not hold.

    A Python import counts only when it names a module in known (the files of the checkout); a script, style or
    page reference counts whenever it is a relative path.
    """
    errors = []
    for name in sorted(files):
        folder, suffix = posixpath.dirname(name), posixpath.splitext(name)[1]
        if suffix == ".py":
            for module in sorted(python_imports(files[name])):
                bases = dict.fromkeys((folder, ""))
                options = [posixpath.join(b, module + ".py") for b in bases]
                options += [posixpath.join(b, module, "__init__.py") for b in bases]
                if not any(o in shipped for o in options) and any(o in known for o in options):
                    found = next(o for o in options if o in known)
                    errors.append(f"{name} imports {module}, but {found} is not listed in {MANIFEST}.")
            continue
        text = files[name].decode("utf8", "replace")
        for pattern in REFERENCES.get(suffix, ()):
            for ref in sorted(set(pattern.findall(text))):
                ref = ref.split("#")[0].split("?")[0]
                if not ref or ref.startswith("/") or ":" in ref:
                    continue
                target = posixpath.normpath(posixpath.join(folder, ref))
                options = [target]
                if suffix in (".js", ".cjs", ".mjs"):
                    options += [target + e for e in (".js", ".cjs", ".json")] + [target + "/index.js"]
                if not any(o in shipped for o in options):
                    errors.append(f"{name} refers to {ref}, but {target} is not listed in {MANIFEST}.")
    return errors


def snapshot(paths, scratch):
    """Store the listed files as they are on disk in a throwaway object folder; return the tree and Git environment."""
    objects = git("rev-parse", "--path-format=absolute", "--git-path", "objects").decode().strip()
    env = dict(
        os.environ,
        GIT_INDEX_FILE=str(scratch / "index"),
        GIT_OBJECT_DIRECTORY=str(scratch / "objects"),
        GIT_ALTERNATE_OBJECT_DIRECTORIES=objects,
    )
    (scratch / "objects").mkdir()
    git("-c", "core.safecrlf=false", "add", "--", *paths, env=env)
    return git("write-tree", env=env).decode().strip(), env


def archive(tree, paths, env=None):
    """Return the listed files of a commit or tree as a tar, with LF text and modes 644 or 755 whatever Git config says."""
    return git(
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.eol=lf",
        "-c",
        "tar.umask=022",
        "archive",
        "--format=tar",
        tree,
        "--",
        *paths,
        env=env,
    )


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def fetch(target, cache):
    triple, expected = TARGETS[target]
    name = f"cpython-{PYTHON}+{BUILD}-{triple}-install_only.tar.gz"
    path = cache / name
    if not path.exists():
        url = f"https://github.com/astral-sh/python-build-standalone/releases/download/{BUILD}/{name}"
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as tmp:
            part = Path(tmp.name)
        try:
            with urlopen(url, timeout=90) as source, part.open("wb") as out:
                shutil.copyfileobj(source, out)
            if digest(part) != expected:
                raise ValueError(f"Python checksum mismatch for {target}")
            part.replace(path)
        finally:
            part.unlink(missing_ok=True)
    if digest(path) != expected:
        path.unlink()
        raise ValueError(
            f"The cached Python archive for {target} failed its checksum and was deleted; run the build again."
        )
    return path


def members(archive, runtime=False):
    for entry in archive:
        name = entry.name
        if (
            posixpath.normpath(name) != name
            or name.startswith(("/", "../"))
            or "\\" in name
        ):
            raise ValueError(f"Unexpected archive path {name!r}")
        if runtime and name.split("/")[0] != "python":
            raise ValueError(f"Unexpected runtime path {name!r}")
        if entry.isdir():
            continue
        if entry.issym() and runtime:
            dest = posixpath.normpath(
                posixpath.join(posixpath.dirname(name), entry.linkname)
            )
            if not dest.startswith("python/"):
                raise ValueError(f"Unexpected link in runtime: {name}")
        elif not entry.isfile():
            raise ValueError(f"Unexpected entry type for {name}")
        yield entry


def entry(name, mode, stamp):
    z = zipfile.ZipInfo(name, date_time=time.gmtime(stamp)[:6])
    z.create_system = 3
    z.compress_type = zipfile.ZIP_DEFLATED
    z.external_attr = (0o100000 | mode) << 16
    return z


def package(target, out, sources, runtime, stamp, extra):
    """Write one archive: reviewed sources, builder-written files in extra, and the Python runtime if given."""
    name = f"agent-workbench-{target}"
    with tarfile.open(fileobj=io.BytesIO(sources)) as src:
        archives = [(src, False)]
        if runtime is not None:
            rt = tarfile.open(runtime)
            archives.append((rt, True))
        try:
            if target == "windows-x64":
                path = out / (name + ".zip")
                with zipfile.ZipFile(
                    path, "w", zipfile.ZIP_DEFLATED, compresslevel=6
                ) as dest:
                    for arc, bundled in archives:
                        for e in members(arc, bundled):
                            z = entry(PREFIX + e.name, e.mode, stamp)
                            with (
                                arc.extractfile(e) as reader,
                                dest.open(z, "w") as writer,
                            ):
                                shutil.copyfileobj(reader, writer)
                    for member, data in extra.items():
                        dest.writestr(entry(PREFIX + member, 0o644, stamp), data)
            else:
                path = out / (name + ".tar.gz")
                with (
                    path.open("wb") as raw,
                    gzip.GzipFile(
                        fileobj=raw, mode="wb", filename="", mtime=0, compresslevel=6
                    ) as gz,
                    tarfile.open(fileobj=gz, mode="w|") as dest,
                ):
                    for arc, bundled in archives:
                        for e in members(arc, bundled):
                            reader = arc.extractfile(e) if e.isfile() else None
                            e.name = PREFIX + e.name
                            e.uid = e.gid = 0
                            e.mtime = stamp
                            e.uname = e.gname = ""
                            e.pax_headers = {}
                            dest.addfile(e, reader)
                            if reader:
                                reader.close()
                    for member, data in extra.items():
                        e = tarfile.TarInfo(PREFIX + member)
                        e.size, e.mode, e.mtime = len(data), 0o644, stamp
                        dest.addfile(e, io.BytesIO(data))
        finally:
            if runtime is not None:
                rt.close()
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--cache", type=Path, default=ROOT / ".local/release-cache")
    ap.add_argument(
        "--target",
        action="append",
        choices=list(TARGETS),
        help="build only this device kit (repeatable); the default is all of them",
    )
    ap.add_argument(
        "--worktree",
        action="store_true",
        help="package the listed files as they are on disk now, for local testing; never publish the result",
    )
    ap.add_argument(
        "--write-manifest",
        action="store_true",
        help=f"rewrite {MANIFEST} from the files Git does not ignore, minus EXCLUDED, and build nothing",
    )
    args = ap.parse_args()
    if args.write_manifest:
        try:
            added, removed = write_manifest()
        except (ValueError, subprocess.CalledProcessError) as exc:
            ap.error(str(exc))
        print("".join(f"+ {p}\n" for p in added) + "".join(f"- {p}\n" for p in removed), end="")
        paths = (ROOT / MANIFEST).read_text(encoding="utf8").splitlines()
        print(
            f"{MANIFEST} lists {len(paths)} files ({len(added)} added, {len(removed)} removed). "
            "Read every added line before committing it."
        )
        files = {p: (ROOT / p).read_bytes() for p in paths if p.endswith(SCANNED)}
        problems = dependency_errors(files, set(paths), set(paths))
        if problems:
            ap.exit(1, "\n".join(problems) + "\n")
        return
    if args.output is None:
        ap.error("the following arguments are required: --output")
    with tempfile.TemporaryDirectory() as scratch:
        if args.worktree:
            paths = (ROOT / MANIFEST).read_text(encoding="utf8").splitlines()
            label, env = "worktree", None
        else:
            label = git("rev-parse", "--verify", args.ref + "^{commit}").decode().strip()
            paths = git("show", f"{label}:{MANIFEST}").decode().splitlines()
            tree, env = label, None
        errors = manifest_errors(paths)
        if errors:
            ap.error(" ".join(errors))
        if args.worktree:
            try:
                tree, env = snapshot(paths, Path(scratch))
            except subprocess.CalledProcessError:
                ap.error(
                    f"A file listed in {MANIFEST} is missing on disk or ignored by Git; see the Git message above."
                )
            known = set(tree_files())
        else:
            known = set(git("ls-tree", "-r", "-z", "--name-only", label).decode().split("\0"))
        stamp = int(git("show", "-s", "--format=%ct", "HEAD" if args.worktree else label))
        sources = archive(tree, paths, env)
    with tarfile.open(fileobj=io.BytesIO(sources)) as arc:
        if {e.name for e in members(arc)} != set(paths):
            ap.error(
                "Review individual tracked files. Directory and wildcard entries are forbidden."
            )
        files = {
            e.name: arc.extractfile(e).read()
            for e in arc.getmembers()
            if e.isfile() and e.name.endswith(SCANNED)
        }
        installers = {
            name: arc.extractfile(name).read() for name in INSTALLERS if name in paths
        }
    problems = dependency_errors(files, set(paths), known)
    if problems:
        ap.error(" ".join(problems))
    extra = {"VERSION": f"{VERSION}\n{label}\n".encode()}
    args.cache.mkdir(parents=True, exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        ap.error("Choose an empty output folder.")
    # Build the complete set off to the side so partial downloads never appear as releases.
    with tempfile.TemporaryDirectory(dir=args.output.parent) as tmp:
        staging = Path(tmp)
        artifacts = [package("source", staging, sources, None, stamp, extra)]
        for target in args.target or TARGETS:
            artifacts.append(
                package(target, staging, sources, fetch(target, args.cache), stamp, extra)
            )
        for name, data in installers.items():
            (staging / name).write_bytes(data)
            artifacts.append(staging / name)
        (staging / "SHA256SUMS").write_text(
            "".join(f"{digest(p)}  {p.name}\n" for p in artifacts),
            encoding="ascii",
            newline="\n",
        )
        for path in sorted(staging.iterdir()):
            shutil.move(path, args.output / path.name)
    print(
        f"Packaged {len(paths)} reviewed source files from {label} as Agent Workbench {VERSION} in {args.output}"
    )
    print(
        f"Built with Python {platform.python_version()} and zlib {zlib.ZLIB_RUNTIME_VERSION} on {platform.system()} {platform.machine()}."
    )
    if args.worktree:
        print("This is a working-tree build for local testing. Do not publish it.")


if __name__ == "__main__":
    main()
