from pathlib import Path

import pytest

from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.skills.loader import SkillLoadError, SkillLoader
from raccoon.modules.skills.models import SkillCatalog, SkillDefinition, SkillSource
from raccoon.modules.tools.load_skill import create_load_skill_tool


def test_skill_loader_reads_complete_skill_and_required_files(tmp_path: Path) -> None:
    skill_path = tmp_path / "skills" / "brief.md"
    skill_path.parent.mkdir()
    skill_path.write_text(
        "# Brief\n\n开始前必须读取：\n\n1. `AGENTS.md`\n2. `docs/evidence-policy.md`\n",
        encoding="utf-8",
    )
    skill = SkillDefinition(
        name="brief",
        path="skills/brief.md",
        source=SkillSource.PROJECT_MARKDOWN,
        root=str(tmp_path),
        description="简报",
    )

    loaded = SkillLoader().load(skill)

    assert loaded.skill == skill
    assert loaded.content == skill_path.read_text(encoding="utf-8")
    assert loaded.bytes == len(loaded.content.encode("utf-8"))
    assert loaded.content_hash
    assert loaded.required_files == ["AGENTS.md", "docs/evidence-policy.md"]


def test_skill_loader_rejects_missing_or_escaping_path(tmp_path: Path) -> None:
    loader = SkillLoader()
    missing = SkillDefinition(
        name="missing",
        path="skills/missing.md",
        source=SkillSource.PROJECT_MARKDOWN,
        root=str(tmp_path),
    )
    escaping = SkillDefinition(
        name="escape",
        path="../secret.md",
        source=SkillSource.PROJECT_MARKDOWN,
        root=str(tmp_path),
    )

    with pytest.raises(SkillLoadError):
        loader.load(missing)
    with pytest.raises(SkillLoadError):
        loader.load(escaping)


def test_load_skill_tool_returns_full_content_and_writes_audit_events(tmp_path: Path) -> None:
    skill_path = tmp_path / "review" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text("# Review\n\nCheck correctness.\n", encoding="utf-8")
    skill = SkillDefinition(
        name="review",
        path="review/SKILL.md",
        source=SkillSource.PROJECT_AGENT_SKILL,
        root=str(tmp_path),
        description="Review code.",
    )
    store = SessionStore(tmp_path / "events")
    data = store.create()
    tool = create_load_skill_tool(SkillCatalog(skills=[skill]), store, data)

    result = tool.executor(name="review", args="check app.py")

    assert result.ok is True
    assert "Loaded skill: review" in result.content
    assert "Arguments: check app.py" in result.content
    assert "# Review\n\nCheck correctness." in result.content
    events = store.list_events(data.session_id)[1:]
    assert [event.type for event in events] == ["skill_selected", "skill_loaded"]
    assert events[0].payload["reason"] == "model_tool_call"
    assert events[1].payload["content_hash"]


def test_load_skill_tool_rejects_unknown_or_missing_skill_without_audit_events(tmp_path: Path) -> None:
    skill_path = tmp_path / "review" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text("# Review\n", encoding="utf-8")
    skill = SkillDefinition(
        name="review",
        path="review/SKILL.md",
        source=SkillSource.PROJECT_AGENT_SKILL,
        root=str(tmp_path),
    )
    store = SessionStore(tmp_path / "events")
    data = store.create()
    tool = create_load_skill_tool(SkillCatalog(skills=[skill]), store, data)

    unknown = tool.executor(name="missing")
    skill_path.unlink()
    missing = tool.executor(name="review")

    assert unknown.ok is False
    assert "Available skills: review" in unknown.content
    assert missing.ok is False
    assert "Unable to load skill: review" in missing.content
    assert store.list_events(data.session_id)[1:] == []
