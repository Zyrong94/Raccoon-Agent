from raccoon.modules.sessions.catalog import SessionCatalog
from raccoon.modules.sessions.store import SessionStore


def test_two_store_instances_append_to_one_log_without_losing_messages(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    first, second = SessionStore(tmp_path), SessionStore(tmp_path)
    first.create(session_id="chat")
    def append(i):
        store = first if i % 2 else second
        store.append_user_message(store.load("chat"), str(i), user_timezone="UTC")
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(append, range(24)))
    messages = first.load("chat").messages
    assert len(messages) == 24
    assert {message.parts[0].content for message in messages} == {str(i) for i in range(24)}


def test_catalog_reads_logs_and_leaves_old_index_untouched(tmp_path):
    index = tmp_path / "session_index.json"
    index.write_bytes(b"obsolete")
    store = SessionStore(tmp_path)
    session = store.create(session_id="chat", title="Title")
    store.append_user_message(session, "hello", user_timezone="UTC")
    (store.sessions_dir / "broken.jsonl").write_bytes(b"invalid")
    records = {record.session_id: record for record in SessionCatalog(tmp_path).list_sessions()}
    assert records["chat"].latest_user_input == "hello"
    assert records["broken"].status == "corrupt"
    assert index.read_bytes() == b"obsolete"


def test_writes_and_listing_do_not_create_an_index(tmp_path):
    SessionStore(tmp_path).create(session_id="new")
    assert [row.session_id for row in SessionCatalog(tmp_path).list_sessions()] == ["new"]
    assert not (tmp_path / "session_index.json").exists()


def test_reopened_log_preserves_tool_call_and_result(tmp_path):
    from raccoon.runtime.model_types import ChatResponse, ToolCall
    from raccoon.modules.tools.types import ToolResult

    store = SessionStore(tmp_path)
    data = store.create(session_id="chat")
    store.append_user_message(data, "Check this", user_timezone="UTC")
    call = ToolCall(id="call_1", name="example", arguments={})
    store.append_assistant_response(data, ChatResponse(
        provider="fake", model="test", content="", tool_calls=[call], finish_reason="tool_calls",
    ))
    store.append_tool_result(data, tool_call=call, result=ToolResult(name=call.name, ok=True, content="checked"))

    messages = SessionStore(tmp_path).load("chat").messages
    assert [message.role for message in messages] == ["user", "assistant", "tool"]
    assert messages[1].parts[0].metadata["tool_call_id"] == call.id
    assert messages[2].parts[0].metadata["tool_call_id"] == call.id
    assert messages[2].parts[0].content == "checked"


def test_catalog_sorts_sessions_and_applies_title_changes_without_adding_messages(tmp_path):
    store = SessionStore(tmp_path)
    older = store.create(session_id="older", title="Old title", workspace="example")
    newer = store.create(session_id="newer")
    store.append_user_message(older, "first", user_timezone="UTC")
    store.append_user_message(newer, "second", user_timezone="UTC")
    store.append_event(older, "session_metadata_updated", {"title": "Renamed"}, created_at="2099-01-01T00:00:00Z")

    records = SessionCatalog(tmp_path).list_sessions()
    assert [record.session_id for record in records] == ["older", "newer"]
    assert (records[0].title, records[0].workspace, records[0].message_count) == ("Renamed", "example", 1)


def test_catalog_rejects_a_session_id_outside_the_sessions_directory(tmp_path):
    import pytest
    from raccoon.modules.sessions.errors import SessionInvalidIdError

    with pytest.raises(SessionInvalidIdError):
        SessionCatalog(tmp_path).get_session("../private")
