"""Test QQ transport without a live API or model."""

from raccoon.interfaces.qq.channel import QQChannel
from raccoon.interfaces.qq.state import QQState
from raccoon.modules.events import EventSourceAdapter


def packet(text="hello", *, sender="owner", message_id="m1", seq=2):
    return {
        "op": 0, "t": "C2C_MESSAGE_CREATE", "s": seq,
        "d": {"id": message_id, "author": {"user_openid": sender}, "content": text},
    }


def group_packet(text="hello", *, group="group-a", member="member-owner", message_id="g1",
                 seq=3, kind="GROUP_AT_MESSAGE_CREATE"):
    author = {"member_openid": member, "username": "队友" if member == "teammate" else "本人"}
    return {
        "op": 0, "t": kind, "s": seq,
        "d": {"id": message_id, "group_openid": group, "author": author, "content": text},
    }


class RecordingRuntime:
    def __init__(self):
        self.events = []
        self.pending_inputs = {}
        self.answers = []
        self.vision = True

    def submit_event(self, event):
        self.events.append(event)

    def answer_run(self, session_id, request_id, answer):
        self.answers.append((session_id, request_id, answer))
        self.pending_inputs.pop(session_id, None)

    def accepts_images(self, session_id):
        return self.vision


def channel(tmp_path, *, runtime=None, image_input=None):
    state = QQState(tmp_path / "qq.db")
    runtime = runtime if runtime is not None else RecordingRuntime()
    bot = QQChannel(
        state, runtime, EventSourceAdapter(main_session_id="main"),
        pairing_code="test-pairing", pairing_expires=9999999999, image_input=image_input,
    )
    return bot, state, runtime
