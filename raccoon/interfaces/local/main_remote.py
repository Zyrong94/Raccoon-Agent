"""QQ text connector port when another local process owns personal inference."""

from raccoon.runtime.user_input import UserInputOption, UserInputRequest


class RemoteMainRuntime:
    def __init__(self, bridge):
        self.bridge = bridge
        self.state = None

    async def refresh(self):
        self.state = await self.bridge.call('snapshot')

    @property
    def pending_inputs(self):
        raw = self.state.get('pending') if self.state else None
        if raw is None:
            return {}
        request = UserInputRequest(id=raw['id'], kind=raw['kind'], question=raw['question'],
            options=[UserInputOption(**option) for option in raw['options']], payload=raw['payload'])
        return {self.state['session_id']: request}

    def accepts_images(self, session_id):
        return bool(self.state and self.state.get('input_capabilities', {}).get('images'))

    def identity(self):
        return {key: self.state[key] for key in ('session_id', 'generation')}

    async def submit_event(self, event):
        self.state = await self.bridge.call('event', {**self.identity(), 'event': event.to_dict()})

    async def answer_run(self, session_id, request_id, answer):
        self.state = await self.bridge.call('input', {**self.identity(), 'request_id': request_id, 'answer': answer})
