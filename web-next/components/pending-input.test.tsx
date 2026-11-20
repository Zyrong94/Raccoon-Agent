// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {PendingInput} from './pending-input';
afterEach(cleanup);
it('uses every backend option and preserves free text after a failed submit',async()=>{
  const answer=vi.fn().mockRejectedValueOnce(new Error('失败')).mockResolvedValue(undefined);
  render(<PendingInput pending={{id:'q',kind:'ask_user',question:'选择或填写',options:[{id:'a',label:'选项甲'},{id:'b',label:'选项乙'}],payload:{}}} disabled={false} answer={answer}/>);
  fireEvent.click(screen.getByText('选项甲'));fireEvent.click(screen.getByText('发送回答'));
  await waitFor(()=>expect(answer).toHaveBeenCalledWith('q','a'));
  fireEvent.change(screen.getByLabelText('自由回答'),{target:{value:'我的回答'}});
  fireEvent.click(screen.getByText('发送回答'));fireEvent.click(screen.getByText('发送回答'));
  await waitFor(()=>expect(answer).toHaveBeenCalledWith('q','我的回答'));
  expect(answer).toHaveBeenCalledTimes(2);
});
it('renders the compact official card for the three permission decisions',async()=>{
  const answer=vi.fn().mockResolvedValue(undefined);
  const options=[{id:'once',label:'本次允许'},{id:'always',label:'始终允许此命令范围'},{id:'deny',label:'拒绝'}];
  render(<PendingInput pending={{id:'p',kind:'permission_confirmation',question:'允许运行此命令?',options,payload:{},presentation:{title:'允许运行此命令?',target:'pnpm vitest run',description:'执行 shell 命令需要用户确认。',details:'完整命令详情',options}}} disabled={false} answer={answer}/>);
  const buttons=screen.getAllByRole('button');
  expect(buttons.map(button=>button.textContent)).toEqual(['拒绝','始终允许此命令范围','本次允许']);
  fireEvent.click(screen.getByText('本次允许'));
  await waitFor(()=>expect(answer).toHaveBeenCalledWith('p','once'));
});
it('renders full approval details/diff and arbitrary option counts without fixed grants',()=>{
  render(<PendingInput pending={{id:'p',kind:'permission_confirmation',question:'批准？',options:Array.from({length:5},(_,i)=>({id:String(i),label:`决定${i}`})),payload:{prewrite_review:{files:[{path:'a.txt',diff:'@@ -1 +1 @@\n-old\n+new'}]}},presentation:{title:'写文件',target:'a.txt',description:'修改',details:'完整命令详情',options:Array.from({length:5},(_,i)=>({id:String(i),label:`决定${i}`}))}}} disabled={false} answer={vi.fn()}/>);
  expect(screen.getAllByRole('button')).toHaveLength(5);expect(screen.getByText('完整命令详情')).toBeTruthy();expect(screen.getByText('new')).toBeTruthy();
});

it('keeps the backend review label instead of calling it a permission grant',()=>{
 const options=[{id:'deny',label:'拒绝'},{id:'allow_once',label:'应用修改'}];
 render(<PendingInput pending={{id:'review',kind:'permission_confirmation',question:'检查修改',options,payload:{}}} disabled={false} answer={vi.fn()}/>);
 expect(screen.getByRole('button',{name:'应用修改'})).toBeTruthy();
 expect(screen.queryByRole('button',{name:'本次允许'})).toBeNull();
});
