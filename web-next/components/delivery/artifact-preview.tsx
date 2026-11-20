"use client";

import {useEffect,useState} from 'react';
import {TextMessagePartProvider} from '@assistant-ui/react';
import {File} from '@/components/assistant-ui/elements/file';
import {Image} from '@/components/assistant-ui/elements/image';
import {MarkdownText} from '@/components/assistant-ui/elements/markdown-text';
import {Button} from '@/components/ui/button';
import type {Artifact} from '@/lib/workbench/types';

const textSuffixes=new Set(['.md','.txt','.csv','.json','.py','.js','.ts','.tsx','.css','.yaml','.toml']);

function ImagePreview({src,name}:{src:string;name:string}){
 const [error,setError]=useState(false);
 return <div className="h-full overflow-auto p-5">
  {error?<p role="alert" className="text-destructive text-sm">图片无法读取，请刷新或下载后查看</p>:
   <Image.Root className="mx-auto block w-full max-w-none border-0 bg-transparent">
    <Image.Zoom src={src} alt={name}><Image.Preview src={src} alt={name} fit="contain" onError={()=>setError(true)} className="max-h-[75dvh] object-contain"/></Image.Zoom>
   </Image.Root>}
 </div>;
}

function PdfPreview({artifact}:{artifact:Artifact}){
 const [page,setPage]=useState(0);
 const [count,setCount]=useState(0);
 const [src,setSrc]=useState('');
 const [error,setError]=useState('');
 useEffect(()=>{
  const controller=new AbortController();let objectUrl='';
  setSrc('');setError('');
  if(artifact.size>50*1024*1024){setError('PDF 较大，请下载后查看');return;}
  const pageUrl=artifact.content_url.replace(/\/content(?=\?|$)/,`/pdf-page/${page}`);
  fetch(pageUrl,{signal:controller.signal,cache:'no-store'}).then(async response=>{
   if(!response.ok)throw new Error('PDF 无法预览，文件可能不存在、已加密或损坏，请下载后查看');
   const pages=Number(response.headers.get('X-PDF-Pages'));
   const blob=await response.blob();
   if(controller.signal.aborted)return;
   if(!Number.isInteger(pages)||pages<1)throw new Error('PDF 页数无法读取，请下载后查看');
   objectUrl=URL.createObjectURL(blob);setCount(pages);setSrc(objectUrl);
  }).catch(problem=>{if(!controller.signal.aborted)setError(problem.message);});
  return()=>{controller.abort();if(objectUrl)URL.revokeObjectURL(objectUrl);};
 },[artifact.content_url,artifact.size,page]);
 return <div className="flex h-full min-h-0 flex-col">
  <div className="border-foreground/10 flex shrink-0 items-center justify-center gap-3 border-b p-2">
   <Button variant="ghost" size="sm" aria-label="PDF 上一页" disabled={page===0||(!src&&!error)} onClick={()=>setPage(value=>value-1)}>上一页</Button>
   <span className="text-muted-foreground text-xs tabular-nums" aria-live="polite">{page+1} / {count||'…'}</span>
   <Button variant="ghost" size="sm" aria-label="PDF 下一页" disabled={!src||page+1>=count} onClick={()=>setPage(value=>value+1)}>下一页</Button>
  </div>
  <div className="min-h-0 flex-1">{error?<p role="alert" className="text-destructive p-5 text-sm">{error}</p>:src?<ImagePreview key={src} src={src} name={`PDF 第 ${page+1} 页`}/>:<p role="status" className="text-muted-foreground p-5 text-sm">正在渲染 PDF…</p>}</div>
 </div>;
}

function TextPreview({artifact}:{artifact:Artifact}){
 const [content,setContent]=useState<string|null>(null);
 const [error,setError]=useState('');
 useEffect(()=>{
  const controller=new AbortController();
  if(artifact.size>2*1024*1024){setError('文件较大，请下载后查看');return;}
  fetch(artifact.content_url,{signal:controller.signal,cache:'no-store'}).then(async response=>{
   if(!response.ok)throw new Error('文件不存在或无法读取，请刷新或下载后查看');
   const text=await response.text();if(!controller.signal.aborted)setContent(text);
  }).catch(problem=>{if(!controller.signal.aborted)setError(problem.message);});
  return()=>controller.abort();
 },[artifact.content_url,artifact.size]);
 if(error)return <p role="alert" className="text-destructive p-5 text-sm">{error}</p>;
 if(content===null)return <p role="status" className="text-muted-foreground p-5 text-sm">正在读取文件…</p>;
 return artifact.suffix==='.md'?<div className="h-full overflow-auto p-6"><TextMessagePartProvider text={content}><MarkdownText/></TextMessagePartProvider></div>:
  <pre className="h-full overflow-auto p-5 font-mono text-xs leading-6"><code>{content}</code></pre>;
}

export function ArtifactPreview({artifact}:{artifact:Artifact}){
 if(artifact.suffix==='.pdf')return <PdfPreview key={artifact.content_url} artifact={artifact}/>;
 if(artifact.mime.startsWith('image/'))return <ImagePreview key={artifact.content_url} src={artifact.content_url} name={artifact.name}/>;
 if(artifact.mime.startsWith('audio/')||artifact.mime.startsWith('video/'))return <div className="h-full overflow-auto p-5">
  <File.Player key={artifact.content_url} data={artifact.content_url} mimeType={artifact.mime} filename={artifact.name} sourceType="url" className="mx-auto max-w-xl"/>
  <p className="text-muted-foreground mt-4 text-center text-xs">若浏览器无法播放，请下载后查看</p>
 </div>;
 if(textSuffixes.has(artifact.suffix))return <TextPreview key={artifact.content_url} artifact={artifact}/>;
 return <div className="flex h-full flex-col items-center justify-center gap-4 overflow-auto p-5">
  <File.Root className="w-full max-w-sm">
   <File.Icon mimeType={artifact.mime}/>
   <div className="flex min-w-0 flex-1 flex-col gap-1"><File.Name title={artifact.name}>{artifact.name}</File.Name><File.Size bytes={artifact.size} className="text-xs"/></div>
   <File.Download data={artifact.download_url} mimeType={artifact.mime} filename={artifact.name} sourceType="url" aria-label={`下载 ${artifact.name}`} className="p-2">下载</File.Download>
  </File.Root>
  <p className="text-muted-foreground text-center text-sm">此文件暂不支持在线预览，请下载后查看</p>
 </div>;
}
