"use client";
import {useCallback,useEffect,useMemo,useRef,useState,useSyncExternalStore} from 'react';
import {useExternalStoreRuntime,useRemoteThreadListRuntime,useAuiState,type RemoteThreadListAdapter,type AssistantRuntime,type Attachment,type ThreadMessageLike} from '@assistant-ui/react';
import {createMessageMapper} from './messages';
import {createAttachmentAdapter} from './attachments';
import type {Snapshot} from '../workbench/types';
import type {ComposerSelection,WorkbenchStore} from '../workbench/client';
type FailedDraft={text:string;attachments:readonly Attachment[]};
const savedDrafts=new WeakMap<WorkbenchStore,Map<string,FailedDraft>>();
const draftNotices=new WeakMap<WorkbenchStore,{drafts:Map<string,FailedDraft>;restore:Map<string,()=>Promise<void>>;listeners:Set<()=>void>}>();
function noticesFor(store:WorkbenchStore){let notices=draftNotices.get(store);if(!notices){notices={drafts:new Map(),restore:new Map(),listeners:new Set()};draftNotices.set(store,notices);}return notices;}
const draftFiles=new WeakMap<WorkbenchStore,Map<string,Map<string,File>>>();
type FailedEdit={messageId:string;text:string};
const savedEdits=new WeakMap<WorkbenchStore,Map<string,FailedEdit>>();
const convertMessage=(message:ThreadMessageLike)=>message;
export function useWorkbenchRuntime(store:WorkbenchStore,state:Snapshot,connected:boolean,mutating:boolean,navigating=false){
  const scopeKey=JSON.stringify([state.project.id,state.session.id]);
  const drafts=useMemo(()=>{let drafts=savedDrafts.get(store);if(!drafts){drafts=new Map();savedDrafts.set(store,drafts);}return drafts;},[store]);
  const files=useMemo(()=>{let scopes=draftFiles.get(store);if(!scopes){scopes=new Map();draftFiles.set(store,scopes);}let files=scopes.get(scopeKey);if(!files){files=new Map();scopes.set(scopeKey,files);}return files;},[store,scopeKey]);
  const reportError=(error:unknown)=>{const selected=store.getSelectedScope();if(selected&&JSON.stringify([selected.projectId,selected.sessionId])===scopeKey)store.reportError(error);};
  const originalFile=(attachment:Attachment)=>attachment.file??files.get(attachment.id);
  const localFiles=(attachments:readonly Attachment[])=>attachments.flatMap(attachment=>{const file=originalFile(attachment);return file?[file]:[];});
  const [failedDraft,setFailedDraft]=useState<FailedDraft|null>(()=>drafts.get(scopeKey)??null);
  useEffect(()=>{const notices=noticesFor(store);if(failedDraft)notices.drafts.set(scopeKey,failedDraft);else notices.drafts.delete(scopeKey);notices.listeners.forEach(listener=>listener());},[store,scopeKey,failedDraft]);
  const pendingFailedDraft=useRef(failedDraft);pendingFailedDraft.current=failedDraft;
  const sending=useRef(false);
  const mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  const runtimeRef=useRef<AssistantRuntime|null>(null);
  const scope=useMemo(()=>({projectId:state.project.id,sessionId:state.session.id}),[state.project.id,state.session.id]);
  const attachments=useMemo(()=>createAttachmentAdapter(store,scope,()=>runtimeRef.current?.thread.composer.getState().attachments??[]),[store,scope]);
  const mapMessages=useMemo(createMessageMapper,[]);
  const messages=useMemo(()=>mapMessages(state),[mapMessages,state.project.id,state.session.id,state.messages,state.live,state.activity,state.busy,state.pending,state.status,state.error,state.revisable_message_ids]);
  const inactive=!!state.project.archived||!!state.project.removed;
  // Feeds isSendDisabled only; the visible composer controls use their own flag
  // (workbench `blocked`, which excludes `mutating`) so a short write does not
  // grey them out.
  const blocked=!connected||mutating||navigating||state.busy&&!state.cancellable&&state.status!=='stopping'||!!state.pending||inactive;
  const edits=useMemo(()=>{let value=savedEdits.get(store);if(!value){value=new Map();savedEdits.set(store,value);}return value;},[store]);
  const [failedEdit,setFailedEdit]=useState<FailedEdit|null>(()=>edits.get(scopeKey)??null);
  const runtime=useExternalStoreRuntime({
    messages,convertMessage,isLoading:navigating,isRunning:state.busy&&(state.cancellable||state.status==='stopping'),isSendDisabled:blocked,
    async onNew(message){
      sending.current=true;
      const text=message.content.filter(p=>p.type==='text').map(p=>p.text).join('\n');
      const sentAttachments=message.attachments??[];
      for(const attachment of sentAttachments){const file=originalFile(attachment);if(file)files.set(attachment.id,file);}
      try{
        const attachmentPayload=sentAttachments.length?{ids:sentAttachments.map(attachment=>attachment.id),scope}:undefined;
        const selection=message.runConfig?.custom?.selection as ComposerSelection|undefined;
        await store.send(text,attachmentPayload,scope,selection);
        drafts.delete(scopeKey);if(mounted.current)setFailedDraft(null);
      }
      catch(error){
        if(store.get().snapshot?.project.id===scope.projectId&&store.get().snapshot?.session.id===scope.sessionId&&!store.get().error)reportError(error);
        const draft={text,attachments:sentAttachments};
        drafts.set(scopeKey,draft);
        // The official composer detaches text before onNew resolves.
        const composer=mounted.current?runtimeRef.current?.thread.composer:undefined;
        if(composer&&!composer.getState().text&&!composer.getState().attachments.length){
          composer.setText(text);
          for(const attachment of sentAttachments)await composer.addAttachment({...attachment,content:attachment.content??[]});
        }else if(mounted.current)setFailedDraft(draft);
        // A rejected callback clears the SDK's optimistic attachment row.
        throw error;
      }
      finally{sending.current=false;}
    },
    onCancel:!navigating&&state.busy&&state.cancellable?async()=>{try{await store.cancel(scope);}catch{/* store exposes the error */}}:undefined,
    onEdit:state.history_revision&&!inactive&&!navigating?async message=>{
      const source=message.sourceId;
      const text=message.content.filter(part=>part.type==='text').map(part=>part.text).join('\n');
      if(!source||!state.messages.some(value=>value.id===source&&value.role==='user')||state.revisable_message_ids!==undefined&&!state.revisable_message_ids.includes(source))throw new Error('编辑目标已经失效，请重新同步');
      try{await store.editMessage(source,text,scope);edits.delete(scopeKey);if(mounted.current)setFailedEdit(null);}
      catch(error){
        const draft={messageId:source,text};edits.set(scopeKey,draft);
        if(mounted.current){reportError(error);setFailedEdit(draft);}
        throw error;
      }
    }:undefined,
    onReload:state.history_revision&&!inactive&&!navigating?async parentId=>{
      if(!parentId||!state.messages.some(message=>message.id===parentId&&message.role==='user')||state.revisable_message_ids!==undefined&&!state.revisable_message_ids.includes(parentId))throw new Error('找不到回答对应的用户消息，请重新同步');
      try{await store.regenerateMessage(parentId,scope);}catch(error){if(mounted.current)reportError(error);throw error;}
    }:undefined,
    adapters:{attachments},
  });
  runtimeRef.current=runtime;
  useEffect(()=>store.registerComposerDraft({scope,
    capture:()=>{const composer=runtime.thread.composer.getState();return {text:composer.text,files:localFiles(composer.attachments)};},
    restore:async(draft,expected)=>{
      const composer=runtime.thread.composer;
      const current=composer.getState();
      const currentFiles=localFiles(current.attachments);
      const matches=expected&&current.text===expected.text&&currentFiles.length===expected.files.length&&currentFiles.every((file,index)=>file===expected.files[index]);
      if(!mounted.current||!matches&&(current.text||current.attachments.length))return;
      if(matches&&draft.text===current.text&&draft.files.length===currentFiles.length&&draft.files.every((file,index)=>file===currentFiles[index]))return;
      drafts.delete(scopeKey);setFailedDraft(null);files.clear();
      composer.setText(draft.text);
      await composer.clearAttachments();
      for(const file of draft.files){
        if(!mounted.current||store.get().snapshot?.project.id!==scope.projectId||store.get().snapshot?.session.id!==scope.sessionId)return;
        try{await composer.addAttachment(file);}catch(error){if(mounted.current)reportError(error);return;}
      }
    },
  }),[store,runtime,scope]);
  useEffect(()=>{
    if(!failedEdit||blocked||!state.messages.some(message=>message.id===failedEdit.messageId&&message.role==='user'))return;
    const composer=runtime.thread.getMessageById(failedEdit.messageId).composer;
    if(composer.getState().isEditing)return;
    composer.beginEdit();composer.setText(failedEdit.text);
    edits.delete(scopeKey);setFailedEdit(null);
  },[failedEdit,blocked,state.messages,runtime,edits,scopeKey]);
  useEffect(()=>()=>{
    // Only refresh an already recovered failure, never normal or in-flight drafts.
    if(!drafts.has(scopeKey)||pendingFailedDraft.current||sending.current)return;
    const {text,attachments}=runtime.thread.composer.getState();
    if(text||attachments.length)drafts.set(scopeKey,{text,attachments});else drafts.delete(scopeKey);
  },[drafts,scopeKey,runtime]);
  const restoreDraft=useCallback(async()=>{if(!failedDraft)return;const composer=runtime.thread.composer;const current=composer.getState().text;composer.setText([current,failedDraft.text].filter(Boolean).join('\n'));for(const attachment of failedDraft.attachments)await composer.addAttachment({...attachment,content:attachment.content??[]});setFailedDraft(null);},[failedDraft,runtime]);
  useEffect(()=>{const notices=noticesFor(store);notices.restore.set(scopeKey,restoreDraft);return()=>{if(notices.restore.get(scopeKey)===restoreDraft)notices.restore.delete(scopeKey);};},[store,scopeKey,restoreDraft]);
  return {runtime,failedDraft,restoreDraft};
}

