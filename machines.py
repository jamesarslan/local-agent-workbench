"""Workbench-managed SSH identities, Linux workers and loopback forwards."""

import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import time

import psutil
from filelock import FileLock
from host import terminal
from runtime import DATA, ROOT, QUIET, load, write_json

HOSTS = DATA / "machines.json"
LINKS = DATA / "machine-links.json"
NODE_PORT = 10542
PROTOCOLS = ("chat", "responses", "messages")
REMOTE_CLI = {
    "OMP": "omp",
    "Codex": "codex",
    "Claude Code": "claude",
    "Hermes": "hermes",
    "Pi": "pi",
    "opencode": "opencode",
}
LOGIN = 'exec "${SHELL:-/bin/sh}" -l'
NO_SSH = (
    "OpenSSH was not found on this computer; install the OpenSSH client, then retry."
)
REVOKE = """from pathlib import Path
import fcntl, os, sys, tempfile
path = Path.home() / ".ssh/authorized_keys"
if path.is_symlink():
    sys.exit("The authorized_keys file is a symlink. Revoke this key manually.")
if path.exists():
    with path.open("rb") as original:
        fcntl.flock(original, fcntl.LOCK_EX)
        previous = original.read()
        key = sys.argv[1].encode()
        current = b"".join(line for line in previous.splitlines(True) if line.strip() != key)
        if current != previous:
            with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as dest:
                try:
                    dest.write(current)
                    dest.flush()
                    os.fsync(dest.fileno())
                    if path.stat().st_ino != os.fstat(original.fileno()).st_ino or path.read_bytes() != previous:
                        sys.exit("SSH keys changed concurrently. Retry.")
                    os.replace(dest.name,path)
                finally:
                    Path(dest.name).unlink(missing_ok=True)
"""


class Unreachable(ValueError):
    """SSH itself failed, so nothing ran on the machine."""


def target(value):
    if not re.fullmatch(r"(?:[a-zA-Z0-9_][\w.-]*@)?[a-zA-Z0-9][\w.-]*", value):
        raise ValueError("Use an SSH config alias or user@hostname.")
    return value


def labels():
    return load(HOSTS, {})


def save(label, host):
    host = target(host.strip())
    with FileLock(str(HOSTS) + ".lock"):
        rows = labels()
        rows[host] = label.strip()[:80] or host
        write_json(HOSTS, rows)
    return host


def identifier(host):
    return hashlib.sha256(target(host).encode()).hexdigest()[:16]


def identity(host):
    path = DATA / "ssh" / identifier(host)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    from local_permissions import protect

    protect(path)
    return path / "workbench_ed25519"


def ssh(host, interactive=False):
    key = identity(host)
    args = ["ssh", "-o", "ConnectTimeout=7", "-o", "ServerAliveInterval=20"]
    if not interactive:
        args += ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes"]
    if key.is_file():
        args += ["-i", str(key)]
    return args


def pairing_line(host):
    key = identity(host)
    with FileLock(str(key) + ".lock"):
        if not key.exists():
            try:
                subprocess.run(
                    [
                        "ssh-keygen",
                        "-q",
                        "-t",
                        "ed25519",
                        "-N",
                        "",
                        "-C",
                        "agent-workbench-" + key.parent.name,
                        "-f",
                        str(key),
                    ],
                    check=True,
                    stdin=subprocess.DEVNULL,
                    creationflags=QUIET,
                )
            except FileNotFoundError:
                raise ValueError(NO_SSH) from None
    pub = key.with_suffix(".pub").read_text().strip()
    value = shlex.quote(pub)
    return (
        "umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && (grep -qxF "
        + value
        + ' ~/.ssh/authorized_keys || { [ -z "$(tail -c1 ~/.ssh/authorized_keys)" ]'
        + " || echo >> ~/.ssh/authorized_keys; printf '%s\\n' "
        + value
        + " >> ~/.ssh/authorized_keys; })"
    )


