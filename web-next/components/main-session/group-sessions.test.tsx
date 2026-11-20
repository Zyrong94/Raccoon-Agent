// @vitest-environment jsdom
import {act,cleanup,render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {GroupSession,GroupSessionsSection} from './group-sessions';
import {GroupSessionsStore} from '@/lib/group-sessions';

afterEach(()=>{cleanup();});

const key=(letter:string)=>letter.repeat(64);
const setup=()=>{
 const request=vi.fn(async(path:string)=>path==='/group-sessions'
  ?{groups:[{id:key('a'),title:'Raccoon 用户群'},{id:key('b'),title:'测试群'}]}
  :{available:true,session:{id:'session-1',title:'Raccoon 用户群'},messages:[
   {id:'question',role:'user',content:'群里问了个问题'},
   {id:'answer',role:'assistant',content:'收到，正在处理。'},
  ]});
 return {request,store:new GroupSessionsStore(request)};
};

it('replaces the chat area with the group transcript and switches groups in place',async()=>{
 const {store}=setup();const user=userEvent.setup();
 render(<GroupSession onClose={()=>{}} store={store}/>);
 expect(await screen.findByRole('heading',{name:'Raccoon 用户群'})).toBeTruthy();
 expect(await screen.findByText('群里问了个问题')).toBeTruthy();
 expect(screen.getByText('群成员')).toBeTruthy();
 expect(screen.getByText('Agent')).toBeTruthy();
 expect(screen.queryByRole('textbox')).toBeNull();
 await act(async()=>{await user.selectOptions(screen.getByRole('combobox',{name:'切换群聊'}),key('b'));});
 expect(await screen.findByRole('heading',{name:'测试群'})).toBeTruthy();
});

it('shows an empty state when the account has no QQ groups yet',async()=>{
 const store=new GroupSessionsStore(vi.fn(async()=>({groups:[]})));
 render(<GroupSession onClose={()=>{}} store={store}/>);
 expect(await screen.findByRole('heading',{name:'还没有 QQ 群聊'})).toBeTruthy();
 expect(screen.queryByRole('combobox',{name:'切换群聊'})).toBeNull();
});

it('lists groups in the main session overlay and opens the whole group window',async()=>{
 const {store}=setup();const onOpen=vi.fn();const user=userEvent.setup();
 render(<GroupSessionsSection store={store} onOpen={onOpen}/>);
 await user.click(await screen.findByRole('button',{name:'查看群聊记录：测试群'}));
 expect(onOpen).toHaveBeenCalledTimes(1);
 expect(store.get().selected).toBe(key('b'));
});

it('returns to the main session from the header',async()=>{
 const {store}=setup();const onClose=vi.fn();const user=userEvent.setup();
 render(<GroupSession onClose={onClose} store={store}/>);
 await screen.findByText('群里问了个问题');
 await user.click(screen.getByRole('button',{name:'返回主会话'}));
 expect(onClose).toHaveBeenCalledTimes(1);
});
