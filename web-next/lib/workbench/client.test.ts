import {describe,it,expect,vi,afterEach} from 'vitest';
import {WorkbenchStore,browserTransport,type Transport} from './client';
import {snapshot} from './fixtures';
import type {ServerEvent,Snapshot} from './types';
function setup(){
  const feeds:{receive:(e:ServerEvent)=>void;fail:()=>void;close:ReturnType<typeof vi.fn>}[]=[];
  const request=vi.fn<Transport['request']>(async()=>({token:'secret'}));
  const wire=vi.fn(request);
  const io:Transport={request:wire,subscribe:(receive,fail)=>{const close=vi.fn();feeds.push({receive,fail,close});return close;},retry:()=>()=>{}};
  const store=new WorkbenchStore(io);
  return {store,feeds,request,wire};
}
function navigationId(request:ReturnType<typeof setup>['request']){return (request.mock.calls.at(-1)?.[1] as {navigation_id:string}).navigation_id;}
function selected(request:ReturnType<typeof setup>['request'],patch:Partial<Snapshot>={}){return snapshot({...patch,navigation_id:navigationId(request)});}
afterEach(()=>{vi.unstubAllGlobals();vi.useRealTimers();});
it('uses the existing token for schedule edits without disconnecting chat',async()=>{
 const {store,feeds,request}=setup();await store.start();
 feeds[0].receive({kind:'snapshot',state:snapshot()});
 await store.scheduleRequest('p','/task-1',{revision:2,title:'简报'},'PATCH');
 expect(request).toHaveBeenLastCalledWith('/scheduled-tasks/task-1',{revision:2,title:'简报'},'secret','PATCH');
 expect(feeds[0].close).not.toHaveBeenCalled();
 await store.scheduleRequest('other','/task-1/run',{});
 expect(request).toHaveBeenLastCalledWith('/scheduled-tasks/task-1/run',{},'secret',undefined);
});

