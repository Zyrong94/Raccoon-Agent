// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {AssistantRuntimeProvider} from '@assistant-ui/react';
import {WorkbenchShell} from './shell';
import {WorkbenchContext,type ProjectNavigation} from './context';
import {useWorkbenchRuntime,useWorkbenchThreadListRuntime} from '@/lib/chat/runtime';
import {WorkbenchStore} from '@/lib/workbench/client';
import {snapshot} from '@/lib/workbench/fixtures';
import type {Snapshot} from '@/lib/workbench/types';
import type {ScheduledTask} from '@/lib/scheduling';
vi.mock('../chat/thread',()=>({Thread:()=> <textarea data-composer-input aria-label="消息输入"/>}));
vi.mock('@/components/main-session/main-session',()=>({MainSession:({onClose}:{onClose:()=>void})=><section aria-label="个人主会话"><textarea aria-label="主会话输入"/><button type="button" onClick={onClose}>返回项目会话</button></section>}));
afterEach(cleanup);
function shellNavigation(state:Snapshot):ProjectNavigation{
 return {state:state.sidebar,onNew:vi.fn(async()=>{}),onOpen:vi.fn(async()=>{}),onUpdate:vi.fn(async()=>{}),onAdd:vi.fn(async()=>{}),browse:vi.fn(async()=>({path:'/',parent:null,directories:[]})),getArchives:vi.fn(async()=>({projects:[],unassigned:[]}))};
}
function renderChangingScope(){
 const store=new WorkbenchStore();let state=snapshot();
 vi.spyOn(store,'get').mockImplementation(()=>({selectedScope:{projectId:state.project.id,sessionId:state.session.id},snapshot:state,connected:true,mutating:false,navigating:false,error:''}));
 function Harness({state}:{state:Snapshot}){const navigation=shellNavigation(state);const {runtime}=useWorkbenchRuntime(store,state,true,false);return <AssistantRuntimeProvider runtime={runtime}><WorkbenchContext.Provider value={{navigation,blocked:false,modelControl:null,headerExtra:null,notice:null,pendingSlot:null,workbench:{store,projectId:state.project.id,sessionId:state.session.id}}}><WorkbenchShell navigation={navigation}/></WorkbenchContext.Provider></AssistantRuntimeProvider>;}
 const mounted=render(<Harness state={state}/>);
 return {store,user:userEvent.setup(),change:(next:Snapshot)=>{state=next;mounted.rerender(<Harness state={state}/>);}};
}

it.each(['success','failure'] as const)('keeps the current session error after an old fullscreen request finishes with %s',async(outcome)=>{
 const {user,change}=renderChangingScope();
 let finish!:()=>void,fail!:(error:Error)=>void;
 const original=Object.getOwnPropertyDescriptor(document.documentElement,'requestFullscreen');
 const request=vi.fn().mockImplementationOnce(()=>new Promise<void>((resolve,reject)=>{finish=resolve;fail=reject;})).mockRejectedValue(new Error('B fullscreen failed'));
 Object.defineProperty(document.documentElement,'requestFullscreen',{configurable:true,value:request});
 try{
  await user.click(screen.getByRole('button',{name:'全屏显示'}));
  change(snapshot({project:{id:'q',name:'Q',path:'/q'}}));
  await user.click(screen.getByRole('button',{name:'全屏显示'}));
  expect(await screen.findByText('当前浏览器无法进入全屏。')).toBeTruthy();
  await act(async()=>{if(outcome==='success')finish();else fail(new Error('A fullscreen failed'));});
  expect(screen.getByText('当前浏览器无法进入全屏。')).toBeTruthy();
 }finally{if(original)Object.defineProperty(document.documentElement,'requestFullscreen',original);else Reflect.deleteProperty(document.documentElement,'requestFullscreen');}
});

