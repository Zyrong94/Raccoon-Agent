// @vitest-environment jsdom
import {cleanup,render,screen,within,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {WorkbenchContext,type ProjectNavigation} from './context';
import {ArchivedProjects} from './archived-projects';
import type {ArchiveSnapshot} from '@/lib/workbench/types';

afterEach(cleanup);
const archive:ArchiveSnapshot={projects:[
 {id:'p',name:'Whole project',path:'C:/whole',archived:true,available:true,conversations:[{id:'a',session_id:'av',title:'same title',project_id:'p',storage_project_id:'p'}]},
 {id:'q',name:'Active project',path:'C:/active',archived:false,available:true,conversations:[{id:'b',session_id:'bv',title:'same title',project_id:'q',storage_project_id:'q'}]},
],unassigned:[{id:'legacy',session_id:'legacy',title:'Old chat',project_id:null,storage_project_id:'p'}]};
function setup(data=archive){
 const navigation:ProjectNavigation={state:{projects:[],recent:[],new_chat:false,current_conversation_id:null},onNew:vi.fn(),onOpen:vi.fn(),onAdd:vi.fn(),onUpdate:vi.fn(),browse:vi.fn(),getArchives:vi.fn(async()=>data),updateProject:vi.fn(async()=>{}),restoreArchivedChat:vi.fn(async()=>{})};
 render(<WorkbenchContext.Provider value={{navigation,blocked:false,modelControl:null,headerExtra:null,notice:null,pendingSlot:null}}><ArchivedProjects navigation={navigation}/></WorkbenchContext.Provider>);
 return {navigation,user:userEvent.setup()};
}
it('keeps project groups and truly unassigned chats separate with scoped restore actions',async()=>{
 const {navigation,user}=setup();
 const whole=await screen.findByRole('region',{name:'Whole project 归档'});
 const active=screen.getByRole('region',{name:'Active project 归档'});
 const unassigned=screen.getByRole('region',{name:'无项目归属的聊天'});
 expect(within(whole).getByText('same title')).toBeTruthy();
 expect(within(active).getByText('same title')).toBeTruthy();
 expect(within(unassigned).queryByText('same title')).toBeNull();
 await user.click(within(whole).getByRole('button',{name:'恢复项目'}));
 await waitFor(()=>expect(navigation.updateProject).toHaveBeenCalledWith('p',false));
 await user.click(within(active).getByRole('button',{name:'取消归档'}));
 await waitFor(()=>expect(navigation.restoreArchivedChat).toHaveBeenCalledWith('q','b'));
 await user.click(within(unassigned).getByRole('button',{name:'取消归档'}));
 await waitFor(()=>expect(navigation.restoreArchivedChat).toHaveBeenCalledWith('p','legacy'));
});
it('searches project names and titles and leaves the unassigned bucket empty when appropriate',async()=>{
 const {user}=setup({...archive,unassigned:[]});
 await screen.findByText('Whole project');
 expect(screen.queryByRole('region',{name:'无项目归属的聊天'})).toBeNull();
 await user.type(screen.getByRole('searchbox',{name:'搜索已归档的聊天'}),'Active');
 expect(screen.getByText('Active project')).toBeTruthy();
 expect(screen.queryByText('Whole project')).toBeNull();
});
it('shows restore failure and allows retry without hiding the project',async()=>{
 const {user,navigation}=setup();
 vi.mocked(navigation.updateProject!).mockRejectedValueOnce(new Error('registry unavailable'));
 const group=await screen.findByRole('region',{name:'Whole project 归档'});
 await user.click(within(group).getByRole('button',{name:'恢复项目'}));
 expect(await screen.findByRole('alert')).toHaveProperty('textContent','registry unavailable');
 expect(screen.getByText('Whole project')).toBeTruthy();
 await user.click(within(group).getByRole('button',{name:'恢复项目'}));
 await waitFor(()=>expect(navigation.updateProject).toHaveBeenCalledTimes(2));
});
