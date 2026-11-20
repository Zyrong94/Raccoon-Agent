import threading

import pytest

from raccoon.modules.agent.background import BackgroundCapacityError, BackgroundJobManager
from raccoon.modules.permissions.sandbox_access import SandboxAccess, SandboxAccessMode
from raccoon.modules.tools.types import make_error_result, make_text_result


def test_background_tool_keeps_its_sandbox_mode_without_leaking_to_completion():
    manager = BackgroundJobManager()
    access = SandboxAccess()
    release = threading.Event()
    observed = []
    manager.bind_completion_sink("owner", lambda note: observed.append(("completion", access.unrestricted)))

    def tool():
        assert release.wait(3)
        observed.append(("tool", access.unrestricted))
        return make_text_result("tool", "done")

    try:
        with access.use_mode(SandboxAccessMode.UNRESTRICTED):
            manager.start(tool, session_id="owner", tool_name="tool")
        assert not access.unrestricted
        release.set()
        assert manager.wait(3)
        assert observed == [("tool", True), ("completion", False)]
    finally:
        release.set()
        manager.shutdown()


def test_completion_sink_reports_snapshot_without_finalizing_plan_and_is_exclusive():
    manager = BackgroundJobManager()
    snapshots, finalized = [], []
    release = threading.Event()
    detach = manager.bind_completion_sink("owner", snapshots.append)
    try:
        with pytest.raises(ValueError, match="sink"):
            manager.bind_completion_sink("owner", lambda note: None)
        job = manager.start(lambda: (release.wait(3), make_text_result("tool", "result"))[1],
                            session_id="owner", tool_name="tool", on_completed=lambda job: finalized.append(threading.get_ident()))
        release.set()
        assert manager.wait(3)
        assert len(snapshots) == 1
        assert snapshots[0].run_id == manager.run_id
        assert snapshots[0].job_id == job.id
        assert finalized == []
        assert manager.collect_completed(session_id="owner") == []
        note = manager.finalize_completed(job.id, session_id="owner")
        again = manager.finalize_completed(job.id, session_id="owner")
        assert note == again
        assert finalized == [threading.get_ident()]
        assert manager.collect_completed() == []
    finally:
        release.set()
        detach()
        manager.shutdown()


def test_wait_does_not_finish_before_the_completion_sink_returns():
    manager = BackgroundJobManager()
    entered, release = threading.Event(), threading.Event()

    def slow_sink(note):
        entered.set()
        assert release.wait(3)

    detach = manager.bind_completion_sink("owner", slow_sink)
    try:
        manager.start(lambda: make_text_result("tool", "result"), session_id="owner", tool_name="tool")
        assert entered.wait(3)
        assert manager.wait(0.01) is False
        release.set()
        assert manager.wait(3)
    finally:
        release.set()
        detach()
        manager.shutdown()


def test_manager_enforces_capacity() -> None:
    manager = BackgroundJobManager(max_jobs=1, max_workers=2)
    release = threading.Event()
    try:
        manager.start(lambda: (release.wait(5), make_text_result("shell", "done"))[1], tool_name="shell")
        with pytest.raises(BackgroundCapacityError):
            manager.start(lambda: make_text_result("shell", "second"), tool_name="shell")
        release.set()
        assert manager.wait(timeout=5) is True
    finally:
        release.set()
        manager.shutdown()


def test_manager_marks_failed_when_tool_result_not_ok() -> None:
    manager = BackgroundJobManager()
    try:
        manager.start(lambda: make_error_result("diagnostics", "boom"), tool_name="diagnostics")
        assert manager.wait(timeout=5) is True
        note = manager.collect_completed()[0]
        assert note.status == "failed"
        assert note.ok is False
        assert "boom" in note.summary
    finally:
        manager.shutdown()
