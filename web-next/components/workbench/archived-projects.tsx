"use client";
import {useEffect,useRef,useState} from 'react';
import {ArchiveRestoreIcon,SearchIcon,Trash2Icon} from 'lucide-react';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import type {ArchiveConversation,ArchiveSnapshot} from '@/lib/workbench/types';
import {useWorkbenchView,type ProjectNavigation} from './context';

const dateFormat=new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
function dateLabel(value?:string|null){if(!value)return null;const date=new Date(value);return Number.isNaN(date.getTime())?null:dateFormat.format(date);}

export function ArchivedProjects({navigation,onDelete,canDelete}:{navigation:ProjectNavigation;onDelete?:(item:ArchiveConversation,onDeleted:()=>Promise<void>)=>void;canDelete?:(item:ArchiveConversation)=>boolean}){
 const {blocked,navigationBlocked}=useWorkbenchView();
 const [archive,setArchive]=useState<ArchiveSnapshot|null>(null),[search,setSearch]=useState(''),[error,setError]=useState(''),[working,setWorking]=useState(false);
 const generation=useRef(0),active=useRef(true),pending=useRef(false);
 const getArchives=navigation.getArchives;
 const load=async()=>{const request=++generation.current;const value=await getArchives();if(active.current&&generation.current===request)setArchive(value);};
 // Reload quietly: a snapshot arriving must not blank the list back to "正在读取归档…".
 useEffect(()=>{active.current=true;setError('');void load().catch(error=>{if(active.current)setError((error as Error).message);});return(()=>{active.current=false;generation.current++;});},[getArchives]);
 const drop=(storageProjectId:string,id:string)=>setArchive(current=>current?{...current,
  projects:current.projects.map(project=>({...project,conversations:project.conversations.filter(item=>!(item.storage_project_id===storageProjectId&&item.id===id))})),
  unassigned:current.unassigned.filter(item=>!(item.storage_project_id===storageProjectId&&item.id===id))}:current);
 // Restoring one chat only removes that row; the workbench snapshot carries the rest of the state.
 const run=async(action:()=>Promise<void>,optimistic?:()=>void)=>{if(pending.current)return;pending.current=true;setWorking(true);setError('');try{await action();if(optimistic)optimistic();else await load();}catch(error){if(active.current)setError((error as Error).message);}finally{pending.current=false;if(active.current)setWorking(false);}};
 const query=search.trim().toLocaleLowerCase();
 const projects=archive?.projects.flatMap(project=>{const matches=`${project.name} ${project.path}`.toLocaleLowerCase().includes(query);const conversations=project.conversations.filter(item=>matches||item.title.toLocaleLowerCase().includes(query));return !query||matches||conversations.length?[{...project,conversations}]:[];})??[];
 const unassigned=archive?.unassigned.filter(item=>item.title.toLocaleLowerCase().includes(query))??[];
 const count=archive?archive.projects.reduce((sum,project)=>sum+project.conversations.length,archive.unassigned.length):0;
 const row=(item:ArchiveConversation,wholeProject=false)=><div key={JSON.stringify([item.storage_project_id,item.id])} className="border-foreground/[0.07] flex items-center gap-4 border-b py-3.5 last:border-b-0">
  <div className="min-w-0 flex-1"><p className="truncate text-sm" title={item.title}>{item.title||'新会话'}</p>{dateLabel(item.updated_at)&&<p className="text-muted-foreground mt-1 text-xs tabular-nums">{dateLabel(item.updated_at)}</p>}</div>
  <div className="flex shrink-0 items-center gap-1">{!wholeProject&&onDelete&&canDelete?.(item)&&<Button variant="ghost" size="icon-sm" aria-label={`删除「${item.title||'新会话'}」`} disabled={blocked||working} onClick={()=>onDelete(item,()=>run(async()=>{}))} className="text-muted-foreground hover:text-destructive"><Trash2Icon/></Button>}
   {!wholeProject&&<Button variant="outline" size="sm" disabled={navigationBlocked||working||!navigation.restoreArchivedChat} onClick={()=>void run(()=>navigation.restoreArchivedChat!(item.storage_project_id,item.id),()=>drop(item.storage_project_id,item.id))}><ArchiveRestoreIcon/>取消归档</Button>}</div>
 </div>;
 return <>
  <h1 className="text-2xl font-semibold tracking-[-0.01em]">已归档的聊天</h1><p className="text-muted-foreground mt-1.5 text-sm">按项目保留在本机，恢复项目后可继续聊天。</p>
  <div className="mt-6 flex items-center justify-between gap-4">
   <div className="relative w-full max-w-[320px] px-0.5 py-1">
    <SearchIcon aria-hidden className="text-muted-foreground pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2"/>
    <Input type="search" value={search} onChange={event=>setSearch(event.target.value)}
     placeholder="搜索项目或已归档的聊天" aria-label="搜索已归档的聊天" className="border-foreground/15 bg-background h-8 ps-8 text-sm"/>
   </div>
   <span className="text-muted-foreground shrink-0 text-xs">{count} 个聊天</span>
  </div>
  {error&&<div className="mt-4 flex items-center gap-3"><p role="alert" className="text-destructive text-sm">{error}</p>{!archive&&<Button variant="outline" size="sm" onClick={()=>void run(async()=>{})}>重试</Button>}</div>}
  {!archive&&!error&&<p role="status" className="text-muted-foreground mt-6 text-sm">正在读取归档…</p>}
  {projects.map(project=><section key={project.id} aria-label={`${project.name} 归档`} className="mt-6 border-t border-foreground/10 pt-4">
   <div className="flex items-center gap-4"><div className="min-w-0 flex-1"><h2 className="truncate text-sm font-medium">{project.name}</h2><p className="text-muted-foreground mt-1 truncate text-xs" title={project.path}>{project.path}</p></div>{project.archived?<Button variant="outline" size="sm" disabled={blocked||working||!navigation?.updateProject} onClick={()=>void run(()=>navigation!.updateProject!(project.id,false))}><ArchiveRestoreIcon/>恢复项目</Button>:<span className="text-muted-foreground text-xs">项目中的归档聊天</span>}</div>
   {project.archived&&<p className="text-muted-foreground mt-2 text-xs">整个项目已归档；恢复项目会保留原有的单个聊天归档状态。</p>}
   {!project.available&&<p className="text-muted-foreground mt-2 text-xs">{project.error??'项目暂不可用'}</p>}
   {project.conversations.map(item=>row(item,project.archived))}
   {!project.conversations.length&&<p className="text-muted-foreground py-4 text-sm">此项目暂无聊天</p>}
  </section>)}
  {!!unassigned.length&&<section aria-label="无项目归属的聊天" className="mt-6 border-t border-foreground/10 pt-4"><h2 className="text-sm font-medium">无项目归属的聊天</h2><p className="text-muted-foreground mt-1 text-xs">未关联项目的聊天，可恢复后继续。</p>{unassigned.map(item=>row(item))}</section>}
  {archive&&!projects.length&&!unassigned.length&&<div className="border-foreground/15 text-muted-foreground mt-4 rounded-[8px] border border-dashed py-8 text-center text-sm">{query?'没有匹配的聊天或项目':'暂无归档的聊天或项目'}</div>}
 </>;
}
