"""Tool images stay with the matching result from JSONL to Responses."""

import asyncio
import base64
from raccoon.runtime.model_types import MessagePart
from io import BytesIO

from PIL import Image

from raccoon.modules.context.context_builder import ContextBuilder
from raccoon.modules.sessions.fork import fork_session
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.tools.types import ToolImage, ToolResult
from raccoon.runtime.model_types import ChatRequest, ChatResponse, ToolCall
from tests.test_responses_provider import provider, response


def test_tool_image_survives_reload_and_stays_with_its_call(tmp_path):
    stream = BytesIO()
    Image.new("RGB", (2, 2), "red").save(stream, format="PNG")
    image_bytes = stream.getvalue()

    store = SessionStore(tmp_path)
    data = store.create()
    call = ToolCall("render", "view_pdf_pages", {})
    store.append_assistant_response(data, ChatResponse(
        provider="fake",
        model="fake",
        parts=[MessagePart.from_tool_call(call) for call in ([call])],
    ))
    store.append_tool_result(
        data,
        tool_call=call,
        result=ToolResult("view_pdf_pages", True, "Rendered", images=[
            ToolImage(image_bytes, "image/png", "paper.pdf", page=2),
        ]),
    )

    forked_id = fork_session(store, tmp_path, data.session_id)
    restored = store.load(forked_id)
    builder = ContextBuilder()
    visible = builder.build_provider_messages(restored, supports_vision=True)
    assert [message.role for message in visible] == ["assistant", "tool"]
    assert visible[-1].tool_call_id == call.id

    model, endpoint = provider(response([]))
    asyncio.run(model.acomplete(ChatRequest(messages=visible)))
    call_result = endpoint.requests[0]["input"][-1]
    assert call_result["type"] == "function_call_output"
    assert call_result["call_id"] == call.id
    sent_image = next(part for part in call_result["output"] if part["type"] == "input_image")
    assert base64.b64decode(sent_image["image_url"].split(",", 1)[1]) == image_bytes

    text_only = builder.build_provider_messages(restored)
    assert text_only[-1].tool_call_id == call.id
    assert text_only[-1].content_parts is None
    assert "未发送" in text_only[-1].content


def test_invalid_tool_image_is_recorded_as_a_failed_result(tmp_path):
    store = SessionStore(tmp_path)
    data = store.create()
    call = ToolCall("render", "view_pdf_pages", {})
    store.append_assistant_response(data, ChatResponse(
        provider="fake",
        model="fake",
        parts=[MessagePart.from_tool_call(call) for call in ([call])],
    ))
    result = ToolResult("view_pdf_pages", True, "Rendered", images=[
        ToolImage(b"invalid image", "image/png", "paper.pdf"),
    ])
    store.append_tool_result(data, tool_call=call, result=result)

    part = store.load(data.session_id).messages[-1].parts[0]
    assert part.metadata["tool_call_id"] == call.id
    assert part.metadata["ok"] is False
    assert "images" not in part.metadata
