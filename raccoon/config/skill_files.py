"""File-backed skill editing. Scope selects a directory, never a registry entry."""

import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

import portalocker
import yaml

from raccoon.modules.skills.discovery import (
    _discover_global_skills, _frontmatter_metadata, discover_project_skills,
    raccoon_skills_root,
)
from raccoon.modules.skills.models import SkillCatalog, SkillSource


def skill_records(project_path=None, *, env=None, include_package_assets=True):
    from raccoon.config.management_sources import source_record

    # The management page can edit global files even when a runtime disables them.
    environment = dict(os.environ if env is None else env)
    environment.pop("RACCOON_DISABLE_GLOBAL_SKILLS", None)
    skills = _discover_global_skills(environment)
    if project_path is not None:
        skills.extend(discover_project_skills(project_path).skills)
    effective = {skill.name.casefold(): skill for skill in SkillCatalog(skills=skills).resolved().skills}
    records = []
    for skill in skills:
        path = Path(skill.root) / skill.path
        scope = "project" if skill.scope == "project" else "global"
        if skill.source == SkillSource.GLOBAL_AGENT_SKILL:
            scope = "shared"
        record = source_record(
            skill.name, {"name": skill.name, "description": skill.description},
            path.read_text(encoding="utf-8"), package=path.parent, path=path,
            include_package_assets=include_package_assets,
        )
        record.update(scope=scope, source=scope, read_only=scope == "shared",
                      overridden=effective[skill.name.casefold()] != skill)
        records.append(record)
    return records


def writable_skill_root(scope, project_path=None):
    if scope == "global":
        return raccoon_skills_root()
    if scope == "project" and project_path is not None:
        return Path(project_path) / ".agents" / "skills"
    raise ValueError("技能只能保存到 Raccoon 全局目录或当前项目；共享目录只读")


def save_skill(store, config, content, *, identifier=None, revision=None, files=None,
               scope="global", project_path=None):
    from raccoon.config.management import ConfigurationConflict, _safe

    name = store.validate("skills", config, content)
    if identifier is not None and identifier != name:
        raise ValueError("已有技能名称不可修改")
    root = writable_skill_root(scope, project_path)
    _safe(root)
    root.mkdir(parents=True, exist_ok=True)
    _safe(root / ".lock")
    with portalocker.Lock(str(root / ".lock"), timeout=5):
        records = skill_records(project_path)
        previous = next((record for record in records
                         if record["scope"] == scope and record["id"].casefold() == name.casefold()), None)
        if identifier is not None:
            if previous is None or previous["id"] != identifier or previous["revision"] != revision:
                raise ConfigurationConflict("技能已修改或不存在，请刷新后重试")
        elif previous is not None:
            raise ConfigurationConflict("此位置已有同名技能，请编辑已有技能或更换名称")
        folder = Path(previous["_package_path"]) if previous else root / name
        _safe(folder)
        if folder.parent.resolve() != root.resolve():
            raise ValueError("技能路径超出所选目录")
        if previous is None and folder.exists():
            raise ConfigurationConflict("目标目录已存在，请检查目录内容")
        metadata = _frontmatter_metadata(content)
        body = content
        if content.startswith("---"):
            lines = content.splitlines()
            end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
            body = "\n".join(lines[end + 1:])
        metadata.update(name=name, description=config.get("description", ""))
        content = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n" + body
        # Stage the whole package so a failed edit cannot discard scripts or references.
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
        backup = root / (".previous-" + uuid4().hex)
        try:
            store._package("skills", name, config, content, files or {},
                           previous=folder if previous else None, folder=staging)
            if previous:
                folder.rename(backup)
            try:
                staging.rename(folder)
            except BaseException:
                if backup.exists():
                    backup.rename(folder)
                raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        if backup.exists():
            shutil.rmtree(backup)
        return next(record for record in skill_records(project_path)
                    if record["scope"] == scope and record["id"] == name)


def delete_skill(identifier, revision, *, scope="global", project_path=None):
    from raccoon.config.management import ConfigurationConflict, _safe

    root = writable_skill_root(scope, project_path)
    _safe(root)
    root.mkdir(parents=True, exist_ok=True)
    _safe(root / ".lock")
    with portalocker.Lock(str(root / ".lock"), timeout=5):
        previous = next((record for record in skill_records(project_path)
                         if record["scope"] == scope and record["id"] == identifier), None)
        if previous is None or previous["revision"] != revision:
            raise ConfigurationConflict("技能已修改或不存在，请刷新后重试")
        folder = Path(previous["_package_path"])
        _safe(folder)
        if folder.parent.resolve() != root.resolve():
            raise ValueError("技能路径超出所选目录")
        backup = root / (".deleted-" + uuid4().hex)
        folder.rename(backup)
        try:
            shutil.rmtree(backup)
        except OSError:
            # Keep any remaining package visible for recovery instead of hiding it.
            backup.rename(folder)
            raise
