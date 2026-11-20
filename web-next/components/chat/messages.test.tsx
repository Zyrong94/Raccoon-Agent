// @vitest-environment jsdom
import {cleanup,render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it} from 'vitest';
import {AssistantRuntimeProvider,ThreadPrimitive,useExternalStoreRuntime} from '@assistant-ui/react';
import {mapMessages} from '@/lib/chat/messages';
import {snapshot} from '@/lib/workbench/fixtures';
import {AssistantMessage,UserMessage} from './messages';
import type {Snapshot} from '@/lib/workbench/types';

class ResizeObserverStub{observe(){}unobserve(){}disconnect(){}}
globalThis.ResizeObserver??=ResizeObserverStub as unknown as typeof ResizeObserver;
afterEach(cleanup);

function StatusHarness({state}:{state:Snapshot}){
  const runtime=useExternalStoreRuntime({messages:mapMessages(state),convertMessage:m=>m,isRunning:state.busy,onNew:async()=>{}});
  return <AssistantRuntimeProvider runtime={runtime}><ThreadPrimitive.Messages components={{AssistantMessage,UserMessage}}/></AssistantRuntimeProvider>;
}

it('restores sent attachment previews and a visible same-origin filename download link',()=>{
 render(<StatusHarness state={snapshot({messages:[{id:'u',role:'user',content:'',attachments:[
  {id:'image',kind:'image',name:'screenshot.png',content_type:'image/png',size:4,content_url:'/api/attachments/image/content?project_id=p&session_id=s',download_url:'/api/attachments/image/download?project_id=p&session_id=s'},
  {id:'file',kind:'file',name:'notes.txt',content_type:'text/plain',size:5,content_url:'/api/attachments/file/content?project_id=p&session_id=s',download_url:'/api/attachments/file/download?project_id=p&session_id=s'},
 ]}]})}/>);
 expect(screen.getByRole('button',{name:'Preview screenshot.png'})).toBeTruthy();
 const download=screen.getByRole('link',{name:'notes.txt'});
 expect(download.getAttribute('href')).toBe('/api/attachments/file/download?project_id=p&session_id=s');
 expect(download.getAttribute('download')).toBe('notes.txt');
 expect(screen.queryByRole('button',{name:'Remove notes.txt'})).toBeNull();
});

it.each(['https://untrusted.example/file','//untrusted.example/file','javascript:alert(1)','/api/attachments/../../outside'])('does not render unsafe saved attachment URLs: %s',url=>{
 render(<StatusHarness state={snapshot({messages:[{id:'u',role:'user',content:'',attachments:[
  {id:'bad',kind:'file',name:'untrusted.txt',content_type:'text/plain',size:5,content_url:url,download_url:url},
 ]}]})}/>);
 expect(screen.queryByRole('link')).toBeNull();
 expect(screen.getByText('untrusted.txt')).toBeTruthy();
});

it.each([
  {status:'waiting',pending:{id:'p',kind:'permission_confirmation',question:'批准？',options:[],payload:{}},label:'waiting 2 tools'},
  {status:'interrupted',pending:null,label:'stopped 2 tools'},
])('shows $status before opening an unresolved tool group',({status,pending,label})=>{
  render(<StatusHarness state={snapshot({status,pending,messages:[
    {id:'a',role:'assistant',content:'',calls:[{id:'one',name:'view',arguments:{}}]},
    {id:'r',role:'tool',tool_call_id:'one',content:'ok',ok:true},
    {id:'b',role:'assistant',content:'',calls:[{id:'two',name:'shell',arguments:{command:'npm test'}}]},
  ]})}/>);
  expect(screen.getByRole('button',{name:label})).toBeTruthy();
});

it('keeps the streamed reply visible and marks it stopped after a cancel',()=>{
  render(<StatusHarness state={snapshot({status:'interrupted',messages:[
    {id:'u',role:'user',content:'写一篇长文'},
    {id:'a',role:'assistant',content:'已经写了一半的'},
  ]})}/>);
  expect(screen.getByText('已经写了一半的')).toBeTruthy();
  expect(screen.getByText('已停止生成')).toBeTruthy();
});

it('does not mark a finished reply as stopped',()=>{
  render(<StatusHarness state={snapshot({messages:[
    {id:'u',role:'user',content:'问'},
    {id:'a',role:'assistant',content:'答'},
  ]})}/>);
  expect(screen.getByText('答')).toBeTruthy();
  expect(screen.queryByText('已停止生成')).toBeNull();
});

it('exposes a failed tool in the collapsed summary even when a later tool succeeds',()=>{
  render(<StatusHarness state={snapshot({messages:[
    {id:'a',role:'assistant',content:'',calls:[{id:'one',name:'fetch',arguments:{}},{id:'two',name:'view',arguments:{}}]},
    {id:'r1',role:'tool',tool_call_id:'one',content:'failed',ok:false},
    {id:'r2',role:'tool',tool_call_id:'two',content:'ok',ok:true},
  ]})}/>);
  expect(screen.getByRole('button',{name:'ran 2 tools · 1 failed'})).toBeTruthy();
});

it('collapses tools across assistant steps and reasoning, splitting at normal text and user replies',async()=>{
  const state=snapshot({messages:[
    {id:'u',role:'user',content:'检查'},
    {id:'a',role:'assistant',content:'',calls:[{id:'one',name:'view',arguments:{path:'a'}}]},
    {id:'r1',role:'tool',tool_call_id:'one',content:'A',ok:true},
    {id:'b',role:'assistant',content:'',calls:[{id:'two',name:'view',arguments:{path:'b'}}]},
    {id:'r2',role:'tool',tool_call_id:'two',content:'B',ok:true},
    {id:'c',role:'assistant',content:'接下来检查目录',calls:[{id:'three',name:'ls',arguments:{path:'.'}}]},
    {id:'r3',role:'tool',tool_call_id:'three',content:'files',ok:true},
    {id:'d',role:'assistant',content:'',calls:[{id:'four',name:'tree',arguments:{path:'.'}}]},
    {id:'r4',role:'tool',tool_call_id:'four',content:'tree',ok:true},
    {id:'u2',role:'user',content:'再检查一次'},
  ],live:[{reasoning:'检查权限',content:'',order:0}],activity:[
    {id:'five',name:'git_status',kind:'completed',ok:true,content:'clean',order:1},
    {id:'six',name:'git_diff',kind:'completed',ok:true,content:'',order:3},
  ]});
  state.live.push({reasoning:'再确认',content:'',order:2});
  function Harness(){
    const runtime=useExternalStoreRuntime({messages:mapMessages(state),convertMessage:m=>m,onNew:async()=>{}});
    return <AssistantRuntimeProvider runtime={runtime}><ThreadPrimitive.Messages components={{AssistantMessage,UserMessage}}/></AssistantRuntimeProvider>;
  }
  render(<Harness/>);
  const groups=screen.getAllByRole('button',{name:'ran 2 tools'});
  expect(groups).toHaveLength(3);
  expect(screen.queryByText('读取文件')).toBeNull();
  await userEvent.click(groups[0]);
  expect(screen.getAllByText('读取文件')).toHaveLength(2);
  expect(screen.getByText('接下来检查目录')).toBeTruthy();
  expect(screen.queryByText('查看目录')).toBeNull();
  await userEvent.click(groups[2]);
  expect(screen.getByText('查看 Git 状态')).toBeTruthy();
  expect(screen.getAllByRole('button',{name:'reasoning'}).length).toBeGreaterThan(0);
});