it('keeps an open schedule draft bound to its source project and session after an external scope change',async()=>{
 const {store,user,change}=renderChangingScope();
 const request=vi.spyOn(store,'scheduleRequest').mockImplementation(async(_project,_path,body)=>body?{id:'created',...body as object}:{tasks:[],host:{owner:true,error:'',message:''}});
 await user.click(screen.getByRole('button',{name:'定时任务'}));
 await user.click(await screen.findByRole('button',{name:'新建任务'}));
 await user.type(screen.getByLabelText('任务名称'),'来源项目任务');
 await user.type(screen.getByLabelText('执行指令'),'检查来源项目');
 const field=screen.getByLabelText('任务名称');
 change(snapshot({project:{id:'q',name:'Q',path:'/q'},session:{id:'other',title:'Q会话'}}));
 expect(screen.getByLabelText('任务名称')).toBe(field);
 await user.click(screen.getByRole('button',{name:'保存任务'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('p','',expect.objectContaining({project_id:'p',session_id:'s',title:'来源项目任务'}),undefined));
 expect(request.mock.calls.some(([project])=>project==='q')).toBe(false);
});

it.each(['project','session'] as const)('opens a schedule run in its saved project after the active %s changes',async(kind)=>{
 const {store,user,change}=renderChangingScope();
 const open=vi.spyOn(store,'openConversation').mockResolvedValue(undefined);
 const task:ScheduledTask={source:{kind:'project',project_id:'p',label:'来源项目'},execution:'project',id:'t',revision:1,title:'来源任务',prompt:'检查',kind:'cron',spec:{cron:'0 9 * * *'},timezone:'UTC',model:null,permission_mode:null,status:'enabled',next_at:null,channel:'web',origin_session:'s',latest:null};
 vi.spyOn(store,'scheduleRequest').mockImplementation(async(_project,path='')=>path.includes('/runs')?[{id:'run',session_id:'source-run',status:'succeeded',scheduled_at:1793494800,output:'完成',error:'',pending:null,pending_token:null,notices:[]}]:{tasks:[task],host:{owner:true,error:'',message:''}});
 await user.click(screen.getByRole('button',{name:'定时任务'}));
 await user.click(await screen.findByRole('button',{name:/来源任务/}));
 await screen.findByRole('button',{name:'查看会话'});
 change(snapshot(kind==='project'?{project:{id:'q',name:'Q',path:'/q'}}:{session:{id:'other',title:'另一会话'}}));
 await user.click(screen.getByRole('button',{name:'查看会话'}));
 await waitFor(()=>expect(open).toHaveBeenCalledWith('p','source-run'));
});

it('opens a real main schedule execution in the shared main conversation',async()=>{
 const {store,user}=renderChangingScope();
 const open=vi.spyOn(store,'openConversation').mockResolvedValue(undefined);
 const navigate=vi.spyOn(store,'navigate').mockResolvedValue(undefined);
 const task:ScheduledTask={id:'main-task',revision:1,title:'QQ 主会话日报',prompt:'检查',kind:'cron',spec:{cron:'0 9 * * *'},timezone:'Asia/Shanghai',model:null,permission_mode:null,status:'enabled',next_at:null,channel:'qq',origin_session:'personal-main',latest:null,execution:'main',source:{kind:'main',label:'主会话（QQ）'}};
 vi.spyOn(store,'scheduleRequest').mockImplementation(async(_project,path='')=>path.includes('/runs')?[{id:'main-run',session_id:'personal-main',execution:'main',status:'running',scheduled_at:1793494800,output:'',error:'',pending:null,pending_token:null,notices:[]}]:{tasks:[task],host:{owner:true,error:'',message:''}});
 await user.click(screen.getByRole('button',{name:'定时任务'}));
 await user.click(await screen.findByRole('button',{name:/QQ 主会话日报/}));
 await user.click(await screen.findByRole('button',{name:'查看主会话'}));
 expect(await screen.findByRole('region',{name:'个人主会话'})).toBeTruthy();
 expect(open).not.toHaveBeenCalled();expect(navigate).not.toHaveBeenCalled();
});
function renderWorkbench({blocked=false,navigationBlocked=blocked,multipleWorkspaces=false,noProjects=false}:{blocked?:boolean;navigationBlocked?:boolean;multipleWorkspaces?:boolean;noProjects?:boolean}={}){
 const store=new WorkbenchStore();vi.spyOn(store,'configurationRequest').mockResolvedValue({root:'test',models:[],mcp:[],skills:[],subagents:[]} as never);const navigate=vi.spyOn(store,'navigate').mockResolvedValue(undefined);
 vi.spyOn(store,'scheduleRequest').mockResolvedValue({tasks:[],host:{owner:true,error:'',message:'定时宿主运行中'}});
 const current={id:'p',name:'Raccoon',path:'C:/projects/Raccoon'};
 const conversations=[{id:'s',session_id:'s',project_id:'p',title:'当前会话',pinned:false,archived:false},{id:'other',session_id:'other',project_id:'p',title:'另一会话',pinned:false,archived:false}];
 const navigation:ProjectNavigation={state:{projects:[{...current,available:true,conversations},...(multipleWorkspaces?[{id:'next',name:'另一个项目',path:'C:/projects/next',available:true,conversations:[]}]:[])],recent:conversations,current_conversation_id:'s',new_chat:false},onNew:vi.fn(async(projectId?:string)=>{await store.navigate(`/sidebar/new/${projectId??'current'}`);}),onOpen:vi.fn(async(_projectId:string,id:string)=>{await store.navigate(`/sessions/${id}/activate`);}),onUpdate:vi.fn(async()=>{}),onAdd:vi.fn(async()=>{}),browse:vi.fn(async()=>({path:'C:/',parent:null,directories:[]})),getArchives:vi.fn(async()=>({projects:[],unassigned:[]}))};
 if(noProjects)navigation.state.projects=[];
 function Harness(){const {runtime}=useWorkbenchRuntime(store,snapshot({sessions:[{id:'s',title:'当前会话'},{id:'other',title:'另一会话'}]}),true,false);return <AssistantRuntimeProvider runtime={runtime}><WorkbenchContext.Provider value={{navigation,blocked,navigationBlocked,modelControl:null,headerExtra:null,notice:null,pendingSlot:null,workbench:{store,projectId:'p',sessionId:'s'},workspace:{current,items:[current],status:'已连接',onSelect:()=>{}}}}><WorkbenchShell navigation={navigation}/></WorkbenchContext.Provider></AssistantRuntimeProvider>;}
 render(<Harness/>);return {navigate,navigation,user:userEvent.setup()};
}

it('opens schedules inside the workspace main panel and keeps the sidebar and chat draft',async()=>{
 const {user}=renderWorkbench();
 await user.type(screen.getByRole('textbox',{name:'消息输入'}),'保留的聊天草稿');
 await user.click(screen.getByRole('button',{name:'定时任务'}));
 const panel=await screen.findByRole('region',{name:'定时任务管理'});
 expect(screen.getByRole('main',{name:'工作区主面板'}).contains(panel)).toBe(true);
 expect(screen.getAllByRole('button',{name:'另一会话'})[0]!.closest('[inert]')).toBeNull();
 expect(screen.getByRole('button',{name:'定时任务'}).getAttribute('aria-current')).toBe('page');
 await user.click(screen.getAllByRole('button',{name:'当前会话'})[0]!);
 await waitFor(()=>expect(screen.queryByRole('region',{name:'定时任务管理'})).toBeNull());
 expect((screen.getByRole('textbox',{name:'消息输入'}) as HTMLTextAreaElement).value).toBe('保留的聊天草稿');
});

it('opens the interactive main conversation in the workspace and preserves the project draft',async()=>{
 const {user}=renderWorkbench();
 await user.type(screen.getByRole('textbox',{name:'消息输入'}),'项目中的草稿');
 await user.click(screen.getByRole('button',{name:'主会话'}));
 const panel=await screen.findByRole('region',{name:'个人主会话'});
 expect(screen.getByRole('main',{name:'工作区主面板'}).contains(panel)).toBe(true);
 expect(screen.getByRole('button',{name:'主会话'}).getAttribute('aria-current')).toBe('page');
 await user.type(screen.getByRole('textbox',{name:'主会话输入'}),'从网页继续聊');
 await user.click(screen.getByRole('button',{name:'返回项目会话'}));
 expect(screen.queryByRole('region',{name:'个人主会话'})).toBeNull();
 expect((screen.getByRole('textbox',{name:'消息输入'}) as HTMLTextAreaElement).value).toBe('项目中的草稿');
});

it('guards unsaved schedule edits before opening the main conversation',async()=>{
 const {user}=renderWorkbench();
 await user.click(screen.getByRole('button',{name:'定时任务'}));
 await user.click(await screen.findByRole('button',{name:'新建任务'}));
 await user.type(screen.getByLabelText('任务名称'),'未保存的排期');
 await user.click(screen.getByRole('button',{name:'主会话'}));
 let dialog=await screen.findByRole('dialog');
 await user.click(within(dialog).getByRole('button',{name:'取消'}));
 expect((screen.getByLabelText('任务名称') as HTMLInputElement).value).toBe('未保存的排期');
 expect(screen.queryByRole('region',{name:'个人主会话'})).toBeNull();
 await user.click(screen.getByRole('button',{name:'主会话'}));
 dialog=await screen.findByRole('dialog');
 await user.click(within(dialog).getByRole('button',{name:'放弃修改'}));
 expect(await screen.findByRole('textbox',{name:'主会话输入'})).toBeTruthy();
});

it('keeps the main conversation selected when settings are closed',async()=>{
 const {user}=renderWorkbench();
 await user.click(screen.getByRole('button',{name:'主会话'}));
 await user.type(await screen.findByRole('textbox',{name:'主会话输入'}),'主会话草稿');
 await user.click(screen.getByRole('button',{name:'设置'}));
 await user.click(await screen.findByRole('button',{name:'返回会话'}));
 expect((screen.getByRole('textbox',{name:'主会话输入'}) as HTMLTextAreaElement).value).toBe('主会话草稿');
 expect(screen.getByRole('button',{name:'主会话'}).getAttribute('aria-current')).toBe('page');
});

it('allows opening the main conversation while project mutations are blocked',async()=>{
 const {user}=renderWorkbench({blocked:true});
 const mainButton=screen.getByRole('button',{name:'主会话'});
 expect(mainButton.closest('[inert]')).toBeNull();
 expect((screen.getAllByRole('button',{name:'当前会话'})[0]! as HTMLButtonElement).disabled).toBe(true);
 await user.click(mainButton);
 expect(await screen.findByRole('textbox',{name:'主会话输入'})).toBeTruthy();
});

it('allows new-chat and conversation-switch shortcuts while the current chat is busy, but keeps mutation shortcuts blocked',async()=>{
 const {navigation}=renderWorkbench({blocked:true,navigationBlocked:false});
 const input=screen.getByRole('textbox',{name:'消息输入'});
 fireEvent.keyDown(input,{altKey:true,shiftKey:true,code:'KeyO',key:'O'});
 await waitFor(()=>expect(navigation.onNew).toHaveBeenCalledOnce());
 fireEvent.keyDown(input,{altKey:true,code:'ArrowDown',key:'ArrowDown'});
 await waitFor(()=>expect(navigation.onOpen).toHaveBeenCalledWith('p','other'));
 fireEvent.keyDown(input,{altKey:true,shiftKey:true,code:'KeyX',key:'X'});
 fireEvent.keyDown(input,{altKey:true,shiftKey:true,code:'KeyP',key:'P'});
 fireEvent.keyDown(input,{altKey:true,shiftKey:true,code:'KeyR',key:'R'});
 expect(navigation.onUpdate).not.toHaveBeenCalled();expect(screen.queryByRole('textbox',{name:'重命名会话'})).toBeNull();
});

it('returns to project chat when selecting a sidebar conversation from main',async()=>{
 const {user,navigate}=renderWorkbench();
 await user.click(screen.getByRole('button',{name:'主会话'}));
 await user.click(screen.getAllByRole('button',{name:'另一会话'})[0]!);
 await waitFor(()=>expect(navigate).toHaveBeenCalledWith('/sessions/other/activate'));
 expect(screen.queryByRole('region',{name:'个人主会话'})).toBeNull();
});

it('checks unsaved task changes before switching a sidebar conversation',async()=>{
 const {user,navigate}=renderWorkbench();
 await user.click(screen.getByRole('button',{name:'定时任务'}));
 await user.click(await screen.findByRole('button',{name:'新建任务'}));
 await user.type(screen.getByLabelText('任务名称'),'未保存任务');
 await user.click(screen.getAllByRole('button',{name:'另一会话'})[0]!);
 expect(navigate).not.toHaveBeenCalled();
 let dialog=await screen.findByRole('dialog');
 await user.click(within(dialog).getByRole('button',{name:'取消'}));
 expect((screen.getByLabelText('任务名称') as HTMLInputElement).value).toBe('未保存任务');
 await user.click(screen.getAllByRole('button',{name:'另一会话'})[0]!);
 dialog=await screen.findByRole('dialog');
 await user.click(within(dialog).getByRole('button',{name:'放弃修改'}));
 await waitFor(()=>expect(navigate).toHaveBeenCalledWith('/sessions/other/activate'));
 expect(screen.queryByRole('region',{name:'定时任务管理'})).toBeNull();
});

it('removes the chat title bar while keeping sidebar actions and fullscreen',async()=>{
 const store=new WorkbenchStore();vi.spyOn(store,'getSelectedScope').mockReturnValue({projectId:'p',sessionId:'s'});
 const conversation={id:'s',session_id:'s',project_id:'p',title:'会话',pinned:false,archived:false};
 const state=snapshot({sidebar:{projects:[],recent:[conversation],current_conversation_id:'s',new_chat:false},sessions:[{id:'s',title:'会话'}]});
 const navigation=shellNavigation(state);
 function Harness(){const {runtime}=useWorkbenchThreadListRuntime(store,state);return <AssistantRuntimeProvider runtime={runtime}><WorkbenchContext.Provider value={{navigation,blocked:false,modelControl:null,headerExtra:null,notice:null,pendingSlot:null}}><WorkbenchShell navigation={navigation}/></WorkbenchContext.Provider></AssistantRuntimeProvider>;}
 render(<Harness/>);
 expect(document.querySelector('[data-thread-title]')).toBeNull();
 expect(screen.queryByRole('heading',{name:'会话'})).toBeNull();
 expect(screen.queryByRole('button',{name:'会话菜单'})).toBeNull();
 expect(await screen.findByRole('button',{name:'会话操作'})).toBeTruthy();
 fireEvent.keyDown(screen.getByRole('textbox',{name:'消息输入'}),{altKey:true,shiftKey:true,code:'KeyR',key:'R'});
 expect(screen.queryByRole('textbox',{name:'重命名会话'})).toBeNull();
 expect(screen.getByRole('button',{name:'全屏显示'})).toBeTruthy();
});

it('shows the project tree and top search icon without a redundant workspace selector',async()=>{
 renderWorkbench();
 const workspace=screen.getByRole('navigation',{name:'项目工作区'});
 expect(within(workspace).getByText('Raccoon').title).toBe('C:/projects/Raccoon');
 expect(within(workspace).queryByRole('combobox')).toBeNull();
 expect(screen.queryByRole('status')).toBeNull();
 expect(screen.queryByText('已连接')).toBeNull();
 expect(screen.getByRole('button',{name:'设置'})).toBeTruthy();
 expect(screen.queryByRole('textbox',{name:'搜索聊天'})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'搜索聊天'}));
 expect(screen.getByRole('textbox',{name:'搜索聊天'})).toBeTruthy();
});

