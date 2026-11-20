// @vitest-environment jsdom
import {useEffect,useSyncExternalStore} from 'react';
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {useAuiState,type AssistantRuntime} from '@assistant-ui/react';
import {Workbench} from './workbench';
import {Composer} from '../chat/composer';
import {useWorkbenchView} from './context';
import {WorkbenchStore,type Transport} from '@/lib/workbench/client';
import {snapshot} from '@/lib/workbench/fixtures';
import type {ServerEvent,Snapshot} from '@/lib/workbench/types';

let activeStore:WorkbenchStore;
let activeRuntime:AssistantRuntime;
const mounts=vi.hoisted(()=>({thread:0,main:0,groups:0}));
vi.mock('@/components/workbench/assistant',async importOriginal=>{
 const actual=await importOriginal<typeof import('@/components/workbench/assistant')>();
 return {...actual,Assistant:(props:Parameters<typeof actual.Assistant>[0])=>{
  activeRuntime=props.runtime;return <actual.Assistant {...props}/>;
 }};
});
vi.mock('@/lib/workbench/use-workbench',()=>({useWorkbench:()=>{
 const view=useSyncExternalStore(activeStore.subscribe,activeStore.get,activeStore.get);
 return {store:activeStore,...view};
}}));
vi.mock('../chat/thread',()=>({Thread:()=>{
 const history=useAuiState(s=>s.thread.messages.map(message=>message.content.filter(part=>part.type==='text').map(part=>part.text).join('')).join('|'));
 const {pendingSlot}=useWorkbenchView();
 useEffect(()=>{mounts.thread++;},[]);
 return <><output aria-label="运行时消息">{history}</output>{pendingSlot}<Composer/></>;
}}));
vi.mock('../delivery/canvas-workspace',()=>({CanvasWorkspace:({children}:{children:React.ReactNode})=><div>{children}</div>}));
vi.mock('../main-session/main-session',()=>({MainSession:({onClose,onOpenGroups}:{onClose:()=>void;onOpenGroups?:()=>void})=>{
 useEffect(()=>{mounts.main++;},[]);
 return <section aria-label="个人主会话"><textarea aria-label="主会话输入"/>{onOpenGroups&&<button onClick={onOpenGroups}>打开群聊</button>}<button onClick={onClose}>返回项目会话</button></section>;
}}));
vi.mock('@/components/main-session/group-sessions',()=>({GroupSession:({onClose}:{onClose:()=>void})=>{
 useEffect(()=>{mounts.groups++;},[]);
 return <section aria-label="群聊会话"><p>QQ 群聊记录</p><button onClick={onClose}>返回主会话</button></section>;
}}));

beforeEach(()=>{
 mounts.thread=0;mounts.main=0;mounts.groups=0;
 activeRuntime=undefined as unknown as AssistantRuntime;
 vi.stubGlobal('ResizeObserver',class{observe(){}unobserve(){}disconnect(){}});
 Element.prototype.scrollIntoView=vi.fn();
});
afterEach(()=>{cleanup();activeStore?.stop();vi.restoreAllMocks();vi.unstubAllGlobals();});

