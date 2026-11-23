"""Find, check and attach model servers that are already running. Loopback, a typed address, or a paired machine's own loopback only."""

import hashlib
import http.client
import inspect
import json
import secrets
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import requests

import account_book
import connections
import providers
from runtime import PORT

SWEEP_TIMEOUT = 1.0
PROBE_TIMEOUT = 5.0
AUTH = ("none", "required", "accepted", "rejected")
HINTS = {
    "lmstudio-server-off": "LM Studio is running, but its local server is off. "
    "In LM Studio open Developer and start the server, then scan again."
}


def _http(method, url, headers=None, body=None, timeout=1.0):
    rest = url.split("://", 1)[1]
    hostport, _, path = rest.partition("/")
    conn = http.client.HTTPConnection(hostport, timeout=timeout)
    try:
        conn.request(method, "/" + path, body=body, headers=headers or {})
        reply = conn.getresponse()
        head = {k.lower(): v for k, v in reply.getheaders()}
        end, parts, size = time.monotonic() + timeout, [], 0
        while size < 2097152:
            part = reply.read1(65536)
            if not part:
                break
            if time.monotonic() > end:
                return 0, {}, b""
            parts.append(part)
            size += len(part)
        return reply.status, head, b"".join(parts)[:2097152]
    except (OSError, http.client.HTTPException):
        return 0, {}, b""
    finally:
        conn.close()


def _json(body):
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        return None


def _items(doc):
    rows = doc.get("data", doc.get("models")) if isinstance(doc, dict) else doc
    if rows is None and isinstance(doc, dict) and doc.get("object") == "list":
        rows = []
    if not isinstance(rows, list):
        return None
    return [
        r
        for r in rows
        if isinstance(r, dict) and isinstance(r.get("id", r.get("name")), str)
    ]


def _dig(doc, path):
    for key in path:
        doc = doc.get(key) if isinstance(doc, dict) else None
    return doc


def _window(item):
    for key in (
        "context_length",
        "context_window",
        "max_model_len",
        "max_input_tokens",
    ):
        value = item.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def _identify(root, status, head, items, kinds, identity, get, timeout):
    server = head.get("server", "").lower()
    realm = head.get("www-authenticate", "").lower()
    owners = set(str(i.get("owned_by")) for i in items or [])
    for name, row in kinds.items():
        marks = row["identity"]
        if (
            any(s in server for s in marks.get("server", []))
            or any(o in owners for o in marks.get("owned_by", []))
            or any(r in realm for r in marks.get("realm", []))
        ):
            return name
    seen = {}
    for name, path, test, keys, want in identity:
        if path not in seen:
            seen[path] = get("GET", root + path, {}, None, timeout)
        code, hdr, body = seen[path]
        if code != 200:
            continue
        doc, text = _json(body), body.decode("utf-8", "replace")
        if (
            (test == "eq" and _dig(doc, keys) == want)
            or (test == "has" and isinstance(doc, dict) and all(k in doc for k in keys))
            or (test == "text" and want in text)
            or (test == "text_is" and text.strip() == want)
            or (test == "server" and want in hdr.get("server", "").lower())
        ):
            return name
    if items is None:
        return None
    health = _json(seen.get("/health", (0, {}, b""))[2])
    plain = items and not any("owned_by" in i for i in items)
    if plain and isinstance(health, dict) and health.get("status") == "ok":
        return "mlx-lm"
    return "generic"


