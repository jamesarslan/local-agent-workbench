"""Resolve an OMP image reader using published model input modalities."""

import time
from functools import lru_cache
import requests

import providers
from runtime import DATA, load, write_json

PARETO = "~unbiased/pareto-latest"
CACHE = DATA / "openrouter-models.json"


def stored():
    try:
        saved = load(CACHE, {})
    except (OSError, ValueError):
        return {}
    models = saved.get("models") if isinstance(saved, dict) else None
    return models if isinstance(models, dict) else {}


def _inputs(entry):
    marks = entry.get("architecture") if isinstance(entry, dict) else None
    found = marks.get("input_modalities") if isinstance(marks, dict) else None
    return found if isinstance(found, list) else []


def _entry(model):
    size = model.get("context_length")
    whole = isinstance(size, int) and not isinstance(size, bool)
    return {
        "architecture": {"input_modalities": _inputs(model)},
        "context_length": size if whole else None,
    }


@lru_cache(maxsize=2)
def public_catalog(epoch):
    try:
        response = requests.get("https://openrouter.ai/api/v1/models", timeout=(3, 6))
        response.raise_for_status()
        rows = {
            m["id"]: _entry(m)
            for m in response.json()["data"]
            if isinstance(m, dict) and isinstance(m.get("id"), str)
        }
    except (requests.RequestException, KeyError, ValueError, TypeError):
        return stored()
    try:
        write_json(CACHE, {"fetched": int(time.time()), "models": rows})
    except OSError:
        pass
    return rows


def context_length(provider, model):
    try:
        prefix = providers.source(provider)["catalog_prefix"]
    except ValueError:
        return None
    if prefix is None or not isinstance(model, str):
        return None
    entry = stored().get(prefix + model)
    size = entry.get("context_length") if isinstance(entry, dict) else None
    whole = isinstance(size, int) and not isinstance(size, bool)
    return size if whole and size > 0 else None


def route(s):
    provider = s.get("provider")
    model = s.get("remote_model", "")
    model = model if isinstance(model, str) else ""
    choice = s.get("image_route", "auto")
    inputs = []
    reader = ""
    try:
        prefix = providers.source(provider)["catalog_prefix"]
    except ValueError:
        prefix = None
    if prefix is not None and model:
        try:
            catalog = public_catalog(int(time.monotonic() // 900))
        except (requests.RequestException, KeyError, ValueError):
            catalog = {}
        inputs = _inputs(catalog.get(prefix + model))
        available = _inputs(catalog.get(PARETO))
        if (
            provider == "OpenRouter"
            and s.get("blender")
            and "image" in available
            and (choice == "pareto" or choice == "auto" and "image" not in inputs)
        ):
            reader = PARETO
    if choice == "manual":
        inputs = ["text", "image"]
    if not reader and "image" in inputs:
        reader = model
    return dict(primary_image="image" in inputs, reader=reader, primary=model)
