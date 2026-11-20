"""The Web group history view is read-only and scoped to the QQ binding."""

import hashlib
import json

from raccoon.bootstrap.group_chat import QQGroups
from raccoon.interfaces.qq.state import QQState
from raccoon.modules.sessions.store import SessionStore
from tests.web_fixtures import response, web


def test_web_group_history_is_read_only_and_requires_the_current_binding(web):
    client, runtime, _ = web
    root = runtime.catalog.root
    key = hashlib.sha256(b"group-a").hexdigest()
    path = root / "channels" / "qq.sqlite3"
    state = QQState(path)
    try:
        state.set("owner", "private-owner")
        QQGroups(state, project=runtime.project_path, data_root=root)
        with state.db:
            state.db.execute(
                "INSERT INTO qq_groups(group_key,group_openid,private_owner,member_owner,title) VALUES (?,?,?,?,?)",
                (key, "group-a", "private-owner", "member-owner", "Project group"),
            )

        group_root = root / "channels" / "groups" / key
        store = SessionStore(group_root)
        data = store.create(session_id="group-chat")
        store.append_user_message(data, "Message from the group", user_timezone="UTC")
        store.append_assistant_response(data, response("Group reply"))
        pointer = group_root / "channels" / "main_session.json"
        pointer.parent.mkdir(exist_ok=True)
        pointer.write_text(json.dumps({"session_id": data.session_id}), encoding="utf-8")
        log = store.sessions_dir / f"{data.session_id}.jsonl"
        original = log.read_bytes()

        initial = client.get("/api/state").json()["session"]["id"]
        route = f"/api/group-sessions/{key}"
        assert client.get("/api/group-sessions").json()["groups"][0]["id"] == key
        result = client.get(route)
        assert result.status_code == 200
        assert [message["content"] for message in result.json()["messages"]] == [
            "Message from the group", "Group reply",
        ]
        assert "DO_NOT_EXPOSE" not in result.text and "private-owner" not in result.text
        assert client.get("/api/state").json()["session"]["id"] == initial
        assert log.read_bytes() == original

        state.set("owner", "other-owner")
        assert client.get(route).status_code == 404
    finally:
        state.close()
