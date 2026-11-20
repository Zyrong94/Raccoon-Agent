"use client";

import {useCallback,useEffect,useImperativeHandle,useRef,useState,type ReactNode,type Ref} from 'react';
import {ArrowLeftIcon,CalendarClockIcon,PlusIcon,RefreshCwIcon,SearchIcon} from 'lucide-react';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {Dialog,DialogContent,DialogDescription,DialogFooter,DialogHeader,DialogTitle} from '@/components/ui/dialog';
import {cn} from '@/lib/utils';
import type {WorkbenchStore} from '@/lib/workbench/client';
import {blankSchedule,scheduleChanges,scheduleDefinition,scheduleDraft,scheduleLabel,scheduleStatuses,scheduleTime,type ScheduleDraft,type ScheduleListing,type ScheduledTask,type ScheduleRun} from '@/lib/scheduling';
import {ScheduleForm} from './schedule-form';
import {RunHistory} from './run-history';

export type ScheduleNavigationHandle={requestLeave:(action:()=>void)=>void};
type Props={store:WorkbenchStore;projectId:string;sessionId:string;sourceLabel?:string;onClose:()=>void;onOpenRun:(task:ScheduledTask,run:ScheduleRun)=>Promise<void>;ref?:Ref<ScheduleNavigationHandle>;headerLeading?:ReactNode};
type Confirmation={title:string;description:string;label:string;action:()=>void};

