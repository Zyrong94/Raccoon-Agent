
from raccoon.modules.sessions.compaction import CompactionRecord
from raccoon.modules.context.context_builder import ContextBuilder
from raccoon.modules.sessions.models import AgentMessage, MessagePart, SessionData
from raccoon.runtime.model_types import ChatMessage


def _tool_transaction(suffix: str) -> tuple[AgentMessage, AgentMessage]:
    call_id = f"call_{suffix}"
    call = AgentMessage(
        id=f"msg_call_{suffix}",
        role="assistant",
        parts=[
            MessagePart(
                id=f"part_call_{suffix}",
                message_id=f"msg_call_{suffix}",
                kind="tool_call",
                content="",
                metadata={"tool_call_id": call_id, "tool_name": "echo", "arguments": {}},
            )
        ],
    )
    result = AgentMessage(
        id=f"msg_result_{suffix}",
        role="tool",
        parts=[
            MessagePart(
                id=f"part_result_{suffix}",
                message_id=f"msg_result_{suffix}",
                kind="tool_result",
                content=suffix,
                metadata={"tool_call_id": call_id, "tool_name": "echo"},
            )
        ],
    )
    return call, result


def test_context_builder_projects_internal_messages_to_provider_messages() -> None:
    view = SessionData(
        session_id="sess_test",
        messages=[
            AgentMessage(
                id="msg_user",
                role="user",
                parts=[
                    MessagePart(
                        id="part_user",
                        message_id="msg_user",
                        kind="text",
                        content="读一下 README",
                    )
                ],
            ),
            AgentMessage(
                id="msg_assistant",
                role="assistant",
                parts=[
                    MessagePart(
                        id="part_call",
                        message_id="msg_assistant",
                        kind="tool_call",
                        content="",
                        metadata={
                            "tool_call_id": "call_1",
                            "tool_name": "read_file",
                            "arguments": {"path": "README.md"},
                        },
                    )
                ],
            ),
            AgentMessage(
                id="msg_tool",
                role="tool",
                parts=[
                    MessagePart(
                        id="part_result",
                        message_id="msg_tool",
                        kind="tool_result",
                        content="README 内容预览",
                        metadata={"tool_call_id": "call_1", "tool_name": "read_file"},
                    )
                ],
            ),
        ],
    )

    messages = ContextBuilder().build_provider_messages(
        view,
        system_prefix=[ChatMessage(role="system", content="你是 Raccoon。")],
    )

    assert [message.role for message in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert "读一下 README" in messages[1].content
    assert messages[2].tool_calls[0].name == "read_file"
    assert messages[3].tool_call_id == "call_1"
    assert messages[3].content == "README 内容预览"


def test_context_builder_collapses_identical_adjacent_duplicate_tool_call_before_result() -> None:
    arguments = {"path": "app.py", "old": "old", "new": "new"}
    view = SessionData(
        session_id="sess_duplicate_call",
        messages=[
            AgentMessage(
                id="msg_first",
                role="assistant",
                parts=[
                    MessagePart(
                        id="part_first",
                        message_id="msg_first",
                        kind="tool_call",
                        content="",
                        metadata={
                            "tool_call_id": "call_duplicate",
                            "tool_name": "edit",
                            "arguments": arguments,
                            "prewrite_review_only": True,
                        },
                    )
                ],
            ),
            AgentMessage(
                id="msg_second",
                role="assistant",
                parts=[
                    MessagePart(
                        id="part_second",
                        message_id="msg_second",
                        kind="tool_call",
                        content="",
                        metadata={
                            "tool_call_id": "call_duplicate",
                            "tool_name": "edit",
                            "arguments": arguments,
                        },
                    )
                ],
            ),
            AgentMessage(
                id="msg_result",
                role="tool",
                parts=[
                    MessagePart(
                        id="part_result",
                        message_id="msg_result",
                        kind="tool_result",
                        content="edited",
                        metadata={"tool_call_id": "call_duplicate", "tool_name": "edit"},
                    )
                ],
            ),
        ],
    )

    projected = ContextBuilder().build_provider_messages(view)

    assert [message.role for message in projected] == ["assistant", "tool"]
    assert projected[0].tool_calls[0].id == "call_duplicate"
    assert projected[1].tool_call_id == "call_duplicate"


def test_projected_tool_result_part_ids_only_include_effective_compaction_tail() -> None:
    old_call, old_result = _tool_transaction("old")
    tail_call, tail_result = _tool_transaction("tail")
    view = SessionData(
        session_id="sess_test",
        messages=[old_call, old_result, tail_call, tail_result],
        compactions=[
            CompactionRecord(
                id="ckpt_1",
                session_id="sess_test",
                summary="old",
                first_kept_message_id=tail_call.id,
                created_at="2026-11-16T00:00:00Z",
                tokens_before=100,
            )
        ],
    )

    part_ids = ContextBuilder().projected_tool_result_part_ids(view)

    assert part_ids == (tail_result.parts[0].id,)
