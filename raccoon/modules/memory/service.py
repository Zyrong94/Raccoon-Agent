"""One serialized, bounded worker for each local memory directory."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Any, Callable

from raccoon.modules.sessions.records import RedactionOptions
from raccoon.modules.sessions.redaction import redact_text


_REGISTRY: dict[Path, _Store] = {}
_REGISTRY_LOCK = Lock()
_REDACTION = RedactionOptions(redact_paths=False)


@dataclass
class _Job:
    operation: str
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    done: Event = field(default_factory=Event)
    result: Any = None
    cancelled: bool = False


class _Store:
    def __init__(self, path, config, backend_factory):
        self.path = path
        self.config = config
        self.factory = backend_factory
        self.references = 0
        self.queue: Queue[_Job] = Queue(maxsize=8)
        self.lock = Lock()
        self.stopping = Event()
        self.finished = Event()
        self.active = False
        self.error = ""
        self.write_error = ""
        self.unavailable = getattr(config, "unavailable_reason", None)
        self.seen: OrderedDict[tuple[str, str], None] = OrderedDict()
        self.thread = Thread(target=self._run, name="raccoon-memory", daemon=True)
        self.thread.start()

    def enqueue(self, job, *, dedup=None):
        with self.lock:
            if self.stopping.is_set() or self.unavailable:
                return False
            if dedup is not None and dedup in self.seen:
                return False
            try:
                self.queue.put_nowait(job)
            except Full:
                self.error = "queue full; memory operation skipped"
                return False
            if dedup is not None:
                self.seen[dedup] = None
                # Bounded same-process protection; not a durable delivery queue.
                if len(self.seen) > 4096:
                    self.seen.popitem(last=False)
            return True

    def _run(self):
        backend = None
        try:
            while not self.stopping.is_set() or not self.queue.empty():
                try:
                    job = self.queue.get(timeout=0.05)
                except Empty:
                    continue
                if job.cancelled:
                    job.done.set()
                    continue
                self.active = True
                try:
                    if not self.unavailable:
                        if backend is None:
                            try:
                                backend = self.factory(self.config)
                            except Exception as exc:
                                from raccoon.modules.memory.backend import MemoryStorageError
                                self.unavailable = str(exc) if isinstance(exc, MemoryStorageError) else f"initialization failed ({type(exc).__name__}); check SDK installation, memory endpoints, credentials and storage lock"
                        if backend is not None:
                            job.result = getattr(backend, job.operation)(*job.args, **job.kwargs)
                            if job.operation == "add":
                                self.write_error = ""
                            else:
                                self.error = ""
                except Exception as exc:
                    # Exception messages may contain HTTP request credentials/content.
                    error = f"{job.operation} failed ({type(exc).__name__})"
                    if job.operation == "add":
                        self.write_error = error
                    else:
                        self.error = error
                finally:
                    self.active = False
                    job.done.set()
        finally:
            try:
                if backend is not None:
                    backend.close()
            finally:
                with _REGISTRY_LOCK:
                    if _REGISTRY.get(self.path) is self:
                        del _REGISTRY[self.path]
                self.finished.set()


class MemoryService:
    def __init__(self, store, config):
        self._store = store
        self.config = config
        self._closed = False
        self._close_lock = Lock()

    def _request(self, operation, *args, default=None, **kwargs):
        if self._closed:
            return default
        job = _Job(operation, args, dict(namespace=self.config.namespace, **kwargs))
        if not self._store.enqueue(job):
            return default
        if not job.done.wait(self.config.search_timeout_seconds):
            # Queued reads are disposable. Already running SDK calls finish serially.
            job.cancelled = True
            self._store.error = f"{operation} timed out"
            return default
        return job.result if job.result is not None else default

    def search(self, query: str) -> str:
        hits = self._request("search", redact_text(query, _REDACTION), limit=self.config.top_k, default=[])
        if not hits:
            return ""
        lines, seen = [], set()
        for hit in hits:
            if hit.id in seen or not hit.text:
                continue
            seen.add(hit.id)
            source = redact_text(getattr(hit, "project_root", ""), _REDACTION)
            text = redact_text(hit.text, _REDACTION)
            lines.append((f"- [project: {source}] " if source else "- ") + text)
        if not lines:
            return ""
        # The official README labels the recalled facts this way; no custom rules.
        block = "User Memories:\n" + "\n".join(lines)
        return block[: self.config.max_context_tokens * 4]

    def submit_turn(self, user_text, assistant_text, *, session_id, message_id, project_root="") -> bool:
        if self._closed or not user_text.strip() or not assistant_text.strip():
            return False
        job = _Job("add", (redact_text(user_text, _REDACTION), redact_text(assistant_text, _REDACTION)),
                   dict(namespace=self.config.namespace, session_id=session_id,
                        message_id=message_id, project_root=project_root))
        return self._store.enqueue(job, dedup=(session_id, message_id))

    def list(self, limit=20):
        return self._request("list", limit=max(1, min(int(limit), 100)), default=[])

    def delete(self, memory_id):
        return self._request("delete", memory_id, default=False)

    def status(self) -> str:
        if self._closed:
            return "Memory: closed"
        store = self._store
        if store.unavailable:
            return f"Memory: unavailable — {store.unavailable}"
        state = "busy" if store.active or not store.queue.empty() else "ready (lazy initialization)"
        detail = "".join(f"; {error}" for error in (store.error, store.write_error) if error)
        return f"Memory: enabled; personal shared store; Qdrant local; {state}{detail}"

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        with _REGISTRY_LOCK:
            store = self._store
            store.references -= 1
            last = store.references == 0
            if last:
                store.stopping.set()
        if last:
            store.finished.wait(0.1)


def acquire_memory(config, *, backend_factory: Callable | None = None) -> MemoryService:
    if backend_factory is None:
        from raccoon.modules.memory.backend import Mem0Backend
        backend_factory = Mem0Backend
    path = Path(config.storage_path).resolve()
    with _REGISTRY_LOCK:
        store = _REGISTRY.get(path)
        if store is not None:
            if store.stopping.is_set():
                raise ValueError("Memory storage is closing; retry after the active operation finishes")
            if store.config.backend_fingerprint != config.backend_fingerprint:
                raise ValueError("Conflicting memory backend configuration for the same storage directory")
        else:
            store = _Store(path, config, backend_factory)
            _REGISTRY[path] = store
        store.references += 1
    return MemoryService(store, config)