it('keeps global schedules across a workspace switch and never replays failed writes',async()=>{
 const {store,feeds,request}=setup();await store.start();
 feeds[0].receive({kind:'snapshot',state:snapshot()});
 let finish!:(value:unknown)=>void;
 request.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
 const loading=store.scheduleRequest('p');
 request.mockResolvedValueOnce(snapshot({project:{id:'other',name:'其他',path:'/other'},session:{id:'other',title:'其他'}}));await store.openConversation('other','other');
 finish({tasks:[]});
 await expect(loading).resolves.toEqual({tasks:[]});
 request.mockResolvedValueOnce(snapshot());await store.openConversation('p','s');
 request.mockRejectedValueOnce(new Error('连接断开'));
 await expect(store.scheduleRequest('p','',{request_id:'stable'})).rejects.toThrow('连接断开');
 expect(request.mock.calls.filter(call=>call[1]&&typeof call[1]==='object'&&'request_id' in call[1])).toHaveLength(1);
});
it('applies permission SSE updates without replacing history or sidebar and ignores older events',async()=>{
 const {store,feeds,request}=setup();await store.start();
 feeds[0].receive({kind:'snapshot',state:snapshot({permission_mode:'ask'})});
 const before=store.get().snapshot!;
 feeds[0].receive({kind:'permission_mode',project_id:'p',session_id:'s',permission_mode:'auto',seq:2});
 expect(store.get().snapshot?.permission_mode).toBe('auto');
 expect(store.get().snapshot?.messages).toBe(before.messages);
 expect(store.get().snapshot?.sessions).toBe(before.sessions);
 expect(store.get().snapshot?.sidebar).toBe(before.sidebar);
 expect(store.get()).toMatchObject({connected:true,navigating:false,mutating:false});
 feeds[0].receive({kind:'permission_mode',project_id:'p',session_id:'s',permission_mode:'ask',seq:1});
 expect(store.get().snapshot?.permission_mode).toBe('auto');
 expect(request.mock.calls.map(([path])=>path)).toEqual(['/state']);
});
it('uses PATCH and the existing token header for a session update',async()=>{
 const fetch=vi.fn().mockResolvedValue({ok:true,json:async()=>({})});vi.stubGlobal('fetch',fetch);
 await browserTransport.request('/sessions/s',{pinned:true},'test-token','PATCH');
 expect(fetch).toHaveBeenCalledWith('/api/sessions/s',expect.objectContaining({method:'PATCH',headers:{'Content-Type':'application/json','X-Raccoon-Token':'test-token'},body:'{"pinned":true}'}));
});
it('deletes a session through the DELETE route',async()=>{
 const {store,feeds,request}=setup();await store.start();
 feeds[0].receive({kind:'snapshot',state:snapshot({sessions:[{id:'a',title:'A'}]})});
 await store.deleteSession('a');
 expect(request.mock.calls.some(call=>call[0]==='/sessions/a'&&call[3]==='DELETE')).toBe(true);
});
describe('connection ownership',()=>{
  it('resets cached sequence watermarks when the server process epoch changes',async()=>{
    const {store,feeds}=setup();await store.start();
    feeds[0].receive({kind:'snapshot',epoch:'server-a',seq:48,state:snapshot({model:'old',event_epoch:'server-a',event_seq:48})});
    expect(store.get().snapshot?.model).toBe('old');
    await store.start();
    feeds[1].receive({kind:'snapshot',epoch:'server-b',seq:3,state:snapshot({model:'new',event_epoch:'server-b',event_seq:3})});
    expect(store.get().snapshot?.model).toBe('new');
    expect(store.get().snapshot?.event_epoch).toBe('server-b');
  });
  it('waits for initial SSE snapshot and ignores late events after reconnect',async()=>{
    const {store,feeds}=setup();await store.start();
    expect(store.get().connected).toBe(false);
    feeds[0].receive({kind:'snapshot',state:snapshot({busy:true})});
    expect(store.get().connected).toBe(true);
    await store.start();feeds[0].receive({kind:'message_committed',project_id:'p',session_id:'s',messages:[{id:'stale',role:'assistant',content:'stale'}]});
    feeds[1].receive({kind:'snapshot',state:snapshot({pending:{id:'q',kind:'ask_user',question:'Q',options:[],payload:{}}})});
    expect(store.get().snapshot?.messages).toEqual([]);
    expect(store.get().snapshot?.pending?.id).toBe('q');
  });
  it('serializes submissions; never replays a failed or unknown write',async()=>{
    const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot()});
    let reject!:(e:Error)=>void;
    request.mockImplementationOnce(()=>new Promise((_,r)=>{reject=r;}));
    const send=store.send('hello');await expect(store.send('hello')).rejects.toThrow();
    reject(new Error('network'));
    await expect(send).rejects.toThrow('network');
    expect(request.mock.calls.filter(c=>c[0]==='/messages')).toHaveLength(1);
    expect(store.get().connected).toBe(false);
    feeds.at(-1)!.receive({kind:'snapshot',state:snapshot()});
    expect(store.get().error).toContain('network');
  });
  it('restores authoritative connection on switch failure',async()=>{
    const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot()});
    request.mockRejectedValueOnce(new Error('conflict'));
    await expect(store.navigate('/sessions/x/activate')).rejects.toThrow('conflict');
    feeds.at(-1)!.receive({kind:'snapshot',state:snapshot()});
    expect(store.get().connected).toBe(true);
  });
  it('validates pending identity, allows free text, and checks cancellation',async()=>{
    const {store,feeds,request}=setup();await store.start();
    feeds[0].receive({kind:'snapshot',state:snapshot({pending:{id:'q',kind:'ask_user',question:'Q',options:[],payload:{}}})});
    await expect(store.answer('old','a')).rejects.toThrow();
    await store.answer('q','自由回答');
    expect(request).toHaveBeenCalledWith('/input',expect.objectContaining({project_id:'p',session_id:'s',request_id:'q',answer:'自由回答',submission_id:expect.any(String)}),'secret');
    await expect(store.cancel()).rejects.toThrow();
  });
});

