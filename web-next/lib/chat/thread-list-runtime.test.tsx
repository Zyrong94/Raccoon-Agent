// @vitest-environment jsdom
import {StrictMode,useSyncExternalStore} from 'react';
import {act,cleanup,render,waitFor} from '@testing-library/react';
import {AssistantRuntimeProvider,type AssistantRuntime} from '@assistant-ui/react';
import {afterEach,expect,it,vi} from 'vitest';
import {WorkbenchStore,type Transport} from '../workbench/client';
import {snapshot} from '../workbench/fixtures';
import {loadingSnapshot,threadScopeId,useWorkbenchThreadListRuntime} from './runtime';
import type {ServerEvent,Snapshot} from '../workbench/types';

afterEach(cleanup);
const scope=(projectId='p',sessionId='s')=>({projectId,sessionId});
const directory={projects:['p','q'].map(id=>({id,name:id,path:`/${id}`,available:true,conversations:[{id:`${id}-chat`,project_id:id,session_id:'s',title:id,pinned:false,archived:false}]})),recent:[],new_chat:false,current_conversation_id:'p-chat'};
async function mount(initial:Snapshot=snapshot({sidebar:directory}),strict=false){
  let receive!:(event:ServerEvent)=>void;
  const request=vi.fn<Transport['request']>(async path=>{if(path==='/state')return {token:'secret'};const id=new URL(path,'http://local').searchParams.get('project_id')??'p';return snapshot({project:{id,name:id,path:`/${id}`},sidebar:{...directory,current_conversation_id:`${id}-chat`}});});
  const store=new WorkbenchStore({request,subscribe:callback=>{receive=callback;return()=>{};},retry:()=>()=>{}});
  await store.start();receive({kind:'snapshot',state:initial});
  let runtime!:AssistantRuntime;
  function Harness(){const view=useSyncExternalStore(store.subscribe,store.get,store.get);const {runtime:value}=useWorkbenchThreadListRuntime(store,view.snapshot??initial);runtime=value;return <AssistantRuntimeProvider runtime={value}/>;}
  render(strict?<StrictMode><Harness/></StrictMode>:<Harness/>);
  await waitFor(()=>expect(runtime.threads.mainItem.getState().remoteId).toBe(threadScopeId(scope())));
  return {store,request,receive,get runtime(){return runtime;}};
}

it('retains original attachment objects and background state through StrictMode and A to B to A navigation',async()=>{
  const initial=snapshot({sidebar:directory,messages:[{id:'history',role:'assistant',content:'P history'}]});
  const app=await mount(initial,true),manager=app.runtime;
  expect(app.store.get().navigating).toBe(false);
  expect(app.request.mock.calls).toEqual([['/state']]);
  const source=manager.threads.getById(threadScopeId(scope()));
  const file=new File(['P'],'p.txt',{type:'text/plain'});
  await act(()=>source.composer.addAttachment(file));
  act(()=>source.composer.setText('P draft'));
  const attachment=source.composer.getState().attachments[0];
  await act(()=>app.store.openConversation('q','q-chat'));
  await waitFor(()=>expect(manager.threads.mainItem.getState().remoteId).toBe(threadScopeId(scope('q'))));
  expect(app.runtime).toBe(manager);expect(manager.thread.composer.getState().text).toBe('');
  expect(manager.thread.composer.getState().attachments).toEqual([]);
  act(()=>manager.thread.composer.setText('Q draft'));
  const target=manager.threads.getById(threadScopeId(scope('q')));
  act(()=>app.receive({kind:'snapshot',state:{...initial,busy:true,cancellable:true}}));
  await waitFor(()=>expect(source.getState().isRunning).toBe(true));
  expect(manager.threads.mainItem.getState().remoteId).toBe(threadScopeId(scope('q')));
  expect(target.composer.getState().text).toBe('Q draft');
  await act(()=>app.store.openConversation('p','p-chat'));
  await waitFor(()=>expect(manager.threads.mainItem.getState().remoteId).toBe(threadScopeId(scope())));
  expect(app.runtime).toBe(manager);
  expect(manager.thread.composer.getState().text).toBe('P draft');
  expect(manager.thread.composer.getState().attachments[0]).toBe(attachment);
  expect(manager.thread.composer.getState().attachments[0].file).toBe(file);
  expect(target.composer.getState().text).toBe('Q draft');
  expect(app.request.mock.calls.some(([path])=>path==='/execution/prepare')).toBe(false);
});