const conversations=[
 {id:'pa',session_id:'shared',project_id:'p',title:'A 会话',pinned:false,archived:false},
 {id:'pb',session_id:'b',project_id:'p',title:'B 会话',pinned:false,archived:false},
 {id:'qa',session_id:'shared',project_id:'q',title:'Q 会话',pinned:false,archived:false},
];
function state(project='p',session='shared'):Snapshot{
 const item=conversations.find(item=>item.project_id===project&&item.session_id===session)!;
 return snapshot({project:{id:project,name:project.toUpperCase(),path:`/${project}`},session:{id:session,title:item.title},
  sessions:conversations.filter(item=>item.project_id===project).map(item=>({id:item.session_id,title:item.title})),
  messages:[{id:`${project}-${session}`,role:'assistant',content:`${item.title}历史`}],models:[{ref:'m',label:'测试模型'}],
  sidebar:{projects:['p','q'].map(id=>({id,name:id.toUpperCase(),path:`/${id}`,available:true,conversations:conversations.filter(item=>item.project_id===id)})),recent:conversations,current_conversation_id:item.id,new_chat:false},
 });
}
async function mountWorkbench(initial=state()){
 let receive!:(event:ServerEvent)=>void;
 const request=vi.fn<Transport['request']>(async path=>{
  if(path==='/state')return {token:'test'};
  const query=new URL(path,'http://local').searchParams;
  const project=query.get('project_id')??'p';
  const conversation=query.get('conversation_id');
  const target=conversations.find(item=>item.project_id===project&&(item.id===conversation||item.session_id===conversation));
  return target?state(project,target.session_id):{children:[]};
 });
 const upload=vi.fn(async(_path:string,file:File)=>({id:`uploaded-${file.name}`,kind:'file' as const,name:file.name,content_type:file.type,size:file.size,content_url:'/api/attachments/test/content',download_url:'/api/attachments/test/download'}));
 activeStore=new WorkbenchStore({request,upload,subscribe:callback=>{receive=callback;return()=>{};},retry:()=>()=>{}});
 await activeStore.start();receive({kind:'snapshot',state:initial});
 const mounted=render(<Workbench/>);
 await waitFor(()=>expect(activeRuntime.threads.mainItem.getState().remoteId).toBe(JSON.stringify([initial.project.id,initial.session.id])));
 const feed=async(next:Snapshot)=>{
  const item=conversations.find(item=>item.project_id===next.project.id&&item.session_id===next.session.id)!;
  await activeStore.openConversation(item.project_id,item.id);
  await waitFor(()=>expect(activeRuntime.threads.mainItem.getState().remoteId).toBe(JSON.stringify([item.project_id,item.session_id])));
  await waitFor(()=>expect(screen.getByLabelText('运行时消息').textContent).toBe(`${next.session.title}历史`));
 };
 return {...mounted,feed,receive,user:userEvent.setup(),store:activeStore,request,upload};
}

it('archives the open sidebar conversation into an empty landing view while retaining its original composer draft',async()=>{
 const {container,request,receive}=await mountWorkbench();
 const source=activeRuntime.threads.getById(JSON.stringify(['p','shared'])).composer;
 act(()=>source.setText('已归档会话的未发送草稿'));
 const directory=state().sidebar!;
 const archived={...directory,projects:directory.projects.map(project=>({...project,conversations:project.conversations.filter(item=>item.id!=='pa')})),recent:directory.recent.filter(item=>item.id!=='pa')};
 request.mockImplementation(async(path,body)=>{
  if(path==='/sidebar/conversations/pa'){
   // The server's global selection can belong to another project.
   const other={...state('q'),sidebar:{...archived,current_conversation_id:'qa'}};
   receive({kind:'snapshot',state:other});return other;
  }
  if(path==='/sidebar/new'){
   const landing=snapshot({session:{id:'draft',title:'新会话'},sidebar:{...archived,current_conversation_id:null,new_chat:true},navigation_id:(body as {navigation_id:string}).navigation_id});
   receive({kind:'snapshot',state:landing});return landing;
  }
  return {children:[]};
 });
 const row=screen.getAllByRole('button',{name:'A 会话'})[0].closest('[data-testid="conversation-row"]') as HTMLElement;
 fireEvent.click(within(row).getByRole('button',{name:'归档'}));
 await waitFor(()=>expect(activeRuntime.threads.mainItem.getState().remoteId).toBe(JSON.stringify(['p','draft'])));
 expect(container.querySelector('[data-thread-title]')).toBeNull();
 expect(screen.getByLabelText('运行时消息').textContent).toBe('');
 expect(activeRuntime.thread.composer.getState().text).toBe('');
 expect(source.getState().text).toBe('已归档会话的未发送草稿');
 expect(screen.queryByRole('button',{name:'A 会话'})).toBeNull();
 expect(request.mock.calls.filter(([path])=>path.startsWith('/sidebar/')).map(([path])=>path)).toEqual(['/sidebar/conversations/pa','/sidebar/new']);
});