it('guards project-local new chats against unsaved schedule changes',async()=>{
 const {user,navigate}=renderWorkbench({multipleWorkspaces:true});
 await user.click(screen.getByRole('button',{name:'定时任务'}));
 await user.click(await screen.findByRole('button',{name:'新建任务'}));
 await user.type(screen.getByLabelText('任务名称'),'未保存排期');
 await user.click(screen.getByRole('button',{name:'在 另一个项目 中新建聊天'}));
 expect(navigate).not.toHaveBeenCalled();
 await user.click(within(await screen.findByRole('dialog')).getByRole('button',{name:'取消'}));
 expect(screen.getByRole('region',{name:'Raccoon 聊天'})).toBeTruthy();
 expect((screen.getByLabelText('任务名称') as HTMLInputElement).value).toBe('未保存排期');
 await user.click(screen.getByRole('button',{name:'在 另一个项目 中新建聊天'}));
 await user.click(within(await screen.findByRole('dialog')).getByRole('button',{name:'放弃修改'}));
 await waitFor(()=>expect(navigate).toHaveBeenCalledWith('/sidebar/new/next'));
 expect(screen.queryByRole('region',{name:'定时任务管理'})).toBeNull();
});

it('offers project-local new chat in the mobile sidebar drawer',async()=>{
 const {user,navigate}=renderWorkbench({multipleWorkspaces:true});
 await user.click(screen.getAllByRole('button',{name:'打开会话侧栏'})[0]);
 const drawer=await screen.findByRole('dialog');
 await user.click(within(drawer).getByRole('button',{name:'在 另一个项目 中新建聊天'}));
 await waitFor(()=>expect(navigate).toHaveBeenCalledWith('/sidebar/new/next'));
});

