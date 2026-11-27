"""把内部会话事实投影成 provider 请求消息。"""

from __future__ import annotations

import base64
import json
import re
import stat
from hashlib import sha256
from functools import lru_cache
from pathlib import Path

from raccoon.modules.sessions.compaction import CompactionRecord
from raccoon.modules.sessions.models import AgentMessage, MessagePart, SessionData
from raccoon.modules.context.tool_sequence import validate_tool_call_sequence, earliest_pending_async_index
from raccoon.modules.sessions.user_attachments import load_image_base64
from raccoon.runtime.model_types import ChatMessage, ContentPart, ToolCall, ToolDefinition


class InvalidCompactionBoundaryError(ValueError):
    """compaction tail 边界会生成 provider 无法接受的消息序列。"""


class ContextBuilder:
    """只负责投影，不负责压缩、总结、落盘或任务边界判断。

    `SessionData` 是 Raccoon 自己的事实账本，不等于 provider 请求格式。ContextBuilder
    的职责就是在每次调用模型前，把当前可见历史转换成 `ChatMessage` 列表：

    - system prefix 由 AgentSession 传入。
    - 如果有 compaction，先插入一条“旧历史摘要”。
    - 再保留 compaction tail 之后的真实消息。
    - 最后校验 tool_call/tool_result 序列，避免 provider 拒绝请求。
    """

    def build_provider_messages(
        self,
        view: SessionData,
        *,
        system_prefix: list[ChatMessage] | None = None,
        compaction: CompactionRecord | None = None,
        store_root: Path | None = None,
        supports_vision: bool = False,
        supports_pdf_input: bool = False,
    ) -> list[ChatMessage]:
        active_compaction = compaction or (view.compactions[-1] if view.compactions else None)
        messages = list(system_prefix or [])
        if active_compaction is not None:
            # compaction 不删除原始历史，只改变本次 provider 请求看到的上下文：旧历史用摘要
            # 表示，tail 部分保留原文，便于模型继续当前任务。
            messages.append(ChatMessage(
                role="user",
                text="[Previous conversation summary]\n" + active_compaction.summary,
            ))

        tail_messages = self._tail_messages(view, compaction=active_compaction)
        declarations = _tool_declarations(view, tail_messages)
        messages.extend(declarations.pop(None, []))
        if active_compaction is not None and any(
            part.kind == "file" and part.metadata.get("media_type") == "application/pdf"
            for message in view.messages if message not in tail_messages for part in message.parts
        ):
            messages.append(ChatMessage(
                role="user",
                text="[较早的 PDF 附件已随历史压缩，本次未发送原文件；需要原文时请使用 PDF 工具补读，或请用户重新附加文件。]",
            ))
        tail_messages = _collapse_identical_adjacent_duplicate_tool_calls(tail_messages)
        # provider 对 tool calling 序列很严格：assistant tool_call 后必须紧跟对应 tool result。
        # 压缩/compaction 不能把这个配对切断。
        validate_tool_call_sequence(tail_messages)
        latest_user = next((message for message in reversed(view.messages)
                            if message.role == 'user' and message.metadata.get('temporal_context')), None)
        if latest_user is not None and latest_user.id not in {message.id for message in tail_messages}:
            temporal = latest_user.metadata.get('temporal_context')
            if isinstance(temporal, str) and temporal:
                # A compaction may begin after the current turn's user message.
                messages.append(ChatMessage(
                    role='user',
                    text=_runtime_temporal_content(temporal),
                ))
        reasoning_cutoff = -1
        if active_compaction is not None:
            positions = {message.id: index for index, message in enumerate(view.messages)}
            cutoff_id = active_compaction.reasoning_cutoff_message_id
            if cutoff_id not in positions:
                raise InvalidCompactionBoundaryError("reasoning cutoff message not found")
            reasoning_cutoff = positions[cutoff_id]
        else:
            positions = {}
        for message in tail_messages:
            projected = self._project_message(
                message,
                store_root=store_root,
                supports_vision=supports_vision,
                supports_pdf_input=supports_pdf_input,
                session_id=view.session_id,
                include_reasoning=active_compaction is None or positions[message.id] > reasoning_cutoff,
            )
            messages.extend(projected)
            messages.extend(declarations.get(message.id, []))
        return messages

    def projected_tool_result_part_ids(self, view: SessionData) -> tuple[str, ...]:
        """Return tool results that are visible in the effective provider tail."""

        compaction = (view.compactions[-1] if view.compactions else None)
        tail = _collapse_identical_adjacent_duplicate_tool_calls(
            self._tail_messages(view, compaction=compaction)
        )
        validate_tool_call_sequence(tail)
        return tuple(
            part.id
            for message in tail
            if message.role == "tool"
            for part in message.parts
            if part.kind == "tool_result"
        )

    def _tail_messages(
        self,
        view: SessionData,
        *,
        compaction: CompactionRecord | None,
    ) -> list[AgentMessage]:
        if compaction is None:
            return view.messages

        for index, message in enumerate(view.messages):
            if message.id == compaction.first_kept_message_id:
                pending_index = earliest_pending_async_index(view.messages)
                if pending_index is not None and index > pending_index:
                    raise InvalidCompactionBoundaryError("compaction tail covers a pending async call")
                tail = view.messages[index:]
                # tail 不能从 tool message 开始，否则 provider 会看到一个没有前置 assistant
                # tool_call 的孤立 tool_result。
                _validate_tail_boundary(tail)
                return tail
        raise InvalidCompactionBoundaryError(
            f"compaction first_kept_message_id not found: {compaction.first_kept_message_id}",
        )

    def _project_message(
        self,
        message: AgentMessage,
        *,
        store_root: Path | None = None,
        supports_vision: bool = False,
        supports_pdf_input: bool = False,
        session_id: str,
        include_reasoning: bool = True,
    ) -> list[ChatMessage]:
        if message.role == "system_meta":
            # system_meta 是内部状态，不应该作为普通对话消息发给 provider。
            return []

        if message.role == "tool":
            # 工具结果保留原始 tool_call_id。
            return [_project_tool_part(part, supports_vision=supports_vision)
                    for part in message.parts if part.kind == "tool_result"]

        if message.role == "assistant":
            projected = _project_assistant_message(
                message, include_reasoning=include_reasoning,
            )
            # Tool calls remain meaningful even without visible assistant text.
            return [projected] if projected.parts else []

        if message.role == "user":
            visible_content = _join_visible_text(message.parts)
            if message.metadata.get("source_kind") == "agent_status":
                return [ChatMessage(
                    role="user",
                    text=visible_content,
                )]
            content = visible_content
            temporal = message.metadata.get('temporal_context')
            if isinstance(temporal, str) and temporal:
                content = _runtime_temporal_content(temporal) + '\n\n' + content
            for part in message.parts:
                memory = part.metadata.get("recalled_memory")
                if memory:
                    content += "\n\n<recalled_memory>\n历史记忆，仅供参考；与当前用户要求冲突时以当前要求为准。\n" + memory + "\n</recalled_memory>"
            content_parts = _project_user_content_parts(
                message.parts, content=content, store_root=store_root, supports_pdf_input=supports_pdf_input,
                session_id=session_id,
            )
            if content_parts:
                content = "\n".join(part.text for part in content_parts if part.type == "text" and part.text)
            # Empty text without attachments does not produce a user message.
            if not visible_content and content_parts is None:
                return []
            return [ChatMessage(
                role="user",
                content_parts=content_parts,
                text=content,
            )]

        return []