export const threadScopeId=(scope:{projectId:string;sessionId:string})=>JSON.stringify([scope.projectId,scope.sessionId]);
function decodeScope(id:string):{projectId:string;sessionId:string}{
  const value:unknown=JSON.parse(id);
  if(!Array.isArray(value)||value.length!==2||value.some(part=>typeof part!=='string'||!part))throw new Error('无效会话');
  return {projectId:value[0],sessionId:value[1]};
}
export function loadingSnapshot(previous:Snapshot,scope:{projectId:string;sessionId:string}):Snapshot{
  const project=previous.sidebar.projects.find(item=>item.id===scope.projectId);
  const conversation=project?.conversations.find(item=>item.session_id===scope.sessionId);
  return {...previous,project:project?{id:project.id,name:project.name,path:project.path,...(project.projectless?{projectless:true}:{})}:{id:scope.projectId,name:'项目',path:''},session:{id:scope.sessionId,title:conversation?.title??'正在加载…'},messages:[],live:[],activity:[],run_phase:null,running_tool_call_ids:[],artifacts:[],canvas:null,pending:null,pending_messages:[],error:null,busy:false,cancellable:false,history_revision:undefined,revisable_message_ids:[],versions:undefined,model:null,models:[],effort:undefined,input_capabilities:undefined,sidebar:{...previous.sidebar,current_conversation_id:conversation?.id??null,new_chat:false}};
}

