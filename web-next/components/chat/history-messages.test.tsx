// @vitest-environment jsdom
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {AssistantRuntimeProvider,ComposerPrimitive,ThreadPrimitive,useExternalStoreRuntime,type AssistantRuntime,type ThreadMessageLike} from '@assistant-ui/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {HistoryMessages} from './history-messages';
import {WorkbenchContext,type WorkbenchConfig} from '../workbench/context';
import {createScopedAttachmentAdapter} from '@/lib/chat/attachments';


const resizeObservers=new Set<ResizeObserverCallback>();
const observedElements=new Map<ResizeObserverCallback,Set<Element>>();
class Observer{
  constructor(callback:ResizeObserverCallback){this.callback=callback;resizeObservers.add(callback);observedElements.set(callback,new Set());}
  callback:ResizeObserverCallback;
  observe(element:Element){observedElements.get(this.callback)?.add(element);}
  unobserve(element:Element){observedElements.get(this.callback)?.delete(element);}
  disconnect(){resizeObservers.delete(this.callback);observedElements.delete(this.callback);}
}
beforeEach(()=>{
  vi.stubGlobal('ResizeObserver',Observer);
  vi.spyOn(HTMLElement.prototype,'offsetHeight','get').mockImplementation(function(this:HTMLElement){return this.hasAttribute('data-history-viewport')?400:80;});
  vi.spyOn(HTMLElement.prototype,'offsetWidth','get').mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype,'scrollHeight','get').mockImplementation(function(this:HTMLElement){return Number(this.querySelector('[data-total-rows]')?.getAttribute('data-total-rows')??1)*80+48;});
  HTMLElement.prototype.scrollTo=function(options?:ScrollToOptions|number){const value=typeof options==='object'?options.top??0:options??0;this.scrollTop=Math.max(0,Math.min(value,this.scrollHeight-this.clientHeight));this.dispatchEvent(new Event('scroll'));};
  HTMLElement.prototype.scrollIntoView=vi.fn();
});
afterEach(()=>{cleanup();resizeObservers.clear();observedElements.clear();vi.restoreAllMocks();vi.unstubAllGlobals();});

function Composer(){return <ComposerPrimitive.Root><ComposerPrimitive.Input aria-label="draft"/></ComposerPrimitive.Root>;}
const messages=(count=200):ThreadMessageLike[]=>Array.from({length:count},(_,index)=>({id:`message-${index}`,role:index%2?'assistant':'user',content:[{type:'text',text:`Body ${index}`}]}));
let runtime:AssistantRuntime;
function Harness({items=messages(),scope='project/session',running=false,runStatus}:{items?:ThreadMessageLike[];scope?:string;running?:boolean;runStatus?:WorkbenchConfig['runStatus']}){
  runtime=useExternalStoreRuntime({messages:items,convertMessage:message=>message,isRunning:running,onNew:async()=>{},onEdit:async()=>{},adapters:{attachments:createScopedAttachmentAdapter({check:()=>{},capabilities:()=>({images:true}),upload:async()=>{throw new Error('No uploads in this test');},reportError:()=>{}},()=>runtime?.thread.composer.getState().attachments??[])}});
  return <WorkbenchContext.Provider value={{blocked:false,modelControl:null,headerExtra:null,notice:null,pendingSlot:null,runStatus}}><AssistantRuntimeProvider runtime={runtime}><ThreadPrimitive.Root><HistoryMessages scope={scope} footer={<Composer/>}/></ThreadPrimitive.Root></AssistantRuntimeProvider></WorkbenchContext.Provider>;
}
async function scroll(top:number){
  const viewport=screen.getByRole('region',{name:'聊天记录'});
  fireEvent.wheel(viewport,{deltaY:-100});
  act(()=>{viewport.scrollTop=top;fireEvent.scroll(viewport);});
  await act(()=>new Promise(resolve=>setTimeout(resolve,30)));
  return viewport;
}

it('mounts a viewport of SDK rows and reaches the full history by natural scrolling',async()=>{
  const items=messages();const app=render(<Harness items={items}/>);
  await screen.findByText('Body 199');
  expect(app.container.querySelectorAll('[data-virtual-row-key]').length).toBeLessThan(30);
  expect(screen.queryByText('Body 0')).toBeNull();
  expect(runtime.thread.getState().messages).toHaveLength(200);
  expect(screen.queryByRole('button',{name:/显示更早/})).toBeNull();
  await scroll(0);await screen.findByText('Body 0');expect(screen.queryByText('Body 199')).toBeNull();
  await scroll(8000);await screen.findByText('Body 100');
  expect(app.container.querySelectorAll('[data-virtual-row-key]').length).toBeLessThan(30);
});

