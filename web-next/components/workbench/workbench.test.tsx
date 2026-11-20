// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {Workbench} from './workbench';
import {useAuiState} from '@assistant-ui/react';
import {Composer} from '../chat/composer';
import {useWorkbenchView} from './context';
import {useWorkbench} from '@/lib/workbench/use-workbench';
import {WorkbenchStore,type Transport} from '@/lib/workbench/client';
import {snapshot} from '@/lib/workbench/fixtures';

vi.mock('@/lib/workbench/use-workbench',()=>({useWorkbench:vi.fn()}));
vi.mock('../chat/thread',()=>({Thread:()=>{const {pendingSlot}=useWorkbenchView();const messages=useAuiState(value=>value.thread.messages);return <>{messages.map(message=><p key={message.id}>{message.content.filter(part=>part.type==='text').map(part=>part.text).join('')}</p>)}{pendingSlot}<Composer/></>;}}));
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();});

it.each(['model','permission'] as const)('keeps the %s menu and focus mounted across streamed snapshots and applies updated controls',async(kind)=>{
 vi.stubGlobal('ResizeObserver',class{observe(){}unobserve(){}disconnect(){}});
 Element.prototype.scrollIntoView=vi.fn();
 const store=new WorkbenchStore();
 let state=snapshot({models:[{ref:'m',label:'模型一'},{ref:'other',label:'模型二'}]});
 let view={selectedScope:{projectId:state.project.id,sessionId:state.session.id},snapshot:state,connected:true,mutating:false,navigating:false,error:''};
 vi.spyOn(store,'getScope').mockImplementation(scope=>scope.projectId===view.snapshot.project.id&&scope.sessionId===view.snapshot.session.id?view.snapshot:null);vi.spyOn(store,'getSelectedScope').mockImplementation(()=>view.selectedScope??null);vi.spyOn(store,'get').mockImplementation(()=>view);vi.mocked(useWorkbench).mockImplementation(()=>({store,...view}));
 const mounted=render(<Workbench/>);const user=userEvent.setup();
 const model=screen.getByRole('combobox');
 const permission=screen.getByRole('button',{name:'权限模式：需要时询问'});
 await user.click(kind==='model'?model:permission);
 const menu=await screen.findByRole(kind==='model'?'listbox':'menu');
 const focused=kind==='model'?screen.getByLabelText('Model'):within(menu).getByRole('menuitemradio',{name:'自动审核'});
 focused.focus();expect(document.activeElement).toBe(focused);
 for(const content of ['流式','流式更新']){
  state={...state,live:[{content,reasoning:''}]};view={...view,snapshot:state};mounted.rerender(<Workbench/>);
  expect(screen.getByRole(kind==='model'?'listbox':'menu')).toBe(menu);
  expect(document.activeElement).toBe(focused);
  expect(within(screen.getByRole('group',{name:'模型选择'})).getByRole('combobox')).toBe(model);
  expect(screen.getByRole('button',{name:'权限模式：需要时询问'})).toBe(permission);
 }
 await user.keyboard('{Escape}');
 state={...state,model:'other',permission_mode:'auto',busy:true};view={...view,snapshot:state};mounted.rerender(<Workbench/>);
 expect(within(screen.getByRole('group',{name:'模型选择'})).getByRole('combobox')).toBe(model);
 expect(model.textContent).toContain('模型二');
 expect((screen.getByRole('group',{name:'模型选择'}) as HTMLFieldSetElement).disabled).toBe(false);
 expect(screen.getByRole('button',{name:'权限模式：自动审核'})).toBe(permission);
 expect((permission as HTMLButtonElement).disabled).toBe(false);
});

