// @vitest-environment jsdom
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {WorkbenchContext,type WorkbenchConfig} from './context';
import {ProjectPicker,OpenProjectDialog} from './project-picker';
afterEach(cleanup);
it('browse opens the system picker and lists its selection without opening the project',async()=>{
 const value=config();const pickDirectory=vi.fn(async()=>'/selected');
 value.navigation={...value.navigation!,pickDirectory};
 render(<WorkbenchContext.Provider value={value}><OpenProjectDialog open onOpenChange={()=>{}}/></WorkbenchContext.Provider>);
 await screen.findByRole('button',{name:'project'});
 await userEvent.setup().click(screen.getByRole('button',{name:'浏览'}));
 await waitFor(()=>expect((screen.getByRole('textbox',{name:'项目目录'}) as HTMLInputElement).value).toBe('/selected'));
 expect(pickDirectory).toHaveBeenCalledWith('/root',expect.any(AbortSignal));
 expect(value.navigation.browse).toHaveBeenLastCalledWith('/selected');
 expect(value.navigation.onAdd).not.toHaveBeenCalled();
});
it('system cancellation preserves the typed path and existing directory listing',async()=>{
 const value=config();value.navigation={...value.navigation!,pickDirectory:vi.fn(async()=>null)};
 render(<WorkbenchContext.Provider value={value}><OpenProjectDialog open onOpenChange={()=>{}}/></WorkbenchContext.Provider>);
 await screen.findByRole('button',{name:'project'});const user=userEvent.setup();const input=screen.getByRole('textbox',{name:'项目目录'});
 await user.clear(input);await user.type(input,'/typed');await user.click(screen.getByRole('button',{name:'浏览'}));
 await waitFor(()=>expect(screen.queryByRole('status')).toBeNull());
 expect((input as HTMLInputElement).value).toBe('/typed');
 expect(screen.getByRole('button',{name:'project'})).toBeTruthy();
 expect(value.navigation.browse).toHaveBeenCalledTimes(1);
});
it('Enter locates a typed path without launching the system picker',async()=>{
 const value=config();const pickDirectory=vi.fn(async()=>null);value.navigation={...value.navigation!,pickDirectory};
 render(<WorkbenchContext.Provider value={value}><OpenProjectDialog open onOpenChange={()=>{}}/></WorkbenchContext.Provider>);
 await screen.findByRole('button',{name:'project'});const user=userEvent.setup();const input=screen.getByRole('textbox',{name:'项目目录'});
 await user.clear(input);await user.type(input,'/typed{Enter}');
 await waitFor(()=>expect(value.navigation!.browse).toHaveBeenLastCalledWith('/typed'));
 expect(pickDirectory).not.toHaveBeenCalled();
});
it('system selection prevents repeat clicks and shows retryable failures',async()=>{
 const value=config();let fail!:(error:Error)=>void;const pickDirectory=vi.fn(()=>new Promise<string|null>((_,reject)=>{fail=reject;}));
 value.navigation={...value.navigation!,pickDirectory};
 render(<WorkbenchContext.Provider value={value}><OpenProjectDialog open onOpenChange={()=>{}}/></WorkbenchContext.Provider>);
 await screen.findByRole('button',{name:'project'});const user=userEvent.setup();const button=screen.getByRole('button',{name:'浏览'});
 await user.click(button);expect((button as HTMLButtonElement).disabled).toBe(true);
 expect(screen.getByRole('status').textContent).toContain('系统');
 expect((screen.getByRole('textbox',{name:'项目目录'}) as HTMLInputElement).disabled).toBe(true);
 await user.click(button);expect(pickDirectory).toHaveBeenCalledTimes(1);
 fail(new Error('系统窗口不可用'));await screen.findByRole('alert');
 expect(screen.getByRole('alert').textContent).toContain('系统窗口不可用');
 expect((button as HTMLButtonElement).disabled).toBe(false);
});
it('aborts native selection on close and ignores its late result',async()=>{
 const value=config();let select!:(path:string)=>void;
 let signal:AbortSignal|undefined;
 value.navigation={...value.navigation!,pickDirectory:vi.fn((_path?:string,pickSignal?:AbortSignal)=>{signal=pickSignal;return new Promise<string|null>(resolve=>{select=resolve;});})};
 const {rerender}=render(<WorkbenchContext.Provider value={value}><OpenProjectDialog open onOpenChange={()=>{}}/></WorkbenchContext.Provider>);
 await screen.findByRole('button',{name:'project'});await userEvent.setup().click(screen.getByRole('button',{name:'浏览'}));
 rerender(<WorkbenchContext.Provider value={value}><OpenProjectDialog open={false} onOpenChange={()=>{}}/></WorkbenchContext.Provider>);
 expect(signal?.aborted).toBe(true);
 select('/late');await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
 expect(value.navigation.browse).toHaveBeenCalledTimes(1);
});
function config(newChat=true):WorkbenchConfig{return {blocked:false,modelControl:null,headerExtra:null,notice:null,pendingSlot:null,workbench:{projectId:'p',sessionId:'s',store:{} as never},navigation:{state:{new_chat:newChat,current_conversation_id:null,recent:[],projects:[{id:'p',name:'Raccoon',path:'/p',available:true,conversations:[]},{id:'q',name:'paper-radar',path:'/q',available:true,conversations:[]}]},onNew:vi.fn(async()=>{}),onOpen:vi.fn(async()=>{}),onUpdate:vi.fn(async()=>{}),onAdd:vi.fn(async()=>{}),browse:vi.fn(async(path?:string)=>({path:path??'/root',parent:null,directories:path?[]:[{name:'project',path:'/root/project'}]}))}};}
it('only shows project selection before the first accepted message and preserves a picker draft',async()=>{
 const value=config();const {rerender}=render(<WorkbenchContext.Provider value={value}><ProjectPicker/></WorkbenchContext.Provider>);
 await userEvent.setup().click(screen.getByRole('button',{name:/选择项目/}));
 await userEvent.setup().click(screen.getByRole('button',{name:'paper-radar'}));
 expect(value.navigation!.onNew).toHaveBeenCalledWith('q',true);
 rerender(<WorkbenchContext.Provider value={{...value,navigation:{...value.navigation!,state:{...value.navigation!.state,new_chat:false}}}}><ProjectPicker/></WorkbenchContext.Provider>);
 expect(screen.queryByRole('button',{name:/选择项目/})).toBeNull();
});
it('uses registered directory browsing, opens a fresh project chat and keeps errors retryable',async()=>{
 const value=config();vi.mocked(value.navigation!.browse).mockRejectedValueOnce(new Error('目录不可访问'));
 render(<WorkbenchContext.Provider value={value}><OpenProjectDialog open onOpenChange={()=>{}}/></WorkbenchContext.Provider>);
 await waitFor(()=>expect(screen.getByRole('alert').textContent).toContain('目录不可访问'));
 await userEvent.setup().type(screen.getByRole('textbox',{name:'项目目录'}),'{Enter}');
 await userEvent.setup().click(await screen.findByRole('button',{name:'project'}));
 await userEvent.setup().click(screen.getByRole('button',{name:'打开项目'}));
 await waitFor(()=>expect(value.navigation!.onAdd).toHaveBeenCalledWith('/root/project'));
});
it('does not show project controls in the personal session',()=>{
 const value=config();delete value.navigation;delete value.workbench;
 render(<WorkbenchContext.Provider value={value}><ProjectPicker/></WorkbenchContext.Provider>);
 expect(screen.queryByRole('button')).toBeNull();
});

