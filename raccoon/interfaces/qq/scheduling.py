"""Explicit private-chat controls, after the QQ channel authenticates its owner."""

from datetime import datetime
from zoneinfo import ZoneInfo


async def schedule_command(channel, text, owner, message_id):
    if not (text == '/schedule' or text.startswith('/schedule ')):
        return False
    service = getattr(channel, 'scheduling', None)
    if service is None:
        channel._reply(owner, message_id, '定时任务宿主尚未启用，请更新并重启宿主。')
        return True
    store, origin = service.store, channel.source.main_session_id
    parts = text.split()
    try:
        action = parts[1] if len(parts) > 1 else 'list'
        if action == 'list':
            tasks = store.list(origin_session=origin)
            lines = ['定时任务（本对话）']
            for task in tasks[:30]:
                instant = datetime.fromtimestamp(task['next_at'], ZoneInfo(task['timezone'])).isoformat() if task['next_at'] else '无'
                lines.append(f"{task['title']} · {task['status']}\n{task['id']}\n下次：{instant}")
            lines.append('暂无任务。可直接说“每天9点整理社区早报”。' if not tasks else '/schedule show <任务ID> 查看详情与历史')
            channel._reply(owner, message_id, '\n\n'.join(lines))
            return True
        if action == 'answer':
            if len(parts) < 5:
                raise ValueError('用法：/schedule answer <执行ID> <本次随机码> <回答>')
            run = next((r for r in store.runs(limit=200) if r['id'] == parts[2]), None)
            if run is None or run['status'] != 'waiting':
                raise ValueError('没有该待确认执行')
            store.require_origin(run['task_id'], origin)
            answer = ' '.join(parts[4:])
            if run['pending']['kind'] == 'permission_confirmation' and answer != 'deny':
                state = channel.state
                receipt = state.get('schedule-details:' + run['id'] + ':' + run['pending_token'], [])
                if not receipt or not all(state.delivered(row_id) for row_id in receipt):
                    raise ValueError('请先 /schedule show <任务ID> 查看完整操作详情，待全部发送成功再批准')
            await service.answer(run['id'], token=parts[3], request_id=run['pending']['id'], answer=answer)
            channel._reply(owner, message_id, '已提交本次回答。')
            return True
        if len(parts) != 3:
            raise ValueError('用法：/schedule list，或 /schedule show|run|pause|resume|delete <任务ID>')
        identity = parts[2]
        task = store.require_origin(identity, origin)
        if action == 'show':
            lines = [task['title'], task['prompt'], f"{task['kind']} · {task['timezone']}"]
            for run in store.runs(identity, limit=5):
                lines.append(f"执行 {run['id']}：{run['status']}\n{run['error'] or '输出请查看对应会话'}")
                if run['status'] == 'waiting':
                    pending = run['pending']
                    lines.append(pending['question'])
                    presentation = pending.get('presentation', {})
                    if presentation.get('details'):
                        lines.append(presentation['details'])
                    options = pending.get('options', [])
                    lines.extend(f"{o['label']}：/schedule answer {run['id']} {run['pending_token']} {o['id']}" for o in options)
                    if not options:
                        lines.append(f"/schedule answer {run['id']} {run['pending_token']} <你的回答>")
            state = channel.state
            before = state.db.execute('SELECT COALESCE(MAX(id),0) FROM outbound').fetchone()[0]
            channel._reply(owner, message_id, '\n\n'.join(lines))
            receipts = [r[0] for r in state.db.execute('SELECT id FROM outbound WHERE id>?', (before,))]
            for run in store.runs(identity, limit=5):
                if run['status'] == 'waiting':
                    state.set('schedule-details:' + run['id'] + ':' + run['pending_token'], receipts)
        elif action == 'run':
            store.trigger(identity)
            channel._reply(owner, message_id, '已加入执行队列，原定周期保持不变。')
        elif action in ('pause', 'resume', 'delete'):
            task = store.change(identity, action, revision=task['revision'])
            channel._reply(owner, message_id, f"{task['title']}：{task['status']}")
        else:
            raise ValueError('未知操作；发送 /schedule 查看任务')
    except (ValueError, KeyError, TypeError) as exc:
        channel._reply(owner, message_id, str(exc))
    return True
