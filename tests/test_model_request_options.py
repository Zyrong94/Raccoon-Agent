"""Model options reach the normal non-streaming request."""

import asyncio

from raccoon.app.payloads import MessageInput
from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.sessions.store import SessionStore

from tests.scripted_providers import FakeProvider, response


def test_session_turn_passes_selected_model_options(tmp_path):
    effort = "medium"
    provider = FakeProvider([response()])
    runtime = SessionRuntime(new_session(store=SessionStore(tmp_path)), provider, reasoning_effort=effort)
    try:
        asyncio.run(runtime.run_turn(MessageInput("input", "Check README"), lambda: ()))
    finally:
        runtime.close()

    request = provider.requests[0]
    assert request.reasoning_effort == "medium"