it.each(['busy','pending'] as const)('allows draft choices during %s while sidebar navigation stays available',async(kind)=>{
 const store=new WorkbenchStore();
 const open=vi.spyOn(store,'openConversation').mockResolvedValue(undefined);
 const create=vi.spyOn(store,'newChat').mockResolvedValue(undefined);
 const send=vi.spyOn(store,'send').mockResolvedValue(undefined);
 const conversations=[{id:'s',session_id:'s',project_id:'p',title:'当前会话',pinned:false,archived:false,busy:kind==='busy',pending:kind==='pending'},{id:'other',session_id:'other',project_id:'p',title:'另一会话',pinned:false,archived:false}];
 const state=snapshot({busy:kind==='busy',cancellable:kind==='busy',pending:kind==='pending'?{id:'ask',kind:'question',question:'是否继续？',options:[],payload:{}}:null,
  models:[{ref:'m',label:'模型'}],sessions:[{id:'s',title:'当前会话'},{id:'other',title:'另一会话'}],
  sidebar:{projects:[{id:'p',name:'项目',path:'/p',available:true,conversations}],recent:conversations,current_conversation_id:'s',new_chat:false},
 });
 const view={selectedScope:{projectId:state.project.id,sessionId:state.session.id},snapshot:state,connected:true,mutating:false,navigating:false,error:''};
 vi.spyOn(store,'getScope').mockReturnValue(state);vi.spyOn(store,'getSelectedScope').mockReturnValue(view.selectedScope);vi.spyOn(store,'get').mockReturnValue(view);vi.mocked(useWorkbench).mockReturnValue({store,...view});
 render(<Workbench/>);const user=userEvent.setup();
 await user.type(screen.getByRole('textbox',{name:'消息输入'}),'保留的新消息');
 expect((screen.getByRole('button',{name:'发送消息'}) as HTMLButtonElement).disabled).toBe(kind==='pending');
 fireEvent.keyDown(screen.getByRole('textbox',{name:'消息输入'}),{key:'Enter',code:'Enter'});
 expect(send).toHaveBeenCalledTimes(kind==='busy'?1:0);
 expect((screen.getByRole('group',{name:'模型选择'}) as HTMLFieldSetElement).disabled).toBe(false);
 expect((screen.getByRole('button',{name:'权限模式：需要时询问'}) as HTMLButtonElement).disabled).toBe(false);
 const region=screen.getByRole('region',{name:'项目 聊天'});
 const current=within(region).getByRole('button',{name:'当前会话'}).closest('[data-testid="conversation-row"]') as HTMLElement;
 expect((within(current).getByRole('button',{name:'归档'}) as HTMLButtonElement).disabled).toBe(true);
 expect((within(current).getByRole('button',{name:'置顶'}) as HTMLButtonElement).disabled).toBe(true);
 await user.click(within(region).getByRole('button',{name:'另一会话'}));
 await waitFor(()=>expect(open).toHaveBeenCalledWith('p','other'));
 await user.click(screen.getByRole('button',{name:'新聊天'}));
 await waitFor(()=>expect(create).toHaveBeenCalledOnce());
});

it('keeps unknown history writes blocked while another sidebar selection stays available',async()=>{
 const store=new WorkbenchStore();const open=vi.spyOn(store,'openConversation').mockResolvedValue(undefined);const answer=vi.spyOn(store,'answer').mockResolvedValue(undefined);const cancel=vi.spyOn(store,'cancel').mockResolvedValue(undefined);
 const conversations=[{id:'cached',session_id:'cached',project_id:'p',title:'缓存会话',pinned:false,archived:false},{id:'other',session_id:'other',project_id:'p',title:'另一会话',pinned:false,archived:false}];
 const state=snapshot({session:{id:'cached',title:'缓存会话'},models:[{ref:'m',label:'模型'}],pending:{id:'ask-cached',kind:'permission_confirmation',question:'缓存中的审批',options:[{id:'allow',label:'允许'}],payload:{}},sidebar:{projects:[{id:'p',name:'项目',path:'/p',available:true,conversations}],recent:conversations,current_conversation_id:'cached',new_chat:false}});
 const view={selectedScope:{projectId:state.project.id,sessionId:state.session.id},snapshot:state,connected:true,mutating:false,navigating:true,error:''};
 vi.spyOn(store,'getScope').mockReturnValue(state);vi.spyOn(store,'getSelectedScope').mockReturnValue(view.selectedScope);vi.spyOn(store,'get').mockReturnValue(view);vi.mocked(useWorkbench).mockReturnValue({store,...view});
 render(<Workbench/>);
 expect((screen.getByRole('textbox',{name:'消息输入'}) as HTMLTextAreaElement).disabled).toBe(true);
 expect((screen.getByRole('group',{name:'模型选择'}) as HTMLFieldSetElement).disabled).toBe(true);
 expect(screen.getByRole('status').textContent).toBe('正在加载聊天记录…');
 const approval=screen.getByRole('region',{name:'待审批操作'});expect((approval.querySelector('fieldset') as HTMLFieldSetElement).disabled).toBe(true);
 const region=screen.getByRole('region',{name:'项目 聊天'});const next=within(region).getByRole('button',{name:'另一会话'});
 expect((next as HTMLButtonElement).disabled).toBe(false);fireEvent.click(next);
 expect(open).toHaveBeenCalledWith('p','other');expect(answer).not.toHaveBeenCalled();expect(cancel).not.toHaveBeenCalled();
});


