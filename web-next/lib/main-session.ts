import type {Message,Pending,Activity,PermissionMode,Snapshot,AttachmentDescriptor,ParallelReply} from './workbench/types';
import {RequestError,isBusinessError} from './workbench/client';
import type {SubagentRequest} from './subagents';

export type MainSnapshot=Pick<Snapshot,'run_phase'|'running_tool_call_ids'>&{pending_messages?:Snapshot['pending_messages'];parallel_replies?:ParallelReply[];available:boolean;session:{id:string;title:string};session_id:string;generation:string;messages:Message[];live:{content:string;reasoning:string}[];activity?:Activity[];busy:boolean;cancellable:boolean;pending:Pending|null;status:string;error:string|null;model?:string|null;models?:Snapshot['models'];effort?:string;permission_mode?:PermissionMode;input_capabilities?:Snapshot['input_capabilities']};
export type MainScope={sessionId:string;generation:string};
type View={snapshot:MainSnapshot|null;connected:boolean;mutating:boolean;error:string;draft:string;failedDraft:string};
export type MainRequest=(path:string,body?:unknown,token?:string,signal?:AbortSignal)=>Promise<unknown>;
const browserRequest:MainRequest=async(path,body,token,signal)=>{
 const binary=typeof Blob!=='undefined'&&body instanceof Blob;
 const response=await fetch('/api'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?undefined:{'Content-Type':binary?body.type||'application/octet-stream':'application/json','X-Raccoon-Token':token??''},body:body===undefined?undefined:binary?body:JSON.stringify(body),cache:'no-store',signal});
 const value=await response.json();if(!response.ok)throw new RequestError(typeof value.detail==='string'?value.detail:`主会话请求失败 (${response.status})`,response.status);return value;
};

