"""Tool calling 历史序列校验。"""

from __future__ import annotations

from raccoon.modules.sessions.models import AgentMessage, MessagePart


class InvalidToolCallSequenceError(ValueError):
    """会话历史中的 tool_call / tool_result 序列无法投影给 provider。"""


def validate_tool_call_sequence(messages: list[AgentMessage]) -> None:
    """校验同步原子序列与显式原生异步调用的延迟结果。

    普通调用必须紧接结果，async_execution 允许中间有其他对话；两者结果都必须
    回溯到原调用。上下文压缩、checkpoint tail 或手写事件一旦留下孤立 tool result，
    这里会在投影前报错，而不是把坏请求发给 provider。
    """

    pending_tool_call_ids: set[str] = set()
    async_tool_call_ids: set[str] = set()
    seen: set[str] = set()
    for message in messages:
        if message.role == "assistant":
            _raise_if_pending(pending_tool_call_ids)
            for part in message.parts:
                if part.kind == "tool_call":
                    call_id = _required_metadata(part, "tool_call_id")
                    if call_id in seen:
                        raise InvalidToolCallSequenceError(f"duplicate tool call: {call_id}")
                    seen.add(call_id)
                    target = async_tool_call_ids if part.metadata.get("async_execution") is True else pending_tool_call_ids
                    target.add(call_id)
            continue

        if message.role != "tool":
            _raise_if_pending(pending_tool_call_ids)
            continue

        for part in message.parts:
            if part.kind not in {"tool_result"}:
                continue
            tool_call_id = _required_metadata(part, "tool_call_id")
            if tool_call_id not in pending_tool_call_ids and tool_call_id not in async_tool_call_ids:
                raise InvalidToolCallSequenceError(
                    f"orphan tool result without matching assistant tool_call: {tool_call_id}",
                )
            pending_tool_call_ids.discard(tool_call_id)
            async_tool_call_ids.discard(tool_call_id)
    _raise_if_pending(pending_tool_call_ids)


def earliest_pending_async_index(messages: list[AgentMessage]) -> int | None:
    """Pin unfinished native calls in the real tail, never in a summary."""
    pending: dict[str, int] = {}
    for index, message in enumerate(messages):
        for part in message.parts:
            call_id = str(part.metadata.get("tool_call_id", ""))
            if part.kind == "tool_call" and part.metadata.get("async_execution") is True:
                pending[call_id] = index
            elif part.kind in {"tool_result"}:
                pending.pop(call_id, None)
    return min(pending.values(), default=None)


def _required_metadata(part: MessagePart, key: str) -> str:
    value = part.metadata.get(key)
    if value is None or value == "":
        raise InvalidToolCallSequenceError(f"{part.kind} missing metadata.{key}")
    return str(value)


def _raise_if_pending(pending_tool_call_ids: set[str]) -> None:
    if pending_tool_call_ids:
        pending = ", ".join(sorted(pending_tool_call_ids))
        raise InvalidToolCallSequenceError(f"assistant tool_call missing matching tool result: {pending}")