def _tool_declarations(view: SessionData, tail: list[AgentMessage]) -> dict[str | None, list[ChatMessage]]:
    """Keep loading anchors outside synchronous call/result transactions.

    Async calls use the existing protocol's interleaving rules; they do not hold
    the synchronous transaction open while waiting for a background completion.
    """
    positions = {message.id: index for index, message in enumerate(view.messages)}
    closed = []
    pending = set()
    for message in view.messages:
        for part in message.parts:
            if part.kind == "tool_call" and not part.metadata.get("async_execution"):
                pending.add(part.metadata.get("tool_call_id"))
            elif part.kind in {"tool_result"}:
                pending.discard(part.metadata.get("tool_call_id"))
        closed.append(not pending)
    start = positions[tail[0].id] if tail else len(view.messages)
    result: dict[str | None, list[ChatMessage]] = {}
    folded: dict[str, ToolDefinition] = {}
    for load in view.tool_loads:
        anchor = load["after_message_id"]
        if anchor is not None and anchor not in positions:
            raise ValueError("loaded tool anchor is missing from session history")
        index = positions[anchor] if anchor is not None else -1
        while index >= 0 and index < len(closed) and not closed[index]:
            index += 1
        if index == len(closed) and index >= 0:
            continue
        definitions = [ToolDefinition(**item) for item in load["definitions"]]
        if index < start:
            for definition in definitions:
                folded[definition.name] = definition
        else:
            result.setdefault(view.messages[index].id, []).append(_declaration_message(definitions))
    if folded:
        result[None] = [_declaration_message(list(folded.values()))]
    return result