def _models(root, kind, items, kinds, get, headers, timeout):
    plan = kinds[kind]["context_source"]
    sizes, loaded, skip, shared = {}, None, set(), None
    if plan == "ollama":
        doc = _json(get("GET", root + "/api/ps", headers, None, timeout)[2])
        rows = doc.get("models") if isinstance(doc, dict) else None
        if isinstance(rows, list):
            loaded = set()
            for row in rows:
                if isinstance(row, dict):
                    for key in (row.get("name"), row.get("model")):
                        if isinstance(key, str):
                            loaded.add(key)
                            sizes[key] = _window(row)
    elif plan == "lmstudio":
        doc = _json(get("GET", root + "/api/v1/models", headers, None, timeout)[2])
        rows = doc.get("models") if isinstance(doc, dict) else None
        if isinstance(rows, list):
            loaded = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = row.get("key")
                embeds = row.get("type") in ("embedding", "embeddings")
                if embeds and isinstance(name, str):
                    skip.add(name)
                running = row.get("loaded_instances")
                for inst in running if isinstance(running, list) else []:
                    if isinstance(inst, dict):
                        config = inst.get("config")
                        size = _window(config) if isinstance(config, dict) else None
                        for key in (name, inst.get("id")):
                            if isinstance(key, str):
                                loaded.add(key)
                                sizes[key] = size
    elif plan == "props" and len(items) == 1:
        doc = _json(get("GET", root + "/props", headers, None, timeout)[2])
        value = _dig(doc, ["default_generation_settings", "n_ctx"])
        shared = _window({"context_length": value})
        loaded = set(i.get("id", i.get("name")) for i in items)
    elif plan == "kobold":
        path = "/api/extra/true_max_context_length"
        doc = _json(get("GET", root + path, headers, None, timeout)[2])
        shared = _window({"context_length": _dig(doc, ["value"])})
    out = []
    for item in items:
        ident = item.get("id", item.get("name"))
        if ident in skip:
            continue
        size = sizes.get(ident) or shared
        if not size and plan == "item":
            size = _window(item)
        state = None if loaded is None else ident in loaded
        out.append({"id": ident, "context": size, "loaded": state})
    return out[:500]


def _protocols(base, prefix, get, headers, timeout):
    stem = base[:-3] if base.endswith("/v1") else base
    sent = dict(headers)
    sent["Content-Type"] = "application/json"
    out = {}
    for name, url in (
        ("chat", base + "/chat/completions"),
        ("responses", base + "/responses"),
        ("messages", stem + prefix + "/v1/messages"),
    ):
        code, _, body = get("POST", url, sent, b"{}", timeout)
        text = body.lower()
        if (
            code in (405, 501)
            or (code == 404 and b"model" not in text)
            or (code == 200 and b"unexpected endpoint" in text)
        ):
            out[name] = False
        elif code in (200, 400, 422):
            out[name] = True
        else:
            out[name] = None
    return out


def _probe_base(base, kinds, identity, get, headers, timeout):
    root = "/".join(base.split("/", 3)[:3])
    status, head, body = get("GET", base + "/models", headers, None, timeout)
    if not status or "airtunes" in head.get("server", "").lower():
        return None
    items = _items(_json(body)) if status == 200 else None
    locked = status in (401, 403)
    if items is None and not locked:
        return None
    kind = _identify(root, status, head, items, kinds, identity, get, timeout)
    kind = kind or "generic"
    record = {
        "base": base,
        "kind": kind,
        "auth": "none",
        "models": [],
        "protocols": {"chat": None, "responses": None, "messages": None},
    }
    if locked:
        record["auth"] = "rejected" if headers else "required"
        return record
    if headers:
        record["auth"] = "accepted"
    record["models"] = _models(root, kind, items, kinds, get, headers, timeout)
    prefix = kinds[kind]["anthropic_prefix"]
    record["protocols"] = _protocols(base, prefix, get, headers, timeout)
    return record


def _listening(port, timeout=0.25):
    try:
        socket.create_connection(("127.0.0.1", port), timeout).close()
        return True
    except OSError:
        return False


def _sweep(ports, control, kinds, identity, get, timeout):
    def one(port):
        if not _listening(port):
            return None
        base = "http://127.0.0.1:%d/v1" % port
        record = _probe_base(base, kinds, identity, get, {}, timeout)
        if record and (record["kind"] != "generic" or record["auth"] == "none"):
            record["port"] = port
            return record
        return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        found = [r for r in pool.map(one, ports) if r]
    hints = []
    if not any(r["kind"] == "lmstudio" for r in found):
        for port in control:
            if not _listening(port):
                continue
            greeting = "http://127.0.0.1:%d/lmstudio-greeting" % port
            doc = _json(get("GET", greeting, {}, None, timeout)[2])
            if _dig(doc, ["lmstudio"]) is True:
                hints.append("lmstudio-server-off")
                break
    return {"v": 1, "endpoints": found, "hints": hints}


CORE = (
    _http,
    _json,
    _items,
    _dig,
    _window,
    _identify,
    _models,
    _protocols,
    _probe_base,
    _listening,
    _sweep,
)


