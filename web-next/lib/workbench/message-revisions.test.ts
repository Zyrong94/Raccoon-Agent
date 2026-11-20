import {expect,it,vi} from 'vitest';
import {WorkbenchStore,type Transport} from './client';
import {snapshot} from './fixtures';
import type {ServerEvent} from './types';

async function setup(){
 const feeds:{receive:(event:ServerEvent)=>void}[]=[];
 const request=vi.fn<Transport['request']>(async()=>({token:'token'}));
 const store=new WorkbenchStore({request:request,subscribe:receive=>{feeds.push({receive});return()=>{};},retry:()=>()=>{}});
 await store.start();
 feeds[0].receive({kind:'snapshot',state:snapshot({history_revision:'rev-1',messages:[{id:'u',role:'user',content:'原始问题'},{id:'a',role:'assistant',content:'旧答案'}],versions:{current:'s',items:[{session_id:'s',label:'原始版本',operation:'original'},{session_id:'v2',label:'编辑版本',operation:'edit'}]}})});
 return {store,request,feeds};
}

it('submits a scoped edit with the current permission and follows the returned version',async()=>{
 const {store,request}=await setup();
 await store.selectComposerPermission('auto');
 request.mockResolvedValueOnce(snapshot({session:{id:'v2',title:'版本'}}));
 await store.editMessage('u','新的问题');
 expect(request.mock.calls[1]).toEqual(['/messages/edit',{project_id:'p',session_id:'s',history_revision:'rev-1',message_id:'u',message:'新的问题',request_id:expect.any(String),submission_id:expect.any(String),model_ref:'m',permission_mode:'auto'},'token','POST',undefined]);
 expect(store.get().snapshot?.session.id).toBe('v2');
});

it('refuses non-user targets, unavailable versions, stale scopes and busy writes',async()=>{
 const {store,request,feeds}=await setup();
 await expect(store.editMessage('a','oops')).rejects.toThrow('用户消息');
 await expect(store.regenerateMessage('foreign')).rejects.toThrow('用户消息');
 await expect(store.editMessage('u','x',{projectId:'other',sessionId:'s'})).rejects.toThrow('会话');
 await expect(store.switchMessageVersion('other')).rejects.toThrow('版本');
 feeds[0].receive({kind:'snapshot',state:snapshot({history_revision:'rev-1',busy:true,messages:[{id:'u',role:'user',content:'x'}]})});
 await expect(store.regenerateMessage('u')).rejects.toThrow('等待');
 expect(request).toHaveBeenCalledOnce();
});

it('issues only one regenerate request when its result is unknown',async()=>{
 const {store,request}=await setup();
 request.mockRejectedValueOnce(new Error('result unknown'));
 await expect(store.regenerateMessage('u')).rejects.toThrow('result unknown');
 expect(request.mock.calls.filter(call=>call[0]==='/messages/regenerate')).toHaveLength(1);
 expect(request.mock.calls[1][1]).toMatchObject({message_id:'u',history_revision:'rev-1',request_id:expect.any(String)});
 expect(store.get().snapshot?.messages.at(-1)?.content).toBe('旧答案');
});

it('switches only within the backend version catalogue without generating a reply',async()=>{
 const {store,request}=await setup();
 await store.switchMessageVersion('v2');
 expect(request.mock.calls[1]).toEqual(['/messages/version',{project_id:'p',session_id:'s',history_revision:'rev-1',target_session_id:'v2',submission_id:expect.any(String)},'token','POST',undefined]);
 expect(request.mock.calls.some(call=>call[0]==='/messages'||call[0]==='/messages/regenerate')).toBe(false);
});
