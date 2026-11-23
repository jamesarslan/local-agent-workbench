"""Provider selection and harness launch specifications. Secrets stay in the child environment."""

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests

import paths
import providers
from host import command, terminal
from runtime import (
    ALIAS,
    DATA,
    URL,
    key,
    load,
    loopback_env,
    omp_command,
    start,
    write_json,
)

PROVIDERS = {
    row["id"]: row["openai_base"] for row in providers.SOURCES if row["selectable"]
}
PROVIDER_KEYS = {
    row["id"]: row["key_env"] for row in providers.SOURCES if row["kind"] == "hosted"
}
HARNESS = [h["id"] for h in providers.HARNESSES]
HARNESS_CLI = {h["id"]: h["cli"] for h in providers.HARNESSES}
SERVICE = "Agent Workbench"
WINDOWS = os.name == "nt"
_MEMORY = {}
_HOST = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|[0-9A-Fa-f:.]+")
_PATH = re.compile(r"(/[A-Za-z0-9._~-]+)*")
_AUTH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_TAILS = ("/chat/completions", "/completions", "/responses", "/messages", "/models")
_FIELDS = ("provider", "endpoint", "connection_id", "remote_model")
_ACTIONS = (
    "source-compat",
    "source-context",
    "source-credential",
    "source-key-save",
    "source-key-forget",
)
NO_VAULT = {
    "darwin": "The login keychain is not available. Unlock it in Keychain Access, "
    "then restart Agent Workbench.",
    "win32": "Windows Credential Manager is not available. Check that the Credential "
    "Manager service is running, then restart Agent Workbench.",
    "linux": "No system credential vault was found. Install and unlock a Secret "
    "Service provider such as GNOME Keyring or KWallet, then restart Agent Workbench.",
}


def normalise(address):
    text = address.strip() if isinstance(address, str) else ""
    if not text:
        raise ValueError(
            "Enter the server address, for example http://127.0.0.1:11434/v1."
        )
    if "://" not in text:
        try:
            host = urlsplit("//" + text).hostname or ""
        except ValueError:
            host = ""
        text = ("http://" if providers.is_loopback(host) else "https://") + text
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        raise ValueError(
            "The port in this address is not valid. Use a number from 1 to 65535, "
            "as in http://127.0.0.1:1234/v1."
        ) from None
    path = parsed.path.rstrip("/")
    if (
        parsed.scheme not in ("http", "https")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port == 0
        or not _HOST.fullmatch(parsed.hostname or "")
        or not _PATH.fullmatch(path)
    ):
        raise ValueError(
            "Use a plain API address such as http://127.0.0.1:1234/v1, with no user "
            "name, password, query string or special characters."
        )
    for tail in _TAILS:
        if path.endswith(tail):
            path = path[: -len(tail)]
            break
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def endpoint(s):
    provider = s.get("provider", "Managed local")
    model = str(s.get("remote_model") or "").strip()
    if provider == "Managed local":
        return URL + "/v1", ALIAS
    if provider == "Native login":
        return "", model
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("Choose a supported model source.")
    address = PROVIDERS[provider] or normalise(s.get("endpoint", ""))
    parsed = urlsplit(address)
    if parsed.scheme == "http" and not providers.is_loopback(parsed.hostname):
        raise ValueError(
            "Plain HTTP is only allowed to this computer. Use an https:// address, "
            "or add the machine under Machines to reach it through an SSH tunnel."
        )
    return address, model


def clean_model(value):
    text = str(value or "").strip()
    if (
        len(text) > 300
        or text.startswith("-")
        or any(ord(c) < 32 or ord(c) == 127 for c in text)
    ):
        raise ValueError(
            "This model ID cannot be used. Pick the model from the list or retype "
            "its ID."
        )
    return text


def clean_auth(value):
    if not value:
        return ""
    if not isinstance(value, str) or not _AUTH.fullmatch(value):
        raise ValueError("Choose a subscription from the list.")
    return value


def batch_unsafe(program, values, windows=None):
    windows = WINDOWS if windows is None else windows
    if not windows or not str(program).lower().endswith((".cmd", ".bat")):
        return ""
    for value in values:
        for char in str(value or ""):
            if char in '&|<>^%"\r\n':
                return char
    return ""


