// @vitest-environment jsdom
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {AssistantRuntimeProvider,ThreadPrimitive} from '@assistant-ui/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {WorkbenchStore} from '@/lib/workbench/client';
import {useWorkbenchRuntime} from '@/lib/chat/runtime';
import {snapshot} from '@/lib/workbench/fixtures';
import type {Snapshot} from '@/lib/workbench/types';
import {WorkbenchContext} from '../workbench/context';
import {Composer,EditComposer} from './composer';
import {UserMessage,AssistantMessage} from './messages';

afterEach(cleanup);
beforeEach(()=>{globalThis.ResizeObserver??=class{observe(){}unobserve(){}disconnect(){}} as unknown as typeof ResizeObserver;Element.prototype.scrollIntoView=vi.fn();});
const source=()=>snapshot({history_revision:'rev',messages:[{id:'u',role:'user',content:'原始文字',attachments:[{id:'file',kind:'file',name:'notes.txt',content_type:'text/plain',size:5,content_url:'/api/attachments/file/content?project_id=p&session_id=s',download_url:'/api/attachments/file/download?project_id=p&session_id=s'}]},{id:'a',role:'assistant',content:'原始回答'}]});
function Harness({store,state=source()}:{store:WorkbenchStore;state?:Snapshot}){
 const {runtime}=useWorkbenchRuntime(store,state,true,false);
 return <WorkbenchContext.Provider value={{blocked:false,modelControl:null,headerExtra:null,notice:null,pendingSlot:null,workbench:{projectId:'p',sessionId:'s',store}}}><AssistantRuntimeProvider runtime={runtime}>
  <ThreadPrimitive.Messages>{({message})=>message.composer.isEditing?<EditComposer/>:message.role==='user'?<UserMessage/>:<AssistantMessage/>}</ThreadPrimitive.Messages><Composer/>
 </AssistantRuntimeProvider></WorkbenchContext.Provider>;
}

it('uses a separate edit form and retains original attachments and a rejected edit',async()=>{
 const store=new WorkbenchStore();const edit=vi.spyOn(store,'editMessage').mockRejectedValue(new Error('编辑结果未知'));
 render(<Harness store={store}/>);const user=userEvent.setup();
 await user.type(screen.getByRole('textbox',{name:'消息输入'}),'底部草稿');
 await user.hover(screen.getByText('原始文字'));
 await user.click(screen.getByRole('button',{name:'编辑消息'}));
 expect(screen.getByText('保留原附件：notes.txt')).toBeTruthy();
 expect(screen.queryByRole('button',{name:/Remove notes/})).toBeNull();
 await user.clear(screen.getByRole('textbox',{name:'编辑消息'}));await user.type(screen.getByRole('textbox',{name:'编辑消息'}),'保留编辑文字');
 await user.click(screen.getByRole('button',{name:'发送'}));
 await waitFor(()=>expect(edit).toHaveBeenCalledWith('u','保留编辑文字',{projectId:'p',sessionId:'s'}));
 await waitFor(()=>expect((screen.getByRole('textbox',{name:'编辑消息'}) as HTMLTextAreaElement).value).toBe('保留编辑文字'));
 expect((screen.getByRole('textbox',{name:'消息输入'}) as HTMLTextAreaElement).value).toBe('底部草稿');
 expect(screen.getByText('保留原附件：notes.txt')).toBeTruthy();
});

it('does not advertise editing or regeneration for background-notification messages',async()=>{
 const store=new WorkbenchStore();render(<Harness store={store} state={{...source(),revisable_message_ids:[]}}/>);
 await userEvent.setup().hover(screen.getByText('原始文字'));
 expect(screen.queryByRole('button',{name:'编辑消息'})).toBeNull();
 expect(screen.queryByRole('button',{name:'重新生成回答'})).toBeNull();
});

it('places persisted version selection beside reply actions and retries a failed turn without an assistant reply',async()=>{
 let receive!:(event:import('@/lib/workbench/types').ServerEvent)=>void;
 const store=new WorkbenchStore({request:async()=>({token:'token'}),subscribe:fn=>{receive=fn;return()=>{};},retry:()=>()=>{}});
 await store.start();
 const state={...source(),versions:{current:'s',items:[{session_id:'s',label:'原始版本',operation:'original' as const},{session_id:'v2',label:'编辑版本',operation:'edit' as const}]}};
 receive({kind:'snapshot',state});
 const mounted=render(<Harness store={store} state={state}/>);
 const selector=screen.getByRole('group',{name:'会话版本'});
 expect(selector.closest('[data-role="assistant"]')).toBeTruthy();
 expect(selector.closest('form')).toBeNull();
 mounted.unmount();
 const failed={...state,status:'error',error:'模型失败',messages:[{id:'u',role:'user' as const,content:'重试这个问题'}]};
 receive({kind:'snapshot',state:failed});
 const retry=vi.spyOn(store,'regenerateMessage').mockResolvedValue(undefined);
 render(<Harness store={store} state={failed}/>);
 await userEvent.setup().click(screen.getByRole('button',{name:'重试回答'}));
 expect(retry).toHaveBeenCalledWith('u',{projectId:'p',sessionId:'s'});
});