def _fetch(method, url, headers=None, body=None, timeout=1.0):
    parsed = urlsplit(url)
    if parsed.scheme == "http" and providers.is_loopback(parsed.hostname):
        return _http(method, url, headers, body, timeout)
    if parsed.scheme != "https":
        return 0, {}, b""
    try:
        r = providers.request(
            method, url, headers=headers or {}, data=body, timeout=timeout, stream=True
        )
    except requests.RequestException:
        return 0, {}, b""
    try:
        end, parts, size = time.monotonic() + timeout, [], 0
        for part in r.iter_content(65536):
            if time.monotonic() > end:
                return 0, {}, b""
            parts.append(part)
            size += len(part)
            if size >= 2097152:
                break
        head = {k.lower(): v for k, v in r.headers.items()}
        return r.status_code, head, b"".join(parts)[:2097152]
    except requests.RequestException:
        return 0, {}, b""
    finally:
        r.close()


def _auth(key):
    key = (key or "").strip()
    if not key:
        return {}
    if any(ord(c) < 32 or ord(c) == 127 or ord(c) > 255 for c in key):
        raise ValueError(
            "This API key contains a line break or another character that cannot be "
            "sent. Paste the key again."
        )
    return {"Authorization": "Bearer " + key, "x-api-key": key}


def listed(payload):
    items = _items(payload)
    if items is None:
        return None
    return [{"id": i.get("id", i.get("name")), "context": _window(i)} for i in items]


def _size(value):
    whole = isinstance(value, int) and not isinstance(value, bool)
    return value if whole and 1024 <= value <= 10000000 else None


def _chosen(models, model):
    if model:
        unlisted = {"id": model, "context": None}
        return next((m for m in models if m["id"] == model), unlisted)
    loaded = [m for m in models if m["loaded"] is True]
    return loaded[0] if len(loaded) == 1 else None


def _warnings(record, label, model, assumed=True):
    notes = []
    if record["auth"] == "required":
        notes.append(f"{label} needs its API key before models can be listed.")
    if providers.KINDS[record["kind"]]["exposed"] and record["auth"] == "none":
        notes.append(
            f"{label} listens on every network interface by default and answered "
            "without a key; bind it to 127.0.0.1 or set a key if other devices share "
            "your network."
        )
    if record["auth"] in ("required", "rejected"):
        return notes
    chosen = _chosen(record["models"], model)
    ident, size = (chosen["id"], chosen["context"]) if chosen else ("", 0)
    if chosen and isinstance(size, int) and size < 16000:
        notes.append(
            f"{ident} is loaded with a {size:,}-token context, which is small for an "
            f"agent; raise the context size in {label} and check again."
        )
    elif chosen and size is None and assumed:
        notes.append(
            f"{label} did not report a context size for {ident}, so 32,768 tokens is "
            "assumed; set the context size on the connection if the server uses "
            "another value."
        )
    return notes


def _saved(base):
    for ident, row in account_book.entries().items():
        if (
            row.get("provider") == "Attached endpoint"
            and not row.get("host")
            and not row.get("context")
            and row.get("endpoint") == base
        ):
            return ident
    return ""


def _hints(names):
    names = [n for n in names if isinstance(n, str)] if isinstance(names, list) else []
    return [{"id": n, "text": HINTS[n]} for n in names if n in HINTS]


def describe(record, model="", local=True):
    label = providers.KINDS[record["kind"]]["label"]
    out = dict(record)
    out["label"] = label
    out["compat"] = connections.compat(
        "Attached endpoint", {"protocols": record["protocols"], "name": label}
    )
    out["warnings"] = _warnings(record, label, model)
    out["connection_id"] = _saved(record["base"]) if local else ""
    return out


def scan():
    raw = _sweep(
        [p for p in providers.SWEEP_PORTS if p != PORT],
        providers.LMSTUDIO_CONTROL_PORTS,
        providers.KINDS,
        providers.IDENTITY,
        _http,
        SWEEP_TIMEOUT,
    )
    return {
        "endpoints": [describe(r) for r in raw["endpoints"]],
        "hints": _hints(raw["hints"]),
    }


def probe(address, key=""):
    base = connections.endpoint({"provider": "Attached endpoint", "endpoint": address})[
        0
    ]
    headers = _auth(key)
    path = urlsplit(base).path
    if not path:
        candidates = [base + "/v1", base]
    elif path.endswith("/v1"):
        candidates = [base]
    else:
        candidates = [base, base + "/v1"]
    answers = []

    def get(method, url, headers=None, body=None, timeout=1.0):
        reply = _fetch(method, url, headers, body, timeout)
        answers.append(reply[0])
        return reply

    for candidate in candidates:
        record = _probe_base(
            candidate, providers.KINDS, providers.IDENTITY, get, headers, PROBE_TIMEOUT
        )
        if record:
            return describe(record)
        if not any(answers):
            break
    parsed = urlsplit(base)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if not any(answers):
        raise ValueError(
            f"Nothing answered at {origin}. Start the server or check the address "
            "and port, then try again."
        )
    raise ValueError(
        f"{origin} answered, but not as an OpenAI-compatible API. "
        "Check the address; most servers use a path that ends in /v1."
    )


