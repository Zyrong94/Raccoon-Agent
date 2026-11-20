"use client";

import {useRef,useSyncExternalStore} from 'react';
import {Assistant} from '@/components/workbench/assistant';
import {BackendModelPicker} from '@/components/chat/model-picker';
import {PermissionPicker} from '@/components/chat/permission-picker';
import {TooltipProvider} from '@/components/ui/tooltip';
import {Button} from '@/components/ui/button';
import {PendingInput} from '../pending-input';
import {useWorkbench} from '@/lib/workbench/use-workbench';
import {useWorkbenchThreadListRuntime,loadingSnapshot} from '@/lib/chat/runtime';
import type {WorkbenchStore} from '@/lib/workbench/client';
import type {Snapshot} from '@/lib/workbench/types';

type WorkbenchProps={store:WorkbenchStore;state:Snapshot;connected:boolean;mutating:boolean;navigating:boolean;error:string};
type WorkbenchPresentation=WorkbenchProps&ReturnType<typeof useWorkbenchThreadListRuntime>;

export function Workbench(){
  const {store,snapshot,connected,mutating,navigating,error}=useWorkbench();
  const previous=useRef<Snapshot|null>(null);
  if(snapshot)previous.current=snapshot;
  const scope=store.getSelectedScope();
  const state=snapshot??(previous.current&&scope?loadingSnapshot(previous.current,scope):null);
  if(!state)return <main className="flex h-dvh items-center justify-center p-6"><p role="status">{error||'正在连接 Raccoon…'}</p></main>;
  return <ConnectedWorkbench {...{store,state,connected,mutating,navigating,error}}/>;
}

function ConnectedWorkbench(props:WorkbenchProps){
  const presentation=useWorkbenchThreadListRuntime(props.store,props.state);
  return <WorkbenchAssistant {...props} {...presentation}/>;
}

function WorkbenchAssistant({store,state,connected,mutating,navigating,error,runtime,failedDraft,restoreDraft}:WorkbenchPresentation){
  const navigationBlocked=!connected;
  const blocked=!connected||navigating||state.busy||!!state.pending;
  const chatUnavailable=!!state.project.archived||!!state.project.removed;
  const settings=store.composerSettings(state);
  const settingsBlocked=!connected||navigating||chatUnavailable;
  // Keep the clicked conversation visible while its read-only history loads.
  const pendingConversation=useSyncExternalStore(store.subscribe,()=>store.get().pendingTarget,()=>store.get().pendingTarget);
  return <TooltipProvider><Assistant
    runtime={runtime}
    runStatus={state}
    blocked={blocked}
    navigationBlocked={navigationBlocked}
    pendingConversation={pendingConversation}
    chatUnavailable={chatUnavailable}
    workbench={{store,projectId:state.project.id,sessionId:state.session.id,ready:connected&&!navigating}}
    navigation={{state:state.sidebar,onNew:store.newChat,onOpen:store.openConversation,onPrewarm:store.prewarmConversation,onUpdate:store.updateConversation,onAdd:store.addProject,browse:store.browseDirectories,pickDirectory:store.pickDirectory,updateProject:store.updateProject,removeProject:store.removeProject,getArchives:store.getArchives,restoreArchivedChat:store.restoreArchivedChat}}
    canvasState={{artifacts:state.artifacts,canvas:state.canvas}}
    modelControl={<>
      <BackendModelPicker state={{model:settings.model,models:state.models}} blocked={settingsBlocked} onChange={store.selectComposerModel}/>
      <span aria-hidden className="bg-foreground/15 h-3 w-px shrink-0"/>
      <PermissionPicker value={settings.permissionMode} blocked={settingsBlocked} onChange={store.selectComposerPermission}/>
    </>}
    headerExtra={null}
    workspace={{current:state.project,items:state.workspaces.filter(project=>!project.projectless),status:navigating?'正在切换会话…':!connected?'正在重新同步…':mutating?'正在提交…':state.pending?'等待确认':state.busy?'正在运行':'已连接',onSelect:id=>{void store.activateProject(id).catch(()=>{});}}}
    notice={<>
      {navigating&&<p role="status" className="text-muted-foreground shrink-0 px-4 py-2 text-sm">正在加载聊天记录…</p>}
      {chatUnavailable&&<p role="status" className="text-muted-foreground shrink-0 px-4 py-2 text-sm">{state.project.removed?'此项目已移出工作台。打开项目后可继续聊天。':'此项目已归档。可在设置中恢复项目后继续聊天。'}</p>}
      {(error||state.error)&&<p role="alert" className="text-destructive shrink-0 px-4 py-2 text-sm whitespace-pre-wrap">{error||state.error}</p>}
      {failedDraft&&<div className="px-4 py-2 text-sm">发送失败的草稿已保留。<Button variant="link" onClick={restoreDraft}>恢复草稿</Button></div>}
    </>}
    pendingSlot={state.pending&&<div className="max-h-[45dvh] overflow-auto px-1"><PendingInput key={JSON.stringify([state.project.id,state.session.id,state.pending.id])} pending={state.pending} disabled={!connected||mutating||navigating} answer={store.answer}/></div>}
  /></TooltipProvider>;
}