it('keeps edit and composer objects alive offscreen while new rows arrive, and resets scope',async()=>{
  const items=messages();const app=render(<Harness items={items}/>);await screen.findByText('Body 199');
  await scroll(0);await screen.findByText('Body 0');
  act(()=>{runtime.thread.getMessageById('message-0').composer.beginEdit();runtime.thread.getMessageById('message-0').composer.setText('unsaved edit');runtime.thread.composer.setText('bottom draft');});
  const editor=await screen.findByRole('textbox',{name:'编辑消息'});
  const draft=screen.getByRole('textbox',{name:'draft'});const file=new File(['content'],'keep.txt');
  await act(()=>runtime.thread.composer.addAttachment(file));
  const attachment=runtime.thread.composer.getState().attachments[0];
  await scroll(15200);
  expect(screen.getByRole('textbox',{name:'编辑消息'})).toBe(editor);
  app.rerender(<Harness items={[...items,{id:'new',role:'assistant',content:'new reply'}]}/>);
  expect((screen.getByRole('textbox',{name:'编辑消息'}) as HTMLTextAreaElement).value).toBe('unsaved edit');
  expect(screen.getByRole('textbox',{name:'draft'})).toBe(draft);
  expect(runtime.thread.composer.getState().attachments[0]).toBe(attachment);
  expect(runtime.thread.composer.getState().attachments[0].file).toBe(file);
  // Scope changes get their own scroll/retention state; no old editor row is transplanted.
  app.rerender(<Harness scope="other/session" items={messages(4).map(message=>({...message,id:`other-${message.id}`}))}/>);
  await screen.findByText('Body 3');expect(screen.queryByRole('textbox',{name:'编辑消息'})).toBeNull();
});

it('keeps each assistant footer in its own message scope and groups tool rows',async()=>{
  const items:ThreadMessageLike[]=[
    {id:'user',role:'user',content:'question'},
    {id:'first',role:'assistant',content:[{type:'text',text:'first reply'},{type:'source',sourceType:'url',id:'one',url:'https://one.example',title:'First source'}],status:{type:'incomplete',reason:'error',error:'first failure'}},
    {id:'tools',role:'assistant',content:[{type:'tool-call',toolCallId:'one',toolName:'view',args:{},result:'ok'},{type:'tool-call',toolCallId:'two',toolName:'view',args:{},result:'ok'}]},
    {id:'last',role:'assistant',content:'last reply'},
  ];
  const app=render(<Harness items={items}/>);await screen.findByText('last reply');
  const first=screen.getByText('first failure').closest('[data-message-id]');
  expect(first?.getAttribute('data-message-id')).toBe('first');
  fireEvent.mouseEnter(first!);
  expect(first?.querySelector('[aria-label="复制回复"]')).toBeTruthy();
  expect(screen.getByRole('button',{name:/ran.*2 tools/})).toBeTruthy();
  expect(app.container.querySelectorAll('[data-message-id="tools"] [aria-label="复制回复"]')).toHaveLength(0);
  expect(app.container.querySelector('[data-message-id="last"] [aria-label="复制回复"]')).toBeTruthy();
});

