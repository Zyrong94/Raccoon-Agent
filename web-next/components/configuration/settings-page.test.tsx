// @vitest-environment jsdom
import {act,cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {AssistantRuntimeProvider} from '@assistant-ui/react';
import {SettingsPage} from './settings-page';
import {WorkbenchContext,type ProjectNavigation} from '../workbench/context';
import {useWorkbenchThreadListRuntime} from '@/lib/chat/runtime';
import {WorkbenchStore} from '@/lib/workbench/client';
import {snapshot} from '@/lib/workbench/fixtures';
import type {ArchiveSnapshot,SidebarSnapshot} from '@/lib/workbench/types';

const dateFormat=new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});

afterEach(()=>{cleanup();vi.restoreAllMocks();document.documentElement.classList.remove('dark');window.localStorage.clear();});

const archives:ArchiveSnapshot={projects:[
 {id:'p',name:'Current project',path:'/p',archived:false,available:true,conversations:[{id:'family',session_id:'a',title:'归档任务',project_id:'p',storage_project_id:'p',updated_at:'2026-10-28T12:48:00Z'},{id:'other',session_id:'b',title:'另一个归档',project_id:'p',storage_project_id:'p'}]},
],unassigned:[]};
const afterDelete:ArchiveSnapshot={...archives,projects:archives.projects.map(project=>({...project,conversations:project.conversations.filter(item=>item.session_id!=='a')}))};
const sidebar:SidebarSnapshot={projects:[
 {...archives.projects[0]!,conversations:[{id:'current',session_id:'s',title:'当前会话',project_id:'p',archived:false,pinned:false},...archives.projects[0]!.conversations.map(item=>({...item,project_id:'p',archived:true,pinned:false}))]},
 {id:'q',name:'Other project',path:'/q',available:true,conversations:[{id:'family',session_id:'a',title:'其他项目同ID',project_id:'q',archived:true,pinned:false}]},
],recent:[],current_conversation_id:'current',new_chat:false};
const initial=snapshot({sidebar});
const testStore=()=>new WorkbenchStore({request:async()=>({token:'test'}),subscribe:receive=>{receive({kind:'snapshot',state:initial});return()=>{};},retry:()=>()=>{}});
function archiveNavigation():ProjectNavigation{
 return {state:sidebar,onNew:vi.fn(),onOpen:vi.fn(),onUpdate:vi.fn(),onAdd:vi.fn(),browse:vi.fn(),getArchives:vi.fn(async()=>archives),restoreArchivedChat:vi.fn(async()=>{}),updateProject:vi.fn(async()=>{})};
}
async function renderSettings(store:WorkbenchStore,navigation=archiveNavigation()){
 await store.start();
 function Harness(){
  const {runtime}=useWorkbenchThreadListRuntime(store,initial);
  return <AssistantRuntimeProvider runtime={runtime}><WorkbenchContext.Provider value={{navigation,workbench:{store,projectId:'p',sessionId:'s'},blocked:false,modelControl:null,headerExtra:null,notice:null,pendingSlot:null}}><SettingsPage navigation={navigation} open onClose={()=>{}}/></WorkbenchContext.Provider></AssistantRuntimeProvider>;
 }
 render(<Harness/>);
 await screen.findByText('归档任务');
 return navigation;
}

it('lists only archived chats with their last message time and unarchives from the row',async()=>{
 const user=userEvent.setup();const store=testStore();const navigation=await renderSettings(store);
 expect(screen.getByRole('heading',{name:'已归档的聊天'})).toBeTruthy();
 expect(screen.getByText('归档任务')).toBeTruthy();
 expect(screen.queryByText('当前会话')).toBeNull();
 expect(screen.getByText(dateFormat.format(new Date('2026-10-28T12:48:00Z')))).toBeTruthy();
 expect(screen.getByText('2 个聊天')).toBeTruthy();
 const buttons=screen.getAllByRole('button',{name:/取消归档/});
 await user.click(buttons[0]!);
 await waitFor(()=>expect(navigation.restoreArchivedChat).toHaveBeenCalledWith('p','family'));
});

it('deletes an archived chat after confirmation',async()=>{
 const user=userEvent.setup();const store=testStore();const del=vi.spyOn(store,'deleteSession').mockResolvedValue(undefined);
 const navigation=await renderSettings(store);
 vi.mocked(navigation.getArchives!).mockResolvedValue(afterDelete);
 await user.click(await screen.findByRole('button',{name:'删除「归档任务」'}));
 expect(screen.getByRole('dialog')).toBeTruthy();
 expect(screen.getByText(/永久删除.*无法恢复/)).toBeTruthy();
 await user.click(screen.getByRole('button',{name:'删除'}));
 await waitFor(()=>expect(del).toHaveBeenCalledWith('a'));
 await waitFor(()=>expect(screen.queryByText('归档任务')).toBeNull());
 expect(screen.getByText('另一个归档')).toBeTruthy();
 expect(screen.getByText('1 个聊天')).toBeTruthy();
 expect(navigation.getArchives).toHaveBeenCalledTimes(2);
});

