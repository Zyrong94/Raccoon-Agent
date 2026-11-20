"use client";
import {useMemo,type ReactNode} from 'react';
import type {ThreadMessageLike} from '@assistant-ui/react';
import Markdown,{type Components} from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {ToolRowView,type ToolRow} from '@/components/chat/tool-row';
import {ToolTraceGroupView,toolGroupFailed,toolGroupLabel} from '@/components/chat/tool-trace-group';
import {joinTurns,projectDisplayMessages} from '@/lib/chat/messages';
import type {Message} from '@/lib/workbench/types';

const markdownComponents:Components={a:props=><a {...props} target="_blank" rel="noreferrer noopener"/>,img:props=><a href={props.src as string} target="_blank" rel="noreferrer noopener">{props.alt||'查看图片'}</a>};

export type TranscriptLabels={user:string;assistant:string};
const defaultLabels:TranscriptLabels={user:'指令',assistant:'子代理'};

function MarkdownBlock({text}:{text:string}){
 return <div className="aui-md text-[13px] leading-6 break-words"><Markdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{text}</Markdown></div>;
}

type ToolCallPart={type:'tool-call';toolCallId?:string;toolName:string;args?:unknown;argsText?:string;result?:unknown;isError?:boolean};

// The projection pairs calls and results, so a row carries both. Calls without a
// result are running while the session is active; otherwise the parent's
// cancelled presentation explains why no result exists.
function toolRow(part:ToolCallPart,running:boolean,index:number):ToolRow{
 const status=part.result===undefined
  ?running?{type:'running' as const}:{type:'incomplete' as const,reason:'cancelled' as const}
  :{type:'complete' as const};
 return {id:part.toolCallId??`tool-${index}`,name:part.toolName,args:part.args as unknown as Record<string,unknown>|undefined,argsText:part.argsText,result:part.result,isError:part.isError,status};
}

// Prompts sit on the right as bubbles, mirroring the parent composer; replies and
// tool traces stay on the left like the parent thread.
function UserTurn({turn,label,showLabel}:{turn:ThreadMessageLike;label:string;showLabel:boolean}){
 const parts=typeof turn.content==='string'?[{type:'text' as const,text:turn.content}]:turn.content;
 const text=parts.map(part=>part.type==='text'?part.text:'').join('\n\n').trim();
 if(!text)return null;
 return <article aria-label={label} className="flex w-full flex-col items-end gap-1">
  {showLabel&&<p className="text-muted-foreground px-1 text-[10px] leading-4">{label}</p>}
  <div className="bg-muted rounded-thread max-w-[80%] px-4 py-2.5">
   <MarkdownBlock text={text}/>
  </div>
 </article>;
}

function AssistantTurn({turn,running,label,showLabel}:{turn:ThreadMessageLike;running:boolean;label:string;showLabel:boolean}){
 if(turn.role!=='assistant')return null;
 const parts=typeof turn.content==='string'?[{type:'text' as const,text:turn.content}]:turn.content;
 const nodes:ReactNode[]=[];
 let rows:ToolRow[]=[];
 let texts:string[]=[];
 const flushText=()=>{const text=texts.join('\n\n');texts=[];if(text.trim())nodes.push(<MarkdownBlock key={`text-${nodes.length}`} text={text}/>);};
 const flushRows=()=>{if(!rows.length)return;const group=rows;rows=[];nodes.push(<ToolTraceGroupView key={`tools-${nodes.length}`} label={toolGroupLabel(group)} count={group.length} failed={toolGroupFailed(group)}>{group.map(row=><ToolRowView key={row.id} row={row}/>)}</ToolTraceGroupView>);};
 for(const [index,part] of parts.entries()){
  if(part.type==='tool-call'){flushText();rows.push(toolRow(part,running,index));}
  else if(part.type==='text'){flushRows();texts.push(part.text);}
 }
 flushText();flushRows();
 if(!nodes.length)return null;
 return <article aria-label={label} className="w-full">
  {showLabel&&<p className="text-muted-foreground mb-1 text-[10px] leading-4">{label}</p>}
  <div className="space-y-2">{nodes}</div>
 </article>;
}

export function ReadOnlyTranscript({messages,running=false,labels=defaultLabels,showLabels=false}:{messages:Message[];running?:boolean;labels?:TranscriptLabels;showLabels?:boolean}){
 const turns=useMemo(()=>joinTurns(projectDisplayMessages(messages).messages),[messages]);
 return <>{turns.map(turn=>turn.role==='user'
  ?<UserTurn key={turn.id} turn={turn} label={labels.user} showLabel={showLabels}/>
  :<AssistantTurn key={turn.id} turn={turn} running={running} label={labels.assistant} showLabel={showLabels}/>)}</>;
}
