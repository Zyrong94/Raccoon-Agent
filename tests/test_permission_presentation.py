from raccoon.runtime import UserInputOption, UserInputRequest


def request():
    return UserInputRequest('internal-id', 'permission_confirmation', '允许执行权限操作 network_request 吗？',
        [UserInputOption('deny','Deny'),UserInputOption('allow_once','Allow once'),
         UserInputOption('allow_always_same_scope','Allow always')],
        {'action':'network_request','target':'https://www.tianqi.com/hangzhou/',
         'reason':'读取网页需要网络访问；HTML 网页会通过 Jina Reader 提取。',
         'scope_type':'host','scope_value':'www.tianqi.com',
         'pending_tool_call':{'id':'internal-call','name':'web_fetch','arguments':{'url':'https://www.tianqi.com/hangzhou/','max_chars':5000}}})


def test_permission_summary_is_human_readable_and_keeps_scope_and_arguments():
    from raccoon.application.delivery.permissions import permission_presentation
    display=permission_presentation(request())
    assert display['title']=='允许访问此网站？'
    assert display['target']=='www.tianqi.com/hangzhou/'
    assert display['description']=='读取网页内容'
    assert display['options'][0]['id']=='allow_once'
    assert any(o['label']=='始终允许此网站' for o in display['options'])
    assert 'https://www.tianqi.com/hangzhou/' in display['details']
    assert '5000' in display['details']
    assert 'internal-id' not in str(display) and 'internal-call' not in str(display)


def test_unknown_permission_and_long_command_remain_inspectable():
    from raccoon.application.delivery.permissions import permission_presentation
    pending=request();pending.payload={'action':'execute_shell','target':'python '+'x'*4000,'reason':'运行构建'}
    display=permission_presentation(pending)
    assert display['title']=='允许运行此命令？'
    assert len(display['target'])<250
    assert 'x'*4000 in display['details']
    pending.payload={}
    display=permission_presentation(pending)
    assert display['title']==pending.question


def test_review_only_uses_apply_label_without_granting_more_options():
    from raccoon.application.delivery.permissions import permission_presentation
    pending=request();pending.payload['request_type']='prewrite_review_confirmation'
    pending.options=pending.options[:2]
    display=permission_presentation(pending)
    assert next(o for o in display['options'] if o['id']=='allow_once')['label']=='应用修改'
    assert not any(o['id']=='allow_always_same_scope' for o in display['options'])
