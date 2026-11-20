import {expect,it,vi} from 'vitest';
import {WorkbenchStore,type Transport} from './workbench/client';
import {MainSessionStore,type MainSnapshot} from './main-session';
import {snapshot} from './workbench/fixtures';
import type {ServerEvent} from './workbench/types';

it('uses the project token and fences late child responses and writes after parent changes',async()=>{
 let receive!:(event:ServerEvent)=>void;
 const request=vi.fn<Transport['request']>(async()=>({token:'token'}));
 const store=new WorkbenchStore({request,subscribe:callback=>{receive=callback;return()=>{};},retry:()=>()=>{}});await store.start();receive({kind:'snapshot',state:snapshot()});
 const scope={project_id:'p',session_id:'s'};
 await store.subagentRequest(scope,'/child/messages',{message:'hi',mode:'follow_up',event_id:'one'});
 expect(request).toHaveBeenLastCalledWith('/subagents/child/messages',{...scope,submission_id:expect.any(String),message:'hi',mode:'follow_up',event_id:'one'},'token','POST',undefined);
 let finish!:(value:unknown)=>void;request.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
 const reading=store.subagentRequest(scope,'/child');receive({kind:'snapshot',state:snapshot({session:{id:'other',title:'other'}})});
 expect(store.getSelectedScope()).toEqual({projectId:'p',sessionId:'s'});
 request.mockResolvedValueOnce(snapshot({session:{id:'other',title:'other'}}));await store.openConversation('p','other');finish({messages:[]});
 await expect(reading).rejects.toThrow('父会话已切换');
 const before=request.mock.calls.length;await expect(store.subagentRequest(scope,'/child/stop',{})).rejects.toThrow('父会话已切换');expect(request.mock.calls.length).toBe(before);store.stop();
});

it('uses main-session generation for child controls and rejects a rotated host',async()=>{
 let generation='g1';let finish!:(value:unknown)=>void;
 const state=():MainSnapshot=>({available:true,session:{id:'main',title:'main'},session_id:'main',generation,messages:[],live:[],busy:false,cancellable:false,pending:null,status:'idle',error:null});
 const request=vi.fn(async(path:string)=>path==='/state'?{token:'token'}:path.includes('/subagents')?new Promise(resolve=>{finish=resolve;}):state());
 const store=new MainSessionStore(request);await store.refresh();const scope={session_id:'main',generation:'g1'};
 const writing=store.subagentRequest(scope,'/child/messages',{message:'review',mode:'steer',event_id:'stable'});
 expect(request).toHaveBeenLastCalledWith('/main-session/subagents/child/messages',{...scope,message:'review',mode:'steer',event_id:'stable'},'token',undefined);
 generation='g2';await store.refresh();finish({delivery:'queued'});await expect(writing).rejects.toThrow('宿主已更换');
 const before=request.mock.calls.length;await expect(store.subagentRequest(scope,'/child/stop',{})).rejects.toThrow('宿主已更换');expect(request.mock.calls.length).toBe(before);
});
