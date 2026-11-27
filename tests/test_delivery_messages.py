from types import SimpleNamespace

import json
import pytest

from raccoon.runtime.background import STATUS_RUNNING
from raccoon.application.delivery.messages import display_messages, running_tool_call_ids
from raccoon.modules.context.context_builder import ContextBuilder
from raccoon.modules.sessions.models import AgentMessage, MessagePart, SessionData
from raccoon.modules.sessions.store import SessionStore
from raccoon.runtime.model_types import message_tool_calls


@pytest.mark.parametrize("arguments", ["", "{bad json"])
def test_corrupt_tool_arguments_remain_visible_but_fail_when_read_for_execution(arguments):
    part = MessagePart("call-part", "answer", "tool_call", "", {
        "tool_call_id": "call_1", "tool_name": "read_file", "arguments": arguments,
    })
    view = SessionData("session", messages=[AgentMessage("answer", "assistant", [part])])

    assert display_messages(view)[0]["calls"][0]["arguments"] == arguments
    with pytest.raises(json.JSONDecodeError):
        message_tool_calls([part])


def test_valid_tool_arguments_are_parsed_for_display_and_execution():
    part = MessagePart("call-part", "answer", "tool_call", "", {
        "tool_call_id": "call_1", "tool_name": "read_file", "arguments": '{"path":"README.md"}',
    })
    view = SessionData("session", messages=[AgentMessage("answer", "assistant", [part])])

    assert display_messages(view)[0]["calls"][0]["arguments"] == {"path": "README.md"}
    assert message_tool_calls([part])[0].arguments == {"path": "README.md"}


def test_display_projection_keeps_only_visible_roles_and_parts():
    view = SessionData("session", messages=[
        AgentMessage("internal", "system", [
            MessagePart("internal-text", "internal", "text", "system instructions"),
        ]),
        AgentMessage("answer", "assistant", [
            MessagePart("text", "answer", "text", "visible answer"),
            MessagePart("opaque", "answer", "reasoning", "", {"reasoning_item": {"encrypted_content": "secret model state"}}),
        ]),
    ])

    assert display_messages(view) == [
        {"id": "answer", "role": "assistant", "content": "visible answer", "calls": []},
    ]


def test_agent_status_reaches_the_model_but_stays_out_of_displayed_history(tmp_path):
    store = SessionStore(tmp_path)
    data = store.create()
    store.append_agent_status(data, "<agent_status>internal note</agent_status>")
    user_text = "<agent_status>quoted by the user</agent_status>"
    user_id = store.append_user_message(data, user_text, user_timezone="UTC")

    restored = store.load(data.session_id)
    assert display_messages(restored) == [
        {"id": user_id, "role": "user", "content": user_text, "calls": []},
    ]
    model_messages = ContextBuilder().build_provider_messages(restored)
    assert any(message.content == "<agent_status>internal note</agent_status>" for message in model_messages)


def test_ui_event_masking_and_attachments_use_the_injected_capability():
    messages = [
        AgentMessage("native", "user", [
            MessagePart("native-text", "native", "text", "private action payload"),
        ], metadata={"source_event_id": "genui-action:receipt"}),
        AgentMessage("channel", "user", [
            MessagePart("channel-text", "channel", "text", "private channel payload"),
        ], metadata={"source_event_id": "qq-ui-event"}),
        AgentMessage("file", "user", [
            MessagePart("file-part", "file", "file", "", {"attachment_id": "upload"}),
        ]),
    ]
    attachment = {"id": "upload", "download_url": "/existing/download"}
    seen = []

    def for_parts(parts):
        seen.append(parts)
        return [attachment] if parts is messages[-1].parts else []

    projected = display_messages(SessionData("session", messages=messages),
        attachments=SimpleNamespace(for_parts=for_parts), ui_event_ids={"qq-ui-event"})

    assert [item["content"] for item in projected] == [
        "已提交界面中的选择与填写内容", "已提交界面中的选择与填写内容", "",
    ]
    assert projected[-1] == {"id": "file", "role": "user", "content": "", "calls": [],
                             "attachments": [attachment]}
    assert all(parts is message.parts for parts, message in zip(seen, messages, strict=True))


def test_running_tool_ids_query_the_session_and_handle_missing_manager():
    queried = []

    def list_jobs(*, session_id):
        queried.append(session_id)
        return [
            SimpleNamespace(status=STATUS_RUNNING, tool_call_id="running-call"),
            SimpleNamespace(status="succeeded", tool_call_id="completed-call"),
            SimpleNamespace(status=STATUS_RUNNING, tool_call_id=None),
        ]

    runner = SimpleNamespace(background_manager=SimpleNamespace(list=list_jobs))

    assert running_tool_call_ids(runner, "selected-session") == ["running-call"]
    assert queried == ["selected-session"]
    assert running_tool_call_ids(SimpleNamespace(), "selected-session") == []
    assert running_tool_call_ids(SimpleNamespace(background_manager=None), "selected-session") == []
