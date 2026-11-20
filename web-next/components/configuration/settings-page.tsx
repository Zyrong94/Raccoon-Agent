"use client";

// Full-window settings view for the adapted home/demo shell.
// Art direction: framed-sheet (design-taste-frontend app sheet, seed 1687204941):
// a tinted frame holding a white sheet with a 1px edge, 16px corners and no
// shadow. Settings use the dealt two-column row (label column beside a panel);
// the archived list follows the dealt list pattern: title row, toolbar, rows on
// rules, one visible action per row. Chat stays mounted underneath.
import {useEffect,useState} from 'react';
import {useAui,useAuiState} from '@assistant-ui/react';
import {Switch} from '@base-ui/react/switch';
import {ArchiveIcon,ArrowLeftIcon,PaletteIcon,TerminalIcon} from 'lucide-react';
import {Button} from '@/components/ui/button';
import {Dialog,DialogContent,DialogDescription,DialogFooter,DialogHeader,DialogTitle} from '@/components/ui/dialog';
import {cn} from '@/lib/utils';
import {threadScopeId} from '@/lib/chat/runtime';
import type {ArchiveConversation} from '@/lib/workbench/types';
import {useWorkbenchView,type ProjectNavigation} from '../workbench/context';
import {applyTheme,readStoredTheme,type Theme} from '../workbench/theme';
import {ArchivedProjects} from '../workbench/archived-projects';

type Section='archived'|'appearance';

const SECTIONS=[{id:'archived' as const,label:'已归档的聊天',Icon:ArchiveIcon},{id:'appearance' as const,label:'外观',Icon:PaletteIcon}];

