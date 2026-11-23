"""Named provider connections. Only public configuration is serialized."""

import os
import secrets
import shutil
import subprocess
from functools import lru_cache
from filelock import FileLock
import connections
import providers
from runtime import DATA, load, write_json

EXTRA = (
    "kind",
    "protocols",
    "context_window",
    "context_source",
    "host",
    "remote_port",
    "probed",
    "late_system",
)
CONTEXT_RANGE = "Enter a context size between 1,024 and 10,000,000 tokens."


def entries():
    return load(DATA / "connections.json", {})


def _whole(value, low, high):
    return (
        isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
    )


def _checked(name, value):
    """A trusted caller's probe field as stored, or None when it cannot be kept."""
    if name == "kind":
        return value if isinstance(value, str) and value in providers.KINDS else None
    if name == "protocols":
        if not isinstance(value, dict):
            return None
        return {
            flag: value.get(flag) if isinstance(value.get(flag), bool) else None
            for flag in providers.PROTOCOL_LABEL
        }
    if name == "context_window":
        return value if _whole(value, 1024, 10000000) else None
    if name == "context_source":
        return value if value in ("user", "server") else None
    if name == "host":
        import machines

        return machines.target(value) if isinstance(value, str) and value else None
    if name == "remote_port":
        return value if _whole(value, 1, 65535) else None
    if name == "late_system":
        return value if isinstance(value, bool) else None
    return value if _whole(value, 0, 10**12) else None


def _typed_context(value):
    if isinstance(value, str) and value.isdecimal() and len(value) < 10:
        value = int(value)
    if not _whole(value, 1024, 10000000):
        raise ValueError(CONTEXT_RANGE)
    return value


def _echoed(old, size):
    """True when a form sends back the size the server reported, not a user's choice."""
    return old.get("context_source") == "server" and size == old.get("context_window")


def _extras(old, data, model, trusted, legacy):
    out = {name: old[name] for name in EXTRA if name in old}
    if out.get("context_source") == "server" and old.get("remote_model") != model:
        out.pop("context_window", None)
    if old.get("remote_model") != model:
        # The template answer belongs to the model it was asked of.
        out.pop("late_system", None)
    if trusted:
        for name in EXTRA:
            if name in data:
                out.pop(name, None)
                value = None if data[name] is None else _checked(name, data[name])
                if value is not None:
                    out[name] = value
    elif "context_window" in data and not legacy:
        typed = data["context_window"]
        typed = typed.strip() if isinstance(typed, str) else typed
        size = None if typed in ("", 0, None) else _typed_context(typed)
        if not (size and _echoed(old, size)):
            out.pop("context_window", None)
            if size:
                out["context_window"], out["context_source"] = size, "user"
    if "context_window" not in out:
        out.pop("context_source", None)
    return out


def save(data, allow_endpoint_change=False):
    provider = data.get("provider")
    if (
        not isinstance(provider, str)
        or provider not in connections.PROVIDERS
        or provider == "Managed local"
    ):
        raise ValueError("Choose an API provider or native account.")
    name = str(data.get("name") or "").strip() or provider
    if len(name) > 90:
        raise ValueError("Use a shorter connection name.")
    url, model = connections.endpoint(data)
    model = connections.clean_model(model)
    auth = connections.clean_auth(data.get("auth_provider", ""))
    harness = data.get("harness", "OMP")
    if not isinstance(harness, str) or harness not in connections.HARNESS:
        raise ValueError("Choose a supported agent.")
    ident = data.get("connection_id") or secrets.token_hex(8)
    if not isinstance(ident, str):
        raise ValueError("Unknown connection.")
    with FileLock(str(DATA / "connections.lock")):
        rows = entries()
        if (
            ident in rows
            and not allow_endpoint_change
            and (rows[ident]["provider"], rows[ident]["endpoint"]) != (provider, url)
        ):
            raise ValueError(
                "Remove this connection and attach the new address instead."
            )
        if ident.startswith("legacy-") and ident != "legacy-" + provider:
            raise ValueError("Choose the matching provider.")
        if ident not in rows and not (
            ident == "legacy-" + provider
            or len(ident) == 16
            and all(c in "0123456789abcdef" for c in ident)
        ):
            raise ValueError("Unknown connection.")
        old = rows.get(ident, {})
        capacity = old.get("context")
        if allow_endpoint_change:
            capacity = data.get("context", capacity)
        rows[ident] = dict(
            **({"context": int(capacity)} if capacity else {}),
            name=name,
            provider=provider,
            endpoint=url,
            remote_model=model,
            harness=harness,
            auth_provider=auth,
            connection_id=ident,
            **_extras(old, data, model, allow_endpoint_change, bool(capacity)),
        )
        write_json(DATA / "connections.json", rows)
    return rows[ident]


