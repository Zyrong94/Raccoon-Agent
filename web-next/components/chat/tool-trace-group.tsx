"use client";

// Grouping chrome shared by the live parent thread and the read-only subagent
// transcript. Callers supply the status summary; this file owns the markup.
import type {ToolCallMessagePartStatus} from '@assistant-ui/react';
import type {ReactNode} from 'react';
import {ToolGroupContent,ToolGroupRoot} from '@/components/assistant-ui/elements/tool-group.aui';
import {DisclosureTrigger,disclosureContentClass} from './trace';

export type ToolGroupLabel='waiting'|'running'|'stopped'|'incomplete'|'ran';

const backgroundRunning={type:'running'} as const;
export function displayToolStatus(status:ToolCallMessagePartStatus|undefined,id:string,result:unknown,runningToolIds?:readonly string[]){
 return result===undefined&&status?.type==='requires-action'&&status.reason==='tool-calls'&&runningToolIds?.includes(id)
  ?backgroundRunning:status;
}

type GroupItem={status?:ToolCallMessagePartStatus|undefined;isError?:boolean|undefined};

export function toolGroupLabel(items:readonly GroupItem[]):ToolGroupLabel{
 if(items.some(item=>item.status?.type==='requires-action'))return 'waiting';
 if(items.some(item=>item.status?.type==='running'))return 'running';
 if(items.some(item=>item.status?.type==='incomplete'&&item.status.reason==='cancelled'))return 'stopped';
 if(items.some(item=>item.status?.type==='incomplete'&&item.status.reason!=='error'))return 'incomplete';
 return 'ran';
}

export function toolGroupFailed(items:readonly GroupItem[]):number{
 return items.filter(item=>item.isError||(item.status?.type==='incomplete'&&item.status.reason==='error')).length;
}

export function ToolTraceGroupView({label,count,failed,children}:{label:ToolGroupLabel;count:number;failed:number;children:ReactNode}){
 if(count<2)return <div>{children}</div>;
 return <ToolGroupRoot className="my-1">
  <DisclosureTrigger live={label==='running'} label={label} detail={`${count} tools${failed?` · ${failed} failed`:''}`} error={failed>0}/>
  <ToolGroupContent className={disclosureContentClass}>{children}</ToolGroupContent>
 </ToolGroupRoot>;
}