it('starts cold approval collaboration reads after navigation settles without flashing a scope error',async()=>{
 const {store,request}=await mountWorkbench();
 const subagents=vi.spyOn(store,'subagentRequest');
 let finishHistory!:(value:Snapshot)=>void;
 const history=new Promise<Snapshot>(resolve=>{finishHistory=resolve;});
 request.mockImplementation(async path=>path.startsWith('/session-view?')?history:{children:[]});
 let opening!:Promise<void>;
 act(()=>{opening=store.openConversation('p','pb');});
 expect(store.get()).toMatchObject({snapshot:null,navigating:true});
 expect(subagents).not.toHaveBeenCalled();
 await act(async()=>{finishHistory(state('p','b'));await opening;});
 await waitFor(()=>expect(screen.getByLabelText('运行时消息').textContent).toBe('B 会话历史'));
 expect(store.get().navigating).toBe(false);
 expect(screen.queryByText('父会话已切换或连接未就绪，请同步后继续')).toBeNull();
 const before=request.mock.calls.length;
 await expect(store.subagentRequest({project_id:'p',session_id:'shared'},'/child/messages',{message:'late',mode:'follow_up',event_id:'late'})).rejects.toThrow('父会话已切换');
 expect(request.mock.calls.length).toBe(before);
 await waitFor(()=>expect(subagents).toHaveBeenCalledWith({project_id:'p',session_id:'b'},'',undefined,expect.any(AbortSignal)));
 expect(screen.queryByText('父会话已切换或连接未就绪，请同步后继续')).toBeNull();
});

it('keeps the actual shell and sidebar DOM with isolated retained composers and approval input across sessions and colliding project IDs',async()=>{
 const ask=(value:Snapshot):Snapshot=>({...value,pending:{id:'shared-question',kind:'question',question:`${value.session.title}的问题`,options:[],payload:{}}});
 const {container,feed,request}=await mountWorkbench(ask(state()));
 request.mockImplementation(async path=>{
  const query=new URL(path,'http://local').searchParams;
  const item=conversations.find(item=>item.project_id===query.get('project_id')&&item.id===query.get('conversation_id'));
  return item?ask(state(item.project_id,item.session_id)):{children:[]};
 });
 const panel=screen.getByRole('main',{name:'工作区主面板'}),sidebar=container.querySelector('#raccoon-demo-sidebar');
 const shell=panel.parentElement,first=activeRuntime;
 act(()=>first.thread.composer.setText('A 未发送文字'));
 fireEvent.change(screen.getByRole('textbox',{name:'自由回答'}),{target:{value:'旧问题回答'}});
 for(const next of [state('p','b'),state('q','shared'),state()]){
  await feed(next);
  expect(screen.getByRole('main',{name:'工作区主面板'})).toBe(panel);
  expect(panel.parentElement).toBe(shell);
  expect(container.querySelector('#raccoon-demo-sidebar')).toBe(sidebar);
  expect(activeRuntime).toBe(first);
  expect(screen.getByLabelText('运行时消息').textContent).toBe(`${next.session.title}历史`);
  expect(activeRuntime.thread.composer.getState().text).toBe(next.project.id==='p'&&next.session.id==='shared'?'A 未发送文字':'');
  expect(activeRuntime.thread.composer.getState().attachments).toHaveLength(0);
  const answer=screen.getByRole('textbox',{name:'自由回答'}) as HTMLInputElement;
  expect(answer.value).toBe('');fireEvent.change(answer,{target:{value:'不能带往下一个会话'}});
 }
 // The chat subtree is intentionally scoped even though the surrounding shell survives.
 expect(mounts.thread).toBe(4);
});

