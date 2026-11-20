from types import SimpleNamespace

from raccoon.modules.memory.backend import Mem0Backend
from raccoon.modules.memory.config import load_memory_config
from raccoon.config.settings import AppConfig


def test_adapter_uses_official_defaults_and_checks_delete_ownership(tmp_path):
    class SDK:
        def __init__(self): self.calls = []
        def add(self, messages, **kw): self.calls.append((messages, kw))
        def search(self, **kw): return {"results": [{"id": "a", "memory": "Prefer Chinese", "metadata": {"project_root": "project-a"}}]}
        def get_all(self, **kw): return {"results": []}
        def get(self, id): return {"user_id": "different" if id == "other" else "raccoon-personal"}
        def delete(self, id): self.calls.append(id)
        def close(self): pass
    cfg = load_memory_config(AppConfig(env={}), project_root=tmp_path, data_root=tmp_path)
    sdk = SDK()
    backend = Mem0Backend(cfg, memory=sdk)
    backend.add("hello", "reply", namespace=cfg.namespace, session_id="s", message_id="u", project_root="project-a")
    messages, kw = sdk.calls[0]
    assert messages == [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "reply"}]
    assert kw["user_id"] == "raccoon-personal"
    assert kw["metadata"]["project_root"] == "project-a"
    assert not {"prompt", "custom_instructions", "run_id"} & kw.keys()
    assert backend.search("style", namespace=cfg.namespace, limit=3)[0].project_root == "project-a"
    assert not backend.delete("other", namespace=cfg.namespace)
    assert backend.delete("mine", namespace=cfg.namespace)
