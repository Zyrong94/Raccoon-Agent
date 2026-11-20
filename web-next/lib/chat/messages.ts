import type { ThreadMessageLike } from '@assistant-ui/react';
import { extractSources, isUrlCited, SEARCH_TOOL_NAMES, type SourcePart } from './sources';
import type { Message, Snapshot } from '../workbench/types';
import {toRuntimeAttachment} from './attachments';
type Parts = Array<Exclude<ThreadMessageLike['content'],string>[number]>;
type MessageState=Pick<Snapshot,'project'|'session'|'messages'|'live'|'activity'|'busy'|'pending'|'status'|'error'|'revisable_message_ids'>;
export function joinTurns(messages:ThreadMessageLike[]):ThreadMessageLike[]{
  const turns:ThreadMessageLike[]=[];
  for(const message of messages){
    const previous=turns.at(-1);
    // Backend model steps are one reply; retain text between tool groups.
    if(message.role==='assistant'&&previous?.role==='assistant'){
      turns[turns.length-1]={...previous,content:[...(previous.content as Parts),...(message.content as Parts)]};
    }else turns.push(message);
  }
  return turns;
}
export type DisplayMessageHooks={
  result?:(name:string|undefined,text:string|undefined)=>void;
  assistantText?:(content:Parts)=>void;
};
export function projectDisplayMessages(messages:Message[],hooks?:DisplayMessageHooks){
  const results=new Map(messages.filter(m=>m.role==='tool'&&m.tool_call_id).map(m=>[m.tool_call_id!,m]));
  const calls=new Set(messages.flatMap(m=>m.calls?.map(c=>c.id)??[]));
  const projected:ThreadMessageLike[]=[];
  for(const message of messages){
    if(message.role==='tool'){
      if(message.tool_call_id&&calls.has(message.tool_call_id))continue;
      projected.push({id:message.id,role:'assistant',content:[{type:'tool-call',toolCallId:message.tool_call_id??message.id,toolName:message.name??'tool',args:{说明:'历史调用参数未知'},argsText:'历史调用参数未知',result:message.content,isError:message.ok===false}]});
      hooks?.result?.(message.name,message.content);
      continue;
    }
    const content:Parts=[];
    if(message.role==='assistant'&&message.reasoning)content.push({type:'reasoning',text:message.reasoning});
    if(message.content?.trim())content.push({type:'text',text:message.content});
    for(const call of message.calls??[]){
      const result=results.get(call.id);
      content.push({type:'tool-call',toolCallId:call.id,toolName:call.name,args:(call.arguments??{}) as Extract<Parts[number],{type:"tool-call"}>["args"],argsText:JSON.stringify(call.arguments??{}),...(result?{result:result.content,isError:result.ok===false}:{})});
      hooks?.result?.(call.name,result?.content);
    }
    if(message.role==='assistant'&&message.content?.trim()&&!(message.calls?.length))hooks?.assistantText?.(content);
    projected.push({id:message.id,role:message.role,content,...(message.role==='user'&&message.attachments?.length?{attachments:message.attachments.map(toRuntimeAttachment)}:{})});
  }
  return {messages:projected,calls};
}
function mapHistory(state:MessageState){
  // Search results are projected onto the answer that follows them, not onto
  // the tool row: `Sources` reads its own message's source parts.
  const pendingSources:SourcePart[]=[];
  const seenSources=new Set<string>();
  const collectSources=(name:string|undefined,text:string|undefined)=>{
    if(!text||!name||!SEARCH_TOOL_NAMES.has(name))return;
    for(const source of extractSources(text)){
      if(seenSources.has(source.url))continue;
      seenSources.add(source.url);
      pendingSources.push(source);
    }
  };
  const attachSources=(content:Parts)=>{
    if(!pendingSources.length)return;
    const text=content.flatMap(part=>part.type==='text'?[part.text]:[]).join('\n');
    const cited=text?pendingSources.filter(source=>isUrlCited(text,source.url)).slice(-5):[];
    pendingSources.length=0;
    seenSources.clear();
    if(cited.length)content.push(...cited);
  };
  const {messages,calls}=projectDisplayMessages(state.messages,{result:collectSources,assistantText:attachSources});
  return {turns:joinTurns(messages),calls};
}

export function createMessageMapper(){
 let previous:MessageState|undefined;
 let history:ReturnType<typeof mapHistory>;
 const decorated=new WeakMap<ThreadMessageLike,ThreadMessageLike>();
 return (state:MessageState):ThreadMessageLike[]=>{
  if(!previous||previous.messages!==state.messages||previous.project.id!==state.project.id||previous.session.id!==state.session.id){
    history=mapHistory(state);
  }
  previous=state;
  const {calls}=history;
  // Live segments and tools share first-seen order, also carried by snapshots.
  const identity=`${state.project.id}:${state.session.id}:${state.messages.at(-1)?.id??'empty'}`;
  const liveMessages:Array<{order:number;message:ThreadMessageLike}>=[];
  state.live.forEach((live,index)=>{
    const content:Parts=[];
    if(live.reasoning)content.push({type:'reasoning',text:live.reasoning});
    if(live.content.trim())content.push({type:'text',text:live.content});
    if(content.length)liveMessages.push({order:live.order??index,message:{id:`${identity}:live:${index}`,role:'assistant',content}});
  });
  for(const tool of state.activity){
    if(calls.has(tool.id))continue;
    liveMessages.push({order:tool.order??state.live.length+liveMessages.length,message:{id:`${identity}:tool:${tool.id}`,role:'assistant',content:[{type:'tool-call',toolCallId:tool.id,toolName:tool.name,args:(tool.arguments??{说明:'参数未知'}) as Extract<Parts[number],{type:"tool-call"}>["args"],argsText:JSON.stringify(tool.arguments??{说明:'参数未知'}),...(tool.ok!=null?{result:tool.content??'',isError:!tool.ok}:{})}]}});
  }
  const live=liveMessages.sort((a,b)=>a.order-b.order).map(entry=>entry.message);
  const turns=joinTurns([...history.turns,...live]);
  if(turns.at(-1)?.role==='user'&&(state.status==='error'||state.status==='interrupted')){
    turns.push({id:`${state.project.id}:${state.session.id}:failed-reply`,role:'assistant',content:[{type:'text',text:''}]});
  }
  const last=turns.at(-1);
  if(last?.role==='assistant'&&!state.busy){
    if(state.pending)turns[turns.length-1]={...last,status:{type:'requires-action',reason:'tool-calls'}};
    else if(state.status==='interrupted')turns[turns.length-1]={...last,status:{type:'incomplete',reason:'cancelled'}};
    else if(state.status==='error')turns[turns.length-1]={...last,status:{type:'incomplete',reason:'error',error:state.error??undefined}};
  }
  let parentUser:string|undefined;
  return turns.map((message,index)=>{
    if(message.role==='user')parentUser=message.id;
    const currentVersionControls=message.role==='assistant'&&index===turns.length-1;
    const canRevise=!!parentUser&&(state.revisable_message_ids===undefined||state.revisable_message_ids.includes(parentUser));
    const cached=decorated.get(message);
    if(cached&&cached.metadata?.custom?.revisionTarget===parentUser&&cached.metadata?.custom?.currentVersionControls===currentVersionControls&&cached.metadata?.custom?.canRevise===canRevise)return cached;
    const result={...message,metadata:{...message.metadata,custom:{...message.metadata?.custom,revisionTarget:parentUser,currentVersionControls,canRevise}}};
    decorated.set(message,result);
    return result;
  });
 };
}
export const mapMessages=createMessageMapper();