def _checked(harness, argv, values):
    bad = batch_unsafe(argv[0], values)
    if bad:
        shown = "a line break" if bad in "\r\n" else f"the character {bad}"
        raise ValueError(
            f"{harness} is installed as a Windows batch file, which cannot safely "
            f"receive {shown} in the model ID, prompt or folder path. Remove it, or "
            f"reinstall {harness} with its native installer."
        )
    return argv


NO_LOGIN_KEYCHAIN = (
    "The macOS login keychain was not found for this home folder, so keys are kept "
    "only until Agent Workbench closes. Start Agent Workbench from your own account "
    "without changing HOME to keep them."
)


def _keychain_missing(module):
    """macOS shows a "Keychain Not Found" dialog that offers to reset the keychain when the
    login keychain is missing, for example when HOME points elsewhere; never ask it then."""
    if sys.platform != "darwin" or not module.endswith(".macOS"):
        return False
    folder = Path.home() / "Library" / "Keychains"
    return not any((folder / n).exists() for n in ("login.keychain-db", "login.keychain"))


def vault_status():
    reason = NO_VAULT.get(sys.platform, NO_VAULT["linux"])
    try:
        import keyring

        backend = keyring.get_keyring()
    except Exception:
        return {"available": False, "backend": "none", "reason": reason}
    module, name = type(backend).__module__, type(backend).__name__
    if _keychain_missing(module):
        return {"available": False, "backend": "macOS", "reason": NO_LOGIN_KEYCHAIN}
    available = not (module.endswith((".fail", ".null")) or "plaintext" in name.lower())
    return {
        "available": available,
        "backend": module.rsplit(".", 1)[-1],
        "reason": "" if available else reason,
    }


def _row(s):
    ident = s.get("connection_id")
    if not ident or not isinstance(ident, str):
        return None
    from account_book import entries

    row = entries().get(ident)
    return row if row and row.get("provider") == s.get("provider") else None


def bound(s):
    out = {k: v for k, v in s.items() if k not in ("host", "remote_port")}
    row = _row(out) if out.get("provider") == "Attached endpoint" else None
    if row:
        out["endpoint"] = row["endpoint"]
        if row.get("host") and row.get("remote_port"):
            out["host"], out["remote_port"] = row["host"], row["remote_port"]
    return out


def _named(s):
    ident = s.get("connection_id")
    ident = ident if isinstance(ident, str) else ""
    return "" if ident.startswith("legacy-") else ident


def vault_slot(s):
    ident = _named(s)
    suffix = "#" + ident if ident else ""
    if s.get("host") and s.get("remote_port"):
        return f"ssh://{s['host']}:{int(s['remote_port'])}" + suffix
    return endpoint(s)[0] + suffix


def key_env(s):
    ident = _named(s)
    if ident:
        return "WORKBENCH_KEY_" + ident.upper()
    provider = s.get("provider")
    return PROVIDER_KEYS.get(provider, "") if isinstance(provider, str) else ""


def _own(s):
    """False when the id is saved for another provider or address than the caller names."""
    ident = _named(s)
    if not ident:
        return True
    from account_book import entries

    row = entries().get(ident)
    if not row:
        return True
    try:
        same = vault_slot(row) == vault_slot(s)
    except ValueError:
        return False
    return same and row.get("provider") == s.get("provider")


def _lookup(s):
    name = s["provider"]
    if name == "Managed local":
        return key(), "local"
    if name == "Native login":
        return "", "none"
    variable = key_env(s)
    value = os.getenv(variable, "") if variable else ""
    if value and _own(s):
        return value, "env"
    slot = vault_slot(s)
    if _MEMORY.get(slot):
        return _MEMORY[slot], "memory"
    if not vault_status()["available"]:
        return "", "none"
    import keyring

    # Windows Credential Manager failures surface as pywintypes.error, not KeyringError.
    try:
        value = keyring.get_password(SERVICE, slot) or ""
    except Exception:
        value = ""
    return (value, "vault") if value else ("", "none")


def secret(s):
    value = _lookup(s)[0]
    if not value and s["provider"] in PROVIDER_KEYS:
        raise ValueError(f"Set {key_env(s)} or save a key for {s['provider']}.")
    return value


