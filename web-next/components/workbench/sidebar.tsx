"use client";
import {useEffect,useMemo,useState,useSyncExternalStore} from 'react';
import {Menu} from '@base-ui/react/menu';
import {ArchiveIcon,CalendarClockIcon,ChevronDownIcon,ChevronRightIcon,CopyIcon,FolderIcon,FolderMinusIcon,MessagesSquareIcon,MoreHorizontalIcon,PlusIcon,Settings2Icon,SquarePenIcon,XIcon} from 'lucide-react';
import type {ConversationSummary} from '@/lib/workbench/types';
import {GearIcon} from '@/components/icons/gear';
import {Dialog,DialogContent,DialogDescription,DialogFooter,DialogHeader,DialogTitle} from '@/components/ui/dialog';
import {Button} from '@/components/ui/button';
import {cn} from '@/lib/utils';
import {useWorkbenchView,type SidebarNavigation} from './context';
import {SidebarNavigationContext} from './sidebar-context';
import {ProjectConversationRow} from './project-conversation-row';
import {OpenProjectDialog} from './project-picker';
import {menuContentClass,menuItemClass} from '../shared/workbench-styles';

const navClass='text-foreground/85 hover:bg-foreground/[0.04] aria-[current=page]:bg-foreground/[0.06] focus-visible:outline-ring rounded-control flex h-8 w-full items-center gap-2 px-2 text-[13px] transition-colors';
const iconClass='text-muted-foreground hover:text-foreground hover:bg-foreground/[0.05] focus-visible:outline-ring rounded-control grid size-6 shrink-0 place-items-center';
function uniqueConversations(items:ConversationSummary[]){const seen=new Set<string>();return items.filter(item=>{const key=JSON.stringify([item.project_id,item.id]);if(seen.has(key)||item.archived&&!item.busy&&!item.pending)return false;seen.add(key);return true;});}

type SidebarPreferences={collapsed:Record<string,boolean>;expanded:Record<string,boolean>;recentOpen:boolean;current:string};
const defaultPreferences:SidebarPreferences={collapsed:{},expanded:{},recentOpen:true,current:''};
type SidebarPreferenceStore={value:SidebarPreferences;listeners:Set<()=>void>};
const sidebarPreferences=new WeakMap<object,SidebarPreferenceStore>();
function useSidebarPreferences(owner?:object){
 const store=useMemo(()=>{let value=owner?sidebarPreferences.get(owner):undefined;if(!value){value={value:defaultPreferences,listeners:new Set()};if(owner)sidebarPreferences.set(owner,value);}return value;},[owner]);
 const preferences=useSyncExternalStore(listener=>{store.listeners.add(listener);return()=>{store.listeners.delete(listener);};},()=>store.value,()=>defaultPreferences);
 const update=(change:(value:SidebarPreferences)=>SidebarPreferences)=>{const next=change(store.value);if(next!==store.value){store.value=next;store.listeners.forEach(listener=>listener());}};
 return [preferences,update] as const;
}