it('keeps a running tool group and nested result anchored through resize and streaming until an explicit return to bottom',async()=>{
  // Keep the initial scrollToIndex alignment pending until the disclosure takes over.
  const frames=new Map<number,FrameRequestCallback>();let frameId=0;
  vi.spyOn(window,'requestAnimationFrame').mockImplementation(callback=>{frames.set(++frameId,callback);return frameId;});
  vi.spyOn(window,'cancelAnimationFrame').mockImplementation(id=>{frames.delete(id);});
  const frame=()=>act(()=>{const pending=[...frames.values()];frames.clear();pending.forEach(callback=>callback(performance.now()));});
  const items=messages(20);
  items[17]={id:'tools',role:'assistant',content:[
    {type:'tool-call',toolCallId:'view',toolName:'view',args:{path:'a'},result:'file contents'},
    {type:'tool-call',toolCallId:'grep',toolName:'grep',args:{pattern:'needle'},result:'one match'},
    {type:'tool-call',toolCallId:'shell',toolName:'shell',args:{command:'pending'}},
  ],status:{type:'running'}};
  let groupHeight=80,tailHeight=80;
  vi.spyOn(HTMLElement.prototype,'scrollHeight','get').mockImplementation(function(this:HTMLElement){
    return this.hasAttribute('data-history-viewport')?20*80+48+groupHeight-80+tailHeight-80:80;
  });
  const app=render(<Harness items={items} running/>);
  const group=await screen.findByRole('button',{name:/running.*3 tools/});
  const viewport=screen.getByRole('region',{name:'聊天记录'});
  viewport.style.overflowY='auto';
  let scrollTop=Math.min(viewport.scrollTop,viewport.scrollHeight-viewport.clientHeight);
  Object.defineProperty(viewport,'scrollTop',{get:()=>scrollTop,set:(value:number)=>{scrollTop=Math.max(0,Math.min(value,viewport.scrollHeight-viewport.clientHeight));}});
  const position=viewport.scrollTop;
  const row=group.closest('[data-virtual-row-key]')!;
  const resize=async(height:number)=>{
    groupHeight=height;
    await act(()=>{
      // Deliver actual row entries to TanStack, then the resulting content resize to follow.
      for(const [callback,targets] of observedElements)if(targets.has(row))callback([{target:row,borderBoxSize:[{blockSize:height,inlineSize:800}]}] as unknown as ResizeObserverEntry[],{} as ResizeObserver);
    });
    act(()=>resizeObservers.forEach(callback=>callback([],{} as ResizeObserver)));
    frame();
  };
  fireEvent.click(group);
  await screen.findByRole('button',{name:/读取文件/});
  await resize(240);
  expect(viewport.scrollTop).toBe(position);
  expect(screen.getByRole('button',{name:'滚动到最新消息'})).toBeTruthy();
  expect(viewport.style.scrollbarWidth).toBe('');
  const tool=screen.getByRole('button',{name:/读取文件/});
  fireEvent.click(tool);await screen.findByText('file contents');await resize(480);
  expect(viewport.scrollTop).toBe(position);
  expect(viewport.style.scrollbarWidth).toBe('');
  tailHeight=200;
  app.rerender(<Harness items={items.map((item,index)=>index===19?{...item,content:'new streaming output'}:item)} running/>);
  act(()=>resizeObservers.forEach(callback=>callback([],{} as ResizeObserver)));
  expect(viewport.scrollTop).toBe(position);
  fireEvent.click(tool);await resize(240);
  expect(viewport.scrollTop).toBe(position);
  fireEvent.click(group);await resize(80);
  expect(viewport.scrollTop).toBe(position);
  // Native collapse can clamp upward to the bottom; that is not a request to follow.
  tailHeight=80;act(()=>{viewport.scrollTop=viewport.scrollHeight-viewport.clientHeight;fireEvent.scroll(viewport);});
  tailHeight=200;act(()=>resizeObservers.forEach(callback=>callback([],{} as ResizeObserver)));
  expect(viewport.scrollTop).toBe(position);
  fireEvent.keyDown(viewport,{key:'End'});
  frame();
  await waitFor(()=>expect(viewport.scrollTop).toBeGreaterThanOrEqual(viewport.scrollHeight-viewport.clientHeight));
  expect(screen.queryByRole('button',{name:'滚动到最新消息'})).toBeNull();
  tailHeight=320;act(()=>resizeObservers.forEach(callback=>callback([],{} as ResizeObserver)));
  expect(viewport.scrollTop).toBeGreaterThanOrEqual(viewport.scrollHeight-viewport.clientHeight);
});

it('keeps an empty failed assistant reply and its retry controls visible',async()=>{
  const app=render(<Harness items={[{id:'user',role:'user',content:'question'},{id:'failed',role:'assistant',content:[],status:{type:'incomplete',reason:'error',error:'empty failed reply'}}]}/>);
  await screen.findByText('empty failed reply');
  expect(app.container.querySelector('[data-message-id="failed"] [aria-label="复制回复"]')).toBeTruthy();
});

it('shows one pending indicator before the first assistant token',async()=>{
  render(<Harness items={[{id:'u',role:'user',content:'question'},{id:'pending',role:'assistant',content:[]}]} running/>);
  await screen.findByText('thinking');
  expect(screen.getAllByText('thinking')).toHaveLength(1);
});

