"""Abandon pending QQ delivery before explicitly replacing a main session."""
from raccoon.interfaces.qq.state import QQState
from raccoon.application.conversations.main_session import prepare_main_session as prepare_session, MainSessionConflict

QQSessionConflict = MainSessionConflict

def abandon_pending(data_root, *, private_only):
    state = QQState(data_root / "channels" / "qq.sqlite3")
    try:
        state.abandon_pending(private_only=private_only)
    finally:
        state.close()

def prepare_main_session(data_root, *, new_session=False, private_only=True):
    return prepare_session(data_root, new_session=new_session, private_only=private_only, abandon_pending=abandon_pending)
