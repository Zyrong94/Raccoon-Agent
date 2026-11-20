"use client";

import {useState} from 'react';
import {ArrowLeftIcon,DownloadIcon,FilesIcon,Maximize2Icon,Minimize2Icon,RefreshCwIcon,XIcon} from 'lucide-react';
import {File} from '@/components/assistant-ui/elements/file';
import {ghostButton} from '@/components/assistant-ui/elements/surfaces';
import {CanvasSplitHeader} from '@/components/assistant-ui/elements/canvas-split';
import {ArtifactPreview} from './artifact-preview';
import {CanvasPreview} from './canvas-preview';
import type {Artifact,Canvas} from '@/lib/workbench/types';

type Props={artifacts:Artifact[];artifact?:Artifact;canvas?:Canvas|null;expanded:boolean;onExpand:()=>void;onClose:()=>void;onSelect:(id:string|null)=>void};
export function SessionFiles({artifacts,artifact,canvas,expanded,onExpand,onClose,onSelect}:Props){
 const [refresh,setRefresh]=useState(0);
 const back=<button type="button" aria-label="返回文件列表" title="返回文件列表" className={`${ghostButton} size-7`} onClick={()=>onSelect(null)}><ArrowLeftIcon className="size-3.5"/></button>;
 const expand=<button type="button" aria-label={expanded?'还原文件面板':'展开文件面板'} title={expanded?'还原':'展开'} className={`${ghostButton} size-7`} onClick={onExpand}>{expanded?<Minimize2Icon className="size-3.5"/>:<Maximize2Icon className="size-3.5"/>}</button>;
 if(artifact&&['.html','.htm'].includes(artifact.suffix))return <CanvasPreview key={artifact.id} artifact={artifact} canvas={canvas?.artifact_id===artifact.id?canvas:{artifact_id:artifact.id,title:artifact.name,request_id:artifact.id}} expanded={expanded} onExpand={onExpand} onClose={onClose} backControl={back}/>;
 if(artifact)return <>
  <CanvasSplitHeader title={artifact.name} onClose={onClose} closeLabel="关闭文件面板"/>
  <div className="border-foreground/10 flex h-11 shrink-0 items-center gap-2 border-b px-3">
   {back}<span className="text-muted-foreground min-w-0 flex-1 truncate text-xs">{artifact.suffix.slice(1).toUpperCase()}<span className="mx-2">·</span><File.Size bytes={artifact.size}/></span>
   <button type="button" aria-label="刷新文件" title="刷新" className={`${ghostButton} size-7`} onClick={()=>setRefresh(value=>value+1)}><RefreshCwIcon className="size-3.5"/></button>
   <a href={artifact.download_url} aria-label="下载文件" title="下载" className={`${ghostButton} size-7`}><DownloadIcon className="size-3.5"/></a>{expand}
  </div>
  <div className="min-h-0 flex-1"><ArtifactPreview key={`${artifact.id}:${refresh}`} artifact={artifact}/></div>
 </>;
 return <>
  <div className="border-foreground/10 flex h-12 shrink-0 items-center gap-2 border-b px-3.5">
   <FilesIcon className="text-muted-foreground size-4"/><h2 className="flex-1 text-[13px] font-medium">会话文件</h2><span className="text-muted-foreground text-xs">{artifacts.length} 个文件</span>{expand}
   <button type="button" aria-label="关闭文件面板" title="关闭" className={`${ghostButton} size-7`} onClick={onClose}><XIcon className="size-3.5"/></button>
  </div>
  <div className="min-h-0 flex-1 overflow-auto p-3">
   {artifacts.length?<ul className="space-y-1">{artifacts.map(item=><li key={item.id}>
    <File.Root variant="ghost" className="flex w-full gap-2 py-0">
     <button type="button" aria-label={`打开 ${item.name}`} title={item.name} className="flex min-w-0 flex-1 items-center gap-3 rounded-md py-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={()=>onSelect(item.id)}><File.Icon mimeType={item.mime}/><File.Name>{item.name}</File.Name></button>
     <File.Size bytes={item.size} className="text-xs"/><a href={item.download_url} aria-label={`下载 ${item.name}`} title="下载" className={`${ghostButton} size-7 shrink-0`}><DownloadIcon className="size-3.5"/></a>
    </File.Root>
   </li>)}</ul>:<p className="text-muted-foreground px-3 py-10 text-center text-sm">这个会话还没有生成文件</p>}
  </div>
 </>;
}
