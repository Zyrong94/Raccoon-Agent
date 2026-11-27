"""A saved PDF reaches Responses only when file input is enabled."""

import base64

from raccoon.integrations.providers.openai_responses import OpenAIResponsesProvider
from raccoon.modules.context.context_builder import ContextBuilder
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.sessions.user_attachments import UserAttachment


def test_reopened_pdf_uses_provider_file_input_only_when_supported(tmp_path):
    source = tmp_path / "paper.pdf"
    raw = b"%PDF-1.7\nexample"
    source.write_bytes(raw)
    store = SessionStore(tmp_path / "history")
    data = store.create(session_id="chat")
    attachment = UserAttachment("file", source, source.name, "application/pdf", len(raw))
    store.append_user_message(data, "Read this", attachments=[attachment], user_timezone="UTC")
    view = SessionStore(store.root).load("chat")

    builder = ContextBuilder()
    without_pdf = builder.build_provider_messages(view, store_root=store.root, supports_vision=True)
    assert not any(part.type == "file" for part in without_pdf[0].content_parts or [])
    assert "未发送" in without_pdf[0].content
    with_pdf = builder.build_provider_messages(view, store_root=store.root, supports_pdf_input=True)
    provider = OpenAIResponsesProvider(name="offline", model="test", api_key="unused")
    wire = provider._build_input_items(with_pdf)[0]
    file = next(part for part in wire["content"] if part["type"] == "input_file")
    assert file["filename"] == "paper.pdf"
    assert base64.b64decode(file["file_data"].split(",", 1)[1]) == raw
