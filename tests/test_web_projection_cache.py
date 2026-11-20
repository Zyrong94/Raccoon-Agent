"""Web event replay preserves session scope and snapshot epoch."""

from raccoon.interfaces.web.event_stream import WebEventStream


def test_event_replay_keeps_scope_and_snapshot_epoch():
    stream = WebEventStream()
    a = stream.publish({"kind": "snapshot", "project_id": "project", "session_id": "a", "state": {}})
    b = stream.publish({"kind": "snapshot", "project_id": "project", "session_id": "b", "state": {}})
    assert stream.replay_after(a["seq"]) == [b]
    assert b["session_id"] == "b" and b["state"]["event_epoch"] == a["state"]["event_epoch"]
    assert stream.replay_after(b["seq"] + 1) is None
