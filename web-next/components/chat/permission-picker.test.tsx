// @vitest-environment jsdom
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import {useSyncExternalStore} from 'react';
import {AssistantRuntimeProvider} from '@assistant-ui/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {PermissionPicker} from './permission-picker';
import {Thread} from './thread';
import {WorkbenchContext} from '../workbench/context';
import {useWorkbenchRuntime} from '@/lib/chat/runtime';
import {WorkbenchStore,type Transport} from '@/lib/workbench/client';
import {snapshot} from '@/lib/workbench/fixtures';

afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it('uses the requested labels and passes the selected mode to its caller',async()=>{
 const user=userEvent.setup();const change=vi.fn().mockResolvedValue(undefined);
 render(<PermissionPicker value="ask" blocked={false} onChange={change}/>);
 await user.click(screen.getByRole('button',{name:'权限模式：需要时询问'}));
 expect(await screen.findByRole('menuitemradio',{name:'需要时询问'})).toBeTruthy();
 expect(screen.getByRole('menuitemradio',{name:/需要时询问/}).getAttribute('aria-checked')).toBe('true');
 expect(screen.getByRole('menuitemradio',{name:/全部允许/})).toBeTruthy();
 await user.click(await screen.findByRole('menuitemradio',{name:/自动审核/}));
 expect(change).toHaveBeenCalledWith('auto');
 expect(screen.getByRole('button',{name:'权限模式：需要时询问'})).toBeTruthy();
});
it('closes the menu right after a mode is chosen',async()=>{
 const user=userEvent.setup();const change=vi.fn().mockResolvedValue(undefined);
 render(<PermissionPicker value="ask" blocked={false} onChange={change}/>);
 await user.click(screen.getByRole('button',{name:'权限模式：需要时询问'}));
 await user.click(await screen.findByRole('menuitemradio',{name:'自动审核'}));
 expect(change).toHaveBeenCalledWith('auto');
 await waitFor(()=>expect(screen.queryByRole('menu')).toBeNull());
});

it('disables the control when its caller blocks it',()=>{
 render(<PermissionPicker value="bypass" blocked onChange={vi.fn()}/>);
 expect(screen.getByRole('button',{name:'权限模式：全部允许'}).hasAttribute('disabled')).toBe(true);
});
it('does not resubmit an unchanged mode',async()=>{
 const user=userEvent.setup();const change=vi.fn();
 render(<PermissionPicker value="auto" blocked={false} onChange={change}/>);
 await user.click(screen.getByRole('button',{name:'权限模式：自动审核'}));
 await user.click(await screen.findByRole('menuitemradio',{name:/自动审核/}));
 expect(change).not.toHaveBeenCalled();
});

it('keeps the greeting and composer in place when a draft permission changes',async()=>{
 vi.stubGlobal('ResizeObserver',class{observe(){}unobserve(){}disconnect(){}});
 HTMLElement.prototype.scrollTo=vi.fn();HTMLElement.prototype.scrollIntoView=vi.fn();
 const initial=snapshot();
 const request=vi.fn<Transport['request']>(async()=>({token:'fake-token'}));
 const store=new WorkbenchStore({
  request,subscribe:receive=>{receive({kind:'snapshot',state:initial});return()=>{};},retry:()=>()=>{},
 });
 await store.start();
 let runtime!:ReturnType<typeof useWorkbenchRuntime>['runtime'];
 function Harness(){
  const view=useSyncExternalStore(store.subscribe,store.get,store.get),state=view.snapshot!;
  runtime=useWorkbenchRuntime(store,state,view.connected,view.mutating).runtime;
  return <AssistantRuntimeProvider runtime={runtime}><WorkbenchContext.Provider value={{blocked:false,headerExtra:null,notice:null,pendingSlot:null,modelControl:<PermissionPicker value={store.composerSettings(state).permissionMode} blocked={false} onChange={store.selectComposerPermission}/>}}><Thread/></WorkbenchContext.Provider></AssistantRuntimeProvider>;
 }
 const user=userEvent.setup();render(<Harness/>);
 const greeting=screen.getByText('今天需要我帮你做什么？'),composer=screen.getByRole('textbox',{name:'消息输入'}),originalRuntime=runtime;
 await user.type(composer,'尚未发送的任务');
 await user.click(screen.getByRole('button',{name:'权限模式：需要时询问'}));
 await user.click(await screen.findByRole('menuitemradio',{name:'自动审核'}));
 expect(await screen.findByRole('button',{name:'权限模式：自动审核'})).toBeTruthy();
 expect(request.mock.calls.map(([path])=>path)).toEqual(['/state']);
 expect(store.get().snapshot?.permission_mode).toBe('ask');
 expect(runtime.thread.getState().isSendDisabled).toBe(false);
 expect(screen.getByText('今天需要我帮你做什么？')).toBe(greeting);
 expect(screen.getByRole('textbox',{name:'消息输入'})).toBe(composer);
 expect(runtime).toBe(originalRuntime);expect(runtime.thread.composer.getState().text).toBe('尚未发送的任务');
 expect(runtime.thread.getState().isRunning).toBe(false);expect(runtime.thread.getState().messages).toHaveLength(0);
 store.stop();
});


