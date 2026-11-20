"""Session attachment facts, copies and validation."""
from __future__ import annotations
import base64
import mimetypes
import re
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from PIL import Image
from raccoon.modules.tools.types import MAX_TOOL_IMAGE_BYTES
from raccoon.app.payloads import AttachmentRef
if TYPE_CHECKING:
    from raccoon.modules.tools.types import ToolImage


AttachmentKind = Literal["image", "file"]


MAX_IMAGE_BYTES = 20 * 1024 * 1024


MAX_INLINE_TEXT_BYTES = 200 * 1024


MAX_ATTACHMENTS_PER_MESSAGE = 16


_TEXT_MEDIA_PREFIXES = ("text/",)


_TEXT_MEDIA_TYPES = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-javascript",
    "application/yaml",
    "application/x-yaml",
    "application/toml",
    "application/x-sh",
    "application/sql",
}


@dataclass(slots=True)
class UserAttachment:
    """An attachment staged in the composer before send."""

    kind: AttachmentKind
    path: Path
    filename: str
    media_type: str
    size_bytes: int
    source: str = "path"


@dataclass(slots=True)
class PreparedAttachment:
    """Attachment copied into the session attachment store and ready for persistence."""

    kind: AttachmentKind
    filename: str
    media_type: str
    size_bytes: int
    relative_path: str
    sha256: str
    source: str = "path"
    inline_text: str | None = None


def guess_media_type(path: Path) -> str:
    media_type, _ = mimetypes.guess_type(str(path))
    if media_type == "image/jpg":
        return "image/jpeg"
    return media_type or "application/octet-stream"


def is_text_like_media_type(media_type: str, path: Path | None = None) -> bool:
    if media_type.startswith(_TEXT_MEDIA_PREFIXES) or media_type in _TEXT_MEDIA_TYPES:
        return True
    if path is not None:
        suffix = path.suffix.lower()
        if suffix in {
            ".md",
            ".txt",
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".csv",
            ".tsv",
            ".xml",
            ".html",
            ".css",
            ".scss",
            ".sh",
            ".bash",
            ".zsh",
            ".rs",
            ".go",
            ".java",
            ".kt",
            ".c",
            ".cc",
            ".cpp",
            ".h",
            ".hpp",
            ".sql",
            ".log",
            ".env",
            ".gitignore",
            ".dockerfile",
        }:
            return True
    return False


def prepare_attachments_for_session(
    attachments: list[UserAttachment],
    *,
    store_root: Path,
    session_id: str,
) -> list[PreparedAttachment]:
    """Copy attachments under the session attachment directory."""

    if not attachments:
        return []
    if len(attachments) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise ValueError(f"Too many attachments (max {MAX_ATTACHMENTS_PER_MESSAGE})")

    target_dir = store_root / "attachments" / session_id
    target_dir.mkdir(parents=True, exist_ok=True)
    prepared: list[PreparedAttachment] = []

    for item in attachments:
        raw = item.path.read_bytes()
        digest = sha256(raw).hexdigest()
        suffix = item.path.suffix or _suffix_for_media_type(item.media_type)
        safe_name = _safe_filename(item.filename or f"attachment{suffix}")
        dest_name = f"{digest[:16]}-{safe_name}"
        dest_path = target_dir / dest_name
        if not dest_path.exists():
            dest_path.write_bytes(raw)
        relative = dest_path.relative_to(store_root).as_posix()
        inline_text = _inline_attachment_text(item, raw)
        prepared.append(
            PreparedAttachment(
                kind=item.kind,
                filename=item.filename,
                media_type=item.media_type if item.media_type != "image/jpg" else "image/jpeg",
                size_bytes=item.size_bytes,
                relative_path=relative,
                sha256=digest,
                source=item.source,
                inline_text=inline_text,
            )
        )
    return prepared


