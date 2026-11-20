"use client";

// Presentation adapted directly from the official homepage demo/shell.tsx.
// Cloud commands are replaced with real local actions; see home-demo-provenance.md.
import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {useAuiState} from '@assistant-ui/react';
import {Maximize2Icon,Minimize2Icon,PanelLeftIcon,SearchIcon} from 'lucide-react';
import {Sheet,SheetContent,SheetTitle} from '@/components/ui/sheet';
import {cn} from '@/lib/utils';
import {Sidebar} from './sidebar';
import {SettingsPage} from '../configuration/settings-page';
import {ScheduledTasks,type ScheduleNavigationHandle} from '@/components/scheduling/scheduled-tasks';
import {ConfigurationManager,type ConfigurationNavigationHandle} from '@/components/configuration/configuration-manager';
import {MainSession} from '@/components/main-session/main-session';
import {GroupSession} from '@/components/main-session/group-sessions';
import {groupSessionsStore} from '@/lib/group-sessions';
import {CanvasWorkspace} from '../delivery/canvas-workspace';
import {CollaborationWorkspace} from '@/components/collaboration/collaboration-workspace';
import {Thread} from '../chat/thread';
import {useWorkbenchView,type ProjectNavigation} from './context';
import {getThreadShortcut,type ThreadCommand} from './thread-shortcuts';
import {SidebarNavigationContext} from './sidebar-context';