def secret_source(s):
    return _lookup(s)[1]


def store_key(s, value):
    if s["provider"] not in PROVIDER_KEYS and s["provider"] != "Attached endpoint":
        raise ValueError("Select an API provider before saving its key.")
    slot = vault_slot(s)
    if vault_status()["available"]:
        import keyring

        try:
            keyring.set_password(SERVICE, slot, value)
            _MEMORY.pop(slot, None)
            return "vault"
        except Exception:
            pass
    _MEMORY[slot] = value
    return "memory"


def key_message(stored, s):
    if stored == "vault":
        return "Key saved in the system credential vault."
    variable = key_env(s)
    return (
        "No system credential vault is available, so this key is kept only until "
        "Agent Workbench closes."
        + (
            f" To keep it, set {variable} in your environment before starting "
            "Agent Workbench."
            if variable
            else ""
        )
    )


def forget_key(s):
    try:
        slot = vault_slot(s)
    except ValueError:
        return False
    _MEMORY.pop(slot, None)
    if not vault_status()["available"]:
        return True
    import keyring

    try:
        if keyring.get_password(SERVICE, slot) is not None:
            keyring.delete_password(SERVICE, slot)
        return True
    except Exception:
        return not vault_status()["available"]


def _any(names):
    return ", ".join(names[:-1]) + " or " + names[-1] if len(names) > 1 else names[0]


def compat(provider, row=None):
    source = providers.source(provider)
    flags = source["protocols"]
    if provider == "Attached endpoint":
        found = (row or {}).get("protocols")
        found = found if isinstance(found, dict) else {}
        flags = {
            name: found.get(name) if isinstance(found.get(name), bool) else None
            for name in providers.PROTOCOL_LABEL
        }
    subject = (row or {}).get("name") or source["label"]
    usable = [
        h["id"]
        for h in providers.HARNESSES
        if not h["native_only"] and flags[h["protocol"]] is not False
    ]
    out = {}
    for h in providers.HARNESSES:
        api = providers.PROTOCOL_LABEL[h["protocol"]]
        offered = flags[h["protocol"]]
        ok, reason = True, ""
        if provider == "Native login" or (offered is True and not h["native_only"]):
            pass
        elif h["native_only"]:
            ok = False
            reason = (
                f"{h['id']} signs in with its own account; choose Agent account as "
                "the source to use it."
            )
        elif offered is False:
            ok = False
            others = (
                f"{_any(usable)}, or another source" if usable else "another source"
            )
            reason = (
                f"{h['id']} needs the {api} API, which {subject} does not offer; "
                f"choose {others}."
            )
        else:
            reason = (
                f"{subject} is not confirmed to offer the {api} API, so {h['id']} "
                "may fail to connect."
            )
        out[h["id"]] = {"ok": ok, "reason": reason}
    return out


def _listed_context(base, model):
    try:
        sizes = load(DATA / "model-context.json", {})
    except (OSError, ValueError):
        return None
    sizes = sizes.get(base) if isinstance(sizes, dict) else None
    return sizes.get(model) if isinstance(sizes, dict) else None


