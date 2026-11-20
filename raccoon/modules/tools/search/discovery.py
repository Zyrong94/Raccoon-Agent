"""Deterministic discovery over the live, permitted session tool catalog."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

from raccoon.modules.tools.search.ranking import ToolSearchEntry, search_tool_entries
from raccoon.runtime.model_types import ToolDefinition
from raccoon.modules.tools.types import Tool, ToolResult, make_error_result


_GROUP_ALIASES = {
    "task_create": "plan planning 计划 规划 任务",
    "task_list": "plan planning 计划 规划 任务",
    "task_update": "plan planning 计划 规划 任务",
    "task_revise": "plan planning 计划 规划 任务",
    "background_status": "background process 后台 进程",
    "background_cancel": "background process 后台 进程",
}


def search_tools(definitions: Sequence[ToolDefinition], query: str) -> list[ToolDefinition]:
    """Rank available tools with small, explicit capability aliases."""
    entries = []
    originals = {definition.name: definition for definition in definitions}
    for definition in definitions:
        pieces = definition.name.split("__", 2)
        server, name = (pieces[1], pieces[2]) if len(pieces) == 3 and pieces[0] == "mcp" else ("local", definition.name)
        searchable = replace(definition, description=definition.description + " " + _GROUP_ALIASES.get(name, ""))
        entries.append(ToolSearchEntry(server, name, searchable))
    return [originals[entry.definition.name] for entry in search_tool_entries(tuple(entries), query)]


def create_tool_search(
    catalog: Callable[[], list[ToolDefinition]],
    loaded: Callable[[], dict[str, ToolDefinition]],
    declare: Callable[[list[ToolDefinition]], None],
    *,
    catalog_status: Callable[[], str] | None = None,
) -> Tool:
    """Activation comes only from this executor's controlled catalog, never result data."""
    name = "tool_search"
    def execute(*, query: str) -> ToolResult:
        if not isinstance(query, str) or not query.strip():
            return make_error_result(name, "Tool search query must not be blank.")
        definitions = catalog()
        previous = loaded()
        matches = search_tools([item for item in definitions if previous.get(item.name) != item], query)
        if matches:
            declare(matches)
            content = "Matching tools loaded for this session:\n" + "\n".join(
                f"- {item.name}: {item.description}" for item in matches
            )
        else:
            existing_matches = search_tools(definitions, query)
            if existing_matches:
                content = "Matching tools are already loaded; call them directly:\n" + "\n".join(
                    f"- {item.name}: {item.description}" for item in existing_matches
                )
            else:
                content = "No matching tools found in the current catalog. Try a more specific capability description."
        status = catalog_status() if catalog_status is not None else ""
        if status:
            content += "\n" + status
        return ToolResult(name, True, content, {"activated_tools": [item.name for item in matches]})

    return Tool(
        ToolDefinition(
            name,
            "Search available local and MCP extensions by name, description, server or capability. "
            "Matching definitions are loaded for this session; then call tools by their original names.",
            {"type": "object", "properties": {"query": {"type": "string"}},
             "required": ["query"], "additionalProperties": False},
        ),
        execute,
    )
