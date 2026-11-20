// @vitest-environment jsdom
import {cleanup,render,screen,waitFor,within,fireEvent} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {ScheduledTasks} from './scheduled-tasks';
import {WorkbenchStore} from '@/lib/workbench/client';
import type {ScheduledTask,ScheduleRun} from '@/lib/scheduling';

afterEach(()=>{cleanup();vi.restoreAllMocks();});
const task:ScheduledTask={id:'t1',revision:3,title:'早间项目简报',prompt:'整理当前项目的更新',kind:'cron',spec:{cron:'0 9 * * *'},timezone:'Asia/Shanghai',model:null,permission_mode:null,status:'enabled',next_at:1793494800,channel:'web',origin_session:'s',latest:null};
function setup(tasks:ScheduledTask[]=[task],runs:ScheduleRun[]=[]){
 const store=new WorkbenchStore();
 const request=vi.spyOn(store,'scheduleRequest').mockImplementation(async(_project,path='',body)=>{
  if(body!==undefined)return {...task,...body as object};
  if(path.endsWith('/runs?limit=50'))return runs;
  return {tasks,host:{owner:true,error:'',message:'定时宿主运行中'}};
 });
 const onClose=vi.fn(),onOpenRun=vi.fn().mockResolvedValue(undefined);
 render(<ScheduledTasks store={store} projectId="p" sessionId="s" sourceLabel="测试项目" onClose={onClose} onOpenRun={onOpenRun}/>);
 return {request,onClose,onOpenRun,user:userEvent.setup()};
}

it('lists existing tasks and shows their actual cadence and history',async()=>{
 const {user}=setup();
 await user.click(await screen.findByRole('button',{name:/早间项目简报/}));
 expect(await screen.findByRole('heading',{name:'执行历史'})).toBeTruthy();
 expect(screen.getAllByText('每天 09:00').length).toBeGreaterThan(0);
 expect(screen.getByText('尚无执行记录')).toBeTruthy();
});

it('creates a task bound to the captured workspace/session and retains the request ID after failure',async()=>{
 const {user,request}=setup([]);
 await user.click(await screen.findByRole('button',{name:'新建任务'}));
 expect(screen.getByText(/新建来源：测试项目/)).toBeTruthy();
 await user.type(screen.getByLabelText('任务名称'),'项目检查');
 await user.type(screen.getByLabelText('执行指令'),'检查项目测试结果');
 let creation=0;
 request.mockImplementation(async(_project,path='',body)=>{
  if(body!==undefined){creation++;if(creation===1)throw new Error('暂时无法保存');return {...task,...body as object};}
  if(path.includes('/runs'))return [];
  return {tasks:[],host:{owner:true,error:'',message:'定时宿主运行中'}};
 });
 await user.click(screen.getByRole('button',{name:'保存任务'}));
 expect(await screen.findByText(/暂时无法保存/)).toBeTruthy();
 expect((screen.getByLabelText('任务名称') as HTMLInputElement).value).toBe('项目检查');
 const first=request.mock.calls.find(call=>call[2]!==undefined)![2] as Record<string,unknown>;
 expect(first).toMatchObject({project_id:'p',session_id:'s',title:'项目检查',kind:'cron'});
 await user.click(screen.getByRole('button',{name:'保存任务'}));
 await waitFor(()=>expect(request.mock.calls.filter(call=>call[2]!==undefined)).toHaveLength(2));
 const second=request.mock.calls.filter(call=>call[2]!==undefined)[1][2] as Record<string,unknown>;
 expect(second.request_id).toBe(first.request_id);
});

