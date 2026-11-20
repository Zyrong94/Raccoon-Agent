"""Human-facing permission summaries, shared by QQ and the Web adapter."""

import json
from dataclasses import asdict
from urllib.parse import urlsplit

from raccoon.runtime import UserInputRequest


_TITLES = {
    'network_request': '允许访问此网站？', 'execute_shell': '允许运行此命令？',
    'read_path': '允许读取此文件？', 'write_path': '允许修改这些文件？',
    'delete_path': '允许删除这些文件？', 'mcp_tool': '允许使用此工具？',
    'git_operation': '允许执行此 Git 操作？', 'read_env': '允许读取此环境变量？',
}
_SCOPES = {'host': '此网站', 'mcp_tool': '此工具', 'exact_path': '此路径',
           'path_tree': '此目录', 'env_key': '此变量', 'command_prefix': '此命令范围'}


def permission_presentation(pending: UserInputRequest) -> dict:
    payload = pending.payload
    action = payload.get('action', '')
    target = str(payload.get('target') or '')
    call = payload.get('pending_tool_call') or {}
    arguments = call.get('arguments') or {}
    title = _TITLES.get(action, pending.question)
    description = str(payload.get('reason') or '')
    display_target = target
    if action == 'network_request' and call.get('name') == 'web_fetch':
        description = '读取网页内容'
    if action == 'network_request':
        try:
            parsed = urlsplit(target)
            if parsed.hostname:
                display_target = parsed.netloc + parsed.path + ('?' + parsed.query if parsed.query else '')
        except ValueError:
            pass
    details = []
    if target:
        details.append('完整目标：' + target)
    if payload.get('reason'):
        details.append('用途：' + str(payload['reason']))
    if payload.get('scope_value'):
        details.append('长期授权范围：' + str(payload['scope_value']))
    if call.get('name'):
        details.append('工具：' + str(call['name']))
    if isinstance(arguments, dict):
        for name, value in arguments.items():
            details.append(str(name) + '：' + (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)))
    elif arguments:
        details.append('参数：' + str(arguments))
    if not details:
        details.append(pending.question)
    review_only = payload.get('request_type') == 'prewrite_review_confirmation'
    labels = {'deny': '拒绝', 'allow_once': '应用修改' if review_only else '仅本次允许',
              'allow_always_same_scope': '始终允许' + _SCOPES.get(payload.get('scope_type'), '此范围')}
    options = [{**asdict(option), 'label': labels.get(option.id, option.label)} for option in pending.options]
    order = {'allow_once': 0, 'allow_always_same_scope': 1, 'deny': 2}
    options.sort(key=lambda option: order.get(option['id'], 1))
    return {'title': title, 'target': display_target if len(display_target) <= 200 else display_target[:197] + '…',
            'description': description if len(description)<=180 else description[:177]+'…',
            'details': '\n\n'.join(details), 'options': options}


def pending_snapshot(pending: UserInputRequest | None) -> dict | None:
    if pending is None:
        return None
    value = asdict(pending)
    if pending.kind == 'permission_confirmation':
        value['presentation'] = permission_presentation(pending)
    return value
