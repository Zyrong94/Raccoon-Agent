"use client";
import {useContext,useEffect,useId,useRef,useState} from 'react';
import {Menu} from '@base-ui/react/menu';
import {ArchiveIcon,ArchiveRestoreIcon,LoaderCircleIcon,MoreHorizontalIcon,PencilIcon,PinIcon,PinOffIcon} from 'lucide-react';
import type {ConversationSummary} from '@/lib/workbench/types';
import {cn} from '@/lib/utils';
import {useWorkbenchView} from './context';
import {SidebarNavigationContext} from './sidebar-context';
import {ThreadRenameInput} from './thread-rename-input';
import {menuContentClass,menuItemClass} from '../shared/workbench-styles';

const actionClass='text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] focus-visible:outline-ring grid size-6 shrink-0 place-items-center rounded-sm';
export function ProjectConversationRow({conversation,active,indented=false,projectName}:{conversation:ConversationSummary;active:boolean;indented?:boolean;projectName?:string}){
 const {navigation,blocked,navigationBlocked=blocked,pendingConversation}=useWorkbenchView();
 const {navigateToChat,onNavigate}=useContext(SidebarNavigationContext);
 const [renaming,setRenaming]=useState(false),[error,setError]=useState(''),[pending,setPending]=useState(false),[menuOpen,setMenuOpen]=useState(false);
 const trigger=useRef<HTMLButtonElement>(null),mounted=useRef(true);
 const statusId=useId();
 // Clicked but not yet confirmed by the server: show it as selected right away.
 const opening=!!pendingConversation&&pendingConversation.projectId===conversation.project_id&&pendingConversation.conversationId===conversation.id;
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 const update=async(changes:{title?:string;pinned?:boolean;archived?:boolean})=>{await navigation?.onUpdate(conversation.project_id,conversation.id,changes);};
 const mutate=async(changes:{pinned?:boolean;archived?:boolean})=>{setPending(true);setError('');try{await update(changes);}catch{if(mounted.current)setError('操作失败，请重试。');}finally{if(mounted.current)setPending(false);}};
 const open=()=>{if(navigationBlocked)return;const action=()=>{onNavigate?.();setError('');void navigation?.onOpen(conversation.project_id,conversation.id).catch(()=>{if(mounted.current)setError('无法打开聊天，请重试。');});};if(navigateToChat)navigateToChat(action);else action();};
 // Preload read-only history while the pointer rests here.
 const prewarmTimer=useRef<ReturnType<typeof setTimeout>>(undefined);
 const requestPrewarm=()=>{if(navigationBlocked||active||opening)return;if(prewarmTimer.current)clearTimeout(prewarmTimer.current);prewarmTimer.current=setTimeout(()=>navigation?.onPrewarm?.(conversation.project_id,conversation.id),200);};
 const cancelPrewarm=()=>{if(prewarmTimer.current){clearTimeout(prewarmTimer.current);prewarmTimer.current=undefined;}};
 useEffect(()=>()=>cancelPrewarm(),[]);
 return <div data-testid="conversation-row" data-conversation-active={active?'true':undefined} className="min-w-0">
  <div className={cn('group relative flex min-h-8 min-w-0 items-center rounded-control hover:bg-foreground/[0.04] focus-within:bg-foreground/[0.04]',(active||opening||menuOpen)&&'bg-foreground/[0.06]')}>
   {renaming?<div className={cn('min-w-0 flex-1',indented&&'pl-6')}><ThreadRenameInput title={conversation.title} onRename={title=>update({title})} onDone={restore=>{setRenaming(false);if(restore)requestAnimationFrame(()=>trigger.current?.focus());}}/></div>:<>
    <button type="button" ref={trigger} aria-label={conversation.title||'新聊天'} aria-describedby={conversation.pending||conversation.busy||conversation.pinned||opening?statusId:undefined} aria-current={active||opening?'page':undefined} aria-busy={opening||undefined} disabled={navigationBlocked} onClick={open} onPointerEnter={requestPrewarm} onPointerLeave={cancelPrewarm} onFocus={requestPrewarm} onBlur={cancelPrewarm} title={conversation.title} className={cn('focus-visible:outline-ring flex h-8 min-w-0 flex-1 items-center gap-1.5 rounded-control px-2 text-left text-[13px] text-foreground/85 disabled:opacity-50',indented&&'pl-8','group-hover:pr-20 group-focus-within:pr-20',menuOpen&&'pr-20')}>
     <span className="min-w-0 flex-1 truncate">{conversation.title||'新聊天'}</span>
     {projectName&&<span className="text-muted-foreground max-w-20 truncate text-[11px]">{projectName}</span>}
     {opening&&!active?<LoaderCircleIcon id={statusId} aria-label="正在打开" className="text-muted-foreground size-3.5 shrink-0 motion-safe:animate-spin"/>:conversation.pending?<span id={statusId} aria-label="等待确认" title="等待确认" className="size-1.5 shrink-0 rounded-full bg-amber-600"/>:conversation.busy?<LoaderCircleIcon id={statusId} aria-label="正在运行" className="text-muted-foreground size-3.5 shrink-0 motion-safe:animate-spin"/>:conversation.pinned?<PinIcon id={statusId} aria-label="已置顶" className="text-muted-foreground size-3 shrink-0"/>:null}
    </button>
    <div className={cn('bg-inherit absolute right-1 flex items-center opacity-0 group-hover:opacity-100 group-focus-within:opacity-100',menuOpen&&'opacity-100')}>
     <button type="button" aria-label={conversation.pinned?'取消置顶':'置顶'} title={conversation.pinned?'取消置顶':'置顶'} disabled={blocked||pending} className={actionClass} onClick={()=>void mutate({pinned:!conversation.pinned})}>{conversation.pinned?<PinOffIcon className="size-3.5"/>:<PinIcon className="size-3.5"/>}</button>
     <button type="button" aria-label={conversation.archived?'取消归档':'归档'} title={conversation.archived?'取消归档':'归档'} disabled={blocked||pending||conversation.busy||conversation.pending} className={actionClass} onClick={()=>void mutate({archived:!conversation.archived})}>{conversation.archived?<ArchiveRestoreIcon className="size-3.5"/>:<ArchiveIcon className="size-3.5"/>}</button>
     <Menu.Root open={menuOpen} onOpenChange={setMenuOpen}><Menu.Trigger aria-label="会话操作" title="更多操作" disabled={blocked||pending} className={actionClass}><MoreHorizontalIcon className="size-3.5"/></Menu.Trigger>
      <Menu.Portal><Menu.Positioner side="right" align="start" sideOffset={6} className="z-50"><Menu.Popup className={menuContentClass}>
       <Menu.Item className={menuItemClass} onClick={()=>setRenaming(true)}><PencilIcon className="size-3.5"/>重命名</Menu.Item>
       <Menu.Item className={menuItemClass} onClick={()=>void mutate({pinned:!conversation.pinned})}><PinIcon className="size-3.5"/>{conversation.pinned?'取消置顶':'置顶'}</Menu.Item>
       <Menu.Item disabled={conversation.busy||conversation.pending} className={cn(menuItemClass,'data-disabled:opacity-40')} onClick={()=>void mutate({archived:!conversation.archived})}><ArchiveIcon className="size-3.5"/>{conversation.archived?'取消归档':'归档'}</Menu.Item>
      </Menu.Popup></Menu.Positioner></Menu.Portal>
     </Menu.Root>
    </div>
   </>}
  </div>
  {error&&<p role="alert" className="text-destructive px-2 py-1 text-xs">{error}</p>}
 </div>;
}