it('retains selection after HTTP rejection and hides the complete row after acceptance even when generation fails',()=>{
 const value=config();const {rerender}=render(<WorkbenchContext.Provider value={value}><ProjectPicker/></WorkbenchContext.Provider>);
 expect(screen.getByRole('button',{name:/选择项目/})).toBeTruthy();
 // A rejected send leaves new_chat true; model errors after acceptance leave it false.
 rerender(<WorkbenchContext.Provider value={{...value,navigation:{...value.navigation!,state:{...value.navigation!.state,new_chat:true}}}}><ProjectPicker/></WorkbenchContext.Provider>);
 expect(screen.getByRole('button',{name:/选择项目/})).toBeTruthy();
 rerender(<WorkbenchContext.Provider value={{...value,navigation:{...value.navigation!,state:{...value.navigation!.state,new_chat:false}}}}><ProjectPicker/></WorkbenchContext.Provider>);
 expect(screen.queryByText('Raccoon')).toBeNull();
});

it('selects no-project while preserving the draft and uses the same plain menu row',async()=>{
 const value=config();render(<WorkbenchContext.Provider value={value}><ProjectPicker/></WorkbenchContext.Provider>);
 const user=userEvent.setup();await user.click(screen.getByRole('button',{name:/选择项目/}));
 const neutral=screen.getByRole('button',{name:'无项目'});
 expect(neutral.className).toBe(screen.getByRole('button',{name:'paper-radar'}).className);
 await user.click(neutral);expect(value.navigation!.onNew).toHaveBeenCalledWith(undefined,true);
 expect(screen.queryByRole('button',{name:'清除项目选择'})).toBeNull();
});
it('displays and checks no-project without exposing the internal workspace, including during search',async()=>{
 const value=config();value.workbench={...value.workbench!,projectId:'neutral'};
 value.navigation!.state.projects.push({id:'neutral',name:'无项目',path:'/neutral',available:true,projectless:true,conversations:[]});
 render(<WorkbenchContext.Provider value={value}><ProjectPicker/></WorkbenchContext.Provider>);
 const user=userEvent.setup();await user.click(screen.getByRole('button',{name:'选择项目：无项目'}));
 await user.type(screen.getByRole('textbox',{name:'搜索项目'}),'missing');
 const rows=screen.getAllByRole('button',{name:'无项目'});expect(rows).toHaveLength(1);
 expect(rows[0].querySelector('.lucide-check')).toBeTruthy();
 await user.click(rows[0]);expect(value.navigation!.onNew).not.toHaveBeenCalled();
});
it('disables no-project selection when blocked',async()=>{
 const value=config();const {rerender}=render(<WorkbenchContext.Provider value={value}><ProjectPicker/></WorkbenchContext.Provider>);
 await userEvent.setup().click(screen.getByRole('button',{name:/选择项目/}));
 rerender(<WorkbenchContext.Provider value={{...value,blocked:true}}><ProjectPicker/></WorkbenchContext.Provider>);
 expect((screen.getByRole('button',{name:'无项目'}) as HTMLButtonElement).disabled).toBe(true);
});
