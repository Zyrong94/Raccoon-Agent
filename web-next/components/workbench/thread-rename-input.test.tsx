// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {ThreadRenameInput} from './thread-rename-input';
afterEach(cleanup);
it('saves trimmed titles and preserves drafts after a failed save',async()=>{
 const rename=vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined);const done=vi.fn();
 render(<ThreadRenameInput title="旧名称" onRename={rename} onDone={done}/>);
 const input=screen.getByRole('textbox',{name:'重命名会话'});
 fireEvent.change(input,{target:{value:' 新名称 '}});fireEvent.submit(input.closest('form')!);
 await waitFor(()=>expect(screen.getByRole('alert')).toBeTruthy());
 expect((input as HTMLInputElement).value).toBe(' 新名称 ');expect(done).not.toHaveBeenCalled();
 fireEvent.submit(input.closest('form')!);await waitFor(()=>expect(done).toHaveBeenCalledWith(true));
 expect(rename.mock.calls).toEqual([['新名称'],['新名称']]);
});
it('rejects blank/too-long names, and Escape cancels without a mutation',()=>{
 const rename=vi.fn();const done=vi.fn();render(<ThreadRenameInput title="旧名称" onRename={rename} onDone={done}/>);
 const input=screen.getByRole('textbox');
 for(const value of ['   ','长'.repeat(201)]){fireEvent.change(input,{target:{value}});fireEvent.submit(input.closest('form')!);expect(screen.getByRole('alert')).toBeTruthy();}
 fireEvent.keyDown(input,{key:'Escape'});expect(done).toHaveBeenCalledWith(true);expect(rename).not.toHaveBeenCalled();
});
it('does not submit while the input method is composing',()=>{
 const rename=vi.fn();render(<ThreadRenameInput title="旧" onRename={rename} onDone={vi.fn()}/>);
 const input=screen.getByRole('textbox');fireEvent.change(input,{target:{value:'中文'}});
 fireEvent.keyDown(input,{key:'Enter',isComposing:true,keyCode:229});expect(rename).not.toHaveBeenCalled();
});