it('does not repeat an acknowledged pending answer while SSE catches up',async()=>{
 const {store,feeds}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot({pending:{id:'q',kind:'ask_user',question:'Q',options:[],payload:{}}})});
 await store.answer('q','a');await expect(store.answer('q','a')).rejects.toThrow();
 feeds.at(-1)!.receive({kind:'snapshot',state:snapshot()});expect(store.get().connected).toBe(true);
});
it('releases the submission lock after a connection fails during a write',async()=>{
 const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot()});
 let resolve!:(value:unknown)=>void;request.mockImplementationOnce(()=>new Promise(r=>{resolve=r;}));
 const send=store.send('hello');feeds[0].fail();resolve({});await send;
 expect(store.get().mutating).toBe(false);
 await store.start();feeds.at(-1)!.receive({kind:'snapshot',state:snapshot()});expect(store.get().error).toBe('');
});

it('sends session metadata with PATCH and keeps the server authoritative',async()=>{
 const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot({sessions:[{id:'s',title:'原名'}]})});
 await store.updateSession('s',{title:'  新名称  ',pinned:true});
 expect(request).toHaveBeenCalledWith('/sessions/s',expect.objectContaining({project_id:'p',session_id:'s',submission_id:expect.any(String),title:'新名称',pinned:true}),'secret','PATCH',undefined);
 expect(store.get().snapshot?.sessions[0].title).toBe('原名');
 expect(store.get().connected).toBe(true);
 expect(feeds).toHaveLength(1);expect(feeds[0].close).not.toHaveBeenCalled();
 feeds.at(-1)!.receive({kind:'snapshot',state:snapshot({sessions:[{id:'s',title:'新名称',pinned:true}]})});
 expect(store.get().snapshot?.sessions[0].pinned).toBe(true);
});

it('rejects invalid titles and pending session mutations without sending requests',async()=>{
 const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot({sessions:[{id:'s',title:'旧'}]})});
 await expect(store.updateSession('s',{title:'  '})).rejects.toThrow();
 await expect(store.updateSession('s',{title:'长'.repeat(201)})).rejects.toThrow();
 feeds[0].receive({kind:'snapshot',state:snapshot({pending:{id:'q',kind:'ask_user',question:'回答',options:[],payload:{}}})});
 await expect(store.updateSession('s',{archived:true})).rejects.toThrow();
 expect(request.mock.calls.filter(c=>c[0].startsWith('/sessions'))).toHaveLength(0);
});

const sidebar=()=>({projects:[{id:'p',name:'项目',path:'/p',available:true,conversations:[]},{id:'q',name:'另一项目',path:'/q',available:true,conversations:[]}],recent:[],current_conversation_id:null,new_chat:true});
it('routes project new/open/update through the logical directory without trusting HTTP snapshots',async()=>{
 const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot({sidebar:sidebar()})});
 request.mockResolvedValueOnce(snapshot({project:{id:'q',name:'stale',path:'/q'}}));
 await store.newChat('q',true);
 expect(request).toHaveBeenCalledWith('/sidebar/new',{project_id:'q',draft_only:true,navigation_id:expect.any(String)},'secret');
 expect(store.get().snapshot?.project.id).toBe('p');
 feeds.at(-1)!.receive({kind:'snapshot',state:selected(request,{sidebar:sidebar()})});
 request.mockResolvedValueOnce(snapshot({project:{id:'q',name:'Q',path:'/q'},session:{id:'family',title:'Family'},sidebar:sidebar()}));
 await store.openConversation('q','family');
 expect(request).toHaveBeenCalledWith('/session-view?project_id=q&conversation_id=family');
 await store.updateConversation('q','family',{pinned:true});
 expect(request).toHaveBeenCalledWith('/sidebar/conversations/family',{project_id:'q',pinned:true},'secret','PATCH');
});
it('refuses draft migration once a message has been accepted',async()=>{
 const {store,feeds}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot({sidebar:{...sidebar(),new_chat:false}})});
 await expect(store.newChat('q',true)).rejects.toThrow('新聊天');
});

