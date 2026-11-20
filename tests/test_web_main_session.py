"""Reading main chat history leaves the project session and log intact."""

import json

import pytest

from raccoon.application.delivery.main_session import main_session_snapshot
from raccoon.modules.sessions.store import SessionStore
from tests.web_fixtures import response, web


def test_main_history_is_read_without_selecting_or_rewriting_it(web):
    client, runtime, _ = web
    current = client.get("/api/state").json()["session"]["id"]
    root = runtime.catalog.root
    store = SessionStore(root)
    data = store.create(session_id="phone")
    store.append_user_message(data, "Message from QQ", user_timezone="UTC")
    store.append_assistant_response(data, response("Reply from Raccoon"))

    pointer = root / "channels" / "main_session.json"
    pointer.parent.mkdir(exist_ok=True)
    pointer.write_text(json.dumps({"session_id": data.session_id}), encoding="utf-8")
    path = store.sessions_dir / f"{data.session_id}.jsonl"
    original = path.read_bytes()

    history = main_session_snapshot(root)
    assert history["available"]
    assert history["session"]["id"] == data.session_id
    assert [message["content"] for message in history["messages"]] == ["Message from QQ", "Reply from Raccoon"]
    assert "DO_NOT_EXPOSE" not in json.dumps(history)
    assert client.post("/api/sessions/phone/activate").status_code == 409
    assert client.get("/api/state").json()["session"]["id"] == current
    assert path.read_bytes() == original


def test_invalid_main_pointer_is_rejected_without_exposing_its_value(tmp_path):
    pointer = tmp_path / "channels" / "main_session.json"
    pointer.parent.mkdir()
    pointer.write_text(json.dumps({"session_id": "../outside"}), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid main session pointer") as error:
        main_session_snapshot(tmp_path)
    assert "../outside" not in str(error.value)