export function SettingsPage({navigation,open,onClose}:{navigation:ProjectNavigation;open:boolean;onClose:()=>void}){
 const {workbench}=useWorkbenchView();
 const aui=useAui();
 const archivedIds=useAuiState(s=>s.threads.archivedThreadIds);
 const items=useAuiState(s=>s.threads.threadItems);
 const [section,setSection]=useState<Section>('archived');
 const [theme,setTheme]=useState<Theme>('light');
 const [pendingDelete,setPendingDelete]=useState<{id:string;title:string;onDeleted?:()=>Promise<void>}|null>(null);
 useEffect(()=>{if(open){setTheme(readStoredTheme());setPendingDelete(null);}},[open]);
 useEffect(()=>{setPendingDelete(null);},[workbench?.projectId]);
 useEffect(()=>{
  if(!open)return;
  const onKey=(event:KeyboardEvent)=>{if(event.key==='Escape')onClose();};
  document.addEventListener('keydown',onKey);
  return ()=>document.removeEventListener('keydown',onKey);
 },[open,onClose]);
 if(!open)return null;
 const dark=theme==='dark';
 const setDark=(checked:boolean)=>{const next:Theme=checked?'dark':'light';setTheme(next);applyTheme(next);};
 const confirmDelete=()=>{const target=pendingDelete;if(!target)return;setPendingDelete(null);void Promise.resolve(aui.threads.item({id:target.id}).delete()).then(()=>target.onDeleted?.()).catch(()=>{});};
 const archived=archivedIds.flatMap(id=>{const item=items.find(entry=>entry.id===id);return item?[item]:[];});
 const archivedItem=(item:ArchiveConversation)=>archived.find(thread=>thread.remoteId===threadScopeId({projectId:item.storage_project_id,sessionId:item.session_id}));
 return <div role="region" aria-label="设置" className="bg-background fixed inset-0 z-50 grid grid-cols-1 grid-rows-[auto_minmax(0,1fr)] md:grid-cols-[15rem_minmax(0,1fr)] md:grid-rows-1">
  <aside className="bg-foreground/[0.03] flex min-h-0 flex-col px-3 md:py-1">
   <div className="flex h-12 shrink-0 items-center gap-1">
    <button type="button" onClick={onClose} autoFocus aria-label="返回会话" className="text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] rounded-control -ms-1 grid size-7 place-items-center transition-colors"><ArrowLeftIcon className="size-4"/></button>
    <span className="text-sm font-medium">设置</span>
   </div>
   <nav className="flex gap-0.5 overflow-x-auto pb-2 md:flex-col md:pb-0">
    {SECTIONS.map(({id,label,Icon})=><button key={id} type="button" onClick={()=>setSection(id)} aria-current={section===id?'page':undefined} className={cn('rounded-control flex h-8 shrink-0 items-center gap-2 px-2 text-sm transition-colors',section===id?'bg-foreground/[0.07] text-foreground':'text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground')}><Icon className="size-4"/>{label}</button>)}
   </nav>
   <p className="text-muted-foreground mt-auto hidden px-2 pb-1 text-xs md:block">Raccoon · 本地工作区</p>
  </aside>
  <div className="bg-foreground/[0.03] min-h-0 md:p-2">
   <div className="border-foreground/10 bg-background h-full overflow-y-auto border-t md:rounded-[16px] md:border">
    <div className="mx-auto w-full max-w-[960px] px-5 py-6 md:px-10 md:py-8">
     {section==='archived'?<ArchivedProjects navigation={navigation} canDelete={item=>item.storage_project_id===workbench?.projectId&&!!archivedItem(item)} onDelete={(item,onDeleted)=>{const thread=archivedItem(item);if(thread)setPendingDelete({id:thread.id,title:item.title||'新会话',onDeleted});}}/>:<>
      <h1 className="text-2xl font-semibold tracking-[-0.01em]">外观</h1>
      <p className="text-muted-foreground mt-1.5 text-sm">只影响这台机器上的 Raccoon。</p>
      <div className="mt-8 grid gap-6 md:grid-cols-[minmax(0,320px)_minmax(0,1fr)] md:gap-10">
       <div>
        <h2 className="text-base font-medium">主题</h2>
        <p className="text-muted-foreground mt-1.5 text-sm leading-6">深色模式立即应用，设置保存在此浏览器。</p>
       </div>
       <div className="bg-foreground/[0.035] rounded-[16px] p-5">
        <div className="flex items-center justify-between gap-6">
         <div className="min-w-0">
          <div className="flex items-center gap-2">
           <p className="text-sm font-medium">深色模式</p>
           <span className="bg-foreground/[0.06] text-muted-foreground rounded-full px-1.5 text-[11px] leading-5">{dark?'已开启':'已关闭'}</span>
          </div>
          <p className="text-muted-foreground mt-1 text-xs">只改变界面配色，不影响会话内容。</p>
         </div>
         <Switch.Root checked={dark} onCheckedChange={setDark} aria-label="深色模式" className="border-foreground/20 bg-foreground/10 data-[checked]:border-foreground data-[checked]:bg-foreground focus-visible:ring-ring focus-visible:ring-offset-background relative h-4 w-7 shrink-0 rounded-full border transition-colors outline-none focus-visible:ring-2 focus-visible:ring-offset-2">
          <Switch.Thumb className="bg-foreground/40 data-[checked]:bg-background absolute start-0.5 top-1/2 size-3 -translate-y-1/2 rounded-full transition-transform data-[checked]:translate-x-3"/>
         </Switch.Root>
        </div>
       </div>
      </div>
      <div className="mt-10">
       <div className="flex items-baseline justify-between">
        <h2 className="text-base font-medium">预览</h2>
        <span className="text-muted-foreground text-xs">{dark?'深色':'浅色'}界面</span>
       </div>
       <div className="border-foreground/10 bg-background mt-3 overflow-hidden rounded-[16px] border">
        <div className="border-foreground/[0.07] flex items-center gap-2 border-b px-4 py-2.5">
         <span className="text-[13px] font-medium">恢复会话的测试</span>
         <span className="text-muted-foreground text-[11px]">本地工作区</span>
        </div>
        <div className="px-5 py-5 md:px-6">
         <div className="flex justify-end"><p className="bg-foreground/[0.06] max-w-[75%] rounded-[10px] px-3 py-2 text-[13px]">把恢复逻辑补上测试，然后跑一遍。</p></div>
         <p className="mt-4 max-w-[85%] text-[13px] leading-6">已添加 <code className="bg-foreground/[0.05] rounded px-1 font-mono text-xs">test_resume_without_id</code>，并把失败路径也覆盖了。</p>
         <div className="border-foreground/10 text-muted-foreground mt-2.5 inline-flex items-center gap-1.5 rounded-[8px] border px-2 py-1 text-[11px]"><TerminalIcon className="size-3"/>pytest · 全部通过</div>
         <div className="border-foreground/15 text-muted-foreground mt-5 flex items-center gap-2 rounded-[10px] border px-3 py-2 text-[13px]"><span className="min-w-0 flex-1 truncate">继续追问…</span><span className="text-xs">↵</span></div>
        </div>
       </div>
      </div>
     </>}
    </div>
   </div>
  </div>
  <Dialog open={!!pendingDelete} onOpenChange={next=>{if(!next)setPendingDelete(null)}}>
   <DialogContent className="sm:max-w-sm">
    <DialogHeader>
     <DialogTitle>删除会话？</DialogTitle>
     <DialogDescription>将永久删除「{pendingDelete?.title}」的会话记录，删除后无法恢复。</DialogDescription>
    </DialogHeader>
    <DialogFooter>
     <Button variant="outline" onClick={()=>setPendingDelete(null)}>取消</Button>
     <Button variant="destructive" onClick={confirmDelete}>删除</Button>
    </DialogFooter>
   </DialogContent>
  </Dialog>
 </div>;
}
