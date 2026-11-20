"""Discover skills from the current project and two user directories."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

import yaml

from raccoon.modules.skills.models import SkillCatalog, SkillDefinition, SkillSource


def raccoon_skills_root(env: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if env is None else env
    return Path(environment.get("HOME", str(Path.home()))) / ".raccoon" / "skills"


def discover_project_skills(project_root: str | Path) -> SkillCatalog:
    root = Path(project_root)
    return SkillCatalog(skills=_discover_agent_skills(
        root / ".agents" / "skills", root=root, source=SkillSource.PROJECT_AGENT_SKILL,
    ))


def discover_all_skills(project_root: str | Path, *, env: Mapping[str, str] | None = None) -> SkillCatalog:
    skills = discover_project_skills(project_root).skills
    skills.extend(_discover_global_skills(env))
    return SkillCatalog(skills=skills).resolved()


def _global_skill_roots(env: Mapping[str, str]) -> list[Path]:
    home = Path(env.get("HOME", str(Path.home())))
    return [raccoon_skills_root(env), home / ".agents" / "skills"]


def _global_skills_disabled(env: Mapping[str, str]) -> bool:
    return env.get("RACCOON_DISABLE_GLOBAL_SKILLS", "").strip().lower() in {"1", "true", "yes", "on"}


def _discover_global_skills(env: Mapping[str, str] | None = None) -> list[SkillDefinition]:
    environment = os.environ if env is None else env
    if _global_skills_disabled(environment):
        return []
    skills = []
    for root in _global_skill_roots(environment):
        source = SkillSource.RACCOON_AGENT_SKILL if root == raccoon_skills_root(environment) else SkillSource.GLOBAL_AGENT_SKILL
        skills.extend(_discover_agent_skills(root, root=root, source=source))
    return skills


def _discover_agent_skills(directory: Path, *, root: Path, source: SkillSource) -> list[SkillDefinition]:
    skills = []
    for path in sorted(directory.glob("*/SKILL.md")):
        # Temporary and backup packages must never become available skills.
        if path.parent.name.startswith("."):
            continue
        content = _read_text(path)
        metadata = _frontmatter_metadata(content)
        skills.append(SkillDefinition(
            name=_metadata_text(metadata, "name") or path.parent.name,
            path=_relative_path(path, root), source=source, root=str(root),
            description=_metadata_text(metadata, "description") or _first_heading(content) or _first_nonempty_line(content),
            triggers=_parse_triggers(metadata.get("triggers", "")),
        ))
    return skills


def _frontmatter_metadata(content: str) -> dict[str, object]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        return {}
    try:
        metadata = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError:
        return {}
    return dict(metadata) if isinstance(metadata, dict) else {}


def _parse_triggers(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list):
        return tuple(str(part).strip() for part in value if str(part).strip())
    if value is None:
        return ()
    return ()


def _metadata_text(metadata: Mapping[str, object], key: str) -> str:
    value = metadata.get(key)
    return value.strip() if isinstance(value, str) else ""


def _first_heading(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _first_nonempty_line(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")
