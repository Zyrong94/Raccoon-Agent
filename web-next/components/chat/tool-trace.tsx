// Adapted from assistant-ui/apps/docs/lib/tool-trace.tsx (MIT); see docs/home-demo-provenance.md.
"use client";

import {useScrollLock} from '@assistant-ui/react';
import {AlertCircle,ChevronRight} from 'lucide-react';
import {useRef,useState,type CSSProperties,type ReactNode} from 'react';
import {CollapsibleRoot as Collapsible,CollapsibleContent,CollapsibleTrigger} from '@/components/assistant-ui/elements/collapsible-root';
import {cn} from '@/lib/utils';

// Tool results may be JSON text, native objects, or ordinary/truncated text.
// Preserve the entire received envelope; never extract only its text field.
export function formatToolValue(value:unknown):string{
  if(value===undefined)return '';
  if(typeof value==='string'){
    try{JSON.parse(value);}catch{return value;}
    // Format validated tokens rather than parsed values: numbers and duplicate
    // keys must survive exactly as received from Python.
    const tokens=value.match(/"(?:\\.|[^"\\])*"|[{}\[\],:]|[^\s{}\[\],:]+/g)??[];
    let depth=0;
    let formatted='';
    const newline=()=>`\n${'  '.repeat(depth)}`;
    tokens.forEach((token,index)=>{
      if(token==='{'||token==='['){
        formatted+=token;
        depth++;
        if(tokens[index+1]!=='}'&&tokens[index+1]!==']')formatted+=newline();
      }else if(token==='}'||token===']'){
        depth--;
        if(tokens[index-1]!=='{'&&tokens[index-1]!=='[')formatted+=newline();
        formatted+=token;
      }else if(token===',')formatted+=`,${newline()}`;
      else if(token===':')formatted+=': ';
      else formatted+=token;
    });
    return formatted;
  }
  return JSON.stringify(value,null,2)??'';
}

function ToolJsonBlock({label,value,error=false}:{label:string;value:string;error?:boolean}){
  return <div className="min-w-0 space-y-1">
    <p className="text-muted-foreground/60 text-[10px] font-medium">{label}</p>
    <pre className={cn('max-h-60 overflow-auto rounded-md p-2.5 text-[11px] leading-relaxed',error?'bg-destructive/20 text-destructive':'text-muted-foreground bg-muted/50')}>{value}</pre>
  </div>;
}

export function ToolTraceCard({icon,signature,description,parameters,result,error=false}:{
  icon:ReactNode;signature:string;description:ReactNode;parameters:string;result:string;error?:boolean;
}){
  const rootRef=useRef<HTMLDivElement>(null);
  const [open,setOpen]=useState(false);
  const lockScroll=useScrollLock(rootRef,200);
  return <Collapsible ref={rootRef} open={open} onOpenChange={next=>{if(!rootRef.current?.closest('[data-history-viewport]'))lockScroll();setOpen(next);}}
    data-tool-error={error?'true':undefined}
    className="group/tool-trace w-full min-w-0 max-w-full"
    style={{'--animation-duration':'200ms'} as CSSProperties}>
    <CollapsibleTrigger className={cn('flex w-fit max-w-full cursor-pointer items-center gap-2 rounded-sm py-0.5 text-left transition-colors select-none focus-visible:outline-2 focus-visible:outline-ring',error?'text-destructive hover:text-destructive/80':'text-muted-foreground hover:text-foreground')}>
      <span className="flex size-4 shrink-0 items-center justify-center [&_svg]:size-3.5">{error?<AlertCircle/>:icon}</span>
      <span className="min-w-0 truncate font-mono text-[13px]">{signature}</span>
      <span className={cn('truncate text-xs',error?'text-destructive/70':'text-muted-foreground/60')}>{description}</span>
      <ChevronRight className="size-3.5 shrink-0 transition-transform group-data-open/tool-trace:rotate-90 motion-reduce:transition-none"/>
    </CollapsibleTrigger>
    <CollapsibleContent className="relative overflow-hidden outline-none data-closed:animate-collapsible-up data-open:animate-collapsible-down ease-out motion-reduce:animate-none data-closed:fill-mode-forwards data-closed:pointer-events-none [--tw-duration:var(--animation-duration)]">
      <div className="my-1 ml-6 min-w-0 space-y-2">
        <ToolJsonBlock label="参数" value={parameters}/>
        <ToolJsonBlock label={error?'错误':'结果'} value={result} error={error}/>
      </div>
    </CollapsibleContent>
  </Collapsible>;
}

export function ToolStatusCard({icon,signature,message,loading=false}:{icon:ReactNode;signature:string;message:string;loading?:boolean}){
  return <div className="text-muted-foreground flex max-w-full items-center gap-2 py-0.5" aria-busy={loading}>
    <span className={cn('flex size-4 shrink-0 items-center justify-center [&_svg]:size-3.5',loading&&'animate-pulse motion-reduce:animate-none')}>{icon}</span>
    <span className="min-w-0 truncate font-mono text-[13px]">{signature}</span>
    <span className="text-muted-foreground/60 truncate text-xs">{message}</span>
  </div>;
}
