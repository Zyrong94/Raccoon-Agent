"use client";

import {useEffect,useMemo,useRef,useState,useSyncExternalStore,type ReactNode} from 'react';
import {ArrowUpIcon,BotIcon,LoaderCircleIcon,SquareIcon} from 'lucide-react';
import {CanvasWorkspace} from '@/components/delivery/canvas-workspace';
import {ReadOnlyTranscript} from '@/components/chat/read-only-transcript';
import {Button} from '@/components/ui/button';
import {Select} from '@/components/ui/select';
import {Textarea} from '@/components/ui/textarea';
import {cn} from '@/lib/utils';
import {SubagentStore,type SubagentDetail,type SubagentMode,type SubagentRequest,type SubagentScope,type SubagentSummary} from '@/lib/subagents';
import type {Artifact,Canvas} from '@/lib/workbench/types';

const states:Record<string,string>={running:'运行中',stopping:'停止中',completed:'已结束',error:'失败',idle:'已结束',failed:'失败',stopped:'已停止',interrupted:'已中断',waiting:'等待中'};
const modeOptions=[{value:'follow_up',label:'排队补充'},{value:'steer',label:'调整方向'}];
const active=(state:string)=>state==='running'||state==='waiting'||state==='stopping';
function Status({subagent,className}:{subagent:SubagentSummary;className?:string}){
 return <span className={cn('inline-flex max-w-full shrink-0 items-center gap-1.5 text-xs',subagent.state==='failed'||subagent.state==='error'||subagent.restricted?'text-destructive':'text-muted-foreground',className)}>{active(subagent.state)&&<LoaderCircleIcon className="size-3 animate-spin motion-reduce:animate-none"/>}{states[subagent.state]??subagent.state}{subagent.restricted?' · 权限受限':''}{subagent.queued_count>0?` · ${subagent.queued_count} 条待处理`:''}</span>;
}

export function CollaborationWorkspace({scope,request,children,artifacts,canvas,enabled=true,extraSections,topRightActions}:{scope:SubagentScope;request:SubagentRequest;children:ReactNode;artifacts?:Artifact[];canvas?:Canvas|null;enabled?:boolean;extraSections?:ReactNode;topRightActions?:ReactNode}){
 const identity=JSON.stringify(scope);
 const store=useMemo(()=>new SubagentStore(scope,request),[identity,request]); // Scope identity owns reads, drafts and controls.
 const view=useSyncExternalStore(store.subscribe,store.get,store.get);
 const [opened,setOpened]=useState<{identity:string;ids:string[];requestId:string}|null>(null);
 useEffect(()=>{if(enabled)store.start();return store.stop;},[store,enabled]);
 const open=(id:string)=>{store.select(id);setOpened(previous=>({identity,ids:[...new Set([...(previous?.identity===identity?previous.ids:[]),id])],requestId:crypto.randomUUID()}));};
 const panel=opened?.identity===identity?{requestId:opened.requestId,content:(actions:ReactNode)=><>{opened.ids.map(id=><div key={`${identity}:${id}`} className={view.selected===id?'flex min-h-0 flex-1 flex-col':'hidden'}><SubagentSession store={store} subagent={view.details[id]} summary={view.subagents.find(subagent=>subagent.subagent_session_id===id)} error={view.selected===id?view.error:''} actions={view.selected===id?actions:undefined}/></div>)}</>}:undefined;
 const taskCards=<div className="space-y-1">
   {view.error&&<p role="alert" className="text-destructive px-2 py-1 text-xs leading-5 break-words">{view.error}</p>}
   {!view.subagents.length&&<p className="text-muted-foreground px-2 py-1 text-xs leading-5">{view.loading&&enabled?'正在读取子智能体…':'暂无子智能体'}</p>}
   {view.subagents.map(subagent=><button key={subagent.subagent_session_id} type="button" onClick={()=>open(subagent.subagent_session_id)} aria-label={`查看子会话：${subagent.agent_name||'子代理'} · ${subagent.task}`} title={`${subagent.agent_name||'子代理'} · ${subagent.task||'协作任务'}`} className={cn('hover:bg-foreground/[0.04] focus-visible:ring-ring flex w-full min-w-0 items-center gap-2 rounded-control px-2 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2',view.selected===subagent.subagent_session_id&&'bg-foreground/[0.04]')}>
     <span className="bg-foreground/[0.06] flex size-6 shrink-0 items-center justify-center rounded-md"><BotIcon aria-hidden="true" className="text-muted-foreground size-3.5"/></span>
     <span className="min-w-0 flex-1 truncate text-[13px]">{subagent.agent_name||'子代理'}</span>
     <Status subagent={subagent} className="text-[11px] leading-4"/>
    </button>)}
  </div>;
 return <CanvasWorkspace artifacts={artifacts} canvas={canvas} sidePanel={panel} taskCards={taskCards} extraSections={extraSections} topRightActions={topRightActions}>
  <div className="min-h-0 flex-1">{children}</div>
 </CanvasWorkspace>;
}

