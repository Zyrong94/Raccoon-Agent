"""Tool-batch state must not leak into the next model response."""
import asyncio
from types import SimpleNamespace

import pytest

from raccoon.modules.agent import tool_execution
from raccoon.runtime.cancellation import AgentCancelledError, CancellationToken
from raccoon.runtime.model_types import ToolCall


@pytest.mark.parametrize("error", [None, ValueError("invalid controls"), AgentCancelledError()])
def test_batch_releases_alias_scope_on_success_error_and_cancellation(monkeypatch, error):
    session = SimpleNamespace(loaded_tool_definitions=lambda: {})
    run = SimpleNamespace(session=session, _background_alias_scope=None)
    def normalize(actual_session, calls):
        assert actual_session is session
        assert run._background_alias_scope is not None
        if error is not None:
            raise error
        return [], {}
    monkeypatch.setattr(tool_execution, "_normalize_background_controls", normalize)
    if error is None:
        asyncio.run(tool_execution.execute_tool_batch(run, []))
    else:
        with pytest.raises(type(error)):
            asyncio.run(tool_execution.execute_tool_batch(run, []))
    assert run._background_alias_scope is None
    assert not hasattr(run, "_background_request")


def test_checked_tool_honors_cancellation_before_executor_runs(monkeypatch):
    token = CancellationToken()
    token.cancel()
    def forbidden(*args):
        raise AssertionError("Cancelled tool was executed")
    monkeypatch.setattr(tool_execution, "execute_tool", forbidden)
    with pytest.raises(AgentCancelledError):
        tool_execution._execute_checked_tool(object(), ToolCall('call', 'read', {}), token)
