"""Settle native completions at the serialized owner-session boundary."""

from __future__ import annotations

from raccoon.runtime.background import BackgroundJobManager
from raccoon.modules.agent.session import AgentSession
from raccoon.runtime.model_types import ToolCall
from raccoon.runtime import CompletionInput
from raccoon.modules.tools.result_payload import result_from_payload


def native_result_recorded(session: AgentSession, tool_call_id: str) -> bool:
    """该原生异步调用是否已经闭合；用于识别重复投递的完成事件。"""

    if not isinstance(tool_call_id, str) or not tool_call_id:
        return False
    view = session.data
    return any(
        part.kind == "tool_result" and part.metadata.get("tool_call_id") == tool_call_id
        for item in view.messages for part in item.parts
    )


def native_completion_answered(session: AgentSession, message: CompletionInput) -> bool:
    """A saved result is retryable until a successful final answer covers it."""
    identity = {"tool_call_id": message.tool_call_id, "run_id": message.run_id,
                "event_id": message.event_id}
    return any(
        item.role == "assistant" and item.metadata.get("finish_reason") in (None, "stop")
        and identity in item.metadata.get("native_completions", [])
        for item in session.data.messages
    )


def consume_native_result(session: AgentSession, message: CompletionInput,
                          manager: BackgroundJobManager | None, *, emit=None) -> str:
    view = session.data
    matches = [part for item in view.messages for part in item.parts
               if part.kind == "tool_call" and part.metadata.get("tool_call_id") == message.tool_call_id]
    if (len(matches) != 1 or matches[0].metadata.get("async_execution") is not True
            or matches[0].metadata.get("background_run_id") != message.run_id
            or matches[0].metadata.get("tool_name") != message.tool_name):
        raise ValueError("native completion does not match its original session call")
    for item in view.messages:
        for part in item.parts:
            if part.kind == "tool_result" and part.metadata.get("tool_call_id") == message.tool_call_id:
                if part.metadata.get("source_event_id") != message.event_id or part.metadata.get("background_run_id") != message.run_id:
                    raise ValueError("native completion conflicts with an existing result")
                return item.id
    payload = message.result
    if manager is not None and manager.run_id == message.run_id:
        note = manager.finalize_completed(message.job_id, session_id=session.session_id)
        if note is None or note.tool_call_id != message.tool_call_id:
            raise ValueError("native completion has no matching finished job")
        payload = note.result
    result = result_from_payload(payload)
    if result.name != message.tool_name:
        raise ValueError("native completion tool name does not match original call")
    call = ToolCall(message.tool_call_id, message.tool_name, matches[0].metadata.get("arguments", {}), async_execution=True)
    message_id = session.settle_tool_result(tool_call=call, result=result,
                                            source_event_id=message.event_id, background_run_id=message.run_id,
                                            background_job_id=message.job_id, background_status=message.status)
    if emit is not None:
        emit("finished", call, result=result)
    return message_id


def record_native_completion(session: AgentSession, message: CompletionInput,
                             manager: BackgroundJobManager | None) -> str:
    """把迟到结果写回原始调用，不触发模型回答（用于已取消的作业）。"""
    return consume_native_result(session, message, manager)
