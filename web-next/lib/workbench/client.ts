import {reduceEvent} from './state';
import type {SubagentRequest} from '../subagents';
import type {AttachmentDescriptor,PermissionMode,ServerEvent,Snapshot,DirectoryListing,ArchiveSnapshot} from './types';
export class RequestError extends Error{constructor(message:string,public status:number){super(message);}}
export const isBusinessError=(error:unknown)=>error instanceof RequestError&&[400,409,422].includes(error.status);
export type AttachmentScope={projectId:string;sessionId:string};
export const scopeKey=(scope:AttachmentScope)=>JSON.stringify([scope.projectId,scope.sessionId]);
const snapshotScope=(state:Snapshot):AttachmentScope=>({projectId:state.project.id,sessionId:state.session.id});
export type Transport={request:(path:string,body?:unknown,token?:string,method?:'GET'|'POST'|'PATCH'|'DELETE',signal?:AbortSignal)=>Promise<unknown>;upload?:(path:string,file:File,token:string)=>Promise<AttachmentDescriptor>;subscribe:(receive:(event:ServerEvent)=>void,fail:()=>void,cursor?:number)=>()=>void;retry:(callback:()=>void)=>()=>void};
export const browserTransport:Transport={
  async upload(path,file,token){
    const response=await fetch('/api'+path,{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream','X-Raccoon-Token':token},body:file,cache:'no-store'});
    const value=await response.json();
    if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:`附件上传失败 (${response.status})`);
    return value;
  },
  async request(path,body,token,method,signal){
    const response=await fetch('/api'+path,{method:method??(token?'POST':'GET'),headers:token?{'Content-Type':'application/json','X-Raccoon-Token':token}:undefined,body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',...(signal?{signal}:{})});
    const value=await response.json();
    if(!response.ok)throw new RequestError(typeof value.detail==='string'?value.detail:`请求失败 (${response.status})`,response.status);
    return value;
  },
  subscribe(receive,fail,cursor){const stream=new EventSource('/api/events'+(cursor?'?cursor='+cursor:''));stream.onmessage=event=>{try{receive(JSON.parse(event.data));}catch{fail();}};stream.onerror=fail;return()=>stream.close();},
  retry(callback){const timer=setTimeout(callback,2000);return()=>clearTimeout(timer);},
};
export type ComposerDraft={text:string;files:File[]};
export type ComposerSelection={model:string|null;permissionMode:PermissionMode};
type DraftPort={scope:AttachmentScope;capture:()=>ComposerDraft;restore:(draft:ComposerDraft,expected?:ComposerDraft)=>void};
type View={selectedScope?:AttachmentScope|null;snapshot:Snapshot|null;connected:boolean;mutating:boolean;navigating:boolean;error:string;pendingTarget?:{projectId:string;conversationId:string}|null;
  composerModel?:string;composerPermissionMode?:PermissionMode};
export class WorkbenchStore{
  private view:View={selectedScope:null,snapshot:null,connected:false,mutating:false,navigating:false,error:'',pendingTarget:null};
  private scopeChanges=0;private navigation=0;
  private listeners=new Set<()=>void>();private generation=0;private token='';private close?:()=>void;private retry?:()=>void;
  private mutationError='';
  private sending=new Set<string>();
  private mutations=new Map<string,number>();
  private beginMutation(scope:AttachmentScope){const key=scopeKey(scope);this.mutations.set(key,(this.mutations.get(key)??0)+1);this.update({});}
  private endMutation(scope:AttachmentScope){const key=scopeKey(scope),count=(this.mutations.get(key)??1)-1;if(count)this.mutations.set(key,count);else this.mutations.delete(key);this.update({});}
  private cancelling=new Set<string>();
  private cancelRequested=new Set<string>();
  private answeredInput=new Set<string>();
  private awaitingNavigationSnapshot=false;
  private pendingNavigation?:string;
  private snapshots=new Map<string,Snapshot>();
  private scopeListeners=new Map<string,Set<()=>void>>();
  private historyLoads=new Map<string,Promise<Snapshot>>();
  private loadingEvents=new Map<string,ServerEvent[]>();
  private localSelection=false;
  private lastSequence=0;
  private serverEpoch?:string;
  private historyEpoch=0;
  private sidebar?:Snapshot['sidebar'];
  private sidebarSequence=-1;
  private composerDrafts=new Map<string,DraftPort>();
  private get composerDraft(){const scope=this.getSelectedScope();return scope?this.composerDrafts.get(scopeKey(scope)):undefined;}
  private draftTransfer?:{projectId?:string;sessionId?:string;navigationId?:string;source:string;draft:ComposerDraft;expected?:ComposerDraft};
  registerComposerDraft=(port:DraftPort)=>{const key=scopeKey(port.scope);this.composerDrafts.set(key,port);this.deliverDraft();return()=>{if(this.composerDrafts.get(key)===port)this.composerDrafts.delete(key);};};
  private deliverDraft(){
    const transfer=this.draftTransfer,port=this.composerDraft,state=this.view.snapshot;
    if(transfer?.navigationId&&state?.navigation_id===transfer.navigationId&&!this.view.navigating){
      transfer.projectId=state.project.id;transfer.sessionId=state.session.id;transfer.navigationId=undefined;
    }
    if(!transfer||!port||this.view.navigating||!state?.sidebar?.new_chat||(transfer.projectId?state.project.id!==transfer.projectId:!state.project.projectless)||transfer.navigationId&&state.navigation_id!==transfer.navigationId||transfer.sessionId&&state.session.id!==transfer.sessionId||port.scope.projectId!==state.project.id||port.scope.sessionId!==state.session.id)return;
    this.draftTransfer=undefined;port.restore(transfer.draft,`${state.project.id}:${state.session.id}`===transfer.source?transfer.expected:undefined);
  }

  private attachmentUploads=0;
  constructor(private io:Transport=browserTransport){}
  get=()=>this.view;
  composerSettings=(state=this.view.snapshot)=>{
    const selected=state?.models.find(model=>model.ref===this.view.composerModel);
    return {
      model:selected?.ref??state?.model??null,
      permissionMode:this.view.composerPermissionMode??state?.permission_mode??'ask',
      capabilities:selected?.input_capabilities??state?.input_capabilities,
    };
  };
  selectComposerModel=(model:string)=>{
    if(this.view.snapshot?.models.some(option=>option.ref===model))this.update({composerModel:model});
  };
  selectComposerPermission=async(mode:PermissionMode)=>{
    this.update({composerPermissionMode:mode});
  };
  subscribe=(callback:()=>void)=>{this.listeners.add(callback);return()=>{this.listeners.delete(callback);};};
  getSelectedScope=()=>this.view.selectedScope??(this.view.snapshot?snapshotScope(this.view.snapshot):null);
  getScope=(scope:AttachmentScope)=>this.snapshots.get(scopeKey(scope))??null;
  subscribeScope=(scope:AttachmentScope,listener:()=>void)=>{
    const key=scopeKey(scope),listeners=this.scopeListeners.get(key)??new Set<()=>void>();
    listeners.add(listener);this.scopeListeners.set(key,listeners);
    return()=>{listeners.delete(listener);if(!listeners.size)this.scopeListeners.delete(key);};
  };
  private learnSidebar(snapshot:Snapshot){
    if((snapshot.event_seq??0)<this.sidebarSequence)return;
    this.sidebar=snapshot.sidebar;this.sidebarSequence=snapshot.event_seq??0;
    const selected=this.view.snapshot;
    if(selected&&selected.sidebar!==snapshot.sidebar&&scopeKey(snapshotScope(selected))!==scopeKey(snapshotScope(snapshot))){
      this.update({snapshot:{...selected,sidebar:{...this.sidebar,current_conversation_id:selected.sidebar.current_conversation_id,new_chat:selected.sidebar.new_chat}}});
    }
  }
  private withSidebar(snapshot:Snapshot):Snapshot{
    return this.sidebar&&snapshot.sidebar!==this.sidebar?{...snapshot,sidebar:{...this.sidebar,current_conversation_id:snapshot.sidebar.current_conversation_id,new_chat:snapshot.sidebar.new_chat}}:snapshot;
  }
  private cacheSnapshot(snapshot:Snapshot){
    const key=scopeKey(snapshotScope(snapshot));
    this.snapshots.delete(key);this.snapshots.set(key,snapshot);
    while(this.snapshots.size>8){
      const oldest=[...this.snapshots.keys()].find(item=>item!==scopeKey(this.getSelectedScope()??snapshotScope(snapshot))&&!this.scopeListeners.has(item));
      if(!oldest)break;
      this.snapshots.delete(oldest);
    }
    this.scopeListeners.get(key)?.forEach(fn=>fn());
  }
  private update(patch:Partial<View>){
    const before=this.view.selectedScope;
    if(patch.snapshot){this.cacheSnapshot(patch.snapshot);if(patch.selectedScope===undefined)patch.selectedScope=snapshotScope(patch.snapshot);}
    if(patch.snapshot&&!patch.snapshot.busy)this.cancelRequested.delete(scopeKey(snapshotScope(patch.snapshot)));
    const after=patch.selectedScope;
    if(after&&(!before||scopeKey(before)!==scopeKey(after))){this.scopeChanges++;}
    this.view={...this.view,...patch};
    const selected=this.getSelectedScope();this.view.mutating=!!selected&&!!this.mutations.get(scopeKey(selected));
    this.listeners.forEach(fn=>fn());
  }
  private publish(snapshot:Snapshot){
    if(!snapshot.busy)this.cancelRequested.delete(scopeKey(snapshotScope(snapshot)));
    if(this.view.selectedScope&&scopeKey(this.view.selectedScope)===scopeKey(snapshotScope(snapshot)))this.update({snapshot});else this.cacheSnapshot(snapshot);
  }
  private applyScopedEvent(event:ServerEvent){this.applyEvents([event]);}
  private applyEvents(events:ServerEvent[]){
    const updated=new Map<string,Snapshot>();
    for(const event of events){
      if(event.kind==='resync'||event.kind==='ready')continue;
      // The sidebar spans every project, so one row update applies to every cached view.
      if(event.kind==='sidebar_item'){
        for(const events of this.loadingEvents.values())events.push(event);
        const keys=new Set<string>(this.snapshots.keys());
        if(this.view.snapshot)keys.add(scopeKey(snapshotScope(this.view.snapshot)));
        for(const key of keys){
          const state=updated.get(key)??this.snapshots.get(key)??(this.view.snapshot&&scopeKey(snapshotScope(this.view.snapshot))===key?this.view.snapshot:null);
          if(!state)continue;
          const next=reduceEvent(state,event);
          if(next)updated.set(key,next);
        }
        const selected=this.getSelectedScope();
        const sidebar=selected&&updated.get(scopeKey(selected))?.sidebar;
        if(sidebar){
          this.sidebar=sidebar;
          this.sidebarSequence=Math.max(this.sidebarSequence,event.seq??0);
        }
        continue;
      }
      const scope=event.kind==='snapshot'?snapshotScope(event.state):event.project_id&&event.session_id?{projectId:event.project_id,sessionId:event.session_id}:this.getSelectedScope();
      if(!scope)continue;
      const key=scopeKey(scope),current=updated.get(key)??this.snapshots.get(key)??null;
      if(event.kind!=='snapshot'&&!current&&!this.loadingEvents.has(key))void this.loadHistory(scope).catch(()=>{});
      if(event.kind!=='snapshot')this.loadingEvents.get(key)?.push(event);
      if(event.kind!=='snapshot'&&event.seq!==undefined&&current?.event_seq!==undefined&&event.seq<=current.event_seq)continue;
      if(event.kind==='snapshot'&&current?.event_seq!==undefined&&event.state.event_seq!==undefined&&event.state.event_seq<current.event_seq)continue;
      const next=reduceEvent(current,event);
      if(next)updated.set(key,event.seq===undefined?next:{...next,event_seq:event.seq});
    }
    for(const snapshot of updated.values())this.publish(snapshot);
  }
  private disconnect(){
    this.generation++;this.close?.();this.retry?.();this.update({connected:false});return this.generation;
  }
  stop=()=>{this.disconnect();};
  async start(){
    const generation=this.disconnect();
    try{
      const value=await this.io.request('/state') as {token:string};
      if(generation!==this.generation)return;
      if(!value.token)throw new Error('未收到安全令牌');
      this.token=value.token;this.listen(generation);
    }catch(error){if(generation!==this.generation)return;this.update({error:(error as Error).message});this.retry=this.io.retry(()=>void this.start());}
  }
  private listen(generation:number){
    this.close=this.io.subscribe(event=>{
      if(generation!==this.generation)return;
      const epoch=event.epoch??(event.kind==='snapshot'?event.state.event_epoch:undefined);
      const restarted=!!epoch&&!!this.serverEpoch&&epoch!==this.serverEpoch;
      if(restarted)this.resetHistory(0);
      if(epoch)this.serverEpoch=epoch;
      if(event.kind==='resync'){
        if(event.reset_cursor||event.seq!==undefined&&event.seq<this.lastSequence){
          this.resetHistory(event.seq??0);
        }else this.lastSequence=Math.max(this.lastSequence,event.seq??0);
        this.update({connected:true});this.resyncCached();return;
      }
      if(restarted)this.resyncCached();
      if(event.kind==='ready'){
        this.update({connected:true,error:this.mutationError});
        return;
      }
      if(event.seq!==undefined){
        if(event.seq<=this.lastSequence&&(event.kind!=='snapshot'||this.view.connected))return;
        if(this.lastSequence&&event.seq>this.lastSequence+1)this.resyncCached();
        this.lastSequence=event.seq;
      }
      if(event.kind==='snapshot'){
        this.learnSidebar(event.state);
        const reconnect=!this.view.connected;
        const select=!this.view.selectedScope||this.pendingNavigation&&event.state.navigation_id===this.pendingNavigation||this.awaitingNavigationSnapshot;
        if(select){
          this.pendingNavigation=undefined;this.awaitingNavigationSnapshot=false;
          const cached=this.getScope(snapshotScope(event.state));
          if(cached?.event_seq===undefined||event.state.event_seq===undefined||cached.event_seq<=event.state.event_seq)this.update({snapshot:event.state,navigating:false,connected:true,error:this.mutationError});
          else this.update({connected:true});
        }else{this.applyScopedEvent(event);this.update({connected:true,error:this.mutationError});}
        this.lastSequence=Math.max(this.lastSequence,event.state.event_seq??0);
        if(reconnect&&this.localSelection)this.resyncCached();
        this.deliverDraft();return;
      }
      this.applyScopedEvent(event);
    },()=>{
      if(generation!==this.generation)return;
      this.disconnect();this.update({error:'连接中断，正在同步；提交不会自动重发。'});
      this.retry=this.io.retry(()=>void this.start());
    },this.lastSequence);
  }
  private resetHistory(sequence:number){
    this.historyEpoch++;this.lastSequence=sequence;
    this.historyLoads.clear();this.loadingEvents.clear();this.sidebarSequence=-1;
    for(const state of [...this.snapshots.values()])this.publish({...state,event_seq:undefined,event_epoch:undefined});
  }
  private resyncCached(){
    const scopes=new Map([...this.snapshots.values()].map(state=>[scopeKey(snapshotScope(state)),snapshotScope(state)]));
    const selected=this.getSelectedScope();if(selected)scopes.set(scopeKey(selected),selected);
    for(const scope of scopes.values())void this.loadHistory(scope).catch(error=>{
      this.reportError(error,scope);
    });
  }
  private loadHistory(scope:AttachmentScope,conversationId?:string):Promise<Snapshot>{
    const key=scopeKey(scope),existing=this.historyLoads.get(key);
    if(existing)return existing;
    const epoch=this.historyEpoch;
    const query=new URLSearchParams({project_id:scope.projectId,...(conversationId?{conversation_id:conversationId}:{session_id:scope.sessionId})});
    this.loadingEvents.set(key,[]);
    const loading:Promise<Snapshot>=this.io.request('/session-view?'+query).then(value=>{
      if(epoch!==this.historyEpoch)return this.historyLoads.get(key)??this.getScope(scope)??this.loadHistory(scope,conversationId);
      let state=value as Snapshot;
      if(state.project?.id!==scope.projectId||!state.session?.id||!conversationId&&state.session.id!==scope.sessionId)throw new Error('会话历史响应无效');
      if(state.event_epoch&&this.serverEpoch&&state.event_epoch!==this.serverEpoch)throw new Error('会话历史已过期，请同步后重试');
      for(const event of this.loadingEvents.get(key)??[]){if(event.seq===undefined||state.event_seq===undefined||event.seq>state.event_seq)state={...(reduceEvent(state,event)??state),...(event.seq!==undefined?{event_seq:event.seq}:{})};}
      const cached=this.getScope(snapshotScope(state));
      if(cached?.event_seq!==undefined&&state.event_seq!==undefined&&cached.event_seq>state.event_seq)return cached;
      this.learnSidebar(state);state=this.withSidebar(state);this.publish(state);return state;
    }).finally(()=>{if(this.historyLoads.get(key)===loading){this.historyLoads.delete(key);this.loadingEvents.delete(key);}});
    this.historyLoads.set(key,loading);return loading;
  }
  private projectRequest(path:string,body?:unknown,method:'GET'|'POST'|'PATCH'|'DELETE'='POST',scope=this.getSelectedScope(),signal?:AbortSignal){
    if(!scope)throw new Error('会话未就绪，请同步后重试');
    if(body&&typeof body==='object'){const owner=body as {project_id?:string;session_id?:string};if(owner.project_id!==undefined&&owner.project_id!==scope.projectId||owner.session_id!==undefined&&owner.session_id!==scope.sessionId)throw new Error('请求会话与目标会话不匹配');}
    const epoch=this.historyEpoch;
    let url=path;
    let payload=body;
    if(method==='GET'){
      const query=new URLSearchParams({project_id:scope.projectId,session_id:scope.sessionId});
      url=path+'?'+query;
    }else{
      payload={...(body as Record<string,unknown>),project_id:scope.projectId,session_id:scope.sessionId,submission_id:crypto.randomUUID()};
    }
    const request=this.io.request(url,payload,this.token,method,signal);
    return request.then(result=>{if(epoch!==this.historyEpoch)throw new Error('服务状态已变化，请同步后确认提交结果');return result;});
  }
  clearError=()=>{this.mutationError='';this.update({error:''});};
  reportError=(error:unknown,scope?:AttachmentScope)=>{if(scope&&(!this.getSelectedScope()||scopeKey(scope)!==scopeKey(this.getSelectedScope()!)))return;this.mutationError=error instanceof Error?error.message:String(error);this.update({error:this.mutationError});};
  private checkAttachmentScope(scope:AttachmentScope,allowUploads=false){
    if(!this.view.connected||this.view.navigating||(this.view.mutating&&!(allowUploads&&this.attachmentUploads)))throw new Error('连接未就绪或正在提交，请稍后重试');
    const state=this.view.snapshot;
    if(state?.project.id!==scope.projectId||state?.session.id!==scope.sessionId)throw new Error('工作区或会话已切换，请返回原会话重试');
    return state;
  }
  uploadAttachment=async(file:File,scope:AttachmentScope):Promise<AttachmentDescriptor>=>{
    const epoch=this.historyEpoch;
    let uploading=false;
    try{
      this.messageReady();this.checkAttachmentScope(scope,true);
      if(file.size>20*1024*1024)throw new Error('每个附件最多 20 MiB');
      if(!this.io.upload)throw new Error('当前连接不支持附件上传');
      this.attachmentUploads++;uploading=true;this.beginMutation(scope);
      const query=new URLSearchParams({project_id:scope.projectId,session_id:scope.sessionId,name:file.name});
      const result=await this.io.upload('/attachments?'+query,file,this.token);
      if(epoch!==this.historyEpoch)throw new Error('服务状态已变化，请同步后确认上传结果');
      this.checkAttachmentScope(scope,true);
      return result;
    }catch(error){if(this.view.snapshot?.project.id===scope.projectId&&this.view.snapshot?.session.id===scope.sessionId)this.reportError(error);throw error;}
    finally{if(uploading){this.attachmentUploads--;this.endMutation(scope);}}
  };
  configurationRequest=async<T>(path='',body?:unknown,method?:'DELETE'):Promise<T>=>{
    if(!this.view.connected)throw new Error('连接未就绪，请同步后重试');
    const scope=this.getSelectedScope();
    if(!scope)throw new Error('会话未就绪，请同步后重试');
    if(path===''){
      const payload={...((body??{}) as Record<string,unknown>),project_id:scope.projectId,session_id:scope.sessionId};
      return await this.io.request('/configuration',payload,this.token,'POST') as T;
    }
    return await this.projectRequest('/configuration'+path,body,method??'POST',scope) as T;
  };
  subagentRequest:SubagentRequest=async<T>(scope:import('../subagents').SubagentScope,path:string,body?:Record<string,unknown>,signal?:AbortSignal):Promise<T>=>{
    const check=()=>{const state=this.view.snapshot;if(!this.view.connected||this.view.navigating||scope.project_id!==state?.project.id||scope.session_id!==state?.session.id||scope.generation!==undefined)throw new Error('父会话已切换或连接未就绪，请同步后继续');};
    check();
    const result=await this.projectRequest('/subagents'+path,body,body===undefined?'GET':'POST',{projectId:scope.project_id!,sessionId:scope.session_id},signal);
    check();if(signal?.aborted)throw new Error('请求已取消');return result as T;
  };
  scheduleRequest=async<T>(projectId:string,path='',body?:unknown,method?:'PATCH'):Promise<T>=>{
    if(!this.view.connected)throw new Error('连接未就绪，请同步后重试');
    if(!path&&body!==undefined){
      const binding=body as {project_id?:string;session_id?:string};
      const scope=binding.project_id&&binding.session_id?{projectId:binding.project_id,sessionId:binding.session_id}:this.getSelectedScope();
      if(!scope||scope.projectId!==projectId)throw new Error('新任务来源工作区不一致，请重新打开创建表单');
      return await this.projectRequest('/scheduled-tasks',body,'POST',scope) as T;
    }
    return await (body===undefined?this.io.request('/scheduled-tasks'+path):this.io.request('/scheduled-tasks'+path,body,this.token,method)) as T;
  };
  private async write(send:(scope:AttachmentScope)=>Promise<unknown>,navigation?:'event'|'response',navigationId?:string){
    if(!this.view.connected||this.view.mutating||this.view.navigating&&!navigation)throw new Error('连接未就绪或正在提交，请稍后重试');
    const scope=this.getSelectedScope()!,generation=this.generation,epoch=this.historyEpoch;
    if(navigation==='event'){this.awaitingNavigationSnapshot=!navigationId;this.pendingNavigation=navigationId;this.update({navigating:true});}
    this.mutationError='';this.beginMutation(scope);this.update({error:''});
    try{
      const navigationVersion=this.navigation;
      const result=await send(scope);
      if(epoch!==this.historyEpoch)throw new Error('服务状态已变化，请同步后确认提交结果');
      if(navigation==='response'&&generation===this.generation&&navigationVersion===this.navigation){const target=result as Snapshot;if(target.project&&target.session&&scopeKey(snapshotScope(target))!==scopeKey(scope))this.update({snapshot:this.getScope(snapshotScope(target))??target,navigating:false});else this.update({navigating:false});}
      return result;
    }
    catch(error){
      if(generation===this.generation&&this.getSelectedScope()&&scopeKey(scope)===scopeKey(this.getSelectedScope()!)){this.mutationError=isBusinessError(error)?(error as Error).message:`${(error as Error).message}。请同步后确认结果再重试。`;this.update({error:this.mutationError,navigating:false});this.pendingNavigation=undefined;this.awaitingNavigationSnapshot=false;if(!isBusinessError(error)){this.disconnect();void this.start();}}else if(!isBusinessError(error))void this.loadHistory(scope).catch(()=>{});
      throw error;
    }finally{this.endMutation(scope);}
  }
  private idle(){const state=this.view.snapshot;if(!state)throw new Error('连接未就绪或正在提交，请稍后重试');if(state.busy||state.pending)throw new Error('请先等待任务完成或处理待确认请求');return state;}
  private messageReady(){const state=this.view.snapshot;if(!state)throw new Error('连接未就绪或正在提交，请稍后重试');if(state.pending||state.busy&&!state.cancellable&&state.status!=='stopping')throw new Error('请先处理待确认请求或等待运行完成');if(state.project.archived||state.project.removed)throw new Error('此项目不可发送消息');return state;}
  send=async(text:string,attachments?:{ids:string[];scope:AttachmentScope},expectedScope?:AttachmentScope,selection?:ComposerSelection)=>{
    const state=this.messageReady();const scope=expectedScope??attachments?.scope??{projectId:state.project.id,sessionId:state.session.id};
    const settings=selection??this.composerSettings(state);
    this.checkAttachmentScope(scope);
    if(attachments&&scopeKey(attachments.scope)!==scopeKey(scope))throw new Error('附件会话与目标会话不匹配');
    if(!text.trim()&&!attachments?.ids.length)throw new Error('消息不能为空');
    if((attachments?.ids.length??0)>16||new Set(attachments?.ids).size!==(attachments?.ids.length??0))throw new Error('每条消息最多 16 个附件，且不能重复');
    const sendingKey=scopeKey(scope);if(this.sending.has(sendingKey))throw new Error('正在提交，请稍后重试');
    this.sending.add(sendingKey);
    try{
      this.messageReady();this.checkAttachmentScope(scope);
      const submissionId=crypto.randomUUID();
      await this.write(()=>this.io.request('/messages',{message:text.trim(),mode:'auto',event_id:submissionId,submission_id:submissionId,
        project_id:scope.projectId,session_id:scope.sessionId,attachment_ids:attachments?.ids??[],
        model_ref:settings.model,permission_mode:settings.permissionMode},this.token));
    }finally{this.sending.delete(sendingKey);}
  };
  navigate=async(path:string,body?:Record<string,unknown>)=>{
    if(!this.view.snapshot||!this.view.connected||this.view.mutating||this.view.navigating)throw new Error('连接未就绪或正在提交，请稍后重试');
    this.draftTransfer=undefined;this.navigation++;this.update({navigating:true});
    try{
      const navigationId=crypto.randomUUID();
      return await this.write(()=>this.io.request(path,{...body,navigation_id:navigationId},this.token),'event',navigationId);
    }
    catch(error){if(this.view.connected&&!this.awaitingNavigationSnapshot)this.update({navigating:false});throw error;}
  };
  newChat=async(projectId?:string,preserveDraft=false)=>{
    const state=this.view.snapshot;if(!state)throw new Error('连接未就绪，请同步后重试');
    if(preserveDraft&&!state.sidebar?.new_chat)throw new Error('只有新聊天可以切换项目并保留草稿');
    const port=this.composerDraft;
    const captured=port?.scope.projectId===state.project.id&&port.scope.sessionId===state.session.id?port.capture():undefined;
    const draft=preserveDraft&&captured?captured:{text:'',files:[]};
    const navigation=this.navigation+1;
    const result=await this.navigate('/sidebar/new',{...(projectId?{project_id:projectId}:{}),...(preserveDraft?{draft_only:true}:{})}) as Snapshot;
    if(navigation!==this.navigation)return;
    this.draftTransfer={projectId:projectId??result?.project?.id??state.workspaces.find(project=>project.projectless)?.id,...(result?.navigation_id?{sessionId:result.session?.id}:{}),navigationId:result?.navigation_id??this.pendingNavigation,source:`${state.project.id}:${state.session.id}`,draft,expected:captured};
    this.deliverDraft();
  };
  openConversation=async(projectId:string,conversationId:string)=>{
    if(!this.view.connected)throw new Error('连接未就绪，请同步后重试');
    const previous=this.view.snapshot;
    const item=previous?.sidebar?.projects.find(project=>project.id===projectId)?.conversations.find(item=>item.id===conversationId);
    const scope={projectId,sessionId:item?.session_id??conversationId};
    const navigation=++this.navigation;
    this.localSelection=true;this.draftTransfer=undefined;
    const loading=this.loadHistory(scope,conversationId);
    this.update({selectedScope:scope,snapshot:this.getScope(scope)?this.withSidebar(this.getScope(scope)!):null,pendingTarget:{projectId,conversationId},navigating:!this.getScope(scope),error:''});
    try{
      const state=await loading;
      if(navigation!==this.navigation)return;
      this.update({snapshot:this.withSidebar(this.getScope(snapshotScope(state))??state),selectedScope:snapshotScope(state),navigating:false,pendingTarget:null});
    }catch(error){
      if(navigation===this.navigation)this.update({navigating:false,pendingTarget:null,error:(error as Error).message});
      throw error;
    }
  };
  prewarmConversation=(projectId:string,conversationId:string)=>{
    if(!this.view.connected)return;
    const item=this.view.snapshot?.sidebar?.projects.find(project=>project.id===projectId)?.conversations.find(item=>item.id===conversationId);
    const scope={projectId,sessionId:item?.session_id??conversationId};
    if(this.getScope(scope))return;
    void this.loadHistory(scope,conversationId).catch(()=>{});
  };
  updateConversation=async(projectId:string,conversationId:string,changes:{title?:string;pinned?:boolean;archived?:boolean})=>{
    const state=this.idle(),generation=this.generation,navigation=this.navigation;
    const current=projectId===state.project.id&&conversationId===state.sidebar.current_conversation_id;
    const update={...changes};
    if(update.title!==undefined){update.title=update.title.trim();if(!update.title||[...update.title].length>200)throw new Error('会话名称需为 1–200 个字符');}
    await this.write(()=>this.io.request(`/sidebar/conversations/${encodeURIComponent(conversationId)}`,{project_id:projectId,...update},this.token,'PATCH'));
    if(update.archived&&current)await this.leaveUnavailableConversation(state,generation,navigation);
  };
  private async leaveUnavailableConversation(state:Snapshot,generation:number,navigation:number){
    const selected=this.getSelectedScope();
    if(generation===this.generation&&navigation===this.navigation&&selected&&scopeKey(selected)===scopeKey(snapshotScope(state)))await this.newChat(state.project.id);
  }
  addProject=async(path:string)=>{
    const state=this.idle(),port=this.composerDraft;
    const captured=port?.scope.projectId===state.project.id&&port.scope.sessionId===state.session.id?port.capture():undefined;
    const draft=state.sidebar?.new_chat&&captured?captured:{text:'',files:[]};
    const navigation=this.navigation+1;
    const result=await this.navigate('/workspaces',{path,new_chat:true}) as Snapshot;
    if(navigation!==this.navigation||!result?.project?.id)return;
    this.draftTransfer={projectId:result.project.id,sessionId:result.session?.id,source:`${state.project.id}:${state.session.id}`,draft,expected:captured};
    this.deliverDraft();
  };
  browseDirectories=async(path?:string):Promise<DirectoryListing>=>{
    if(!this.view.connected)throw new Error('连接未就绪，请同步后重试');
    return await this.io.request('/directories',path?{path}:{},this.token) as DirectoryListing;
  };
  activateProject=async(projectId:string)=>{
    this.idle();
    return this.navigate(`/workspaces/${encodeURIComponent(projectId)}/activate`);
  };
  pickDirectory=async(path?:string,signal?:AbortSignal):Promise<string|null>=>{
    if(!this.view.connected)throw new Error('连接未就绪，请同步后重试');
    const generation=this.generation;
    const result=await this.io.request('/directories/pick',path?{path}:{},this.token,undefined,signal) as {path:string|null};
    if(generation!==this.generation)throw new Error('连接已变化，请重新选择目录');
    return result.path;
  };
  updateProject=async(projectId:string,archived:boolean)=>{
    this.idle();this.navigation++;this.draftTransfer=undefined;
    await this.write(()=>this.io.request(`/workspaces/${encodeURIComponent(projectId)}`,{archived},this.token,'PATCH'),'event');
  };
  removeProject=async(projectId:string)=>{
    this.idle();this.navigation++;this.draftTransfer=undefined;
    await this.write(()=>this.io.request(`/workspaces/${encodeURIComponent(projectId)}`,undefined,this.token,'DELETE'),'event');
  };
  getArchives=async():Promise<ArchiveSnapshot>=>{
    const generation=this.generation;
    const result=await this.io.request('/archive') as ArchiveSnapshot;
    if(generation!==this.generation)throw new Error('连接已变化，请刷新归档列表');
    return result;
  };
  restoreArchivedChat=async(storageProjectId:string,conversationId:string)=>{
    if(!this.view.connected)throw new Error('连接未就绪，请同步后重试');
    await this.io.request('/archive/restore',{storage_project_id:storageProjectId,conversation_id:conversationId},this.token);
  };
  private async reviseMessage(operation:'edit'|'regenerate'|'version',target:string,text?:string,expected?:AttachmentScope){
    const original=this.idle();
    if(original.project.archived||original.project.removed)throw new Error('请先恢复或重新打开项目');
    const scope=expected??{projectId:original.project.id,sessionId:original.session.id};
    this.checkAttachmentScope(scope);
    if(!original.history_revision)throw new Error('当前会话不支持消息版本，请重新同步');
    if(operation==='version'){
      if(!original.versions?.items.some(item=>item.session_id===target))throw new Error('该版本不属于当前会话');
      if(target===original.versions.current)return;
    }else{
      const source=original.messages.find(message=>message.id===target&&message.role==='user');
      if(!source||original.revisable_message_ids!==undefined&&!original.revisable_message_ids.includes(target))throw new Error('请选择当前会话中可编辑的用户消息');
      if(operation==='edit'&&(typeof text!=='string'||text.length>100000||!text.trim()&&!source.attachments?.length))throw new Error('编辑内容不能为空且不能超过 100000 字符');
    }
    const generation=this.generation,changes=this.scopeChanges;
    const requestId=operation==='version'?undefined:crypto.randomUUID();
    const settings=this.composerSettings(original);
    const sendingKey=scopeKey(scope);if(this.sending.has(sendingKey))throw new Error('正在提交，请稍后重试');
    this.sending.add(sendingKey);
    try{
      const current=this.idle();this.checkAttachmentScope(scope);
      if(generation!==this.generation||changes!==this.scopeChanges)throw new Error('连接或会话已切换，请重新同步后继续');
      if(!current.history_revision)throw new Error('会话历史已变化，请重新同步');
      this.navigation++;
      const body=operation==='version'
        ?{target_session_id:target}
        :{message_id:target,request_id:requestId,model_ref:settings.model,permission_mode:settings.permissionMode,
          ...(operation==='edit'?{message:text}:{})};
      await this.write(()=>this.projectRequest('/messages/'+operation,{
        history_revision:current.history_revision,...body,
      },'POST',scope),'response');
    }finally{this.sending.delete(sendingKey);}
  }
  editMessage=(messageId:string,text:string,scope?:AttachmentScope)=>this.reviseMessage('edit',messageId,text,scope);
  regenerateMessage=(messageId:string,scope?:AttachmentScope)=>this.reviseMessage('regenerate',messageId,undefined,scope);
  switchMessageVersion=(sessionId:string,scope?:AttachmentScope)=>this.reviseMessage('version',sessionId,undefined,scope);
  updateSession=async(id:string,changes:{title?:string;pinned?:boolean;archived?:boolean})=>{
    const state=this.view.snapshot,generation=this.generation,navigation=this.navigation;
    if(!state)throw new Error('连接未就绪，请同步后重试');
    const session=state.sessions.find(s=>s.id===id&&!s.deleted&&s.status!=='corrupt');
    if(!session)throw new Error('会话已失效，请同步后重试');
    const family=session.conversation_id??session.id;
    const currentFamily=state.sessions.find(item=>item.id===state.session.id)?.conversation_id??state.sidebar?.current_conversation_id??state.session.id;
    const current=family===currentFamily||!!state.versions?.items.some(item=>item.session_id===id);
    const update={...changes};
    if(update.title!==undefined){update.title=update.title.trim();if(!update.title||[...update.title].length>200)throw new Error('会话名称需为 1–200 个字符');}
    await this.write(()=>this.projectRequest(`/sessions/${encodeURIComponent(id)}`,update,'PATCH',{projectId:state.project.id,sessionId:id}));
    if(update.archived&&current)await this.leaveUnavailableConversation(state,generation,navigation);
  };
  deleteSession=async(id:string)=>{
    const state=this.view.snapshot,generation=this.generation,navigation=this.navigation;
    if(!state)throw new Error('连接未就绪，请同步后重试');
    if(!state.sessions.some(s=>s.id===id&&!s.deleted&&s.status!=='corrupt'))throw new Error('会话已失效，请同步后重试');
    await this.write(()=>this.projectRequest(`/sessions/${encodeURIComponent(id)}`,undefined,'DELETE',{projectId:state.project.id,sessionId:id}));
    if(id===state.session.id)await this.leaveUnavailableConversation(state,generation,navigation);
  };
  answer=async(id:string,answer:string)=>{
    const state=this.view.snapshot,key=state?JSON.stringify([state.project.id,state.session.id,id]):'';
    if(!state||state.pending?.id!==id||this.answeredInput.has(key)||!answer.trim())throw new Error('请求已失效或回答为空');
    await this.write(()=>this.io.request('/input',{project_id:state.project.id,session_id:state.session.id,
      request_id:id,answer:answer.trim(),submission_id:crypto.randomUUID()},this.token));
    this.answeredInput.add(key);
  };
  cancel=async(expectedScope?:AttachmentScope)=>{
    const state=this.view.snapshot;
    if(this.view.navigating)throw new Error('正在切换会话，请确认后再停止任务');
    if(!this.token||!state?.cancellable||!state.busy)throw new Error('当前任务无法停止');
    const scope=expectedScope??snapshotScope(state),key=scopeKey(scope);
    if(key!==scopeKey(snapshotScope(state)))throw new Error('工作区或会话已切换，未停止其他任务');
    if(this.cancelling.has(key)||this.cancelRequested.has(key))throw new Error('正在停止当前任务，请稍候');
    this.cancelling.add(key);
    try{
      this.cancelRequested.add(key);
      const epoch=this.historyEpoch;
      await this.io.request('/cancel',{project_id:scope.projectId,session_id:scope.sessionId,submission_id:crypto.randomUUID()},this.token);
      if(epoch!==this.historyEpoch)throw new Error('服务状态已变化，请同步后确认提交结果');
    }catch(error){this.cancelRequested.delete(key);this.reportError(error,scope);throw error;}
    finally{this.cancelling.delete(key);}
  };
}
