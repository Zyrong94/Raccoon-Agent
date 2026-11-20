"""Web subscriptions and bounded event replay; no conversation execution."""
import asyncio
import uuid
from collections import deque


class WebEventStream:
    def __init__(self):
        self.event_seq = 0
        self.event_epoch = uuid.uuid4().hex
        self._events = deque(maxlen=2048)
        self.subscribers = set()

    def subscribe(self):
        queue = asyncio.Queue(maxsize=512)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
        self.subscribers.discard(queue)

    def close(self):
        self.subscribers.clear()
        self._events.clear()

    def publish(self, event):
        self.event_seq += 1
        event = {**event, 'seq': self.event_seq, 'epoch': self.event_epoch}
        if event.get('kind') == 'snapshot':
            event['state'] = {**event['state'], 'event_seq': self.event_seq,
                              'event_epoch': self.event_epoch}
        self._events.append(event)
        for queue in tuple(self.subscribers):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({'kind': 'resync', 'seq': self.event_seq,
                                  'epoch': self.event_epoch, 'reset_cursor': True})
            else:
                queue.put_nowait(event)
        return event

    def replay_after(self, cursor):
        if cursor < 0 or cursor > self.event_seq or self._events and cursor < self._events[0]['seq'] - 1:
            return None
        return [event for event in self._events if event['seq'] > cursor]


