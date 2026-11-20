"""基于 JSONL 的会话事件存储。"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, fields
from copy import deepcopy

from raccoon.modules.sessions.compaction import CompactionRecord
from raccoon.modules.sessions.events import SessionEvent
from raccoon.modules.sessions.metadata import merge_metadata_patch, metadata_without_reserved_keys
from raccoon.modules.sessions.identity import new_event_id, new_message_id, new_part_id, new_session_id
from raccoon.modules.sessions.versions import CONTEXT_EVENT_SCHEMA_VERSION
from raccoon.runtime.model_types import ChatResponse, ToolCall, ToolDefinition
from raccoon.modules.tools.types import ToolResult
from raccoon.modules.sessions.user_attachments import (
    PreparedAttachment,
    UserAttachment,
    prepare_attachments_for_session,
)
from raccoon.modules.context.temporal import build_temporal_context
from raccoon.modules.sessions.models import AgentMessage, MessagePart, SessionData, utc_now_iso
from raccoon.modules.sessions.errors import (
    SessionCorruptError,
    SessionEmptyError,
    SessionNotFoundError,
    SessionUnsupportedSchemaError,
)
from raccoon.modules.planning.models import TaskPlan, TaskPlanError
from raccoon.modules.planning.validation import validate_plan

# 同一 store 根目录可能被多个线程/实例同时写入（主执行器与并行分支）。JSONL 追加
# 必须串行，否则 Windows 上会因文件占用抛 PermissionError，POSIX 上也可能出现
# 交错写入导致行粘连。锁按规范化后的根路径共享。
_STORE_LOCKS: dict[str, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


def _store_lock(root: Path) -> threading.RLock:
    key = str(root.resolve())
    with _STORE_LOCKS_GUARD:
        lock = _STORE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STORE_LOCKS[key] = lock
        return lock


EVENT_ROLE_MAP = {
    "agent_status": "user",
    "user_message": "user",
    "assistant_message": "assistant",
    "tool_result": "tool",
    "background_notification": "user",
}


class SessionStore:
    """append-only JSONL store。

    load 从日志恢复数据；append_event 校验并落盘后更新同一份 SessionData。
    refresh 用于已知的外部写入边界，不重新创建工具和权限对象。
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.sessions_dir = self.root / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = _store_lock(self.root)
        self.on_message_committed: Callable[[str, AgentMessage], None] | None = None

    def list_events(self, session_id: str) -> list[SessionEvent]:
        with self._lock:
            path = self._session_path(session_id)
            if not path.exists():
                return []

            events: list[SessionEvent] = []
            with path.open("r", encoding="utf-8") as file:
                for line in file:
                    if line.strip():
                        events.append(SessionEvent.from_dict(json.loads(line)))
        return events

    def load(self, session_id: str) -> SessionData:
        path = self._session_path(session_id)
        with self._lock:
            if not path.exists():
                raise SessionNotFoundError(f"session not found: {session_id}")
            data = SessionData(session_id=session_id)
            saw_created = False
            saw_content = False
            try:
                with path.open("r", encoding="utf-8") as file:
                    for line_number, line in enumerate(file, start=1):
                        if not line.strip():
                            continue
                        saw_content = True
                        event = SessionEvent.from_dict(json.loads(line))
                        if event.session_id != session_id:
                            raise SessionCorruptError(f"session ID mismatch at line {line_number}")
                        if event.type == "session_created":
                            if saw_created:
                                raise SessionCorruptError("duplicate session_created event")
                            saw_created = True
                        apply_event(data, event)
            except (UnicodeError, ValueError, TypeError, KeyError, AttributeError) as error:
                raise SessionCorruptError(f"session {session_id} cannot be read: {error}") from error
            if not saw_content:
                raise SessionEmptyError(f"session {session_id} is empty")
            if not saw_created:
                raise SessionCorruptError("missing session_created event")
            return data

    def create(self, *, session_id: str | None = None, **metadata: Any) -> SessionData:
        data = SessionData(session_id=session_id or new_session_id())
        with self._lock:
            if self._session_path(data.session_id).exists():
                raise ValueError(f"session already exists: {data.session_id}")
            payload = metadata_without_reserved_keys(metadata)
            payload["context_event_schema_version"] = CONTEXT_EVENT_SCHEMA_VERSION
            self.append_event(data, "session_created", payload)
        return data

    def refresh(self, data: SessionData) -> None:
        """Refresh in place so session tools keep the same data reference."""
        with self._lock:
            latest = self.load(data.session_id)
            for item in fields(data):
                setattr(data, item.name, getattr(latest, item.name))

    def append_event(
        self, data: SessionData, event_type: str, payload: dict[str, Any], *, created_at: str | None = None
    ) -> None:
        event = SessionEvent(
            id=new_event_id(),
            session_id=data.session_id,
            type=event_type,
            payload=deepcopy(payload),
            created_at=created_at or utc_now_iso(),
        )
        with self._lock:
            # Validate against a copy before writing; failed writes leave live data unchanged.
            updated = deepcopy(data)
            apply_event(updated, event)
            path = self._session_path(data.session_id)
            mode = "x" if event_type == "session_created" else "a"
            with path.open(mode, encoding="utf-8") as file:
                file.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            for item in fields(data):
                setattr(data, item.name, getattr(updated, item.name))
            if self.on_message_committed is not None and event_type in EVENT_ROLE_MAP:
                self.on_message_committed(data.session_id, updated.messages[-1])

    def append_tool_definitions_loaded(
        self, data: SessionData, definitions: list[ToolDefinition], *, after_message_id: str | None
    ) -> None:
        self.append_event(
            data,
            "tool_definitions_loaded",
            {
                "definitions": [asdict(definition) for definition in definitions],
                "after_message_id": after_message_id,
            },
        )

    def append_session_metadata_updated(self, data: SessionData, **metadata: Any) -> None:
        """追加用户可见 session metadata patch。

        这个事件更新会话列表等入口，不生成普通消息，也不进入
        provider context。
        """

        self.append_event(data, "session_metadata_updated", metadata_without_reserved_keys(metadata))

    def append_message_part_metadata_updated(
        self, data: SessionData, *, message_id: str, part_id: str, metadata: dict[str, Any]
    ) -> None:
        self.append_event(
            data,
            "message_part_metadata_updated",
            {
                "message_id": message_id,
                "part_id": part_id,
                "metadata": dict(metadata),
            },
        )

    def _append_user_parts(
        self,
        data: SessionData,
        content: str,
        *,
        attachments: list[PreparedAttachment] | None = None,
        metadata: dict[str, Any] | None = None,
        part_metadata: dict[str, Any] | None = None,
    ) -> str:
        message_id = new_message_id()
        parts = [
            MessagePart(
                id=new_part_id(),
                message_id=message_id,
                kind="text",
                content=content,
                metadata=dict(part_metadata or {}),
            )
        ]
        for attachment in attachments or []:
            attachment_metadata = dict(part_metadata or {})
            attachment_metadata.update(
                {
                    "filename": attachment.filename,
                    "media_type": attachment.media_type,
                    "path": attachment.relative_path,
                    "bytes": attachment.size_bytes,
                    "sha256": attachment.sha256,
                    "source": attachment.source,
                }
            )
            parts.append(
                MessagePart(
                    id=new_part_id(),
                    message_id=message_id,
                    kind=attachment.kind,
                    content=(
                        f"[image: {attachment.filename}]"
                        if attachment.kind == "image"
                        else attachment.inline_text or f"[file: {attachment.filename}]"
                    ),
                    metadata=attachment_metadata,
                )
            )
        self._append_message_event(
            data,
            "user_message",
            message_id=message_id,
            parts=parts,
            metadata=metadata,
        )
        return message_id

    def append_agent_status(self, data: SessionData, content: str) -> str:
        """Persist framework context without creating a new user turn."""
        message_id = new_message_id()
        part = MessagePart(
            id=new_part_id(),
            message_id=message_id,
            kind="text",
            content=content,
            metadata={},
        )
        self._append_message_event(
            data,
            "agent_status",
            message_id=message_id,
            parts=[part],
            metadata={"source_kind": "agent_status"},
        )
        return message_id

    def append_assistant_parts(
        self,
        data: SessionData,
        parts: list[MessagePart],
        *,
        metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
    ) -> str:
        """写入已经由 agent 层转换好的 assistant parts。"""

        message_id = message_id or new_message_id()
        self._append_message_event(
            data,
            "assistant_message",
            message_id=message_id,
            parts=parts,
            metadata=metadata,
        )
        return message_id

    def append_task_plan_updated(
        self,
        data: SessionData,
        *,
        previous_revision: int,
        operation: str,
        changes: Sequence[Mapping[str, object]],
        snapshot: TaskPlan | Mapping[str, object],
    ) -> None:
        """追加一次规划操作及其完整、已验证快照。"""

        if (
            isinstance(previous_revision, bool)
            or not isinstance(previous_revision, int)
            or previous_revision < 0
        ):
            raise ValueError("previous_revision must be a non-negative integer")
        if not isinstance(operation, str) or not operation.strip():
            raise ValueError("operation must be a non-blank string")

        plan = TaskPlan.from_dict(snapshot.to_dict() if isinstance(snapshot, TaskPlan) else snapshot)
        validate_plan(plan)
        if plan.revision != previous_revision + 1:
            raise ValueError("task plan revision must be exactly one greater than previous_revision")
        normalized_changes = [dict(change) for change in changes]
        self.append_event(
            data,
            "task_plan_updated",
            {
                "previous_revision": previous_revision,
                "revision": plan.revision,
                "operation": operation,
                "changes": normalized_changes,
                "snapshot": plan.to_dict(),
            },
        )

    def append_background_notification(
        self,
        data: SessionData,
        *,
        content: str,
        job_id: str,
        tool_name: str,
        status: str,
        task_id: str | None = None,
        observed_revision: int | None = None,
        source_event_id: str | None = None,
        run_id: str | None = None,
    ) -> str:
        """追加一条后台任务完成通知，投影成普通 user 消息。

        通知不是某个 tool_call 的第二条结果：它以独立 user 文本进入历史，因此不会破坏
        provider 的 tool_call/tool_result 配对。
        """

        with self._lock:
            if source_event_id is not None:
                existing = self.background_notification_message_id(
                    data, source_event_id, job_id=job_id, run_id=run_id
                )
                if existing is not None:
                    self.refresh(data)
                    return existing
            message_id = new_message_id()
            metadata = {
                "background_job_id": job_id,
                "background_tool_name": tool_name,
                "background_status": status,
                "background_delivery": "pending",
            }
            if task_id is not None:
                metadata["background_task_id"] = task_id
            if observed_revision is not None:
                metadata["background_observed_revision"] = observed_revision
            if run_id is not None:
                metadata["background_run_id"] = run_id
            part = MessagePart(
                id=new_part_id(),
                message_id=message_id,
                kind="text",
                content=content,
                metadata=metadata,
            )
            self._append_message_event(
                data,
                "background_notification",
                message_id=message_id,
                parts=[part],
                metadata={
                    "background_notification": True,
                    **({"source_event_id": source_event_id} if source_event_id is not None else {}),
                },
            )
            return message_id

    def _append_message_event(
        self,
        data: SessionData,
        event_type: str,
        *,
        message_id: str,
        parts: list[MessagePart],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.append_event(
            data,
            event_type,
            {
                "message_id": message_id,
                "parts": [part.to_dict() for part in parts],
                "metadata": metadata or {},
            },
        )

    def append_user_message(
        self,
        data: SessionData,
        content: str,
        *,
        attachments: list[UserAttachment] | None = None,
        source_event_id: str | None = None,
        scheduled_task_id: str | None = None,
        subagent_parent_id: str | None = None,
        execution_settings: dict[str, str] | None = None,
        user_timezone: str,
        scheduled_run_id: str | None = None,
    ) -> str:
        """把用户输入写成可恢复的 user_message 事件。"""

        if source_event_id is not None and (
            not isinstance(source_event_id, str) or not source_event_id.strip()
        ):
            raise ValueError("source_event_id must be a non-empty string")
        prepared_attachments = prepare_attachments_for_session(
            attachments or [],
            store_root=self.root,
            session_id=data.session_id,
        )
        with self._lock:
            if source_event_id is not None:
                # Deduplication compares append-only facts, never a compacted projection.
                for event in self.list_events(data.session_id):
                    if event.type != "user_message":
                        continue
                    payload = event.payload
                    if payload.get("metadata", {}).get("source_event_id") == source_event_id:
                        existing = "\n".join(
                            part["content"] for part in payload["parts"] if part["kind"] == "text"
                        )
                        attachment_keys = ("filename", "media_type", "path", "bytes", "sha256", "source")
                        old_attachments = [
                            (part["kind"], *(part.get("metadata", {}).get(key) for key in attachment_keys))
                            for part in payload["parts"]
                            if part["kind"] in {"image", "file"}
                        ]
                        new_attachments = [
                            (
                                item.kind,
                                item.filename,
                                item.media_type,
                                item.relative_path,
                                item.size_bytes,
                                item.sha256,
                                item.source,
                            )
                            for item in prepared_attachments
                        ]
                        if (existing != content or old_attachments != new_attachments
                                or payload.get('metadata', {}).get('execution_settings') != execution_settings):
                            raise ValueError("source_event_id already identifies a different user message")
                        message_id = payload["message_id"]
                        self.refresh(data)
                        return message_id

            message_id = self._append_user_parts(
                data,
                content,
                attachments=prepared_attachments,
                part_metadata=(
                    {"scheduled_run_id": scheduled_run_id} if scheduled_run_id is not None else {}
                ),
                metadata={
                    "temporal_context": build_temporal_context(user_timezone),
                    **({"execution_settings": execution_settings} if execution_settings is not None else {}),
                    **({"source_event_id": source_event_id} if source_event_id is not None else {}),
                    **(
                        {"scheduled_task_id": scheduled_task_id, "source_kind": "timer"}
                        if scheduled_task_id
                        else {}
                    ),
                    **(
                        {"subagent_parent_id": subagent_parent_id, "source_kind": "subagent"}
                        if subagent_parent_id
                        else {}
                    ),
                },
            )
            return message_id

    def append_assistant_response(
        self,
        data: SessionData,
        response: ChatResponse,
        *,
        native_completions: list[dict[str, str]] | None = None,
        scheduled_run_id: str | None = None,
    ) -> str:
        """把 provider 返回的 assistant response 写入事件日志。

        assistant response 可能同时包含可见文本和 tool_calls。这里统一转成 MessagePart，
        确保后续 ContextBuilder 能重新投影出合法的 provider assistant message。
        """

        message_id = new_message_id()
        parts = assistant_response_to_parts(message_id=message_id, response=response)
        if scheduled_run_id is not None:
            for part in parts:
                part.metadata["scheduled_run_id"] = scheduled_run_id
        assistant_message_id = self.append_assistant_parts(
            data,
            parts,
            message_id=message_id,
            metadata={
                "provider": response.provider,
                "model": response.model,
                "finish_reason": response.finish_reason,
                "usage": asdict(response.usage) if response.usage is not None else None,
                "diagnostics": asdict(response.diagnostics),
                **({"native_completions": native_completions} if native_completions else {}),
                **({"provider_state": response.provider_state} if response.provider_state else {}),
            },
        )
        return assistant_message_id

    def append_tool_result(
        self,
        data: SessionData,
        *,
        tool_call: ToolCall,
        result: ToolResult,
        source_event_id: str | None = None,
        background_run_id: str | None = None,
        background_job_id: str | None = None,
        background_status: str | None = None,
        scheduled_run_id: str | None = None,
    ) -> tuple[str, bool]:
        """把工具执行结果写成 role=tool 事实。

        这一步是 tool calling 闭环的关键：模型下一次调用时会看到这个 tool_result，并基于
        工具输出生成后续回答。工具结果不直接替代 assistant 回复。
        """

        with self._lock:
            # A late background completion can arrive after another owner wrote a result.
            self.refresh(data)
            existing_message_id = None
            for message in data.messages:
                for part in message.parts:
                    if part.kind == "tool_result" and part.metadata.get("tool_call_id") == tool_call.id:
                        existing_message_id = message.id
            if existing_message_id is not None:
                return existing_message_id, False

            message_id = new_message_id()
            result, images = prepare_tool_result(result)
            part = tool_result_to_part(
                message_id=message_id, tool_call=tool_call, result=result, images=images
            )
            if source_event_id is not None:
                part.metadata.update(source_event_id=source_event_id, background_run_id=background_run_id)
            if background_run_id is not None:
                part.metadata["background_delivery"] = "pending"
                part.metadata["background_job_id"] = background_job_id
                part.metadata["background_status"] = background_status
            if scheduled_run_id is not None:
                part.metadata["scheduled_run_id"] = scheduled_run_id
            self._append_message_event(data, "tool_result", message_id=message_id, parts=[part])
            return message_id, True

    def background_notification_message_id(
        self, data: SessionData, source_event_id: str, *, job_id: str, run_id: str | None
    ) -> str | None:
        if not isinstance(source_event_id, str) or not source_event_id.strip():
            raise ValueError("source_event_id must be a non-empty string")
        for event in self.list_events(data.session_id):
            if (
                event.type != "background_notification"
                or event.payload.get("metadata", {}).get("source_event_id") != source_event_id
            ):
                continue
            metadata = event.payload["parts"][0]["metadata"]
            if metadata.get("background_job_id") != job_id or metadata.get("background_run_id") != run_id:
                raise ValueError("source_event_id identifies a different background completion")
            message_id = event.payload["message_id"]
            return message_id
        return None

    def _session_path(self, session_id: str) -> Path:
        from raccoon.modules.sessions.catalog import is_safe_session_id
        from raccoon.modules.sessions.errors import SessionInvalidIdError

        if not is_safe_session_id(session_id):
            raise SessionInvalidIdError(f"invalid session_id: {session_id!r}")
        return self.sessions_dir / f"{session_id}.jsonl"


def _message_from_event(event: SessionEvent, *, role: str) -> AgentMessage:
    payload = event.payload
    message_id = str(payload["message_id"])
    parts = _parts_from_payload(payload.get("parts", []), message_id=message_id)
    return AgentMessage(
        id=message_id,
        role=role,
        parts=parts,
        metadata=dict(payload.get("metadata") or {}),
    )


def _parts_from_payload(parts: Iterable[dict[str, object]], *, message_id: str) -> list[MessagePart]:
    result: list[MessagePart] = []
    for part in parts:
        data = dict(part)
        data.setdefault("message_id", message_id)
        result.append(MessagePart.from_dict(data))
    return result


def _apply_message_part_metadata_update(view: SessionData, event: SessionEvent) -> None:
    message_id = str(event.payload.get("message_id") or "")
    part_id = str(event.payload.get("part_id") or "")
    metadata = event.payload.get("metadata")
    if not message_id or not part_id or not isinstance(metadata, dict):
        return
    for message in view.messages:
        if message.id != message_id:
            continue
        for part in message.parts:
            if part.id == part_id:
                part.metadata.update(metadata)
                return


def _apply_task_plan_payload(view: SessionData, event: SessionEvent) -> None:
    try:
        plan = TaskPlan.from_dict(event.payload.get("snapshot"))  # type: ignore[arg-type]
        validate_plan(plan)
    except (TaskPlanError, TypeError) as error:
        raise SessionCorruptError(
            f"invalid task_plan_updated snapshot in event {event.id}: {error}"
        ) from error

    previous_revision = event.payload.get("previous_revision")
    revision = event.payload.get("revision")
    if (
        isinstance(previous_revision, bool)
        or not isinstance(previous_revision, int)
        or isinstance(revision, bool)
        or not isinstance(revision, int)
    ):
        raise SessionCorruptError(f"task_plan_updated revision chain is invalid in event {event.id}")
    expected_previous = view.task_plan.revision if view.task_plan is not None else 0
    if previous_revision != expected_previous or revision != previous_revision + 1:
        raise SessionCorruptError(
            f"task_plan_updated revision chain is invalid in event {event.id}: "
            f"expected previous {expected_previous}, got {previous_revision} -> {revision}"
        )
    if revision != plan.revision:
        raise SessionCorruptError(f"task_plan_updated revision mismatch in event {event.id}")
    view.task_plan = plan


def apply_event(view: SessionData, event: SessionEvent) -> None:
    if event.type == "session_created":
        from raccoon.modules.sessions.versions import CONTEXT_EVENT_SCHEMA_VERSION

        actual = event.payload.get("context_event_schema_version")
        if actual != CONTEXT_EVENT_SCHEMA_VERSION:
            raise SessionUnsupportedSchemaError(
                session_id=event.session_id, actual_version=str(actual),
                expected_version=CONTEXT_EVENT_SCHEMA_VERSION,
            )
    if event.type in {"session_created", "session_metadata_updated"}:
        view.metadata = merge_metadata_patch(view.metadata, event.payload)
        view.metadata["session_id"] = event.session_id
        return

    if event.type == "context_compacted":
        view.compactions.append(CompactionRecord.from_dict(event.payload))
        return

    if event.type == "tool_definitions_loaded":
        from raccoon.runtime.model_types import ToolDefinition

        definitions = event.payload.get("definitions")
        anchor = event.payload.get("after_message_id")
        if (
            not isinstance(definitions, list)
            or not definitions
            or (anchor is not None and not isinstance(anchor, str))
        ):
            raise SessionCorruptError("invalid tool definitions event")
        try:
            for item in definitions:
                definition = ToolDefinition(**item)
                if (
                    not isinstance(definition.name, str)
                    or not definition.name
                    or not isinstance(definition.description, str)
                    or not isinstance(definition.parameters, dict)
                    or not isinstance(definition.async_execution, bool)
                ):
                    raise ValueError("invalid definition")
        except (TypeError, ValueError) as exc:
            raise SessionCorruptError("invalid loaded tool definition") from exc
        view.tool_loads.append(dict(event.payload))
        return

    if event.type == "message_part_metadata_updated":
        _apply_message_part_metadata_update(view, event)
        return

    if event.type == "task_plan_updated":
        _apply_task_plan_payload(view, event)
        return

    role = EVENT_ROLE_MAP.get(event.type)
    if role is None:
        return

    message = _message_from_event(event, role=role)
    view.messages.append(message)


def prepare_tool_result(result: ToolResult) -> tuple[ToolResult, list[dict[str, object]]]:
    """将图片内嵌到工具结果中；不再写独立的图片缓存文件。"""

    if not result.ok or not result.images:
        return result, []
    import raccoon.modules.sessions.user_attachments as attachment_storage

    try:
        images = attachment_storage.serialize_tool_images(result.images)
    except (OSError, ValueError) as error:
        # The executor also passes this object to completion/UI callbacks.
        result.ok = False
        result.error = f"工具图片编码失败：{error}"
        result.content = result.error
        result.images = []
        return result, []
    return result, images


def tool_call_to_part(*, message_id: str, tool_call: ToolCall) -> MessagePart:
    return MessagePart(
        id=new_part_id(),
        message_id=message_id,
        kind="tool_call",
        content="",
        metadata={
            "tool_call_id": tool_call.id,
            "tool_name": tool_call.name,
            "arguments": tool_call.arguments,
            **({"async_execution": True} if tool_call.async_execution else {}),
        },
    )


def assistant_response_to_parts(*, message_id: str, response: ChatResponse) -> list[MessagePart]:
    """把 provider assistant response 转成内部 parts。

    provider 协议里的 assistant 消息可以同时包含可见文本和 tool_calls。这里集中做转换，
    避免不同调用方手写 metadata 导致 tool_call_id / arguments 字段漂移。
    """

    parts: list[MessagePart] = []
    if response.content:
        parts.append(
            MessagePart(id=new_part_id(), message_id=message_id, kind="text", content=response.content)
        )
    for tool_call in response.tool_calls:
        parts.append(tool_call_to_part(message_id=message_id, tool_call=tool_call))
    return parts


def tool_result_to_part(
    *,
    message_id: str,
    tool_call: ToolCall,
    result: ToolResult,
    images: list[dict[str, object]] | None = None,
) -> MessagePart:
    """把 ToolResult 转成 role=tool 消息里的单个 part。"""

    return MessagePart(
        id=new_part_id(),
        message_id=message_id,
        kind="tool_result",
        content=result.content,
        metadata={
            "tool_call_id": tool_call.id,
            "tool_name": tool_call.name,
            "ok": result.ok,
            "data": result.data,
            "error": result.error,
            **({"images": images} if images and result.ok else {}),
        },
    )
