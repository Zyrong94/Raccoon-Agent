import {expect,it,vi} from 'vitest';
import {WorkbenchStore,type Transport} from './client';
import {snapshot} from './fixtures';
import type {ServerEvent,Snapshot} from './types';

async function setup(initial=snapshot()){
 let receive!:(event:ServerEvent)=>void;
 const request=vi.fn<Transport['request']>(async path=>path==='/state'?{token:'token'}:{});
 const store=new WorkbenchStore({request:request,subscribe:callback=>{receive=callback;return()=>{};},retry:()=>()=>{}});
 await store.start();receive({kind:'snapshot',state:initial});
 return {store,request,receive:(event:ServerEvent)=>receive(event)};
}

function conversationState(projectId='p',conversationId='family'):Snapshot{
 return snapshot({project:{id:projectId,name:projectId,path:`/${projectId}`},session:{id:'selected-version',title:conversationId},
  sessions:[{id:'family',title:'Original'},{id:'selected-version',conversation_id:conversationId,title:'Version'},{id:'other',title:'Other'}],
  sidebar:{projects:['p','q'].map(id=>({id,name:id,path:`/${id}`,available:true,conversations:[{id:'family',project_id:id,session_id:'selected-version',title:'Family',pinned:false,archived:false}]})),recent:[],current_conversation_id:conversationId,new_chat:false},
 });
}

it.each(['conversation','session'] as const)('leaves an archived current logical family via %s only after success and the matching navigation snapshot',async route=>{
 const initial=conversationState(),{store,request,receive}=await setup(initial);
 if(route==='conversation')await store.updateConversation('p','family',{archived:true});else await store.updateSession('family',{archived:true});
 const archive=request.mock.calls.findIndex(([path])=>path===(route==='conversation'?'/sidebar/conversations/family':'/sessions/family'));
 expect(archive).toBeGreaterThan(0);
 expect(request.mock.calls.slice(archive).map(([path])=>path)).toEqual([route==='conversation'?'/sidebar/conversations/family':'/sessions/family','/sidebar/new']);
 const body=request.mock.calls.at(-1)![1] as {project_id:string;navigation_id:string};
 expect(body).toEqual({project_id:'p',navigation_id:expect.any(String)});
 expect(store.get().snapshot?.session.id).toBe('selected-version');
 const landing=snapshot({session:{id:'draft',title:'新会话'},sidebar:{...initial.sidebar!,current_conversation_id:null,new_chat:true},navigation_id:body.navigation_id});
 receive({kind:'snapshot',state:landing});
 expect(store.get()).toMatchObject({snapshot:landing,navigating:false});
});

it.each(['other-family','other-project','restore','pin'] as const)('keeps the current view when updating %s',async scenario=>{
 const initial=conversationState(),{store,request}=await setup(initial);
 if(scenario==='other-family')await store.updateSession('other',{archived:true});
 else await store.updateConversation(scenario==='other-project'?'q':'p','family',scenario==='restore'?{archived:false}:scenario==='pin'?{pinned:true}:{archived:true});
 expect(request.mock.calls.some(([path])=>path==='/sidebar/new')).toBe(false);
 expect(store.get().snapshot).toBe(initial);
});

it.each(['switch','away-and-back','reconnect','failure'] as const)('does not redirect a late archive after %s',async scenario=>{
 const initial=conversationState(),{store,request,receive}=await setup(initial);
 let finish!:(value:unknown)=>void,reject!:(error:Error)=>void;
 const pending=new Promise((resolve,fail)=>{finish=resolve;reject=fail;});
 request.mockImplementation(async path=>path.startsWith('/sidebar/conversations/')?pending:path.startsWith('/session-view?')?new URL(path,'http://local').searchParams.get('project_id')==='q'?conversationState('q'):initial:{token:'token'});
 const archiving=store.updateConversation('p','family',{archived:true});
 if(scenario==='switch'||scenario==='away-and-back'){
  await store.openConversation('q','family');
  if(scenario==='away-and-back')await store.openConversation('p','family');
 }else if(scenario==='reconnect'){
  store.stop();await store.start();receive({kind:'snapshot',state:initial});
 }
 if(scenario==='failure'){reject(new Error('archive failed'));await expect(archiving).rejects.toThrow('archive failed');}else{finish({});await archiving;}
 expect(request.mock.calls.some(([path])=>path==='/sidebar/new')).toBe(false);
 expect(store.getSelectedScope()?.projectId).toBe(scenario==='switch'?'q':'p');
});

