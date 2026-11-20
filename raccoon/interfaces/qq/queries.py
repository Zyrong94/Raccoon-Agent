"""Read-only QQ binding queries; never initialize a channel database."""
import json
import sqlite3
from pathlib import Path
from contextlib import closing

def group_bindings(data_root: Path) -> list[dict]:
    db = data_root/'channels'/'qq.sqlite3'
    if not db.is_file():
        return []
    with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as con:
        con.row_factory = sqlite3.Row
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qq_groups'").fetchone():
            return []
        owner = con.execute("SELECT value FROM metadata WHERE key='owner'").fetchone()
        if not owner:
            return []
        return [dict(row) for row in con.execute('SELECT group_key,title FROM qq_groups WHERE private_owner=? ORDER BY group_key',
                                                (json.loads(owner[0]),))]
