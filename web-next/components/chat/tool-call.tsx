"use client";

// Bridges Python tool parts to the shared tool-row presentation.
import {useContext} from 'react';
import {WorkbenchContext} from '../workbench/context';
import {displayToolStatus} from './tool-trace-group';
import type {ToolCallMessagePartProps} from '@assistant-ui/react';
import {ToolFallback} from '@/components/assistant-ui/elements/tool-fallback.aui';
import {ToolRowView,toToolRow} from './tool-row';

export function ToolCall(props:ToolCallMessagePartProps){
 const runningToolIds=useContext(WorkbenchContext)?.runStatus?.running_tool_call_ids;
 const displayStatus=displayToolStatus(props.status,props.toolCallId,props.result,runningToolIds);
 const status=displayStatus?.type;
 // Approval and cancellation keep the official controls; a completed or
 // running call uses the same row the read-only subagent transcript renders.
 if(status==='requires-action'||status==='incomplete')return <ToolFallback {...props}/>;
 return <ToolRowView row={toToolRow({...props,status:displayStatus??props.status})}/>;
}
