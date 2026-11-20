"use client";
import {useEffect,useSyncExternalStore,type ReactNode} from 'react';
import {AssistantRuntimeProvider,ComposerPrimitive,ThreadPrimitive} from '@assistant-ui/react';
import {ArrowLeftIcon,RefreshCwIcon} from 'lucide-react';
import {HistoryMessages} from '@/components/chat/history-messages';
import {ComposerFrame,ComposerInput} from '@/components/chat/composer-frame';
import {BackendModelPicker} from '@/components/chat/model-picker';
import {PermissionPicker} from '@/components/chat/permission-picker';
import {PendingInput} from '@/components/pending-input';
import {MainSessionStore,personalMainStore} from '@/lib/main-session';
import {groupSessionsStore} from '@/lib/group-sessions';
import {WorkbenchContext} from '@/components/workbench/context';
import {useMainSessionRuntime} from './runtime';
import {GroupSessionsSection} from './group-sessions';
import {CollaborationWorkspace} from '@/components/collaboration/collaboration-workspace';


export function MainSession({onClose,headerLeading,store=personalMainStore,onOpenGroups}:{onClose:()=>void;headerLeading?:ReactNode;store?:MainSessionStore;onOpenGroups?:()=>void}){
 const view=useSyncExternalStore(store.subscribe,store.get,store.get);const state=view.snapshot;
 const {runtime,failedDraft,restoreDraft}=useMainSessionRuntime(store,state,view.connected,view.mutating);
 useEffect(()=>{store.start();return()=>store.stop();},[store]);
 // Same rule as the project composer: a short in-flight write must not grey out
 // the model/permission controls, or switching modes looks like a refresh.
 const controlsBlocked=!view.connected||!state||!!state.pending||!!state.busy;
 const controls=<><BackendModelPicker state={{model:state?.model??null,models:state?.models??[]}} blocked={controlsBlocked} onChange={model=>{void store.setModel(model).catch(()=>{});}}/><span aria-hidden className="bg-foreground/15 h-3 w-px shrink-0"/><PermissionPicker value={state?.permission_mode??'ask'} blocked={controlsBlocked} onChange={store.setPermissionMode}/></>;
 return <WorkbenchContext.Provider value={{runStatus:state??undefined,blocked:controlsBlocked,modelControl:null,headerExtra:null,notice:null,pendingSlot:null}}><AssistantRuntimeProvider runtime={runtime}><section aria-label="个人主会话" className="flex h-full min-h-0 flex-col [--thread-max-width:42rem]">
  <header className="flex h-12 shrink-0 items-center gap-3 border-b px-4">{headerLeading}<button type="button" onClick={onClose} aria-label="返回项目会话" className="rounded-md p-2 hover:bg-muted"><ArrowLeftIcon className="size-4"/></button><div><h1 className="text-sm font-medium">主会话</h1><p className="text-muted-foreground text-xs">你的个人 Agent · Web 与 QQ 共享上下文</p></div><span role="status" className="text-muted-foreground ml-auto text-xs">{!view.connected?'正在连接':state?.pending?'等待你的回答':state?.busy?'正在处理':'已同步'}</span></header>
  {(view.error||state?.error)&&<div role="alert" className="mx-4 mt-3 flex items-center gap-2 rounded-lg border px-3 py-2 text-sm"><p className="flex-1">{view.error||state?.error}</p><button type="button" onClick={()=>void store.refresh()} className="inline-flex items-center gap-1 rounded px-2 py-1 hover:bg-muted"><RefreshCwIcon className="size-3"/>重新连接</button></div>}
   <div className="min-h-0 flex-1"><CollaborationWorkspace key={`${state?.session_id}:${state?.generation}`} scope={{session_id:state?.session_id??'',generation:state?.generation??''}} enabled={!!state} request={store.subagentRequest}
     extraSections={onOpenGroups?<GroupSessionsSection store={groupSessionsStore} onOpen={onOpenGroups}/>:undefined}><ThreadPrimitive.Root className="flex h-full min-h-0 flex-1 flex-col"><HistoryMessages scope={JSON.stringify([state?.session_id,state?.generation])} isEmpty={!!state&&!state.messages.length&&!state.busy}
     empty={!state?<p className="text-muted-foreground text-sm">正在同步主会话…</p>:!state.messages.length&&!state.busy?<div className="mx-auto max-w-(--thread-max-width) py-12"><h2 className="text-xl font-medium">从这里继续聊</h2><p className="text-muted-foreground mt-2 text-sm">你的消息、QQ 对话与后台结果会汇聚到这里。</p></div>:null} footer={<>
    {failedDraft&&<button type="button" className="text-muted-foreground text-left text-xs underline" onClick={()=>{void restoreDraft().catch(store.reportError);}}>恢复发送失败的草稿</button>}
    {state?.pending&&<PendingInput key={state.pending.id} pending={state.pending} disabled={!view.connected||view.mutating} answer={store.answer}/>}
    <ComposerPrimitive.Root className="relative w-full"><ComposerFrame input={<ComposerInput aria-label="主会话输入" placeholder="输入消息…"/>} controls={controls} blocked={controlsBlocked} pendingMessages={state?.pending_messages} stopping={state?.status==='stopping'} sendLabel="发送到主会话" cancelLabel="停止主会话"/></ComposerPrimitive.Root>
   </>}/></ThreadPrimitive.Root></CollaborationWorkspace></div>
 </section></AssistantRuntimeProvider></WorkbenchContext.Provider>;
}