export function Sidebar({navigation,onNavigate,onOpenSettings,onOpenMain,mainOpen,onOpenSchedules,schedulesOpen,onOpenConfiguration,configurationOpen,navigateToChat,searchOpen=false,onSearchOpenChange}:{navigation:SidebarNavigation;onNavigate?:()=>void;onOpenSettings?:()=>void;onOpenMain?:()=>void;mainOpen?:boolean;onOpenSchedules?:()=>void;schedulesOpen?:boolean;onOpenConfiguration?:()=>void;configurationOpen?:boolean;navigateToChat?:(action:()=>void)=>void;searchOpen?:boolean;onSearchOpenChange?:(open:boolean)=>void}){
 const [search,setSearch]=useState(''),[addOpen,setAddOpen]=useState(false),[error,setError]=useState('');
 const [pendingRemove,setPendingRemove]=useState<{id:string;name:string;path:string}|null>(null),[changing,setChanging]=useState(false);
 const {blocked:chatBlocked,navigationBlocked=chatBlocked,workspace,workbench}=useWorkbenchView();
 const [{collapsed,expanded,recentOpen},updatePreferences]=useSidebarPreferences(workbench?.store);
 const setCollapsed=(change:(value:Record<string,boolean>)=>Record<string,boolean>)=>updatePreferences(value=>({...value,collapsed:change(value.collapsed)}));
 const setExpanded=(change:(value:Record<string,boolean>)=>Record<string,boolean>)=>updatePreferences(value=>({...value,expanded:change(value.expanded)}));
 const setRecentOpen=(recentOpen:boolean)=>updatePreferences(value=>({...value,recentOpen}));
 const navigationContext=useMemo(()=>({onNavigate,navigateToChat}),[onNavigate,navigateToChat]);
 const state=navigation.state,allProjects=state.projects,projects=allProjects.filter(project=>!project.projectless),recent=uniqueConversations(state.recent);
 const currentProject=workbench?.projectId??workspace?.current.id;
 const currentConversationId=state?.current_conversation_id;
 useEffect(()=>{if(currentProject&&currentConversationId){const current=JSON.stringify([currentProject,currentConversationId]);updatePreferences(value=>value.current===current?value:{...value,current,collapsed:{...value.collapsed,[currentProject]:false}});}},[currentProject,currentConversationId]);
 const isActive=(conversation:ConversationSummary)=>!mainOpen&&!schedulesOpen&&!configurationOpen&&!state?.new_chat&&conversation.id===state?.current_conversation_id&&conversation.project_id===currentProject;
 const newChat=(projectId?:string)=>{if(navigationBlocked)return;const action=()=>{onNavigate?.();setError('');void navigation.onNew(projectId).catch(()=>setError('无法新建聊天，请重试。'));};if(navigateToChat)navigateToChat(action);else action();};
 const changeProject=async(action:()=>Promise<void>)=>{if(changing||chatBlocked)return;setChanging(true);setError('');try{await action();setPendingRemove(null);}catch(error){setError((error as Error).message||'无法更新项目，请重试。');}finally{setChanging(false);}};
 const query=searchOpen?search.trim().toLocaleLowerCase():'';
 const copyPath=async(path:string)=>{try{await navigator.clipboard.writeText(path);}catch{setError('无法复制路径，请检查浏览器权限。');}};
 const results=uniqueConversations([...allProjects.flatMap(project=>project.conversations),...recent]).filter(item=>item.title.toLocaleLowerCase().includes(query)||allProjects.find(project=>project.id===item.project_id)?.name.toLocaleLowerCase().includes(query));
 return <SidebarNavigationContext.Provider value={navigationContext}><div className="flex min-h-0 flex-1 flex-col">
  <nav aria-label="个人工作台" className="mb-2 shrink-0 space-y-0.5">
   <button type="button" disabled={navigationBlocked} onClick={()=>newChat()} title="新聊天 (Alt+Shift+O)" className={cn(navClass,'disabled:opacity-40')}><SquarePenIcon className="size-4"/>新聊天</button>
   {onOpenMain&&<button type="button" data-main-trigger aria-current={mainOpen?'page':undefined} onClick={onOpenMain} className={navClass}><MessagesSquareIcon className="size-4"/>主会话</button>}
   {onOpenSchedules&&<button type="button" data-schedule-trigger aria-current={schedulesOpen?'page':undefined} onClick={onOpenSchedules} className={navClass}><CalendarClockIcon className="size-4"/>定时任务</button>}
   {onOpenConfiguration&&<button type="button" data-configuration-trigger aria-current={configurationOpen?'page':undefined} onClick={onOpenConfiguration} className={navClass}><Settings2Icon className="size-4"/>配置管理</button>}
  </nav>
  {searchOpen&&<div className="mb-2 flex h-8 shrink-0 items-center gap-1 px-1"><input autoFocus aria-label="搜索聊天" value={search} onChange={event=>setSearch(event.target.value)} placeholder="搜索聊天" onKeyDown={event=>{if(event.key==='Escape'){setSearch('');onSearchOpenChange?.(false);}}} className="border-foreground/15 bg-background focus-visible:outline-ring h-8 min-w-0 flex-1 rounded-control border px-2 text-[13px]"/>{onSearchOpenChange&&<button type="button" aria-label="关闭搜索" className={iconClass} onClick={()=>{setSearch('');onSearchOpenChange(false);}}><XIcon className="size-4"/></button>}</div>}
  {error&&<p role="alert" className="text-destructive px-2 py-1 text-xs">{error}</p>}
  <div className="-mx-1 min-h-0 flex-1 overflow-y-auto px-1 pb-3 [scrollbar-width:thin] [scrollbar-color:transparent_transparent] hover:[scrollbar-color:color-mix(in_srgb,currentColor_24%,transparent)_transparent] focus-within:[scrollbar-color:color-mix(in_srgb,currentColor_24%,transparent)_transparent]">
   {query?<section aria-label="搜索结果"><p className="text-muted-foreground mb-1 px-2 text-xs">搜索结果</p>{results.map(item=><ProjectConversationRow key={JSON.stringify([item.project_id,item.id])} conversation={item} active={isActive(item)} projectName={projects.find(project=>project.id===item.project_id)?.name}/>)}{!results.length&&<p className="text-muted-foreground px-2 py-3 text-[13px]">没有找到聊天</p>}</section>:<>
    <nav aria-label="项目工作区">
     <div className="text-muted-foreground mt-2 mb-1 flex h-7 items-center px-2 text-xs"><span>项目</span><button type="button" data-open-project aria-label="打开项目" title="打开已有项目" disabled={chatBlocked} className={cn(iconClass,'-mr-1 ml-auto')} onClick={()=>setAddOpen(true)}><PlusIcon className="size-4"/></button></div>
     {projects.map(project=>{const conversations=uniqueConversations(project.conversations),current=conversations.find(isActive);let visible=expanded[project.id]?conversations:conversations.slice(0,5);if(current&&!visible.includes(current))visible=[...visible.slice(0,4),current];const open=!collapsed[project.id];return <div key={project.id} className="mb-1">
      <div className="group flex h-8 min-w-0 items-center rounded-control hover:bg-foreground/[0.04] focus-within:bg-foreground/[0.04]">
       <button type="button" aria-label={`${open?'收起':'展开'} ${project.name}`} aria-expanded={open} title={project.path} onClick={()=>setCollapsed(value=>({...value,[project.id]:open}))} className="focus-visible:outline-ring flex h-8 min-w-0 flex-1 items-center gap-2 rounded-control px-2 text-left text-[13px] text-foreground/85"><span className="relative size-4 shrink-0"><FolderIcon className="absolute size-4 group-hover:opacity-0 group-focus-within:opacity-0"/>{open?<ChevronDownIcon className="absolute size-4 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"/>:<ChevronRightIcon className="absolute size-4 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"/>}</span><span title={project.path} className="min-w-0 truncate">{project.name}</span></button>
       <div className="mr-1 flex opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 has-data-[popup-open]:opacity-100">
        <button type="button" aria-label={`在 ${project.name} 中新建聊天`} title="新聊天" disabled={navigationBlocked||!project.available} onClick={()=>newChat(project.id)} className={iconClass}><PlusIcon className="size-4"/></button>
        <Menu.Root><Menu.Trigger aria-label={`${project.name} 项目操作`} className={iconClass}><MoreHorizontalIcon className="size-4"/></Menu.Trigger><Menu.Portal><Menu.Positioner side="right" align="start" sideOffset={6} className="z-50"><Menu.Popup className={menuContentClass}><Menu.Item disabled={navigationBlocked||!project.available} className={menuItemClass} onClick={()=>newChat(project.id)}><SquarePenIcon className="size-3.5"/>新聊天</Menu.Item><Menu.Item className={menuItemClass} onClick={()=>void copyPath(project.path)}><CopyIcon className="size-3.5"/>复制项目路径</Menu.Item>{navigation.updateProject&&<Menu.Item disabled={chatBlocked||changing||project.conversations.some(item=>item.busy||item.pending)} className={menuItemClass} onClick={()=>void changeProject(()=>navigation.updateProject!(project.id,true))}><ArchiveIcon className="size-3.5"/>归档项目</Menu.Item>}{navigation.removeProject&&<Menu.Item disabled={chatBlocked||changing||project.conversations.some(item=>item.busy||item.pending)} className={menuItemClass} onClick={()=>{setError('');setPendingRemove(project);}}><FolderMinusIcon className="size-3.5"/>移除项目</Menu.Item>}</Menu.Popup></Menu.Positioner></Menu.Portal></Menu.Root>
       </div>
      </div>
      {open&&<section aria-label={`${project.name} 聊天`}>
       {!project.available?<p className="text-muted-foreground px-8 py-1 text-xs" title={project.error}>项目暂不可用</p>:<>{visible.map(item=><ProjectConversationRow key={item.id} conversation={item} active={isActive(item)} indented/>)}{!conversations.length&&<p className="text-muted-foreground/70 px-8 py-1.5 text-[13px]">暂无聊天</p>}{conversations.length>5&&<button type="button" onClick={()=>setExpanded(value=>({...value,[project.id]:!value[project.id]}))} className="text-muted-foreground hover:text-foreground focus-visible:outline-ring h-8 rounded-control px-8 text-[13px]">{expanded[project.id]?'收起显示':'展开显示'}</button>}</>}
      </section>}
     </div>;})}
     {!projects.length&&<p className="text-muted-foreground px-2 py-2 text-[13px]">打开项目以开始聊天</p>}
    </nav>
    <button type="button" aria-expanded={recentOpen} onClick={()=>setRecentOpen(!recentOpen)} className="text-muted-foreground hover:text-foreground focus-visible:outline-ring mt-5 mb-1 flex h-7 items-center gap-1 rounded-control px-2 text-xs">最近<ChevronDownIcon className={cn('size-3.5',!recentOpen&&'-rotate-90')}/></button>
    {recentOpen&&<section aria-label="最近聊天">{recent.map(item=><ProjectConversationRow key={JSON.stringify([item.project_id,item.id])} conversation={item} active={isActive(item)}/>)}{!recent.length&&<p className="text-muted-foreground px-2 py-2 text-[13px]">暂无最近聊天</p>}</section>}
   </>}
  </div>
  {onOpenSettings&&<div className="-mx-1 mt-auto flex shrink-0 items-center px-1 pt-1"><button type="button" onClick={onOpenSettings} aria-label="设置" title="设置" className={cn(navClass,'group text-muted-foreground hover:text-foreground')}><GearIcon className="size-4 shrink-0 transition-transform duration-500 ease-out group-hover:rotate-45"/>设置</button></div>}
  <OpenProjectDialog open={addOpen} onOpenChange={setAddOpen}/>
  <Dialog open={!!pendingRemove} onOpenChange={open=>{if(!open&&!changing)setPendingRemove(null);}}><DialogContent className="sm:max-w-sm"><DialogHeader><DialogTitle>移除项目？</DialogTitle><DialogDescription>从工作台移除「{pendingRemove?.name}」，保留本机目录和会话记录。再次打开相同目录即可恢复。</DialogDescription></DialogHeader>{error&&<p role="alert" className="text-destructive text-sm">{error}</p>}<DialogFooter><Button variant="outline" disabled={changing} onClick={()=>setPendingRemove(null)}>取消</Button><Button variant="destructive" disabled={changing||chatBlocked||projects.find(project=>project.id===pendingRemove?.id)?.conversations.some(item=>item.busy||item.pending)} onClick={()=>{if(pendingRemove&&navigation?.removeProject)void changeProject(()=>navigation.removeProject!(pendingRemove.id));}}>{changing?'正在移除…':'移除'}</Button></DialogFooter></DialogContent></Dialog>
 </div></SidebarNavigationContext.Provider>;
}
