"use client";
import {useContext,useEffect,useRef,useState} from 'react';
import {CheckIcon,ChevronDownIcon,FolderIcon,FolderOpenIcon,ArrowUpIcon,SearchIcon} from 'lucide-react';
import {useWorkbenchView} from './context';
import {SidebarNavigationContext} from './sidebar-context';
import {Popover,PopoverContent,PopoverTrigger} from '@/components/ui/popover';
import {Dialog,DialogContent,DialogTitle,DialogDescription} from '@/components/ui/dialog';
import {Button} from '@/components/ui/button';
import type {DirectoryListing} from '@/lib/workbench/types';

export function ProjectPicker(){
 const {navigation,workbench,blocked}=useWorkbenchView();
 const {navigateToChat}=useContext(SidebarNavigationContext);
 const [open,setOpen]=useState(false),[query,setQuery]=useState(''),[dialog,setDialog]=useState(false);
 if(!navigation?.state.new_chat||!workbench)return null;
 const current=navigation.state.projects.find(project=>project.id===workbench.projectId&&!project.projectless);
 const order=[...new Set([workbench.projectId,...navigation.state.recent.map(item=>item.project_id),...navigation.state.projects.map(project=>project.id)])];
 const projects=order.flatMap(id=>{const project=navigation.state.projects.find(item=>item.id===id);return project&&!project.projectless&&`${project.name} ${project.path}`.toLowerCase().includes(query.toLowerCase())?[project]:[];});
 function choose(id?:string){
  setOpen(false);if(id===current?.id)return;
  const action=()=>{void navigation!.onNew(id,true).catch(()=>{});};
  if(navigateToChat)navigateToChat(action);else action();
 }
 const rowClass="hover:bg-muted flex h-8 w-full items-center gap-2 rounded px-2 text-left text-[13px] disabled:opacity-40";
 return <div className="mb-2 flex items-center px-1">
  <Popover open={open} onOpenChange={setOpen}>
   <PopoverTrigger disabled={blocked} aria-label={`选择项目：${current?.name??'无项目'}`} className="text-muted-foreground hover:bg-muted hover:text-foreground flex h-7 max-w-full items-center gap-1.5 rounded-md px-2 text-[13px] disabled:opacity-50"><FolderIcon className="size-4 shrink-0"/><span className="truncate">{current?.name??'无项目'}</span><ChevronDownIcon className="size-3.5 shrink-0"/></PopoverTrigger>
   <PopoverContent align="start" side="top" className="w-72 gap-0 p-1.5">
    <label className="text-muted-foreground flex items-center gap-2 px-2 py-2"><SearchIcon className="size-3.5"/><input aria-label="搜索项目" placeholder="搜索项目" value={query} onChange={event=>setQuery(event.target.value)} className="text-foreground min-w-0 flex-1 bg-transparent text-[13px] outline-none"/></label>
    <div aria-hidden="true" className="my-1 border-t"/>
    <div className="max-h-64 overflow-y-auto"><button type="button" disabled={blocked} onClick={()=>choose()} className={rowClass}><FolderIcon className="size-4 shrink-0"/><span className="min-w-0 flex-1 truncate">无项目</span>{!current&&<CheckIcon className="size-3.5"/>}</button>{projects.map(project=><button key={project.id} type="button" disabled={!project.available||blocked} title={project.error??project.path} onClick={()=>choose(project.id)} className={rowClass}><FolderIcon className="size-4 shrink-0"/><span className="min-w-0 flex-1 truncate">{project.name}</span>{current?.id===project.id&&<CheckIcon className="size-3.5"/>}</button>)}{!projects.length&&<p className="text-muted-foreground p-3 text-xs">没有匹配的项目</p>}</div>
    <div aria-hidden="true" className="my-1 border-t"/>
    <button type="button" disabled={blocked} onClick={()=>{setOpen(false);setDialog(true);}} className={rowClass}><FolderOpenIcon className="size-4"/>打开本地项目…</button>
   </PopoverContent>
  </Popover>
  <OpenProjectDialog open={dialog} onOpenChange={setDialog}/>
 </div>;
}