def load_image_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def prepare_queued_attachments(
    attachments: list[UserAttachment], *, store_root: Path, session_id: str,
) -> tuple[AttachmentRef, ...]:
    """Stage bytes before queueing so temporary network URLs are never replayed."""
    if not re.fullmatch(r'[A-Za-z0-9_-]+', session_id):
        raise ValueError('Invalid attachment session')
    directory = store_root.resolve() / 'attachments' / session_id
    if directory.resolve() != directory:
        raise ValueError('Attachment session directory may not redirect')
    if any(item.kind == 'image' and item.path.stat().st_size > MAX_IMAGE_BYTES for item in attachments):
        raise ValueError('Queued image exceeds 20 MiB limit')
    prepared = prepare_attachments_for_session(attachments, store_root=store_root, session_id=session_id)
    refs = tuple(AttachmentRef(
        kind=item.kind, relative_path=item.relative_path, filename=item.filename,
        media_type=item.media_type, size_bytes=item.size_bytes, sha256=item.sha256, source=item.source,
    ) for item in prepared)
    resolve_queued_attachments(refs, store_root=store_root, session_id=session_id)
    return refs


def resolve_queued_attachments(
    refs: tuple[AttachmentRef, ...], *, store_root: Path, session_id: str,
) -> list[UserAttachment]:
    """Resolve only the owning session's files and verify the persisted facts."""
    if not re.fullmatch(r'[A-Za-z0-9_-]+', session_id) or len(refs) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise ValueError('Invalid attachment session or count')
    root = store_root.resolve()
    directory = root / 'attachments' / session_id
    if directory.resolve() != directory:
        raise ValueError('Attachment session directory may not redirect')
    result = []
    for ref in refs:
        path = root / ref.relative_path
        if path.parent != directory or path.resolve().parent != directory or path.is_symlink():
            raise ValueError('Attachment does not belong to this session')
        if not path.is_file() or path.stat().st_size != ref.size_bytes:
            raise ValueError('Attachment size or digest mismatch')
        if ref.kind == 'image':
            with path.open('rb') as stream:
                raw = stream.read(MAX_IMAGE_BYTES + 1)
            if len(raw) != ref.size_bytes or sha256(raw).hexdigest() != ref.sha256:
                raise ValueError('Attachment size or digest mismatch')
            validate_tool_image(raw, ref.media_type)
        else:
            digest, size = sha256(), 0
            with path.open('rb') as stream:
                while chunk := stream.read(64 * 1024):
                    size += len(chunk)
                    if size > ref.size_bytes:
                        raise ValueError('Attachment size or digest mismatch')
                    digest.update(chunk)
            if size != ref.size_bytes or digest.hexdigest() != ref.sha256:
                raise ValueError('Attachment size or digest mismatch')
        result.append(UserAttachment(ref.kind, path, ref.filename, ref.media_type, ref.size_bytes, ref.source))
    return result


def validate_tool_image(raw: bytes, media_type: str) -> None:
    formats = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP", "image/gif": "GIF"}
    try:
        with Image.open(BytesIO(raw)) as image:
            if media_type not in formats or image.format != formats[media_type]:
                raise ValueError("图片内容与声明格式不一致")
            image.verify()
        with Image.open(BytesIO(raw)) as image:
            image.load()
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise ValueError(f"图片无法完整解码：{error}") from error


def serialize_tool_images(images: list[ToolImage]) -> list[dict[str, object]]:
    """入库前检查一次，图片数据直接随工具结果保存在 JSONL 中。"""
    encoded = []
    for image in images:
        if len(image.data) > MAX_TOOL_IMAGE_BYTES:
            raise ValueError("Tool image exceeds 20 MiB per-image limit")
        if image.page is not None and (type(image.page) is not int or image.page < 1):
            raise ValueError("Tool image page must be a positive integer")
        validate_tool_image(image.data, image.media_type)
        encoded.append({
            "data": base64.b64encode(image.data).decode("ascii"),
            "media_type": image.media_type,
            "source_path": image.source_path,
            "page": image.page,
        })
    return encoded


def _inline_attachment_text(item: UserAttachment, raw: bytes) -> str | None:
    if item.kind != "file" or item.size_bytes > MAX_INLINE_TEXT_BYTES or not is_text_like_media_type(item.media_type, item.path):
        return None
    return raw.decode("utf-8", errors="replace")


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", name.strip()) or "attachment"
    return cleaned[:120]


def _suffix_for_media_type(media_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "text/plain": ".txt",
        "application/json": ".json",
    }.get(media_type, "")
