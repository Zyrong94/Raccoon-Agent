"""Personal/group main-session pointer; execution queues are process-local."""
import json
from pathlib import Path
from uuid import uuid4
from raccoon.app.conversations import ConversationService

class MainSessionConflict(RuntimeError):
    """Local operational notice; contains no remote response data."""

def prepare_main_session(data_root: Path, *, new_session: bool = False, private_only: bool = True, abandon_pending=None) -> tuple[str, ConversationService]:
    """Called under main.lock; interrupted work must never silently run again."""
    channel_root = data_root / "channels"
    channel_root.mkdir(parents=True, exist_ok=True)
    main_path = channel_root / "main_session.json"
    if new_session and abandon_pending is not None:
        abandon_pending(data_root, private_only=private_only)
    if new_session or not main_path.exists():
        temporary = main_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"session_id": str(uuid4())}), encoding="utf-8")
        temporary.replace(main_path)
    session_id = json.loads(main_path.read_text(encoding="utf-8"))["session_id"]
    from raccoon.modules.sessions.catalog import is_safe_session_id
    if not isinstance(session_id, str) or not is_safe_session_id(session_id):
        raise ValueError('invalid main session pointer')
    return session_id, ConversationService()
