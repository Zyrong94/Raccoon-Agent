// @vitest-environment jsdom
import {cleanup,render,screen,within,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {AssistantRuntimeProvider} from '@assistant-ui/react';
import {WorkbenchContext,type SidebarNavigation} from './context';
import {Sidebar} from './sidebar';
import {useWorkbenchRuntime} from '@/lib/chat/runtime';
import {WorkbenchStore} from '@/lib/workbench/client';
import {snapshot} from '@/lib/workbench/fixtures';
import type {ConversationSummary} from '@/lib/workbench/types';

afterEach(cleanup);
const conversation=(id:string,title:string,project_id='p'):ConversationSummary=>({id,title,project_id,session_id:`${id}-selected-version`,pinned:false,archived:false});
function setup({mainOpen=false,searchOpen=false,guard=false,busy=false,pending=false,openFails=false,noProjects=false,blocked=false,navigationBlocked=blocked,settings=false}:{mainOpen?:boolean;searchOpen?:boolean;guard?:boolean;busy?:boolean;pending?:boolean;openFails?:boolean;noProjects?:boolean;blocked?:boolean;navigationBlocked?:boolean;settings?:boolean}={}){
 const first=Array.from({length:7},(_,i)=>conversation(`c${i}`,i<2?'同名聊天':`项目聊天 ${i}`));
 const second=[conversation('paper','论文跟进','paper'),conversation('c0','同名聊天','paper')];
 second[0]!.busy=busy;second[0]!.pending=pending;
 const navigation:SidebarNavigation={state:{projects:[{id:'p',name:'Raccoon',path:'C:/Raccoon',available:true,conversations:[...first,first[0]!]},{id:'paper',name:'PaperRadar',path:'C:/PaperRadar',available:true,conversations:second}],recent:[...second,...first,first[0]!],current_conversation_id:'c6',new_chat:false},onNew:vi.fn(async()=>{}),onOpen:vi.fn(async()=>{}),onUpdate:vi.fn(async()=>{}),onAdd:vi.fn(async()=>{}),browse:vi.fn(async()=>({path:'C:/',parent:null,directories:[]}))};
 const navigateToChat=vi.fn((action:()=>void)=>{if(!guard)action();});
 navigation.updateProject=vi.fn(async()=>{});navigation.removeProject=vi.fn(async()=>{});
 if(noProjects)navigation.state={...navigation.state,projects:[],recent:[]};
 if(openFails)vi.mocked(navigation.onOpen).mockRejectedValue(new Error('offline'));
 const store=new WorkbenchStore();
 const onOpenSettings=vi.fn();
 function Harness(){const {runtime}=useWorkbenchRuntime(store,snapshot(),true,false);return <AssistantRuntimeProvider runtime={runtime}><WorkbenchContext.Provider value={{navigation,blocked,navigationBlocked,modelControl:null,headerExtra:null,notice:null,pendingSlot:null,workbench:{store,projectId:'p',sessionId:'c6-selected-version'},...(settings?{workspace:{current:{id:'p',name:'Raccoon',path:'C:/Raccoon'},items:[],status:'已连接',onSelect:()=>{}}}:{})}}><Sidebar navigation={navigation} mainOpen={mainOpen} searchOpen={searchOpen} navigateToChat={navigateToChat} onOpenSettings={settings?onOpenSettings:undefined}/></WorkbenchContext.Provider></AssistantRuntimeProvider>;}
 const rendered=render(<Harness/>);return {navigation,navigateToChat,onOpenSettings,user:userEvent.setup(),refresh:()=>rendered.rerender(<Harness/>),remount:()=>{rendered.unmount();render(<Harness/>);}};
}

it('renders independent project trees with five conversations, keeps the current family visible and has a cross-project recent index',async()=>{
 const {user,navigation}=setup();
 const first=screen.getByRole('region',{name:'Raccoon 聊天'});
 expect(within(first).getAllByTestId('conversation-row')).toHaveLength(5);
 expect(within(first).getByRole('button',{name:'项目聊天 6'}).getAttribute('aria-current')).toBe('page');
 expect(within(first).getAllByRole('button',{name:'同名聊天'})).toHaveLength(2);
 expect(within(screen.getByRole('region',{name:'最近聊天'})).getAllByTestId('conversation-row')).toHaveLength(9);
 expect(screen.queryByRole('combobox',{name:'切换工作区'})).toBeNull();
 expect(screen.queryByRole('textbox',{name:'搜索聊天'})).toBeNull();
 expect(screen.queryByText('今天')).toBeNull();
 await user.click(within(first).getByRole('button',{name:'展开显示'}));
 expect(within(first).getAllByTestId('conversation-row')).toHaveLength(7);
 await user.click(screen.getByRole('button',{name:'收起 Raccoon'}));
 expect(screen.queryByRole('region',{name:'Raccoon 聊天'})).toBeNull();
 expect(screen.getByRole('region',{name:'PaperRadar 聊天'})).toBeTruthy();
 expect(navigation.onOpen).not.toHaveBeenCalled();
});

it('archives a whole project from its menu and confirms removal preserves local files',async()=>{
 const {user,navigation}=setup();
 await user.click(screen.getByRole('button',{name:'Raccoon 项目操作'}));
 await user.click(await screen.findByRole('menuitem',{name:'归档项目'}));
 await waitFor(()=>expect(navigation.updateProject).toHaveBeenCalledWith('p',true));
 await user.click(screen.getByRole('button',{name:'Raccoon 项目操作'}));
 await user.click(await screen.findByRole('menuitem',{name:'移除项目'}));
 expect(screen.getByRole('dialog')).toBeTruthy();
 expect(screen.getByText(/保留.*目录.*会话记录/)).toBeTruthy();
 expect(navigation.removeProject).not.toHaveBeenCalled();
 await user.click(screen.getByRole('button',{name:'移除'}));
 await waitFor(()=>expect(navigation.removeProject).toHaveBeenCalledWith('p'));
});

it('starts an independent chat without requiring an active project',async()=>{
 const {user,navigation}=setup({noProjects:true});
 await user.click(screen.getByRole('button',{name:'新聊天'}));
 expect(screen.queryByRole('dialog')).toBeNull();
 expect(navigation.onNew).toHaveBeenCalledWith(undefined);
});

it('uses project and logical family IDs for open and project-local new, and applies the navigation guard',async()=>{
 const {user,navigation,navigateToChat}=setup();
 await user.click(within(screen.getByRole('region',{name:'PaperRadar 聊天'})).getByRole('button',{name:'论文跟进'}));
 expect(navigation.onOpen).toHaveBeenCalledWith('paper','paper');
 await user.click(screen.getByRole('button',{name:'在 PaperRadar 中新建聊天'}));
 expect(navigation.onNew).toHaveBeenCalledWith('paper');
 await user.click(screen.getByRole('button',{name:'新聊天'}));
 expect(navigation.onNew).toHaveBeenLastCalledWith(undefined);
 expect(navigateToChat).toHaveBeenCalledTimes(3);
});

it('leaves conversations unselected while main is open and searches across projects with project labels',async()=>{
 const {user}=setup({mainOpen:true,searchOpen:true});
 expect(document.querySelector('[data-conversation-active="true"]')).toBeNull();
 await user.type(screen.getByRole('textbox',{name:'搜索聊天'}),'论文');
 const results=screen.getByRole('region',{name:'搜索结果'});
 expect(within(results).getByText('PaperRadar')).toBeTruthy();
 expect(within(results).getAllByTestId('conversation-row')).toHaveLength(1);
});

it('routes pin, archive and rename through the shared logical conversation actions',async()=>{
 const {user,navigation}=setup();
 const row=within(screen.getByRole('region',{name:'PaperRadar 聊天'})).getByRole('button',{name:'论文跟进'}).closest('[data-testid="conversation-row"]') as HTMLElement;
 await user.click(within(row).getByRole('button',{name:'置顶'}));
 expect(navigation.onUpdate).toHaveBeenCalledWith('paper','paper',{pinned:true});
 await user.click(within(row).getByRole('button',{name:'会话操作'}));
 await user.click(await screen.findByRole('menuitem',{name:'重命名'}));
 const input=within(row).getByRole('textbox',{name:'重命名会话'});
 await user.clear(input);await user.type(input,'研究进展{Enter}');
 await waitFor(()=>expect(navigation.onUpdate).toHaveBeenCalledWith('paper','paper',{title:'研究进展'}));
 await user.click(within(row).getByRole('button',{name:'归档'}));
 expect(navigation.onUpdate).toHaveBeenCalledWith('paper','paper',{archived:true});
});

it('does not execute navigation until an unsaved scheduling guard allows it',async()=>{
 const {user,navigation,navigateToChat}=setup({guard:true});
 await user.click(screen.getByRole('button',{name:'新聊天'}));
 expect(navigateToChat).toHaveBeenCalledOnce();
 expect(navigation.onNew).not.toHaveBeenCalled();
});

it.each([{busy:true,pending:false,label:'正在运行'},{busy:false,pending:true,label:'等待确认'}])('keeps $label conversations navigable while protecting their archive action',async({busy,pending,label})=>{
 const {user,navigation}=setup({busy,pending,blocked:true,navigationBlocked:false});
 for(const region of [screen.getByRole('region',{name:'PaperRadar 聊天'}),screen.getByRole('region',{name:'最近聊天'})]){
  const row=within(region).getByRole('button',{name:'论文跟进'}).closest('[data-testid="conversation-row"]') as HTMLElement;
  expect(within(row).getByLabelText(label)).toBeTruthy();
  expect(within(row).getByRole('button',{name:'论文跟进'}).closest('[inert]')).toBeNull();
  expect((within(row).getByRole('button',{name:'归档'}) as HTMLButtonElement).disabled).toBe(true);
  await user.click(within(row).getByRole('button',{name:'论文跟进'}));
 }
 expect(navigation.onOpen).toHaveBeenCalledTimes(2);
 await user.click(screen.getByRole('button',{name:'新聊天'}));expect(navigation.onNew).toHaveBeenCalledOnce();
 await user.click(screen.getByRole('button',{name:'收起 Raccoon'}));expect(screen.queryByRole('region',{name:'Raccoon 聊天'})).toBeNull();
});

it('keeps navigation disabled during connection or mutation blocking without disabling collapse',async()=>{
 const {user,navigation}=setup({blocked:true,navigationBlocked:true});
 expect((screen.getByRole('button',{name:'新聊天'}) as HTMLButtonElement).disabled).toBe(true);
 expect((within(screen.getByRole('region',{name:'PaperRadar 聊天'})).getByRole('button',{name:'论文跟进'}) as HTMLButtonElement).disabled).toBe(true);
 await user.click(screen.getByRole('button',{name:'收起 Raccoon'}));expect(screen.queryByRole('region',{name:'Raccoon 聊天'})).toBeNull();
 expect(navigation.onOpen).not.toHaveBeenCalled();expect(navigation.onNew).not.toHaveBeenCalled();
});

it('keeps an unsuccessful open visible with a local error so it can be retried',async()=>{
 const {user,navigation}=setup({openFails:true});
 const region=screen.getByRole('region',{name:'PaperRadar 聊天'});
 await user.click(within(region).getByRole('button',{name:'论文跟进'}));
 expect(await within(region).findByRole('alert')).toBeTruthy();
 await user.click(within(region).getByRole('button',{name:'论文跟进'}));
 expect(navigation.onOpen).toHaveBeenCalledTimes(2);
});

it('replaces the connection status line with the settings entry in the sidebar footer',async()=>{
 const {user,onOpenSettings}=setup({settings:true});
 expect(screen.queryByText('已连接')).toBeNull();
 const trigger=screen.getByRole('button',{name:'设置'});
 expect(trigger.textContent).toBe('设置');
 await user.click(trigger);
 expect(onOpenSettings).toHaveBeenCalledOnce();
});

it('reveals the active conversation when a recent shortcut opens a collapsed project',async()=>{
 const {user,navigation,refresh}=setup();
 await user.click(screen.getByRole('button',{name:'收起 Raccoon'}));
 expect(screen.queryByRole('region',{name:'Raccoon 聊天'})).toBeNull();
 navigation.state={...navigation.state,current_conversation_id:'c5'};refresh();
 const project=await screen.findByRole('region',{name:'Raccoon 聊天'});
 expect(within(project).getByRole('button',{name:'项目聊天 5'}).getAttribute('aria-current')).toBe('page');
});

it('preserves project expansion, show-more and recent visibility when chat navigation remounts the shell',async()=>{
 const {user,remount}=setup();
 await user.click(within(screen.getByRole('region',{name:'Raccoon 聊天'})).getByRole('button',{name:'展开显示'}));
 await user.click(screen.getByRole('button',{name:'收起 PaperRadar'}));
 await user.click(screen.getByRole('button',{name:'最近'}));
 remount();
 expect(within(screen.getByRole('region',{name:'Raccoon 聊天'})).getAllByTestId('conversation-row')).toHaveLength(7);
 expect(screen.queryByRole('region',{name:'PaperRadar 聊天'})).toBeNull();
 expect(screen.queryByRole('region',{name:'最近聊天'})).toBeNull();
});

it('hides the neutral workspace from project groups while preserving recent and searchable chats',async()=>{
 const {user,navigation,refresh}=setup({searchOpen:true});
 const independent=conversation('independent','独立聊天','neutral');
 navigation.state.projects.push({id:'neutral',name:'无项目',path:'/neutral',available:true,projectless:true,conversations:[independent]});
 navigation.state.recent.unshift(independent);refresh();
 expect(screen.queryByRole('button',{name:'收起 无项目'})).toBeNull();
 expect(screen.queryByRole('region',{name:'无项目 聊天'})).toBeNull();
 await user.click(within(screen.getByRole('region',{name:'最近聊天'})).getByRole('button',{name:'独立聊天'}));
 expect(navigation.onOpen).toHaveBeenCalledWith('neutral','independent');
 await user.type(screen.getByRole('textbox',{name:'搜索聊天'}),'独立');
 expect(within(screen.getByRole('region',{name:'搜索结果'})).getByRole('button',{name:'独立聊天'})).toBeTruthy();
});
