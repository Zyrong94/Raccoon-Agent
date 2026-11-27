"""Shared display projection; opaque model state never reaches the browser."""

from raccoon.runtime.background import STATUS_RUNNING
import json
from raccoon.runtime.model_types import message_text


def running_tool_call_ids(runner, session_id: str) -> list[str]:
    manager = getattr(runner, "background_manager", None)
    return [
        job.tool_call_id for job in manager.list(session_id=session_id)
        if job.status == STATUS_RUNNING and job.tool_call_id is not None
    ] if manager is not None else []


def display_messages(view, *, attachments=None, ui_event_ids=(), include_reasoning=False):
    return [
        row
        for message in view.messages[-200:]
        for row in display_message(message, attachments=attachments, ui_event_ids=ui_event_ids,
                                   include_reasoning=include_reasoning)
    ]


def display_message(message, *, attachments=None, ui_event_ids=(), include_reasoning=False):
    if message.metadata.get("source_kind") == "agent_status":
        return []
    if message.role not in {"user", "assistant", "tool"}:
        return []
    text = message_text(message.parts) if message.role == "assistant" else "\n\n".join(p.content for p in message.parts if p.kind == "text")
    if (str(message.metadata.get('source_event_id', '')).startswith('genui-action:')
            or message.metadata.get('source_event_id') in ui_event_ids):
        text = '已提交界面中的选择与填写内容'
    messages, calls = [], []
    for part in message.parts:
        if part.kind == "tool_call":
            arguments = part.metadata.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    # 损坏记录仍可展示，但不会因此变成可执行的工具调用。
                    pass
            calls.append({"id": part.metadata.get("tool_call_id", part.id),
                          "name": part.metadata.get("tool_name", "tool"),
                          "arguments": arguments})
        if part.kind == "tool_result":
            result = {"id": part.id, "role": "tool", "content": part.content[:12000],
                      "name": part.metadata.get("tool_name", "tool"),
                      "ok": part.metadata.get("ok", False)}
            if part.metadata.get("tool_call_id"):
                result["tool_call_id"] = part.metadata["tool_call_id"]
            messages.append(result)
    files = attachments.for_parts(message.parts) if attachments is not None else []
    reasoning = None
    if include_reasoning and message.role == 'assistant':
        summaries = []
        for part in message.parts:
            if part.kind == "reasoning":
                for summary in part.metadata["reasoning_item"].get("summary", []):
                    if summary.get("type") == "summary_text":
                        summaries.append(summary["text"])
        reasoning = "\n".join(summaries) or None
    if text or calls or files or reasoning:
        item = {"id": message.id, "role": message.role, "content": text[:60000], "calls": calls}
        if reasoning:
            item['reasoning'] = reasoning[:60000]
        if files:
            item["attachments"] = files
        messages.append(item)
    return messages
