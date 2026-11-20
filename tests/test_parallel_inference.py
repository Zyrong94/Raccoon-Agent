"""An auxiliary conversation is read-only even when its parent can write."""

from concurrent.futures import ThreadPoolExecutor

from raccoon.application.conversations.parallel_inference import PARALLEL_TOOL_ALLOWLIST, ParallelBranchRunner
from raccoon.modules.permissions.manager import PermissionManager
from raccoon.modules.permissions.policy import DefaultPermissionPolicy
from raccoon.modules.sessions.store import SessionStore
from tests.scripted_providers import FakeProvider


def test_parallel_tool_catalog_keeps_the_read_only_boundary(tmp_path):
    runner = ParallelBranchRunner(
        session_id="parent", store=SessionStore(tmp_path / ".raccoon"), project_root=tmp_path,
        provider=FakeProvider([]), request_options=None,
        permission_manager=PermissionManager(policy=DefaultPermissionPolicy(tmp_path)),
        sandbox_access=None, parent_tool_names={"ls", "write", "shell", "delegate", "diagnostics", "web_fetch"},
    )
    names = {tool.name for tool in runner._build_tools(runner.sandbox_access)}
    assert set(PARALLEL_TOOL_ALLOWLIST) <= names
    assert "web_fetch" in names
    assert not {"write", "shell", "delegate", "diagnostics"} & names


def test_concurrent_store_instances_append_complete_jsonl_events(tmp_path):
    first = SessionStore(tmp_path)
    data = first.create(session_id="shared")
    second = SessionStore(tmp_path)
    other = second.load(data.session_id)

    def append_many(store, record, label):
        for number in range(10):
            store.append_session_metadata_updated(record, title=f"{label} {number}")

    with ThreadPoolExecutor(max_workers=2) as workers:
        a = workers.submit(append_many, first, data, "parent")
        b = workers.submit(append_many, second, other, "branch")
        a.result()
        b.result()

    assert len(first.list_events(data.session_id)) == 21
    assert first.load(data.session_id).metadata["title"] in {"parent 9", "branch 9"}
