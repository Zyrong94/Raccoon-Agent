"""Explicit model endpoints and a single global personal store."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from copy import deepcopy
from typing import Any, Mapping
from raccoon.modules.memory.models import MemoryConfig, ModelEndpoint


def _deep_merge_dicts(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge_dicts(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _table(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"memory.{name} must be a table")
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"memory.{name} must be a nonempty string")
    return value.strip()


def _number(raw, name, default, *, integer=False):
    value = raw.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 or (integer and not isinstance(value, int)):
        raise ValueError(f"memory.{name} must be a positive {'integer' if integer else 'number'}")
    return value


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def load_memory_config(app_config, *, project_root, data_root) -> MemoryConfig | None:
    raw = {}
    for source in (app_config.global_config, app_config.project_config):
        if source and "memory" in source:
            raw = _deep_merge_dicts(raw, _table(source["memory"], "config"))
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("memory.enabled must be a boolean")
    if not enabled:
        return None
    storage = _table(raw.get("storage", {}), "storage")
    if storage.get("provider", "qdrant") != "qdrant":
        raise ValueError("memory.storage.provider must be qdrant")
    configured_path = storage.get("path")
    path = Path.home() / ".raccoon" / "memory"
    if configured_path is not None:
        path = Path(_text(configured_path, "storage.path")).expanduser()
        if not path.is_absolute():
            path = Path(project_root) / path
    missing = []
    def endpoint(name, default_model):
        section = _table(raw.get(name, {}), name)
        if section.get("provider", "openai") != "openai":
            raise ValueError(f"memory.{name}.provider must be openai (OpenAI-compatible)")
        if "api_key" in section:
            raise ValueError(f"memory.{name}: use api_key_env instead of plaintext api_key")
        model = _text(section.get("model", default_model), f"{name}.model")
        url = _text(section.get("base_url", "https://api.openai.com/v1"), f"{name}.base_url").rstrip("/")
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f"memory.{name}.base_url must be an HTTP URL without credentials or query parameters")
        key_env = _text(section.get("api_key_env", "OPENAI_API_KEY"), f"{name}.api_key_env")
        key = app_config.get_env(key_env) or ""
        if not key.strip():
            missing.append(key_env)
        return ModelEndpoint(model=model, base_url=url, api_key=key)
    llm = endpoint("llm", "gpt-5-mini")
    embedder = endpoint("embedder", "text-embedding-3-small")
    dimensions = _number(_table(raw.get("embedder", {}), "embedder"), "dimensions", 1536, integer=True)
    top_k = _number(raw, "top_k", 3, integer=True)
    max_tokens = _number(raw, "max_context_tokens", 800, integer=True)
    search_timeout = _number(raw, "search_timeout_seconds", 3)
    write_timeout = _number(raw, "write_timeout_seconds", 30)
    embedding_identity = ["openai", embedder.model, embedder.base_url, dimensions]
    return MemoryConfig(
        storage_path=path.resolve(), llm=llm, embedder=embedder, dimensions=dimensions,
        top_k=top_k, max_context_tokens=max_tokens, search_timeout_seconds=search_timeout,
        write_timeout_seconds=write_timeout,
        embedding_fingerprint=_fingerprint(embedding_identity),
        backend_fingerprint=_fingerprint([embedding_identity, llm.model, llm.base_url, llm.api_key, embedder.api_key, write_timeout]),
        unavailable_reason=("Missing memory credential environment variable(s): " + ", ".join(sorted(set(missing)))) if missing else None,
    )
