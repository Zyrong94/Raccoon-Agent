"""Bounded QQ message encoding; model text cannot manufacture controls."""

import html
import re


def reply_text(text: str) -> str:
    """Remove model-internal citation tokens unsupported by QQ's renderer."""
    return re.sub(r'\ue200cite\ue202[^\ue200\ue201\r\n]*(?:\ue201|(?=\r?\n|$))', '', text).strip()


def chunks(text: str, limit: int = 1800) -> list[str]:
    result, part, size = [], [], 0
    for char in text:
        width = len(char.encode('utf-8'))
        if size + width > limit:
            result.append(''.join(part)); part, size = [], 0
        part.append(char); size += width
    if part:
        result.append(''.join(part))
    return result


def literal(text: str) -> str:
    text = html.escape(str(text), quote=False)
    return re.sub(r'([\\`*_{}\[\]()#+!|>~\-])', r'\\\1', text)


def markdown_message(text: str, buttons: list[dict] | None = None) -> dict:
    body = {'version': 1, 'msg_type': 2, 'markdown': {'content': text}}
    if buttons:
        if len(buttons) > 25:
            raise ValueError('QQ keyboard exceeds 25 buttons')
        values = []
        for index, button in enumerate(buttons):
            action = {'type': 0 if 'url' in button else 2, 'permission': {'type': 2},
                      'data': button.get('url', button.get('command')), 'unsupport_tips': '请使用消息中的文字命令'}
            if action['type'] == 2:
                action['enter'] = True
            if button.get('confirm'):
                action['modal'] = {'content': button['confirm'][:40], 'confirm_text': '确认', 'cancel_text': '取消'}
            values.append({'id': str(index), 'render_data': {'label': button['label'][:10],
                'visited_label': button['label'][:10], 'style': 1}, 'action': action})
        body['keyboard'] = {'content': {'rows': [{'buttons': values[i:i+5]} for i in range(0, len(values), 5)]}}
    return body


PERMISSION_LABELS = {'allow_once': '仅本次允许', 'deny': '拒绝',
                     'allow_always_same_scope': '始终允许', 'apply': '应用变更'}
