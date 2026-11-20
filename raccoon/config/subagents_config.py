"""File-backed subagent definitions and validation."""

from dataclasses import dataclass
import logging
import math
from pathlib import Path
import re

import yaml

logger = logging.getLogger(__name__)
DEFAULT_SUBAGENTS_ROOT = Path(__file__).parent / "default_subagents"


@dataclass(frozen=True, slots=True)
class SubagentConfig:
    name: str
    description: str = ""
    system_prompt: str = ""
    tools: tuple[str, ...] | None = None
    skills: tuple[str, ...] | None = None
    max_tool_rounds: int = 20
    max_provider_calls: int = 40
    max_turn_seconds: float = 600
    allow_background: bool = False
    requires_worktree: bool = False


class SubagentConfigError(ValueError):
    """Raised when a subagent definition cannot be loaded or validated."""


def _load_config_values(name: str, *, path: Path) -> dict:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SubagentConfigError(f"subagent config not found: {path}") from error
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise SubagentConfigError(f"cannot read subagent config {path}: {error}") from error
    if not isinstance(raw, dict):
        raise SubagentConfigError(f"subagent config root must be a mapping: {path}")
    unknown = set(raw) - (set(SubagentConfig.__dataclass_fields__) - {"system_prompt"})
    if unknown:
        raise SubagentConfigError(f"unsupported subagent config fields: {', '.join(sorted(map(str, unknown)))}")
    config_name = raw.get("name", name)
    if not isinstance(config_name, str) or re.fullmatch(r"[A-Za-z0-9-]+", config_name) is None:
        raise SubagentConfigError("subagent config name must contain only letters, digits, and hyphens")
    if config_name.lower() != name.lower():
        raise SubagentConfigError("subagent config name must match its directory")
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise SubagentConfigError("subagent config description must be a string")
    options = dict(name=config_name, description=description)
    for key in ("tools", "skills"):
        value = raw.get(key)
        if value is not None:
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise SubagentConfigError(f"{key} must be a list of nonempty strings")
            options[key] = tuple(dict.fromkeys(value))
    for key in ("max_tool_rounds", "max_provider_calls", "max_turn_seconds"):
        value = raw.get(key)
        if value is not None:
            expected = (int, float) if key == "max_turn_seconds" else (int,)
            if isinstance(value, bool) or not isinstance(value, expected) or not math.isfinite(value) or value <= 0:
                raise SubagentConfigError(f"{key} must be positive")
            options[key] = value
    for key in ("allow_background", "requires_worktree"):
        if key in raw:
            if type(raw[key]) is not bool:
                raise SubagentConfigError(f"{key} must be a boolean")
            options[key] = raw[key]
    return options


def load_subagent_config(name: str, *, data_root: Path, include_managed: bool = True) -> SubagentConfig:
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9-]+", name) is None:
        raise SubagentConfigError("invalid subagent name")
    directory = data_root / "subagents" / name.lower()
    if include_managed:
        from raccoon.config.management import ManagedConfigStore
        from raccoon.config.management_sources import source_records

        managed = ManagedConfigStore()
        if name.casefold() in managed.deleted_ids("subagents"):
            raise SubagentConfigError(f"subagent is deleted: {name}")
        record = next((record for record in managed.records("subagents") if record["id"].casefold() == name.casefold()), None)
        if record is None:
            record = next((record for record in source_records(
                managed, data_root=data_root, domains={"subagents"}, include_package_assets=False,
            )["subagents"] if record["id"].casefold() == name.casefold()), None)
            if record is None:
                raise SubagentConfigError(f"subagent is not configured: {name}")
            directory = Path(record["_package_path"])
        else:
            directory = managed.subagents_root / record["id"]
        return _load_subagent_directory(name, directory)
    # A local definition replaces the whole default, including its SOUL.
    # Broken overrides must not silently fall back to broader default tools.
    if not directory.exists():
        directory = DEFAULT_SUBAGENTS_ROOT / name.lower()
    return _load_subagent_directory(name, directory)


def _load_subagent_directory(name: str, directory: Path) -> SubagentConfig:
    values = _load_config_values(
        name, path=directory / "config.yaml",
    )
    if "delegate" in (values.get("tools") or ()):
        raise SubagentConfigError("subagents cannot include delegate in tools")
    try:
        soul = (directory / "SOUL.md").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise SubagentConfigError(f"cannot read subagent SOUL: {directory / 'SOUL.md'}") from exc
    if not soul:
        raise SubagentConfigError(f"subagent SOUL must not be empty: {directory}")
    return SubagentConfig(**values, system_prompt=soul)


def discover_subagent_configs(*, data_root: Path, include_managed: bool = True) -> dict[str, SubagentConfig]:
    if include_managed:
        from raccoon.config.management import ManagedConfigStore
        from raccoon.config.management_sources import source_records

        managed = ManagedConfigStore()
        records = {record["id"].casefold(): record for record in source_records(
            managed, data_root=data_root, domains={"subagents"}, include_package_assets=False,
        )["subagents"]}
        records.update({record["id"].casefold(): record for record in managed.records("subagents")})
        deleted = managed.deleted_ids("subagents")
        configs = {}
        for key, record in sorted(records.items()):
            if key in deleted:
                continue
            directory = (managed.subagents_root / record["id"] if "_package_path" not in record
                         else Path(record["_package_path"]))
            try:
                configs[record["id"]] = _load_subagent_directory(record["id"], directory)
            except SubagentConfigError as exc:
                logger.warning("Skipping invalid subagent %s: %s", record["id"], exc)
        return configs

    names = {path.name for path in DEFAULT_SUBAGENTS_ROOT.iterdir() if path.is_dir()}
    local_root = data_root / "subagents"
    if local_root.exists():
        names.update(path.name for path in local_root.iterdir() if path.is_dir())
    configs = {}
    for name in sorted(names):
        try:
            configs[name] = load_subagent_config(name, data_root=data_root, include_managed=include_managed)
        except SubagentConfigError as exc:
            logger.warning("Skipping invalid subagent %s: %s", name, exc)
    return configs
