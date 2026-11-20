import json
from dataclasses import replace

import pytest

from raccoon.config.settings import AppConfig
from raccoon.modules.memory.backend import Mem0Backend
from raccoon.modules.memory.config import load_memory_config


def test_real_mem0_qdrant_persistence_defaults_and_lock_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("MEM0_TELEMETRY", "false")
    import socket
    def no_network(*args, **kwargs):
        raise AssertionError("Network access forbidden in memory integration test")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    from mem0.utils.factory import LlmFactory, EmbedderFactory
    prompts = []
    class Embedder:
        def embed(self, text, *args): return [1.0, 0.0, 0.0]
        def embed_batch(self, texts, *args): return [self.embed(t) for t in texts]
    class LLM:
        def generate_response(self, messages, **kwargs):
            prompts.append(messages)
            return json.dumps({"memory": [{"id": "0", "text": "User prefers concise Chinese answers", "event": "ADD"}]})
    # Patch only model boundaries; SDK extraction pipeline/storage are real.
    monkeypatch.setattr(LlmFactory, "create", lambda *args: LLM())
    monkeypatch.setattr(EmbedderFactory, "create", lambda *args: Embedder())
    cfg = load_memory_config(AppConfig(env={"OPENAI_API_KEY": "offline"}, project_config={"memory": {
        "storage": {"path": str(tmp_path)}, "embedder": {"dimensions": 3}}}), project_root=tmp_path, data_root=tmp_path)
    # Model fakes deliberately have no HTTP clients.
    monkeypatch.setattr(Mem0Backend, "_configure_clients", lambda self: None)
    backend = Mem0Backend(cfg)
    try:
        with pytest.raises(Exception):
            Mem0Backend(cfg)
        backend.add("我喜欢简短中文回答", "好的", namespace=cfg.namespace, session_id="s", message_id="u", project_root="Raccoon")
        hits = backend.search("response style", namespace=cfg.namespace, limit=3)
        assert hits and hits[0].project_root == "Raccoon"
        assert "BOTH user and assistant" in prompts[0][0]["content"]
        assert "Custom Instructions" not in prompts[0][1]["content"]
    finally:
        backend.close()
    reopened = Mem0Backend(cfg)
    try:
        hits = reopened.list(namespace=cfg.namespace)
        assert len(hits) == 1
        assert not reopened.delete(hits[0].id, namespace="other")
        assert reopened.delete(hits[0].id, namespace=cfg.namespace)
        assert reopened.list(namespace=cfg.namespace) == []
    finally:
        reopened.close()
    with pytest.raises(ValueError, match="embedding"):
        Mem0Backend(replace(cfg, embedding_fingerprint="different"))
