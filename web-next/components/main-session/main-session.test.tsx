// @vitest-environment jsdom
import {act,cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {MainSession} from './main-session';
import {MainSessionStore,type MainSnapshot} from '@/lib/main-session';



vi.stubGlobal('ResizeObserver',class{observe(){}unobserve(){}disconnect(){}});
HTMLElement.prototype.scrollIntoView=vi.fn();
HTMLElement.prototype.scrollTo=vi.fn();

vi.mock('@/components/assistant-ui/elements/markdown-text',()=>({MarkdownText:({text}:{text:string})=><span>{text}</span>}));
beforeEach(()=>{
 vi.spyOn(HTMLElement.prototype,'offsetHeight','get').mockReturnValue(400);
 vi.spyOn(HTMLElement.prototype,'offsetWidth','get').mockReturnValue(800);
 vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockReturnValue(400);
 vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockReturnValue(800);
});
afterEach(()=>{cleanup();vi.restoreAllMocks();});
const state=(patch:Partial<MainSnapshot>={}):MainSnapshot=>({available:true,session:{id:'main',title:'主会话'},session_id:'main',generation:'owner',messages:[{id:'qq',role:'user',content:'来自 QQ 的消息'}],live:[],busy:false,cancellable:false,pending:null,status:'idle',error:null,...patch});

it('exposes the shared attachment control in the personal session',async()=>{
 const request=vi.fn(async(path:string)=>path==='/state'?{token:'token'}:state());
 const store=new MainSessionStore(request);
 render(<MainSession onClose={()=>{}} store={store}/>);
 await screen.findByText('来自 QQ 的消息');
 expect(screen.getByRole('button',{name:'添加图片或文件'})).toBeTruthy();
});

it('sends text through its own main transport and shows shared QQ history',async()=>{
 let serverState=state();
 const request=vi.fn(async(path:string)=>{
  if(path==='/state')return {token:'token'};
  if(path.endsWith('/messages'))serverState=state({messages:[{id:'qq',role:'user',content:'来自 QQ 的消息'},{id:'web',role:'user',content:'从网页继续'}]});
  return serverState;
 });
 const store=new MainSessionStore(request);const user=userEvent.setup();render(<MainSession onClose={()=>{}} store={store}/>);
 expect(await screen.findByText('来自 QQ 的消息')).toBeTruthy();await user.type(screen.getByRole('textbox',{name:'主会话输入'}),'从网页继续');await user.click(screen.getByRole('button',{name:'发送到主会话'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/main-session/messages',expect.objectContaining({message:'从网页继续',session_id:'main',generation:'owner'}),'token'));
 await act(()=>store.refresh());
 expect(await screen.findByText('从网页继续')).toBeTruthy();
});

it('uses the shared composer with personal model and permission controls instead of a QQ footer',async()=>{
 const request=vi.fn(async(path:string)=>path==='/state'?{token:'token'}:state({model:'fake/a',models:[{ref:'fake/a',label:'个人模型'}],permission_mode:'ask'}));
 const store=new MainSessionStore(request);const user=userEvent.setup();render(<MainSession onClose={()=>{}} store={store}/>);
 await screen.findByText('来自 QQ 的消息');
 expect(screen.getByRole('combobox').textContent).toContain('个人模型');
 expect(screen.queryByText('与 QQ 使用同一个主会话')).toBeNull();
 expect(screen.getByRole('textbox',{name:'主会话输入'}).getAttribute('placeholder')).toBe('输入消息…');
 await user.click(screen.getByRole('button',{name:'权限模式：需要时询问'}));
 await user.click(await screen.findByRole('menuitemradio',{name:'自动审核'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/main-session/mode',expect.objectContaining({mode:'auto',session_id:'main'}),'token'));
});

it('restores a failed draft and keeps it when leaving and returning',async()=>{
 const request=vi.fn(async(path:string)=>{if(path==='/state')return {token:'token'};if(path.endsWith('/messages'))throw new Error('宿主离线，提交结果未知');return state();});
 const store=new MainSessionStore(request);const user=userEvent.setup();const first=render(<MainSession onClose={()=>{}} store={store}/>);
 await screen.findByText('来自 QQ 的消息');await user.type(screen.getByRole('textbox',{name:'主会话输入'}),'保留这条草稿');await user.click(screen.getByRole('button',{name:'发送到主会话'}));
 await screen.findByRole('alert');await waitFor(()=>expect((screen.getByRole('textbox',{name:'主会话输入'}) as HTMLTextAreaElement).value).toBe('保留这条草稿'));
 await act(()=>store.refresh());expect(screen.getByRole('alert').textContent).toContain('提交结果未知');
 first.unmount();render(<MainSession onClose={()=>{}} store={store}/>);
 await waitFor(()=>expect((screen.getByRole('textbox',{name:'主会话输入'}) as HTMLTextAreaElement).value).toBe('保留这条草稿'));
 expect(screen.getByRole('alert').textContent).toContain('提交结果未知');
 expect(request.mock.calls.filter(call=>call[0].endsWith('/messages'))).toHaveLength(1);
});

it('answers pending input on the main identity and offers cancellation',async()=>{
 const pending={id:'ask-1',kind:'ask_user',question:'选择颜色',options:[],payload:{}};
 const request=vi.fn(async(path:string)=>path==='/state'?{token:'token'}:state({pending,busy:true,cancellable:true}));
 const store=new MainSessionStore(request);const user=userEvent.setup();render(<MainSession onClose={()=>{}} store={store}/>);
 await screen.findByText('选择颜色');await user.type(screen.getByLabelText('自由回答'),'蓝色');
 await user.click(screen.getByRole('button',{name:'发送回答'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/main-session/input',expect.objectContaining({request_id:'ask-1',answer:'蓝色',session_id:'main'}),'token'));
 await user.click(screen.getByRole('button',{name:'停止主会话'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/main-session/cancel',expect.objectContaining({generation:'owner'}),'token'));
});

it('pauses polling while hidden and aborts obsolete reads on unmount',async()=>{
 const signals:AbortSignal[]=[];
 const request=vi.fn(async(path:string,_body?:unknown,_token?:string,signal?:AbortSignal)=>{if(signal)signals.push(signal);return path==='/state'?{token:'token'}:state();});
 const store=new MainSessionStore(request);const mounted=render(<MainSession onClose={()=>{}} store={store}/>);
 await screen.findByText('来自 QQ 的消息');
 Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));
 const count=request.mock.calls.length;await new Promise(resolve=>setTimeout(resolve,800));expect(request.mock.calls.length).toBe(count);
 Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));
 await waitFor(()=>expect(request.mock.calls.length).toBeGreaterThan(count));mounted.unmount();expect(signals.at(-1)?.aborted).toBe(true);
});
