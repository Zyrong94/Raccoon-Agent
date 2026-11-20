import { describe, expect, it, vi } from 'vitest';
import { createMessageMapper, mapMessages } from './messages';
import { reduceEvent } from '../workbench/state';
import type { Snapshot } from '../workbench/types';

import {snapshot} from '../workbench/fixtures';

it('retains historical turns and avoids reading tool arguments again on live deltas',()=>{
 const serialize=vi.fn(()=>({path:'old.txt'}));const map=createMessageMapper();
 const initial=snapshot({busy:true,messages:[
  {id:'u1',role:'user',content:'历史问题'},
  {id:'a1',role:'assistant',content:'',calls:[{id:'read',name:'view',arguments:{toJSON:serialize}}]},
  {id:'result',role:'tool',tool_call_id:'read',content:'旧文件',ok:true},
  {id:'answer',role:'assistant',content:'历史回答'},
  {id:'u2',role:'user',content:'现在的问题'},
 ],live:[{content:'新',reasoning:''}]});
 const first=map(initial);expect(serialize).toHaveBeenCalledTimes(1);
 const second=map({...initial,live:[{content:'新内容',reasoning:''}]});
 expect(second.slice(0,3)).toEqual(first.slice(0,3));
 second.slice(0,3).forEach((message,index)=>expect(message).toBe(first[index]));
 expect(second[3]).not.toBe(first[3]);expect(second[3].content).toEqual([{type:'text',text:'新内容'}]);
 expect(serialize).toHaveBeenCalledTimes(1);
 const revised=map({...initial,live:[],revisable_message_ids:['u2']});
 expect(revised[0].metadata?.custom?.canRevise).toBe(false);
 expect(revised[1].metadata?.custom?.revisionTarget).toBe('u1');
 expect(revised[2].metadata?.custom?.canRevise).toBe(true);
});
describe('server message projection', () => {
  it('keeps JSON tool results as text, including retired weather tools',()=>{
    const result=mapMessages(snapshot({messages:[{id:'a',role:'assistant',content:'',calls:[{id:'w',name:'get_weather',arguments:{}},{id:'r',name:'read',arguments:{}}]},{id:'tw',role:'tool',tool_call_id:'w',content:'{"success":true,"id":"weather1","widget":{}}'},{id:'tr',role:'tool',tool_call_id:'r',content:'{"code":1}'}]}));
    expect(result[0].content).toEqual(expect.arrayContaining([expect.objectContaining({toolName:'get_weather',result:'{"success":true,"id":"weather1","widget":{}}'}),expect.objectContaining({toolName:'read',result:'{"code":1}'})]));
  });
  it('pairs repeated tool names only by call id and keeps historical ids', () => {
    const result = mapMessages(snapshot({messages: [
      {id:'a',role:'assistant',content:'先检查',calls:[{id:'one',name:'read',arguments:{path:'a'}},{id:'two',name:'read',arguments:{path:'b'}}]},
      {id:'r2',role:'tool',tool_call_id:'two',name:'read',content:'B',ok:false},
      {id:'r1',role:'tool',tool_call_id:'one',name:'read',content:'A',ok:true},
      {id:'legacy',role:'tool',name:'read',content:'旧结果',ok:true},
      {id:'final',role:'assistant',content:'完成'},
    ]}));
    expect(result[0].id).toBe('a');
    expect(result[0].content).toEqual(expect.arrayContaining([
      expect.objectContaining({toolCallId:'one',args:{path:'a'},result:'A'}),
      expect.objectContaining({toolCallId:'two',args:{path:'b'},result:'B',isError:true}),
    ]));
    expect(JSON.stringify(result[0])).toContain('参数未知');
    expect(result[0].content).toEqual(expect.arrayContaining([{type:'text',text:'完成'}]));
  });
  it('projects web_search results onto the following answer as source parts', () => {
    const result = mapMessages(snapshot({messages: [
      {id:'a',role:'assistant',content:'',calls:[{id:'s1',name:'web_search',arguments:{query:'宁德天气'}}]},
      {id:'r',role:'tool',tool_call_id:'s1',name:'web_search',content:'[宁德天气](https://weather.com.cn/ningde) 多云',ok:true},
      {id:'final',role:'assistant',content:'今天多云，见 https://weather.com.cn/ningde 。'},
    ]}));
    expect(result.at(-1)?.content).toEqual(expect.arrayContaining([
      expect.objectContaining({type:'source',sourceType:'url',url:'https://weather.com.cn/ningde',title:'宁德天气'}),
    ]));
    expect(result).toHaveLength(1);
  });
  it('drops search results the answer never cites', () => {
    const result = mapMessages(snapshot({messages: [
      {id:'a',role:'assistant',content:'',calls:[{id:'s1',name:'web_search',arguments:{query:'宁德天气'}}]},
      {id:'r',role:'tool',tool_call_id:'s1',name:'web_search',content:'[宁德天气](https://weather.com.cn/ningde) 多云',ok:true},
      {id:'final',role:'assistant',content:'今天多云。'},
    ]}));
    expect(result.at(-1)?.content).toEqual(expect.arrayContaining([{type:'text',text:'今天多云。'}]));
    expect(JSON.stringify(result)).not.toContain('"type":"source"');
  });
  it('does not complete a running tool merely because content is empty', () => {
    const result=mapMessages(snapshot({busy:true,activity:[{id:'c',name:'shell',kind:'started',content:'',ok:null}]}));
    expect(result[0].content).toEqual([expect.objectContaining({toolCallId:'c',argsText:expect.any(String)})]);
    expect(result[0].content[0]).not.toHaveProperty('result');
  });
  it('replaces live content with authoritative history without duplication', () => {
    const running=snapshot({busy:true,live:[{content:'途中',reasoning:'想法'}],activity:[{id:'c',name:'read',kind:'started'}]});
    const final=reduceEvent(running,{kind:'snapshot',state:snapshot({messages:[{id:'done',role:'assistant',content:'完成'}]})});
    expect(mapMessages(final!)).toEqual([expect.objectContaining({id:'done',content:[{type:'text',text:'完成'}]})]);
  });

});