it('routes the global new-chat shortcut through the same scheduling guard',async()=>{
 const {user,navigation}=renderWorkbench();
 await user.click(screen.getByRole('button',{name:'定时任务'}));
 await user.click(await screen.findByRole('button',{name:'新建任务'}));
 const input=screen.getByLabelText('任务名称');
 await user.type(input,'未保存的快捷键排期');
 fireEvent.keyDown(input,{altKey:true,shiftKey:true,code:'KeyO',key:'O'});
 expect(navigation.onNew).not.toHaveBeenCalled();
 await user.click(within(await screen.findByRole('dialog')).getByRole('button',{name:'放弃修改'}));
 await waitFor(()=>expect(navigation.onNew).toHaveBeenCalledOnce());
});

it('closes the mobile drawer after the shared project dialog opens a project',async()=>{
 const {user,navigation}=renderWorkbench({multipleWorkspaces:true});
 await user.click(screen.getAllByRole('button',{name:'打开会话侧栏'})[0]!);
 const drawer=await screen.findByRole('dialog');
 await user.click(within(drawer).getByRole('button',{name:'打开项目'}));
 const projectDialog=await screen.findByRole('dialog',{name:'打开项目'});
 await waitFor(()=>expect((within(projectDialog).getByRole('textbox',{name:'项目目录'}) as HTMLInputElement).value).toBe('C:/'));
 await user.click(within(projectDialog).getByRole('button',{name:'打开项目'}));
 await waitFor(()=>expect(navigation.onAdd).toHaveBeenCalledWith('C:/'));
 await waitFor(()=>expect(document.body.contains(drawer)).toBe(false));
});