def managed(row):
    """True for a Workbench-run SSH machine row, which Machines owns and removes."""
    return bool(row.get("context") or row.get("host")) and not row.get("remote_port")


def _listed(provider):
    variable = connections.PROVIDER_KEYS[provider]
    if not os.getenv(variable):
        return ""
    return f"this connection stays listed while {variable} is set in your environment."


def remove(ident):
    legacy = ident[7:] if isinstance(ident, str) and ident.startswith("legacy-") else ""
    listed = _listed(legacy) if legacy in connections.PROVIDER_KEYS else ""
    with FileLock(str(DATA / "connections.lock")):
        rows = entries()
        row = rows.get(ident) if isinstance(ident, str) else None
        if row is None:
            if legacy not in connections.PROVIDER_KEYS:
                raise ValueError("This connection no longer exists. Refresh the list.")
            gone = connections.forget_key({"provider": legacy})
            note = f"The saved {legacy} key was removed"
            if not gone:
                note = (
                    "The system credential vault refused to delete the saved "
                    f"{legacy} key; remove the Agent Workbench entry in your vault "
                    "manually"
                )
            return {
                "removed": False,
                "key_removed": gone,
                "message": f"{note}; {listed}" if listed else note + ".",
            }
        if managed(row):
            raise ValueError(
                "Remove this machine under Machines so its tunnel and remote runtime "
                "are cleaned up."
            )
        del rows[ident]
        keyed = row.get("provider") in (*connections.PROVIDER_KEYS, "Attached endpoint")
        gone = connections.forget_key(row) if keyed else True
        write_json(DATA / "connections.json", rows)
    if row.get("host") and row.get("remote_port"):
        import machines

        try:
            machines.drop_forward(row["host"], row["remote_port"])
        except Exception:
            pass
    note = "Connection removed"
    if not gone:
        note += (
            ", but the system credential vault refused to delete its key; remove "
            "the Agent Workbench entry in your vault manually"
        )
    return {
        "removed": True,
        "key_removed": gone,
        "message": f"{note}; {listed}" if listed else note + ".",
    }


def remove_node(ident):
    with FileLock(str(DATA / "connections.lock")):
        rows = entries()
        if ident in rows and rows[ident]["provider"] == "Attached endpoint":
            connections.forget_key(rows.pop(ident))
            write_json(DATA / "connections.json", rows)


def _key(row):
    try:
        return connections._lookup(row)
    except ValueError:
        return "", "none"


@lru_cache(maxsize=1)
def existing_codex():
    exe = shutil.which("codex")
    if exe:
        try:
            r = subprocess.run(
                [exe, "login", "status"],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return r.returncode == 0 and "chatgpt" in (r.stdout + r.stderr).lower()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return False


def available():
    rows = entries()
    found = {}
    for provider, variable in connections.PROVIDER_KEYS.items():
        ident = "legacy-" + provider
        if ident in rows:
            continue
        if providers.source(provider)["evidence"] == "existing":
            found[ident] = _key(dict(provider=provider))
        else:
            found[ident] = (os.getenv(variable, ""), "env")
        if found[ident][0]:
            rows[ident] = dict(
                connection_id=ident,
                name=provider,
                provider=provider,
                endpoint=connections.PROVIDERS[provider],
                remote_model="",
                harness="OMP",
            )
    if existing_codex() and "legacy-Native login" not in rows:
        rows["legacy-Native login"] = dict(
            connection_id="legacy-Native login",
            name="ChatGPT subscription · Codex",
            provider="Native login",
            endpoint="",
            remote_model="",
            harness="Codex",
            auth_provider="openai-codex-device",
        )
    for ident, row in rows.items():
        row["ready"], row["key_source"] = True, ""
        if row["provider"] in connections.PROVIDER_KEYS:
            value, row["key_source"] = found.get(ident) or _key(row)
            row["ready"] = bool(value)
        try:
            row["compat"] = connections.compat(row["provider"], row)
        except ValueError:
            row["compat"] = {}
    import machines

    for host in machines.labels():
        ident = machines.identifier(host)
        if ident in rows:
            rows[ident]["host"] = host
    return list(rows.values())
