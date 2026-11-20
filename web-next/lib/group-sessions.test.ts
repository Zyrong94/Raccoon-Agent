import {describe,expect,it,vi} from 'vitest';
import {GroupSessionsStore} from './group-sessions';

const key=(letter:string)=>letter.repeat(64);
const transcript=(text:string)=>({available:true,session:{id:'session-1',title:'Raccoon 用户群'},messages:[{id:'message-1',role:'user',content:text}]});

const list=()=>({groups:[{id:key('a'),title:'Raccoon 用户群'},{id:key('b'),title:'测试群'}]});

describe('QQ group transcript store',()=>{
 it('lists groups and opens the first transcript read only',async()=>{
  const request=vi.fn(async(path:string)=>path==='/group-sessions'?list():transcript('群里问了个问题'));
  const store=new GroupSessionsStore(request);
  await store.refresh();
  expect(store.get().groups.map(group=>group.title)).toEqual(['Raccoon 用户群','测试群']);
  expect(store.get().selected).toBe(key('a'));
  expect(store.get().snapshot?.messages[0].content).toBe('群里问了个问题');
  expect(store.get().error).toBe('');
  expect(request.mock.calls.map(call=>call[0])).toEqual(['/group-sessions',`/group-sessions/${key('a')}`]);
 });

 it('switches transcripts when another group is selected',async()=>{
  const request=vi.fn(async(path:string)=>path==='/group-sessions'?list():transcript(path.endsWith(key('b'))?'第二个群的记录':'第一个群的记录'));
  const store=new GroupSessionsStore(request);
  await store.refresh();
  store.select(key('b'));
  expect(store.get().snapshot).toBeNull();
  await vi.waitFor(()=>expect(store.get().snapshot?.messages[0].content).toBe('第二个群的记录'));
  expect(store.get().selected).toBe(key('b'));
 });

 it('drops a removed selection back to the first group',async()=>{
  let groups=list().groups;
  const request=vi.fn(async(path:string)=>path==='/group-sessions'?{groups}:transcript('记录'));
  const store=new GroupSessionsStore(request);
  await store.refresh();
  store.select(key('b'));
  await vi.waitFor(()=>expect(store.get().selected).toBe(key('b')));
  groups=groups.filter(group=>group.id!==key('b'));
  await store.refresh();
  expect(store.get().selected).toBe(key('a'));
 });

 it('reports read failures instead of throwing',async()=>{
  const request=vi.fn(async()=>{throw new Error('群聊记录暂时无法读取，请稍后重试');});
  const store=new GroupSessionsStore(request);
  await store.refresh();
  expect(store.get().groups).toEqual([]);
  expect(store.get().loading).toBe(false);
  expect(store.get().error).toBe('群聊记录暂时无法读取，请稍后重试');
 });
});
