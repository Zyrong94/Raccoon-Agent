// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {useSyncExternalStore} from 'react';
import userEvent from '@testing-library/user-event';
import {AssistantRuntimeProvider} from '@assistant-ui/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {Composer} from './composer';
import {WorkbenchContext} from '../workbench/context';
import {useWorkbenchRuntime} from '@/lib/chat/runtime';
import {WorkbenchStore,type Transport} from '@/lib/workbench/client';
import {snapshot} from '@/lib/workbench/fixtures';
import type {ServerEvent} from '@/lib/workbench/types';

afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();});
beforeEach(()=>{vi.stubGlobal('ResizeObserver',class{observe(){}unobserve(){}disconnect(){}});Element.prototype.scrollIntoView=vi.fn();vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:local-preview');vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{});});
async function setup(state=snapshot()){
 let receive!:(event:ServerEvent)=>void;
 const upload=vi.fn();
 const request=vi.fn<Transport['request']>(async()=>({token:'secret'}));
 const io:Transport={upload,request:request,subscribe:callback=>{receive=callback;return()=>{};},retry:()=>()=>{}};
 const store=new WorkbenchStore(io);await store.start();receive({kind:'snapshot',state});
 let runtime:ReturnType<typeof useWorkbenchRuntime>['runtime'];
 function Harness(){const value=useWorkbenchRuntime(store,state,true,false);runtime=value.runtime;return <AssistantRuntimeProvider runtime={value.runtime}><WorkbenchContext.Provider value={{blocked:false,modelControl:null,headerExtra:null,notice:null,pendingSlot:null,workbench:{projectId:'p',sessionId:'s',store}}}><Composer/></WorkbenchContext.Provider></AssistantRuntimeProvider>;}
 const view=render(<Harness/>);
 return {view,upload,request,store,state,composer:()=>runtime.thread.composer};
}

it('sends the model and permission captured before attachment upload starts',async()=>{
 const app=await setup(snapshot({models:[{ref:'m',label:'M'},{ref:'next',label:'Next'}]}));
 let finishUpload!:()=>void;
 app.upload.mockImplementation(()=>new Promise(resolve=>{
  finishUpload=()=>resolve({id:'notes',kind:'file',name:'note.txt',size:4,content_type:'text/plain',content_url:'',download_url:''});
 }));
 await act(async()=>app.composer().addAttachment(new File(['note'],'note.txt',{type:'text/plain'})));
 await userEvent.setup().click(screen.getByRole('button',{name:'发送消息'}));
 await waitFor(()=>expect(app.upload).toHaveBeenCalledOnce());
 act(()=>{app.store.selectComposerModel('next');void app.store.selectComposerPermission('bypass');});
 await act(async()=>finishUpload());
 await waitFor(()=>expect(app.request).toHaveBeenCalledWith('/messages',expect.objectContaining({
  model_ref:'m',permission_mode:'ask',attachment_ids:['notes'],
 }),'secret'));
 expect(app.store.composerSettings()).toMatchObject({model:'next',permissionMode:'bypass'});
});

it('opens the file picker directly from the add button, shows the preview and removes it before upload',async()=>{
 const app=await setup();const user=userEvent.setup();
 const click=vi.spyOn(HTMLInputElement.prototype,'click').mockImplementation(()=>{});
 // The plus button is the picker itself: no intermediate menu between click and dialog.
 await user.click(screen.getByRole('button',{name:'添加图片或文件'}));
 expect(screen.queryByRole('menuitem')).toBeNull();
 expect(click).toHaveBeenCalledTimes(1);
 const picker=document.querySelector<HTMLInputElement>('input[type="file"]')!;
 expect(picker).toBeTruthy();expect(picker.multiple).toBe(true);
 await act(async()=>{fireEvent.change(picker,{target:{files:[new File(['shot'],'shot.png',{type:'image/png'})]}});});
 expect(await screen.findByRole('button',{name:'Preview shot.png'})).toBeTruthy();
 expect(app.composer().getState().attachments).toHaveLength(1);
 await user.click(screen.getByRole('button',{name:'Remove shot.png'}));
 await waitFor(()=>expect(app.composer().getState().attachments).toHaveLength(0));
 expect(app.upload).not.toHaveBeenCalled();
});

it('routes file drop and screenshot paste into the same official attachment adapter',async()=>{
 const app=await setup();const input=screen.getByRole('textbox',{name:'消息输入'});
 const drop=new File(['notes'],'notes.txt',{type:'text/plain'});
 await act(async()=>{fireEvent.drop(input,{dataTransfer:{types:['Files'],files:[drop]}});});
 expect(app.composer().getState().attachments.map(file=>file.name)).toEqual(['notes.txt']);
 const screenshot=new File(['shot'],'clipboard.png',{type:'image/png'});
 await act(async()=>{fireEvent.paste(input,{clipboardData:{files:[screenshot],items:[{kind:'file',type:'image/png',getAsFile:()=>screenshot}],getData:()=>''}});});
 expect(app.composer().getState().attachments.map(file=>file.name)).toEqual(['notes.txt','clipboard.png']);
 expect(await screen.findByRole('button',{name:'Preview clipboard.png'})).toBeTruthy();
 expect(app.upload).not.toHaveBeenCalled();
 // No speech adapter here, so the composer must not render a dead mic button.
 expect(screen.queryByRole('button',{name:/语音输入/})).toBeNull();
});

it('locks only navigation input until the target SSE remount, including the gap after HTTP acknowledgement',async()=>{
 let receive!:(event:ServerEvent)=>void,finish!:(value:unknown)=>void;
 const request=vi.fn<Transport['request']>(async(path)=>path==='/sidebar/new'?new Promise(resolve=>{finish=resolve;}):{token:'secret'});
 const store=new WorkbenchStore({request,subscribe:callback=>{receive=callback;return()=>{};},retry:()=>()=>{}});
 const initial=snapshot();await store.start();receive({kind:'snapshot',state:initial});
 function Session({state}:{state:ReturnType<typeof snapshot>}){
  const value=useWorkbenchRuntime(store,state,true,false);
  return <AssistantRuntimeProvider runtime={value.runtime}><WorkbenchContext.Provider value={{blocked:false,modelControl:null,headerExtra:null,notice:null,pendingSlot:null,workbench:{store,projectId:state.project.id,sessionId:state.session.id}}}><Composer/></WorkbenchContext.Provider></AssistantRuntimeProvider>;
 }
 function Live(){const view=useSyncExternalStore(store.subscribe,store.get,store.get);return <Session key={view.snapshot!.session.id} state={view.snapshot!}/>;}
 render(<Live/>);const oldInput=screen.getByRole('textbox',{name:'消息输入'}) as HTMLTextAreaElement;
 let navigating!:Promise<void>;act(()=>{navigating=store.newChat();});
 expect(oldInput.disabled).toBe(true);
 await userEvent.setup().type(oldInput,'切换途中输入');expect(oldInput.value).toBe('');
 const next=snapshot({navigation_id:(request.mock.calls.find(call=>call[0]==='/sidebar/new')![1] as {navigation_id:string}).navigation_id,session:{id:'next',title:'新会话'}});
 await act(async()=>{finish(next);await navigating;});
 expect(oldInput.disabled).toBe(true);
 act(()=>receive({kind:'snapshot',state:next}));
 const target=screen.getByRole('textbox',{name:'消息输入'}) as HTMLTextAreaElement;
 expect(target).not.toBe(oldInput);expect(target.disabled).toBe(false);
 await userEvent.setup().type(target,'新会话草稿');expect(target.value).toBe('新会话草稿');
 act(()=>receive({kind:'snapshot',state:{...next,busy:true}}));
 expect(target.disabled).toBe(false);await userEvent.setup().type(target,'继续写');expect(target.value).toBe('新会话草稿继续写');
});

it('sends automatically while running without manual send options',async()=>{
 let receive!:(event:ServerEvent)=>void;
 const state=snapshot({busy:true,cancellable:true,status:'running'});
 const request=vi.fn<Transport['request']>(async(path)=>path==='/messages'?state:{token:'secret'});
 const store=new WorkbenchStore({request:request,subscribe:callback=>{receive=callback;return()=>{};},retry:()=>()=>{}});
 await store.start();receive({kind:'snapshot',state});
 function Harness(){const value=useWorkbenchRuntime(store,state,true,false);return <AssistantRuntimeProvider runtime={value.runtime}><WorkbenchContext.Provider value={{blocked:false,modelControl:null,headerExtra:null,notice:null,pendingSlot:null,workbench:{store,projectId:state.project.id,sessionId:state.session.id}}}><Composer/></WorkbenchContext.Provider></AssistantRuntimeProvider>;}
 render(<Harness/>);
 const input=screen.getByRole('textbox',{name:'消息输入'});
 fireEvent.change(input,{target:{value:'运行中追加一个问题'}});
 expect(screen.queryByRole('button',{name:'发送选项'})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'发送消息'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/messages',expect.objectContaining({message:'运行中追加一个问题',mode:'auto'}),'secret'));
});

it('shows stop for an empty composer while running and swaps to send as soon as text exists',async()=>{
 let receive!:(event:ServerEvent)=>void;
 const state=snapshot({busy:true,cancellable:true,status:'running'});
 const request=vi.fn<Transport['request']>(async(path)=>path==='/messages'?state:{token:'secret'});
 const store=new WorkbenchStore({request:request,subscribe:callback=>{receive=callback;return()=>{};},retry:()=>()=>{}});
 await store.start();receive({kind:'snapshot',state});
 function Harness(){const value=useWorkbenchRuntime(store,state,true,false);return <AssistantRuntimeProvider runtime={value.runtime}><WorkbenchContext.Provider value={{blocked:false,modelControl:null,headerExtra:null,notice:null,pendingSlot:null,workbench:{store,projectId:state.project.id,sessionId:state.session.id}}}><Composer/></WorkbenchContext.Provider></AssistantRuntimeProvider>;}
 render(<Harness/>);
 expect(screen.getByRole('button',{name:'停止生成'})).toBeTruthy();
 expect(screen.queryByRole('button',{name:'发送消息'})).toBeNull();
 const input=screen.getByRole('textbox',{name:'消息输入'});
 fireEvent.change(input,{target:{value:'补充要求'}});
 expect(screen.getByRole('button',{name:'发送消息'})).toBeTruthy();
 expect(screen.queryByRole('button',{name:'停止生成'})).toBeNull();
 fireEvent.change(input,{target:{value:''}});
 expect(screen.getByRole('button',{name:'停止生成'})).toBeTruthy();
});

it('sends slash-prefixed input as a normal message without suggestions',async()=>{
 const app=await setup();const input=screen.getByRole('textbox',{name:'消息输入'});
 fireEvent.change(input,{target:{value:'/context'}});
 expect(screen.queryByRole('listbox',{name:'命令建议'})).toBeNull();
 fireEvent.keyDown(input,{key:'Tab'});
 expect(app.composer().getState().text).toBe('/context');
 fireEvent.click(screen.getByRole('button',{name:'发送消息'}));
 await waitFor(()=>expect(app.request).toHaveBeenCalledWith('/messages',expect.objectContaining({message:'/context'}),'secret'));
 expect(app.request.mock.calls.some(call=>call[0]==='/commands')).toBe(false);
});
it('does not send slash text during IME composition or Shift+Enter',async()=>{
 const app=await setup();const input=screen.getByRole('textbox',{name:'消息输入'});
 fireEvent.change(input,{target:{value:'/context'}});
 fireEvent.compositionStart(input);fireEvent.keyDown(input,{key:'Enter',isComposing:true});fireEvent.compositionEnd(input);
 fireEvent.keyDown(input,{key:'Enter',shiftKey:true});
 expect(app.composer().getState().text).toBe('/context');
 expect(app.request.mock.calls.some(call=>call[0]==='/messages'||call[0]==='/commands')).toBe(false);
});
