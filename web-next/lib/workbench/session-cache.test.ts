import {afterEach,expect,it,vi} from 'vitest';
import {WorkbenchStore,type Transport} from './client';
import {snapshot} from './fixtures';
import type {ServerEvent,Snapshot} from './types';
const scope=(sessionId:string,projectId='p')=>({projectId,sessionId});
const state=(id:string,patch:Partial<Snapshot>={})=>snapshot({session:{id,title:id},...patch});
const deferred=<T,>()=>{let resolve!:(value:T)=>void;let reject!:(error:Error)=>void;const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
async function setup(initial=state('a')){
 const feeds:{receive:(event:ServerEvent)=>void;fail:()=>void;cursor?:number}[]=[];
 const request=vi.fn<Transport['request']>(async path=>path==='/state'?{token:'secret'}:path.startsWith('/session-view?')?state(new URLSearchParams(path.split('?')[1]).get('session_id')??new URLSearchParams(path.split('?')[1]).get('conversation_id')!):{});
 const upload=vi.fn<NonNullable<Transport['upload']>>();
 const store=new WorkbenchStore({request,upload,subscribe:(receive,fail,cursor)=>{feeds.push({receive,fail,cursor});return()=>{};},retry:()=>()=>{}});
 await store.start();feeds[0].receive({kind:'snapshot',state:initial,seq:initial.event_seq});
 return {store,request,upload,feeds,feed:(event:ServerEvent)=>feeds.at(-1)!.receive(event)};
}
afterEach(()=>vi.useRealTimers());
it('allows rapid A to B to C selection and late B updates only its own cache',async()=>{
 const {store,request,feed}=await setup(state('a',{event_seq:1}));const b=deferred<Snapshot>(),c=deferred<Snapshot>();
 request.mockImplementationOnce(()=>b.promise).mockImplementationOnce(()=>c.promise);
 const openingB=store.openConversation('p','b');expect(store.get().snapshot).toBeNull();expect(store.getSelectedScope()).toEqual(scope('b'));
 const openingC=store.openConversation('p','c');expect(store.getSelectedScope()).toEqual(scope('c'));
 c.resolve(state('c',{event_seq:3}));await openingC;const selected=store.get().snapshot;
 b.resolve(state('b',{event_seq:2}));await openingB;expect(store.get().snapshot).toBe(selected);expect(store.getScope(scope('b'))?.session.id).toBe('b');
 feed({kind:'snapshot',state:state('a',{busy:true,event_seq:1})});expect(store.get().snapshot).toBe(selected);
 expect(request.mock.calls.map(call=>call[0])).toEqual(['/state','/session-view?project_id=p&conversation_id=b','/session-view?project_id=p&conversation_id=c']);
});
it('uses a cached target immediately while refreshing and retains target on failed reads',async()=>{
 const {store,request,feed}=await setup();const cached=state('b');feed({kind:'snapshot',state:cached});
 const loading=deferred<Snapshot>();request.mockImplementationOnce(()=>loading.promise);
 const opening=store.openConversation('p','b');expect(store.get().snapshot).toBe(cached);expect(store.get().navigating).toBe(false);
 loading.reject(new Error('history offline'));await expect(opening).rejects.toThrow('offline');
 expect(store.get().snapshot).toBe(cached);expect(store.getSelectedScope()).toEqual(scope('b'));expect(store.get().error).toContain('offline');
});
it('keeps an uncached failed target selected with an empty body',async()=>{
 const {store,request}=await setup();request.mockRejectedValueOnce(new Error('missing'));
 await expect(store.openConversation('p','b')).rejects.toThrow('missing');expect(store.get().snapshot).toBeNull();expect(store.getSelectedScope()).toEqual(scope('b'));
});
it('keeps project identity in cache keys and isolates background subscriptions',async()=>{
 vi.useFakeTimers();const {store,request,feed}=await setup(state('same'));
 const other=state('same',{project:{id:'q',name:'Q',path:'/q'}});request.mockResolvedValueOnce(other);await store.openConversation('q','same');
 const selected=store.get().snapshot,notify=vi.fn(),sourceNotify=vi.fn();store.subscribeScope(scope('same','q'),notify);store.subscribeScope(scope('same'),sourceNotify);
 feed({kind:'message_committed',project_id:'p',session_id:'same',messages:[{id:'delta',role:'assistant',content:'background'}]});vi.advanceTimersByTime(40);
 expect(store.get().snapshot).toBe(selected);expect(notify).not.toHaveBeenCalled();expect(sourceNotify).toHaveBeenCalledOnce();expect(store.getScope(scope('same'))?.messages.at(-1)?.content).toBe('background');
});
it('allows B writes during pending A submission and every write keeps its captured envelope',async()=>{
 const {store,request,feed}=await setup();feed({kind:'snapshot',state:state('b')});const pending=deferred<unknown>();request.mockImplementationOnce(()=>pending.promise);
 const sending=store.send('from A',undefined,scope('a'));request.mockResolvedValueOnce(state('b'));await store.openConversation('p','b');
 expect(store.get().mutating).toBe(false);await store.send('from B',undefined,scope('b'));
 expect(request.mock.calls.filter(call=>call[0]==='/messages').map(call=>call[1])).toEqual([
  expect.objectContaining({submission_id:expect.any(String),project_id:'p',session_id:'a',message:'from A'}),
  expect.objectContaining({submission_id:expect.any(String),project_id:'p',session_id:'b',message:'from B'}),
 ]);
 pending.resolve({});await sending;await expect(store.send('wrong',undefined,scope('a'))).rejects.toThrow('会话');
 await expect(store.send('wrong',{ids:['x'],scope:scope('a')},scope('b'))).rejects.toThrow('附件会话');
});
it('keeps attachment upload source scope while the selected conversation changes',async()=>{
 const {store,request,upload}=await setup();const pending=deferred<Awaited<ReturnType<NonNullable<Transport['upload']>>>>();upload.mockImplementationOnce(()=>pending.promise);
 const file=new File(['data'],'note.txt');const uploading=store.uploadAttachment(file,scope('a'));request.mockResolvedValueOnce(state('b'));await store.openConversation('p','b');
 pending.resolve({id:'x',kind:'file',name:'note.txt',size:4,content_type:'text/plain',content_url:'',download_url:''});await expect(uploading).rejects.toThrow('会话');
 expect(upload).toHaveBeenCalledWith('/attachments?project_id=p&session_id=a&name=note.txt',file,'secret');expect(store.get().error).toBe('');
});
it('deduplicates sequence events and retains background deltas newer than a slow history read',async()=>{
 vi.useFakeTimers();const {store,request,feed}=await setup(state('a',{event_seq:1}));const history=deferred<Snapshot>();request.mockImplementationOnce(()=>history.promise);
 const opening=store.openConversation('p','b');feed({kind:'message_committed',seq:2,project_id:'p',session_id:'b',messages:[{id:'delta',role:'assistant',content:'B'}]});feed({kind:'message_committed',seq:2,project_id:'p',session_id:'b',messages:[{id:'delta',role:'assistant',content:'duplicate'}]});vi.advanceTimersByTime(40);
 history.resolve(state('b',{event_seq:1}));await opening;expect(store.get().snapshot?.messages.at(-1)?.content).toBe('B');expect(store.get().snapshot?.event_seq).toBe(2);
 feed({kind:'message_committed',seq:3,project_id:'p',session_id:'a',messages:[{id:'delta',role:'assistant',content:'A'}]});vi.advanceTimersByTime(40);expect(store.getScope(scope('a'))?.messages.at(-1)?.content).toBe('A');
});
it('does not replay a delta already covered by gap recovery history',async()=>{
 vi.useFakeTimers();const {store,request,feed}=await setup(state('a',{event_seq:1}));request.mockResolvedValueOnce(state('a',{event_seq:3,messages:[{id:'recovered',role:'assistant',content:'recovered'}]}));
 feed({kind:'message_committed',seq:3,project_id:'p',session_id:'a',messages:[{id:'delta',role:'assistant',content:'duplicate'}]});await vi.advanceTimersByTimeAsync(40);
 expect(store.get().snapshot?.messages.at(-1)?.content).toBe('recovered');expect(request.mock.calls.map(call=>call[0])).toContain('/session-view?project_id=p&session_id=a');
});
it('resynchronizes a replay gap without resending uncertain writes',async()=>{
 const {store,request,feed,feeds}=await setup(state('a',{event_seq:7}));request.mockRejectedValueOnce(new Error('unknown'));await expect(store.send('once')).rejects.toThrow('unknown');
 await Promise.resolve();feed({kind:'snapshot',seq:7,state:state('a',{event_seq:7})});request.mockResolvedValueOnce(state('a',{event_seq:9}));feed({kind:'resync',seq:9});await Promise.resolve();await Promise.resolve();
 expect(request.mock.calls.filter(call=>call[0]==='/messages')).toHaveLength(1);expect(feeds.at(-1)?.cursor).toBe(7);
});
it('restores a connection at an unchanged cursor without changing local selection',async()=>{
 const {store,request,feed,feeds}=await setup(state('a',{event_seq:5}));request.mockResolvedValueOnce(state('b',{event_seq:5}));await store.openConversation('p','b');
 feeds[0].fail();await store.start();expect(feeds.at(-1)?.cursor).toBe(5);expect(store.get().connected).toBe(false);
 feed({kind:'ready'});expect(store.get().connected).toBe(true);expect(store.getSelectedScope()).toEqual(scope('b'));
 expect(request.mock.calls.map(call=>call[0])).toEqual(['/state','/session-view?project_id=p&conversation_id=b','/state']);
});
it('retries history prefetch after failure, shares inflight reads, and bounds inactive history',async()=>{
 const {store,request}=await setup();const first=deferred<unknown>();request.mockImplementationOnce(()=>first.promise);store.prewarmConversation('p','b');store.prewarmConversation('p','b');expect(request).toHaveBeenCalledTimes(2);
 first.reject(new Error('offline'));await Promise.resolve();await Promise.resolve();await Promise.resolve();store.prewarmConversation('p','b');await Promise.resolve();await Promise.resolve();expect(request.mock.calls.filter(call=>call[0].includes('conversation_id=b'))).toHaveLength(2);
 for(let i=0;i<10;i++){store.prewarmConversation('p','prefetch-'+i);await Promise.resolve();await Promise.resolve();await Promise.resolve();}
 expect(store.getScope(scope('b'))).toBeNull();expect(store.getScope(scope('a'))).toBeTruthy();expect(store.getScope(scope('prefetch-9'))).toBeTruthy();
});
it('projects background sidebar metadata without changing the selected history',async()=>{
 const sidebar={projects:[{id:'p',name:'P',path:'/p',available:true,conversations:[{id:'a',project_id:'p',session_id:'a',title:'A',pinned:false,archived:false},{id:'b',project_id:'p',session_id:'b',title:'B',pinned:false,archived:false}]}],recent:[],current_conversation_id:'a',new_chat:false};
 const {store,feed}=await setup(state('a',{sidebar}));feed({kind:'snapshot',state:state('b',{sidebar:{...sidebar,current_conversation_id:'b',projects:[{...sidebar.projects[0],conversations:sidebar.projects[0].conversations.map(item=>item.id==='b'?{...item,busy:true}:item)}]}})});
 expect(store.get().snapshot?.session.id).toBe('a');expect(store.get().snapshot?.sidebar?.current_conversation_id).toBe('a');expect(store.get().snapshot?.sidebar?.projects[0].conversations[1].busy).toBe(true);
});
it('keeps configuration and subagents scoped while schedules use the global host',async()=>{
 const {store,request}=await setup();
 await store.configurationRequest('',{domains:['models']});await store.scheduleRequest('p');await store.subagentRequest({project_id:'p',session_id:'a'},'');
 expect(request.mock.calls.slice(1).map(call=>[call[0],call[1]])).toEqual([
  ['/configuration',{domains:['models'],project_id:'p',session_id:'a'}],
  ['/scheduled-tasks',undefined],
  ['/subagents?project_id=p&session_id=a',undefined],
 ]);
 expect(request.mock.calls.some(call=>call[0]==='/scheduled-tasks')).toBe(true);
 await expect(store.subagentRequest({project_id:'p',session_id:'a'},'/child/messages',{project_id:'q',message:'bad'})).rejects.toThrow('不匹配');
});

it('waits for a selected session before requesting configuration',async()=>{
 const {store,request}=await setup();
 vi.spyOn(store,'getSelectedScope').mockReturnValue(null);
 request.mockClear();
 await expect(store.configurationRequest('',{domains:['skills']})).rejects.toThrow('会话未就绪');
 expect(request).not.toHaveBeenCalled();
});

it('accepts a server restart with a lower cursor and ignores old epoch HTTP history',async()=>{
 vi.useFakeTimers();const {store,request,feed}=await setup(state('a',{event_seq:500}));const stale=deferred<Snapshot>();request.mockImplementationOnce(()=>stale.promise);
 const opening=store.openConversation('p','a');request.mockResolvedValueOnce(state('a',{event_seq:0,messages:[{id:'fresh',role:'assistant',content:'restarted'}]}));
 feed({kind:'resync',seq:0,reset_cursor:true});await Promise.resolve();await Promise.resolve();
 feed({kind:'snapshot',seq:0,state:state('a',{event_seq:0,messages:[{id:'fresh',role:'assistant',content:'restarted'}]})});
 feed({kind:'message_committed',seq:1,project_id:'p',session_id:'a',messages:[{id:'delta',role:'assistant',content:'new process'}]});vi.advanceTimersByTime(40);
 stale.resolve(state('a',{event_seq:500,messages:[{id:'old',role:'assistant',content:'stale'}]}));await opening;
 expect(store.get().snapshot?.messages.at(-1)?.content).toBe('new process');expect(store.get().snapshot?.messages[0].content).toBe('restarted');expect(store.get().connected).toBe(true);
});
