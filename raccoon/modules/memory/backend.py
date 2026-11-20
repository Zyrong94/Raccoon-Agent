"""Adapter for the pinned Mem0 SDK; extraction prompts remain official defaults."""

from __future__ import annotations

import json
import os
import logging

from raccoon.modules.memory.models import MemoryHit


class MemoryStorageError(ValueError):
    """Known safe diagnostic that can be shown without SDK request details."""


class Mem0Backend:
    def __init__(self, config, *, memory=None):
        self.config = config
        self.memory = memory
        self._lock = None
        if memory is not None:
            return
        if config.unavailable_reason:
            raise ValueError(config.unavailable_reason)
        import portalocker
        config.storage_path.mkdir(parents=True, exist_ok=True)
        self._lock = portalocker.Lock(str(config.storage_path / ".raccoon.lock"), timeout=0)
        self._lock.acquire()
        try:
            manifest_path = config.storage_path / "manifest.json"
            manifest = {"schema": 1, "embedding_fingerprint": config.embedding_fingerprint}
            if manifest_path.exists():
                if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
                    raise MemoryStorageError("Memory embedding configuration changed; migrate explicitly or select a new storage directory")
            elif (config.storage_path / "qdrant").exists():
                raise MemoryStorageError("Memory manifest missing for existing storage; select a new directory or restore the manifest")
            os.environ["MEM0_TELEMETRY"] = "false"
            # SDK error/debug messages can contain full inputs or credential-bearing URLs.
            logging.getLogger("mem0").setLevel(logging.CRITICAL + 1)
            from mem0 import Memory
            import mem0.memory.main as main
            import mem0.memory.telemetry as telemetry
            # Also handle an SDK imported earlier in this process.
            main.MEM0_TELEMETRY = telemetry.MEM0_TELEMETRY = False
            sdk_config = {
                "vector_store": {"provider": "qdrant", "config": {
                    "collection_name": "raccoon", "path": str(config.storage_path / "qdrant"),
                    "embedding_model_dims": config.dimensions}},
                "history_db_path": str(config.storage_path / "history.db"),
                "llm": {"provider": "openai", "config": {"model": config.llm.model,
                    "api_key": config.llm.api_key, "openai_base_url": config.llm.base_url}},
                "embedder": {"provider": "openai", "config": {"model": config.embedder.model,
                    "api_key": config.embedder.api_key, "openai_base_url": config.embedder.base_url}},
            }
            self.memory = Memory.from_config(sdk_config)
            self._configure_clients()
            temporary = manifest_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(manifest), encoding="utf-8")
            temporary.replace(manifest_path)
        except BaseException:
            self.close()
            raise

    def _configure_clients(self):
        config = self.config
        from openai import OpenAI
        for component, endpoint in ((self.memory.llm, config.llm), (self.memory.embedding_model, config.embedder)):
            component.client.close()
            component.client = OpenAI(api_key=endpoint.api_key, base_url=endpoint.base_url,
                                      timeout=config.write_timeout_seconds, max_retries=0)
        # Storage dimensions need not be an accepted API parameter.
        self.memory.embedding_model.config.embedding_dims = config.dimensions
        self.memory.embedding_model._pass_dimensions_to_api = config.embedder.model.startswith("text-embedding-3-")

    @staticmethod
    def _hits(response):
        rows = response.get("results", []) if isinstance(response, dict) else response
        if not isinstance(rows, list):
            return []
        return [MemoryHit(str(row["id"]), row["memory"], str((row.get("metadata") or {}).get("project_root", "")))
                for row in rows if isinstance(row, dict) and row.get("id") and isinstance(row.get("memory"), str)]

    def search(self, query, *, namespace, limit):
        return self._hits(self.memory.search(query=query, filters={"user_id": namespace}, top_k=limit))

    def add(self, user_text, assistant_text, *, namespace, session_id, message_id, project_root=""):
        return self.memory.add([{"role": "user", "content": user_text}, {"role": "assistant", "content": assistant_text}],
            user_id=namespace, metadata={"project_root": project_root, "source_session_id": session_id,
                                       "source_message_id": message_id, "source": "raccoon-v1"})

    def list(self, *, namespace, limit=20):
        return self._hits(self.memory.get_all(filters={"user_id": namespace}, top_k=limit))

    def delete(self, memory_id, *, namespace):
        item = self.memory.get(memory_id)
        if not item or item.get("user_id") != namespace:
            return False
        self.memory.delete(memory_id)
        return True

    def close(self):
        try:
            if self.memory is not None:
                memory, self.memory = self.memory, None
                try:
                    memory.close()
                finally:
                    client = getattr(getattr(memory, "vector_store", None), "client", None)
                    if client is not None:
                        client.close()
                    for component in (getattr(memory, "llm", None), getattr(memory, "embedding_model", None)):
                        client = getattr(component, "client", None)
                        if client is not None:
                            client.close()
        finally:
            if self._lock is not None:
                self._lock.release()
                self._lock = None
