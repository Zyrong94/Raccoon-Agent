"use client";

import {useEffect,useState,type ReactNode} from 'react';
import {Tabs} from '@base-ui/react/tabs';
import {DownloadIcon,Maximize2Icon,Minimize2Icon,RefreshCwIcon} from 'lucide-react';
import {CanvasSplitHeader} from '@/components/assistant-ui/elements/canvas-split';
import {ghostButton} from '@/components/assistant-ui/elements/surfaces';
import type {Artifact,Canvas} from '@/lib/workbench/types';

export function CanvasPreview({artifact,canvas,expanded,onExpand,onClose,backControl}:{artifact:Artifact;canvas:Canvas;expanded:boolean;onExpand:()=>void;onClose:()=>void;backControl?:ReactNode}){
 const [mode,setMode]=useState('preview');
 const [refresh,setRefresh]=useState(0);
 const [content,setContent]=useState<string|null>(null);
 const [ready,setReady]=useState(false);
 const [loading,setLoading]=useState(true);
 const [error,setError]=useState('');
 const [copyError,setCopyError]=useState('');
 const version=`${canvas.revision??''}-${refresh}`;
 const frameURL=`${artifact.canvas_url??artifact.content_url}${(artifact.canvas_url??artifact.content_url).includes('?')?'&':'?'}canvas_revision=${encodeURIComponent(version)}`;
 useEffect(()=>{
  const controller=new AbortController();
  setReady(false);setContent(null);setLoading(true);setError('');setCopyError('');
  if(mode==='source'&&artifact.size>2*1024*1024){setError('文件较大，请下载后查看源码');setLoading(false);return;}
  fetch(artifact.content_url,{signal:controller.signal,cache:'no-store'}).then(async response=>{
   if(!response.ok)throw new Error('页面文件不存在或无法读取，请让 Agent 重新生成');
   if(mode==='source'){const text=await response.text();if(!controller.signal.aborted){setContent(text);setLoading(false);}}
   else {await response.body?.cancel();if(!controller.signal.aborted)setReady(true);}
  }).catch(problem=>{if(!controller.signal.aborted){setError(problem.message);setLoading(false);}});
  return()=>controller.abort();
 },[artifact.content_url,artifact.size,mode,version]);
 const copy=async()=>{try{await navigator.clipboard.writeText(content!);setCopyError('');}catch{setCopyError('复制失败，请选择源码后复制');}};
 return <>
  <CanvasSplitHeader title={canvas.title||artifact.name} onClose={onClose} onCopy={content!==null?()=>void copy():undefined}/>
  <Tabs.Root value={mode} onValueChange={value=>setMode(String(value))} className="flex min-h-0 flex-1 flex-col">
   <div className="border-foreground/10 flex h-11 shrink-0 items-center gap-1 border-b px-3">
    {backControl}
    <Tabs.List aria-label="Canvas 显示方式" className="bg-foreground/[0.04] flex gap-0.5 rounded-control p-0.5">
     {['preview','source'].map(value=><Tabs.Tab key={value} value={value} className="text-muted-foreground data-[active]:bg-background data-[active]:text-foreground rounded-control px-3 py-1 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring">{value==='preview'?'预览':'源码'}</Tabs.Tab>)}
    </Tabs.List>
    <div className="ml-auto flex gap-1">
     <button type="button" aria-label="刷新画布" title="刷新" className={`${ghostButton} size-7`} onClick={()=>setRefresh(value=>value+1)}><RefreshCwIcon className="size-3.5"/></button>
     <a href={artifact.download_url} aria-label="下载文件" title="下载" className={`${ghostButton} size-7`}><DownloadIcon className="size-3.5"/></a>
     <button type="button" aria-label={expanded?'还原画布':'展开画布'} title={expanded?'还原':'展开'} className={`${ghostButton} size-7`} onClick={onExpand}>{expanded?<Minimize2Icon className="size-3.5"/>:<Maximize2Icon className="size-3.5"/>}</button>
    </div>
   </div>
   <Tabs.Panel value={mode} className="relative min-h-0 flex-1 overflow-hidden">
    {copyError&&<p role="alert" className="text-destructive p-3 text-sm">{copyError}</p>}
    {error?<p role="alert" className="text-destructive p-5 text-sm">{error}</p>:<>
     {loading&&<p role="status" className="text-muted-foreground absolute inset-x-0 top-0 bg-background/90 p-4 text-sm">{mode==='source'?'正在读取源码…':'正在载入画布…'}</p>}
     {mode==='source'&&content!==null&&<pre className="h-full overflow-auto p-5 font-mono text-xs leading-6"><code>{content}</code></pre>}
     {mode==='preview'&&ready&&<iframe key={frameURL} src={frameURL} title={canvas.title||artifact.name} sandbox="allow-scripts" referrerPolicy="no-referrer" className="h-full w-full border-0 bg-white" onLoad={()=>setLoading(false)} onError={()=>{setError('画布载入失败，请刷新重试');setLoading(false);}}/>}
    </>}
   </Tabs.Panel>
  </Tabs.Root>
 </>;
}