it('carries a new-chat draft into a newly opened directory but starts blank from history',async()=>{
 const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot({sidebar:sidebar()})});
 const file=new File(['notes'],'notes.txt');const source={text:'保留打开项目草稿',files:[file]};
 store.registerComposerDraft({scope:{projectId:'p',sessionId:'s'},capture:()=>source,restore:vi.fn()});
 request.mockResolvedValueOnce(snapshot({project:{id:'added',name:'added',path:'/added'},session:{id:'fresh',title:'新聊天'}}));
 await store.addProject('/added');
 expect(request).toHaveBeenCalledWith('/workspaces',{path:'/added',new_chat:true,navigation_id:expect.any(String)},'secret');
 const restored=vi.fn();store.registerComposerDraft({scope:{projectId:'added',sessionId:'fresh'},capture:()=>({text:'',files:[]}),restore:restored});
 feeds.at(-1)!.receive({kind:'snapshot',state:selected(request,{project:{id:'added',name:'added',path:'/added'},session:{id:'fresh',title:'新聊天'},sidebar:sidebar()})});
 expect(restored).toHaveBeenCalledWith(source,undefined);
 feeds.at(-1)!.receive({kind:'snapshot',state:snapshot({sidebar:{...sidebar(),new_chat:false}})});
 store.registerComposerDraft({scope:{projectId:'p',sessionId:'s'},capture:()=>source,restore:vi.fn()});
 request.mockResolvedValueOnce(snapshot({project:{id:'next',name:'next',path:'/next'},session:{id:'fresh2',title:'新聊天'}}));
 await store.addProject('/next');
 const fresh=vi.fn();store.registerComposerDraft({scope:{projectId:'next',sessionId:'fresh2'},capture:()=>({text:'',files:[]}),restore:fresh});
 feeds.at(-1)!.receive({kind:'snapshot',state:selected(request,{project:{id:'next',name:'next',path:'/next'},session:{id:'fresh2',title:'新聊天'},sidebar:sidebar()})});
 expect(fresh).toHaveBeenCalledWith({text:'',files:[]},undefined);
});

describe('committed chat events',()=>{
 it('shows each saved message immediately and finishes without replacing history',async()=>{
  const {store,feeds}=setup();
  await store.start();
  const feed=feeds[0].receive;
  const scope={project_id:'p',session_id:'s'};
  feed({kind:'snapshot',state:snapshot({busy:true,cancellable:true})});
  const user={id:'user',role:'user' as const,content:'hello'};
  feed({kind:'message_committed',...scope,messages:[user]});
  expect(store.get().snapshot?.messages).toEqual([user]);

  feed({kind:'tool',...scope,event:{id:'tool',name:'view',kind:'started'}});
  const answer={id:'answer',role:'assistant' as const,content:'world'};
  feed({kind:'message_committed',...scope,messages:[answer]});
  feed({kind:'message_committed',...scope,messages:[answer]});
  const history=store.get().snapshot?.messages;
  expect(history).toEqual([user,answer]);

  feed({kind:'run_state',...scope,settled:true,
    state:{busy:false,cancellable:false,status:'completed',pending:null,error:null,run_phase:null}});
  expect(store.get().snapshot).toMatchObject({busy:false,cancellable:false,activity:[]});
  expect(store.get().snapshot?.messages).toBe(history);
 });
 it('keeps the active feed during successful sends and ignores their stale HTTP snapshot',async()=>{
  const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot()});
  request.mockImplementationOnce(async()=>{feeds[0].receive({kind:'snapshot',state:snapshot({busy:true,cancellable:true,model:'new'})});return snapshot({model:'old'});});
  await store.send('hello');
  expect(feeds).toHaveLength(1);expect(feeds[0].close).not.toHaveBeenCalled();
  expect(request.mock.calls.filter(call=>call[0]==='/state')).toHaveLength(1);
  expect(store.get().connected).toBe(true);expect(store.get().snapshot?.model).toBe('new');
 });
});

