"use client";
import {Button} from '@/components/ui/button';
import {PendingInput} from '@/components/pending-input';
import {scheduleStatuses,scheduleTime,type ScheduleRun} from '@/lib/scheduling';

export function RunHistory({runs,timezone,busy,loading,onOpen,onAnswer,onMore}:{runs:ScheduleRun[];timezone:string;busy:boolean;loading:boolean;onOpen:(run:ScheduleRun)=>void;onAnswer:(run:ScheduleRun,answer:string)=>Promise<void>;onMore?:()=>void}){
 return <section className="border-foreground/10 mt-8 border-t pt-6" aria-label="执行历史">
  <h2 className="mb-5 text-sm font-semibold">执行历史</h2>
  {loading&&!runs.length?<p role="status" className="text-muted-foreground text-sm">正在读取执行历史…</p>:!runs.length?<p className="text-muted-foreground text-sm">尚无执行记录</p>:<div className="space-y-6">{runs.map(run=><article key={run.id} className="border-foreground/10 border-b pb-6 last:border-0">
   <div className="mb-3 flex items-center gap-3 text-xs"><span className={['failed','uncertain'].includes(run.status)?'text-destructive':'text-foreground'}>{scheduleStatuses[run.status]||run.status}</span><time className="text-muted-foreground tabular-nums">{scheduleTime(run.scheduled_at,timezone)}</time>{run.session_id&&run.execution!=='isolated'&&run.can_open_session!==false&&['queued','running','succeeded','failed','waiting','uncertain','reviewed'].includes(run.status)&&<Button variant="ghost" size="sm" className="ml-auto h-7 text-xs" disabled={busy} onClick={()=>onOpen(run)}>{run.execution==='main'?'查看主会话':'查看会话'}</Button>}</div>
   {run.execution==='main'&&run.can_open_session===false&&<p className="text-muted-foreground mb-3 text-xs">原主会话已更换，保留本页审计记录。</p>}
   {run.execution==='isolated'&&<p className="text-muted-foreground mb-3 text-xs">历史独立执行，保留本页审计记录。</p>}
   {run.error&&<p className="text-destructive mb-3 whitespace-pre-wrap text-sm">{run.error}</p>}
   {run.status==='waiting'&&run.pending&&<div className="mt-4"><PendingInput key={run.pending.id} pending={run.pending} disabled={busy||!run.pending_token} answer={(_id,answer)=>onAnswer(run,answer)}/></div>}
  </article>)}</div>}
  {onMore&&<Button variant="ghost" size="sm" className="mt-3" disabled={busy} onClick={onMore}>查看更多历史</Button>}
 </section>;
}
