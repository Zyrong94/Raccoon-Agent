"use client";

import {useEffect,useMemo,useRef,useState} from 'react';
import {useExternalStoreRuntime,type AssistantRuntime,type Attachment,type ThreadMessageLike} from '@assistant-ui/react';
import {MainSessionStore,type MainSnapshot} from '@/lib/main-session';
import {createScopedAttachmentAdapter} from '@/lib/chat/attachments';
import {mapMessages} from '@/lib/chat/messages';

type Draft={text:string;attachments:readonly Attachment[]};
type PersonalDrafts={current:Draft;failed:Draft|null};
const savedDrafts=new WeakMap<MainSessionStore,PersonalDrafts>();
const convertMessage=(message:ThreadMessageLike)=>message;

export function useMainSessionRuntime(store:MainSessionStore,state:MainSnapshot|null,connected:boolean,mutating:boolean){
 const drafts=useMemo(()=>{
  let value=savedDrafts.get(store);
  if(!value){value={current:{text:store.get().draft,attachments:[]},failed:null};savedDrafts.set(store,value);}
  return value;
 },[store]);
 const [failedDraft,setFailedDraft]=useState(drafts.failed);
 const runtimeRef=useRef<AssistantRuntime|null>(null),alive=useRef(true),restoring=useRef(false),restored=useRef(false);
 const scope=useMemo(()=>state?{sessionId:state.session_id,generation:state.generation}:null,[state?.session_id,state?.generation]);
 const attachments=useMemo(()=>createScopedAttachmentAdapter({
  check:()=>{if(!scope)throw new Error('主会话连接未就绪');store.checkAttachmentScope(scope);},
  capabilities:()=>store.get().snapshot?.input_capabilities,
  upload:file=>{if(!scope)return Promise.reject(new Error('主会话连接未就绪'));return store.uploadAttachment(file,scope);},
  reportError:store.reportError,
 },()=>runtimeRef.current?.thread.composer.getState().attachments??[]),[store,scope]);
 const messages=useMemo(()=>state?mapMessages({project:{id:'personal-main',name:'个人主会话',path:''},session:state.session,messages:state.messages,live:state.live,activity:state.activity??[],busy:state.busy,pending:state.pending,status:state.status,error:state.error}):[],[state]);
 const blocked=!connected||mutating||!state||!!state.pending;
 const runtime=useExternalStoreRuntime({
  messages,convertMessage,isRunning:!!state?.busy,isLoading:!state,isSendDisabled:blocked,
  adapters:{attachments},
  async onNew(message){
   const text=message.content.filter(part=>part.type==='text').map(part=>part.text).join('\n');
   const sent:Draft={text,attachments:message.attachments??[]};
   try{
    if(!scope)throw new Error('主会话连接未就绪');
    await store.send(text,sent.attachments.length?{ids:sent.attachments.map(attachment=>attachment.id),scope}:undefined);
    drafts.failed=null;if(alive.current)setFailedDraft(null);
   }catch(error){
    store.reportError(error);drafts.failed=sent;
    const composer=alive.current?runtimeRef.current?.thread.composer:undefined;
    if(composer&&!composer.getState().text&&!composer.getState().attachments.length){
     composer.setText(text);
     for(const attachment of sent.attachments)await composer.addAttachment({...attachment,content:attachment.content??[]});
     drafts.failed=null;if(alive.current)setFailedDraft(null);
    }else{
     if(!alive.current)drafts.current=sent;
     else setFailedDraft(sent);
    }
    throw error;
   }
  },
  onCancel:state?.cancellable&&connected&&!mutating?async()=>{try{await store.cancel();}catch{/* The personal store exposes the error. */}}:undefined,
 });
 runtimeRef.current=runtime;
 useEffect(()=>{
  alive.current=true;
  const composer=runtime.thread.composer;
  const unsubscribe=composer.subscribe(()=>{
   if(restoring.current||!restored.current)return;
   const {text,attachments}=composer.getState();drafts.current={text,attachments};store.setDraft(text);
  });
  return()=>{alive.current=false;unsubscribe();};
 },[store,drafts,runtime]);
 useEffect(()=>{
  if(restored.current||!connected||!state)return;
  restored.current=true;restoring.current=true;
  const composer=runtime.thread.composer,saved=drafts.current;
  void (async()=>{
   try{
    if(!composer.getState().text)composer.setText(saved.text);
    if(!composer.getState().attachments.length)for(const attachment of saved.attachments){
     await composer.addAttachment(attachment.status.type!=='complete'&&attachment.file?attachment.file:{...attachment,content:attachment.content??[]});
    }
   }catch(error){store.reportError(error);}
   finally{restoring.current=false;}
  })();
 },[store,drafts,runtime,connected,state]);
 return {runtime,blocked,failedDraft,restoreDraft:async()=>{
  if(!failedDraft)return;
  const composer=runtime.thread.composer;composer.setText([composer.getState().text,failedDraft.text].filter(Boolean).join('\n'));
  for(const attachment of failedDraft.attachments)await composer.addAttachment({...attachment,content:attachment.content??[]});
  drafts.failed=null;setFailedDraft(null);
 }};
}