function SubagentSession({store,subagent,summary,error,actions}:{store:SubagentStore;subagent?:SubagentDetail;summary?:SubagentSummary;error:string;actions?:ReactNode}){
 const [draft,setDraft]=useState(''),[mode,setMode]=useState<SubagentMode>('follow_up');
 const modeHint=mode==='steer'?'在模型边界生效，已执行的操作不会撤销':'完成后处理';
 const [writing,setWriting]=useState<'send'|'stop'|null>(null),[feedback,setFeedback]=useState(''),[problem,setProblem]=useState('');
 const pending=useRef<{text:string;mode:SubagentMode;eventId:string}|null>(null);
 const mounted=useRef(true);useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 const current=subagent??summary;
 if(!current)return <p role="status" className="text-muted-foreground p-4 text-sm">{error||'正在读取子会话…'}</p>;
 // 正常结束的子会话不再占用标题栏，只有进行中或异常状态才提示。
 const showStatus=(current.state!=='idle'&&current.state!=='completed')||current.restricted||current.queued_count>0;
 const send=async()=>{
  if(!draft.trim()||writing)return;
  const text=draft.trim();
  if(!pending.current||pending.current.text!==text||pending.current.mode!==mode)pending.current={text,mode,eventId:crypto.randomUUID()};
  setWriting('send');setFeedback('');setProblem('');
  try{const result=await store.send(current.subagent_session_id,text,mode,pending.current.eventId);if(mounted.current){setDraft('');pending.current=null;setFeedback(result.status==='failed'?'此消息此前处理失败，请查看子会话记录。':result.status==='cancelled'?'此消息此前已取消，请查看子会话记录。':result.delivery==='processed'?'此消息已处理；重复投递不会再次执行。':result.delivery==='delivered'?'消息已交给子代理，执行尚未结束。':result.delivery==='queued'?'已加入消息队列，等待子代理处理。':'投递请求已接收，正在同步子会话状态。');}}
  catch(cause){if(mounted.current)setProblem(`${cause instanceof Error?cause.message:String(cause)}。草稿已保留；请核对会话后再重试。`);}
  finally{if(mounted.current)setWriting(null);}
 };
 const stop=async()=>{
  if(writing)return;setWriting('stop');setFeedback('');setProblem('');
  try{await store.stopSubagent(current.subagent_session_id);if(mounted.current)setFeedback('已停止子 Agent，已排队消息一并取消。');}
  catch(cause){if(mounted.current)setProblem(cause instanceof Error?cause.message:String(cause));}
  finally{if(mounted.current)setWriting(null);}
 };
 return <>
  <header className="border-foreground/10 flex shrink-0 items-center gap-2 border-b px-4 py-2.5"><h2 className="min-w-0 truncate text-[13px] font-medium" title={current.task}>{current.agent_name||'子代理'}</h2>{showStatus&&<Status subagent={current} className="text-[11px] leading-4"/>}<Button variant="ghost" size="icon-sm" className="text-muted-foreground ml-auto" aria-label="停止子代理" title="停止子代理" onClick={()=>void stop()} disabled={!!writing||!active(current.state)}>{writing==='stop'?<LoaderCircleIcon className="size-3 animate-spin motion-reduce:animate-none"/>:<SquareIcon className="size-2.5 fill-current"/>}</Button>{actions}</header>
  <div className="min-h-0 flex-1 overflow-y-auto" aria-label="子会话消息">
   <div className="mx-auto flex w-full max-w-[42rem] flex-col gap-5 px-4 py-5">
    {current.restricted&&<p className="text-destructive bg-destructive/5 rounded-lg px-3 py-2 text-xs leading-5">部分操作需要人工批准或权限不足，已被拒绝。请查看工具返回的原因。</p>}
    <SubagentTranscript subagent={subagent} running={active(current.state)}/>
   </div>
  </div>
  <form className="shrink-0 px-4 pt-2 pb-4" onSubmit={event=>{event.preventDefault();void send();}}>
   <div className="mx-auto w-full max-w-[42rem]">
    {current.queued_count>0&&<details className="text-muted-foreground mb-2 text-xs"><summary className="cursor-pointer py-1">{current.queued_count} 条待处理消息</summary><ol className="mt-1 max-h-24 list-decimal space-y-1 overflow-auto pl-5">{current.queued_messages.map(message=><li key={message.event_id} className="whitespace-pre-wrap break-words">{message.text}</li>)}</ol></details>}
    {current.state==='stopped'&&<p className="text-muted-foreground mb-2 text-xs leading-5">已停止。发送新消息后，先处理已保留的队列，再处理新消息。</p>}
    {(problem||error)&&<p role="alert" className="text-destructive mb-2 text-xs leading-5">{problem||error}</p>}
    {feedback&&<p role="status" className="text-muted-foreground mb-2 text-xs leading-5">{feedback}</p>}
    <div className="bg-muted rounded-thread flex items-end gap-1 px-2 py-1.5 focus-within:ring-1 focus-within:ring-ring/30">
     <Textarea aria-label={`发送给 ${current.agent_name||'子代理'}`} value={draft} onChange={event=>setDraft(event.target.value)} rows={1} disabled={!!writing} placeholder="补充要求或发送审查意见…" className="max-h-32 min-h-8 flex-1 resize-none border-0 bg-transparent px-1.5 py-1.5 text-[13px] leading-5 shadow-none focus-visible:ring-0"/>
     <Button type="submit" size="icon-sm" aria-label="发送" className="shrink-0 rounded-full" disabled={!draft.trim()||!!writing}>{writing==='send'?<LoaderCircleIcon className="size-3.5 animate-spin motion-reduce:animate-none"/>:<ArrowUpIcon className="size-3.5"/>}</Button>
    </div>
    <div className="mt-1.5 flex items-center gap-2 px-1"><label id={`mode-label-${current.subagent_session_id}`} className="text-muted-foreground/70 shrink-0 text-[11px]" htmlFor={`mode-${current.subagent_session_id}`}>投递方式</label><Select id={`mode-${current.subagent_session_id}`} aria-labelledby={`mode-label-${current.subagent_session_id}`} value={mode} onValueChange={value=>setMode(value as SubagentMode)} disabled={!!writing} options={modeOptions} className="text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] h-6 w-auto shrink-0 gap-1 border-transparent bg-transparent px-1.5 text-[11px] hover:border-transparent" popupClassName="w-40 text-[12px]"/><span className="text-muted-foreground/50 ml-auto min-w-0 truncate text-[11px]" title={modeHint}>{modeHint}</span></div>
   </div>
  </form>
 </>;
}

function SubagentTranscript({subagent,running}:{subagent?:SubagentDetail;running:boolean}){
 if(!subagent)return <p role="status" className="text-muted-foreground text-xs">正在读取消息…</p>;
 if(!subagent.messages.length)return <p className="text-muted-foreground text-xs">子代理尚未产生消息。</p>;
 return <ReadOnlyTranscript messages={subagent.messages} running={running}/>;
}
