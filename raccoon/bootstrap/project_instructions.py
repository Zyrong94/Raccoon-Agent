"""Read project instructions at the assembly boundary."""
from pathlib import Path

def read_agents_md(project_root: str | Path) -> str:
    """读取项目根目录的 AGENTS.md。

    第一版只读取项目根目录这一份规则，避免在 context 装配阶段引入目录继承、覆盖和
    多文件合并语义。后续如果支持子目录 AGENTS.md，应在这里扩展，而不是散落在 agent
    loop 或 Textual UI 中。
    """

    agents_path = Path(project_root) / "AGENTS.md"
    if not agents_path.exists():
        return ""
    return agents_path.read_text(encoding="utf-8")


def read_default_soul(data_root: str | Path) -> str | None:
    """Read optional shared main-session instructions from the current data root."""
    path = Path(data_root) / "SOUL.md"
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as error:
        raise ValueError(f"cannot read default SOUL {path}: {error}") from error
    return content.strip() or None