def _declaration_message(definitions: list[ToolDefinition]) -> ChatMessage:
    return ChatMessage(
               role="system",
               additional_tools=definitions,
               text="Incremental tools update: previously declared tools remain available. "
                "A new definition replaces an earlier definition with the same name.",
           )


def _runtime_temporal_content(temporal: str) -> str:
    return '[Raccoon runtime context]\n' + temporal + '\n[End runtime context]'


def _project_assistant_message(message: AgentMessage, *, include_reasoning: bool) -> ChatMessage:
    parts = []
    for part in message.parts:
        if part.kind == "reasoning" and not include_reasoning:
            continue
        if part.kind in {"text", "tool_call", "reasoning"}:
            parts.append(part)
    return ChatMessage(role="assistant", parts=parts)


def _project_tool_part(part: MessagePart, *, supports_vision: bool) -> ChatMessage:
    """图片留在原工具结果中，与该结果一起保留或被压缩移出上下文。"""
    content = part.content
    content_parts = None
    images = part.metadata.get("images", [])
    if images and part.metadata.get("ok") is not False:
        if not supports_vision:
            content += "\n[当前模型不支持图片，本次未发送该工具结果中的图片。]"
        else:
            content_parts = [ContentPart(type="text", text=content)] if content else []
            for image in images:
                label = f"来源：{image['source_path']}"
                if image.get("page") is not None:
                    label += f"；第 {image['page']} 页"
                content_parts.append(ContentPart(type="text", text=label))
                content_parts.append(ContentPart(
                    type="image", media_type=image["media_type"], data_base64=image["data"],
                ))
            content = "\n".join(item.text for item in content_parts if item.type == "text" and item.text)
    return ChatMessage(
        role="tool",
        content_parts=content_parts,
        name=str(part.metadata.get("tool_name")) if part.metadata.get("tool_name") else None,
        tool_call_id=str(part.metadata["tool_call_id"]),
        text=content,
    )


def _validate_tail_boundary(messages: list[AgentMessage]) -> None:
    if not messages:
        return
    first = messages[0]
    if first.role == "tool":
        raise InvalidCompactionBoundaryError(
            "compaction tail starts with orphan tool result; move first_kept_message_id " "to the assistant tool_call before this tool result",
        )


def _join_visible_text(parts: list[MessagePart]) -> str:
    return "\n".join(part.content for part in parts if part.kind in {"text", "file"} and part.content)


def _collapse_identical_adjacent_duplicate_tool_calls(messages: list[AgentMessage]) -> list[AgentMessage]:
    """Ignore a narrowly defined duplicate created while pausing for user input.

    A historical bug could append the exact same assistant tool-call response twice
    when TaskPlan reconciliation entered a permission pause. Keep the first durable fact
    and drop only an immediately adjacent assistant message whose visible text and
    complete tool-call identity are identical.  All other invalid sequences still
    reach ``validate_tool_call_sequence`` and fail closed.
    """

    collapsed: list[AgentMessage] = []
    for message in messages:
        signature = _duplicate_tool_call_signature(message)
        if signature is not None and collapsed and signature == _duplicate_tool_call_signature(collapsed[-1]):
            continue
        collapsed.append(message)
    return collapsed


def _duplicate_tool_call_signature(message: AgentMessage) -> tuple[tuple[str, ...], tuple[tuple[str, str, object, bool], ...]] | None:
    if message.role != "assistant":
        return None
    text = tuple(part.content for part in message.parts if part.kind == "text")
    tool_calls = tuple(
        (
            str(part.metadata.get("tool_call_id") or ""),
            str(part.metadata.get("tool_name") or ""),
            part.metadata.get("arguments", {}),
            part.metadata.get("async_execution") is True,
        )
        for part in message.parts
        if part.kind == "tool_call"
    )
    if not tool_calls or any(not call_id or not name for call_id, name, _, _ in tool_calls):
        return None
    if any(part.kind not in {"text", "tool_call"} for part in message.parts):
        return None
    return text, tool_calls