export class MainSessionStore{
 private view:View={snapshot:null,connected:false,mutating:false,error:'',draft:'',failedDraft:''};
 private listeners=new Set<()=>void>();private token='';private revision=0;private polling=false;private timer?:ReturnType<typeof setTimeout>;private controller?:AbortController;
 private uploads=0;
 private failedSendError='';
 constructor(private request:MainRequest=browserRequest){}
 get=()=>this.view;
 subscribe=(callback:()=>void)=>{this.listeners.add(callback);return()=>{this.listeners.delete(callback);};};
 private update(patch:Partial<View>){this.view={...this.view,...patch};this.listeners.forEach(callback=>callback());}
 setDraft=(draft:string)=>this.update({draft});
  subagentRequest:SubagentRequest=async<T>(scope:import('./subagents').SubagentScope,path:string,body?:Record<string,unknown>,signal?:AbortSignal):Promise<T>=>{
   const check=()=>{const state=this.view.snapshot;if(!this.view.connected||scope.project_id!==undefined||scope.session_id!==state?.session_id||scope.generation!==state?.generation)throw new Error('主会话宿主已更换或连接未就绪，请同步后继续');};
   check();const query=new URLSearchParams({session_id:scope.session_id,generation:scope.generation!});
   const result=await this.request('/main-session/subagents'+path+(body===undefined?'?'+query:''),body===undefined?undefined:{...body,...scope},body===undefined?undefined:this.token,signal);
   check();if(signal?.aborted)throw new Error('请求已取消');return result as T;
  };
 reportError=(error:unknown)=>this.update({error:error instanceof Error?error.message:String(error)});
 private checkScope(scope:MainScope,allowUploads=false,allowBusy=false){
  const state=this.view.snapshot;
  if(!state||state.session_id!==scope.sessionId||state.generation!==scope.generation)throw new Error('主会话宿主已更换，请重新选择附件或同步后继续。');
  if(!this.view.connected||this.view.mutating&&!(allowUploads&&this.uploads)||(!allowBusy&&state.busy)||state.pending)throw new Error('请在主会话连接就绪且任务空闲时继续');
 }
 attachmentScope=():MainScope=>{
  const state=this.view.snapshot;if(!state)throw new Error('主会话连接未就绪');
  return {sessionId:state.session_id,generation:state.generation};
 };
 checkAttachmentScope=(scope:MainScope)=>this.checkScope(scope,true,true);
 uploadAttachment=async(file:File,scope:MainScope):Promise<AttachmentDescriptor>=>{
  this.checkScope(scope,true,true);this.uploads++;this.update({mutating:true,error:''});
  try{
   const query=new URLSearchParams({session_id:scope.sessionId,generation:scope.generation,name:file.name});
   const descriptor=await this.request('/main-session/attachments?'+query,file,this.token) as AttachmentDescriptor;
   this.checkScope(scope,true,true);return descriptor;
  }catch(error){this.reportError(error);throw error;}
  finally{this.uploads--;this.update({mutating:this.uploads>0});}
 };
 async refresh(){
  const revision=this.revision;
  const controller=new AbortController();this.controller?.abort();this.controller=controller;
  try{
   if(!this.token){const auth=await this.request('/state',undefined,undefined,controller.signal) as {token:string};if(!auth.token)throw new Error('未收到安全令牌');this.token=auth.token;}
   const snapshot=await this.request('/main-session/state',undefined,undefined,controller.signal) as MainSnapshot;
   if(!controller.signal.aborted&&revision===this.revision){if(this.view.snapshot&&snapshot.generation!==this.view.snapshot.generation)this.revision++;this.update({snapshot,connected:true,error:this.failedSendError});}
  }catch(error){if(!controller.signal.aborted&&revision===this.revision){this.token='';this.update({connected:false,error:(error as Error).message});}}
 }
 private tick=async()=>{if(!this.polling||typeof document!=='undefined'&&document.hidden)return;await this.refresh();if(this.polling)this.timer=setTimeout(this.tick,this.view.connected?700:2000);};
 private visibility=()=>{clearTimeout(this.timer);if(document.hidden){this.controller?.abort();this.revision++;}else void this.tick();};
 start=()=>{if(this.polling)return;this.polling=true;if(typeof document!=='undefined')document.addEventListener('visibilitychange',this.visibility);void this.tick();};
 stop=()=>{this.polling=false;clearTimeout(this.timer);this.controller?.abort();this.revision++;if(typeof document!=='undefined')document.removeEventListener('visibilitychange',this.visibility);};
 private async mutate(path:string,body:Record<string,unknown>={}){
  const state=this.view.snapshot;
  if(!this.view.connected||!state||this.view.mutating)throw new Error('主会话连接未就绪，请稍后重试');
  this.revision++;this.update({mutating:true,error:''});
  try{
   const snapshot=await this.request('/main-session/'+path,{...body,session_id:state.session_id,generation:state.generation},this.token) as MainSnapshot;
   if(this.view.snapshot?.generation!==state.generation||this.view.snapshot.session_id!==state.session_id||snapshot.generation!==state.generation||snapshot.session_id!==state.session_id)throw new Error('主会话宿主已更换，请核对历史；提交不会自动重试。');
   this.update({snapshot,connected:true});
   return snapshot;
  }catch(error){if(!isBusinessError(error)){this.token='';this.update({connected:false});}this.reportError(error);throw error;}
  finally{this.revision++;this.update({mutating:false});}
 }
 send=async(text:string,attachments?:{ids:string[];scope:MainScope})=>{
  this.setDraft(text);
  try{
   const scope=attachments?.scope??this.attachmentScope();this.checkScope(scope,false,true);
   await this.mutate('messages',{message:text,mode:'auto',message_id:crypto.randomUUID(),...(attachments?.ids.length?{attachment_ids:attachments.ids}:{})});
   if(this.view.draft===text)this.setDraft('');this.failedSendError='';this.update({failedDraft:'',error:''});
  }catch(error){this.failedSendError=error instanceof Error?error.message:String(error);this.update({failedDraft:text});this.reportError(error);throw error;}
 };
 restoreDraft=()=>{this.setDraft([this.view.draft,this.view.failedDraft].filter(Boolean).join('\n'));this.update({failedDraft:''});};
 answer=async(request_id:string,answer:string)=>{await this.mutate('input',{request_id,answer});};
 cancel=()=>this.mutate('cancel');
 private async change(path:string,body:Record<string,unknown>){const scope=this.attachmentScope();this.checkScope(scope);await this.mutate(path,body);}
 setModel=(model:string)=>this.change('model',{model});
 setPermissionMode=(mode:PermissionMode)=>this.change('mode',{mode});
}

// Keep the personal draft and transport independent of project workspace switches.
export const personalMainStore=new MainSessionStore();
