"""Session event helpers for skill audit records."""

from __future__ import annotations

from raccoon.modules.sessions.models import SessionData
from raccoon.modules.sessions.store import SessionStore
from raccoon.modules.skills.models import LoadedSkill, SkillDefinition


def append_skill_selected(
    store: SessionStore, data: SessionData, skill: SkillDefinition, *, reason: str, confidence: str
) -> None:
    store.append_event(
        data,
        "skill_selected",
        {
            "skill_name": skill.name,
            "skill_scope": skill.scope,
            "skill_source": skill.source.value,
            "skill_root": skill.root,
            "skill_path": skill.path,
            "reason": reason,
            "confidence": confidence,
        },
    )


def append_skill_loaded(store: SessionStore, data: SessionData, loaded: LoadedSkill) -> None:
    skill = loaded.skill
    store.append_event(
        data,
        "skill_loaded",
        {
            "skill_name": skill.name,
            "skill_scope": skill.scope,
            "skill_source": skill.source.value,
            "skill_root": skill.root,
            "skill_path": skill.path,
            "content_hash": loaded.content_hash,
            "bytes": loaded.bytes,
            "required_files": list(loaded.required_files),
        },
    )
