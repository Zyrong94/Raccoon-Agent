import {RequestError} from './workbench/client';
import type {Message} from './workbench/types';

export type GroupSummary={id:string;title:string};
export type GroupSnapshot={available:boolean;session:{id:string;title:string}|null;messages:Message[]};
export type GroupSessionsRequest=(path:string,signal?:AbortSignal)=>Promise<unknown>;

type View={groups:GroupSummary[];selected:string;snapshot:GroupSnapshot|null;loading:boolean;error:string};

const browserRequest:GroupSessionsRequest=async(path,signal)=>{
 const response=await fetch('/api'+path,{cache:'no-store',signal});
 const value=await response.json().catch(()=>null) as {detail?:unknown}|null;
 if(!response.ok)throw new RequestError(typeof value?.detail==='string'?value.detail:`群聊记录请求失败 (${response.status})`,response.status);
 return value??{};
};

// QQ group history is read only: the store never creates, resumes or writes a
// group agent, it only pages through what the channel host already recorded.
export class GroupSessionsStore{
 private view:View={groups:[],selected:'',snapshot:null,loading:true,error:''};
 private listeners=new Set<()=>void>();
 private polling=false;private timer?:ReturnType<typeof setTimeout>;private controller?:AbortController;private revision=0;
 constructor(private request:GroupSessionsRequest=browserRequest){}
 get=()=>this.view;
 subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener);};};
 private update(patch:Partial<View>){this.view={...this.view,...patch};this.listeners.forEach(listener=>listener());}
 select=(id:string)=>{if(!id||id===this.view.selected)return;this.revision++;this.update({selected:id,snapshot:null,error:'',loading:true});void this.refresh();};
 async loadGroups(){
  try{
   const listed=await this.request('/group-sessions') as {groups?:unknown};
   const groups=(Array.isArray(listed?.groups)?listed.groups as GroupSummary[]:[]).filter(group=>typeof group?.id==='string'&&typeof group?.title==='string');
   this.update({groups,loading:false,error:''});
  }catch(error){this.update({loading:false,error:error instanceof Error?error.message:String(error)});}
 }
 async refresh(){
  const revision=this.revision;
  const controller=new AbortController();this.controller?.abort();this.controller=controller;
  try{
   const listed=await this.request('/group-sessions',controller.signal) as {groups?:unknown};
   if(controller.signal.aborted||revision!==this.revision)return;
   const groups=(Array.isArray(listed?.groups)?listed.groups as GroupSummary[]:[]).filter(group=>typeof group?.id==='string'&&typeof group?.title==='string');
   const selected=this.view.selected&&groups.some(group=>group.id===this.view.selected)?this.view.selected:groups[0]?.id??'';
   const snapshot=selected?await this.request(`/group-sessions/${encodeURIComponent(selected)}`,controller.signal) as GroupSnapshot:null;
   if(controller.signal.aborted||revision!==this.revision)return;
   this.update({groups,selected,snapshot,loading:false,error:''});
  }catch(error){
   if(controller.signal.aborted)return;
   this.update({loading:false,error:error instanceof Error?error.message:String(error)});
  }
 }
 private tick=async()=>{if(!this.polling||typeof document!=='undefined'&&document.hidden)return;await this.refresh();if(this.polling)this.timer=setTimeout(this.tick,4000);};
 private visibility=()=>{clearTimeout(this.timer);if(document.hidden){this.controller?.abort();this.revision++;}else void this.tick();};
 start=()=>{if(this.polling)return;this.polling=true;if(typeof document!=='undefined')document.addEventListener('visibilitychange',this.visibility);void this.tick();};
 stop=()=>{this.polling=false;clearTimeout(this.timer);this.controller?.abort();this.revision++;if(typeof document!=='undefined')document.removeEventListener('visibilitychange',this.visibility);};
}

// The selected group and its transcript survive leaving the group panel.
export const groupSessionsStore=new GroupSessionsStore();