def explain(host, code, stderr):
    # OpenSSH 10.1+ prefixes its own key-exchange notice with "** " on every connection to an older server.
    text = "\n".join(
        line for line in (stderr or "").splitlines() if not line.startswith("** ")
    ).strip()
    if code != 255:
        if code == 127 or "command not found" in text or "python3: not found" in text:
            return f"python3 was not found on {host}; install Python 3.8 or newer there, then retry."
        if "xcode-select" in text:
            return f"python3 is not installed on {host}; run xcode-select --install on that Mac, then retry."
        if "SyntaxError" in text:
            return f"The python3 on {host} is older than 3.8; install Python 3.8 or newer there, then retry."
        return (
            text[-650:]
            or f"The command on {host} failed without a message; use Check connection, then retry."
        )
    forwarding = f"SSH could not open the local forward for {host}; close other SSH sessions that forward ports for this host, then retry."
    silent = f"{host} did not answer; check that it is switched on and reachable from this network, then retry."
    for needle, message in (
        (
            "REMOTE HOST IDENTIFICATION HAS CHANGED",
            f"The host key of {host} has changed; if that machine was reinstalled, remove its old line from your known_hosts file and pair again.",
        ),
        (
            "Host key verification failed",
            f"This computer has not accepted the host key of {host} yet; use Pair through SSH once, then retry.",
        ),
        (
            "Too many authentication failures",
            f"{host} closed the login after too many keys were offered; add IdentitiesOnly yes for this host to your SSH config, then retry.",
        ),
        (
            "Permission denied (",
            f"{host} refused the login; pair this machine again, then retry.",
        ),
        (
            "Could not resolve hostname",
            f"The name {host} could not be found; check the SSH alias or host name, then retry.",
        ),
        (
            "Connection refused",
            f"{host} refused the SSH connection; check that its SSH server is running, then retry.",
        ),
        ("Address already in use", forwarding),
        ("Could not request local forwarding", forwarding),
        ("timed out", silent),
        ("No route to host", silent),
        ("Network is unreachable", silent),
    ):
        if needle in text:
            return message
    last = text.splitlines()[-1].strip()[:300] if text else ""
    return (
        f"SSH to {host} failed"
        + (f" ({last})" if last else "")
        + "; check pairing, the host key and the SSH port, then retry."
    )


def _ssh_run(host, tail, source=None, timeout=90):
    feed = dict(stdin=subprocess.DEVNULL) if source is None else dict(input=source)
    try:
        result = subprocess.run(
            ssh(host) + [host, *tail],
            capture_output=True,
            timeout=timeout,
            creationflags=QUIET,
            **feed,
        )
    except FileNotFoundError:
        raise ValueError(NO_SSH) from None
    except subprocess.TimeoutExpired:
        raise Unreachable(
            f"{host} did not finish within {timeout} seconds; check the connection and the machine, then retry."
        ) from None
    if result.returncode:
        text = explain(host, result.returncode, result.stderr.decode(errors="replace"))
        raise (Unreachable if result.returncode == 255 else ValueError)(text)
    return result.stdout


def execute(host, source, command, timeout=90):
    out = _ssh_run(host, [command], source, timeout).decode(errors="replace")
    lines = [line for line in out.splitlines() if line.strip()]
    try:
        return json.loads(lines[-1])
    except (IndexError, ValueError, RecursionError):
        raise ValueError(
            f"{host} sent a reply Workbench could not read; remove any text its shell startup files print for non-interactive logins, then retry."
        ) from None


def run(host, action, timeout=90):
    return execute(
        host, (ROOT / "remote_node.py").read_bytes(), "python3 - " + action, timeout
    )


def _load(path):
    try:
        rows = load(path, {})
    except (OSError, ValueError):
        return {}
    return rows if isinstance(rows, dict) else {}


