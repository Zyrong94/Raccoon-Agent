import type {Message} from './workbench/types';

export type SubagentScope={session_id:string;project_id:string;generation?:never}|{session_id:string;generation:string;project_id?:never};
export type SubagentMode='follow_up'|'steer';
export type SubagentSummary={subagent_session_id:string;agent_name:string|null;task:string;state:string;summary:string;latest_activity:string;queued_count:number;queued_messages:{event_id:string;text:string;mode:string;delivery:string}[];created_at:string|null;updated_at:string|null;completed_at:string|null;restricted:boolean};
export type SubagentDetail=SubagentSummary&{parent_session_id:string;messages:Message[]};
export type SubagentRequest=<T>(scope:SubagentScope,path:string,body?:Record<string,unknown>,signal?:AbortSignal)=>Promise<T>;
type View={subagents:SubagentSummary[];details:Record<string,SubagentDetail>;selected:string|null;loading:boolean;error:string};

// Reads can be repeated. Writes are sent once and keep their caller's event ID.
export class SubagentStore{
 private view:View={subagents:[],details:{},selected:null,loading:true,error:''};
 private listeners=new Set<()=>void>();private active=false;private revision=0;private pollCycle=0;private timer?:ReturnType<typeof setTimeout>;private read?:AbortController;private writes=new Set<AbortController>();
 constructor(readonly scope:SubagentScope,private request:SubagentRequest){}
 get=()=>this.view;
 subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener);};};
 private update(patch:Partial<View>){this.view={...this.view,...patch};this.listeners.forEach(listener=>listener());}
 select=(selected:string)=>{this.update({selected});void this.refresh();};
 async refresh(){
  if(!this.active||typeof document!=='undefined'&&document.hidden)return;
  const revision=this.revision,selected=this.view.selected,controller=new AbortController();this.read?.abort();this.read=controller;
  try{
   const [listing,detail]=await Promise.all([
    this.request<{subagents:SubagentSummary[]}>(this.scope,'',undefined,controller.signal),
    selected?this.request<SubagentDetail>(this.scope,'/'+encodeURIComponent(selected),undefined,controller.signal):Promise.resolve(null),
   ]);
   if(!controller.signal.aborted&&revision===this.revision)this.update({subagents:listing.subagents??[],details:detail?{...this.view.details,[detail.subagent_session_id]:detail}:this.view.details,loading:false,error:''});
  }catch(error){if(!controller.signal.aborted&&revision===this.revision)this.update({loading:false,error:error instanceof Error?error.message:String(error)});}
 }
 private tick=async()=>{const cycle=this.pollCycle;await this.refresh();if(this.active&&cycle===this.pollCycle)this.timer=setTimeout(this.tick,1000);};
 private visibility=()=>{this.pollCycle++;clearTimeout(this.timer);this.read?.abort();if(!document.hidden)void this.tick();};
 start=()=>{if(this.active)return;this.active=true;if(typeof document!=='undefined')document.addEventListener('visibilitychange',this.visibility);void this.tick();};
 stop=()=>{this.active=false;this.revision++;this.pollCycle++;clearTimeout(this.timer);this.read?.abort();this.writes.forEach(controller=>controller.abort());if(typeof document!=='undefined')document.removeEventListener('visibilitychange',this.visibility);};
 private async write(child:string,path:string,body:Record<string,unknown>){
  if(!this.active)throw new Error('父会话已切换，请返回原会话后继续');
  const revision=this.revision,controller=new AbortController();this.writes.add(controller);
  try{
   const result=await this.request<Record<string,unknown>>(this.scope,`/${encodeURIComponent(child)}/${path}`,body,controller.signal);
   if(controller.signal.aborted||revision!==this.revision)throw new Error('父会话已切换，请同步后确认投递结果');
   void this.refresh();return result;
  }finally{this.writes.delete(controller);}
 }
 send=(child:string,message:string,mode:SubagentMode,event_id:string)=>this.write(child,'messages',{message,mode,event_id});
 stopSubagent=(child:string)=>this.write(child,'stop',{});
}
