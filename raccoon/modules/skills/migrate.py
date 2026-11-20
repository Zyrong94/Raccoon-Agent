"""Explicit, non-destructive migration of the former managed skill library."""

import argparse
import json

from raccoon.config.management import ManagedConfigStore, _safe


def migrate_managed_skills(store, *, apply=False):
    registry = store._read()
    deleted = set(registry.get("_deleted", {}).get("skills", []))
    root = store.root / "skills"
    report = []
    for path in sorted(root.glob("*/SKILL.md")):
        if path.parent.name.startswith("."):
            continue
        _safe(path)
        from raccoon.modules.skills.discovery import _frontmatter_metadata

        metadata = _frontmatter_metadata(path.read_text(encoding="utf-8"))
        name = metadata.get("name") or path.parent.name
        if name.casefold() in deleted:
            report.append({"name": name, "status": "skipped_deleted"})
            continue
        existing = next((record for record in store.records("skills")
                         if record["scope"] == "global" and record["id"].casefold() == name.casefold()), None)
        if existing is not None or (store.skills_root / name).exists():
            report.append({"name": name, "status": "conflict", "source": str(path)})
            continue
        status = "ready"
        if apply:
            store.import_path("skills", path.parent)
            status = "copied"
        report.append({"name": name, "status": status, "source": str(path),
                       "destination": str(store.skills_root / name)})
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Copy skills; keep originals and skip conflicts")
    arguments = parser.parse_args()
    print(json.dumps(migrate_managed_skills(ManagedConfigStore(), apply=arguments.apply), ensure_ascii=False, indent=2))
