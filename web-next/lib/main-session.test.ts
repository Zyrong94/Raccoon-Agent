import {describe,expect,it,vi} from 'vitest';
import {MainSessionStore,type MainSnapshot} from './main-session';


const state=(overrides:Partial<MainSnapshot>={}):MainSnapshot=>({available:true,session:{id:'main',title:'主会话'},session_id:'main',generation:'owner-1',messages:[],live:[],busy:false,cancellable:false,pending:null,status:'idle',error:null,...overrides});

describe('personal main transport',()=>{
 it('changes model and permission mode on the guarded main endpoints',async()=>{
  const request=vi.fn(async(path:string)=>path==='/state'?{token:'token'}:state());
  const store=new MainSessionStore(request);await store.refresh();
  await store.setModel('fake/other');await store.setPermissionMode('auto');
  expect(request).toHaveBeenCalledWith('/main-session/model',expect.objectContaining({model:'fake/other',session_id:'main',generation:'owner-1'}),'token');
  expect(request).toHaveBeenCalledWith('/main-session/mode',expect.objectContaining({mode:'auto',session_id:'main',generation:'owner-1'}),'token');
 });
 it('uses the main identity and never submits to a project endpoint',async()=>{
  const request=vi.fn(async(path:string)=>path==='/state'?{token:'token'}:state());
  const store=new MainSessionStore(request);await store.refresh();await store.send('hello');
  expect(request.mock.calls.map(call=>call[0])).toEqual(['/state','/main-session/state','/main-session/messages']);
  expect(request.mock.calls.at(-1)).toEqual(['/main-session/messages',expect.objectContaining({message:'hello',session_id:'main',generation:'owner-1',message_id:expect.any(String)}),'token']);
 });
 it('retains history and draft on failure without replaying a write',async()=>{
  let fail=false;const request=vi.fn(async(path:string)=>{if(path==='/state')return {token:'token'};if(fail)throw new Error('offline');return state({messages:[{id:'q',role:'user',content:'QQ hello'}]});});
  const store=new MainSessionStore(request);await store.refresh();store.setDraft('keep me');fail=true;
  await expect(store.send('keep me')).rejects.toThrow('offline');
  expect(store.get().draft).toBe('keep me');expect(store.get().snapshot?.messages).toHaveLength(1);
  await store.refresh();expect(request.mock.calls.filter(call=>call[0]==='/main-session/messages')).toHaveLength(1);
  expect(store.get().connected).toBe(false);
 });
 it('keeps a failed send visible after polling recovers until an explicit retry succeeds',async()=>{
  let rejectSend=true;
  const request=vi.fn(async(path:string)=>{if(path==='/state')return {token:'token'};if(path.endsWith('/messages')&&rejectSend)throw new Error('submission unknown');return state();});
  const store=new MainSessionStore(request);await store.refresh();
  await expect(store.send('keep me')).rejects.toThrow('submission unknown');
  await store.refresh();
  expect(store.get()).toMatchObject({connected:true,error:'submission unknown',draft:'keep me',failedDraft:'keep me'});
  expect(request.mock.calls.filter(call=>call[0].endsWith('/messages'))).toHaveLength(1);
  rejectSend=false;await store.send('keep me');await store.refresh();
  expect(store.get()).toMatchObject({connected:true,error:'',draft:'',failedDraft:''});
 });
 it('clears a recovered polling error when no send failed',async()=>{
  let offline=true;
  const request=vi.fn(async(path:string)=>{if(offline)throw new Error('offline');return path==='/state'?{token:'token'}:state();});
  const store=new MainSessionStore(request);await store.refresh();
  expect(store.get()).toMatchObject({connected:false,error:'offline'});
  offline=false;await store.refresh();expect(store.get()).toMatchObject({connected:true,error:''});
 });
 it('ignores an older refresh after a mutation and clears successful drafts',async()=>{
  let release:((state:MainSnapshot)=>void)|undefined;let reads=0;
  const request=vi.fn(async(path:string)=>{if(path==='/state')return {token:'token'};if(path==='/main-session/state'&&++reads===2)return new Promise<MainSnapshot>(resolve=>{release=resolve;});return state({messages:reads>1?[{id:'new',role:'user',content:'sent'}]:[]});});
  const store=new MainSessionStore(request);await store.refresh();const pending=store.refresh();await Promise.resolve();await store.send('sent');release?.(state());await pending;
  expect(store.get().snapshot?.messages[0]?.id).toBe('new');expect(store.get().draft).toBe('');
 });
 it('keeps newly typed text while an earlier send is pending',async()=>{
  let resolve:((value:MainSnapshot)=>void)|undefined;
  const request=vi.fn(async(path:string)=>path==='/state'?{token:'token'}:path.endsWith('/messages')?new Promise<MainSnapshot>(done=>{resolve=done;}):state());
  const store=new MainSessionStore(request);await store.refresh();const sending=store.send('first draft');store.setDraft('newer text');resolve?.(state());await sending;
  expect(store.get().draft).toBe('newer text');
 });
 it('does not accept a late mutation response after the host generation rotates',async()=>{
  let resolve:((value:MainSnapshot)=>void)|undefined;let rotated=false;
  const request=vi.fn(async(path:string)=>path==='/state'?{token:'token'}:path.endsWith('/messages')?new Promise<MainSnapshot>(done=>{resolve=done;}):state({generation:rotated?'owner-2':'owner-1'}));
  const store=new MainSessionStore(request);await store.refresh();const sending=store.send('maybe accepted');rotated=true;await store.refresh();resolve?.(state());
  await expect(sending).rejects.toThrow('宿主已更换');expect(store.get().snapshot?.generation).toBe('owner-2');expect(store.get().draft).toBe('maybe accepted');
 });
});

