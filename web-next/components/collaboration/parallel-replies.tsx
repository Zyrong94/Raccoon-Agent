"use client";

import type {ParallelReply} from '@/lib/workbench/types';

const LABELS:Record<ParallelReply['status'],string>={running:'正在回答',succeeded:'已回答',failed:'回答失败',cancelled:'已取消',interrupted:'已中断'};

// Parallel answers come from a separate read-only child session. They are shown
// beside the main thread as their own cards: never appended to the parent
// messages and never re-sent to the main model. The snapshot is the single
// source, so a refresh or reconnect re-renders the same persisted cards once.
export function ParallelReplies({replies}:{replies?:ParallelReply[]}){
 if(!replies?.length)return null;
 return <section aria-label="并行回答" className="mx-auto w-full max-w-(--thread-max-width) space-y-2">
  {replies.map(reply=><article key={reply.source_event_id} aria-label={`并行问题：${reply.question}`} className="border-foreground/10 bg-muted/30 rounded-thread border px-3 py-2">
   <header className="flex items-center gap-2 text-xs">
    <span className="text-foreground/80 font-medium">并行回答</span>
    <span className={reply.status==='succeeded'?'text-muted-foreground':reply.status==='running'?'text-muted-foreground animate-pulse':reply.status==='failed'?'text-destructive':'text-muted-foreground'}>{LABELS[reply.status]??reply.status}</span>
   </header>
   <p className="mt-1 text-sm font-medium break-words whitespace-pre-wrap">{reply.question}</p>
   {reply.answer&&<p className="text-foreground/90 mt-1 text-sm break-words whitespace-pre-wrap">{reply.answer}</p>}
   {!reply.answer&&reply.error&&<p role="alert" className="text-destructive mt-1 text-xs break-words whitespace-pre-wrap">{reply.error}</p>}
   {!reply.answer&&!reply.error&&reply.status==='running'&&<p className="text-muted-foreground mt-1 text-xs">在独立只读上下文中回答，主任务继续执行。</p>}
  </article>)}
 </section>;
}