it('does not reload the thread list or replace messages on a permission update',async()=>{
 const initial=snapshot({sidebar:directory,messages:[{id:'history',role:'assistant',content:'保留历史'}]});
 const app=await mount(initial),reload=vi.spyOn(app.runtime.threads,'reload');
 const thread=app.runtime.thread,before=thread.getState().messages;
 act(()=>thread.composer.setText('未发送草稿'));
 act(()=>app.receive({kind:'permission_mode',project_id:'p',session_id:'s',permission_mode:'auto',seq:1}));
 expect(app.store.get().snapshot?.permission_mode).toBe('auto');
 expect(reload).not.toHaveBeenCalled();
 expect(app.runtime.thread).toBe(thread);
 expect(thread.getState().messages[0]).toBe(before[0]);
 expect(thread.composer.getState().text).toBe('未发送草稿');
});

it('routes official metadata mutations to the project conversation instead of a colliding session',async()=>{
  const app=await mount();const update=vi.spyOn(app.store,'updateConversation').mockResolvedValue();
  const item=app.runtime.threads.getItemById(threadScopeId(scope('q')));
  await act(()=>item.rename('Q renamed'));
  await act(()=>item.updateCustom({pinned:'true'}));
  await act(()=>item.archive());
  expect(update.mock.calls).toEqual([['q','q-chat',{title:'Q renamed'}],['q','q-chat',{pinned:true}],['q','q-chat',{archived:true}]]);
});

it('does not transplant previous messages, approvals or artifacts into a loading target',()=>{
  const previous=snapshot({messages:[{id:'secret',role:'user',content:'old'}],pending:{id:'approval',kind:'permission',question:'old',options:[],payload:{}},artifacts:[{id:'a',name:'old',size:1,suffix:'',mime:'text/plain',content_url:'/old',download_url:'/old'}],sidebar:directory});
  const target=loadingSnapshot(previous,scope('q'));
  expect(target.project.id).toBe('q');expect(target.messages).toEqual([]);expect(target.pending).toBeNull();expect(target.artifacts).toEqual([]);
});


it('uses grouped archived conversations while preserving independent same-title chats',async()=>{
 const conversations=[{id:'family',session_id:'s',project_id:'p',title:'同名',archived:true,pinned:false},{id:'independent',session_id:'independent',project_id:'p',title:'同名',archived:true,pinned:false}];
 const app=await mount(snapshot({sidebar:{projects:[{id:'p',name:'项目',path:'/p',available:true,conversations}],recent:conversations,current_conversation_id:'family',new_chat:false},sessions:[{id:'old',conversation_id:'family',title:'同名',archived:true},{id:'s',conversation_id:'family',title:'同名',archived:true},{id:'independent',title:'同名',archived:true}]}));
 const update=vi.spyOn(app.store,'updateSession').mockResolvedValue();
 expect(app.runtime.threads.getState().archivedThreadIds.map(id=>app.runtime.threads.getItemById(id).getState().remoteId)).toEqual([threadScopeId(scope()),threadScopeId(scope('p','independent'))]);
 expect(update).not.toHaveBeenCalled();
 expect(app.request.mock.calls).toEqual([['/state']]);
});

it('sends through the selected manager runtime with an explicit project and session envelope',async()=>{
 const app=await mount();await act(()=>app.store.openConversation('q','q-chat'));
 await waitFor(()=>expect(app.runtime.threads.mainItem.getState().remoteId).toBe(threadScopeId(scope('q'))));
 act(()=>app.runtime.thread.composer.setText('Q message'));
 await act(async()=>{app.runtime.thread.composer.send();});
 expect(app.request).toHaveBeenCalledWith('/messages',expect.objectContaining({message:'Q message',project_id:'q',session_id:'s',mode:'auto',submission_id:expect.any(String)}),'secret');
});


it('preserves the neutral workspace marker while a recent conversation history loads',()=>{
 const project={id:'neutral',name:'无项目',path:'/neutral',available:true,projectless:true,conversations:[{id:'chat',session_id:'version',project_id:'neutral',title:'独立聊天',pinned:false,archived:false}]};
 const previous=snapshot({sidebar:{...directory,projects:[...directory.projects,project]}});
 const pending=loadingSnapshot(previous,{projectId:'neutral',sessionId:'version'});
 expect(pending.project).toEqual({id:'neutral',name:'无项目',path:'/neutral',projectless:true});
 expect(pending.sidebar?.current_conversation_id).toBe('chat');
});
