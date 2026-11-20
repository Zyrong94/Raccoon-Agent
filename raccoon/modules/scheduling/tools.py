"""Install a host-bound scheduling capability in an interactive runner."""

import json
from datetime import datetime, timezone as utc_timezone

from raccoon.runtime.model_types import ToolDefinition
from raccoon.modules.tools.types import Tool, make_error_result, make_text_result


def install_scheduling(bundle, store, *, channel, target=lambda: '', timezone=lambda: None):
    runner = bundle
    if getattr(runner, '_scheduling_installed', False):
        return
    runner._scheduling_installed = True
    omitted = object()

    def execute(*, action, task_id=None, revision=None, title=None, prompt=None, kind=None, spec=None,
                timezone=None, request_id=None, model=omitted, permission_mode=omitted, **unknown):
        try:
            if unknown:
                raise ValueError('来源、项目和收件人只能由宿主指定')
            if model is not omitted and model is not None and model not in bundle.model_catalog.models:
                raise ValueError('模型不在可选目录中')
            origin = runner.session_id
            if action == 'list':
                return make_text_result('schedule_task', json.dumps(store.list(origin_session=origin), ensure_ascii=False))
            if action == 'create':
                zone = timezone or default_timezone()
                if not zone:
                    raise ValueError('请先向用户确认时区，例如 Asia/Tokyo 或 Asia/Shanghai')
                task = store.create(title=title, prompt=prompt, kind=kind, spec=spec, timezone=zone,
                    project=str(bundle.project_path.resolve()), origin_session=origin, channel=channel, target=target(),
                    model=None if model is omitted else model,
                    permission_mode=None if permission_mode is omitted else permission_mode,
                    request_id=request_id, data_root=getattr(getattr(bundle, 'catalog', None), 'root', store.path.parent),
                    execution_owner='personal-main' if channel == 'qq' else None)
            else:
                task = store.require_origin(task_id, origin)
                if action == 'history':
                    return make_text_result('schedule_task', json.dumps(store.runs(task_id), ensure_ascii=False))
                if action == 'run':
                    return make_text_result('schedule_task', '已加入执行队列；立即运行不改变原定周期。', run=store.trigger(task_id))
                if action == 'update':
                    changes = {key: value for key, value in dict(title=title, prompt=prompt, kind=kind, spec=spec, timezone=timezone).items() if value is not None}
                    if model is not omitted:
                        changes['model'] = model
                    if permission_mode is not omitted:
                        changes['permission_mode'] = permission_mode
                    task = store.update(task_id, changes, revision=revision)
                else:
                    task = store.change(task_id, action, revision=revision)
            next_at = datetime.fromtimestamp(task['next_at'], utc_timezone.utc).isoformat() if task['next_at'] else '无'
            return make_text_result('schedule_task', f"{task['title']}：{task['status']}；时区 {task['timezone']}；下次执行（UTC）：{next_at}", task=task)
        except (ValueError, KeyError, TypeError) as error:
            return make_error_result('schedule_task', str(error))

    default_timezone = timezone
    definition = ToolDefinition(name='schedule_task', description=(
        '创建、查看、修改、暂停、恢复、删除定时任务，或立即试运行、查看历史。只有成功工具结果才代表已保存。'
        '时间支持 once(spec.at 为 ISO 时间)、interval(spec.seconds 为整数秒，至少60)、cron(spec.cron 为五字段表达式)。'
        '任务指令须自包含，最终结果自动回到来源聊天，不在指令中复制收件人或要求调用发消息工具。'
        '任务到期进入来源会话队列；未指定模型和权限时沿用会话设置。未确定时区应先询问用户。'
        '修改状态或内容先 list 取得最新 revision。'), parameters={
        'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': ['create','list','update','pause','resume','delete','run','history']},
            'task_id': {'type': 'string'}, 'revision': {'type': 'integer'}, 'title': {'type': 'string'},
            'prompt': {'type': 'string'}, 'kind': {'type': 'string', 'enum': ['once','interval','cron']},
            'spec': {'type': 'object', 'properties': {'at': {'type': 'string'}, 'seconds': {'type': 'integer'}, 'cron': {'type': 'string'}}},
            'timezone': {'type': 'string'},
            'model': {'type': ['string', 'null'], 'description': '可选模型标识；null 沿用会话模型'},
            'permission_mode': {'type': ['string', 'null'], 'enum': ['ask', 'auto', 'bypass', None], 'description': '可选权限模式；null 沿用会话权限'},
            'request_id': {'type': 'string', 'description': '可选创建去重标识；重试同次创建须保持不变'},
        }, 'required': ['action'], 'additionalProperties': False})
    tool = Tool(definition=definition, executor=execute)
    previous = runner.tools_provider
    runner.tools_provider = lambda: [*(previous() if previous else runner.tools or []), tool]