def model_context(base, kind, model, token="", timeout=1.5):
    if not isinstance(kind, str) or kind not in providers.KINDS:
        return None
    try:
        headers = _auth(token)
        status, _, body = _fetch("GET", base + "/models", headers, None, timeout)
        items = _items(_json(body)) if status == 200 else None
        if items is None:
            return None
        root = "/".join(base.split("/", 3)[:3])
        rows = _models(root, kind, items, providers.KINDS, _fetch, headers, timeout)
    except (ValueError, OSError, http.client.HTTPException):
        return None
    return _reported(rows, model)


def _reported(models, model):
    return next((m["context"] for m in models if m["id"] == model), None)


def remote_ident(host, remote_port):
    return hashlib.sha256(f"{host}:{int(remote_port)}".encode()).hexdigest()[:16]


def attach(
    address,
    name="",
    model="",
    key="",
    context=None,
    harness="OMP",
    host="",
    remote_port=0,
):
    key = (key or "").strip()
    found = probe(address, key)
    if found["auth"] == "required":
        raise ValueError(
            "This server needs an API key. Paste its key and attach again."
        )
    if found["auth"] == "rejected":
        raise ValueError(
            "The server rejected this API key. Check the key and attach again."
        )
    model = connections.clean_model(model)
    models = found["models"]
    if not model:
        loaded = [m["id"] for m in models if m["loaded"] is True]
        if len(loaded) == 1:
            model = loaded[0]
        elif len(models) == 1:
            model = models[0]["id"]
        model = connections.clean_model(model)
    if not model and models:
        raise ValueError("Choose which model this connection should use.")
    if not model:
        raise ValueError(
            "This server has no model available. Load a model there, then attach again."
        )
    if context is not None:
        if _size(context) is None:
            raise ValueError(
                "Enter a context size between 1,024 and 10,000,000 tokens."
            )
        window, origin = context, "user"
    else:
        window = _size(_reported(models, model))
        origin = "server" if window else None
    if host:
        ident = remote_ident(host, remote_port)
    else:
        ident = found["connection_id"] or secrets.token_hex(8)
    label = f"{found['label']} · {model}" + (f" on {host}" if host else "")
    created = ident not in account_book.entries()
    row = account_book.save(
        dict(
            provider="Attached endpoint",
            connection_id=ident,
            name=((name or "").strip() or label)[:90],
            endpoint=found["base"],
            remote_model=model,
            harness=harness,
            kind=found["kind"],
            protocols=found["protocols"],
            probed=int(time.time()),
            context_window=window,
            context_source=origin,
            **({"host": host, "remote_port": int(remote_port)} if host else {}),
        ),
        allow_endpoint_change=True,
    )
    stored = ""
    if key:
        try:
            stored = connections.store_key(row, key)
        except Exception:
            if created:
                account_book.remove(ident)
            raise
    return {
        "connection": row,
        "stored": stored,
        "message": connections.key_message(stored, row) if stored else "",
        "warnings": _warnings(found, found["label"], model, assumed=context is None),
    }


def refresh(connection_id):
    row = account_book.entries().get(connection_id)
    if not row or row.get("provider") != "Attached endpoint":
        raise ValueError("Only attached endpoints can be checked again.")
    base = row["endpoint"]
    if row.get("host") and row.get("remote_port"):
        base = connections._forward(connection_id, row) or base
    token = connections.secret(row)
    record = _probe_base(
        base, providers.KINDS, providers.IDENTITY, _fetch, _auth(token), PROBE_TIMEOUT
    )
    if record is None:
        raise ValueError(
            f"Nothing answered at {base}. Start the server, then check again."
        )
    row = account_book.entries().get(connection_id)
    if not row or row.get("provider") != "Attached endpoint":
        raise ValueError("This connection no longer exists. Refresh the list.")
    answered = record["auth"] in ("none", "accepted")
    kind, known = record["kind"], row.get("kind")
    if not answered and kind == "generic" and isinstance(known, str):
        kind = known if known in providers.KINDS else kind
    record = dict(record, kind=kind)
    fresh = {**row, "endpoint": base, "kind": kind}
    fresh["probed"] = int(time.time())
    model = row.get("remote_model", "")
    user = row.get("context_source") == "user"
    if answered:
        fresh["protocols"] = record["protocols"]
        if not user and not row.get("context"):
            window = _size(_reported(record["models"], model))
            fresh["context_window"] = window
            fresh["context_source"] = "server" if window else None
    saved = account_book.save(fresh, allow_endpoint_change=True)
    label = providers.KINDS[kind]["label"]
    return {
        "connection": saved,
        "models": record["models"],
        "auth": record["auth"],
        "warnings": _warnings(record, label, model, assumed=not user),
    }