it('preserves shell collapse, sidebar search, and the mounted main conversation during external scope changes',async()=>{
 const {container,feed,user}=await mountWorkbench();
 await user.click(screen.getByRole('button',{name:'搜索聊天'}));
 const search=screen.getByRole('textbox',{name:'搜索聊天'}) as HTMLInputElement;
 await user.type(search,'会话');
 await user.click(screen.getByRole('button',{name:'收起会话侧栏'}));
 await feed(state('p','b'));
 expect(container.querySelector('#raccoon-demo-sidebar')?.classList.contains('md:flex')).toBe(false);
 expect(screen.getByRole('textbox',{name:'搜索聊天'})).toBe(search);expect(search.value).toBe('会话');
 await user.click(screen.getByRole('button',{name:'展开会话侧栏'}));
 await user.click(screen.getByRole('button',{name:'主会话'}));
 const main=screen.getByRole('region',{name:'个人主会话'});
 const input=screen.getByRole('textbox',{name:'主会话输入'}) as HTMLTextAreaElement;
 await user.type(input,'主会话中的草稿');
 for(const next of [state('q','shared'),state()]){
  await feed(next);
  expect(screen.getByRole('region',{name:'个人主会话'})).toBe(main);
  expect(screen.getByRole('textbox',{name:'主会话输入'})).toBe(input);expect(input.value).toBe('主会话中的草稿');
  expect(screen.getByRole('button',{name:'主会话'}).getAttribute('aria-current')).toBe('page');
  expect(screen.getByRole('textbox',{name:'搜索聊天'})).toBe(search);expect(search.value).toBe('会话');
 }
 expect(mounts.main).toBe(1);
});

it('replaces the whole panel with the QQ group session opened from the main session',async()=>{
 const {user}=await mountWorkbench();
 await user.click(screen.getByRole('button',{name:'主会话'}));
 await user.click(screen.getByRole('button',{name:'打开群聊'}));
 const groups=screen.getByRole('region',{name:'群聊会话'});
 expect(groups.textContent).toContain('QQ 群聊记录');
 expect(screen.queryByRole('region',{name:'个人主会话'})).toBeNull();
 expect(mounts.groups).toBe(1);
 await user.click(within(groups).getByRole('button',{name:'返回主会话'}));
 expect(screen.queryByRole('region',{name:'群聊会话'})).toBeNull();
 expect(screen.getByRole('region',{name:'个人主会话'})).toBeTruthy();
});

it('keeps a late failed send and its attachment in its original scope without replacing target input or errors',async()=>{
 const {feed,store,upload,request}=await mountWorkbench();
 expect(request.mock.calls.some(([path])=>path==='/sidebar/new')).toBe(false);
 await waitFor(()=>expect(store.get()).toMatchObject({connected:true,mutating:false,navigating:false}));
 let reject!:(cause:Error)=>void;
 const send=vi.spyOn(store,'send').mockImplementation(()=>new Promise((_resolve,rejectSend)=>{reject=rejectSend;}));
 const report=vi.spyOn(store,'reportError');
 const source=activeRuntime.thread.composer;
 await act(()=>source.addAttachment(new File(['A'],'a.txt',{type:'text/plain'})));
 act(()=>source.setText('A 失败草稿'));
 act(()=>source.send());
 await waitFor(()=>expect(send).toHaveBeenCalledOnce());expect(upload).toHaveBeenCalledOnce();
 await feed(state('q','shared'));
 const target=activeRuntime.thread.composer;
 act(()=>target.setText('Q 新输入'));
 await act(()=>target.addAttachment(new File(['Q'],'q.txt',{type:'text/plain'})));
 await act(async()=>{reject(new Error('A 延迟失败'));await Promise.resolve();});
 expect(target.getState().text).toBe('Q 新输入');expect(target.getState().attachments.map(value=>value.name)).toEqual(['q.txt']);
 expect(store.get().error).toBe('');expect(report).not.toHaveBeenCalled();expect(screen.queryByText('恢复草稿')).toBeNull();
 await feed(state());
 await waitFor(()=>expect(activeRuntime.thread.composer.getState().text).toBe('A 失败草稿'));
 expect(activeRuntime.thread.composer.getState().attachments.map(value=>value.name)).toEqual(['a.txt']);
 expect(activeRuntime.thread.composer.getState().attachments[0].id).toBe('uploaded-a.txt');
});
