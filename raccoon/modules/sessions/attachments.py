"""持久保存会话附件，校验文件、索引和会话归属。"""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import json
import re
import stat
import portalocker

import raccoon.modules.sessions.user_attachments as storage
from raccoon.app.payloads import AttachmentRef
from raccoon.modules.sessions.catalog import is_safe_session_id


MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_MESSAGE_BYTES = 40 * 1024 * 1024
MAX_ATTACHMENTS = 16
_ID = re.compile(r"[a-f0-9]{32}")
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".tiff", ".tif"}


def validate_upload(name: str, content_type: str) -> tuple[str, str]:
    if (not name or name in {".", ".."} or len(name) > 256
            or any(c in name for c in '/\\<>:"|?*') or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError("附件名称无效")
    media_type = content_type.split(";", 1)[0].strip().lower() or "application/octet-stream"
    if len(media_type) > 128 or not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", media_type):
        raise ValueError("附件类型无效")
    if media_type == "image/jpg":
        media_type = "image/jpeg"
    if media_type == "application/octet-stream" and Path(name).suffix.lower() in _IMAGE_SUFFIXES:
        media_type = storage.guess_media_type(Path(name))
    if media_type.startswith("image/") and media_type not in _IMAGE_TYPES:
        raise ValueError("图片仅支持 PNG、JPEG、GIF 和 WebP")
    return name, media_type


class SessionAttachmentStore:
    """保存一个会话的附件及原有上传索引，不生成展示地址。"""

    def __init__(self, root: Path, session_id: str):
        if not is_safe_session_id(session_id):
            raise ValueError("附件会话无效")
        self.root = root.absolute()
        self.session_id = session_id
        self.directory = self.root / "attachments" / session_id
        self.manifest = self.directory / "web-manifest.json"

    def _safe_path(self, path: Path, *, missing=False):
        try:
            relative = path.relative_to(self.root)
            # Inspect every component, including Windows junction/reparse points.
            for target in [self.root, *(self.root / parent for parent in reversed(relative.parents)), path]:
                if target.exists() or target.is_symlink():
                    info = target.lstat()
                    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                        raise ValueError("附件存储不可重定向")
            if path.resolve() != path or (not missing and not path.is_file()):
                raise ValueError("附件不存在或不可读取")
        except (OSError, RuntimeError) as error:
            raise ValueError("附件不存在或不可读取") from error

    def _entries(self) -> dict[str, AttachmentRef]:
        self._safe_path(self.manifest, missing=True)
        if not self.manifest.exists():
            return {}
        try:
            if self.manifest.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("附件索引过大")
            saved = json.loads(self.manifest.read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or any(not _ID.fullmatch(key) for key in saved):
                raise ValueError("附件索引无效")
            return {key: AttachmentRef(**item) for key, item in saved.items()}
        except (OSError, TypeError, ValueError) as error:
            raise ValueError("附件索引不可读取，请重新选择文件") from error

    def _resolve(self, ref: AttachmentRef):
        if ref.size_bytes > MAX_FILE_BYTES:
            raise ValueError("单个附件不能超过 20 MiB")
        self._safe_path(self.root / ref.relative_path)
        try:
            return storage.resolve_queued_attachments((ref,), store_root=self.root, session_id=self.session_id)[0]
        except (OSError, ValueError, RuntimeError) as error:
            raise ValueError("附件不存在、已变更或不属于当前会话，请重新选择") from error

    def stage(self, raw: bytes, name: str, media_type: str) -> tuple[str, AttachmentRef]:
        self._safe_path(self.manifest, missing=True)
        self.directory.mkdir(parents=True, exist_ok=True)
        lock = self.directory / 'web-manifest.lock'
        self._safe_path(lock, missing=True)
        # Multiple Web processes may attach to the same personal owner.
        with portalocker.Lock(str(lock), timeout=5):
            return self._stage(raw, name, media_type)

    def _stage(self, raw: bytes, name: str, media_type: str) -> tuple[str, AttachmentRef]:
        name, media_type = validate_upload(name, media_type)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("单个附件不能超过 20 MiB")
        if media_type.startswith("image/"):
            try:
                storage.validate_tool_image(raw, media_type)
            except ValueError as error:
                raise ValueError("图片损坏或内容与声明格式不一致") from error
        self._safe_path(self.manifest, missing=True)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._safe_path(self.manifest, missing=True)
        entries = self._entries()
        identity = uuid4().hex
        temporary = self.directory / f"upload-{identity}{Path(name).suffix}"
        destination = self.directory / f"{sha256(raw).hexdigest()[:16]}-{storage._safe_filename(name)}"
        self._safe_path(destination, missing=True)
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
            attachment = storage.UserAttachment("image" if media_type.startswith("image/") else "file",
                temporary, name, media_type, len(raw), "web:" + identity)
            ref = storage.prepare_queued_attachments([attachment], store_root=self.root, session_id=self.session_id)[0]
            self._resolve(ref)
            entries[identity] = ref
            manifest_tmp = self.directory / f"manifest-{identity}.tmp"
            try:
                with manifest_tmp.open("x", encoding="utf-8") as stream:
                    json.dump({key: asdict(item) for key, item in entries.items()}, stream, ensure_ascii=False)
                    stream.flush()
                self._safe_path(self.manifest, missing=True)
                manifest_tmp.replace(self.manifest)
            finally:
                manifest_tmp.unlink(missing_ok=True)
        except OSError as error:
            raise ValueError("附件保存失败，请检查存储空间后重试") from error
        finally:
            temporary.unlink(missing_ok=True)
        return identity, ref

    def staged_refs(self, identities: list[str]) -> tuple[AttachmentRef, ...]:
        if not identities:
            return ()
        if len(identities) > MAX_ATTACHMENTS or len(identities) != len(set(identities)):
            raise ValueError("每条消息最多 16 个附件，且不能重复")
        entries = self._entries()
        refs = []
        for identity in identities:
            if not _ID.fullmatch(identity) or identity not in entries:
                raise ValueError("附件不存在或不属于当前会话，请重新选择")
            ref = entries[identity]
            path = self.root / ref.relative_path
            if path.parent != self.directory or ref.size_bytes > MAX_FILE_BYTES:
                raise ValueError("附件无效或不属于当前会话")
            self._safe_path(path)
            refs.append(ref)
        if sum(ref.size_bytes for ref in refs) > MAX_MESSAGE_BYTES:
            raise ValueError("每条消息的附件总大小不能超过 40 MiB")
        return tuple(refs)

    def references_for_queued(self, refs: tuple[AttachmentRef, ...]) -> list[tuple[str, AttachmentRef]]:
        if not refs:
            return []
        try:
            entries = self._entries()
        except ValueError:
            return []
        references = []
        for ref in refs:
            scheme, _, identity = ref.source.partition(':')
            if scheme == 'web' and entries.get(identity) == ref:
                references.append((identity, ref))
        return references

    def read(self, identity: str):
        ref, = self.staged_refs([identity])
        file = self._resolve(ref)
        # Returning verified bytes avoids FileResponse reopening a swapped path.
        with file.path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) != ref.size_bytes or sha256(raw).hexdigest() != ref.sha256:
            raise ValueError("附件内容已变更")
        return raw, ref

    def references_for_parts(self, parts):
        parts = [part for part in parts if part.kind in {"image", "file"}]
        if not parts:
            return []
        try:
            entries = self._entries()
        except ValueError:
            return []
        result = []
        for part in parts:
            for identity, ref in entries.items():
                if (part.metadata.get("path") == ref.relative_path and part.metadata.get("sha256") == ref.sha256
                        and part.metadata.get("filename") == ref.filename and part.metadata.get("source") == ref.source):
                    try:
                        self._resolve(ref)
                    except ValueError:
                        break
                    result.append((identity, ref))
                    break
        return result

    def index_copied_refs(self, refs):
        """为已复制到当前会话的引用建立索引，保留排他创建语义。"""
        entries = {}
        for ref in refs:
            identity = ref.source[4:] if re.fullmatch(r'web:[a-f0-9]{32}', ref.source) else uuid4().hex
            entries[identity] = asdict(ref)
        if entries:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._safe_path(self.manifest, missing=True)
            with self.manifest.open('x', encoding='utf-8') as stream:
                json.dump(entries, stream, ensure_ascii=False)
