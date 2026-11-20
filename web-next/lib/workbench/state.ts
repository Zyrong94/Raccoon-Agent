import type { ConversationSummary, ServerEvent, SessionSummary, Snapshot } from './types';
const mergeSessions=(sessions:SessionSummary[],rows:SessionSummary[])=>{
  if(!rows.length)return sessions;
  const byId=new Map(rows.map(row=>[row.id,row]));
  return sessions.map(item=>byId.has(item.id)?{...item,...byId.get(item.id)}:item);
};
const byConversation=(a:ConversationSummary,b:ConversationSummary)=>
  Number(b.pinned)-Number(a.pinned)||(b.updated_at??'').localeCompare(a.updated_at??'')||b.id.localeCompare(a.id);
const byRecent=(a:ConversationSummary,b:ConversationSummary)=>
  (b.updated_at??'').localeCompare(a.updated_at??'')||b.project_id.localeCompare(a.project_id)||b.id.localeCompare(a.id);
export function reduceEvent(state:Snapshot|null,event:ServerEvent):Snapshot|null {
  if(event.kind==='resync'||event.kind==='ready')return state;
  if(event.kind==='snapshot') return event.state;
  if(!state)return state;
  // A sidebar row belongs to a project, not necessarily to the selected session.
  if(event.kind==='sidebar_item'){
    const sameProject=state.project.id===event.project_id;
    const sessions=sameProject?mergeSessions(state.sessions,event.sessions):state.sessions;
    const projects=state.sidebar.projects.map(project=>{
      if(project.id!==event.project_id)return project;
      const conversations=project.conversations.filter(item=>item.id!==event.conversation_id);
      if(event.item)conversations.push(event.item);
      conversations.sort(byConversation);
      return {...project,conversations};
    });
    const recent=projects.flatMap(project=>project.conversations).sort(byRecent);
    const current=event.item&&sameProject&&event.sessions.some(row=>row.id===state.session.id)?event.item:null;
    return {...state,sessions,sidebar:{...state.sidebar,projects,recent,
      ...(current?{current_conversation_id:current.id,new_chat:false}:{})},
      ...(current&&current.title!==state.session.title?{session:{...state.session,title:current.title}}:{})};
  }
  if((event.project_id!==undefined||event.session_id!==undefined)&&(event.project_id!==state.project.id||event.session_id!==state.session.id))return state;
  if(event.kind==='message_committed'){
    const knownIds=new Set(state.messages.map(message=>message.id));
    const added=event.messages.filter(message=>!knownIds.has(message.id));
    return added.length?{...state,messages:[...state.messages,...added]}:state;
  }
  if(event.kind==='run_state'){
    return {...state,...event.state,...(event.settled?{activity:[],live:[]}:{})};
  }
  if(event.kind==='run_phase')return {...state,run_phase:event.event.phase};
  if(event.kind==='permission_mode')return {...state,permission_mode:event.permission_mode};
  if(event.kind==='model')return {...state,model:event.model,effort:event.effort,input_capabilities:event.input_capabilities??state.input_capabilities};
  if(event.kind==='tool'){
    const index=state.activity.findIndex(a=>a.id===event.event.id);
    const activity=[...state.activity];
    const tool={...activity[index],...event.event,order:activity[index]?.order??event.event.order??activity.length};
    if(index<0)activity.push(tool);else activity[index]=tool;
    let runningToolIds=state.running_tool_call_ids;
    if(tool.kind==='background_started'&&tool.ok==null&&!runningToolIds?.includes(tool.id)){
      runningToolIds=[...(runningToolIds??[]),tool.id];
    }else if(tool.ok!=null&&runningToolIds?.includes(tool.id)){
      runningToolIds=runningToolIds.filter(id=>id!==tool.id);
    }
    return {...state,activity,running_tool_call_ids:runningToolIds};
  }
  if(event.project_id!==state.project.id||event.session_id!==state.session.id)return state;
  if(event.kind==='artifacts')return {...state,canvas:event.canvas,artifacts:event.artifacts};
  return state;
}
