// @vitest-environment jsdom
import {act,cleanup,renderHook,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {MainSessionStore,type MainSnapshot} from '@/lib/main-session';
import {useMainSessionRuntime} from './runtime';
import {attachmentUrl} from '@/lib/chat/attachments';

afterEach(()=>{cleanup();vi.unstubAllGlobals();});
const state:MainSnapshot={available:true,session:{id:'main',title:'主会话'},session_id:'main',generation:'host',messages:[],live:[],busy:false,cancellable:false,pending:null,status:'idle',error:null,input_capabilities:{images:true,pdf:true}};
const descriptor={id:'uploaded',kind:'file',name:'notes.txt',size:5,content_type:'text/plain',content_url:'/api/main-session/attachments/uploaded/content?session_id=main',download_url:'/api/main-session/attachments/uploaded/download?session_id=main'};
async function setup(){
 let failed=false;
 const request=vi.fn(async(path:string)=>{if(path==='/state')return {token:'secret'};if(path.includes('/attachments?'))return descriptor;if(path.endsWith('/messages')&&failed)throw new Error('unknown send');return state;});
 const store=new MainSessionStore(request);await store.refresh();
 const mount=()=>renderHook(()=>useMainSessionRuntime(store,store.get().snapshot,store.get().connected,store.get().mutating));
 return {store,request,mount,fail:()=>{failed=true;}};
}
it('keeps unchanged history stable while the personal live reply advances',()=>{
 const store=new MainSessionStore();
 const initial={...state,messages:[{id:'user',role:'user' as const,content:'问题'}],busy:true,live:[{content:'回答',reasoning:''}]};
 const {result,rerender}=renderHook(({snapshot})=>useMainSessionRuntime(store,snapshot,true,false),{initialProps:{snapshot:initial}});
 const history=result.current.runtime.thread.getState().messages[0];
 rerender({snapshot:{...initial,live:[{content:'回答继续',reasoning:''}]}});
 const messages=result.current.runtime.thread.getState().messages;
 expect(messages[0]).toBe(history);
 expect(messages[1].content).toEqual([expect.objectContaining({type:'text',text:'回答继续'})]);
});
it('uses official attachment selection, removal and attachment-only main sends',async()=>{
 const app=await setup();const {result}=app.mount();const composer=result.current.runtime.thread.composer;
 await act(()=>composer.addAttachment(new File(['notes'],'notes.txt',{type:'text/plain'})));
 expect(composer.getState().attachments).toHaveLength(1);await act(()=>composer.getAttachmentByIndex(0).remove());
 expect(composer.getState().attachments).toHaveLength(0);expect(app.request.mock.calls.some(call=>call[0].includes('/attachments?'))).toBe(false);
 await act(()=>composer.addAttachment(new File(['notes'],'notes.txt',{type:'text/plain'})));
 await act(()=>composer.send());
 await waitFor(()=>expect(app.request).toHaveBeenCalledWith('/main-session/messages',expect.objectContaining({message:'',attachment_ids:['uploaded'],session_id:'main',generation:'host'}),'secret'));
});
it('always sends auto regardless of SDK steer hints',async()=>{
 const app=await setup();const {result}=app.mount();const composer=result.current.runtime.thread.composer;
 act(()=>composer.setText('自动处理这条'));
 await act(()=>composer.send());
 await waitFor(()=>expect(app.request).toHaveBeenCalledWith('/main-session/messages',expect.objectContaining({message:'自动处理这条',mode:'auto'}),'secret'));
 act(()=>composer.setText('下一轮再处理'));act(()=>composer.send({steer:false}));
 await waitFor(()=>expect(app.request).toHaveBeenCalledWith('/main-session/messages',expect.objectContaining({message:'下一轮再处理',mode:'auto'}),'secret'));
 act(()=>composer.setText('下个步骤'));await act(()=>composer.send());
 await waitFor(()=>expect(app.request).toHaveBeenCalledWith('/main-session/messages',expect.objectContaining({message:'下个步骤',mode:'auto'}),'secret'));
});
it('retains unsent personal files and text after leaving and returning',async()=>{
 const app=await setup();const first=app.mount();const composer=first.result.current.runtime.thread.composer;
 act(()=>composer.setText('personal draft'));await act(()=>composer.addAttachment(new File(['notes'],'notes.txt',{type:'text/plain'})));first.unmount();
 const next=app.mount();await waitFor(()=>expect(next.result.current.runtime.thread.composer.getState().text).toBe('personal draft'));
 expect(next.result.current.runtime.thread.composer.getState().attachments).toEqual([expect.objectContaining({name:'notes.txt'})]);
 expect(app.request.mock.calls.some(call=>call[0].includes('/attachments?'))).toBe(false);
});
it('recovers failed message files without reuploading them or replacing a newer draft',async()=>{
 const app=await setup();app.fail();const {result}=app.mount();const composer=result.current.runtime.thread.composer;
 await act(()=>composer.addAttachment(new File(['notes'],'notes.txt',{type:'text/plain'})));act(()=>composer.setText('keep both'));await act(()=>composer.send());
 await waitFor(()=>expect(composer.getState().text).toBe('keep both'));
 expect(composer.getState().attachments).toEqual([expect.objectContaining({id:'uploaded',name:'notes.txt'})]);
 expect(app.request.mock.calls.filter(call=>call[0].includes('/attachments?'))).toHaveLength(1);
 expect(app.request.mock.calls.filter(call=>call[0].endsWith('/messages'))).toHaveLength(1);
});
it('keeps a newer composer draft when an earlier attachment send fails and restores explicitly',async()=>{
 let fail!:(error:Error)=>void;
 const request=vi.fn(async(path:string)=>path==='/state'?{token:'secret'}:path.includes('/attachments?')?descriptor:path.endsWith('/messages')?new Promise((_resolve,reject)=>{fail=reject;}):state);
 const store=new MainSessionStore(request);await store.refresh();const {result}=renderHook(()=>useMainSessionRuntime(store,state,true,false));
 const composer=result.current.runtime.thread.composer;
 await act(()=>composer.addAttachment(new File(['notes'],'notes.txt',{type:'text/plain'})));act(()=>{composer.setText('first');void composer.send();});
 await waitFor(()=>expect(request.mock.calls.some(call=>call[0].endsWith('/messages'))).toBe(true));
 act(()=>composer.setText('newer'));await act(async()=>{fail(new Error('unknown'));});
 expect(composer.getState().text).toBe('newer');expect(result.current.failedDraft?.attachments[0]?.id).toBe('uploaded');
 await act(()=>result.current.restoreDraft());expect(composer.getState().text).toBe('newer\nfirst');expect(composer.getState().attachments[0]?.id).toBe('uploaded');
});
it('uses raw file bytes in the browser and permits only scoped attachment URLs',async()=>{
 const fetch=vi.fn(async(path:string)=>({ok:true,json:async()=>path==='/api/state'?{token:'secret'}:path.includes('/attachments?')?descriptor:state}));vi.stubGlobal('fetch',fetch);
 const store=new MainSessionStore();await store.refresh();const file=new File(['notes'],'notes.txt',{type:'text/plain'});
 await store.uploadAttachment(file,{sessionId:'main',generation:'host'});
 expect(fetch).toHaveBeenLastCalledWith('/api/main-session/attachments?session_id=main&generation=host&name=notes.txt',expect.objectContaining({body:file,headers:{'Content-Type':'text/plain','X-Raccoon-Token':'secret'}}));
 expect(attachmentUrl(descriptor.download_url)).toBe(descriptor.download_url);
 for(const url of ['https://evil.test/api/main-session/attachments/a','//evil.test/a','/api/main-session/attachments/../../secret','/api/main-session/attachments/\\evil'])expect(attachmentUrl(url)).toBeUndefined();
});
