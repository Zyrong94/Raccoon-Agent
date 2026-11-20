// @vitest-environment jsdom
import {cleanup,render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {CollaborationWorkspace} from './collaboration-workspace';
import type {SubagentRequest,SubagentSummary,SubagentDetail} from '@/lib/subagents';
const collaborationChildren:SubagentSummary[]=[
 {subagent_session_id:'coder',agent_name:'实现代理',task:'为项目聊天接入持续协作与子会话控制',state:'running',summary:'',latest_activity:'正在验证消息投递的幂等与会话隔离。',queued_count:1,queued_messages:[{event_id:'review-1',text:'请补上停止后保留队列的测试。',mode:'follow_up',delivery:'queued'}],created_at:null,updated_at:null,completed_at:null,restricted:false},
 {subagent_session_id:'reviewer',agent_name:'审查代理',task:'检查权限边界和停止后的恢复语义',state:'idle',summary:'审查发现两处需要修正：旧队列恢复顺序、宿主切换时的过期请求。意见已发回实现代理。',latest_activity:'只读审查已结束。',queued_count:0,queued_messages:[],created_at:null,updated_at:null,completed_at:null,restricted:false},
 {subagent_session_id:'restricted',agent_name:'验证代理',task:'验证隔离工作区中的执行行为',state:'failed',summary:'命令需要人工授权，子代理权限边界已拒绝此操作。',latest_activity:'permission_denied',queued_count:0,queued_messages:[],created_at:null,updated_at:null,completed_at:null,restricted:true},
];
const collaborationDetail=(child:SubagentSummary):SubagentDetail=>({...child,parent_session_id:'preview',messages:[
 {id:'instruction',role:'user',content:child.task},
 {id:'progress',role:'assistant',content:'父聊天仍然保留在左侧。我正在核对 **消息队列** 与会话身份，随后补上对应验证。'},
 {id:'call',role:'assistant',content:'读取当前实现。',calls:[{id:'read-1',name:'view',arguments:{path:'raccoon/subagents/manager.py'}}]},
 {id:'result',role:'tool',name:'view',tool_call_id:'read-1',ok:!child.restricted,content:child.restricted?'permission_denied: 此操作需要人工授权，子代理不能请求批准。':'已读取队列处理与停止逻辑。'},
 {id:'answer',role:'assistant',content:child.summary||'消息将按 FIFO 顺序交付。停止当前任务会保留尚未处理的队列。\n\n```python\nqueued = pending_messages[subagent_session_id]\n```'},
]});

vi.mock('@/components/chat/tool-trace',()=>({
 formatToolValue:(value:unknown)=>typeof value==='string'?value:JSON.stringify(value),
 ToolStatusCard:({signature,message}:{signature:string;message:string})=><div data-testid="tool-status" data-signature={signature}>{signature}: {message}</div>,
 ToolTraceCard:({signature,description,result}:{signature:string;description?:string;result:string})=><div data-testid="tool-trace" data-signature={signature}><span>{signature}</span><span>{description}</span><span>{result}</span></div>,
}));
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
function setup(){
 let children=structuredClone(collaborationChildren);let fail=false;
 const request=vi.fn(async(_scope:unknown,path:string,body?:Record<string,unknown>)=>{
  const id=path.split('/')[1];
  if(body){if(fail)throw new Error('网络结果未知');if(path.endsWith('/stop'))children=children.map(child=>child.subagent_session_id===id?{...child,state:'stopped'}:child);return {delivery:'queued'};}
  return path?collaborationDetail(children.find(child=>child.subagent_session_id===id)!):{subagents:children};
 }) as unknown as SubagentRequest;
 return {request,fail:()=>{fail=true;},setChildren:(value:SubagentSummary[])=>{children=value;}};
}
it('keeps completed and restricted task cards available and defaults sends to follow_up',async()=>{
 const artifact={id:'html',name:'chart.html',suffix:'.html',size:100,content_url:'/api/projects/0123456789abcdef/sessions/history-b/artifacts/html/content',canvas_url:'/api/projects/0123456789abcdef/sessions/history-b/artifacts/html/canvas/chart.html',download_url:'/api/projects/0123456789abcdef/sessions/history-b/artifacts/html/download',mime:'text/html'};
 const fake=setup(),user=userEvent.setup();render(<CollaborationWorkspace scope={{project_id:'p',session_id:'s'}} request={fake.request} artifacts={[artifact]} canvas={{artifact_id:'html',title:'交互图表',request_id:'open-1',revision:'r1'}}><p>父聊天保留</p></CollaborationWorkspace>);
 await user.click(screen.getByRole('button',{name:'展开会话内容'}));
 const completed=await screen.findByRole('button',{name:/查看子会话：审查代理/});expect(screen.getByText('失败 · 权限受限')).toBeTruthy();
 const content=screen.getByRole('complementary',{name:'会话内容'});
 expect(within(content).getAllByRole('heading').map(heading=>heading.textContent)).toEqual(['输出文件','子智能体']);
 expect(within(content).getByRole('button',{name:'打开文件：chart.html'})).toBeTruthy();
 expect(within(content).getByRole('button',{name:/查看子会话：审查代理/})).toBe(completed);
 expect(screen.queryByRole('button',{name:/会话文件/})).toBeNull();expect(content.querySelector('[data-slot="artifact-card"]')).toBeNull();expect(screen.queryByRole('heading',{name:'协作任务'})).toBeNull();
 expect(screen.queryByText(collaborationChildren[1].summary)).toBeNull();await user.click(completed);
 expect(await screen.findByLabelText('发送给 审查代理')).toBeTruthy();expect(screen.getByText('父聊天保留')).toBeTruthy();
 expect(screen.queryByRole('heading',{name:'任务摘要'})).toBeNull();
 const traces=screen.getAllByTestId('tool-trace');
 expect(traces).toHaveLength(1);
 expect(traces[0].dataset.signature).toBe('读取文件');
 expect(traces[0].textContent).toContain('raccoon/subagents/manager.py');
 expect(traces[0].textContent).toContain('已读取队列处理与停止逻辑。');
 expect(screen.queryByText('工具调用')).toBeNull();
 expect(screen.queryByText('执行结果')).toBeNull();
 expect(screen.getByLabelText('投递方式').textContent).toContain('排队补充');
 await user.type(screen.getByLabelText('发送给 审查代理'),'请继续核对');await user.click(screen.getByRole('button',{name:'发送'}));
 await waitFor(()=>expect(fake.request).toHaveBeenCalledWith({project_id:'p',session_id:'s'},'/reviewer/messages',expect.objectContaining({message:'请继续核对',mode:'follow_up',event_id:expect.any(String)}),expect.any(AbortSignal)));
});

it('keeps uncertain drafts and event IDs, offers steer and independently stops without dropping the queue',async()=>{
 const fake=setup(),user=userEvent.setup();render(<CollaborationWorkspace scope={{session_id:'main',generation:'g'}} request={fake.request}><p>个人父聊天</p></CollaborationWorkspace>);
 await user.click(await screen.findByRole('button',{name:/查看子会话：实现代理/}));await screen.findByLabelText('发送给 实现代理');
 await user.click(screen.getByRole('button',{name:'停止子代理'}));await screen.findByText(/已停止。发送新消息后/);expect(screen.getByText('1 条待处理消息')).toBeTruthy();
 await user.click(screen.getByLabelText('投递方式'));await user.click(await screen.findByRole('option',{name:'调整方向'}));await user.type(screen.getByLabelText('发送给 实现代理'),'优先修复');fake.fail();
 await user.click(screen.getByRole('button',{name:'发送'}));await screen.findByText(/草稿已保留/);expect((screen.getByLabelText('发送给 实现代理') as HTMLTextAreaElement).value).toBe('优先修复');
 await user.click(screen.getByRole('button',{name:'发送'}));
 await waitFor(()=>expect(vi.mocked(fake.request).mock.calls.filter(call=>call[1].endsWith('/messages'))).toHaveLength(2));
 const sends=vi.mocked(fake.request).mock.calls.filter(call=>call[1].endsWith('/messages'));expect(sends[0][2]).toEqual(sends[1][2]);expect(sends[0][2]?.mode).toBe('steer');
});

it('preserves child drafts and file view across panel switches',async()=>{
 const file={id:'notes',name:'notes.txt',suffix:'.txt',size:100,content_url:'/api/projects/0123456789abcdef/sessions/history-b/artifacts/notes/content',download_url:'/api/projects/0123456789abcdef/sessions/history-b/artifacts/notes/download',mime:'text/plain'};
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,text:async()=> 'saved file contents'}));
 const fake=setup(),user=userEvent.setup();render(<CollaborationWorkspace scope={{project_id:'p',session_id:'s'}} request={fake.request} artifacts={[file]}><p>父聊天</p></CollaborationWorkspace>);
 await user.click(await screen.findByRole('button',{name:/查看子会话：实现代理/}));await user.type(await screen.findByLabelText('发送给 实现代理'),'尚未发送');
 await user.click(screen.getByRole('button',{name:'展开会话内容'}));
 await user.click(screen.getByRole('button',{name:'打开文件：notes.txt'}));expect(await screen.findByText('saved file contents')).toBeTruthy();
 await user.click(screen.getByRole('button',{name:'展开会话内容'}));await user.click(screen.getByRole('button',{name:/查看子会话：实现代理/}));expect((screen.getByLabelText('发送给 实现代理') as HTMLTextAreaElement).value).toBe('尚未发送');
 await user.click(screen.getByRole('button',{name:'展开会话内容'}));await user.click(screen.getByRole('button',{name:'打开文件：notes.txt'}));expect(screen.getByRole('link',{name:'下载文件'}).getAttribute('href')).toBe(file.download_url);
 await user.click(screen.getByRole('button',{name:'展开会话内容'}));await user.click(screen.getByRole('button',{name:/查看子会话：审查代理/}));await screen.findByLabelText('发送给 审查代理');await user.click(screen.getByRole('button',{name:'展开会话内容'}));await user.click(screen.getByRole('button',{name:/查看子会话：实现代理/}));expect((screen.getByLabelText('发送给 实现代理') as HTMLTextAreaElement).value).toBe('尚未发送');
});