const SIDEBAR_REGION_ID='raccoon-demo-sidebar';
export function WorkbenchShell({navigation}:{navigation:ProjectNavigation}){
 const rootRef=useRef<HTMLDivElement>(null);
 const scheduleNavigation=useRef<ScheduleNavigationHandle>(null);
 const configurationNavigation=useRef<ConfigurationNavigationHandle>(null);
 const [sidebarCollapsed,setSidebarCollapsed]=useState(false);
 const [mobileSidebarOpen,setMobileSidebarOpen]=useState(false);
 const [settingsOpen,setSettingsOpen]=useState(false);
 const [view,setView]=useState<'chat'|'schedules'|'main'|'groups'|'configuration'>('chat');
 const [scheduleWorkbench,setScheduleWorkbench]=useState<NonNullable<ReturnType<typeof useWorkbenchView>['workbench']>&{sourceLabel:string}>();
 const schedulesOpen=view==='schedules';
 const configurationOpen=view==='configuration';
 const mainOpen=view==='main';
 const groupsOpen=view==='groups';
 const [expanded,setExpanded]=useState(false);
 const [scopedError,setScopedError]=useState<{scope:string;message:string}|null>(null);
 const [searchOpen,setSearchOpen]=useState(false);
 const {blocked,navigationBlocked=blocked,chatUnavailable,notice,canvasState,workbench}=useWorkbenchView();
 const currentId=useAuiState(s=>s.threads.mainThreadId);
 const scopeKey=JSON.stringify([workbench?.projectId,workbench?.sessionId??currentId]);
 const activeScope=useRef(scopeKey);
 const displayError=scopedError?.scope===scopeKey?scopedError.message:'';
 const setDisplayError=(message:string)=>{if(activeScope.current===scopeKey)setScopedError(message?{scope:scopeKey,message}:null);};
 useLayoutEffect(()=>{activeScope.current=scopeKey;setScopedError(null);},[scopeKey]);
 const current=useAuiState(s=>s.threads.threadItems.find(t=>t.id===s.threads.mainThreadId));
 const runtimeMatchesScope=!workbench||!current?.remoteId||current.remoteId===scopeKey;
 const currentConversation=navigation.state.projects.find(project=>project.id===workbench?.projectId)?.conversations.find(item=>item.id===navigation.state.current_conversation_id);
 useEffect(()=>{const update=()=>setExpanded(document.fullscreenElement===document.documentElement);document.addEventListener('fullscreenchange',update);return()=>document.removeEventListener('fullscreenchange',update);},[]);
 async function toggleExpanded(){try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();setDisplayError('');}catch{setDisplayError('当前浏览器无法进入全屏。');}}
 const newThread=()=>{if(navigationBlocked)return;navigateToChat(()=>{setMobileSidebarOpen(false);void navigation.onNew().catch(()=>setDisplayError('无法新建聊天，请重试。'));});};
 const toggleSidebar=()=>{if(window.matchMedia('(max-width: 767px)').matches)setMobileSidebarOpen(!mobileSidebarOpen);else setSidebarCollapsed(!sidebarCollapsed);};
 const closeSettings=()=>{setSettingsOpen(false);requestAnimationFrame(()=>rootRef.current?.querySelector<HTMLButtonElement>('button[aria-label="设置"]')?.focus());};
 const runCommand=(command:ThreadCommand)=>{
  if(command==='sidebar'){toggleSidebar();return;}
  if(command==='composer'){rootRef.current?.querySelector<HTMLTextAreaElement>('[data-composer-input]')?.focus();return;}
  if(navigationBlocked)return;
  if(command==='new'){newThread();return;}
  if(command==='previous'||command==='next'){
   const recent=navigation.state.recent;const index=recent.findIndex(item=>item.id===currentConversation?.id&&item.project_id===currentConversation?.project_id);const next=recent[index+(command==='next'?1:-1)];if(next)navigateToChat(()=>{void navigation.onOpen(next.project_id,next.id).catch(()=>setDisplayError('无法打开聊天，请重试。'));});return;
  }
  if(blocked)return;
  if(command==='archive'&&(currentConversation?.busy||currentConversation?.pending))return;
  if(!currentConversation)return;void navigation.onUpdate(currentConversation.project_id,currentConversation.id,command==='archive'?{archived:!currentConversation.archived}:{pinned:!currentConversation.pinned}).catch(()=>setDisplayError('无法更新聊天，请重试。'));
 };
 const leaveSchedule=(action:()=>void)=>{if(schedulesOpen)scheduleNavigation.current?.requestLeave(action);else if(configurationOpen)configurationNavigation.current?.requestLeave(action);else action();};
 const openSchedules=workbench?()=>{if(!schedulesOpen)leaveSchedule(()=>{setScheduleWorkbench({...workbench,sourceLabel:workbench.store.get().snapshot?.project.name||'当前工作区会话'});setMobileSidebarOpen(false);setView('schedules');});}:undefined;
 const openConfiguration=workbench?()=>{if(!configurationOpen)leaveSchedule(()=>{setMobileSidebarOpen(false);setView('configuration');});}:undefined;
 const closeConfiguration=()=>{setView('chat');requestAnimationFrame(()=>rootRef.current?.querySelector<HTMLButtonElement>('button[data-configuration-trigger]')?.focus());};
 const closeSchedules=()=>{setView('chat');requestAnimationFrame(()=>rootRef.current?.querySelector<HTMLButtonElement>('button[data-schedule-trigger]')?.focus());};
 const openMain=workbench?()=>leaveSchedule(()=>{setMobileSidebarOpen(false);setView('main');}):undefined;
 const closeMain=()=>{setView('chat');requestAnimationFrame(()=>rootRef.current?.querySelector<HTMLButtonElement>('button[data-main-trigger]')?.focus());};
 const openGroups=workbench?()=>leaveSchedule(()=>{setMobileSidebarOpen(false);setView('groups');}):undefined;
 const closeGroups=()=>{setView('main');requestAnimationFrame(()=>rootRef.current?.querySelector<HTMLButtonElement>('button[data-main-trigger]')?.focus());};
 const navigateToChat=(action:()=>void)=>leaveSchedule(()=>{setView('chat');action();});
 const openSettings=()=>leaveSchedule(()=>{if(schedulesOpen||configurationOpen)setView('chat');setSettingsOpen(true);});
 const panelSidebarButton=<button type="button" onClick={toggleSidebar} aria-label={sidebarCollapsed?'展开会话侧栏':'打开会话侧栏'} className={cn('text-muted-foreground hover:text-foreground rounded-control grid size-7 place-items-center',!sidebarCollapsed&&'md:hidden')}><PanelLeftIcon className="size-4"/></button>;
 // Window-level action. The work panel owns this corner while it is open, so the
 // canvas workspace renders it only when nothing is covering that space.
 const fullscreenAction=<button type="button" onClick={()=>void toggleExpanded()} aria-label={expanded?'退出全屏':'全屏显示'} title={expanded?'退出全屏':'全屏显示'} className="text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] rounded-control pointer-events-auto grid size-8 shrink-0 place-items-center transition-colors">{expanded?<Minimize2Icon className="size-3.5"/>:<Maximize2Icon className="size-3.5"/>}</button>;
 return <SidebarNavigationContext.Provider value={{navigateToChat}}><div ref={rootRef} onKeyDown={event=>{if(settingsOpen)return;const command=getThreadShortcut(event.nativeEvent);if(command&&(view==='chat'||command==='new'||command==='sidebar')){event.preventDefault();runCommand(command);}}} className={cn('bg-background grid h-full grid-rows-[3rem_minmax(0,1fr)]',sidebarCollapsed?'md:grid-cols-[minmax(0,1fr)]':'md:grid-cols-[272px_minmax(0,1fr)]')}>
  <div className={cn('border-foreground/10 bg-foreground/[0.025] dark:bg-foreground/[0.04] hidden h-12 items-center gap-2 border-r border-b px-4',!sidebarCollapsed&&'md:flex')}>
   <img src="/raccoon-icon.png" alt="" width={24} height={24} className="size-6 shrink-0 rounded-md"/><span className="text-[13px] font-medium">Raccoon</span>
   <button type="button" onClick={()=>setSearchOpen(!searchOpen)} aria-label="搜索聊天" aria-expanded={searchOpen} className="text-muted-foreground hover:text-foreground rounded-control ms-auto grid size-7 shrink-0 place-items-center"><SearchIcon className="size-4"/></button>
   <button type="button" onClick={()=>setSidebarCollapsed(true)} aria-label="收起会话侧栏" aria-expanded aria-controls={SIDEBAR_REGION_ID} className="text-muted-foreground hover:text-foreground rounded-control -me-1.5 grid size-7 shrink-0 place-items-center transition-colors"><PanelLeftIcon className="size-4"/></button>
  </div>
  <div id={SIDEBAR_REGION_ID} className={cn('border-foreground/10 bg-foreground/[0.025] dark:bg-foreground/[0.04] hidden min-h-0 flex-col overflow-hidden border-r p-3 md:col-start-1 md:row-start-2',!sidebarCollapsed&&'md:flex')}><Sidebar navigation={navigation} onOpenSettings={openSettings} onOpenMain={openMain} mainOpen={mainOpen} onOpenSchedules={openSchedules} schedulesOpen={schedulesOpen} onOpenConfiguration={openConfiguration} configurationOpen={configurationOpen} navigateToChat={navigateToChat} searchOpen={searchOpen} onSearchOpenChange={setSearchOpen}/></div>
  <main aria-label="工作区主面板" className={cn('col-start-1 row-span-2 row-start-1 flex min-h-0 min-w-0 flex-col',!sidebarCollapsed&&'md:col-start-2')}>
   <div className={cn('relative flex min-h-0 flex-1 flex-col',view!=='chat'&&'hidden')}>
   <div className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-center gap-1 px-3 pt-2 md:px-4">
    <button type="button" onClick={()=>setMobileSidebarOpen(true)} aria-label="打开会话侧栏" className="text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] rounded-control pointer-events-auto grid size-8 shrink-0 place-items-center transition-colors md:hidden"><PanelLeftIcon className="size-4"/></button>
    {sidebarCollapsed&&<button type="button" onClick={()=>setSidebarCollapsed(false)} aria-label="展开会话侧栏" aria-expanded={false} aria-controls={SIDEBAR_REGION_ID} className="text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] rounded-control pointer-events-auto hidden size-8 shrink-0 place-items-center transition-colors md:grid"><PanelLeftIcon className="size-4"/></button>}
   </div>
   {notice}{displayError&&<p role="alert" className="text-destructive px-4 py-2 text-sm">{displayError}</p>}
    <div className="min-h-0 flex-1">{workbench?<CollaborationWorkspace key={`${workbench.projectId}:${workbench.sessionId}`} scope={{project_id:workbench.projectId,session_id:workbench.sessionId}} request={workbench.store.subagentRequest} enabled={view==='chat'&&workbench.ready!==false} {...canvasState} topRightActions={fullscreenAction}>{runtimeMatchesScope?<Thread/>:<div role="status" className="text-muted-foreground flex h-full items-center justify-center text-sm">正在加载聊天记录…</div>}</CollaborationWorkspace>:<CanvasWorkspace {...canvasState} topRightActions={fullscreenAction}><Thread/></CanvasWorkspace>}</div>
   </div>
   {schedulesOpen&&scheduleWorkbench&&<ScheduledTasks {...scheduleWorkbench} ref={scheduleNavigation} onClose={closeSchedules} onOpenRun={async(task,run)=>{if(run.execution==='main'){setView('main');return;}if(run.execution==='isolated'||!task.source?.project_id)throw new Error('执行来源不可直接打开，请查看本页审计记录。');await scheduleWorkbench.store.openConversation(task.source.project_id,run.session_id);setView('chat');}} headerLeading={panelSidebarButton}/ >}
   {mainOpen&&workbench&&<MainSession onClose={closeMain} onOpenGroups={openGroups} headerLeading={panelSidebarButton}/>}
   {groupsOpen&&workbench&&<GroupSession onClose={closeGroups} headerLeading={panelSidebarButton} store={groupSessionsStore}/>}
   {configurationOpen&&workbench&&<ConfigurationManager store={workbench.store} ref={configurationNavigation} onClose={closeConfiguration} headerLeading={panelSidebarButton}/>}
  </main>
  <Sheet open={mobileSidebarOpen} onOpenChange={setMobileSidebarOpen}><SheetContent side="left" className="bg-background w-[272px] overflow-hidden p-3 pt-12"><SheetTitle className="mb-3 flex items-center gap-2 text-[13px] font-medium"><img src="/raccoon-icon.png" alt="" width={24} height={24} className="size-6 shrink-0 rounded-md"/>Raccoon<span className="sr-only">会话侧栏</span><button type="button" aria-label="搜索聊天" aria-expanded={searchOpen} onClick={()=>setSearchOpen(!searchOpen)} className="text-muted-foreground ml-auto grid size-7 place-items-center"><SearchIcon className="size-4"/></button></SheetTitle><Sidebar navigation={navigation} onNavigate={()=>setMobileSidebarOpen(false)} onOpenMain={openMain} mainOpen={mainOpen} onOpenSchedules={openSchedules} schedulesOpen={schedulesOpen} onOpenConfiguration={openConfiguration} configurationOpen={configurationOpen} navigateToChat={navigateToChat} searchOpen={searchOpen} onSearchOpenChange={setSearchOpen} onOpenSettings={()=>{setMobileSidebarOpen(false);openSettings();}}/></SheetContent></Sheet>
  <SettingsPage navigation={navigation} open={settingsOpen} onClose={closeSettings}/>
 </div></SidebarNavigationContext.Provider>;
}
