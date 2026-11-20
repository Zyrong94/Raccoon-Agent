"use client";
import {useState,type ReactNode} from 'react';
import {CanvasSplit,CanvasSplitDocument,CanvasSplitThread} from '@/components/assistant-ui/elements/canvas-split';
import {FileTextIcon,LayoutListIcon,Maximize2Icon,Minimize2Icon,MinusIcon,XIcon} from 'lucide-react';
import {paper} from '@/components/assistant-ui/elements/surfaces';
import {SessionFiles} from './session-files';
import type {Artifact,Canvas} from '@/lib/workbench/types';

export function CanvasWorkspace({artifacts=[],canvas,children,sidePanel,taskCards,extraSections,topRightActions}:{artifacts?:Artifact[];canvas?:Canvas|null;children:ReactNode;sidePanel?:{requestId:string;content:(actions:ReactNode)=>ReactNode};taskCards?:ReactNode;extraSections?:ReactNode;topRightActions?:ReactNode}){
 const [choice,setChoice]=useState<{request?:string;open:boolean;selected:string|null}|null>(null);
 const [expanded,setExpanded]=useState(false);
 const [contentOpen,setContentOpen]=useState<boolean|null>(null);
  const [sideChoice,setSideChoice]=useState<{requestId:string;view:'detail'|'files'|'closed'}|null>(null);
  const detailOpen=!!sidePanel&&(sideChoice?.requestId!==sidePanel.requestId||sideChoice.view==='detail');
 const canvasArtifact=artifacts.find(item=>item.id===canvas?.artifact_id&&['.html','.htm'].includes(item.suffix));
 const manual=choice!==null&&choice.request===canvas?.request_id;
 const fileOpen=manual?choice.open:!!canvasArtifact;
 const open=detailOpen||fileOpen;
 const artifact=artifacts.find(item=>item.id===(manual?choice.selected:canvasArtifact?.id));
 const select=(id:string|null)=>{setChoice({request:canvas?.request_id,open:true,selected:id});if(sidePanel)setSideChoice({requestId:sidePanel.requestId,view:'files'});};
 const close=()=>{setChoice({request:canvas?.request_id,open:false,selected:artifact?.id??null});if(sidePanel)setSideChoice({requestId:sidePanel.requestId,view:'closed'});setExpanded(false);};
 // The child session header carries its own panel controls: a subagent panel is
 // only ever the subagent, files and canvas stay in the session content list.
 const panelActions=<div className="flex shrink-0 items-center gap-0.5">
  <button type="button" onClick={()=>setExpanded(value=>!value)} aria-label={expanded?'退出面板全屏':'面板全屏'} aria-pressed={expanded} title={expanded?'退出面板全屏':'面板全屏'} className="text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] rounded-control focus-visible:outline-ring grid size-7 place-items-center">{expanded?<Minimize2Icon aria-hidden="true" className="size-3.5"/>:<Maximize2Icon aria-hidden="true" className="size-3.5"/>}</button>
  <button type="button" onClick={close} aria-label="关闭工作面板" title="关闭工作面板" className="text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] rounded-control focus-visible:outline-ring grid size-7 place-items-center"><XIcon aria-hidden="true" className="size-4"/></button>
 </div>;
 return <div className="@container/workspace relative h-full min-h-0 min-w-0">
  {contentOpen!==true&&!expanded&&<button type="button" aria-label="展开会话内容" aria-expanded={false} title="输出文件与子智能体" onClick={()=>setContentOpen(true)} className={`${paper} absolute top-4 left-4 z-20 flex size-9 items-center justify-center rounded-xl shadow-sm focus-visible:outline-2 focus-visible:outline-ring ${contentOpen===null&&!open?'@min-[1248px]/workspace:hidden':''}`}><LayoutListIcon aria-hidden="true" className="text-muted-foreground size-4"/></button>}
  <aside aria-label="会话内容" hidden={contentOpen===false||(contentOpen===null&&open)} onClickCapture={event=>{if((event.target as Element).closest('button'))setContentOpen(false);}} className={`${paper} absolute top-4 left-4 z-20 max-h-[calc(100%_-_2rem)] w-64 max-w-[calc(100%_-_2rem)] overflow-y-auto rounded-2xl p-3 shadow-sm ${contentOpen===null&&!open?'hidden @min-[1248px]/workspace:block':''}`}>
   <button type="button" aria-label="收起会话内容" title="收起会话内容" onClick={()=>setContentOpen(false)} className="text-muted-foreground hover:bg-foreground/[0.04] focus-visible:ring-ring absolute top-2 right-2 flex size-6 items-center justify-center rounded-control focus-visible:outline-none focus-visible:ring-2"><MinusIcon aria-hidden="true" className="size-3.5"/></button>
   <section aria-label="输出文件">
    <h2 className="text-muted-foreground pr-8 pb-1.5 pl-2 text-xs font-medium">输出文件</h2>
    {artifacts.length?<ul className="space-y-1">{artifacts.map(item=><li key={item.id}><button type="button" aria-label={`打开文件：${item.name}`} title={item.name} onClick={()=>{select(item.id);setExpanded(false);}} className="hover:bg-foreground/[0.04] focus-visible:ring-ring flex w-full min-w-0 items-center gap-2 rounded-control px-2 py-2 text-left focus-visible:outline-none focus-visible:ring-2"><FileTextIcon aria-hidden="true" className="text-muted-foreground size-4 shrink-0"/><span className="min-w-0 truncate text-[13px]">{item.id===canvasArtifact?.id?canvas?.title||item.name:item.name}</span></button></li>)}</ul>:<p className="text-muted-foreground px-2 py-1 text-xs leading-5">暂无输出文件</p>}
   </section>
   <section aria-label="子智能体" className="mt-3">
    <h2 className="text-muted-foreground px-2 pb-1.5 text-xs font-medium">子智能体</h2>
    {taskCards??<p className="text-muted-foreground px-2 py-1 text-xs leading-5">暂无子智能体</p>}
   </section>
   {extraSections}
  </aside>
  {!open&&topRightActions&&<div className="absolute top-2 right-3 z-20 flex items-center gap-1">{topRightActions}</div>}
  <CanvasSplit className="h-full min-h-0 min-w-0 max-w-none rounded-none border-0 [&>div]:h-full [&>div]:min-h-0 [&>div]:flex-row">
   <CanvasSplitThread className={expanded&&open?'hidden':open?'min-h-0 min-w-0 w-[48%] shrink-0 gap-0 border-r border-b-0 p-0 @xl:w-[48%] @xl:overflow-hidden':'min-h-0 min-w-0 w-full flex-1 gap-0 border-0 p-0 @xl:w-full @xl:overflow-hidden'}><div className="flex min-h-0 flex-1 flex-col">{children}</div></CanvasSplitThread>
   <CanvasSplitDocument role="region" aria-label={detailOpen?'子代理协作详情':artifact&&['.html','.htm'].includes(artifact.suffix)?'Canvas 画布':'会话文件'} className={open?'min-h-0 overflow-hidden':'hidden'}>
    <div className={detailOpen?'flex min-h-0 flex-1 flex-col':'hidden'}>{sidePanel?.content(panelActions)}</div>
    <div className={detailOpen?'hidden':'flex min-h-0 flex-1 flex-col'}>{(fileOpen||choice!==null||canvasArtifact)&&<SessionFiles artifacts={artifacts} artifact={artifact} canvas={canvas} expanded={expanded} onExpand={()=>setExpanded(value=>!value)} onClose={close} onSelect={select}/>}</div>
   </CanvasSplitDocument>
  </CanvasSplit>
 </div>;
}
