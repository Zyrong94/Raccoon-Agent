// @vitest-environment jsdom
import {act,cleanup,renderHook,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {useWorkbenchRuntime} from './runtime';
import {WorkbenchStore} from '../workbench/client';
import {snapshot} from '../workbench/fixtures';

afterEach(cleanup);
it('keeps inactive project history readable without edit or generation capabilities',()=>{
 const store=new WorkbenchStore();const saved=snapshot();
 const inactive=snapshot({project:{...saved.project,archived:true},history_revision:'rev',messages:[{id:'u',role:'user',content:'saved'},{id:'a',role:'assistant',content:'answer'}]});
 const {result}=renderHook(()=>useWorkbenchRuntime(store,inactive,true,false));
 expect(result.current.runtime.thread.getState().messages).toHaveLength(2);
 expect(result.current.runtime.thread.getState().capabilities.edit).toBe(false);
 expect(result.current.runtime.thread.getState().capabilities.reload).toBe(false);
 act(()=>result.current.runtime.thread.composer.setText('retained local draft'));
 expect(result.current.runtime.thread.composer.getState().canSend).toBe(false);
});
const state=()=>snapshot({history_revision:'rev-1',messages:[
 {id:'u1',role:'user',content:'第一问'},
 {id:'step',role:'assistant',content:'',calls:[{id:'call',name:'read',arguments:{}}]},
 {id:'tool',role:'tool',tool_call_id:'call',name:'read',content:'result'},
 {id:'answer',role:'assistant',content:'第一答'},
 {id:'u2',role:'user',content:'第二问'},
 {id:'a2',role:'assistant',content:'第二答'},
]});

it('reloads a merged assistant turn through its real originating user message',async()=>{
 const store=new WorkbenchStore();const regenerate=vi.spyOn(store,'regenerateMessage').mockResolvedValue();
 const {result}=renderHook(()=>useWorkbenchRuntime(store,state(),true,false));
 act(()=>result.current.runtime.thread.getMessageById('step').reload());
 await waitFor(()=>expect(regenerate).toHaveBeenCalledWith('u1',{projectId:'p',sessionId:'s'}));
});

it('submits edits to their original user ID without calling the ordinary send path',async()=>{
 const store=new WorkbenchStore();const edit=vi.spyOn(store,'editMessage').mockResolvedValue();const send=vi.spyOn(store,'send');
 const {result}=renderHook(()=>useWorkbenchRuntime(store,state(),true,false));
 const composer=result.current.runtime.thread.getMessageById('u1').composer;
 act(()=>composer.beginEdit());act(()=>composer.setText('改后的第一问'));act(()=>composer.send());
 await waitFor(()=>expect(edit).toHaveBeenCalledWith('u1','改后的第一问',{projectId:'p',sessionId:'s'}));
 expect(send).not.toHaveBeenCalled();
});

it('restores a rejected edit into its edit composer, preserving the main draft',async()=>{
 const store=new WorkbenchStore();vi.spyOn(store,'editMessage').mockRejectedValue(new Error('未知结果'));
 const {result}=renderHook(()=>useWorkbenchRuntime(store,state(),true,false));
 act(()=>result.current.runtime.thread.composer.setText('另外一条草稿'));
 act(()=>result.current.runtime.thread.getMessageById('u1').composer.beginEdit());
 act(()=>result.current.runtime.thread.getMessageById('u1').composer.setText('不能丢失的编辑'));
 act(()=>result.current.runtime.thread.getMessageById('u1').composer.send());
 await waitFor(()=>expect(result.current.runtime.thread.getMessageById('u1').composer.getState().isEditing).toBe(true));
 expect(result.current.runtime.thread.getMessageById('u1').composer.getState().text).toBe('不能丢失的编辑');
 expect(result.current.runtime.thread.composer.getState().text).toBe('另外一条草稿');
});