export function useWorkbenchThreadListRuntime(store:WorkbenchStore,state:Snapshot){
  const latest=useRef(state);latest.current=state;
  const adapter=useMemo<RemoteThreadListAdapter>(()=>{
    const metadata=new Map<string,Awaited<ReturnType<RemoteThreadListAdapter['fetch']>>>();
    const refresh=()=>{
      const current=store.get().snapshot??latest.current;
      for(const project of current.sidebar.projects){for(const item of project.conversations){
        const remoteId=threadScopeId({projectId:project.id,sessionId:item.session_id});
        metadata.set(remoteId,{remoteId,status:item.archived?'archived':'regular',title:item.title||'新会话',custom:{pinned:item.pinned?'true':'false'},...(item.updated_at&&Number.isFinite(Date.parse(item.updated_at))?{lastMessageAt:new Date(item.updated_at)}:{})});
      }}
      const id=threadScopeId({projectId:current.project.id,sessionId:current.session.id});
      if(!metadata.has(id))metadata.set(id,{remoteId:id,status:'regular',title:current.session.title||'新会话'});
      return current;
    };
    const conversation=(id:string)=>{const scope=decodeScope(id);const current=refresh();const item=current.sidebar.projects.find(project=>project.id===scope.projectId)?.conversations.find(item=>item.session_id===scope.sessionId);return {scope,item};};
    const requireProject=(projectId:string)=>{if(store.getSelectedScope()?.projectId!==projectId)throw new Error('项目已切换，请重新打开聊天后继续');};
    const update=async(id:string,changes:{title?:string;pinned?:boolean;archived?:boolean})=>{const {scope,item}=conversation(id);if(item)await store.updateConversation(scope.projectId,item.id,changes);else {requireProject(scope.projectId);await store.updateSession(scope.sessionId,changes);}};
    return {
      async list(){refresh();return {threads:[...metadata.values()]};},
      async fetch(id){refresh();const known=metadata.get(id);if(known)return known;const scope=decodeScope(id);const snapshot=store.getScope(scope);const item={remoteId:id,status:'regular' as const,title:snapshot?.session.title??'新会话'};metadata.set(id,item);return item;},
      rename:(id,title)=>update(id,{title}),updateCustom:(id,custom)=>update(id,{pinned:custom?.pinned==='true'}),archive:id=>update(id,{archived:true}),
      // Omitting unarchive keeps the SDK from writing when an archived thread is selected.
      async delete(id){const {scope}=conversation(id);requireProject(scope.projectId);await store.deleteSession(scope.sessionId);metadata.delete(id);},
      async initialize(){await store.newChat();const current=store.getSelectedScope();if(!current)throw new Error('新聊天尚未就绪');return {remoteId:threadScopeId(current)};},
      async generateTitle(){return new ReadableStream({start(controller){controller.close();}});},
    };
  },[store]);
  const runtimeHook=useMemo(()=>function useScopedRuntime(){
    const remoteId=useAuiState(value=>value.threadListItem.remoteId);
    const scope=useMemo(()=>remoteId?decodeScope(remoteId):{projectId:latest.current.project.id,sessionId:latest.current.session.id},[remoteId]);
    const subscribe=useCallback((listener:()=>void)=>store.subscribeScope(scope,listener),[scope]);
    // The SDK's temporary new thread must not inherit history and initialize it.
    const read=useCallback(()=>remoteId?store.getScope(scope):null,[remoteId,scope]);
    const snapshot=useSyncExternalStore(subscribe,read,read);
    const connected=useSyncExternalStore(store.subscribe,()=>store.get().connected,()=>store.get().connected);
    const readMutating=()=>{const current=store.getSelectedScope();return current?.projectId===scope.projectId&&current.sessionId===scope.sessionId&&store.get().mutating;};
    const mutating=useSyncExternalStore(store.subscribe,readMutating,readMutating);
    const fallback=useMemo(()=>loadingSnapshot(latest.current,scope),[scope]);
    return useWorkbenchRuntime(store,snapshot??fallback,connected,mutating,!snapshot).runtime;
  },[store]);
  const scope=store.getSelectedScope()??{projectId:state.project.id,sessionId:state.session.id};
  const runtime=useRemoteThreadListRuntime({adapter,runtimeHook,threadId:threadScopeId(scope),onThreadIdChange:id=>{
    if(!id){void store.newChat().catch(()=>{});return;}
    const target=decodeScope(id),current=store.getSelectedScope();
    if(current&&threadScopeId(current)===id)return;
    const directory=(store.get().snapshot??latest.current).sidebar;
    const item=directory.projects.find(project=>project.id===target.projectId)?.conversations.find(item=>item.session_id===target.sessionId);
    if(item)void store.openConversation(target.projectId,item.id).catch(()=>{});
    else void store.navigate(`/sessions/${encodeURIComponent(target.sessionId)}/activate`).catch(()=>{});
  }});
  useEffect(()=>{void runtime.threads.reload().catch(()=>{});},[runtime,state.sidebar,state.sessions]);
  const notices=noticesFor(store),key=threadScopeId(scope);
  const subscribeNotices=useCallback((listener:()=>void)=>{notices.listeners.add(listener);return()=>{notices.listeners.delete(listener);};},[notices]);
  const readNotice=useCallback(()=>notices.drafts.get(key)??null,[notices,key]);
  const failedDraft=useSyncExternalStore(subscribeNotices,readNotice,readNotice);
  const restoreDraft=useCallback(async()=>{
    if(!failedDraft)return;
    await notices.restore.get(key)?.();
  },[failedDraft,notices,key]);
  return {runtime,failedDraft,restoreDraft};
}