it('distinguishes native tools from thinking without dropping the active run',async()=>{
  const items:ThreadMessageLike[]=[
    {id:'first',role:'user',content:'run local command'},
    {id:'tools',role:'assistant',content:[
      {type:'tool-call',toolCallId:'gate',toolName:'shell',args:{command:'wait'}},
      {type:'tool-call',toolCallId:'second-gate',toolName:'shell',args:{command:'wait again'}},
    ]},
    {id:'steer',role:'user',content:'small correction'},
  ];
  const running_tool_call_ids=['gate','second-gate'];
  const app=render(<Harness items={items} running runStatus={{run_phase:'thinking',running_tool_call_ids}}/>);
  await screen.findByText('thinking');
  fireEvent.click(await screen.findByRole('button',{name:'running 2 tools'}));
  expect(screen.queryByText(/Waiting on tool/)).toBeNull();
  expect(screen.getAllByText('运行命令中')).toHaveLength(2);
  app.rerender(<Harness items={items} running runStatus={{run_phase:'waiting_tools',running_tool_call_ids}}/>);
  expect(await screen.findByText('等待后台工具完成')).toBeTruthy();
  expect(screen.queryByText('thinking')).toBeNull();
  expect(runtime.thread.getState().isRunning).toBe(true);
  app.rerender(<Harness items={items} running runStatus={{run_phase:null,running_tool_call_ids}}/>);
  expect(screen.queryByText('等待后台工具完成')).toBeNull();
  expect(screen.queryByText('thinking')).toBeNull();
  const priorReply:ThreadMessageLike={id:'earlier-text',role:'assistant',content:'earlier text'};
  app.rerender(<Harness items={[...items,priorReply]} running runStatus={{run_phase:'thinking',running_tool_call_ids}}/>);
  await screen.findByText('thinking');
});

it('keeps main progress after a parallel reply and hides thinking only during main text output',async()=>{
  const items:ThreadMessageLike[]=[
    {id:'user',role:'user',content:'run local command'},
    {id:'tool',role:'assistant',content:[{type:'tool-call',toolCallId:'gate',toolName:'shell',args:{command:'wait'}}]},
  ];
  const runStatus:NonNullable<WorkbenchConfig['runStatus']>={
    run_phase:'thinking',running_tool_call_ids:['gate'],
    parallel_replies:[{source_event_id:'side',child_session_id:'child',question:'What is 2+2?',status:'succeeded',answer:'4',error:null}],
  };
  const app=render(<Harness items={items} running runStatus={runStatus}/>);
  const reply=await screen.findByRole('region',{name:'并行回答'});
  expect(reply.textContent).toContain('4');
  const thinking=await screen.findByText('thinking');
  expect(reply.compareDocumentPosition(thinking)&Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

  app.rerender(<Harness items={items} running runStatus={{...runStatus,run_phase:'waiting_tools'}}/>);
  await screen.findByText('等待后台工具完成');
  expect(screen.queryByText('thinking')).toBeNull();

  const mainText:ThreadMessageLike={id:'main-text',role:'assistant',content:'Main reply is streaming'};
  app.rerender(<Harness items={[...items,mainText]} running runStatus={{...runStatus,run_phase:'responding'}}/>);
  await screen.findByText('Main reply is streaming');
  expect(screen.queryByText('thinking')).toBeNull();
  expect(screen.queryByText('等待后台工具完成')).toBeNull();
  expect(runtime.thread.getState().isRunning).toBe(true);

  app.rerender(<Harness items={[...items,mainText]} running runStatus={runStatus}/>);
  await screen.findByText('thinking');
});

it('navigates virtual edges, disarms follow while reading, and follows a new run without stealing edit keys',async()=>{
  const items=messages();const app=render(<Harness items={items}/>);await screen.findByText('Body 199');
  const viewport=screen.getByRole('region',{name:'聊天记录'});
  expect(fireEvent.keyDown(viewport,{key:'Home',ctrlKey:true,cancelable:true})).toBe(false);
  await screen.findByText('Body 0');expect(viewport.scrollTop).toBe(0);
  fireEvent.keyDown(viewport,{key:'PageUp'});
  act(()=>resizeObservers.forEach(callback=>callback([],{} as ResizeObserver)));
  expect(viewport.scrollTop).toBe(0);
  app.rerender(<Harness items={items} running/>);
  await screen.findByText('Body 199');
  await waitFor(()=>expect(viewport.scrollTop).toBeGreaterThanOrEqual(viewport.scrollHeight-viewport.clientHeight));
  await scroll(0);await screen.findByText('Body 0');
  expect(fireEvent.keyDown(viewport,{key:'End',ctrlKey:true,cancelable:true})).toBe(false);
  await screen.findByText('Body 199');
  await waitFor(()=>expect(viewport.scrollTop).toBeGreaterThanOrEqual(viewport.scrollHeight-viewport.clientHeight));
  await scroll(0);await screen.findByText('Body 0');
  act(()=>runtime.thread.getMessageById('message-0').composer.beginEdit());
  const editor=await screen.findByRole('textbox',{name:'编辑消息'});
  expect(fireEvent.keyDown(editor,{key:'End',cancelable:true})).toBe(true);
  expect(viewport.scrollTop).toBe(0);
});
