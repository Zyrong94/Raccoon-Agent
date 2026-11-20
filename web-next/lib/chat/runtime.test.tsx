// @vitest-environment jsdom
import {act,cleanup,renderHook,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {useWorkbenchRuntime} from './runtime';
import {WorkbenchStore,type Transport} from '../workbench/client';
import {snapshot} from '../workbench/fixtures';
import type {ServerEvent} from '../workbench/types';
afterEach(cleanup);

it.each([false,true])('accepts messages while busy only when the run can still be stopped (cancellable=%s)',cancellable=>{
 const store=new WorkbenchStore();
 const {result}=renderHook(()=>useWorkbenchRuntime(store,snapshot({busy:true,cancellable,status:'running'}),true,false));
 const thread=result.current.runtime.thread.getState();
 expect(thread.isSendDisabled).toBe(!cancellable);
 expect(thread.isRunning).toBe(cancellable);
 expect(thread.messages).toHaveLength(cancellable?1:0);
});

it('allows slash text with attachments through the normal send path',async()=>{
 let receive!:(event:ServerEvent)=>void;const upload=vi.fn(async()=>({id:'file',kind:'file' as const,name:'notes.txt',content_type:'text/plain',size:4,content_url:'/file',download_url:'/download'}));const request=vi.fn(async()=>({token:'secret'}));
 const store=new WorkbenchStore({request,upload,subscribe:callback=>{receive=callback;return()=>{};},retry:()=>()=>{}});
 await store.start();receive({kind:'snapshot',state:snapshot()});
 const {result}=renderHook(()=>useWorkbenchRuntime(store,snapshot(),true,false));
 const composer=result.current.runtime.thread.composer;
 await act(()=>composer.addAttachment(new File(['data'],'notes.txt',{type:'text/plain'})));
 act(()=>composer.setText('/context'));
 expect(composer.getState().canSend).toBe(true);
 await act(()=>composer.send());
 await waitFor(()=>expect(upload).toHaveBeenCalledTimes(1));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/messages',expect.objectContaining({message:'/context',attachment_ids:['file']}),'secret'));
 expect(composer.getState().text).toBe('');
 expect(composer.getState().attachments).toHaveLength(0);
});

it('restores a failed send into the official composer',async()=>{
 const store=new WorkbenchStore();vi.spyOn(store,'send').mockRejectedValue(new Error('offline'));
 const {result}=renderHook(()=>useWorkbenchRuntime(store,snapshot(),true,false));
 act(()=>result.current.runtime.thread.composer.setText('保留草稿'));
 act(()=>result.current.runtime.thread.composer.send());
 await waitFor(()=>expect(store.send).toHaveBeenCalledWith('保留草稿',undefined,{projectId:'p',sessionId:'s'},undefined));
 await waitFor(()=>expect(result.current.runtime.thread.composer.getState().text).toBe('保留草稿'));
});

it('keeps cancellation available while disconnected or another mutation is pending',async()=>{
 const store=new WorkbenchStore();const cancel=vi.spyOn(store,'cancel').mockResolvedValue(undefined);
 const {result}=renderHook(()=>useWorkbenchRuntime(store,snapshot({busy:true,cancellable:true}),false,true));
 expect(result.current.runtime.thread.getState().isSendDisabled).toBe(true);
 expect(result.current.runtime.thread.getState().capabilities.cancel).toBe(true);
 await act(()=>result.current.runtime.thread.cancelRun());expect(cancel).toHaveBeenCalledTimes(1);
});

it('disables sending and cancellation while displaying an unconfirmed navigation preview',()=>{
 const store=new WorkbenchStore();const cancel=vi.spyOn(store,'cancel').mockResolvedValue(undefined);
 const {result}=renderHook(()=>useWorkbenchRuntime(store,snapshot({busy:true,cancellable:true}),true,false,true));
 expect(result.current.runtime.thread.getState().isSendDisabled).toBe(true);
 expect(result.current.runtime.thread.getState().capabilities.cancel).toBe(false);expect(cancel).not.toHaveBeenCalled();
});

it('preserves SDK history message identity while displaying each live update',()=>{
 const store=new WorkbenchStore();const state=snapshot({busy:true,messages:[
  {id:'u1',role:'user',content:'之前的问题'},{id:'a1',role:'assistant',content:'之前的回答'},{id:'u2',role:'user',content:'新问题'},
 ],live:[{content:'第一个字',reasoning:''}],sessions:[{id:'s',title:'会话'}]});
 const {result,rerender}=renderHook(({state})=>useWorkbenchRuntime(store,state,true,false),{initialProps:{state}});
 const first=result.current.runtime.thread.getState().messages;
 rerender({state:{...state,live:[{content:'第一个字，继续输出',reasoning:''}]}});
 const second=result.current.runtime.thread.getState().messages;
 expect(second[0]).toBe(first[0]);expect(second[1]).toBe(first[1]);expect(second[2]).toBe(first[2]);
 expect(second[3].content).toEqual([{type:'text',text:'第一个字，继续输出'}]);
});


const projectSidebar=(newChat=true)=>({projects:[{id:'p',name:'项目',path:'/p',available:true,conversations:[]},{id:'q',name:'另一项目',path:'/q',available:true,conversations:[]}],recent:[],current_conversation_id:null,new_chat:newChat});
it.each(['q','neutral'])('migrates text and original File to %s and stages its attachment again',async(targetId)=>{
 const feeds:((event:ServerEvent)=>void)[]=[];
 const request=vi.fn<Transport['request']>(async()=>({token:'secret'}));
 const upload=vi.fn(async(_path:string,_file:File,_token:string)=>({id:'target-upload',kind:'file' as const,name:'notes.txt',content_type:'text/plain',size:4,content_url:'/api/attachments/target-upload/content',download_url:'/api/attachments/target-upload/download'}));
 const store=new WorkbenchStore({request,upload,subscribe:receive=>{feeds.push(receive);return()=>{};},retry:()=>()=>{}});
 const source=snapshot({sidebar:projectSidebar()});await store.start();feeds[0]({kind:'snapshot',state:source});
 const first=renderHook(()=>useWorkbenchRuntime(store,source,true,false));
 const file=new File(['data'],'notes.txt',{type:'text/plain'});
 act(()=>first.result.current.runtime.thread.composer.setText('跨项目草稿'));
 await act(()=>first.result.current.runtime.thread.composer.addAttachment(file));
 await act(()=>store.newChat(targetId==='neutral'?undefined:targetId,true));first.unmount();
 const target=snapshot({navigation_id:(request.mock.calls.at(-1)![1] as {navigation_id:string}).navigation_id,project:{id:targetId,name:targetId==='neutral'?'无项目':'另一项目',path:'/target',projectless:targetId==='neutral'},session:{id:'new',title:'新会话'},sidebar:projectSidebar()});
 feeds.at(-1)!({kind:'snapshot',state:target});
 const second=renderHook(()=>useWorkbenchRuntime(store,target,true,false));
 await waitFor(()=>expect(second.result.current.runtime.thread.composer.getState().attachments).toHaveLength(1));
 const composer=second.result.current.runtime.thread.composer;
 expect(composer.getState().text).toBe('跨项目草稿');
 expect(composer.getState().attachments[0].id).not.toBe('source-upload');
 expect(composer.getState().attachments[0].file).toBe(file);
 act(()=>composer.send());
 await waitFor(()=>expect(upload).toHaveBeenCalled());
 expect(upload.mock.calls[0][0]).toContain('project_id='+targetId);expect(upload.mock.calls[0][0]).toContain('session_id=new');
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/messages',expect.objectContaining({message:'跨项目草稿',attachment_ids:['target-upload'],project_id:targetId,session_id:'new',mode:'auto',submission_id:expect.any(String)}),'secret'));
});
it('keeps the source draft on navigation rejection and keeps a later global new chat blank',async()=>{
 const feeds:((event:ServerEvent)=>void)[]=[];const request=vi.fn<Transport['request']>(async()=>({token:'secret'}));
 const store=new WorkbenchStore({request,subscribe:receive=>{feeds.push(receive);return()=>{};},retry:()=>()=>{}});
 const state=snapshot({sidebar:projectSidebar()});await store.start();feeds[0]({kind:'snapshot',state});
 const {result}=renderHook(()=>useWorkbenchRuntime(store,state,true,false));
 act(()=>result.current.runtime.thread.composer.setText('源草稿'));
 request.mockRejectedValueOnce(new Error('拒绝切换'));
 await act(async()=>{await expect(store.newChat('q',true)).rejects.toThrow('拒绝切换');});
 expect(result.current.runtime.thread.composer.getState().text).toBe('源草稿');
 feeds.at(-1)!({kind:'snapshot',state});
 await act(()=>store.newChat());
 expect(result.current.runtime.thread.composer.getState().text).toBe('源草稿');
 const neutral=snapshot({project:{id:'neutral',name:'无项目',path:'/neutral',projectless:true},session:{id:'new',title:'新聊天'},sidebar:projectSidebar(),navigation_id:(request.mock.calls.at(-1)![1] as {navigation_id:string}).navigation_id});
 act(()=>feeds.at(-1)!({kind:'snapshot',state:neutral}));
 const next=renderHook(()=>useWorkbenchRuntime(store,neutral,true,false));
 expect(next.result.current.runtime.thread.composer.getState().text).toBe('');
});
it('does not let a delayed picker transfer replace newly typed target input',async()=>{
 const feeds:((event:ServerEvent)=>void)[]=[];const request=vi.fn<Transport['request']>(async()=>({token:'secret'}));
 const store=new WorkbenchStore({request,subscribe:receive=>{feeds.push(receive);return()=>{};},retry:()=>()=>{}});
 const state=snapshot({sidebar:projectSidebar()});await store.start();feeds[0]({kind:'snapshot',state});
 const source=renderHook(()=>useWorkbenchRuntime(store,state,true,false));act(()=>source.result.current.runtime.thread.composer.setText('旧文字'));
 await act(()=>store.newChat('q',true));source.unmount();
 const target=snapshot({navigation_id:(request.mock.calls.at(-1)![1] as {navigation_id:string}).navigation_id,project:{id:'q',name:'q',path:'/q'},session:{id:'new',title:'new'},sidebar:projectSidebar()});
 const next=renderHook(()=>useWorkbenchRuntime(store,target,true,false));act(()=>next.result.current.runtime.thread.composer.setText('新输入'));
 act(()=>feeds.at(-1)!({kind:'snapshot',state:target}));
 expect(next.result.current.runtime.thread.composer.getState().text).toBe('新输入');
});

it('recovers the original File from a failed complete upload before moving its draft to another project',async()=>{
 const feeds:((event:ServerEvent)=>void)[]=[];
 const request=vi.fn<Transport['request']>(async()=>({token:'secret'}));
 const upload=vi.fn(async()=>({id:'source-upload',kind:'file' as const,name:'notes.txt',content_type:'text/plain',size:4,content_url:'/api/attachments/source-upload/content',download_url:'/api/attachments/source-upload/download'}));
 const store=new WorkbenchStore({request,upload,subscribe:receive=>{feeds.push(receive);return()=>{};},retry:()=>()=>{}});
 const source=snapshot({sidebar:projectSidebar()});await store.start();feeds[0]({kind:'snapshot',state:source});
 vi.spyOn(store,'send').mockRejectedValue(new Error('HTTP拒绝'));
 const first=renderHook(()=>useWorkbenchRuntime(store,source,true,false));const composer=first.result.current.runtime.thread.composer;
 const file=new File(['data'],'notes.txt',{type:'text/plain'});await act(()=>composer.addAttachment(file));act(()=>composer.setText('保留原文件'));
 await act(()=>composer.send());await waitFor(()=>expect(composer.getState().text).toBe('保留原文件'));
 expect(composer.getState().attachments[0].id).toBe('source-upload');expect(composer.getState().attachments[0].file).toBeUndefined();
 await act(()=>store.newChat('q',true));first.unmount();
 const target=snapshot({navigation_id:(request.mock.calls.at(-1)![1] as {navigation_id:string}).navigation_id,project:{id:'q',name:'q',path:'/q'},session:{id:'new',title:'new'},sidebar:projectSidebar()});feeds.at(-1)!({kind:'snapshot',state:target});
 const next=renderHook(()=>useWorkbenchRuntime(store,target,true,false));
 await waitFor(()=>expect(next.result.current.runtime.thread.composer.getState().attachments).toHaveLength(1));
 const attachment=next.result.current.runtime.thread.composer.getState().attachments[0];expect(attachment.file).toBe(file);expect(attachment.id).not.toBe('source-upload');
});
