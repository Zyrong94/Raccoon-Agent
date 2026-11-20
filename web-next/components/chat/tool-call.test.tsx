// @vitest-environment jsdom
import {cleanup,render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import type {ToolCallMessagePartProps} from '@assistant-ui/react';
import {ToolCall} from './tool-call';

// jsdom has no ResizeObserver; SwapLabel only measures its label width with it.
class ResizeObserverStub{observe(){}unobserve(){}disconnect(){}}
globalThis.ResizeObserver??=ResizeObserverStub as unknown as typeof ResizeObserver;

const baseProps={
 toolCallId:'call-1',
 args:{},
 argsText:'{}',
 addResult:vi.fn(),
 resume:vi.fn(),
 respondToApproval:vi.fn(async()=>{}),
} as unknown as ToolCallMessagePartProps;

afterEach(cleanup);

it('renders a completed tool call with the official label, query badge and expandable result',async()=>{
 const user=userEvent.setup();
 render(<ToolCall {...baseProps} toolName="grep" args={{pattern:'resume'}} argsText={'{"pattern":"resume"}'} result="2 matches" status={{type:'complete'}}/>);
 expect(screen.getByText('搜索内容')).toBeTruthy();
 expect(screen.getByText('resume')).toBeTruthy();
 await user.click(screen.getByRole('button'));
 expect(screen.getByText('参数')).toBeTruthy();
 expect(screen.getByText('结果')).toBeTruthy();
 expect(screen.getByText('2 matches')).toBeTruthy();
});

it('pretty prints JSON parameters and result envelopes without dropping metadata',async()=>{
 const result={content:[{type:'text',text:'line one\nline two'}],structuredContent:{count:2},isError:false};
 render(<ToolCall {...baseProps} toolName="view" args={{path:'a'}} argsText={'{"path":"a"}'} result={result} status={{type:'complete'}}/>);
 await userEvent.click(screen.getByRole('button'));
 const blocks=document.querySelectorAll('pre');
 expect(blocks[0].textContent).toBe('{\n  "path": "a"\n}');
 expect(blocks[1].textContent).toBe(JSON.stringify(result,null,2));
});

it.each(['{"count":2,"items":["a","b"]}','first line\nsecond line','{"truncated":'])('renders received text safely: %s',async(result)=>{
 render(<ToolCall {...baseProps} toolName="view" result={result} status={{type:'complete'}}/>);
 await userEvent.click(screen.getByRole('button'));
 const expected=result.startsWith('{"count"')?'{\n  "count": 2,\n  "items": [\n    "a",\n    "b"\n  ]\n}':result;
 expect(document.querySelectorAll('pre')[1].textContent).toBe(expected);
});

it('keeps failed tool parameters and error details in an expandable error trace',async()=>{
 render(<ToolCall {...baseProps} toolName="fetch" args={{url:'https://example.com'}} result="network failed\nretry later" isError status={{type:'complete'}}/>);
 await userEvent.click(screen.getByRole('button'));
 expect(document.querySelector('pre')?.textContent).toContain('"url": "https://example.com"');
 expect(screen.getAllByText(/network failed/).length).toBeGreaterThan(0);
 expect(document.querySelector('[data-tool-error="true"]')).toBeTruthy();
});

it('formats JSON without changing numeric tokens, duplicate keys or escaped string contents',async()=>{
 const result='{"id":9007199254740993,"ratio":0.12345678901234567890,"big":1e400,"key":1,"key":2,"text":"a, \\"b\\": []"}';
 render(<ToolCall {...baseProps} toolName="view" result={result} status={{type:'complete'}}/>);
 await userEvent.click(screen.getByRole('button'));
 expect(document.querySelectorAll('pre')[1].textContent).toBe('{\n  "id": 9007199254740993,\n  "ratio": 0.12345678901234567890,\n  "big": 1e400,\n  "key": 1,\n  "key": 2,\n  "text": "a, \\"b\\": []"\n}');
});

it('shows the running label and hides the completion check while a tool executes',()=>{
 render(<ToolCall {...baseProps} toolName="shell" args={{command:'pytest -q'}} argsText={'{"command":"pytest -q"}'} status={{type:'running'}}/>);
 expect(screen.getByText('运行命令中')).toBeTruthy();
 expect(screen.getByText('pytest -q')).toBeTruthy();
 expect(document.querySelector('.lucide-check')).toBeNull();
});

it('humanizes unknown MCP tool names and picks a readable argument for the badge',()=>{
 render(<ToolCall {...baseProps} toolName="mcp_playwright_browser_navigate" args={{url:'https://example.com/docs'}} argsText={'{"url":"https://example.com/docs"}'} result="ok" status={{type:'complete'}}/>);
 expect(screen.getByText('MCP · playwright browser navigate')).toBeTruthy();
 expect(screen.getByText('https://example.com/docs')).toBeTruthy();
});
