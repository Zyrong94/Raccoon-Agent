"""Personal configuration library. Importing and saving never execute packages."""

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
import tomllib
from uuid import uuid4

import portalocker
import yaml

from raccoon.config.model_store import ModelConfigStore
from raccoon.config.settings import default_global_config_path
from raccoon.config.subagents_config import load_subagent_config

DOMAINS = ("models", "mcp", "skills", "subagents")
MASK = "••••••"
_LOCK = threading.RLock()


def sensitive_name(name):
    return name.lower() in {"authorization", "proxy-authorization", "cookie", "set-cookie"} or any(word in name.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))


class ConfigurationConflict(ValueError):
    pass


def managed_root() -> Path:
    return default_global_config_path().parent / "managed"


def _safe(path: Path) -> None:
    for item in [path, *path.parents]:
        if item.exists() or item.is_symlink():
            metadata = item.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError("配置路径不能包含符号链接或目录联接")


def _identifier(value: str) -> str:
    pattern = r"[A-Za-z0-9_-]{1,80}"
    if not isinstance(value, str) or not re.fullmatch(pattern, value) or any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("配置标识无效")
    if re.fullmatch(r"(?i:con|prn|aux|nul|com[0-9]|lpt[0-9])", value):
        raise ValueError("配置标识为系统保留名称")
    return value


