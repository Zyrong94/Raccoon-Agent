from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ModelEndpoint:
    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str = field(default="", repr=False)


@dataclass(frozen=True)
class MemoryConfig:
    storage_path: Path
    llm: ModelEndpoint
    embedder: ModelEndpoint
    dimensions: int = 1536
    namespace: str = "raccoon-personal"
    top_k: int = 3
    max_context_tokens: int = 800
    search_timeout_seconds: float = 3
    write_timeout_seconds: float = 30
    backend_fingerprint: str = ""
    embedding_fingerprint: str = ""
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class MemoryHit:
    id: str
    text: str
    project_root: str = ""
