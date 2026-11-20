"""QQ mode commands switch the current session policy and sandbox."""

from raccoon.app.session_runtime import SessionRuntime
from raccoon.application.conversations.permission_commands import PermissionCommandHandler
from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.permissions.sandbox_access import SandboxAccess, SandboxAccessMode
from raccoon.modules.permissions.types import PermissionMode
from raccoon.modules.sessions.store import SessionStore
from tests.scripted_providers import FakeProvider


def test_mode_command_switches_permissions_and_restores_project_sandbox(tmp_path):
    access = SandboxAccess()
    session = new_session(store=SessionStore(tmp_path / ".raccoon"), session_id="mode",
                          project_root=tmp_path, tools=[], sandbox_access=access, user_timezone="UTC")
    handler = PermissionCommandHandler(SessionRuntime(session, FakeProvider([])))
    assert "Permission mode: ask" in handler.handle("/mode").output
    handler.handle("/mode bypass")
    assert session.permission_manager.mode == PermissionMode.BYPASS
    assert access.mode == SandboxAccessMode.UNRESTRICTED
    handler.handle("/mode ask")
    assert session.permission_manager.mode == PermissionMode.ASK
    assert access.mode == SandboxAccessMode.PROJECT
    assert "Unknown permission mode" in handler.handle("/mode invalid").output
    assert session.permission_manager.mode == PermissionMode.ASK
