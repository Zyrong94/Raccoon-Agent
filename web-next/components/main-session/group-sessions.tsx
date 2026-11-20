"use client";
import {useEffect,useSyncExternalStore,type ReactNode} from 'react';
import {ArrowLeftIcon,UsersIcon} from 'lucide-react';
import {ReadOnlyTranscript} from '@/components/chat/read-only-transcript';
import type {GroupSessionsStore} from '@/lib/group-sessions';

const rowClass='hover:bg-foreground/[0.04] focus-visible:ring-ring flex w-full min-w-0 items-center gap-2 rounded-control px-2 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2';

// Entry list inside the main session's "会话内容" overlay, below 子智能体.
export function GroupSessionsSection({store,onOpen}:{store:GroupSessionsStore;onOpen:()=>void}){
 const view=useSyncExternalStore(store.subscribe,store.get,store.get);
 useEffect(()=>{void store.loadGroups();},[store]);
 return <section aria-label="群聊记录" className="mt-3">
  <h2 className="text-muted-foreground px-2 pb-1.5 text-xs font-medium">群聊记录</h2>
  {view.error&&<p role="alert" className="text-destructive px-2 py-1 text-xs leading-5 break-words">{view.error}</p>}
  {!view.groups.length?<p className="text-muted-foreground px-2 py-1 text-xs leading-5">{view.loading?'正在读取群聊…':'暂无 QQ 群聊'}</p>
   :<ul className="space-y-1">{view.groups.map(group=><li key={group.id}>
    <button type="button" aria-label={`查看群聊记录：${group.title}`} title={group.title} onClick={()=>{store.select(group.id);onOpen();}} className={rowClass}>
     <UsersIcon aria-hidden="true" className="text-muted-foreground size-4 shrink-0"/>
     <span className="min-w-0 truncate text-[13px]">{group.title}</span>
    </button>
   </li>)}</ul>}
 </section>;
}

// A full panel, like the personal main session: the QQ group history replaces
// the chat area instead of sharing it with a second column.
export function GroupSession({onClose,headerLeading,store}:{onClose:()=>void;headerLeading?:ReactNode;store:GroupSessionsStore}){
 const view=useSyncExternalStore(store.subscribe,store.get,store.get);
 useEffect(()=>{store.start();return()=>store.stop();},[store]);
 const group=view.groups.find(item=>item.id===view.selected);
 const snapshot=view.snapshot;
 const empty=!group?(view.loading?null:'还没有 QQ 群聊')
  :snapshot&&!snapshot.messages.length?'这个群还没有聊天记录'
  :null;
 return <section aria-label="群聊会话" className="flex h-full min-h-0 flex-col [--thread-max-width:42rem]">
  <header className="flex h-12 shrink-0 items-center gap-3 border-b px-4">
   {headerLeading}
   <button type="button" onClick={onClose} aria-label="返回主会话" className="rounded-md p-2 hover:bg-muted"><ArrowLeftIcon className="size-4"/></button>
   <div className="min-w-0"><h1 className="truncate text-sm font-medium">{group?.title??'群聊会话'}</h1><p className="text-muted-foreground text-xs">QQ 群聊 · 只读记录</p></div>
   <span role="status" className="text-muted-foreground ml-auto shrink-0 text-xs">{view.loading?'正在同步':group?'已同步':''}</span>
   {view.groups.length>1&&<select aria-label="切换群聊" value={view.selected} onChange={event=>store.select(event.target.value)} className="border-input bg-background rounded-control max-w-40 shrink-0 border px-2 py-1 text-xs"><option value="">选择群聊</option>{view.groups.map(item=><option key={item.id} value={item.id}>{item.title}</option>)}</select>}
  </header>
  <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6" aria-label="群聊记录消息">
   <div className="mx-auto flex max-w-(--thread-max-width) flex-col gap-4">
    {view.error&&<p role="alert" className="text-destructive rounded-lg bg-destructive/5 px-3 py-2 text-sm leading-6">{view.error}</p>}
    {empty&&<div className="py-12"><h2 className="text-xl font-medium">{empty}</h2><p className="text-muted-foreground mt-2 text-sm">{group?'群里 @ 机器人之后，对话会出现在这里。':'在 QQ 群里 @ 机器人并完成绑定后，群聊记录会出现在这里。'}</p></div>}
    {!empty&&!snapshot&&!view.error&&<p role="status" className="text-muted-foreground text-sm">正在同步群聊记录…</p>}
    {!empty&&snapshot&&<ReadOnlyTranscript messages={snapshot.messages} labels={{user:'群成员',assistant:'Agent'}} showLabels/>}
   </div>
  </div>
  <footer className="border-foreground/10 flex shrink-0 items-center gap-2 border-t px-4 py-3"><UsersIcon aria-hidden="true" className="text-muted-foreground size-4"/><p className="text-muted-foreground text-xs">群聊记录为只读；如需继续对话，请在 QQ 群里 @ 机器人。</p></footer>
 </section>;
}
