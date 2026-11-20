"""压缩记录：摘要和第一条保留消息，不包含旧压缩策略状态。"""
from dataclasses import asdict, dataclass
from raccoon.runtime.model_types import TokenUsage

@dataclass(slots=True)
class CompactionRecord:
    id: str
    session_id: str
    created_at: str
    summary: str
    first_kept_message_id: str
    tokens_before: int
    usage: TokenUsage | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "CompactionRecord":
        data = dict(value)
        if data.get("usage") is not None:
            data["usage"] = TokenUsage(**data["usage"])
        return cls(**data)