it('keeps thinking and background tool phases scoped to their owning conversation',async()=>{
 const {store,feeds,request}=setup();await store.start();
 const main={project_id:'p',session_id:'s'};
 const other={project_id:'p',session_id:'other'};
 feeds[0].receive({kind:'snapshot',state:snapshot({busy:true,cancellable:true})});
 request.mockResolvedValueOnce(snapshot({session:{id:'other',title:'Other'}}));
 await store.openConversation('p','other');
 feeds[0].receive({kind:'run_phase',...main,event:{phase:'thinking'}});
 feeds[0].receive({kind:'tool',...main,event:{id:'native',name:'shell',kind:'background_started'}});
 expect(store.get().snapshot?.run_phase).toBeUndefined();
 const first=store.getScope({projectId:'p',sessionId:'s'});
 expect(first).toMatchObject({run_phase:'thinking',running_tool_call_ids:['native'],busy:true,cancellable:true});
 feeds[0].receive({kind:'run_phase',...other,event:{phase:'waiting_tools'}});
 expect(store.get().snapshot?.run_phase).toBe('waiting_tools');
 feeds[0].receive({kind:'tool',...main,event:{id:'native',name:'shell',kind:'tool_completed',ok:true}});
 expect(store.getScope({projectId:'p',sessionId:'s'})?.running_tool_call_ids).toEqual([]);
});

describe('independent cancellation',()=>{
 const cancelled=(request:ReturnType<typeof setup>['request'])=>request.mock.calls.filter(call=>call[0]==='/cancel');
 it('stops during an outstanding chat POST without waiting for its mutation lock',async()=>{
  const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot()});
  let finish!:(value:unknown)=>void;request.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const send=store.send('hello');feeds[0].receive({kind:'snapshot',state:snapshot({busy:true,cancellable:true})});
  await store.cancel();
  expect(request).toHaveBeenLastCalledWith('/cancel',{project_id:'p',session_id:'s',submission_id:expect.any(String)},'secret');expect(store.get().mutating).toBe(true);
  expect(feeds[0].close).not.toHaveBeenCalled();
  await expect(store.cancel()).rejects.toThrow('正在停止');
  finish({accepted:true});await send;
  feeds[0].receive({kind:'snapshot',state:snapshot()});feeds[0].receive({kind:'snapshot',state:snapshot({busy:true,cancellable:true})});
  await store.cancel();
  expect(cancelled(request)).toHaveLength(2);
 });
 it('can stop with the existing token while the event stream is disconnected',async()=>{
  const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot({busy:true,cancellable:true})});feeds[0].fail();
  await store.cancel();
  expect(store.get().connected).toBe(false);expect(request).toHaveBeenLastCalledWith('/cancel',{project_id:'p',session_id:'s',submission_id:expect.any(String)},'secret');
 });
 it('refuses to cancel without a live cancellable snapshot and exposes the request failure',async()=>{
  const {store,feeds,request}=setup();await store.start();
  await expect(store.cancel()).rejects.toThrow('无法停止');expect(cancelled(request)).toHaveLength(0);
  feeds[0].receive({kind:'snapshot',state:snapshot({busy:true,cancellable:false})});
  await expect(store.cancel()).rejects.toThrow('无法停止');expect(cancelled(request)).toHaveLength(0);
  feeds[0].receive({kind:'snapshot',state:snapshot({busy:true,cancellable:true})});
  request.mockRejectedValueOnce(new Error('cancel network failure'));await expect(store.cancel()).rejects.toThrow('cancel network failure');
  expect(store.get().error).toBe('cancel network failure');
 });
 it('blocks duplicate cancels and refuses to stop a session other than the one the caller captured',async()=>{
  const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot({busy:true,cancellable:true})});
  let finish!:(value:unknown)=>void;request.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  const cancelling=store.cancel();await expect(store.cancel()).rejects.toThrow('正在停止');
  request.mockResolvedValueOnce(snapshot({busy:true,cancellable:true,session:{id:'other',title:'Other'}}));await store.openConversation('p','other');
  await expect(store.cancel({projectId:'p',sessionId:'s'})).rejects.toThrow('已切换');
  finish({accepted:true});await cancelling;
  expect(cancelled(request)).toHaveLength(1);
 });
});