it('renders a running child tool with the parent status row and no separate result row',async()=>{
 const summary:SubagentSummary={...collaborationChildren[0],subagent_session_id:'live',agent_name:'实现代理',task:'检查运行中的工具',state:'running',queued_count:0,queued_messages:[]};
 const detail:SubagentDetail={...summary,parent_session_id:'p',messages:[
  {id:'step',role:'assistant',content:'开始检查',calls:[{id:'tree-1',name:'tree',arguments:{path:'raccoon'}}]},
 ]};
 const request=vi.fn(async(_scope:unknown,path:string)=>path?detail:{subagents:[summary]}) as unknown as SubagentRequest;
 const user=userEvent.setup();
 render(<CollaborationWorkspace scope={{project_id:'p',session_id:'s'}} request={request}><p>父聊天</p></CollaborationWorkspace>);
 await user.click(await screen.findByRole('button',{name:/查看子会话：实现代理/}));
 const status=await screen.findByTestId('tool-status');
 expect(status.dataset.signature).toBe('查看目录树中');
 expect(status.textContent).toContain('raccoon');
 expect(screen.queryAllByTestId('tool-trace')).toHaveLength(0);
});

it('groups multiple child tools with the parent trace labels and pairs each result once',async()=>{
 const summary:SubagentSummary={...collaborationChildren[1],subagent_session_id:'grouped',task:'检查两个文件',state:'idle',summary:'',queued_count:0,queued_messages:[]};
 const detail:SubagentDetail={...summary,parent_session_id:'p',messages:[
  {id:'step',role:'assistant',content:'检查目录和内容',calls:[
   {id:'read-1',name:'view',arguments:{path:'raccoon/agent/loop.py'}},
   {id:'grep-1',name:'grep',arguments:{pattern:'subagent'}},
  ]},
  {id:'r1',role:'tool',name:'view',tool_call_id:'read-1',ok:true,content:'文件内容'},
  {id:'r2',role:'tool',name:'grep',tool_call_id:'grep-1',ok:false,content:'没有匹配'},
 ]};
 const request=vi.fn(async(_scope:unknown,path:string)=>path?detail:{subagents:[summary]}) as unknown as SubagentRequest;
 const user=userEvent.setup();
 render(<CollaborationWorkspace scope={{project_id:'p',session_id:'s'}} request={request}><p>父聊天</p></CollaborationWorkspace>);
 await user.click(await screen.findByRole('button',{name:/查看子会话：审查代理/}));
 await user.click(await screen.findByRole('button',{name:/ran 2 tools · 1 failed/}));
 const traces=await screen.findAllByTestId('tool-trace');
 expect(traces).toHaveLength(2);
 expect(traces.map(trace=>trace.dataset.signature)).toEqual(['读取文件','搜索内容']);
 expect(traces[0].textContent).toContain('raccoon/agent/loop.py');
 expect(traces[1].textContent).toContain('没有匹配');
});