it('joins assistant steps across backend messages while retaining text and user boundaries',()=>{
  const messages=mapMessages(snapshot({messages:[
    {id:'u',role:'user',content:'检查'},
    {id:'a',role:'assistant',content:'',calls:[{id:'c1',name:'view',arguments:{path:'a'}}]},
    {id:'r1',role:'tool',tool_call_id:'c1',content:'A',ok:true},
    {id:'b',role:'assistant',content:'',calls:[{id:'c2',name:'view',arguments:{path:'b'}}]},
    {id:'r2',role:'tool',tool_call_id:'c2',content:'B',ok:false},
    {id:'c',role:'assistant',content:'还需要检查',calls:[{id:'c3',name:'ls',arguments:{}}]},
    {id:'u2',role:'user',content:'继续'},
    {id:'d',role:'assistant',content:'',calls:[{id:'c4',name:'ls',arguments:{}}]},
  ]}));
  expect(messages.map(m=>m.role)).toEqual(['user','assistant','user','assistant']);
  expect(messages[1].id).toBe('a');
  expect(messages[1].content).toEqual([
    expect.objectContaining({toolCallId:'c1',result:'A'}),
    expect.objectContaining({toolCallId:'c2',result:'B',isError:true}),
    {type:'text',text:'还需要检查'},
    expect.objectContaining({toolCallId:'c3'}),
  ]);
});

it('keeps committed tool calls and reply text in order through completion and reconnect',()=>{
  const scope={project_id:'p',session_id:'s'};
  let state=snapshot({busy:true});
  const apply=(event:Parameters<typeof reduceEvent>[1])=>{state=reduceEvent(state,event)!;};
  const first={id:'one',name:'view',arguments:{path:'a'}};
  const second={id:'two',name:'view',arguments:{path:'b'}};
  const third={id:'three',name:'ls',arguments:{}};
  apply({kind:'message_committed',...scope,messages:[{id:'step',role:'assistant',content:'',calls:[first,second]}]});
  apply({kind:'tool',...scope,event:{...first,kind:'started'}});
  apply({kind:'message_committed',...scope,messages:[{id:'result',role:'tool',tool_call_id:first.id,content:'A',ok:true}]});
  apply({kind:'message_committed',...scope,messages:[{id:'next',role:'assistant',content:'继续检查',calls:[third]}]});
  apply({kind:'run_state',...scope,settled:true,state:{busy:false,cancellable:false,status:'completed',pending:null,error:null}});
  expect(mapMessages(state)[0].content).toEqual([
    expect.objectContaining({toolCallId:'one',result:'A'}),
    expect.objectContaining({toolCallId:'two'}),
    {type:'text',text:'继续检查'},
    expect.objectContaining({toolCallId:'three'}),
  ]);
  const reconnected=reduceEvent(null,{kind:'snapshot',state:JSON.parse(JSON.stringify(state))})!;
  expect(mapMessages(reconnected)).toEqual(mapMessages(state));
});