def _size(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def context_for(s, row=None, base="", model="", token=""):
    provider = s.get("provider", "Managed local")
    row = row or {}
    if provider == "Managed local":
        return int(s["context"]), "local"
    remote = bool(row.get("remote_port"))
    if row.get("context") and not row.get("context_window") and not remote:
        return min(int(s["context"]), int(row["context"])), "node"
    if row.get("context_source") == "user" and row.get("context_window"):
        return int(row["context_window"]), "user"
    kind = row.get("kind")
    if (
        provider == "Attached endpoint"
        and isinstance(kind, str)
        and kind in providers.KINDS
    ):
        import endpoints

        live = endpoints.model_context(base, kind, model, token)
        if _size(live):
            return live, "server"
    saved = row.get("context_window") or (row.get("context") if remote else None)
    if saved and row.get("remote_model") == model:
        return int(saved), "server"
    source = providers.source(provider)
    if source["kind"] == "hosted":
        listed = _listed_context(base, model)
        if _size(listed):
            return listed, "listing"
    import visual_routes

    known = visual_routes.context_length(provider, model)
    if _size(known):
        return known, "catalog"
    return source["context_default"], "default"


def _usable(ident):
    try:
        return bool(ident) and clean_model(ident) == ident
    except ValueError:
        return False


def discover(s):
    if s["provider"] == "Native login":
        raise ValueError("Select the model in the native CLI for account login.")
    if s["provider"] == "Managed local":
        start(s)
    s = bound(s)
    base, _ = endpoint(s)
    token = secret(s)
    source = providers.source(s["provider"])
    hosted = source["kind"] == "hosted"
    parsed = urlsplit(base)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    name = source["label"] if hosted else origin
    try:
        r = providers.request(
            "GET",
            base + source["models_route"],
            headers=providers.model_headers(source, token),
            timeout=(4, 20),
        )
    except requests.exceptions.SSLError:
        raise ValueError(
            f"The secure connection to {origin} failed. Check the address and the "
            "server's certificate."
        ) from None
    except requests.exceptions.Timeout:
        raise ValueError(
            f"{name} did not answer in time. Check that it is running, then list "
            "models again."
        ) from None
    except requests.RequestException:
        row = _row(s) or {}
        if row.get("host") or row.get("context"):
            raise ValueError(
                "The SSH tunnel for this connection is not open. Connect the machine "
                "under Machines, then list models again."
            ) from None
        raise ValueError(
            f"Nothing answered at {origin}. Start the server or check the address, "
            "then list models again."
        ) from None
    code = r.status_code
    if 300 <= code < 400:
        raise ValueError(
            f"{origin} redirected the request. Enter the final API address instead."
        )
    if code in (401, 403):
        raise ValueError(
            f"{name} rejected the saved API key. Save a valid key for this connection."
            if token
            else f"{name} needs an API key. Save its key, then list models again."
        )
    if code == 404:
        raise ValueError(
            f"{name} did not return a model list. Type the model ID instead."
            if hosted
            else f"There is no model list at {base}/models. Check the address; most "
            "servers use a path that ends in /v1."
        )
    if code >= 400:
        raise ValueError(
            f"{name} answered with HTTP {code}. Try again, or check the server log."
        )
    import endpoints

    try:
        rows = endpoints.listed(r.json())
    except ValueError:
        rows = None
    if rows is None:
        raise ValueError(
            f"{origin} answered, but not with a model list. It may be the server "
            "root; try the address with /v1 added."
        )
    ids = sorted({m["id"] for m in rows if _usable(m["id"])})
    sizes = {m["id"]: m["context"] for m in rows if m["id"] in ids and m["context"]}
    if hosted and sizes:
        try:
            cache = load(DATA / "model-context.json", {})
        except (OSError, ValueError):
            cache = {}
        cache = cache if isinstance(cache, dict) else {}
        cache[base] = sizes
        try:
            write_json(DATA / "model-context.json", cache)
        except OSError:
            pass
    return ids


def _forward(ident, row):
    if not (row.get("host") or row.get("context")):
        return None
    import machines

    if row.get("host") and row.get("remote_port"):
        local = machines.forward(row["host"], int(row["remote_port"]))
        parsed = urlsplit(row["endpoint"])
        host = parsed.netloc.rsplit(":", 1)[0] if parsed.port else parsed.netloc
        fresh = f"{parsed.scheme}://{host}:{local}{parsed.path}"
        from account_book import entries, save

        current = entries().get(ident)
        if current and current.get("endpoint") != fresh:
            save({**current, "endpoint": fresh}, allow_endpoint_change=True)
        return fresh
    host = row.get("host") or next(
        (h for h in machines.labels() if machines.identity(h).parent.name == ident),
        None,
    )
    return machines.tunnel(host)["endpoint"] if host else None


def _tools(s, harness, home, env, sent):
    """Arguments that hand the session its MCP servers; their secrets go to env only."""
    import project_setup

    extra = project_setup.launch_mcp(s, harness, home)
    env.update(extra["env"])
    if sent is not None:
        sent.extend(extra["servers"])
    return list(extra["argv"])


def _lead(harness, argv, tools):
    if batch_unsafe(argv[0], tools):
        raise ValueError(
            f"{harness} is installed as a Windows batch file, which cannot safely "
            f"receive this folder's MCP tools. Reinstall {harness} with its native "
            "installer, or turn off the tools, plugins and project servers this "
            "launch uses."
        )
    return [*argv, *tools]


def prepare(s, harness=None, sent=None, facts=None):
    """sent, when given, collects the names of the MCP servers the harness receives.
    facts, when given, receives the settings the session runs with, its API address and model ID."""
    harness = harness or s.get("harness", "OMP")
    if harness not in HARNESS:
        raise ValueError("Unknown harness")
    provider = s.get("provider", "Managed local")
    import project_setup

    cwd = paths.canonical(s.get("workspace"))
    s = project_setup.before_launch({**s, "workspace": str(cwd)}, harness)
    s = bound({**s, "provider": provider, "workspace": str(cwd)})
    s["remote_model"] = clean_model(s.get("remote_model", ""))
    row = _row(s)
    if provider == "Attached endpoint" and s.get("connection_id"):
        if not row:
            raise ValueError("Reconnect this endpoint in Connections or Machines.")
        s["endpoint"] = _forward(s["connection_id"], row) or row["endpoint"]
    env = loopback_env(os.environ)
    env.pop("WORKBENCH_CALLER_CWD", None)
    # Some agents take the working folder from PWD rather than the process's own folder.
    env["PWD"] = str(cwd)
    cli = HARNESS_CLI[harness]
    if provider == "Native login":
        argv = _lead(harness, command(cli), _tools(s, harness, None, env, sent))
        choice = s["remote_model"]
        auth = clean_auth(s.get("auth_provider", ""))
        account = "openai-codex" if auth.startswith("openai-codex") else auth
        if harness in ("Hermes", "OMP") and auth:
            argv += ["--provider", account]
        if harness == "OMP" and auth and choice and "/" not in choice:
            choice = account + "/" + choice
        if choice:
            argv += ["--model", choice]
        if harness == "Codex":
            argv += ["--sandbox", "workspace-write"]
        if facts is not None:
            facts.update(settings=dict(s), base="", model=choice, prompt=False)
        return _checked(harness, argv, [choice, auth]), env, cwd
    gate = compat(provider, row)[harness]
    if not gate["ok"]:
        raise ValueError(gate["reason"])
    if provider == "Managed local":
        start(s)
    base, model = endpoint(s)
    if not model:
        raise ValueError("Select or type the backend model ID.")
    token = secret(s)
    s["context"] = context_for(s, row, base, model, token)[0]
    tunnelled = row and (row.get("host") or row.get("context"))
    anchor = s["connection_id"] if tunnelled else base
    slot = hashlib.sha256((str(cwd) + anchor + model).encode()).hexdigest()[:12]
    home = DATA / "clients" / slot
    home.mkdir(parents=True, exist_ok=True)
    tools = _tools(s, harness, home, env, sent)
    env["WORKBENCH_SESSION_KEY"] = token or "local"
    if facts is not None:
        # Codex is the one agent here that is not handed the folder prompt.
        facts.update(settings=dict(s), base=base, model=model, prompt=harness != "Codex")
    if harness in ("OMP", "Pi") and provider != "Managed local":
        from visual_routes import route

        s["_visual"] = route(s)
    if harness == "OMP":
        env.pop("OLLAMA_HOST", None)
        if provider == "Managed local":
            argv = omp_command(s)
        else:
            argv = omp_command(
                s,
                profile_name="workbench-" + slot,
                base=base,
                model_id=model,
                env_key="WORKBENCH_SESSION_KEY",
                **({"anchor": anchor} if tunnelled else {}),
            )
        return _checked(harness, argv, [model, s["prompt"], s["workspace"]]), env, cwd
    if harness == "Pi":
        pi_dir = home / "pi"
        pi_dir.mkdir(exist_ok=True)
        env["PI_CODING_AGENT_DIR"] = str(pi_dir)
        write_json(
            pi_dir / "models.json",
            {
                "providers": {
                    "workbench": {
                        "baseUrl": base,
                        "api": "openai-completions",
                        "apiKey": "${WORKBENCH_SESSION_KEY}",
                        "models": [
                            {
                                "id": model,
                                "name": model,
                                "input": ["text", "image"]
                                if s.get("_visual", {}).get("primary_image")
                                else ["text"],
                                "contextWindow": int(s["context"]),
                                "maxTokens": min(16384, int(s["context"]) // 2),
                                "compat": {
                                    "supportsDeveloperRole": False,
                                    "supportsStore": False,
                                },
                            }
                        ],
                    }
                }
            },
        )
        argv = _lead(harness, command(cli), tools) + [
            "--provider",
            "workbench",
            "--model",
            model,
            "--offline",
            "--append-system-prompt",
            s["prompt"],
        ]
        return _checked(harness, argv, [model, s["prompt"]]), env, cwd
    if harness == "Claude Code":
        source = providers.source(provider)
        attached = provider == "Attached endpoint"
        kind = providers.KINDS.get((row or {}).get("kind"), providers.KINDS["generic"])
        local = source["kind"] != "hosted"
        style = kind["anthropic_auth"] if attached else source["anthropic_auth"]
        env.update(
            ANTHROPIC_BASE_URL=source["anthropic_base"]
            or base.removesuffix("/v1")
            + (kind["anthropic_prefix"] if attached else ""),
            ANTHROPIC_DEFAULT_SONNET_MODEL=model,
            ANTHROPIC_DEFAULT_HAIKU_MODEL=model,
            ANTHROPIC_DEFAULT_OPUS_MODEL=model,
            CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
            CLAUDE_CODE_MAX_CONTEXT_TOKENS=str(s["context"]),
            CLAUDE_CONFIG_DIR=str(home / "claude"),
        )
        if style == "x-api-key":
            env["ANTHROPIC_API_KEY"] = token or "local"
            env.pop("ANTHROPIC_AUTH_TOKEN", None)
        else:
            env["ANTHROPIC_AUTH_TOKEN"] = token or "local"
            if local:
                env.pop("ANTHROPIC_API_KEY", None)
            else:
                env["ANTHROPIC_API_KEY"] = ""
        if local:
            env["CLAUDE_CODE_ATTRIBUTION_HEADER"] = "0"
        argv = _lead(harness, command(cli), tools) + [
            "--model",
            model,
            "--append-system-prompt",
            s["prompt"],
        ]
        return _checked(harness, argv, [model, s["prompt"]]), env, cwd
    if harness == "Codex":
        env["CODEX_HOME"] = str(home / "codex")
        Path(env["CODEX_HOME"]).mkdir(exist_ok=True)
        cfg = {
            "model_provider": "workbench",
            "model_context_window": int(s["context"]),
            "model_providers.workbench.name": "Workbench",
            "model_providers.workbench.base_url": base,
            "model_providers.workbench.env_key": "WORKBENCH_SESSION_KEY",
            "model_providers.workbench.wire_api": "responses",
        }
        argv = _lead(harness, command(cli), tools) + [
            "--model",
            model,
            "--sandbox",
            "workspace-write",
        ]
        for k, v in cfg.items():
            argv += ["-c", k + "=" + json.dumps(v)]
        return _checked(harness, argv, [model]), env, cwd
    if harness == "opencode":
        # opencode expands {file:...} in this JSON and pastes the key in unescaped.
        if "{" in model:
            raise ValueError(
                "opencode cannot use a model ID that contains {, because it reads that "
                "as a reference to a file. Choose another agent for this model."
            )
        if any(c in '{}"\\' or ord(c) < 32 or ord(c) == 127 for c in token):
            raise ValueError(
                "opencode cannot safely receive this connection's API key because it "
                "contains a quote, backslash, brace or control character. Choose "
                "another agent for this connection, or replace the key."
            )
        window = int(s["context"])
        options = {"baseURL": base}
        if token:
            options["apiKey"] = "{env:WORKBENCH_SESSION_KEY}"
        config = {
            "$schema": "https://opencode.ai/config.json",
            "model": "workbench/" + model,
            "autoupdate": False,
            "provider": {
                "workbench": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Agent Workbench",
                    "options": options,
                    "models": {
                        model: {
                            "name": model,
                            "limit": {
                                "context": window,
                                "output": min(16384, window // 2),
                            },
                        }
                    },
                }
            },
        }
        if s.get("prompt", "").strip():
            folder = home / "opencode"
            folder.mkdir(exist_ok=True)
            (folder / "instructions.md").write_text(s["prompt"], encoding="utf8")
            config["instructions"] = [str(folder / "instructions.md")]
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
        env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
        env["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
        argv = _lead(harness, command(cli), tools) + ["--model", "workbench/" + model]
        return _checked(harness, argv, [model]), env, cwd
    raise ValueError("Unknown harness")


def ready_session(s, facts=None):
    sent = []
    argv, env, cwd = prepare(s, sent=sent, facts=facts)
    if facts is not None:
        facts["sent"] = list(sent)
    if s["firecrawl"] and "firecrawl" in sent:
        from cli import web

        try:
            response = providers.request("GET", "http://127.0.0.1:3002/", timeout=2)
            ready = response.ok and "Firecrawl" in response.text
        except requests.RequestException:
            ready = False
        if not ready:
            web(True)
    if s["blender"] and "blender" in sent:
        from runtime import ensure_blender

        ensure_blender()
    return argv, env, cwd


def launch(s, facts=None):
    argv, env, cwd = ready_session(s, facts)
    import secrets
    import time
    from runtime import ROOT

    ticket = secrets.token_hex(16)
    ready = DATA / "interactive" / (ticket + ".ready")
    terminal(
        [sys.executable, str(ROOT / "session_window.py"), ticket, s["harness"], *argv],
        cwd=cwd,
        env=env,
    )
    for _ in range(120):
        if ready.exists():
            result = load(ready, {})
            ready.unlink(missing_ok=True)
            if result.get("error"):
                raise ValueError(result["error"])
            return
        time.sleep(0.1)
    raise RuntimeError(
        "The terminal did not confirm startup. Check that a terminal emulator is available."
    )


def _credential(s):
    value, source = ("", "local") if s["provider"] == "Managed local" else _lookup(s)
    return bool(value) or s["provider"] not in PROVIDER_KEYS, source


def _selected(payload):
    from runtime import settings as runtime_settings

    chosen = {}
    for name in _FIELDS:
        value = payload.get(name, "" if "provider" in payload else None)
        if value is not None:
            chosen[name] = value if isinstance(value, str) else ""
    s = runtime_settings() | chosen
    if s["provider"] not in PROVIDERS:
        raise ValueError("Choose a supported model source.")
    return bound(s)


def dispatch(action, payload):
    payload = payload if isinstance(payload, dict) else {}
    if action == "source-table":
        shown = ("id", "label", "kind", "key_env", "protocols", "evidence")
        return {
            "providers": [
                {name: row[name] for name in shown}
                for row in providers.SOURCES
                if row["selectable"]
            ],
            "harnesses": [dict(h) for h in providers.HARNESSES],
            "kinds": {name: row["label"] for name, row in providers.KINDS.items()},
            "vault": vault_status(),
        }
    if action == "source-remove":
        from account_book import remove

        return remove(payload.get("connection_id"))
    if action not in _ACTIONS:
        raise ValueError("Unknown source action.")
    s = _selected(payload)
    if action == "source-compat":
        return compat(s["provider"], _row(s))
    if action == "source-context":
        base, model = endpoint(s)
        model = clean_model(model)
        token = ""
        if s["provider"] == "Attached endpoint":
            try:
                token = secret(s)
            except ValueError:
                pass
        size, source = context_for(s, _row(s), base, model, token)
        return {"context": size, "source": source}
    if action == "source-credential":
        ready, source = _credential(s)
        return {
            "ready": ready,
            "source": source,
            "env": key_env(s),
            "vault": vault_status(),
        }
    if action == "source-key-save":
        token = payload.get("key")
        token = token.strip() if isinstance(token, str) else ""
        if not token:
            raise ValueError("Enter the provider API key.")
        if any(ord(c) < 32 or ord(c) == 127 for c in token):
            raise ValueError(
                "This API key contains a line break or another character that cannot "
                "be sent. Paste the key again."
            )
        stored = store_key(s, token)
        return {"stored": stored, "message": key_message(stored, s)}
    if s["provider"] not in PROVIDER_KEYS and s["provider"] != "Attached endpoint":
        raise ValueError("Select an API provider before removing its key.")
    removed = forget_key(s)
    ready, source = _credential(s)
    return {"removed": removed, "ready": ready, "source": source}
