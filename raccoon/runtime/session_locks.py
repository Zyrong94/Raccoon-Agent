"""Process-wide session exclusion shared by interactive and scheduled turns."""

from hashlib import sha256
from pathlib import Path
import portalocker


def session_lock(root: Path, session_id: str):
    directory = Path(root) / 'session-locks'
    directory.mkdir(parents=True, exist_ok=True)
    return portalocker.Lock(str(directory / (sha256(session_id.encode()).hexdigest() + '.lock')), timeout=0)