it('reloads the remaining archived branch after deleting only the physical session',async()=>{
 const user=userEvent.setup(),store=testStore(),del=vi.spyOn(store,'deleteSession').mockResolvedValue(undefined);
 const navigation=await renderSettings(store);
 vi.mocked(navigation.getArchives!).mockResolvedValue({...archives,projects:[{...archives.projects[0]!,conversations:[{...archives.projects[0]!.conversations[0]!,session_id:'older-version',title:'归档的旧分支'},archives.projects[0]!.conversations[1]!]}]});
 await user.click(await screen.findByRole('button',{name:'删除「归档任务」'}));
 await user.click(screen.getByRole('button',{name:'删除'}));
 expect(await screen.findByText('归档的旧分支')).toBeTruthy();
 expect(screen.queryByText('归档任务')).toBeNull();
 expect(screen.getByText('另一个归档')).toBeTruthy();
 expect(screen.getByText('2 个聊天')).toBeTruthy();
 expect(del.mock.calls).toEqual([['a']]);
 expect(navigation.getArchives).toHaveBeenCalledTimes(2);
 expect(navigation.restoreArchivedChat).not.toHaveBeenCalled();
 expect(navigation.updateProject).not.toHaveBeenCalled();
});

it('keeps archived rows while deletion is pending or fails without reloading',async()=>{
 const user=userEvent.setup(),store=testStore();
 let failDelete!:(error:Error)=>void;
 const deletion=new Promise<void>((_,reject)=>{failDelete=reject;});
 const del=vi.spyOn(store,'deleteSession').mockReturnValue(deletion),logged=vi.spyOn(console,'error').mockImplementation(()=>{});
 const navigation=await renderSettings(store);
 await user.click(await screen.findByRole('button',{name:'删除「归档任务」'}));
 await user.click(screen.getByRole('button',{name:'删除'}));
 await waitFor(()=>expect(del).toHaveBeenCalledWith('a'));
 expect(screen.getByText('归档任务')).toBeTruthy();
 expect(screen.getByText('另一个归档')).toBeTruthy();
 expect(navigation.getArchives).toHaveBeenCalledTimes(1);
 await act(async()=>failDelete(new Error('删除失败')));
 await waitFor(()=>expect(logged).toHaveBeenCalled());
 expect(await screen.findByRole('button',{name:'删除「归档任务」'})).toBeTruthy();
 expect(screen.getByText('归档任务')).toBeTruthy();
 expect(screen.getByText('另一个归档')).toBeTruthy();
 expect(navigation.getArchives).toHaveBeenCalledTimes(1);
 expect(navigation.restoreArchivedChat).not.toHaveBeenCalled();
});

it('filters archived chats by title',async()=>{
 const user=userEvent.setup();
 await renderSettings(testStore());
 await user.type(screen.getByRole('searchbox',{name:'搜索已归档的聊天'}),'另一个');
 expect(screen.getByText('另一个归档')).toBeTruthy();
 expect(screen.queryByText('归档任务')).toBeNull();
});

it('switches to the appearance section and persists dark mode',async()=>{
 const user=userEvent.setup();
 await renderSettings(testStore());
 await user.click(screen.getByRole('button',{name:'外观'}));
 const toggle=screen.getByRole('switch',{name:'深色模式'});
 expect(toggle.getAttribute('aria-checked')).toBe('false');
 await user.click(toggle);
 expect(document.documentElement.classList.contains('dark')).toBe(true);
 expect(window.localStorage.getItem('raccoon-theme')).toBe('dark');
 expect(toggle.getAttribute('aria-checked')).toBe('true');
});

it('keeps the current project single-chat delete confirmation in the grouped archive view',async()=>{
 const store=testStore(),del=vi.spyOn(store,'deleteSession').mockResolvedValue(undefined),user=userEvent.setup();
 const navigation:ProjectNavigation={state:{projects:[],recent:[],current_conversation_id:null,new_chat:false},onNew:vi.fn(),onOpen:vi.fn(),onUpdate:vi.fn(),onAdd:vi.fn(),browse:vi.fn(),getArchives:vi.fn(async()=>({projects:[
  {id:'p',name:'Current project',path:'/p',archived:false,available:true,conversations:[{id:'family',session_id:'a',title:'归档任务',project_id:'p',storage_project_id:'p'}]},
  {id:'q',name:'Other project',path:'/q',archived:false,available:true,conversations:[{id:'family',session_id:'a',title:'其他项目同ID',project_id:'q',storage_project_id:'q'}]},
 ],unassigned:[]})),restoreArchivedChat:vi.fn(async()=>{}),updateProject:vi.fn(async()=>{})};
 await renderSettings(store,navigation);
 vi.mocked(navigation.getArchives!).mockResolvedValue({projects:[{id:'q',name:'Other project',path:'/q',archived:false,available:true,conversations:[{id:'family',session_id:'a',title:'其他项目同ID',project_id:'q',storage_project_id:'q'}]}],unassigned:[]});
 await user.click(await screen.findByRole('button',{name:'删除「归档任务」'}));
 expect(screen.queryByRole('button',{name:'删除「其他项目同ID」'})).toBeNull();
 expect(screen.getByText(/永久删除.*无法恢复/)).toBeTruthy();
 expect(del).not.toHaveBeenCalled();
 await user.click(screen.getByRole('button',{name:'删除'}));
 await waitFor(()=>expect(del).toHaveBeenCalledWith('a'));
 await waitFor(()=>expect(screen.queryByText('归档任务')).toBeNull());
 expect(screen.getByText('其他项目同ID')).toBeTruthy();
});
