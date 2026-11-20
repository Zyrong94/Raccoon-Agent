// @vitest-environment jsdom
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {WorkbenchContext,type SidebarNavigation} from './context';
import {ProjectConversationRow} from './project-conversation-row';
import type {ConversationSummary} from '@/lib/workbench/types';

afterEach(cleanup);

const conversation:ConversationSummary={id:'c1',title:'项目聊天',project_id:'p',session_id:'s1',pinned:false,archived:false};

function setup({active=false,blocked=false,pendingConversation=null,onPrewarm=vi.fn()}:{active?:boolean;blocked?:boolean;pendingConversation?:{projectId:string;conversationId:string}|null;onPrewarm?:(projectId:string,conversationId:string)=>void}={}){
 const navigation:SidebarNavigation={state:{projects:[],recent:[],current_conversation_id:null,new_chat:false},onNew:vi.fn(async()=>{}),onOpen:vi.fn(async()=>{}),onPrewarm,onUpdate:vi.fn(async()=>{}),onAdd:vi.fn(async()=>{}),browse:vi.fn(async()=>({path:'C:/',parent:null,directories:[]}))};
 render(<WorkbenchContext.Provider value={{navigation,blocked,navigationBlocked:blocked,pendingConversation,modelControl:null,headerExtra:null,notice:null,pendingSlot:null}}><ProjectConversationRow conversation={conversation} active={active}/></WorkbenchContext.Provider>);
 return {onPrewarm,user:userEvent.setup()};
}

it('marks the clicked conversation as current before the server confirms it',()=>{
 const {onPrewarm}=setup({pendingConversation:{projectId:'p',conversationId:'c1'}});
 const row=screen.getByRole('button',{name:'项目聊天'});
 expect(row.getAttribute('aria-current')).toBe('page');
 expect(row.getAttribute('aria-busy')).toBe('true');
 expect(onPrewarm).not.toHaveBeenCalled();
});

it('ignores a pending selection that belongs to another conversation',()=>{
 setup({pendingConversation:{projectId:'paper',conversationId:'c9'}});
 const row=screen.getByRole('button',{name:'项目聊天'});
 expect(row.getAttribute('aria-current')).toBeNull();
 expect(row.getAttribute('aria-busy')).toBeNull();
});

it('asks for the runtime once the pointer rests on the row',async()=>{
 const {onPrewarm,user}=setup();
 await user.hover(screen.getByRole('button',{name:'项目聊天'}));
 await waitFor(()=>expect(onPrewarm).toHaveBeenCalledWith('p','c1'));
});

it('skips prewarming the conversation that is already open',async()=>{
 const {onPrewarm,user}=setup({active:true});
 await user.hover(screen.getByRole('button',{name:'项目聊天'}));
 await new Promise(resolve=>setTimeout(resolve,300));
 expect(onPrewarm).not.toHaveBeenCalled();
});
