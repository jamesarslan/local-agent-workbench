"""The single table of model sources, local server kinds and agent harnesses."""

from urllib.parse import urlsplit

import requests

LOOPBACK = ("localhost", "127.0.0.1", "::1")
PROTOCOL_LABEL = {
    "chat": "OpenAI Chat Completions",
    "responses": "OpenAI Responses",
    "messages": "Anthropic Messages",
}
HARNESSES = [
    dict(id="OMP", cli="omp", protocol="chat", native_only=False),
    dict(id="Pi", cli="pi", protocol="chat", native_only=False),
    dict(id="Claude Code", cli="claude", protocol="messages", native_only=False),
    dict(id="Codex", cli="codex", protocol="responses", native_only=False),
    dict(id="Hermes", cli="hermes", protocol="chat", native_only=True),
    dict(id="opencode", cli="opencode", protocol="chat", native_only=False),
]


def _p(chat=None, responses=None, messages=None):
    return dict(chat=chat, responses=responses, messages=messages)


def _row(id, label, kind, **extra):
    row = dict(
        id=id,
        label=label,
        kind=kind,
        selectable=kind != "local-server",
        openai_base=None,
        anthropic_base="",
        anthropic_prefix="",
        anthropic_auth="bearer",
        protocols=_p(),
        models_route="/models",
        models_auth="bearer",
        context_source="none",
        context_default=32768,
        key_env="",
        catalog_prefix=None,
        default_ports=[],
        identity={},
        exposed=False,
        evidence="documented",
    )
    row.update(extra)
    return row


def _hosted(id, base, env, protocols, **extra):
    return _row(
        id,
        id,
        "hosted",
        openai_base=base,
        key_env=env,
        protocols=protocols,
        context_source="listing",
        context_default=128000,
        **extra,
    )


def _server(id, label, ports, context_source, protocols, **extra):
    return _row(
        id,
        label,
        "local-server",
        default_ports=ports,
        context_source=context_source,
        protocols=protocols,
        **extra,
    )