export function ScheduledTasks({store,projectId,sessionId,sourceLabel='当前工作区会话',onClose,onOpenRun,ref,headerLeading}:Props){
 const [data,setData]=useState<ScheduleListing|null>(null),[selected,setSelected]=useState<string|null>(null);
 const [runs,setRuns]=useState<ScheduleRun[]>([]),[loadingHistory,setLoadingHistory]=useState(false),[limit,setLimit]=useState(50);
 const [draft,setDraft]=useState<ScheduleDraft|null>(null),[original,setOriginal]=useState<ScheduledTask|null>(null);
 const [query,setQuery]=useState(''),[filter,setFilter]=useState('all');
 const [problem,setProblem]=useState(''),[feedback,setFeedback]=useState(''),[busy,setBusy]=useState(false);
 const [confirmation,setConfirmation]=useState<Confirmation|null>(null);
 const alive=useRef(false),lock=useRef(false),version=useRef(0),requestId=useRef('');
 const task=data?.tasks.find(item=>item.id===selected);
 const api=useCallback(<T,>(path='',body?:unknown,method?:'PATCH')=>store.scheduleRequest<T>(projectId,path,body,method),[store,projectId]);
 const reload=useCallback(async()=>{
  const ticket=++version.current;
  const [listing,history]=await Promise.allSettled([api<ScheduleListing>(),selected?api<ScheduleRun[]>(`/${encodeURIComponent(selected)}/runs?limit=${limit}`):Promise.resolve([])]);
  if(!alive.current||ticket!==version.current)return;
  if(listing.status==='rejected')throw listing.reason;
  setData(listing.value);setLoadingHistory(false);
  if(selected&&!listing.value.tasks.some(item=>item.id===selected)){setSelected(null);setRuns([]);return;}
  if(history.status==='rejected')throw new Error('执行历史暂时无法读取，请刷新重试');
  setRuns(history.value);
 },[api,selected,limit]);

 useEffect(()=>{
  alive.current=true;let disposed=false;let timer:ReturnType<typeof setTimeout>;
  const sync=async()=>{
   try{if(!lock.current)await reload();}catch(error){if(!disposed){setProblem((error as Error).message);setLoadingHistory(false);}}
   finally{if(!disposed)timer=setTimeout(sync,3000);}
  };
  void sync();return()=>{disposed=true;alive.current=false;version.current++;clearTimeout(timer);};
 },[reload]);
 useEffect(()=>{
  if(!draft)return;
  const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue='';};
  window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);
 },[draft]);

 const leave=(action:()=>void)=>{
  if(lock.current)return;
  if(draft)setConfirmation({title:'放弃未保存的修改？',description:'任务草稿尚未保存。',label:'放弃修改',action:()=>{setDraft(null);setOriginal(null);action();}});
  else action();
 };
 useImperativeHandle(ref,()=>({requestLeave:leave}));
 const edit=(value:ScheduledTask|null)=>leave(()=>{setOriginal(value);setDraft(value?scheduleDraft(value):blankSchedule());requestId.current=crypto.randomUUID();setProblem('');setFeedback('');});
 const select=(id:string)=>leave(()=>{if(id!==selected){setRuns([]);setLimit(50);setLoadingHistory(true);}setSelected(id);setFeedback('');setProblem('');});

 async function mutate(action:()=>Promise<unknown>,message:string){
  if(lock.current)return;
  lock.current=true;version.current++;setBusy(true);setProblem('');setFeedback('');
  try{
   await action();
   if(!alive.current)return;
   setFeedback(message);
   try{await reload();}catch{if(alive.current)setProblem('操作已提交，列表尚未同步，请刷新核对结果。');}
  }catch(error){if(alive.current)setProblem(`${(error as Error).message}。请刷新核对结果后重试。`);throw error;}
  finally{lock.current=false;if(alive.current)setBusy(false);}
 }
 const runMutation=(action:()=>Promise<unknown>,message:string)=>{void mutate(action,message).catch(()=>{});};
 const save=()=>runMutation(async()=>{
  if(!draft)return;
  const changes=original?scheduleChanges(draft,original):null;
  const result=original&&changes&&!Object.keys(changes).length?original:original?await api<ScheduledTask>(`/${encodeURIComponent(original.id)}`,{...changes,revision:original.revision},'PATCH'):await api<ScheduledTask>('',{...scheduleDefinition(draft),request_id:requestId.current,project_id:projectId,session_id:sessionId});
  if(alive.current){setDraft(null);setOriginal(null);setSelected(result.id);setRuns([]);}
 },original?'任务修改已保存':'任务已创建');
 const openRun=(run:ScheduleRun)=>{if(task)runMutation(()=>onOpenRun(task,run),'');};
 const visible=(data?.tasks||[]).filter(item=>(filter==='all'||item.status===filter)&&(item.title+item.prompt).toLowerCase().includes(query.trim().toLowerCase()));

 return <section aria-label="定时任务管理" data-ui className="bg-background flex h-full min-h-0 flex-col">
  <header className="border-foreground/10 flex h-12 shrink-0 items-center gap-3 border-b px-4 md:px-5">
   {headerLeading}<Button variant="ghost" size="icon-sm" aria-label="返回聊天" onClick={()=>leave(onClose)} disabled={busy}><ArrowLeftIcon/></Button>
   <h1 className="text-[13px] font-medium">定时任务</h1>
   <div className="ml-auto flex items-center gap-2"><Button variant="ghost" size="icon-sm" aria-label="刷新任务" disabled={busy} onClick={()=>{setProblem('');void reload().catch(error=>{if(alive.current)setProblem(error.message);});}}><RefreshCwIcon className="size-3.5"/></Button><Button size="sm" disabled={busy||!data} onClick={()=>edit(null)}><PlusIcon/>新建任务</Button></div>
  </header>
  {/* The list and detail share the available workspace width beside the chat sidebar. */}
  <div className="grid min-h-0 flex-1 grid-cols-[15rem_minmax(0,1fr)]">
   <aside className="border-foreground/10 bg-foreground/[0.015] flex min-h-0 flex-col border-r" aria-label="任务列表">
    <div className="shrink-0 space-y-3 p-4"><div className="relative"><SearchIcon className="text-muted-foreground pointer-events-none absolute top-2.5 left-2.5 size-3.5"/><Input type="search" aria-label="搜索定时任务" placeholder="搜索任务" value={query} onChange={e=>setQuery(e.target.value)} className="bg-background h-9 pl-8"/></div><div className="flex gap-1">{[{id:'all',label:'全部'},{id:'enabled',label:'已启用'},{id:'paused',label:'已暂停'}].map(item=><button key={item.id} type="button" aria-pressed={filter===item.id} onClick={()=>setFilter(item.id)} className={cn('rounded-md px-2.5 py-1 text-xs transition-colors',filter===item.id?'bg-foreground/[0.07] text-foreground':'text-muted-foreground hover:text-foreground')}>{item.label}</button>)}</div></div>
    <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">{!data?<p role="status" className="text-muted-foreground px-3 py-6 text-sm">正在读取任务…</p>:!visible.length?<p className="text-muted-foreground px-3 py-6 text-sm">{query||filter!=='all'?'没有匹配的任务':'还没有定时任务'}</p>:<ul className="space-y-1">{visible.map(item=><li key={item.id}><button type="button" aria-pressed={selected===item.id&&!draft} disabled={busy} onClick={()=>select(item.id)} className={cn('hover:bg-foreground/[0.04] w-full rounded-lg px-3 py-3.5 text-left transition-colors disabled:opacity-50',selected===item.id&&!draft&&'bg-foreground/[0.06]')}><span className="mb-1.5 flex items-start justify-between gap-2"><span className="min-w-0 truncate text-[13px] font-medium">{item.title}</span><span className={cn('shrink-0 text-[11px]',item.latest&&['failed','uncertain','waiting'].includes(item.latest.status)?'text-destructive':'text-muted-foreground')}>{scheduleStatuses[item.latest&&['running','queued','waiting','uncertain','failed'].includes(item.latest.status)?item.latest.status:item.status]||item.status}</span></span><span className="text-muted-foreground mb-1 block truncate text-xs">{item.source?.label||(item.channel==='qq'?'主会话（QQ）':'项目会话')}</span><span className="text-muted-foreground block truncate text-xs">{scheduleLabel(item)}</span><span className="text-muted-foreground mt-1 block truncate text-xs tabular-nums">下次 {scheduleTime(item.next_at,item.timezone)}</span></button></li>)}</ul>}</div>
    {data&&<div className={cn('border-foreground/10 shrink-0 border-t px-5 py-3 text-[11px]',data.host.error?'text-destructive':'text-muted-foreground')} role="status">{data.host.error||data.host.message}</div>}
   </aside>
   <div className="min-w-0 overflow-y-auto">
    {(problem||feedback)&&<div className="mx-auto max-w-[840px] px-8 pt-5">{problem?<p role="alert" className="text-destructive text-sm">{problem}</p>:<p role="status" className="text-muted-foreground text-sm">{feedback}</p>}</div>}
    {draft?<ScheduleForm sourceLabel={original?.source?.label||sourceLabel} draft={draft} editing={!!original} busy={busy} conflict={!!original&&(!data?.tasks.some(item=>item.id===original.id&&item.revision===original.revision))} onChange={setDraft} onSave={save} onCancel={()=>leave(()=>{})}/>:task?<div className="mx-auto max-w-[840px] p-8 lg:p-10">
     <div className="flex items-start justify-between gap-4"><div><p className="text-muted-foreground mb-2 text-xs">{scheduleStatuses[task.status]||task.status}</p><h2 className="text-2xl leading-8 font-semibold tracking-tight break-words">{task.title}</h2></div><Button variant="outline" size="sm" aria-label="编辑任务" disabled={busy} onClick={()=>edit(task)}>编辑</Button></div>
     <dl className="border-foreground/10 mt-7 grid grid-cols-2 gap-x-8 gap-y-5 border-y py-5 text-sm"><div><dt className="text-muted-foreground mb-1.5 text-xs">执行规则</dt><dd>{scheduleLabel(task)}</dd></div><div><dt className="text-muted-foreground mb-1.5 text-xs">下次执行</dt><dd className="tabular-nums">{scheduleTime(task.next_at,task.timezone)}</dd></div><div><dt className="text-muted-foreground mb-1.5 text-xs">时区</dt><dd>{task.timezone}</dd></div><div><dt className="text-muted-foreground mb-1.5 text-xs">执行会话</dt><dd>{task.execution==='main'?'主会话':'来源会话'}</dd></div><div><dt className="text-muted-foreground mb-1.5 text-xs">模型</dt><dd>{task.model||'沿用会话模型'}</dd></div><div><dt className="text-muted-foreground mb-1.5 text-xs">权限</dt><dd>{task.permission_mode?({ask:'需要时询问',auto:'自动审核',bypass:'全部允许'}[task.permission_mode]):'沿用会话权限'}</dd></div></dl>
     <p className="text-muted-foreground mt-5 text-xs">来源：{task.source?.label||(task.channel==='qq'?'主会话（QQ）':'项目会话')}</p><h3 className="mt-6 mb-3 text-sm font-semibold">执行指令</h3><p className="text-foreground/80 text-sm leading-7 whitespace-pre-wrap break-words">{task.prompt}</p>
     <div className="mt-6 flex flex-wrap items-center gap-2"><Button size="sm" disabled={busy} onClick={()=>runMutation(()=>api(`/${encodeURIComponent(task.id)}/run`,{}),'已加入执行队列')}>立即运行一次</Button><Button variant="outline" size="sm" disabled={busy||task.status==='completed'} onClick={()=>runMutation(()=>api(`/${encodeURIComponent(task.id)}/${task.status==='enabled'?'pause':'resume'}`,{revision:task.revision}),task.status==='enabled'?'任务已暂停':'任务已恢复')}>{task.status==='enabled'?'暂停':'恢复'}</Button><Button variant="ghost" size="sm" aria-label="删除任务" disabled={busy} className="text-muted-foreground ml-auto" onClick={()=>setConfirmation({title:'删除定时任务？',description:'删除后停止后续触发，已有执行历史仍保存在本机。',label:'删除',action:()=>runMutation(async()=>{await api(`/${encodeURIComponent(task.id)}/delete`,{revision:task.revision});setSelected(null);setRuns([]);},'任务已删除')})}>删除</Button></div>
     <RunHistory runs={runs} timezone={task.timezone} busy={busy} loading={loadingHistory} onOpen={openRun} onAnswer={(run,answer)=>mutate(()=>api(`/${encodeURIComponent(task.id)}/runs/${encodeURIComponent(run.id)}/answer`,{token:run.pending_token,request_id:run.pending!.id,answer}),'回答已提交，正在继续执行')} onMore={runs.length>=limit&&limit<200?()=>setLimit(Math.min(200,limit+50)):undefined}/>
    </div>:<div className="flex h-full min-h-80 flex-col items-center justify-center gap-3 px-8 text-center"><CalendarClockIcon className="text-muted-foreground/60 mb-1 size-7" strokeWidth={1.5}/><h2 className="text-lg font-medium">{data?.tasks.length?'选择一个任务':'安排下一次执行'}</h2><p className="text-muted-foreground text-sm">{data?.tasks.length?'查看执行时间、任务指令和历史结果。':'新建定时任务，让 Raccoon 到点执行。'}</p>{data&&!data.tasks.length&&<Button variant="outline" size="sm" className="mt-2" onClick={()=>edit(null)} disabled={busy}>创建第一个任务</Button>}</div>}
   </div>
  </div>
  <Dialog open={!!confirmation} onOpenChange={open=>{if(!open&&!busy)setConfirmation(null);}}><DialogContent showCloseButton={false}><DialogHeader><DialogTitle>{confirmation?.title}</DialogTitle><DialogDescription>{confirmation?.description}</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" disabled={busy} onClick={()=>setConfirmation(null)}>取消</Button><Button disabled={busy} onClick={()=>{const action=confirmation?.action;setConfirmation(null);action?.();}}>{confirmation?.label}</Button></DialogFooter></DialogContent></Dialog>
 </section>;
}
