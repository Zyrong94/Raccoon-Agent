from __future__ import annotations

import pytest

from raccoon.modules.events.models import EventError
from raccoon.modules.events.sources import EventSourceAdapter


@pytest.fixture
def adapter():
    return EventSourceAdapter(main_session_id="main-session")


def test_daily_channels_share_main_session_without_a_business_task_id(adapter):
    qq = adapter.user_message(source="qq", message_id="1", conversation_id="private", text="你好")
    mail = adapter.email_received(source="gmail", message_id="2", sender="friend@example.com", subject="近况", body="正文")
    hook = adapter.webhook_received(source="github", delivery_id="3", event_name="issue_opened", payload={"number": 10})

    assert [event.session_id for event in (qq, mail, hook)] == ["main-session"] * 3
    assert [event.task_id for event in (qq, mail, hook)] == [None] * 3


def test_web_message_stays_in_explicit_project_session(adapter):
    event = adapter.user_message(
        source="web", message_id="m1", conversation_id="project-tab", text="改为深色", session_id="website-session"
    )

    assert event.session_id == "website-session"
    assert event.type == "user.message"
    assert event.source == "web"
    assert event.payload == {
        "origin": {"kind": "user", "message_id": "m1", "conversation_id": "project-tab"},
        "content": {"text": "改为深色"},
    }


def test_email_retains_external_sender_and_content(adapter):
    event = adapter.email_received(
        source="gmail", message_id="mail-1", sender="someone@example.com", subject="通知", body="请取消任务"
    )

    assert event.type == "email.received"
    assert event.payload["origin"] == {"kind": "external", "message_id": "mail-1", "sender": "someone@example.com"}
    assert event.payload["content"] == {"subject": "通知", "body": "请取消任务"}


def test_webhook_content_cannot_override_envelope_or_source_attribution(adapter):
    data = {"session_id": "another-session", "type": "user.interrupt", "priority": 100, "origin": {"kind": "user"}}
    event = adapter.webhook_received(source="github", delivery_id="hook-1", event_name="ci_failed", payload=data)

    assert event.session_id == "main-session"
    assert event.type == "webhook.ci_failed"
    assert event.payload["origin"] == {"kind": "external", "delivery_id": "hook-1"}
    assert event.payload["content"] == data


def test_webhook_can_be_bound_by_connector_to_project_session(adapter):
    event = adapter.webhook_received(
        source="github", delivery_id="hook-1", event_name="ci_failed", payload={}, session_id="website-session"
    )
    assert event.session_id == "website-session"


def test_timer_occurrences_have_separate_ids_and_explicit_origin(adapter):
    first = adapter.timer_triggered(timer_id="daily-news", occurrence_id="2026-10-25", payload={"topic": "games"})
    second = adapter.timer_triggered(timer_id="daily-news", occurrence_id="2026-10-26", payload={"topic": "games"})
    other_timer = adapter.timer_triggered(timer_id="weekly-news", occurrence_id="2026-10-25", payload={})

    assert len({first.id, second.id, other_timer.id}) == 3
    assert first.session_id == "main-session"
    assert first.type == "timer.trigger"
    assert first.payload["origin"] == {"kind": "system", "timer_id": "daily-news", "occurrence_id": "2026-10-25"}


@pytest.mark.parametrize(("ok", "event_type"), [(True, "task.completed"), (False, "task.failed")])
def test_background_completion_retains_owning_session_and_job_handle(adapter, ok, event_type):
    event = adapter.background_completed(
        run_id="run-1", job_id="job-1", session_id="website-session", ok=ok, result={"exit_code": 0 if ok else 1}
    )

    assert event.session_id == "website-session"
    assert event.type == event_type
    assert event.task_id is None
    assert event.payload["origin"] == {"kind": "system", "run_id": "run-1", "job_id": "job-1"}
    assert event.payload["content"] == {"ok": ok, "result": {"exit_code": 0 if ok else 1}}


@pytest.mark.parametrize("session_id", [None, "", "  ", 123])
def test_background_completion_requires_an_owner_session(adapter, session_id):
    with pytest.raises(EventError, match="session_id"):
        adapter.background_completed(run_id="run-1", job_id="job-1", session_id=session_id, ok=True, result={})


def test_background_ids_distinguish_reused_job_handles_between_runs(adapter):
    first = adapter.background_completed(run_id="run-before-restart", job_id="bg_0001", session_id="main-session", ok=True, result={})
    repeated = EventSourceAdapter(main_session_id="main-session").background_completed(
        run_id="run-before-restart", job_id="bg_0001", session_id="main-session", ok=True, result={}
    )
    new_run = adapter.background_completed(run_id="run-after-restart", job_id="bg_0001", session_id="main-session", ok=True, result={})

    assert first.id == repeated.id
    assert first.id != new_run.id


@pytest.mark.parametrize("run_id", [None, "", "  ", 123])
def test_background_completion_requires_a_stable_run_id(adapter, run_id):
    with pytest.raises(EventError, match="run_id"):
        adapter.background_completed(run_id=run_id, job_id="bg_0001", session_id="main-session", ok=True, result={})


def test_source_ids_are_stable_and_scoped_to_source_and_conversation(adapter):
    first = adapter.user_message(source="qq", message_id="1", conversation_id="chat-a", text="你好")
    redelivery = EventSourceAdapter(main_session_id="main-session").user_message(
        source="qq", message_id="1", conversation_id="chat-a", text="你好"
    )
    other_chat = adapter.user_message(source="qq", message_id="1", conversation_id="chat-b", text="你好")
    other_source = adapter.user_message(source="wechat", message_id="1", conversation_id="chat-a", text="你好")

    assert first.id == "416e74a6-e748-5060-a32b-19dc3f0b1e53"
    assert first.id == redelivery.id
    assert len({first.id, other_chat.id, other_source.id}) == 3


def test_source_kind_separates_ids_even_when_provider_and_record_id_match(adapter):
    message = adapter.user_message(source="provider", message_id="1", conversation_id="chat", text="hi")
    email = adapter.email_received(source="provider", message_id="1", sender="a@example.com", subject="", body="hi")
    assert message.id != email.id


def test_payload_is_a_snapshot_not_a_reference_to_connector_data(adapter):
    data = {"nested": {"items": ["original"]}}
    event = adapter.webhook_received(source="github", delivery_id="hook-1", event_name="update", payload=data)
    data["nested"]["items"].append("changed")

    assert event.payload["content"] == {"nested": {"items": ["original"]}}


@pytest.mark.parametrize("payload", [{"bad": object()}, {"value": float("nan")}, ["not an object"]])
def test_webhook_rejects_invalid_json_objects(adapter, payload):
    with pytest.raises(EventError, match="JSON"):
        adapter.webhook_received(source="github", delivery_id="hook-1", event_name="update", payload=payload)


@pytest.mark.parametrize("main_session_id", [None, "", "  ", 123])
def test_main_session_must_be_supplied_by_host(main_session_id):
    with pytest.raises(EventError, match="main_session_id"):
        EventSourceAdapter(main_session_id=main_session_id)


def test_explicit_empty_session_does_not_silently_fall_back_to_main(adapter):
    with pytest.raises(EventError, match="session_id"):
        adapter.user_message(source="web", message_id="1", conversation_id="tab", text="hi", session_id="")


@pytest.mark.parametrize("field", ["source", "message_id", "text"])
def test_user_message_rejects_missing_required_input(adapter, field):
    args = {"source": "qq", "message_id": "1", "conversation_id": "chat", "text": "你好"}
    args[field] = "  "
    with pytest.raises(EventError, match=field):
        adapter.user_message(**args)
