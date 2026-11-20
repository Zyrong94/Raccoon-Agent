"""A revision keeps tool definitions discovered in the retained history."""

from raccoon.modules.context.context_builder import ContextBuilder
from raccoon.modules.sessions.fork import fork_session
from raccoon.modules.sessions.store import SessionStore
from raccoon.runtime.model_types import ToolDefinition


def test_fork_before_an_edited_message_keeps_only_earlier_tool_discoveries(tmp_path):
    store = SessionStore(tmp_path / ".raccoon")
    data = store.create()
    first = store.append_user_message(data, "Keep this turn", user_timezone="UTC")
    store.append_tool_definitions_loaded(
        data, [ToolDefinition("lookup_extra", "Look up a value", {})], after_message_id=first,
    )
    second = store.append_user_message(data, "Edit this turn", user_timezone="UTC")
    store.append_tool_definitions_loaded(
        data, [ToolDefinition("read_inventory", "Read the inventory", {})], after_message_id=second,
    )

    identity = fork_session(store, tmp_path, data.session_id, before_message_id=second)
    restored = store.load(identity)
    messages = ContextBuilder().build_provider_messages(restored)
    tools = [tool.name for message in messages for tool in message.additional_tools]
    assert tools == ["lookup_extra"]
    assert len(store.load(data.session_id).tool_loads) == 2