describe('history epoch recovery',()=>{
 const state=(id:string,epoch='old',seq=50,content=id)=>snapshot({session:{id,title:id},event_epoch:epoch,event_seq:seq,messages:[{id,role:'assistant',content}]});
 const deferred=<T,>()=>{let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done;});return {promise,resolve};};
 async function harness(){
  let feed!:(event:ServerEvent)=>void;
  const request=vi.fn<Transport['request']>(async()=>({token:'secret'}));
  const store=new WorkbenchStore({request,subscribe:receive=>{feed=receive;return()=>{};},retry:()=>()=>{}});
  await store.start();feed({kind:'snapshot',epoch:'old',seq:50,state:state('a')});
  return {store,request,feed:(event:ServerEvent)=>feed(event)};
 }
 it('refreshes every cached scope on an epoch snapshot and joins new history instead of an old inflight response',async()=>{
  const {store,request,feed}=await harness();
  const stale=deferred<Snapshot>();request.mockImplementationOnce(()=>stale.promise);
  const opening=store.openConversation('p','b');
  expect(store.get().snapshot).toBeNull();
  request.mockImplementation(async path=>state(new URLSearchParams(path.split('?')[1]).get('session_id')!,'new',2,'fresh'));
  feed({kind:'snapshot',epoch:'new',seq:2,state:state('a','new',2,'fresh')});
  expect(request).toHaveBeenLastCalledWith('/session-view?project_id=p&session_id=b');
  stale.resolve(state('b','old',500,'stale'));await opening;
  expect(store.get().snapshot?.session.id).toBe('b');
  for(const sessionId of ['a','b'])expect(store.getScope({projectId:'p',sessionId})).toMatchObject({event_epoch:'new',event_seq:2,messages:[{content:'fresh'}]});
  expect(request.mock.calls.map(call=>call[0])).toEqual(['/state','/session-view?project_id=p&conversation_id=b','/session-view?project_id=p&session_id=a','/session-view?project_id=p&session_id=b']);
 });
 it('refreshes background caches when the first event of the new process is a delta',async()=>{
  vi.useFakeTimers();const {store,request,feed}=await harness();
  feed({kind:'snapshot',epoch:'old',seq:51,state:state('b','old',51)});
  request.mockImplementation(async path=>state(new URLSearchParams(path.split('?')[1]).get('session_id')!,'new',1,'recovered'));
  feed({kind:'message_committed',epoch:'new',seq:1,project_id:'p',session_id:'b',messages:[{id:'covered',role:'assistant',content:'covered'}]});
  await vi.advanceTimersByTimeAsync(40);
  expect(store.get().snapshot?.session.id).toBe('a');
  for(const sessionId of ['a','b'])expect(store.getScope({projectId:'p',sessionId})).toMatchObject({event_epoch:'new',event_seq:1,messages:[{content:'recovered'}],live:[]});
 });
 it('rejects history from an epoch different from the active feed',async()=>{
  const {store,request}=await harness();request.mockResolvedValueOnce(state('a','previous',500,'stale'));
  await expect(store.openConversation('p','a')).rejects.toThrow('过期');
  expect(store.get().snapshot).toMatchObject({event_epoch:'old',event_seq:50,messages:[{content:'a'}]});
 });
 it('requires physical history responses to match the requested session while allowing conversation version resolution',async()=>{
  const {store,request,feed}=await harness();
  request.mockResolvedValueOnce(state('version','old',50));await store.openConversation('p','family');
  expect(store.get().snapshot?.session.id).toBe('version');
  request.mockImplementation(async path=>state(path.includes('session_id=version')?'wrong':'a','old',50));feed({kind:'resync',epoch:'old',seq:50});
  await vi.waitFor(()=>expect(store.get().error).toContain('无效'));
  expect(store.get().snapshot?.session.id).toBe('version');
 });
});

