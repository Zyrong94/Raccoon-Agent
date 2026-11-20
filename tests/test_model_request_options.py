"""Model options reach the normal non-streaming request."""

import asyncio

from raccoon.app.payloads import MessageInput
from raccoon.app.session_runtime import SessionRuntime
from raccoon.bootstrap.agent_session import new_session
from raccoon.modules.sessions.store import SessionStore
from raccoon.runtime.model_types import MainRequestOptions
from tests.scripted_providers import FakeProvider, response


def test_session_turn_passes_selected_model_options(tmp_path):
    options = MainRequestOptions(temperature=0.3, max_tokens=4096, extra_body={"reasoning_effort": "medium"})
    provider = FakeProvider([response()])
    runtime = SessionRuntime(new_session(store=SessionStore(tmp_path)), provider, request_options=options)
    try:
        asyncio.run(runtime.run_turn(MessageInput("input", "Check README"), lambda: ()))
    finally:
        runtime.close()

    request = provider.requests[0]
    assert request.temperature == 0.3
    assert request.max_tokens == 4096
    assert request.extra_body["reasoning_effort"] == "medium"
