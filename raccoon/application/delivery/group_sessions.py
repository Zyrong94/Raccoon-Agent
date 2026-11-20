"""Read QQ group history without creating or resuming a group agent."""

from pathlib import Path
import re

from raccoon.application.delivery.main_session import main_session_snapshot



def group_root(data_root: Path, key: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{64}',key):
        raise KeyError('group not found')
    root = data_root/'channels'/'groups'/key
    if root.resolve()!=data_root.resolve()/'channels'/'groups'/key:
        raise ValueError('Invalid group data directory')
    return root


def list_group_sessions(data_root: Path, *, bindings) -> dict:
    return {'groups':[{'id':row['group_key'],'title':row['title'] or '群聊 '+row['group_key'][:6]}
                      for row in bindings(data_root)]}


def group_session_snapshot(data_root: Path, key: str, *, bindings) -> dict:
    row = next((r for r in bindings(data_root) if r['group_key']==key),None)
    if row is None:
        raise KeyError('group not found')
    state = main_session_snapshot(group_root(data_root,key))
    if state['session']:
        state['session']['title'] = row['title'] or '群聊 '+key[:6]
    return state
