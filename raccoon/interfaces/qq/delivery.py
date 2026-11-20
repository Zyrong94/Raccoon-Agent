"""Durable QQ response delivery when another connector owns the host."""
from raccoon.interfaces.qq.channel import QQChannel
from raccoon.interfaces.qq.state import QQState
from raccoon.modules.events import EventSourceAdapter

def deliver_host_response(data_root, session_id, response, route, runtime):
    state = QQState(data_root / "channels" / "qq.sqlite3")
    try:
        channel = QQChannel(state, runtime, EventSourceAdapter(main_session_id=session_id), pairing_code='', pairing_expires=0)
        channel.on_response(session_id, response, route=route)
    finally:
        state.close()