it.each(['snapshot','receipt','early-sse'])('transfers a cleared project draft to the neutral scope using %s navigation',async(kind)=>{
 const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot({sidebar:sidebar()})});
 const file=new File(['notes'],'notes.txt'),draft={text:'独立聊天草稿',files:[file]};
 store.registerComposerDraft({scope:{projectId:'p',sessionId:'s'},capture:()=>draft,restore:vi.fn()});
 const target=snapshot({project:{id:'neutral',name:'无项目',path:'/neutral',projectless:true},session:{id:'independent',title:'新聊天'},sidebar:sidebar()});
 request.mockImplementationOnce(async(_path,body)=>{
  const navigation_id=(body as {navigation_id:string}).navigation_id;
  if(kind==='early-sse')feeds[0].receive({kind:'snapshot',state:{...target,navigation_id}});
  return kind==='snapshot'?{...target,navigation_id}:{navigation_id};
 });
 await store.newChat(undefined,true);
 expect(request).toHaveBeenCalledWith('/sidebar/new',{draft_only:true,navigation_id:expect.any(String)},'secret');
 const restored=vi.fn();store.registerComposerDraft({scope:{projectId:'neutral',sessionId:'independent'},capture:()=>({text:'',files:[]}),restore:restored});
 if(kind!=='early-sse')feeds[0].receive({kind:'snapshot',state:selected(request,target)});
 expect(restored).toHaveBeenCalledWith(draft,undefined);
 expect(restored.mock.calls[0][0].files[0]).toBe(file);
});
it('defaults a global new chat to the neutral scope and clears its composer draft',async()=>{
 const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot({sidebar:sidebar()})});
 store.registerComposerDraft({scope:{projectId:'p',sessionId:'s'},capture:()=>({text:'旧草稿',files:[new File(['x'],'x.txt')]}),restore:vi.fn()});
 await store.newChat();
 expect(request).toHaveBeenCalledWith('/sidebar/new',{navigation_id:expect.any(String)},'secret');
 const restored=vi.fn();store.registerComposerDraft({scope:{projectId:'neutral',sessionId:'new'},capture:()=>({text:'',files:[]}),restore:restored});
 feeds[0].receive({kind:'snapshot',state:selected(request,{project:{id:'neutral',name:'无项目',path:'/neutral',projectless:true},session:{id:'new',title:'新聊天'},sidebar:sidebar()})});
 expect(restored).toHaveBeenCalledWith({text:'',files:[]},undefined);
});

