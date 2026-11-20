// @vitest-environment jsdom
import {act,cleanup,renderHook,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {WorkbenchStore,browserTransport,type Transport} from '../workbench/client';
import {useWorkbenchRuntime} from './runtime';
import {mapMessages} from './messages';
import {snapshot} from '../workbench/fixtures';
import type {AttachmentDescriptor,ServerEvent,Snapshot} from '../workbench/types';

const image:AttachmentDescriptor={id:'uploaded-image',kind:'image',name:'shot.png',content_type:'image/png',size:4,content_url:'/api/attachments/image/content?project_id=p&session_id=s',download_url:'/api/attachments/image/download?project_id=p&session_id=s'};
function setup(state:Snapshot=snapshot()){
 let receive!:(event:ServerEvent)=>void;
 const requests:Array<{path:string;body?:unknown}>=[];
 let failSend=false;
 const upload=vi.fn<NonNullable<Transport['upload']>>(async()=>image);
 const histories=new Map<string,Snapshot>();
 const messages=()=>requests.filter(request=>request.path==='/messages');
 const io:Transport={upload,request:async(path,body)=>{
  requests.push({path,body});
  if(path==='/messages'&&failSend)throw new Error('offline');
  if(path.startsWith('/session-view?')){const query=new URL(path,'http://local').searchParams;return histories.get(JSON.stringify([query.get('project_id'),query.get('conversation_id')??query.get('session_id')]))??state;}
  return {token:'secret'};
 },subscribe:callback=>{receive=callback;return()=>{};},retry:()=>()=>{}};
 const store=new WorkbenchStore(io);
 return {store,requests,messages,upload,open:async(next:Snapshot)=>{histories.set(JSON.stringify([next.project.id,next.session.id]),next);await store.openConversation(next.project.id,next.session.id);},connect:async()=>{await store.start();receive({kind:'snapshot',state});},feed:(next:Snapshot)=>receive({kind:'snapshot',state:next}),fail:()=>{failSend=true;}};
}
afterEach(()=>{cleanup();vi.unstubAllGlobals();});

it('sends binary upload bodies without JSON encoding and surfaces HTTP upload errors',async()=>{
 const fetch=vi.fn().mockResolvedValue({ok:true,json:async()=>image});vi.stubGlobal('fetch',fetch);
 const file=new File(['shot'],'shot.png',{type:'image/png'});
 await expect(browserTransport.upload!('/attachments?project_id=p&session_id=s&name=shot.png',file,'secret')).resolves.toEqual(image);
 expect(fetch).toHaveBeenCalledWith('/api/attachments?project_id=p&session_id=s&name=shot.png',expect.objectContaining({method:'POST',headers:{'Content-Type':'image/png','X-Raccoon-Token':'secret'},body:file}));
 fetch.mockResolvedValueOnce({ok:false,status:413,json:async()=>({detail:'too large'})});
 await expect(browserTransport.upload!('/attachments',file,'secret')).rejects.toThrow('too large');
});

it('uploads raw file bytes with the scope and token and rejects a late response after session switch',async()=>{
 const app=setup();await app.connect();const file=new File(['shot'],'shot.png',{type:'image/png'});
 await expect(app.store.uploadAttachment(file,{projectId:'p',sessionId:'s'})).resolves.toEqual(image);
 expect(app.upload).toHaveBeenCalledWith('/attachments?project_id=p&session_id=s&name=shot.png',file,'secret');
 let finish!:(value:AttachmentDescriptor)=>void;
 app.upload.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
 const late=app.store.uploadAttachment(file,{projectId:'p',sessionId:'s'});
 await app.open(snapshot({session:{id:'other',title:'其他'}}));finish(image);
 await expect(late).rejects.toThrow('会话');
});

it('blocks navigation during parallel uploads and releases the selection lock after the last upload',async()=>{
 const app=setup();await app.connect();const file=new File(['shot'],'shot.png',{type:'image/png'});
 const finishes:Array<(value:AttachmentDescriptor)=>void>=[];
 app.upload.mockImplementation(()=>new Promise(resolve=>{finishes.push(resolve);}));
 const first=app.store.uploadAttachment(file,{projectId:'p',sessionId:'s'});
 const second=app.store.uploadAttachment(file,{projectId:'p',sessionId:'s'});
 expect(app.store.get().mutating).toBe(true);
 await expect(app.store.navigate('/sessions')).rejects.toThrow();
 finishes[0](image);await first;expect(app.store.get().mutating).toBe(true);
 finishes[1](image);await second;expect(app.store.get().mutating).toBe(false);
 expect(app.requests.some(request=>request.path==='/sessions')).toBe(false);
});

it('uses the official composer for attachment-only sends and removing drafts never deletes server history',async()=>{
 const app=setup();await app.connect();const {result}=renderHook(()=>useWorkbenchRuntime(app.store,snapshot(),true,false));
 const composer=result.current.runtime.thread.composer;
 await act(()=>composer.addAttachment(new File(['shot'],'shot.png',{type:'image/png'})));
 expect(composer.getState().attachments).toHaveLength(1);
 await act(()=>composer.getAttachmentByIndex(0).remove());
 expect(composer.getState().attachments).toHaveLength(0);
 expect(app.requests.some(request=>request.path.includes('attachments'))).toBe(false);
 await act(()=>composer.addAttachment(new File(['shot'],'shot.png',{type:'image/png'})));
 await act(()=>composer.send());
 await waitFor(()=>expect(app.messages()[0]?.body).toMatchObject({message:'',attachment_ids:['uploaded-image'],project_id:'p',session_id:'s',mode:'auto',submission_id:expect.any(String)}));
 expect(app.upload).toHaveBeenCalledTimes(1);
});

it('preserves files and text on message failure and restores without uploading another copy',async()=>{
 const app=setup();await app.connect();app.fail();const {result}=renderHook(()=>useWorkbenchRuntime(app.store,snapshot(),true,false));
 const composer=result.current.runtime.thread.composer;
 await act(()=>composer.addAttachment(new File(['shot'],'shot.png',{type:'image/png'})));
 act(()=>composer.setText('keep draft'));await act(()=>composer.send());
 await waitFor(()=>expect(composer.getState().text).toBe('keep draft'));
 expect(composer.getState().attachments).toEqual([expect.objectContaining({id:'uploaded-image',name:'shot.png'})]);
 expect(composer.getState().inTransit).toHaveLength(0);
 expect(app.store.get().error).toContain('offline');
 app.feed(snapshot());await act(()=>composer.send());
 await waitFor(()=>expect(app.messages()).toHaveLength(2));
 expect(app.upload).toHaveBeenCalledTimes(1);
});

it('retains failed attachments after the composer unmounts without copying them into another session',async()=>{
 const app=setup();await app.connect();
 let reject!:(reason:Error)=>void;
 const sending=vi.spyOn(app.store,'send').mockImplementation(()=>new Promise((_,fail)=>{reject=fail;}));
 const first=renderHook(()=>useWorkbenchRuntime(app.store,snapshot(),true,false));
 const composer=first.result.current.runtime.thread.composer;
 await act(()=>composer.addAttachment(new File(['shot'],'shot.png',{type:'image/png'})));
 act(()=>composer.setText('detached draft'));await act(()=>composer.send());
 await waitFor(()=>expect(sending).toHaveBeenCalledTimes(1));
 first.unmount();await act(async()=>{reject(new Error('offline'));});
 const other=renderHook(()=>useWorkbenchRuntime(app.store,snapshot({session:{id:'other',title:'其他'}}),true,false));
 expect(other.result.current.failedDraft).toBeNull();other.unmount();
 const restored=renderHook(()=>useWorkbenchRuntime(app.store,snapshot(),true,false));
 expect(restored.result.current.failedDraft).toBeTruthy();
 await act(()=>restored.result.current.restoreDraft());
 expect(restored.result.current.runtime.thread.composer.getState().text).toBe('detached draft');
 expect(restored.result.current.runtime.thread.composer.getState().attachments).toEqual([expect.objectContaining({id:'uploaded-image'})]);
 expect(app.upload).toHaveBeenCalledTimes(1);
});

it('retains a draft restored after mounted send failure when switching away and back',async()=>{
 const app=setup();await app.connect();app.fail();
 const first=renderHook(()=>useWorkbenchRuntime(app.store,snapshot(),true,false));
 const composer=first.result.current.runtime.thread.composer;
 await act(()=>composer.addAttachment(new File(['shot'],'shot.png',{type:'image/png'})));
 act(()=>composer.setText('return later'));await act(()=>composer.send());
 await waitFor(()=>expect(composer.getState().text).toBe('return later'));
 expect(composer.getState().attachments).toHaveLength(1);
 expect(first.result.current.failedDraft).toBeNull();first.unmount();
 const restored=renderHook(()=>useWorkbenchRuntime(app.store,snapshot(),true,false));
 expect(restored.result.current.failedDraft).toBeTruthy();
 await act(()=>restored.result.current.restoreDraft());
 expect(restored.result.current.runtime.thread.composer.getState().text).toBe('return later');
 expect(restored.result.current.runtime.thread.composer.getState().attachments).toHaveLength(1);
 expect(app.upload).toHaveBeenCalledTimes(1);
});

it.each(['edited text',''])('keeps recovered draft edits (%s) after switching away and back',async(text)=>{
 const app=setup();await app.connect();app.fail();
 const first=renderHook(()=>useWorkbenchRuntime(app.store,snapshot(),true,false));
 const composer=first.result.current.runtime.thread.composer;
 await act(()=>composer.addAttachment(new File(['shot'],'shot.png',{type:'image/png'})));
 act(()=>composer.setText('original text'));await act(()=>composer.send());
 await waitFor(()=>expect(composer.getState().text).toBe('original text'));
 await act(()=>composer.getAttachmentByIndex(0).remove());act(()=>composer.setText(text));
 first.unmount();
 const other=renderHook(()=>useWorkbenchRuntime(app.store,snapshot({session:{id:'other',title:'其他'}}),true,false));other.unmount();
 const restored=renderHook(()=>useWorkbenchRuntime(app.store,snapshot(),true,false));
 if(text)await act(()=>restored.result.current.restoreDraft());
 else expect(restored.result.current.failedDraft).toBeNull();
 expect(restored.result.current.runtime.thread.composer.getState().text).toBe(text);
 expect(restored.result.current.runtime.thread.composer.getState().attachments).toHaveLength(0);
});

it('keeps the recovery backup while a retry empties the composer before unmount',async()=>{
 const app=setup();await app.connect();app.fail();
 const first=renderHook(()=>useWorkbenchRuntime(app.store,snapshot(),true,false));
 const composer=first.result.current.runtime.thread.composer;
 await act(()=>composer.addAttachment(new File(['shot'],'shot.png',{type:'image/png'})));
 act(()=>composer.setText('retry later'));await act(()=>composer.send());
 await waitFor(()=>expect(composer.getState().text).toBe('retry later'));
 let reject!:(error:Error)=>void;
 vi.spyOn(app.store,'send').mockImplementation(()=>new Promise((_,fail)=>{reject=fail;}));
 await act(()=>composer.send());
 expect(composer.getState().text).toBe('');first.unmount();
 // The still-pending retry must not replace the backup with its transient empty state.
 const restored=renderHook(()=>useWorkbenchRuntime(app.store,snapshot(),true,false));
 await act(()=>restored.result.current.restoreDraft());
 expect(restored.result.current.runtime.thread.composer.getState().text).toBe('retry later');
 expect(restored.result.current.runtime.thread.composer.getState().attachments).toHaveLength(1);
 await act(async()=>{reject(new Error('offline'));});
});

it('keeps a partially uploaded message intact and retries only the failed file on explicit send',async()=>{
 const app=setup();await app.connect();
 const uploaded:string[]=[];let failSecond=true;
 app.upload.mockImplementation(async(_path:string,file:File)=>{
  uploaded.push(file.name);
  if(file.name==='second.txt'&&failSecond)throw new Error('upload interrupted');
  return {...image,id:file.name,kind:'file',name:file.name,content_type:'text/plain'};
 });
 const {result}=renderHook(()=>useWorkbenchRuntime(app.store,snapshot(),true,false));
 const composer=result.current.runtime.thread.composer;
 await act(()=>composer.addAttachment(new File(['one'],'first.txt',{type:'text/plain'})));
 await act(()=>composer.addAttachment(new File(['two'],'second.txt',{type:'text/plain'})));
 act(()=>composer.setText('keep both'));
 const log=vi.spyOn(console,'error').mockImplementation(()=>{});
 await act(()=>composer.send());log.mockRestore();
 expect(composer.getState().text).toBe('keep both');
 expect(composer.getState().attachments.map(file=>file.name)).toEqual(['first.txt','second.txt']);
 expect(app.store.get().error).toContain('upload interrupted');
 expect(app.messages()).toHaveLength(0);
 expect(uploaded).toEqual(['first.txt','second.txt']);
 failSecond=false;await act(()=>composer.send());
 await waitFor(()=>expect(app.messages()[0]?.body).toMatchObject({message:'keep both',attachment_ids:['first.txt','second.txt'],project_id:'p',session_id:'s',mode:'auto',submission_id:expect.any(String)}));
 expect(uploaded).toEqual(['first.txt','second.txt','second.txt']);
});

it('projects persisted attachments into the official message cards while a turn is busy and after refresh',()=>{
 for(const busy of [false,true]){
  const result=mapMessages(snapshot({busy,messages:[{id:'u',role:'user',content:'',attachments:[image,{...image,id:'doc',kind:'file',name:'notes.txt',content_type:'text/plain'}]}]}));
  expect(result[0].attachments).toEqual([
   expect.objectContaining({id:'uploaded-image',type:'image',content:[{type:'image',image:image.content_url,filename:'shot.png'}],status:{type:'complete'}}),
   expect.objectContaining({id:'doc',type:'file',name:'notes.txt'}),
  ]);
  expect(result[0].content).toEqual([]);
 }
});

it('shows size/count errors and respects explicit non-vision capability before uploading',async()=>{
 const app=setup(snapshot({input_capabilities:{images:false,pdf:false}}));await app.connect();
 const {result}=renderHook(()=>useWorkbenchRuntime(app.store,snapshot({input_capabilities:{images:false,pdf:false}}),true,false));
 const composer=result.current.runtime.thread.composer;
 const huge=new File(['x'],'huge.bin');Object.defineProperty(huge,'size',{value:20*1024*1024+1});
 await expect(act(()=>composer.addAttachment(huge))).rejects.toThrow('20');
 expect(app.store.get().error).toContain('20');
 await act(()=>composer.addAttachment(new File(['shot'],'shot.png',{type:'image/png'})));
 const log=vi.spyOn(console,'error').mockImplementation(()=>{});
 await act(()=>composer.send());log.mockRestore();
 expect(app.messages()).toHaveLength(0);
 expect(app.store.get().error).toContain('图片');
 expect(composer.getState().attachments).toHaveLength(1);
 expect(app.upload).not.toHaveBeenCalled();
});

it('releases sent file reservations between turns and counts concurrent selection against message limits',async()=>{
 const app=setup();await app.connect();const {result}=renderHook(()=>useWorkbenchRuntime(app.store,snapshot(),true,false));
 const composer=result.current.runtime.thread.composer;
 for(let turn=0;turn<2;turn++){
  const files=Array.from({length:16},(_,index)=>new File(['x'],`file-${turn}-${index}.txt`));
  app.upload.mockImplementation(async(_path:string,file:File)=>({...image,id:file.name,kind:'file',name:file.name}));
  await act(()=>Promise.all(files.map(file=>composer.addAttachment(file))));
  await expect(act(()=>composer.addAttachment(new File(['x'],'excess.txt')))).rejects.toThrow('16');
  await act(()=>composer.send());
  await waitFor(()=>expect(app.messages()).toHaveLength(turn+1));
  app.feed(snapshot());
 }
 expect(app.upload).toHaveBeenCalledTimes(32);
});

it('treats common raster extensions with missing MIME as image previews and checks total bytes before upload',async()=>{
 const app=setup();await app.connect();const {result}=renderHook(()=>useWorkbenchRuntime(app.store,snapshot(),true,false));
 const composer=result.current.runtime.thread.composer;
 await act(()=>composer.addAttachment(new File(['shot'],'screenshot.PNG')));
 expect(composer.getState().attachments[0].type).toBe('image');
 await act(()=>composer.clearAttachments());
 const files=Array.from({length:3},(_,index)=>{const file=new File(['x'],`${index}.bin`);Object.defineProperty(file,'size',{value:20*1024*1024});return file;});
 await act(()=>composer.addAttachment(files[0]));await act(()=>composer.addAttachment(files[1]));
 await expect(act(()=>composer.addAttachment(files[2]))).rejects.toThrow('40');
 expect(app.upload).not.toHaveBeenCalled();
});