it.each(['busy','pending'] as const)('does not archive or navigate with %s',async scenario=>{
 const initial=conversationState(),{store,request,receive}=await setup(initial);
 if(scenario==='busy'||scenario==='pending')receive({kind:'snapshot',state:{...initial,...(scenario==='busy'?{busy:true}:{pending:{id:'approval',kind:'permission',question:'Confirm',options:[],payload:{}}})}});
 await expect(store.updateConversation('p','family',{archived:true})).rejects.toThrow();
 expect(request.mock.calls.some(([path])=>path.startsWith('/sidebar/'))).toBe(false);
});

it('forwards directory picker cancellation and keeps a cancelled selection empty',async()=>{
 const {store,request}=await setup();const controller=new AbortController();
 request.mockResolvedValueOnce({path:null});
 await expect(store.pickDirectory('/project',controller.signal)).resolves.toBeNull();
 expect(request).toHaveBeenLastCalledWith('/directories/pick',{path:'/project'},'token',undefined,controller.signal);
 expect(store.get().mutating).toBe(false);
});

it('ignores HTTP project lifecycle snapshots until the authoritative stream arrives',async()=>{
 const {store,request,receive}=await setup();const original=store.get().snapshot!;
 request.mockImplementation(async path=>path==='/state'?{token:'token'}:snapshot({project:{...original.project,archived:true}}));
 await store.updateProject('p',true);
 expect(request).toHaveBeenCalledWith('/workspaces/p',{archived:true},'token','PATCH');
 expect(store.get().snapshot?.project.archived).not.toBe(true);
 await Promise.resolve();receive({kind:'snapshot',state:snapshot({project:{...original.project,archived:true}})});
 expect(store.get().snapshot?.project.archived).toBe(true);
});

it('restores another archived chat while the selected chat is waiting for an answer',async()=>{
 const initial=snapshot({busy:true,pending:{id:'question',kind:'ask_user',question:'Continue?',options:[],payload:{}}});
 const {store,request}=await setup(initial);
 await store.restoreArchivedChat('storage-project','family-root');
 expect(request).toHaveBeenCalledWith('/archive/restore',{storage_project_id:'storage-project',conversation_id:'family-root'},'token');
 expect(store.getSelectedScope()).toEqual({projectId:initial.project.id,sessionId:initial.session.id});
 expect(store.get().snapshot?.pending?.id).toBe('question');
});

it('loads the global archive without starting navigation or sending a write token',async()=>{
 const {store,request}=await setup();const archive={projects:[],unassigned:[]};request.mockResolvedValueOnce(archive);
 await expect(store.getArchives()).resolves.toEqual(archive);
 expect(request).toHaveBeenLastCalledWith('/archive');
 expect(store.get().connected).toBe(true);
});

it('can leave an inactive project and open a new project',async()=>{
 const {store,request,receive}=await setup();const original=store.get().snapshot!;
 receive({kind:'snapshot',state:snapshot({project:{...original.project,archived:true}})});
 request.mockImplementation(async path=>path==='/state'?{token:'token'}:snapshot({project:{id:'q',name:'New',path:'/new'}}));
 await store.addProject('/new');
 expect(request).toHaveBeenCalledWith('/workspaces',{path:'/new',new_chat:true,navigation_id:expect.any(String)},'token');
});
