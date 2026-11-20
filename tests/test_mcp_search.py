from raccoon.modules.tools.search.ranking import TOOL_SEARCH_LIMIT as MCP_TOOL_SEARCH_LIMIT, ToolSearchEntry as McpSearchEntry, search_tool_entries as search_mcp_tools
from raccoon.modules.tools.search.discovery import create_tool_search
from raccoon.runtime.model_types import ToolDefinition


def search_tool(entries):
    return create_tool_search(lambda: [entry.definition for entry in entries], lambda: {}, lambda definitions: None)


def _entry(server: str, tool: str, description: str) -> McpSearchEntry:
    return McpSearchEntry(
        server=server,
        tool=tool,
        definition=ToolDefinition(
            name=f"mcp__{server}__{tool}",
            description=description,
            parameters={"type": "object", "properties": {}},
        ),
    )


def test_search_mcp_tools_prefers_name_and_query_coverage() -> None:
    entries = (
        _entry("github", "get_pull_request", "Read one pull request."),
        _entry("github", "search_pull_requests", "Search pull requests."),
        _entry("tracker", "lookup", "Get a GitHub pull request."),
    )

    matches = search_mcp_tools(entries, "get pull request")

    assert [item.tool for item in matches] == [
        "get_pull_request",
        "lookup",
        "search_pull_requests",
    ]


def test_search_mcp_tools_is_stable_and_limited() -> None:
    entries = tuple(
        _entry("demo", f"lookup_{index:02d}", "Lookup records.")
        for index in reversed(range(20))
    )

    matches = search_mcp_tools(entries, "lookup")

    assert len(matches) == MCP_TOOL_SEARCH_LIMIT == 8
    assert [item.definition.name for item in matches] == sorted(
        item.definition.name for item in matches
    )


def test_mcp_tool_search_returns_activated_names_without_executing_mcp() -> None:
    entries = (_entry("github", "get_issue", "Read one issue."),)
    tool = search_tool(entries)

    result = tool.executor(query="read github issue")

    assert result.ok is True
    assert result.data["activated_tools"] == [
        "mcp__github__get_issue"
    ]
    assert "mcp__github__get_issue" in result.content
    assert tool.permission is None
    assert tool.definition.parameters["required"] == ["query"]
    assert tool.definition.parameters["additionalProperties"] is False


def test_mcp_tool_search_rejects_blank_query_and_handles_no_match() -> None:
    tool = search_tool(
        (_entry("github", "get_issue", "Read one issue."),)
    )

    blank = tool.executor(query="   ")
    missing = tool.executor(query="calendar event")

    assert blank.ok is False
    assert blank.data.get("activated_tools", []) == []
    assert missing.ok is True
    assert missing.data["activated_tools"] == []