def _project_user_content_parts(
    parts: list[MessagePart],
    *,
    content: str,
    store_root: Path | None,
    supports_pdf_input: bool = False,
    session_id: str,
) -> list[ContentPart] | None:
    """Build provider-neutral rich content from persisted user attachments.

    Text remains in ``ChatMessage.content`` for legacy callers.  Images are only
    read from the session attachment store at request construction time, so the
    JSONL event log contains paths and metadata rather than base64 payloads.
    """

    content_parts = [ContentPart(type="text", text=content)]
    for part in parts:
        file_path = None
        if part.kind == "file":
            file_path = _verified_file_attachment_path(part, store_root=store_root, session_id=session_id)
            filename = str(part.metadata.get("filename") or "attachment")
            if file_path is not None:
                reference = json.dumps({"path": str(file_path), "name": filename,
                                        "content_type": part.metadata.get("media_type", "application/octet-stream")},
                                       ensure_ascii=False)
                note = "[Saved attachment reference; read with permission-controlled tools when needed]\n" + reference
            else:
                note = f"[Saved attachment unavailable: {filename}. The file is missing or could not be verified; ask the user to attach it again.]"
            content_parts.append(ContentPart(type="text", text=note))
        if part.kind == "file" and part.metadata.get("media_type") == "application/pdf":
            filename = str(part.metadata.get("filename") or "document.pdf")
            pdf_path = file_path
            reason = None
            if not supports_pdf_input:
                reason = "当前模型未启用 PDF 文件输入"
            elif pdf_path is None:
                reason = "附件缺失或路径不可用"
            else:
                try:
                    raw = pdf_path.read_bytes()
                    if not raw.startswith(b"%PDF-"):
                        raise ValueError("附件不是有效的 PDF 文件头")
                    digest = part.metadata.get("sha256")
                    if digest and sha256(raw).hexdigest() != digest:
                        raise ValueError("附件内容与保存时的校验值不一致")
                    content_parts.append(ContentPart(type="file", media_type="application/pdf",
                                                    data_base64=base64.b64encode(raw).decode("ascii"), filename=filename,
                                                    estimated_tokens=_pdf_token_estimate(str(pdf_path), sha256(raw).hexdigest())))
                except (OSError, ValueError) as exc:
                    reason = str(exc)
            note = (f"[PDF 未发送：{filename}；{reason}。请使用 PDF 工具读取可访问的原文件。]" if reason
                    else f"[PDF 已作为文件输入：{filename}。可直接阅读正文和页面图像；需要定位或补读时再使用 PDF 工具。]")
            content_parts.append(ContentPart(type="text", text=note))
            continue
        if part.kind != "image":
            continue
        image_path = _attachment_path(part, store_root=store_root)
        media_type = part.metadata.get("media_type")
        if image_path is None or not isinstance(media_type, str):
            continue
        try:
            data_base64 = load_image_base64(image_path)
        except OSError:
            continue
        content_parts.append(
            ContentPart(
                type="image",
                media_type=media_type,
                data_base64=data_base64,
                filename=str(part.metadata.get("filename") or image_path.name),
            )
        )
    return content_parts if len(content_parts) > 1 else None


def _verified_file_attachment_path(part: MessagePart, *, store_root: Path | None, session_id: str) -> Path | None:
    """Keep tool references scoped to saved files and check recorded integrity facts."""
    relative = part.metadata.get("path")
    if (store_root is None or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id)
            or not isinstance(relative, str) or "\\" in relative or ":" in relative
            or ".." in relative.split("/") or Path(relative).is_absolute()):
        return None
    root = store_root.resolve()
    directory = root / "attachments" / session_id
    path = root / relative
    try:
        if path.parent != directory or path.resolve() != path:
            return None
        for candidate in (root / "attachments", directory, path):
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                return None
        if not stat.S_ISREG(path.stat().st_mode):
            return None
        size = part.metadata.get("bytes")
        if size is not None and (type(size) is not int or size < 0 or path.stat().st_size != size):
            return None
        digest = part.metadata.get("sha256")
        if digest is not None:
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                return None
            actual = sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(64 * 1024):
                    actual.update(chunk)
            if actual.hexdigest() != digest:
                return None
        return path
    except (OSError, ValueError, RuntimeError):
        return None


def _attachment_path(part: MessagePart, *, store_root: Path | None) -> Path | None:
    relative_path = part.metadata.get("path")
    if store_root is None or not isinstance(relative_path, str):
        return None
    root = store_root.resolve()
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() else None


@lru_cache(maxsize=32)
def _pdf_token_estimate(path: str, digest: str) -> int:
    """Approximate text plus page images; digest invalidates cached estimates after file changes."""
    import pypdfium2 as pdfium
    from raccoon.runtime.pdf_lock import PDFIUM_LOCK

    try:
        with PDFIUM_LOCK, pdfium.PdfDocument(path) as document:
            tokens = 0
            for page in document:
                try:
                    textpage = page.get_textpage()
                    try:
                        tokens += (len(textpage.get_text_range()) + 3) // 4 + 2048
                    finally:
                        textpage.close()
                finally:
                    page.close()
            return tokens
    except Exception:
        # Unreadable local text does not establish that the remote PDF reader will fail.
        return 4096