def probe_protocols(base, key=""):
    return _protocols(base, "", _fetch, _auth(key), PROBE_TIMEOUT)


def remote_probe_script(ports=None, control=None):
    slim = {
        k: {f: v[f] for f in ("context_source", "anthropic_prefix", "identity")}
        for k, v in providers.KINDS.items()
    }
    table = json.dumps(
        [
            providers.SWEEP_PORTS if ports is None else ports,
            providers.LMSTUDIO_CONTROL_PORTS if control is None else control,
            slim,
            providers.IDENTITY,
        ]
    )
    return (
        "import http.client, json, socket, time\n"
        "from concurrent.futures import ThreadPoolExecutor\n\n"
        + "\n\n".join(inspect.getsource(f) for f in CORE)
        + "\n\nPORTS, CONTROL, KINDS, IDENTITY = json.loads(%r)\n" % table
        + "print(json.dumps(_sweep(PORTS, CONTROL, KINDS, IDENTITY, _http, 1.0)))\n"
    )


def _remote_models(rows):
    out = []
    for item in rows[:500] if isinstance(rows, list) else []:
        ident = item.get("id") if isinstance(item, dict) else None
        if not isinstance(ident, str) or not ident or len(ident) > 300:
            continue
        try:
            if connections.clean_model(ident) != ident:
                continue
        except ValueError:
            continue
        size, loaded = item.get("context"), item.get("loaded")
        whole = isinstance(size, int) and not isinstance(size, bool)
        out.append(
            {
                "id": ident,
                "context": size if whole and 256 <= size <= 10000000 else None,
                "loaded": loaded if isinstance(loaded, bool) else None,
            }
        )
    return out


def parse_remote(text):
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    try:
        doc = json.loads(lines[-1]) if lines else None
    except (ValueError, RecursionError):
        doc = None
    if not isinstance(doc, dict) or not isinstance(doc.get("endpoints"), list):
        raise ValueError(
            "The machine did not return a readable scan. Check that python3 is "
            "installed there, then scan again."
        )
    found = {}
    for entry in doc["endpoints"]:
        port = entry.get("port") if isinstance(entry, dict) else None
        whole = isinstance(port, int) and not isinstance(port, bool)
        if not whole or not 1 <= port <= 65535 or port in found:
            continue
        kind, auth = entry.get("kind"), entry.get("auth")
        flags = entry.get("protocols")
        flags = flags if isinstance(flags, dict) else {}
        known = isinstance(kind, str) and kind in providers.KINDS
        found[port] = {
            "base": "http://127.0.0.1:%d/v1" % port,
            "port": port,
            "kind": kind if known else "generic",
            "auth": auth if isinstance(auth, str) and auth in AUTH else "none",
            "models": _remote_models(entry.get("models")),
            "protocols": {
                name: flags.get(name) if isinstance(flags.get(name), bool) else None
                for name in ("chat", "responses", "messages")
            },
        }
    return {
        "endpoints": [describe(entry, local=False) for entry in found.values()],
        "hints": _hints(doc.get("hints")),
    }


def _text(payload, name):
    value = payload.get(name)
    return value if isinstance(value, str) else ""


def dispatch(action, payload):
    payload = payload if isinstance(payload, dict) else {}
    if action == "endpoint-scan":
        return scan()
    if action == "endpoint-probe":
        return probe(_text(payload, "address"), _text(payload, "key"))
    if action == "endpoint-attach":
        size = payload.get("context")
        if isinstance(size, str):
            size = size.strip()
            size = int(size) if size.isdecimal() and len(size) < 10 else size
        return attach(
            _text(payload, "address"),
            name=_text(payload, "name"),
            model=_text(payload, "model"),
            key=_text(payload, "key"),
            context=size or None,
            harness=_text(payload, "harness") or "OMP",
        )
    if action == "endpoint-refresh":
        return refresh(_text(payload, "connection_id"))
    raise ValueError("Unknown endpoint action.")
