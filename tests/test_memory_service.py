from threading import Event
from types import SimpleNamespace

import pytest

from raccoon.modules.memory.service import acquire_memory


def config(path, **overrides):
    values = dict(storage_path=path, namespace="raccoon-personal", top_k=3,
                  max_context_tokens=100, search_timeout_seconds=0.1,
                  write_timeout_seconds=0.1, backend_fingerprint="compatible",
                  unavailable_reason=None)
    return SimpleNamespace(**(values | overrides))


class Backend:
    def __init__(self, config):
        self.calls = []
        self.closed = Event()

    def search(self, query, **kwargs):
        self.calls.append(("search", query, kwargs))
        return [SimpleNamespace(id="one", text="Use concise Chinese responses.", project_root="")]

    def add(self, user, assistant, **kwargs):
        self.calls.append(("add", user, assistant, kwargs))

    def list(self, **kwargs):
        return []

    def delete(self, memory_id, **kwargs):
        return False

    def close(self):
        self.closed.set()


def test_shared_backend_and_last_handle_owns_close(tmp_path):
    instances = []
    def factory(cfg):
        instances.append(Backend(cfg))
        return instances[-1]
    one = acquire_memory(config(tmp_path), backend_factory=factory)
    two = acquire_memory(config(tmp_path), backend_factory=factory)
    assert "concise Chinese" in one.search("style")
    assert "concise Chinese" in two.search("style")
    assert len(instances) == 1
    one.close()
    assert not instances[0].closed.is_set()
    two.close()
    assert instances[0].closed.wait(1)


def test_timeout_does_not_close_active_backend_or_allow_reopening(tmp_path):
    entered, release = Event(), Event()
    class Slow(Backend):
        def search(self, query, **kwargs):
            entered.set()
            release.wait(2)
            return super().search(query, **kwargs)
    backend = Slow(None)
    service = acquire_memory(config(tmp_path, search_timeout_seconds=0.02), backend_factory=lambda _: backend)
    assert service.search("style") == ""
    assert entered.is_set()
    assert "busy" in service.status()
    service.close()
    assert not backend.closed.is_set()
    with pytest.raises(ValueError, match="closing"):
        acquire_memory(config(tmp_path), backend_factory=lambda _: backend)
    release.set()
    assert backend.closed.wait(1)


def test_background_writes_deduplicate_and_redact_secrets(tmp_path):
    backend = Backend(None)
    service = acquire_memory(config(tmp_path), backend_factory=lambda _: backend)
    try:
        assert service.submit_turn("API_KEY=secret-value use C:/code", "Got it", session_id="s", message_id="u", project_root="C:/code")
        assert not service.submit_turn("duplicate", "reply", session_id="s", message_id="u")
        service.list()
        write = backend.calls[0]
        assert write[0] == "add"
        assert "secret-value" not in write[1]
        assert "C:/code" in write[1]
        assert write[3] == dict(namespace="raccoon-personal", session_id="s", message_id="u", project_root="C:/code")
    finally:
        service.close()


def test_conflicting_backend_configuration_rejected(tmp_path):
    service = acquire_memory(config(tmp_path), backend_factory=Backend)
    try:
        with pytest.raises(ValueError, match="configuration"):
            acquire_memory(config(tmp_path, backend_fingerprint="other"), backend_factory=Backend)
    finally:
        service.close()


def test_queue_bounded_and_errors_do_not_leak_secrets(tmp_path):
    entered, release = Event(), Event()
    class Slow(Backend):
        def add(self, *args, **kwargs):
            entered.set()
            release.wait(2)
    service = acquire_memory(config(tmp_path), backend_factory=Slow)
    try:
        assert service.submit_turn("u", "a", session_id="s", message_id="0")
        assert entered.wait(1)
        results = [service.submit_turn("u", "a", session_id="s", message_id=str(i)) for i in range(1, 11)]
        assert sum(results) == 8
    finally:
        release.set()
        service.close()
    def failing(_):
        raise RuntimeError("API_KEY=secret-value")
    failed = acquire_memory(config(tmp_path / "failure"), backend_factory=failing)
    try:
        assert failed.search("query") == ""
        assert "unavailable" in failed.status()
        assert "secret-value" not in failed.status()
    finally:
        failed.close()


def test_context_block_including_label_respects_budget(tmp_path):
    class Large(Backend):
        def search(self, *args, **kwargs):
            return [SimpleNamespace(id="one", text="x" * 2000)]
    service = acquire_memory(config(tmp_path, max_context_tokens=80), backend_factory=Large)
    try:
        block = service.search("style")
        assert "User Memories:" in block
        assert len(block) <= 320
    finally:
        service.close()


def test_close_of_one_handle_is_safe_to_repeat_from_multiple_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    service = acquire_memory(config(tmp_path), backend_factory=Backend)
    other = acquire_memory(config(tmp_path), backend_factory=Backend)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: service.close(), range(30)))
    assert "concise Chinese" in other.search("style")
    other.close()


def test_context_truncation_keeps_project_background(tmp_path):
    class Large(Backend):
        def search(self, *args, **kwargs):
            return [SimpleNamespace(id="one", text="x" * 2000, project_root="project-a")]
    service = acquire_memory(config(tmp_path, max_context_tokens=80), backend_factory=Large)
    try:
        assert "project-a" in service.search("style")
    finally:
        service.close()


def test_write_failure_survives_successful_read_and_known_manifest_error_visible(tmp_path):
    from raccoon.modules.memory.backend import MemoryStorageError
    class BrokenWrite(Backend):
        def add(self, *args, **kwargs): raise RuntimeError("API_KEY=never-show-this")
    service = acquire_memory(config(tmp_path), backend_factory=BrokenWrite)
    try:
        service.submit_turn("u", "a", session_id="s", message_id="m")
        service.list()
        assert "add failed" in service.status()
        assert "never-show-this" not in service.status()
    finally:
        service.close()
    def bad_manifest(_):
        raise MemoryStorageError("Memory embedding configuration changed; migrate explicitly or select a new storage directory")
    service = acquire_memory(config(tmp_path / "bad"), backend_factory=bad_manifest)
    try:
        service.search("q")
        assert "migrate" in service.status()
    finally:
        service.close()
