import {expect,it} from 'vitest';
import {snapshot} from './fixtures';
import {reduceEvent} from './state';

it('applies only the permission field to its owning session',()=>{
 const initial=snapshot();
 const event={kind:'permission_mode' as const,project_id:'p',session_id:'s',permission_mode:'auto' as const};
 expect(reduceEvent(initial,event)).toEqual({...initial,permission_mode:'auto'});
 expect(reduceEvent(initial,{...event,session_id:'other'})).toBe(initial);
 expect(reduceEvent(initial,{...event,project_id:'other'})).toBe(initial);
});

it('updates one sidebar row and its session without dropping the rest',()=>{
 const row={id:'c1',project_id:'p',session_id:'s',title:'旧名',updated_at:'2026-01-01T00:00:00',pinned:false,archived:false};
 const initial=snapshot({sidebar:{projects:[{id:'p',name:'项目',path:'/p',available:true,conversations:[row]}],recent:[row],current_conversation_id:'c1',new_chat:false},
  sessions:[{id:'s',title:'旧名',conversation_id:'c1',pinned:false}]});
 const renamed={...row,title:'新名',pinned:true};
 const next=reduceEvent(initial,{kind:'sidebar_item',project_id:'p',conversation_id:'c1',item:renamed,
  sessions:[{id:'s',title:'旧名',conversation_id:'c1',pinned:true}]});
 expect(next?.sidebar?.projects[0].conversations[0].title).toBe('新名');
 expect(next?.sidebar?.recent[0].pinned).toBe(true);
 expect(next?.session.title).toBe('新名');
 expect(next?.sessions[0].pinned).toBe(true);
 expect(next?.messages).toBe(initial.messages);
 const removed=reduceEvent(next!,{kind:'sidebar_item',project_id:'p',conversation_id:'c1',item:null,sessions:[{id:'s',title:'旧名',archived:true}]});
 expect(removed?.sidebar?.projects[0].conversations).toHaveLength(0);
 expect(removed?.sidebar?.recent).toHaveLength(0);
 expect(removed?.sessions[0].archived).toBe(true);
});

it('applies model switches without replacing the conversation state',()=>{
 const initial=snapshot();
 const event={kind:'model' as const,project_id:'p',session_id:'s',model:'next/model',effort:'high',input_capabilities:{images:true,pdf:false}};
 const next=reduceEvent(initial,event);
 expect(next?.model).toBe('next/model');
 expect(next?.effort).toBe('high');
 expect(next?.input_capabilities).toEqual({images:true,pdf:false});
 expect(next?.messages).toBe(initial.messages);
 expect(reduceEvent(initial,{...event,session_id:'other'})).toBe(initial);
});

it('rejects message and tool events belonging to another project or session',()=>{
 const initial=snapshot();
 for(const scope of [{project_id:'other',session_id:'s'},{project_id:'p',session_id:'other'},{project_id:'p',session_id:''},{project_id:'',session_id:'s'}]){
  expect(reduceEvent(initial,{kind:'message_committed',...scope,messages:[{id:'wrong',role:'user',content:'wrong owner'}]})).toBe(initial);
  expect(reduceEvent(initial,{kind:'tool',...scope,event:{id:'wrong',name:'view',kind:'started'}})).toBe(initial);
 }
});

it('accepts matching scoped and legacy unscoped tool events',()=>{
 for(const scope of [{project_id:'p',session_id:'s'},{}]){
  const state=reduceEvent(snapshot(),{kind:'tool',...scope,event:{id:'tool',name:'view',kind:'completed'}})!;
  expect(state.activity[0].id).toBe('tool');
 }
});
