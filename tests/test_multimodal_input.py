"""Multimodal user input: attachments, projection, and provider encoding."""

from __future__ import annotations

from pathlib import Path
from copy import deepcopy

import pytest

from raccoon.modules.context.context_builder import ContextBuilder
from raccoon.modules.sessions.models import AgentMessage, MessagePart, SessionData
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.sessions.user_attachments import UserAttachment, guess_media_type, prepare_attachments_for_session
from raccoon.integrations.providers.openai_responses import OpenAIResponsesProvider
from raccoon.runtime.model_types import ChatMessage, ContentPart

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_session(path):
    store = SessionStore(path)
    return store, store.create(session_id="sess_multi")


def local_attachment(path):
    media_type = guess_media_type(path)
    kind = "image" if media_type.startswith("image/") else "file"
    return UserAttachment(kind, path, path.name, media_type, path.stat().st_size)


def test_prepare_and_append_image_attachment(tmp_path: Path) -> None:
    image = tmp_path / "a.png"
    image.write_bytes(PNG_1x1)
    store, data = _make_session(tmp_path / "store")
    attachment = local_attachment(image)
    message_id = store.append_user_message(data, user_timezone="UTC", content="look at this", attachments=[attachment])
    view = store.load(data.session_id)
    message = next(item for item in view.messages if item.id == message_id)
    kinds = [part.kind for part in message.parts]
    assert "text" in kinds
    assert "image" in kinds
    image_part = next(part for part in message.parts if part.kind == "image")
    assert image_part.metadata["filename"] == "a.png"
    stored = store.root / image_part.metadata["path"]
    assert stored.is_file()
    assert stored.read_bytes() == PNG_1x1


def test_context_builder_projects_image_content_parts(tmp_path: Path) -> None:
    image = tmp_path / "b.png"
    image.write_bytes(PNG_1x1)
    store_root = tmp_path / "store"
    prepared = prepare_attachments_for_session(
        [local_attachment(image)],
        store_root=store_root,
        session_id="sess_x",
    )[0]
    view = SessionData(
        session_id="sess_x",
        messages=[
            AgentMessage(
                id="msg_user",
                role="user",
                parts=[
                    MessagePart(id="p1", message_id="msg_user", kind="text", content="describe"),
                    MessagePart(
                        id="p2",
                        message_id="msg_user",
                        kind="image",
                        content="[image: b.png]",
                        metadata={
                            "filename": prepared.filename,
                            "media_type": prepared.media_type,
                            "path": prepared.relative_path,
                            "bytes": prepared.size_bytes,
                        },
                    ),
                ],
            )
        ],
    )
    messages = ContextBuilder().build_provider_messages(view, store_root=store_root)
    assert len(messages) == 1
    message = messages[0]
    assert message.content_parts is not None
    assert message.content_parts[0].type == "text"
    assert "describe" in message.content_parts[0].text
    image_part = next(part for part in message.content_parts if part.type == "image")
    assert image_part.media_type == "image/png"
    assert image_part.data_base64


def test_context_builder_rejects_tampered_absolute_attachment_path(tmp_path: Path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_1x1)
    view = SessionData(
        session_id="sess_x",
        messages=[
            AgentMessage(
                id="msg_user",
                role="user",
                parts=[
                    MessagePart(
                        id="p1",
                        message_id="msg_user",
                        kind="image",
                        content="[image: outside.png]",
                        metadata={
                            "filename": "outside.png",
                            "media_type": "image/png",
                            "absolute_path": str(outside),
                        },
                    )
                ],
            )
        ],
    )

    assert ContextBuilder().build_provider_messages(view, store_root=tmp_path) == []


def test_responses_encodes_multimodal_content() -> None:
    message = ChatMessage(
        role="user",
        content="hi",
        content_parts=[
            ContentPart(type="text", text="hi"),
            ContentPart(type="image", media_type="image/png", data_base64="abc123", filename="x.png"),
        ],
    )
    provider = OpenAIResponsesProvider(name="relay", api_key="test", model="test")
    wire = provider._to_response_input([message])[0]
    assert wire["role"] == "user"
    assert wire["content"][0] == {"type": "input_text", "text": "hi"}
    assert wire["content"][1] == {"type": "input_image", "image_url": "data:image/png;base64,abc123"}


def test_text_file_attachment_is_inlined(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_text("hello attachment\n", encoding="utf-8")
    store, data = _make_session(tmp_path / "store")
    message_id = store.append_user_message(data, user_timezone="UTC", content="read this", attachments=[local_attachment(source)])
    view = store.load(data.session_id)
    message = next(item for item in view.messages if item.id == message_id)
    file_part = next(part for part in message.parts if part.kind == "file")
    assert "hello attachment" in file_part.content
    projected = ContextBuilder().build_provider_messages(view, store_root=store.root)
    assert "hello attachment" in projected[0].content


def test_saved_file_reference_only_changes_model_projection(tmp_path):
    import json
    source = tmp_path / "workbook.xlsx"
    source.write_bytes(b"PK\x03\x04\x00\xff binary workbook fixture")
    store, data = _make_session(tmp_path / "store")
    store.append_user_message(data, user_timezone="UTC", content="inspect workbook", attachments=[local_attachment(source)])
    view = store.load(data.session_id)
    before = deepcopy(view)
    log_before = (store.root / "sessions" / "sess_multi.jsonl").read_bytes()
    part = next(p for p in view.messages[0].parts if p.kind == "file")
    saved = store.root / part.metadata["path"]
    projected = ContextBuilder().build_provider_messages(view, store_root=store.root)
    reference = json.loads(next(line for line in projected[0].content.splitlines() if line.startswith('{"path":')))
    assert Path(reference["path"]) == saved
    assert "workbook.xlsx" in projected[0].content
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in projected[0].content
    assert view == before
    assert (store.root / "sessions" / "sess_multi.jsonl").read_bytes() == log_before
    from raccoon.application.delivery.messages import display_messages
    assert display_messages(view)[0]["content"] == "inspect workbook"


@pytest.mark.parametrize("tamper", ["foreign", "absolute", "missing", "digest"])
def test_file_reference_does_not_claim_unverified_paths_are_readable(tmp_path, tamper):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4\nfile fixture")
    store, data = _make_session(tmp_path / "store")
    store.append_user_message(data, user_timezone="UTC", content="read it", attachments=[local_attachment(source)])
    view = store.load(data.session_id)
    part = next(p for p in view.messages[0].parts if p.kind == "file")
    original = store.root / part.metadata["path"]
    forbidden = original
    if tamper == "foreign":
        forbidden = store.root / "attachments" / "sess_other" / "paper.pdf"
        forbidden.parent.mkdir()
        forbidden.write_bytes(source.read_bytes())
        part.metadata["path"] = forbidden.relative_to(store.root).as_posix()
    elif tamper == "absolute":
        part.metadata["path"] = str(source)
        forbidden = source
    elif tamper == "missing":
        original.unlink()
    elif tamper == "digest":
        original.write_bytes(source.read_bytes().replace(b"fixture", b"altered"))
    projected = ContextBuilder().build_provider_messages(view, store_root=store.root, supports_pdf_input=True)
    assert str(forbidden) not in projected[0].content
    assert not any(line.startswith('{"path":') for line in projected[0].content.splitlines())
    assert not any(p.type == "file" for p in projected[0].content_parts or [])
    assert "未发送" in projected[0].content
