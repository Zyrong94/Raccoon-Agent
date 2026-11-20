from raccoon.interfaces.web.chat_display import ChatDisplay


def test_tool_updates_keep_the_order_of_the_first_notification():
    workbench = ChatDisplay.__new__(ChatDisplay)
    workbench.activity = {}
    published = []
    published.append(workbench.tool_event({"id": "one", "name": "view", "kind": "started"}))
    published.append(workbench.tool_event({"id": "two", "name": "view", "kind": "started"}))
    published.append(workbench.tool_event({"id": "one", "name": "view", "kind": "completed", "ok": True}))
    published.append(workbench.tool_event({"id": "three", "name": "ls", "kind": "started"}))
    assert [entry["order"] for entry in workbench.activity.values()] == [0, 1, 2]
    assert [event["order"] for event in published] == [0, 1, 0, 2]