export function OpenProjectDialog({open,onOpenChange}:{open:boolean;onOpenChange:(open:boolean)=>void}){
 const {navigation,blocked}=useWorkbenchView();
 const {navigateToChat,onNavigate}=useContext(SidebarNavigationContext);
 const [path,setPath]=useState(''),[listing,setListing]=useState<DirectoryListing|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false),[picking,setPicking]=useState(false);
 const pickerPending=useRef(false);
 const pickerController=useRef<AbortController|null>(null);
 const request=useRef(0),active=useRef(open);active.current=open;
 const navigationRef=useRef(navigation);navigationRef.current=navigation;
 async function browse(target?:string){
  const id=++request.current;setLoading(true);setError('');
  try{const value=await navigationRef.current!.browse(target);if(id!==request.current||!active.current)return;setListing(value);setPath(value.path);}
  catch(error){if(id===request.current&&active.current)setError((error as Error).message);}
  finally{if(id===request.current&&active.current)setLoading(false);}
 }
 useEffect(()=>{if(open&&navigationRef.current){setListing(null);void browse();}return()=>{request.current++;pickerController.current?.abort();};},[open]);
 if(!navigation)return null;
 async function pick(){
  if(loading||blocked||pickerPending.current)return;
  const picker=navigationRef.current?.pickDirectory;
  if(!picker){setError('当前环境不支持系统目录选择，请输入目录后按 Enter');return;}
  const id=++request.current,controller=new AbortController();pickerController.current=controller;pickerPending.current=true;setPicking(true);setLoading(true);setError('');
  try{const selected=await picker(path.trim()||undefined,controller.signal);if(id!==request.current||!active.current||controller.signal.aborted)return;if(selected)await browse(selected);}
  catch(error){if(id===request.current&&active.current&&!controller.signal.aborted)setError((error as Error).message);}
  finally{if(pickerController.current===controller)pickerController.current=null;pickerPending.current=false;setPicking(false);if(id===request.current&&active.current)setLoading(false);}
 }
 async function add(){
  if(!path.trim()||loading||blocked||pickerPending.current)return;
  setLoading(true);setError('');const id=++request.current;
  try{await navigation!.onAdd(path.trim());if(id===request.current&&active.current){onOpenChange(false);onNavigate?.();}}
  catch(error){if(id===request.current&&active.current)setError((error as Error).message);}
  finally{if(id===request.current&&active.current)setLoading(false);}
 }
 return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="sm:max-w-md">
  <DialogTitle>打开项目</DialogTitle><DialogDescription>选择本机已有目录，开始新聊天。</DialogDescription>
  <form onSubmit={event=>{event.preventDefault();if(!loading&&!blocked&&!pickerPending.current)void browse(path.trim()||undefined);}} className="flex gap-2"><input aria-label="项目目录" value={path} disabled={picking} onChange={event=>{setPath(event.target.value);request.current++;setLoading(false);}} placeholder="输入目录路径" className="border-input min-w-0 flex-1 rounded-md border bg-transparent px-2 py-1.5 text-sm outline-none"/><Button type="button" variant="outline" disabled={loading||blocked||picking} onClick={()=>void pick()}>浏览</Button></form>
  {error&&<p role="alert" className="text-destructive text-sm">{error}</p>}
  {loading&&<p role="status" className="text-muted-foreground text-xs">{picking?'请在系统窗口中选择文件夹…':'正在读取…'}</p>}
  <div className="max-h-64 overflow-y-auto rounded-md border p-1">
   {listing?.parent&&<button type="button" disabled={loading||picking} onClick={()=>void browse(listing.parent!)} className="hover:bg-muted flex h-8 w-full items-center gap-2 rounded px-2 text-left text-[13px]"><ArrowUpIcon className="size-4"/>上级目录</button>}
   {listing?.directories.map(directory=><button key={directory.path} type="button" disabled={loading||picking} onClick={()=>void browse(directory.path)} className="hover:bg-muted flex h-8 w-full items-center gap-2 rounded px-2 text-left text-[13px]"><FolderIcon className="size-4 shrink-0"/><span className="truncate">{directory.name}</span></button>)}
   {listing&&!listing.directories.length&&<p className="text-muted-foreground p-3 text-xs">此目录没有子文件夹</p>}
  </div>
  <div className="flex justify-end gap-2"><Button variant="ghost" onClick={()=>onOpenChange(false)}>取消</Button><Button disabled={!path.trim()||loading||blocked||picking} onClick={()=>{const action=()=>{void add();};if(navigateToChat)navigateToChat(action);else action();}}>打开项目</Button></div>
 </DialogContent></Dialog>;
}
