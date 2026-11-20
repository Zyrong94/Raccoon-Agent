"""Read compatibility sources without copying packages or touching credentials."""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tomllib

from raccoon.config.settings import AppConfig, default_global_config_path


def source_record(identifier, config, content="", *, package=None, path=None, builtin=False, include_package_assets=True):
    digest = hashlib.sha256(json.dumps([config, content], sort_keys=True).encode())
    if package:
        from raccoon.config.management import _safe
        _safe(Path(package))
        definitions = [Path(path)] if path else [Path(package) / "config.yaml", Path(package) / "SOUL.md"]
        for definition in definitions:
            _safe(definition)
        # Include assets so editing a stale form cannot replace a newly changed package.
        for asset in sorted(Path(package).rglob("*")) if include_package_assets else ():
            _safe(asset)
            if asset.is_file():
                digest.update(asset.relative_to(package).as_posix().encode())
                metadata = asset.stat()
                digest.update(f"{metadata.st_mtime_ns}:{metadata.st_size}".encode())
    result = {"id": identifier, "revision": digest.hexdigest(), "config": deepcopy(config),
              "content": content, "source": "builtin" if builtin else "managed", "read_only": builtin}
    if package:
        result["_package_path"] = str(package)
    if path:
        result["path"] = str(path)
    return result


def source_records(store, *, app_config=None, project_path=None, data_root=None, domains=None, env=None, include_package_assets=True, registry=None):
    from raccoon.config.management import DOMAINS
    from raccoon.config.subagents_config import DEFAULT_SUBAGENTS_ROOT, discover_subagent_configs
    from raccoon.config.skill_files import skill_records

    result = {domain: {} for domain in DOMAINS}
    warnings = []
    subagent_owners = {}
    selected = set(domains or DOMAINS)
    current_project = Path(project_path).resolve() if project_path is not None else None
    if "skills" in selected:
        try:
            records = skill_records(project_path, env=env, include_package_assets=include_package_assets)
            result["skills"] = {(record["scope"], record["id"]): record for record in records}
        except (OSError, ValueError, UnicodeError) as error:
            warnings.append(f"技能目录无法读取：{error}")
    if selected == {"skills"}:
        return {**{domain: list(records.values()) for domain, records in result.items()}, "warnings": warnings}
    registry = store._read() if registry is None else registry
    deleted = {domain: set(registry.get("_deleted", {}).get(domain, [])) for domain in DOMAINS}
    deleted["skills"] = set()
    sources = list(registry.get("_sources", []))
    if project_path is not None:
        current = {"project_path": str(current_project), "data_root": str(data_root) if data_root is not None else None}
        if not any(source["project_path"] == current["project_path"] for source in sources):
            sources.append(current)
    elif data_root is not None and not any(source.get("data_root") == str(Path(data_root).resolve()) for source in sources):
        sources.append({"project_path": None, "data_root": str(Path(data_root).resolve())})
    # Registered projects that no longer exist contribute nothing. Checking each
    # one once here avoids stat-ing every candidate file underneath them, which
    # dominates when the registry has accumulated stale or temporary entries.
    sources = [source for source in sources
               if source.get("project_path") is None or Path(source["project_path"]).is_dir()]
    if not sources:
        sources = [{"project_path": None, "data_root": None}]
    configurations = {}
    def read(path):
        if path in configurations:
            return configurations[path]
        value = None
        try:
            if path.is_file():
                value = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            warnings.append(f"配置来源无法读取：{path}")
        configurations[path] = value
        return value

    for source in sources:
        project = Path(source["project_path"]) if source["project_path"] else None
        if not selected & {"mcp"}:
            config = AppConfig(env={})
        elif app_config is not None and current_project is not None and project == current_project:
            config = app_config
            # Runtime snapshots can predate external edits; reread paths when available.
            config = AppConfig(env=config.env,
                               global_config=read(config.global_config_path) if config.global_config_path else config.global_config,
                               project_config=read(config.project_config_path) if config.project_config_path else config.project_config)
        else:
            config = AppConfig(env={}, global_config=read(default_global_config_path()),
                               project_config=read(project / "raccoon.toml") if project else None)
        try:
            servers = config.mcp_config() if "mcp" in selected else {}
        except ValueError:
            warnings.append(f"MCP 配置来源无效：{project or default_global_config_path()}")
            servers = {}
        for name, fields in servers.items():
            if not isinstance(fields, dict):
                warnings.append(f"MCP 配置来源无效：{name}")
                continue
            record = source_record(name, fields)
            result["mcp"].setdefault(name, record)
        if "subagents" in selected:
            root = Path(source["data_root"]) if source.get("data_root") else store.root / ".no-project"
            local_root = root / "subagents"
            local_names = {path.name.casefold() for path in local_root.iterdir() if path.is_dir()} if local_root.is_dir() else set()
            definitions = discover_subagent_configs(data_root=root, include_managed=False)
            for key in local_names:
                if key not in subagent_owners:
                    subagent_owners[key] = root
                    # Invalid custom definitions must also suppress a broader built-in template.
                    result["subagents"].pop(key, None)
                if key not in {name.casefold() for name in definitions}:
                    warnings.append(f"子代理来源无效，已停用且不会回退内置模板：{key}")
            for name, definition in definitions.items():
                fields = asdict(definition)
                content = fields.pop("system_prompt")
                local = root / "subagents" / name.lower()
                builtin = not local.exists()
                package = DEFAULT_SUBAGENTS_ROOT / name.lower() if builtin else local
                key = name.casefold()
                if key in subagent_owners and (builtin or subagent_owners[key] != root):
                    continue
                if key in result["subagents"] and (builtin or not result["subagents"][key]["read_only"]):
                    continue
                try:
                    result["subagents"][key] = source_record(name, fields, content, package=package, builtin=builtin,
                                                            include_package_assets=include_package_assets)
                except (OSError, ValueError, UnicodeError):
                    warnings.append(f"子代理来源无法读取或包含不支持的链接：{name}")
    if "models" in selected:
        result["models"] = {record["id"]: record for record in store.models.records()}
        deleted["models"] = set()
    return {**{domain: [record for key, record in records.items() if (key.casefold() if domain == "subagents" else key) not in deleted[domain]]
               for domain, records in result.items()}, "warnings": warnings}