it('opens personal configuration below schedules and guards its draft before sidebar navigation',async()=>{
 const {user,navigate}=renderWorkbench();
 await user.click(screen.getByRole('button',{name:'配置管理'}));
 const panel=await screen.findByRole('region',{name:'配置管理'});
 expect(screen.getByRole('main',{name:'工作区主面板'}).contains(panel)).toBe(true);
 expect(screen.getByRole('button',{name:'配置管理'}).getAttribute('aria-current')).toBe('page');
 await user.click(await screen.findByRole('button',{name:'新建供应商'}));
 await user.type(screen.getByLabelText('显示名称'),'未保存的模型');
 await user.click(screen.getAllByRole('button',{name:'另一会话'})[0]!);
 expect(navigate).not.toHaveBeenCalled();
 await user.click(within(await screen.findByRole('dialog')).getByRole('button',{name:'取消'}));
 expect((screen.getByLabelText('显示名称') as HTMLInputElement).value).toBe('未保存的模型');
 await user.click(screen.getByRole('button',{name:'定时任务'}));
 await user.click(within(await screen.findByRole('dialog')).getByRole('button',{name:'放弃修改'}));
 expect(await screen.findByRole('region',{name:'定时任务管理'})).toBeTruthy();
});

it('uses the global no-project shortcut even when there are no project groups',async()=>{
 const {navigation}=renderWorkbench({noProjects:true});
 fireEvent.keyDown(screen.getByRole('textbox',{name:'消息输入'}),{altKey:true,shiftKey:true,code:'KeyO',key:'O'});
 await waitFor(()=>expect(navigation.onNew).toHaveBeenCalledWith());
 expect(screen.queryByRole('dialog')).toBeNull();
});