it('preserves the shell and search through uncached history without showing the previous chat',async()=>{
 const store=new WorkbenchStore();const user=userEvent.setup();
 const chats=[{id:'s',project_id:'p',session_id:'s',title:'原会话',pinned:false,archived:false},{id:'b',project_id:'p',session_id:'b',title:'目标会话',pinned:false,archived:false}];
 const initial=snapshot({messages:[{id:'u',role:'user',content:'原会话私有内容'}],sidebar:{projects:[{id:'p',name:'项目',path:'/p',available:true,conversations:chats}],recent:chats,new_chat:false,current_conversation_id:'s'}});
 let view:ReturnType<WorkbenchStore['get']>={selectedScope:{projectId:'p',sessionId:'s'},snapshot:initial,connected:true,mutating:false,navigating:false,error:''};
 vi.spyOn(store,'get').mockImplementation(()=>view);vi.spyOn(store,'getSelectedScope').mockImplementation(()=>view.selectedScope??null);
 vi.spyOn(store,'getScope').mockImplementation(scope=>scope.sessionId==='s'?initial:null);
 vi.mocked(useWorkbench).mockImplementation(()=>({store,...view}));
 const mounted=render(<Workbench/>);
 await screen.findByText('原会话私有内容');
 await user.type(screen.getByRole('textbox',{name:'消息输入'}),'会话草稿');
 await user.click(screen.getByRole('button',{name:'搜索聊天'}));
 const search=screen.getByRole('textbox',{name:'搜索聊天'});await user.type(search,'会话');
 view={...view,selectedScope:{projectId:'p',sessionId:'b'},snapshot:null,navigating:true,pendingTarget:{projectId:'p',conversationId:'b'}};
 mounted.rerender(<Workbench/>);
 expect(screen.getByRole('textbox',{name:'搜索聊天'})).toBe(search);expect((search as HTMLInputElement).value).toBe('会话');
 expect(screen.queryByText('原会话私有内容')).toBeNull();
 expect(within(screen.getByRole('region',{name:'搜索结果'})).getByRole('button',{name:'目标会话'}).getAttribute('aria-current')).toBe('page');
 expect(screen.getAllByRole('status').every(status=>status.textContent==='正在加载聊天记录…')).toBe(true);
 expect((screen.getByRole('button',{name:'原会话'}) as HTMLButtonElement).disabled).toBe(false);
 view={...view,selectedScope:{projectId:'p',sessionId:'s'},snapshot:initial,navigating:false,pendingTarget:null};mounted.rerender(<Workbench/>);
 await waitFor(()=>expect((screen.getByRole('textbox',{name:'消息输入'}) as HTMLTextAreaElement).value).toBe('会话草稿'));
});


it('shows cold session choices without initializing execution',async()=>{
 vi.stubGlobal('ResizeObserver',class{observe(){}unobserve(){}disconnect(){}});
 const state={...snapshot({models:[{ref:'m',label:'模型一'},{ref:'other',label:'模型二'}]}),execution_ready:false};
 const request=vi.fn<Transport['request']>(async()=>({token:'secret'}));
 const store=new WorkbenchStore({request,subscribe:receive=>{receive({kind:'snapshot',state});return()=>{};},retry:()=>()=>{}});
 await store.start();
 vi.mocked(useWorkbench).mockReturnValue({store,...store.get()});
 render(<Workbench/>);const user=userEvent.setup();
 expect(screen.queryByRole('button',{name:'加载模型与权限'})).toBeNull();
 await user.click(screen.getByRole('combobox'));
 expect(await screen.findByRole('option',{name:'模型二'})).toBeTruthy();
 expect(request.mock.calls.filter(([,body])=>body!==undefined)).toEqual([]);
});
