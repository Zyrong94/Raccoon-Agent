// @vitest-environment jsdom
import {cleanup,render,screen,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {BackendModelPicker} from './model-picker';

beforeEach(()=>{
 vi.stubGlobal('ResizeObserver',class{observe(){}unobserve(){}disconnect(){}});
 Element.prototype.scrollIntoView=vi.fn();
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();});

const models=[
 {ref:'openai/gpt-6-luna',label:'GPT-6 Luna',supported_reasoning_efforts:['low','high']},
 {ref:'deepseek/deepseek-v4',label:'DeepSeek V4'},
];

it('shows the current model and lists every option with its provider',async()=>{
 const user=userEvent.setup();const change=vi.fn();
 render(<BackendModelPicker state={{model:'openai/gpt-6-luna',models}} blocked={false} onChange={change}/>);
 const trigger=screen.getByRole('combobox');
 expect(trigger.textContent).toContain('GPT-6 Luna');
 await user.click(trigger);
 const list=await screen.findByRole('listbox');
 const options=within(list).getAllByRole('option');
 expect(screen.queryByRole('radiogroup')).toBeNull();
 expect(options).toHaveLength(2);
 expect(options[0].textContent).toContain('GPT-6 Luna');
 expect(options[0].textContent).toContain('OpenAI');
 expect(options[1].textContent).toContain('DeepSeek V4');
 expect(options[1].textContent).toContain('DeepSeek');
 await user.click(options[1]);
 expect(change).toHaveBeenCalledWith('deepseek/deepseek-v4');
});

it('shows a backend id as a readable model name without changing the ref',async()=>{
 const user=userEvent.setup();const change=vi.fn();
 render(<BackendModelPicker state={{model:'custom/gpt-6-luna',models:[{ref:'custom/gpt-6-luna',label:'gpt-6-luna'}]}} blocked={false} onChange={change}/>);
 const trigger=screen.getByRole('combobox');
 expect(trigger.textContent).toBe('GPT-6 Luna');
 await user.click(trigger);
 expect((await screen.findByRole('option')).textContent).toContain('GPT-6 Luna');
});

it('blocks the picker when the session cannot accept changes',()=>{
 render(<BackendModelPicker state={{model:'openai/gpt-6-luna',models}} blocked onChange={vi.fn()}/>);
 expect((screen.getByRole('group',{name:'模型选择'}) as HTMLFieldSetElement).disabled).toBe(true);
});

it('falls back to a single disabled option when execution is not configured yet',()=>{
 render(<BackendModelPicker state={{model:null,models:[]}} blocked={false} onChange={vi.fn()}/>);
 expect(screen.getByRole('combobox').textContent).toContain('未配置模型');
 expect((screen.getByRole('group',{name:'模型选择'}) as HTMLFieldSetElement).disabled).toBe(true);
});