class ManagedConfigStore:
    # Borrowed projects are rescanned at most this often; their file mtimes are
    # still compared on every fingerprint.
    borrowed_paths_interval = 1.0

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else managed_root()
        self.models = ModelConfigStore(self.root.parent / "models.toml")

    @property
    def skills_root(self):
        from raccoon.modules.skills.discovery import raccoon_skills_root
        return raccoon_skills_root()

    @property
    def subagents_root(self):
        return self.root / "subagents"

    def _read(self):
        _safe(self.root)
        path = self.root / "registry.json"
        _safe(path)
        if not path.exists():
            return {domain: {} for domain in DOMAINS}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or any(not isinstance(value.get(domain, {}), dict) for domain in DOMAINS):
                raise ValueError()
            sources, deleted = value.get("_sources", []), value.get("_deleted", {})
            if not isinstance(sources, list) or any(not isinstance(source, dict) or not isinstance(source.get("project_path"), str) or source.get("data_root") is not None and not isinstance(source["data_root"], str) for source in sources):
                raise ValueError()
            if not isinstance(deleted, dict) or any(domain not in DOMAINS or not isinstance(names, list) or any(not isinstance(name, str) for name in names) for domain, names in deleted.items()):
                raise ValueError()
            return {**{domain: value.get(domain, {}) for domain in DOMAINS},
                    "_sources": value.get("_sources", []), "_deleted": value.get("_deleted", {})}
        except (OSError, ValueError):
            raise ValueError("个人配置目录无法读取；原配置未修改") from None

    def _write(self, value):
        _safe(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, delete=False, suffix=".tmp") as stream:
            name = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            _safe(self.root / "registry.json")
            os.replace(name, self.root / "registry.json")
        finally:
            Path(name).unlink(missing_ok=True)

    def records(self, domain):
        if domain == "skills":
            from raccoon.config.skill_files import skill_records
            return skill_records()
        if domain == "models":
            return self.models.records()
        self._domain(domain)
        return list(deepcopy(self._read()[domain]).values())

    def sources(self):
        return deepcopy(self._read().get("_sources", []))

    def register_source(self, project_path, data_root=None):
        source = {"project_path": str(Path(project_path).resolve()),
                  "data_root": str(Path(data_root).resolve()) if data_root is not None else None}
        if source in self.sources():
            return
        _safe(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        _safe(self.root / ".lock")
        with _LOCK, portalocker.Lock(str(self.root / ".lock"), timeout=5):
            value = self._read()
            sources = value.setdefault("_sources", [])
            if source not in sources:
                sources.append(source)
                self._write(value)

    def deleted_ids(self, domain):
        if domain in {"models", "skills"}:
            return set()
        self._domain(domain)
        return set(self._read().get("_deleted", {}).get(domain, []))

    @staticmethod
    def _identity(domain, identifier):
        return identifier.casefold() if domain in {"skills", "subagents"} else identifier

    @staticmethod
    def _domain(domain):
        if domain not in DOMAINS:
            raise ValueError("未知配置分类")

    @staticmethod
    def _check_payload_size(config, content):
        if not isinstance(config, dict) or len(json.dumps(config)) > 100000 or not isinstance(content, str) or len(content.encode()) > 1000000:
            raise ValueError("配置格式无效或内容过大")

    def validate(self, domain, config, content=""):
        self._domain(domain)
        if domain == "mcp":
            raise ValueError("MCP 配置由 raccoon.toml 或全局 config.toml 管理，个人库不再保存 MCP")
        self._check_payload_size(config, content)
        if domain == "models":
            identifier, _, _ = self.models.validate(config)
        else:
            identifier = _identifier(config.get("name", ""))
            if domain == "skills":
                if set(config) - {"name", "description"} or not content.strip():
                    raise ValueError("技能需要名称和非空 SKILL.md")
                from raccoon.modules.skills.discovery import _frontmatter_metadata
                metadata = _frontmatter_metadata(content)
                if content.lstrip().startswith("---"):
                    lines = content.splitlines()
                    try:
                        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
                        parsed = yaml.safe_load("\n".join(lines[1:end]))
                        if not isinstance(parsed, dict):
                            raise ValueError()
                    except (StopIteration, yaml.YAMLError, ValueError):
                        raise ValueError("SKILL.md 的 YAML 元数据无效") from None
                if metadata.get("name") and metadata["name"] != identifier:
                    raise ValueError("SKILL.md 名称须与技能名称一致")
            else:
                if not content.strip():
                    raise ValueError("子代理需要非空 SOUL.md")
                with tempfile.TemporaryDirectory() as temporary:
                    folder = Path(temporary) / "subagents" / identifier.lower()
                    folder.mkdir(parents=True)
                    (folder / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
                    (folder / "SOUL.md").write_text(content, encoding="utf-8")
                    load_subagent_config(identifier, data_root=Path(temporary), include_managed=False)
        return identifier

    def save(self, domain, config, content="", *, identifier=None, revision=None, files=None, source=None, scope="global", project_path=None, check_subagent_references=None):
        if domain == "skills":
            from raccoon.config.skill_files import save_skill
            return save_skill(self, config, content, identifier=identifier, revision=revision, files=files,
                              scope=scope, project_path=project_path)
        if domain == "models":
            self._check_payload_size(config, content)
            return self.models.save(config, identifier=identifier, revision=revision)
        self._domain(domain)
        if domain == "mcp":
            raise ValueError("MCP 配置由 raccoon.toml 或全局 config.toml 管理，个人库不再保存 MCP")
        config = deepcopy(config)
        if any(str(key).startswith("_") for key in config):
            raise ValueError("配置不能指定内部字段")
        _safe(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        _safe(self.root / ".lock")
        with _LOCK, portalocker.Lock(str(self.root / ".lock"), timeout=5):
            value = self._read()
            previous = value[domain].get(identifier) if identifier else None
            if previous is None and identifier and source is not None:
                previous = source() if callable(source) else source
                if previous and previous.get("read_only"):
                    raise ValueError("内置模板请基于模板创建个人子代理")
            if identifier and (previous is None or revision != previous["revision"]):
                raise ConfigurationConflict("配置已修改或不存在，请刷新后重试")
            derived = self.validate(domain, config, content)
            if check_subagent_references is not None:
                check_subagent_references()
            if identifier and identifier != derived:
                raise ValueError("已有配置标识不可修改")
            identifier = derived
            if domain == "subagents" and any(name != identifier and name.casefold() == identifier.casefold() for name in value[domain]):
                raise ConfigurationConflict("名称与已有包仅大小写不同，请使用其他名称")
            if previous is None and identifier in value[domain]:
                raise ConfigurationConflict("标识已存在，请编辑已有配置或更换名称")
            if previous is None:
                from raccoon.config.management_sources import source_records
                existing = next((record for record in source_records(self, domains=(domain,))[domain]
                                 if self._identity(domain, record["id"]) == self._identity(domain, identifier)), None)
                if existing and not existing.get("read_only"):
                    raise ConfigurationConflict("标识已存在，请编辑已有配置或更换名称")
            record = {"id": identifier, "revision": uuid4().hex, "config": config, "content": content}
            backup = None
            folder = self.root / domain / identifier
            if domain == "subagents":
                if folder.exists():
                    _safe(folder)
                    backup = self.root / (".previous-" + uuid4().hex)
                    folder.rename(backup)
                try:
                    self._package(domain, identifier, config, content, files or {}, previous=backup or (previous or {}).get("_package_path"))
                except BaseException:
                    if folder.exists():
                        shutil.rmtree(folder)
                    if backup:
                        backup.rename(folder)
                    raise
            value[domain][identifier] = record
            deleted = value.setdefault("_deleted", {}).setdefault(domain, [])
            key = self._identity(domain, identifier)
            if key in deleted:
                deleted.remove(key)
            try:
                self._write(value)
            except BaseException:
                if domain == "subagents":
                    shutil.rmtree(folder)
                    if backup:
                        backup.rename(folder)
                raise
            if backup:
                shutil.rmtree(backup)
            return deepcopy(record)

    def _package(self, domain, identifier, config, content, files, previous=None, folder=None):
        folder = folder if folder is not None else self.root / domain / identifier
        _safe(folder)
        folder.mkdir(parents=True, exist_ok=True)
        if previous:
            previous = Path(previous)
            _safe(previous)
            total, count = 0, 0
            for path in previous.rglob("*"):
                _safe(path)
                if path.is_file():
                    total += path.stat().st_size
                    count += 1
                    if total > 10000000 or count > 500:
                        raise ValueError("包最多 500 个文件、10 MB")
            shutil.copytree(previous, folder, dirs_exist_ok=True)
        for relative, data in files.items():
            path = folder / relative
            if Path(relative).is_absolute() or ".." in Path(relative).parts or path.resolve().is_relative_to(folder.resolve()) is False:
                raise ValueError("导入文件超出包目录")
            _safe(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if domain == "skills":
            (folder / "SKILL.md").write_text(content, encoding="utf-8")
        else:
            (folder / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
            (folder / "SOUL.md").write_text(content, encoding="utf-8")

    def delete(self, domain, identifier, revision, *, source=None, scope="global", project_path=None):
        if domain == "skills":
            from raccoon.config.skill_files import delete_skill
            return delete_skill(identifier, revision, scope=scope, project_path=project_path)
        if domain == "models":
            return self.models.delete(identifier, revision)
        self._domain(domain)
        if domain == "mcp":
            raise ValueError("MCP 配置由 raccoon.toml 或全局 config.toml 管理，个人库不再管理 MCP")
        _safe(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        _safe(self.root / ".lock")
        with _LOCK, portalocker.Lock(str(self.root / ".lock"), timeout=5):
            value = self._read()
            previous = value[domain].get(identifier)
            if previous is None and source is not None:
                previous = source() if callable(source) else source
            if previous and previous.get("read_only"):
                raise ValueError("内置模板不可删除")
            if not previous or previous["revision"] != revision:
                raise ConfigurationConflict("配置已修改或不存在，请刷新后重试")
            backup = None
            if domain == "subagents":
                folder = self.root / domain / _identifier(identifier)
                _safe(folder)
                if folder.exists():
                    backup = self.root / (".deleted-" + uuid4().hex)
                    folder.rename(backup)
            value[domain].pop(identifier, None)
            deleted = value.setdefault("_deleted", {}).setdefault(domain, [])
            key = self._identity(domain, identifier)
            if key not in deleted:
                deleted.append(key)
            try:
                self._write(value)
            except BaseException:
                if backup:
                    backup.rename(folder)
                raise
            if backup:
                shutil.rmtree(backup)

    def import_path(self, domain, path, *, scope="global", project_path=None):
        self._domain(domain)
        if domain == "mcp":
            raise ValueError("MCP 配置由 raccoon.toml 或全局 config.toml 管理，个人库不再导入 MCP")
        source = Path(path).expanduser()
        _safe(source)
        if domain == "models":
            if not source.is_file() or source.stat().st_size > 2000000:
                raise ValueError("请选择不超过 2 MB 的配置文件")
            return self.import_text(domain, source.read_text(encoding="utf-8"), format="json" if source.suffix.lower() == ".json" else "toml")
        if not source.is_dir():
            raise ValueError("请选择本地包目录")
        files, total = {}, 0
        for path in source.rglob("*"):
            _safe(path)
            if path.is_file():
                total += path.stat().st_size
                if total > 10000000 or len(files) >= 500:
                    raise ValueError("包最多 500 个文件、10 MB")
                files[path.relative_to(source).as_posix()] = path.read_bytes()
        if domain == "skills":
            if "SKILL.md" not in files:
                raise ValueError("目录缺少 SKILL.md")
            content = files["SKILL.md"].decode("utf-8")
            from raccoon.modules.skills.discovery import _frontmatter_metadata
            metadata = _frontmatter_metadata(content)
            config = {"name": metadata.get("name") or source.name, "description": metadata.get("description", "")}
        else:
            if not {"config.yaml", "SOUL.md"} <= files.keys():
                raise ValueError("目录需要 config.yaml 与 SOUL.md")
            config = yaml.safe_load(files["config.yaml"].decode("utf-8"))
            content = files["SOUL.md"].decode("utf-8")
        return [self.save(domain, config, content, files=files, scope=scope, project_path=project_path)]

    def import_text(self, domain, text, *, format="toml", content=""):
        self._domain(domain)
        if domain == "mcp":
            raise ValueError("MCP 配置由 raccoon.toml 或全局 config.toml 管理，个人库不再导入 MCP")
        if not isinstance(text, str) or len(text.encode()) > 2000000:
            raise ValueError("导入内容不得超过 2 MB")
        try:
            if format == "json":
                value = json.loads(text)
            elif format == "toml":
                value = tomllib.loads(text)
            elif format == "yaml" and domain == "subagents":
                value = yaml.safe_load(text)
            else:
                raise ValueError("不支持的导入格式")
        except (json.JSONDecodeError, tomllib.TOMLDecodeError, yaml.YAMLError):
            raise ValueError("导入配置语法无效，请检查格式") from None
        if not isinstance(value, dict):
            raise ValueError("导入配置须为对象或配置表")
        if domain == "subagents":
            return [self.save(domain, value, content)]
        if domain != "models":
            raise ValueError("技能包请通过目录导入")
        return self.models.import_config(value)


def public_record(record, domain):
    result = deepcopy(record)
    result.setdefault("source", "managed")
    result.setdefault("read_only", False)
    for key in list(result):
        if key.startswith("_"):
            result.pop(key)
    if domain == "mcp":
        result["config"]["name"] = result["id"]
        for field in ("env", "headers"):
            if field in result["config"]:
                result["config"][field] = {key: MASK if sensitive_name(key) and not re.fullmatch(r"\{env:[A-Za-z_][A-Za-z0-9_]*\}", value) else value for key, value in result["config"][field].items()}
    return result
