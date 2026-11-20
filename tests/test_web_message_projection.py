from raccoon.modules.sessions.models import AgentMessage, MessagePart, SessionData
from raccoon.application.delivery.messages import display_messages


def test_same_name_results_keep_record_ids_and_expose_distinct_call_ids():
    view = SessionData("session", messages=[AgentMessage("message", "tool", [
        MessagePart("part-a", "message", "tool_result", "first", {"tool_name": "read", "tool_call_id": "call-a", "ok": True}),
        MessagePart("part-b", "message", "tool_result", "second", {"tool_name": "read", "tool_call_id": "call-b", "ok": False}),
    ])])

    assert display_messages(view) == [
        {"id": "part-a", "role": "tool", "content": "first", "name": "read", "ok": True, "tool_call_id": "call-a"},
        {"id": "part-b", "role": "tool", "content": "second", "name": "read", "ok": False, "tool_call_id": "call-b"},
    ]


def test_legacy_result_without_call_metadata_keeps_existing_projection():
    view = SessionData("session", messages=[AgentMessage("message", "tool", [
        MessagePart("legacy-part", "message", "tool_result", "result", {"tool_name": "read", "ok": True}),
    ])])

    assert display_messages(view) == [
        {"id": "legacy-part", "role": "tool", "content": "result", "name": "read", "ok": True},
    ]