it('sends revision on pause and requires confirmation before deletion',async()=>{
 const {user,request}=setup();
 await user.click(await screen.findByRole('button',{name:/早间项目简报/}));
 await user.click(screen.getByRole('button',{name:'暂停'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('p','/t1/pause',{revision:3},undefined));
 await user.click(screen.getByRole('button',{name:'删除任务'}));
 expect(request.mock.calls.some(call=>call[1]==='/t1/delete')).toBe(false);
 const dialog=screen.getByRole('dialog');
 await user.click(within(dialog).getByRole('button',{name:'删除'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('p','/t1/delete',{revision:3},undefined));
});

it('keeps a conflicted edit and asks before discarding the draft',async()=>{
 const {user,request,onClose}=setup();
 await user.click(await screen.findByRole('button',{name:/早间项目简报/}));
 await user.click(screen.getByRole('button',{name:'编辑任务'}));
 await user.clear(screen.getByLabelText('任务名称'));
 await user.type(screen.getByLabelText('任务名称'),'修改过的名称');
 const normalRequest=request.getMockImplementation()!;
 request.mockImplementation((...args)=>args[1]==='/t1'&&args[3]==='PATCH'
  ?Promise.reject(new Error('任务已在其他入口修改')):normalRequest(...args));
 await user.click(screen.getByRole('button',{name:'保存任务'}));
 expect(await screen.findByText(/任务已在其他入口修改/)).toBeTruthy();
 expect((screen.getByLabelText('任务名称') as HTMLInputElement).value).toBe('修改过的名称');
 expect(request).toHaveBeenCalledWith('p','/t1',{revision:3,title:'修改过的名称'},'PATCH');
 await user.click(screen.getByRole('button',{name:'返回聊天'}));
 expect(onClose).not.toHaveBeenCalled();
 await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'放弃修改'}));
 expect(onClose).toHaveBeenCalledTimes(1);
});

it('submits a waiting run answer with its own pending token and request ID',async()=>{
 const pending={id:'question',kind:'ask_user',question:'选择简报范围',options:[],payload:{}};
 const run:ScheduleRun={id:'r1',session_id:'run-session',status:'waiting',scheduled_at:1793494800,error:'',pending,pending_token:'run-token'};
 const {user,request}=setup([task],[run]);
 await user.click(await screen.findByRole('button',{name:/早间项目简报/}));
 await screen.findByText('选择简报范围');
 // The real PendingInput component controls the field and submission.
 const field=screen.getByRole('textbox');
 fireEvent.change(field,{target:{value:'只看代码'}});
 const accept=screen.getByRole('button',{name:'发送回答'});
 await user.click(accept);
 await waitFor(()=>expect(request).toHaveBeenCalledWith('p','/t1/runs/r1/answer',{token:'run-token',request_id:'question',answer:'只看代码'},undefined));
});

it('refreshes task facts without overwriting an editor draft',async()=>{
 const {user,request}=setup();
 await user.click(await screen.findByRole('button',{name:/早间项目简报/}));
 await user.click(screen.getByRole('button',{name:'编辑任务'}));
 await user.type(screen.getByLabelText('任务名称'),'草稿');
 request.mockImplementation(async(_project,path='')=>path.includes('/runs')?[]:{tasks:[{...task,revision:4,title:'外部修改'}],host:{owner:true,error:'',message:'定时宿主运行中'}});
 await user.click(screen.getByRole('button',{name:'刷新任务'}));
 expect(await screen.findByText(/任务已在其他入口修改。草稿已保留/)).toBeTruthy();
 expect((screen.getByLabelText('任务名称') as HTMLInputElement).value).toBe('早间项目简报草稿');
});

it('updates the list if the selected task is deleted elsewhere and its history returns 404',async()=>{
 const {user,request}=setup();
 await user.click(await screen.findByRole('button',{name:/早间项目简报/}));
 await screen.findByRole('heading',{name:'执行历史'});
 request.mockImplementation(async(_project,path='')=>{
  if(path.includes('/runs'))throw new Error('此工作区没有该定时任务');
  return {tasks:[{...task,id:'t2',title:'另一个任务'}],host:{owner:true,error:'',message:'定时宿主运行中'}};
 });
 await user.click(screen.getByRole('button',{name:'刷新任务'}));
 expect(await screen.findByRole('button',{name:/另一个任务/})).toBeTruthy();
 await waitFor(()=>expect(screen.queryByRole('button',{name:/早间项目简报/})).toBeNull());
});

it('does not submit a second immediate run while its first request is pending',async()=>{
 const {user,request}=setup();
 await user.click(await screen.findByRole('button',{name:/早间项目简报/}));
 let complete!:(value:unknown)=>void;
 request.mockImplementation(async(_project,path='',body)=>{
  if(path==='/t1/run')return new Promise(resolve=>{complete=resolve;});
  return body?task:path.includes('/runs')?[]:{tasks:[task],host:{owner:true,error:'',message:'运行中'}};
 });
 const button=screen.getByRole('button',{name:'立即运行一次'});
 await user.dblClick(button);
 expect(request.mock.calls.filter(call=>call[1]==='/t1/run')).toHaveLength(1);
 complete({});
 await screen.findByText('已加入执行队列');
});

it('retains isolated legacy run audit without pretending it is a main conversation',async()=>{
 const run:ScheduleRun={id:'old',session_id:'old-isolated',execution:'isolated',status:'succeeded',scheduled_at:1793494800,error:'',pending:null,pending_token:null};
 const {user,onOpenRun}=setup([{...task,channel:'qq',source:{kind:'main',label:'主会话（QQ）'}}],[run]);
 await user.click(await screen.findByRole('button',{name:/早间项目简报/}));
 expect(await screen.findByText('历史独立执行，保留本页审计记录。')).toBeTruthy();
 expect(screen.queryByText('执行结果')).toBeNull();
 expect(screen.queryByRole('button',{name:'查看主会话'})).toBeNull();
 expect(screen.queryByRole('button',{name:'查看会话'})).toBeNull();
 expect(onOpenRun).not.toHaveBeenCalled();
});

it('keeps main execution bound when editing its schedule',async()=>{
 const {user}=setup([{...task,execution:'main',channel:'qq',source:{kind:'main',label:'主会话（QQ）'}}]);
 await user.click(await screen.findByRole('button',{name:/早间项目简报/}));
 await user.click(screen.getByRole('button',{name:'编辑任务'}));
 expect(screen.queryByRole('combobox',{name:'执行会话'})).toBeNull();
 expect(screen.getByText('到期后进入来源会话队列，回复保存在该会话。')).toBeTruthy();
});