def _port(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 65536


def _linked(row):
    try:
        return (
            isinstance(row, dict)
            and bool(target(row["host"]))
            and _port(row["remote_port"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def links():
    return {ident: row for ident, row in _load(LINKS).items() if _linked(row)}


def link_id(host, remote_port):
    return hashlib.sha256(f"{target(host)}:{int(remote_port)}".encode()).hexdigest()[
        :16
    ]


def _slot(remote_port):
    remote_port = int(remote_port)
    return "tunnel.json" if remote_port == NODE_PORT else f"tunnel-{remote_port}.json"


def _record(link):
    return "tunnel.json" if link.get("managed") else _slot(link["remote_port"])


def _link(ident, row):
    with FileLock(str(LINKS) + ".lock"):
        rows = links()
        if row is None:
            rows.pop(ident, None)
        else:
            rows[ident] = row
        write_json(LINKS, rows)


def owned_tunnel(rec):
    try:
        p = psutil.Process(rec["pid"])
        if (
            abs(p.create_time() - rec["born"]) < 0.02
            and p.name().lower() in ("ssh.exe", "ssh")
            and p.status() != psutil.STATUS_ZOMBIE
        ):
            return p
    except (KeyError, TypeError, ValueError, OverflowError, psutil.Error):
        pass


def _free_port(preferred=None):
    for candidate in (preferred, 0):
        if isinstance(candidate, bool) or not isinstance(candidate, int):
            continue
        if candidate and not 1024 <= candidate <= 65535:
            continue
        try:
            with socket.socket() as s:
                if os.name != "nt":
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", candidate))
                return s.getsockname()[1]
        except OSError:
            continue
    raise ValueError(
        "This computer has no free loopback port; close some programs, then retry."
    )


def _listening(pid, port):
    if os.name == "nt":
        return None
    try:
        return any(
            c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port
            for c in psutil.Process(pid).net_connections(kind="tcp")
        )
    except (psutil.Error, OSError):
        return None


def _accepts(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _end(proc):
    try:
        proc.terminate()
        proc.wait(3)
    except psutil.TimeoutExpired:
        try:
            proc.kill()
        except psutil.Error:
            pass
    except psutil.Error:
        pass


def _log(path):
    try:
        return path.with_suffix(".log").read_text(errors="replace")
    except OSError:
        return ""


def forward(host, remote_port, record=None):
    """Open or reuse the loopback forward to one port on the machine's own loopback."""
    remote_port = int(remote_port)
    path = identity(host).parent / (record or _slot(remote_port))
    with FileLock(str(path) + ".lock"):
        rec = _load(path)
        live = owned_tunnel(rec)
        if (
            live
            and rec.get("remote", remote_port) == remote_port
            and _port(rec.get("port"))
        ):
            return rec["port"]
        if live:
            _end(live)
        port = _free_port(rec.get("port"))
        idle = dict(port=port, remote=remote_port)
        try:
            with path.with_suffix(".log").open("w") as out:
                child = subprocess.Popen(
                    ssh(host)
                    + [
                        "-NT",
                        "-o",
                        "ExitOnForwardFailure=yes",
                        "-L",
                        f"127.0.0.1:{port}:127.0.0.1:{remote_port}",
                        host,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=out,
                    creationflags=QUIET,
                )
        except FileNotFoundError:
            raise ValueError(NO_SSH) from None
        try:
            born = psutil.Process(child.pid).create_time()
        except psutil.Error:
            born = 0
        write_json(path, dict(idle, pid=child.pid, born=born))
        for _ in range(50):
            if child.poll() is not None:
                write_json(path, idle)
                raise Unreachable(explain(host, 255, _log(path)))
            state = _listening(child.pid, port)
            # A connect check alone cannot tell ssh's listener from another program's, so ssh must still be alive after it.
            if state or (state is None and _accepts(port) and child.poll() is None):
                return port
            time.sleep(0.2)
        child.terminate()
        try:
            child.wait(3)
        except subprocess.TimeoutExpired:
            child.kill()
        write_json(path, idle)
        raise Unreachable(
            f"The SSH forward to {host} did not open within 10 seconds; check the connection, then retry."
        )


def disconnect(host, record=None):
    folder = identity(host).parent
    for path in [folder / record] if record else sorted(folder.glob("tunnel*.json")):
        with FileLock(str(path) + ".lock"):
            rec = _load(path)
            if p := owned_tunnel(rec):
                _end(p)
            if _port(rec.get("port")):
                write_json(path, {k: rec[k] for k in ("port", "remote") if k in rec})
            else:
                path.unlink(missing_ok=True)


def _close(host, record):
    disconnect(host, record)
    path = identity(host).parent / record
    path.unlink(missing_ok=True)
    try:
        path.with_suffix(".log").unlink(missing_ok=True)
    except OSError:
        pass


def drop_forward(host, remote_port):
    """Close the forward to one attached port and forget its link; the saved row is the caller's."""
    host = target(host)
    _close(host, _slot(remote_port))
    _link(link_id(host, remote_port), None)


def _http():
    import requests

    session = requests.Session()
    session.trust_env = False
    return session


def _answers(addr):
    import requests

    try:
        _http().get(addr + "/models", timeout=4, allow_redirects=False)
        return True
    except requests.RequestException:
        return False


def _flags(found):
    if not isinstance(found, dict):
        return None
    flags = {name: found.get(name) for name in PROTOCOLS}
    values = list(flags.values())
    if any(isinstance(v, bool) for v in values) and all(
        v is None or isinstance(v, bool) for v in values
    ):
        return flags
    return None


def _protocols(base, token=""):
    try:
        import endpoints

        check = getattr(endpoints, "probe_protocols", None)
        if check:
            return _flags(check(base, token))
        return _flags(endpoints.probe(base, token)["protocols"])
    except Exception:
        return None


def _harness(protocols):
    names = dict(zip(PROTOCOLS, ("OMP", "Codex", "Claude Code")))
    if not protocols:
        return "OMP"
    for wanted in (True, None):
        for name, harness in names.items():
            if protocols[name] is wanted:
                return harness
    return "Claude Code"


def _publish(account, token=None):
    import connections
    from account_book import entries, save as save_account

    previous = entries().get(account["connection_id"])
    moved = bool(
        previous
        and previous.get("provider") == "Attached endpoint"
        and connections.vault_slot(previous) != connections.vault_slot(account)
    )
    if token is None and moved:
        try:
            token = connections.secret(previous)
        except ValueError:
            token = ""
    if token:
        connections.store_key(account, token)
    row = save_account(account, allow_endpoint_change=True)
    if moved:
        try:
            connections.forget_key(previous)
        except Exception:
            pass
    return row


def _remember(host):
    if host not in labels():
        save("", host)


def tunnel(host):
    import requests
    from account_book import entries

    ident = identifier(host)
    _remember(host)
    remote = run(host, "start", 240)
    token = run(host, "key")
    if (
        not isinstance(remote, dict)
        or not _port(remote.get("port"))
        or not isinstance(token, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{16,200}", token)
    ):
        raise ValueError(
            f"{host} sent a reply Workbench could not read; use Check connection, then retry."
        )
    port = forward(host, remote["port"], "tunnel.json")
    addr = f"http://127.0.0.1:{port}/v1"
    path = identity(host).parent / "tunnel.json"
    http = _http()
    for _ in range(25):
        if not owned_tunnel(_load(path)):
            disconnect(host, "tunnel.json")
            raise Unreachable(explain(host, 255, _log(path)))
        try:
            r = http.get(
                addr + "/models",
                headers={"Authorization": "Bearer " + token},
                timeout=2,
                allow_redirects=False,
            )
            r.raise_for_status()
            if r.json()["data"][0]["id"] == "local-agent":
                break
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            pass
        time.sleep(0.2)
    else:
        disconnect(host, "tunnel.json")
        raise ValueError(
            f"The model on {host} did not answer through the SSH forward; use Check connection, then retry."
        )
    account = dict(
        provider="Attached endpoint",
        name=labels().get(host, host),
        endpoint=addr,
        remote_model="local-agent",
        context=8192,
        harness="OMP",
        connection_id=ident,
        host=host,
    )
    known = _flags((entries().get(ident) or {}).get("protocols"))
    protocols = known or _protocols(addr, token)
    if protocols:
        account["protocols"] = protocols
    _publish(account, token)
    _link(
        ident,
        dict(
            host=host,
            remote_port=remote["port"],
            managed=True,
            kind="workbench-node",
            base_path="/v1",
        ),
    )
    return dict(
        endpoint=addr, model="local-agent", machine=account["name"], connection_id=ident
    )


def _text(value, limit=200):
    plain = isinstance(value, str) and len(value) <= limit and value.isprintable()
    return value if plain else ""


def _model(value):
    text = _text(value)
    return text if text == text.strip() and not text.startswith("-") else ""


def _label(kind):
    try:
        import providers

        return _text(providers.KINDS[kind]["label"], 80)
    except Exception:
        return ""


def _base(value):
    plain = isinstance(value, str) and re.fullmatch(
        r"(/[A-Za-z0-9_.-]{1,24}){0,3}", value
    )
    return value if plain else "/v1"


def _endpoint_row(row):
    if not isinstance(row, dict):
        return None
    port = row.get("port")
    if not _port(port) or port == NODE_PORT:
        return None
    listed = row.get("models")
    models = []
    for item in (listed if isinstance(listed, list) else [])[:200]:
        ident = _model(item.get("id") if isinstance(item, dict) else item)
        if ident:
            size = item.get("context") if isinstance(item, dict) else None
            whole = isinstance(size, int) and not isinstance(size, bool)
            models.append(
                dict(id=ident, context=size if whole and 0 < size <= 4000000 else None)
            )
    kind = _text(row.get("kind"), 40)
    return dict(
        port=port,
        kind=kind,
        label=_text(row.get("label"), 80) or _label(kind),
        models=models,
        protocols=_flags(row.get("protocols")),
        needs_key=row.get("needs_key") is True or row.get("auth") == "required",
        base_path=_base(row.get("base_path")),
    )


def discover(host):
    """List model servers answering on the machine's own loopback; nothing here scans a network."""
    import endpoints

    script = endpoints.remote_probe_script()
    command = "sh -s" if script.startswith("#!/bin/sh") else "python3 -"
    found = execute(host, script.encode(), command, 30)
    if isinstance(found, dict):
        found = found.get("endpoints")
    if not isinstance(found, list):
        raise ValueError(
            f"{host} sent a reply Workbench could not read; use Check connection, then search again."
        )
    return [row for row in map(_endpoint_row, found[:32]) if row]


def attach(host, port, model="", name="", key=None):
    from account_book import entries

    host = target(host)
    if not _port(port):
        raise ValueError("Choose one of the servers found on this machine.")
    row = next((r for r in discover(host) if r["port"] == port), None)
    if not row:
        raise ValueError(
            f"No model server is answering on port {port} of {host}; start it there, then search again."
        )
    known = {m["id"]: m["context"] for m in row["models"]}
    model = (model or "").strip() or next(iter(known), "")
    if not _model(model) or (known and model not in known):
        raise ValueError(
            "Choose one of the models this server lists."
            if known
            else "Type the model ID this server should use."
        )
    key = key.strip() if isinstance(key, str) and key.strip() else None
    _remember(host)
    ident = link_id(host, port)
    link = dict(
        host=host,
        remote_port=port,
        managed=False,
        kind=row["kind"],
        base_path=row["base_path"],
    )
    record = _record(link)
    fresh = ident not in links()
    local = forward(host, port, record)
    addr = f"http://127.0.0.1:{local}{row['base_path']}"
    if not _answers(addr):
        disconnect(host, record)
        raise ValueError(
            f"The server on port {port} of {host} stopped answering; start it there, then search again."
        )
    protocols = row["protocols"] or _protocols(addr, key or "")
    label = labels().get(host, host)
    saved = entries().get(ident) or {}
    size = known.get(model)
    account = dict(
        provider="Attached endpoint",
        name=(
            (name or "").strip()
            or saved.get("name")
            or f"{label} · {row['label'] or row['kind'] or 'server'}"
        )[:90],
        endpoint=addr,
        remote_model=model,
        harness=_harness(protocols),
        connection_id=ident,
        host=host,
        remote_port=port,
        kind=row["kind"],
        # The legacy context key marks a Workbench-run node; an attached server's size is context_window.
        context=None,
    )
    if saved.get("context_source") != "user":
        account.update(context_window=size, context_source="server" if size else None)
    if protocols:
        account["protocols"] = protocols
    try:
        _publish(account, key)
    except Exception:
        if fresh:
            disconnect(host, record)
        raise
    _link(ident, link)
    return dict(
        connection_id=ident,
        endpoint=addr,
        model=model,
        name=account["name"],
        machine=label,
        kind=row["kind"],
        protocols=protocols,
        context=known.get(model),
        needs_key=row["needs_key"] and not key,
    )


def ensure(connection_id):
    """Current loopback base URL of a machine-backed connection, reopening its forward if needed."""
    from account_book import entries

    link = links().get(connection_id)
    if link is None:
        host = next((h for h in labels() if identifier(h) == connection_id), None)
        return tunnel(host)["endpoint"] if host else None
    host, port = link["host"], link["remote_port"]
    if link.get("managed"):
        return tunnel(host)["endpoint"]
    record = _record(link)
    local = forward(host, port, record)
    addr = f"http://127.0.0.1:{local}{_base(link.get('base_path'))}"
    if not _answers(addr):
        disconnect(host, record)
        raise ValueError(
            f"The server on port {port} of {host} is not answering; start it there, then retry."
        )
    row = entries().get(connection_id)
    if row and row.get("endpoint") != addr:
        _publish({**row, "endpoint": addr})
    return addr


def detach(connection_id):
    from account_book import remove_node

    link = links().get(connection_id)
    if not link:
        raise ValueError(
            "This connection does not belong to an SSH machine; remove it on the Connections page."
        )
    _close(link["host"], _record(link))
    remove_node(connection_id)
    _link(connection_id, None)
    return True


def forwards():
    names = labels()
    rows = []
    for ident, link in links().items():
        host = link["host"]
        rec = _load(DATA / "ssh" / identifier(host) / _record(link))
        rows.append(
            dict(
                connection_id=ident,
                host=host,
                label=names.get(host, host),
                remote_port=link["remote_port"],
                local_port=rec["port"] if _port(rec.get("port")) else None,
                alive=bool(owned_tunnel(rec)),
                managed=bool(link.get("managed")),
                kind=_text(link.get("kind"), 40),
            )
        )
    return rows


def forget(host):
    """Delete everything this computer holds for a machine without contacting it."""
    from account_book import remove_node

    host = target(host)
    key = identity(host)
    public = key.with_suffix(".pub")
    line = public.read_text().strip() if public.is_file() else ""
    disconnect(host)
    kept = False
    owned = {i for i, link in links().items() if link["host"] == host}
    for ident in sorted(owned | {identifier(host)}):
        try:
            remove_node(ident)
        except Exception:
            kept = True
        _link(ident, None)
    with FileLock(str(HOSTS) + ".lock"):
        rows = labels()
        rows.pop(host, None)
        write_json(HOSTS, rows)
    key.unlink(missing_ok=True)
    public.unlink(missing_ok=True)
    shutil.rmtree(key.parent, ignore_errors=True)
    if kept:
        raise ValueError(
            f"{host} was forgotten, but a saved connection for it could not be deleted; delete it on the Connections page."
        )
    return dict(host=host, public_key=line)


def _remote_dir(folder):
    # The folder is data: only a leading ~ or ~/ is expanded, by the remote shell itself.
    if folder == "~":
        return '"$HOME"'
    if folder.startswith("~/"):
        return '"$HOME"/' + shlex.quote(folder[2:])
    return shlex.quote(folder)


def _say(text):
    return "printf '%s\\n' " + shlex.quote(text)


def remote_command(host, folder, harness, mode):
    if harness not in REMOTE_CLI:
        raise ValueError("Unknown harness.")
    folder = folder.strip() or "."
    if len(folder) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in folder):
        raise ValueError("Use a single-line remote folder path.")
    cmd = REMOTE_CLI[harness]
    enter = (
        "cd -- "
        + _remote_dir(folder)
        + " || { "
        + _say(
            "Workbench could not open that folder on this machine; check the remote project folder."
        )
        + "; "
        + LOGIN
        + "; }"
    )
    if mode == "shell":
        return "exec /bin/sh -c " + shlex.quote(enter + "; " + LOGIN)
    if mode == "device":
        args = {
            "OMP": ["login", "openai-codex-device"],
            "Hermes": [
                "auth",
                "add",
                "openai-codex",
                "--type",
                "oauth",
                "--no-browser",
            ],
            "Claude Code": ["auth", "login"],
            "Codex": ["login", "--device-auth"],
            "Pi": ["auth", "login"],
        }.get(harness)
        if args is None:
            raise ValueError(
                f"Workbench has no tested sign-in command for {harness}; open a project shell on the machine and sign in there."
            )
    elif mode == "local-model":
        if harness != "OMP":
            raise ValueError("Remote managed-model sessions currently use OMP.")
        run(host, "start", 240)
        run(host, "omp-config")
        args = ["--offline", "--model", "workbench/local-agent"]
    else:
        args = []
    inner = (
        'export PATH="$HOME/.local/share/agent-workbench-node/bin:$HOME/.local/bin:$PATH"; '
        + enter
        + "; command -v "
        + cmd
        + " >/dev/null 2>&1 || { "
        + _say(
            f"{harness} ({cmd}) was not found on this machine; install it here, then open the session again."
        )
        + "; "
        + LOGIN
        + "; }; exec "
        + shlex.join([cmd, *args])
    )
    if mode == "local-model":
        inner = (
            'export PI_CODING_AGENT_DIR="$HOME/.local/share/agent-workbench-node/omp-agent"; '
            + inner
        )
    return 'exec "$SHELL" -lic ' + shlex.quote("exec /bin/sh -c " + shlex.quote(inner))


def remote_session(host, folder, harness, mode):
    remote = remote_command(host, folder, harness, mode)
    terminal(ssh(host, True) + ["-t", host, remote])


def _offline(host, what):
    return ValueError(
        f"{host} could not be reached, so {what}; retry when it is online, or use Forget machine to delete it from this computer only."
    )


def _field(p, name, default=""):
    value = p.get(name)
    return default if value is None else str(value)


def dispatch(action, p):
    if action == "machine-list":
        return labels()
    if action == "machine-forwards":
        return forwards()
    if action == "machine-detach":
        return detach(_field(p, "connection_id"))
    host = p.get("host")
    h = target(host.strip() if isinstance(host, str) else "")
    if action == "machine-save":
        return save(_field(p, "name"), h)
    if action == "machine-pair":
        line = pairing_line(h)
        terminal(ssh(h, True) + ["-t", h, line])
        return True
    if action == "machine-key":
        return pairing_line(h)
    if action == "machine-probe":
        return run(h, "probe", 20)
    if action == "machine-install":
        return run(h, "install", 3600)
    if action == "machine-connect":
        return tunnel(h)
    if action == "machine-discover":
        return dict(host=h, endpoints=discover(h))
    if action == "machine-attach":
        return attach(
            h,
            p.get("port"),
            _field(p, "model"),
            _field(p, "name"),
            p.get("key"),
        )
    if action == "machine-forget":
        return forget(h)
    if action == "machine-session":
        remote_session(
            h,
            _field(p, "folder", "."),
            _field(p, "harness", "OMP"),
            _field(p, "mode", "native"),
        )
        return True
    if action in ("machine-stop", "machine-remove"):
        disconnect(h, "tunnel.json")
        if action == "machine-stop":
            return run(h, "stop")
        try:
            result = run(h, "remove")
        except Unreachable:
            raise _offline(h, "its runtime was not removed") from None
        from account_book import remove_node

        remove_node(identifier(h))
        _link(identifier(h), None)
        return result
    if action == "machine-unpair":
        key = identity(h)
        if not key.with_suffix(".pub").is_file():
            raise ValueError(
                "This machine uses your own SSH key, so there is no Workbench key to revoke; use Forget machine to remove it from this computer."
            )
        # Remove only the dedicated public key, even if other authorized users share the account.
        public = key.with_suffix(".pub").read_text().strip()
        try:
            _ssh_run(h, [shlex.join(["python3", "-c", REVOKE, public])], None, 30)
        except Unreachable:
            raise _offline(h, "its key was not revoked") from None
        disconnect(h)
        from account_book import remove_node

        remove_node(identifier(h))
        _link(identifier(h), None)
        key.unlink()
        key.with_suffix(".pub").unlink()
        return True
    raise ValueError("Unknown machine operation.")