it('binds a receipt draft to its accepted scope before the composer mounts and later snapshots omit navigation metadata',async()=>{
 const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot({sidebar:sidebar()})});
 const draft={text:'延迟挂载的草稿',files:[]};
 store.registerComposerDraft({scope:{projectId:'p',sessionId:'s'},capture:()=>draft,restore:vi.fn()});
 request.mockImplementationOnce(async(_path,body)=>({navigation_id:(body as {navigation_id:string}).navigation_id}));
 await store.newChat(undefined,true);
 const target=snapshot({project:{id:'neutral',name:'无项目',path:'/neutral',projectless:true},session:{id:'new',title:'新聊天'},sidebar:sidebar()});
 feeds[0].receive({kind:'snapshot',state:selected(request,target)});
 feeds[0].receive({kind:'snapshot',state:target});
 const restored=vi.fn();store.registerComposerDraft({scope:{projectId:'neutral',sessionId:'new'},capture:()=>({text:'',files:[]}),restore:restored});
 expect(restored).toHaveBeenCalledWith(draft,undefined);
});
it('applies a sidebar row for another project without loading that conversation',async()=>{
 const {store,feeds,request}=setup();await store.start();
 const initial=snapshot({sidebar:{projects:[
   {id:'p',name:'项目',path:'/p',available:true,conversations:[]},
   {id:'q',name:'另一个',path:'/q',available:true,conversations:[]}],recent:[],current_conversation_id:null,new_chat:false}});
 feeds[0].receive({kind:'snapshot',state:initial});
 const row={id:'c9',project_id:'q',session_id:'s9',title:'别处',updated_at:'2026-02-02T00:00:00',pinned:true,archived:false};
 feeds[0].receive({kind:'sidebar_item',project_id:'q',conversation_id:'c9',item:row,sessions:[{id:'s9',title:'别处'}]});
 await new Promise(resolve=>setTimeout(resolve,0));
 const projects=store.get().snapshot?.sidebar?.projects??[];
 expect(projects.find(item=>item.id==='q')?.conversations.map(item=>item.id)).toEqual(['c9']);
 expect(request.mock.calls.some(call=>String(call[1]??'').includes('s9'))).toBe(false);
});
it('keeps the draft model and permission local until a message is sent during a run',async()=>{
 const {store,feeds,request}=setup();await store.start();
 const initial=snapshot({
  busy:true,cancellable:true,model:'a/one',
  models:[{ref:'a/one',label:'One'},{ref:'b/two',label:'Two',input_capabilities:{images:true,pdf:false}}],
 });
 feeds[0].receive({kind:'snapshot',state:initial});
 store.selectComposerModel('b/two');
 await store.selectComposerPermission('auto');
 expect(store.get().snapshot).toBe(initial);
 expect(store.composerSettings()).toEqual({model:'b/two',permissionMode:'auto',capabilities:{images:true,pdf:false}});
 expect(request.mock.calls.map(([path])=>path)).toEqual(['/state']);
 await store.send('next question');
 expect(request).toHaveBeenLastCalledWith('/messages',expect.objectContaining({
  message:'next question',model_ref:'b/two',permission_mode:'auto',
 }),'secret');
});
it('answers the current approval while its original turn is still busy',async()=>{
 const {store,feeds,request}=setup();await store.start();
 feeds[0].receive({kind:'snapshot',state:snapshot({busy:true,cancellable:true,status:'waiting',pending:{id:'approval-1',kind:'permission_confirmation',question:'允许?',options:[{id:'allow_once',label:'本次允许'}],payload:{}}})});
 await store.answer('approval-1','allow_once');
 expect(request).toHaveBeenLastCalledWith('/input',expect.objectContaining({request_id:'approval-1',answer:'allow_once',submission_id:expect.any(String)}),'secret');
 await expect(store.answer('approval-1','allow_once')).rejects.toThrow('失效');
});

it('sends slash text and attachments through the ordinary message endpoint',async()=>{
 const {store,feeds,request}=setup();await store.start();feeds[0].receive({kind:'snapshot',state:snapshot()});
 await store.send('/research task',{ids:['file'],scope:{projectId:'p',sessionId:'s'}});
 expect(request).toHaveBeenCalledWith('/messages',expect.objectContaining({message:'/research task',attachment_ids:['file'],project_id:'p',session_id:'s'}),'secret');
 expect(request.mock.calls.some(call=>call[0]==='/commands')).toBe(false);
});


it('targets another session for metadata and deletion while the selected session is busy',async()=>{
 const {store,feeds,request}=setup();
 await store.start();
 feeds[0].receive({kind:'snapshot',state:snapshot({busy:true,sessions:[{id:'s',title:'当前会话'},{id:'b',title:'其他会话'}]})});
 await store.updateSession('b',{title:'新名称'});
 expect(request).toHaveBeenCalledWith('/sessions/b',expect.objectContaining({project_id:'p',session_id:'b',title:'新名称'}),'secret','PATCH',undefined);
 await store.deleteSession('b');
 expect(request).toHaveBeenCalledWith('/sessions/b',expect.objectContaining({project_id:'p',session_id:'b'}),'secret','DELETE',undefined);
 expect(store.get().snapshot?.session.id).toBe('s');
});
