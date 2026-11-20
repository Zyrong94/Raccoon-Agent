"use client";

import {useContext,useSyncExternalStore} from 'react';
import {ChevronLeftIcon,ChevronRightIcon,RefreshCwIcon} from 'lucide-react';
import {WorkbenchContext} from '../workbench/context';

const emptySubscribe=()=>()=>{};
const emptyView=()=>null;

// Arrow steppers match the icon row they sit in; the count carries the state.
const versionButtonClass="text-muted-foreground/70 hover:text-foreground disabled:pointer-events-none disabled:opacity-40 grid size-5 place-items-center rounded transition-colors";

export function MessageVersions({showRetry=true}:{showRetry?:boolean}={}){
 const integration=useContext(WorkbenchContext);
 const workbench=integration?.workbench,blocked=integration?.blocked??false;
 const view=useSyncExternalStore(workbench?.store.subscribe??emptySubscribe,workbench?.store.get??emptyView,workbench?.store.get??emptyView);
 const state=view?.snapshot;
 if(!workbench||!state||state.project.id!==workbench.projectId||state.session.id!==workbench.sessionId)return null;
 const versions=state.versions;
 const lastUser=state.messages.findLastIndex(message=>message.role==='user');
 const retry=showRetry&&(state.status==='error'||state.status==='interrupted')&&lastUser>=0
  &&(state.revisable_message_ids===undefined||state.revisable_message_ids.includes(state.messages[lastUser].id))
  &&!state.messages.slice(lastUser+1).some(message=>message.role==='assistant');
 if((versions?.items.length??0)<2&&!retry)return null;
 const disabled=blocked||integration?.chatUnavailable||state.project.archived||state.project.removed||!view.connected||view.mutating||state.busy||!!state.pending;
 const scope={projectId:state.project.id,sessionId:state.session.id};
 const items=versions?.items??[];
 const current=Math.max(0,items.findIndex(item=>item.session_id===versions?.current));
 const switchBy=(step:number)=>{const target=items[current+step];if(!target||disabled)return;void workbench.store.switchMessageVersion(target.session_id,scope).catch(workbench.store.reportError);};
 return <div className="text-muted-foreground inline-flex items-center gap-1 text-[11px]">
  {items.length>1&&<span role="group" aria-label="会话版本" className="group inline-flex items-center">
   <button type="button" aria-label="上一个版本" title={items[current-1]?.label} disabled={disabled||current===0}
    className={versionButtonClass} onClick={()=>switchBy(-1)}>
    <ChevronLeftIcon className="size-3.5"/>
   </button>
   <span aria-live="polite" title={items[current]?.label} className="text-muted-foreground group-hover:text-foreground min-w-7 text-center font-mono text-[11px] tabular-nums transition-colors">{current+1} / {items.length}</span>
   <button type="button" aria-label="下一个版本" title={items[current+1]?.label} disabled={disabled||current>=items.length-1}
    className={versionButtonClass} onClick={()=>switchBy(1)}>
    <ChevronRightIcon className="size-3.5"/>
   </button>
  </span>}
  {retry&&state.history_revision&&<button type="button" disabled={disabled} className="hover:text-foreground inline-flex items-center gap-1 rounded px-1 py-1 disabled:opacity-50"
   onClick={()=>{void workbench.store.regenerateMessage(state.messages[lastUser].id,scope).catch(workbench.store.reportError);}}><RefreshCwIcon className="size-3"/>重试回答</button>}
 </div>;
}