# fmt: off
SWEEP_PORTS = [11434, 1234, 9931, 8080, 8000, 1337, 5001, 5000, 13305, 30000, 10240, 9292, 8095]
# The port of the model server a Workbench on another machine manages; the remote sweep adds it.
WORKBENCH_PORT = 10532
LMSTUDIO_CONTROL_PORTS = [41343, 52993, 16141, 39414, 22931]
SOURCES = [
    _row("Managed local", "Local model", "managed", protocols=_p(True), context_source="settings", evidence="existing"),
    _row("Attached endpoint", "Compatible endpoint", "local-server", selectable=True, context_source="probe", evidence="existing"),
    _hosted("OpenAI", "https://api.openai.com/v1", "OPENAI_API_KEY", _p(True, True, False),
            catalog_prefix="openai/", evidence="existing"),
    _hosted("OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", _p(True, True, True),
            anthropic_base="https://openrouter.ai/api", catalog_prefix="", evidence="existing"),
    _hosted("Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", _p(True, True, False),
            catalog_prefix="", evidence="existing"),
    _hosted("DeepSeek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", _p(True, None, True),
            anthropic_base="https://api.deepseek.com/anthropic", catalog_prefix="deepseek/", evidence="existing"),
    _hosted("Anthropic", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY", _p(True, False, True),
            anthropic_base="https://api.anthropic.com", anthropic_auth="x-api-key", models_auth="x-api-key"),
    _hosted("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", _p(True)),
    _hosted("Together", "https://api.together.ai/v1", "TOGETHER_API_KEY", _p(True, True, None),
            anthropic_base="https://api.together.ai"),
    _hosted("Fireworks", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY", _p(True, None, True),
            anthropic_base="https://api.fireworks.ai/inference"),
    _hosted("Mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY", _p(True, None, False)),
    _hosted("xAI", "https://api.x.ai/v1", "XAI_API_KEY", _p(True, True, False)),
    _hosted("Cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", _p(True)),
    _hosted("Hugging Face", "https://router.huggingface.co/v1", "HF_TOKEN", _p(True, None, True),
            anthropic_base="https://router.huggingface.co"),
    _row("Native login", "Agent account", "native", evidence="existing"),
    _server("ollama", "Ollama", [11434], "ollama", _p(True, True, True)),
    _server("lmstudio", "LM Studio", [1234], "lmstudio", _p(True, True, True)),
    _server("llamacpp", "llama.cpp server", [9931, 8080], "props", _p(True, True, True),
            identity=dict(server=["llama.cpp"], owned_by=["llamacpp"])),
    _server("vllm", "vLLM", [8000], "item", _p(True, True, True), identity=dict(owned_by=["vllm"]), exposed=True),
    _server("mlx-lm", "MLX LM server", [8080], "none", _p(True, False, False)),
    _server("mlx-omni", "MLX Omni Server", [10240], "none", _p(True, False, True),
            anthropic_prefix="/anthropic", exposed=True),
    _server("jan", "Jan", [1337], "none", _p(True, False, True)),
    _server("koboldcpp", "KoboldCpp", [5001], "kobold", _p(True, True, True),
            identity=dict(server=["koboldcppserver"], owned_by=["koboldcpp"]), exposed=True),
    _server("textgen", "text-generation-webui", [5000], "none", _p(True, False, True),
            identity=dict(owned_by=["user"]), anthropic_auth="x-api-key"),
    _server("localai", "LocalAI", [8080], "none", _p(True, True, True), exposed=True),
    _server("llama-swap", "llama-swap", [8080, 9292], "item", _p(True),
            identity=dict(owned_by=["llama-swap"], realm=["llama-swap"]), exposed=True),
    _server("tabbyapi", "TabbyAPI", [5000], "item", _p(True, False, False), identity=dict(owned_by=["tabbyAPI"])),
    _server("sglang", "SGLang", [30000], "item", _p(True, True, True), identity=dict(owned_by=["sglang"])),
    _server("lemonade", "Lemonade Server", [13305], "item", _p(True, True, True), identity=dict(owned_by=["lemonade"])),
    _server("strata", "Strata", [8095], "item", _p(True, True, True)),
    _server("generic", "OpenAI-compatible server", [], "item", _p()),
]
KINDS = {row["id"]: row for row in SOURCES if row["kind"] == "local-server" and not row["selectable"]}
IDENTITY = [
    ["strata", "/health", "eq", ["service"], "strata"],
    ["koboldcpp", "/api/extra/version", "eq", ["result"], "KoboldCpp"],
    ["localai", "/.well-known/localai.json", "has", ["endpoints", "version"], None],
    ["tabbyapi", "/.well-known/serviceinfo", "eq", ["software", "name"], "TabbyAPI"],
    ["lmstudio", "/lmstudio-greeting", "eq", ["lmstudio"], True],
    ["lemonade", "/v1/health", "has", ["all_models_loaded"], None],
    ["sglang", "/", "text", [], "SGLang is running"],
    ["ollama", "/", "text", [], "Ollama is running"],
    ["llamacpp", "/health", "server", [], "llama.cpp"],
    ["llama-swap", "/health", "text_is", [], "OK"],
    ["vllm", "/version", "has", ["version"], None],
    ["jan", "/openapi.json", "eq", ["info", "title"], "Jan API Server Endpoints"],
    ["mlx-omni", "/openapi.json", "eq", ["info", "title"], "MLX Omni Server"],
]
# fmt: on


def source(provider):
    for row in SOURCES:
        if row["selectable"] and row["id"] == provider:
            return row
    raise ValueError("Choose a supported model source.")


def harness(name):
    for row in HARNESSES:
        if row["id"] == name:
            return row
    raise ValueError("Choose a supported agent.")


def is_loopback(host):
    return (host or "").lower() in LOOPBACK


def request(method, url, **kwargs):
    kwargs.setdefault("allow_redirects", False)
    kwargs.setdefault("timeout", 20)
    if is_loopback(urlsplit(url).hostname):
        with requests.Session() as session:
            session.trust_env = False
            return session.request(method, url, **kwargs)
    return requests.request(method, url, **kwargs)


def model_headers(row, token):
    if not token:
        return {}
    if row["models_auth"] == "x-api-key":
        return {"x-api-key": token, "anthropic-version": "2023-06-01"}
    return {"Authorization": "Bearer " + token}
