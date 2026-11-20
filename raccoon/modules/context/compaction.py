"""Select a Pi-style cut, summarize old messages, and return a record for persistence.

Reference: earendil-works/pi, commit 3035f08d06c3054f99b6226ea1ad22353c49fc12.
No content routing, archive replacement, first-consumption state or fallback pipeline.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from raccoon.modules.context.summary import summarize, combine_usage
from raccoon.modules.context.token_budget import IMAGE_INPUT_TOKEN_ESTIMATE, estimate_text_tokens
from raccoon.modules.context.tool_sequence import validate_tool_call_sequence, earliest_pending_async_index, InvalidToolCallSequenceError
from raccoon.modules.sessions.compaction import CompactionRecord
from raccoon.modules.sessions.identity import new_event_id
from raccoon.modules.sessions.models import AgentMessage, SessionData, utc_now_iso
from raccoon.runtime.model_provider import ChatProvider
from raccoon.runtime.model_types import MainRequestOptions

@dataclass(frozen=True, slots=True)
class CompactionSettings:
    enabled: bool = True
    reserve_tokens: int = 16384
    keep_recent_tokens: int = 20000

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("compaction.enabled must be boolean")
        for value in (self.reserve_tokens, self.keep_recent_tokens):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("compaction token budgets must be non-negative integers")

@dataclass(slots=True)
class CompactionPreparation:
    messages_to_summarize: list[AgentMessage]
    turn_prefix_messages: list[AgentMessage]
    first_kept_message_id: str
    previous_summary: str | None
    tokens_before: int


def effective_messages(view: SessionData) -> list[AgentMessage]:
    messages = [message for message in view.messages if message.role != "system_meta"]
    if not view.compactions:
        return messages
    boundary = view.compactions[-1].first_kept_message_id
    for index, message in enumerate(messages):
        if message.id == boundary:
            return messages[index:]
    raise ValueError(f"Compaction boundary not found: {boundary}")


def estimate_message_tokens(message: AgentMessage) -> int:
    tokens = 0
    for part in message.parts:
        tokens += estimate_text_tokens(str(part.metadata.get("recalled_memory", "")))
        if part.kind == "tool_call":
            tokens += estimate_text_tokens(str(part.metadata.get("tool_name", "")))
            tokens += estimate_text_tokens(json.dumps(part.metadata.get("arguments", {}), ensure_ascii=False))
        elif part.kind == "image":
            tokens += IMAGE_INPUT_TOKEN_ESTIMATE
        elif part.kind == "file":
            tokens += int(part.metadata.get("estimated_tokens") or estimate_text_tokens(part.content) or 4096)
        elif part.kind in {"text", "tool_result"}:
            tokens += estimate_text_tokens(part.content)
            if part.kind == "tool_result":
                tokens += len(part.metadata.get("images", [])) * IMAGE_INPUT_TOKEN_ESTIMATE
    return tokens


def prepare_compaction(view: SessionData, settings: CompactionSettings, tokens_before: int) -> CompactionPreparation | None:
    messages = effective_messages(view)
    # A result cannot outlive its call. Native asynchronous calls can span user
    # messages in Raccoon, so only protocol-valid suffixes are possible cuts.
    pending_index = earliest_pending_async_index(messages)
    # Unreceived background results must reach the main model verbatim first.
    for index, message in enumerate(messages):
        if any(part.metadata.get("background_delivery") == "pending" for part in message.parts):
            pending_index = min(index, pending_index) if pending_index is not None else index
    cuts = []
    for index, message in enumerate(messages):
        if message.role not in {"user", "assistant"}:
            continue
        if pending_index is not None and index > pending_index:
            continue
        try:
            validate_tool_call_sequence(messages[index:])
        except InvalidToolCallSequenceError:
            continue
        cuts.append(index)
    if not cuts:
        return None
    cut = cuts[0]
    tokens = 0
    for index in range(len(messages) - 1, -1, -1):
        size = estimate_message_tokens(messages[index])
        if not size:
            continue
        tokens += size
        if tokens >= settings.keep_recent_tokens:
            cut = next((candidate for candidate in cuts if candidate >= index), cuts[-1])
            break
    if cut == 0:
        return None
    turn_start = -1
    if messages[cut].role != "user" or messages[cut].metadata.get("source_kind") == "agent_status":
        for index in range(cut, -1, -1):
            if messages[index].role == "user" and messages[index].metadata.get("source_kind") != "agent_status":
                turn_start = index
                break
    history_end = turn_start if turn_start >= 0 else cut
    return CompactionPreparation(
        messages_to_summarize=messages[:history_end],
        turn_prefix_messages=messages[turn_start:cut] if turn_start >= 0 else [],
        first_kept_message_id=messages[cut].id,
        previous_summary=view.compactions[-1].summary if view.compactions else None,
        tokens_before=tokens_before,
    )


async def generate_compaction(
    preparation: CompactionPreparation, *, session_id: str,
    provider: ChatProvider, request_options: MainRequestOptions,
    instructions: str = "",
) -> CompactionRecord:
    history = preparation.previous_summary or "No prior history."
    usage = None
    generated_history = bool(preparation.messages_to_summarize)
    if generated_history:
        history, usage = await summarize(
            preparation.messages_to_summarize, provider=provider,
            request_options=request_options, previous_summary=preparation.previous_summary,
            instructions=instructions,
        )
    if preparation.turn_prefix_messages:
        prefix, prefix_usage = await summarize(
            preparation.turn_prefix_messages, provider=provider,
            request_options=request_options, turn_prefix=True, instructions=instructions,
        )
        history += "\n\n---\n\n**Turn Context (split turn):**\n\n" + prefix
        usage = combine_usage(usage, prefix_usage) if generated_history else prefix_usage
    return CompactionRecord(
        id=new_event_id(), session_id=session_id, created_at=utc_now_iso(),
        summary=history, first_kept_message_id=preparation.first_kept_message_id,
        tokens_before=preparation.tokens_before, usage=usage,
    )