describe('personal main attachments and native UI',()=>{
 it('uploads a raw file with personal identity and sends attachment-only messages by ID',async()=>{
  const file=new Blob(['notes'],{type:'text/plain'});Object.assign(file,{name:'笔记.txt'});
  const descriptor={id:'upload',kind:'file',name:'笔记.txt',content_type:'text/plain',size:5,content_url:'/api/main-session/attachments/upload/content?session_id=main',download_url:'/api/main-session/attachments/upload/download?session_id=main'};
  const request=vi.fn(async(path:string)=>path==='/state'?{token:'token'}:path.includes('/attachments?')?descriptor:state());
  const store=new MainSessionStore(request);await store.refresh();const scope={sessionId:'main',generation:'owner-1'};
  await expect(store.uploadAttachment(file as File,scope)).resolves.toEqual(descriptor);
  expect(request).toHaveBeenCalledWith('/main-session/attachments?session_id=main&generation=owner-1&name=%E7%AC%94%E8%AE%B0.txt',file,'token');
  await store.send('',{ids:['upload'],scope});
  expect(request).toHaveBeenCalledWith('/main-session/messages',expect.objectContaining({message:'',attachment_ids:['upload'],session_id:'main',generation:'owner-1'}),'token');
 });
 it('rejects a late upload after host rotation without sending a message',async()=>{
  let finish!:(value:unknown)=>void;let generation='owner-1';
  const request=vi.fn(async(path:string)=>path==='/state'?{token:'token'}:path.includes('/attachments?')?new Promise(resolve=>{finish=resolve;}):state({generation}));
  const store=new MainSessionStore(request);await store.refresh();const upload=store.uploadAttachment(Object.assign(new Blob(['x']),{name:'x.txt'}) as File,{sessionId:'main',generation});
  generation='owner-2';await store.refresh();finish({id:'old'});
  await expect(upload).rejects.toThrow('宿主');expect(store.get().snapshot?.generation).toBe('owner-2');
  expect(request.mock.calls.some(call=>call[0].endsWith('/messages'))).toBe(false);
 });
});
