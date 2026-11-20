// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
import {SubagentStore,type SubagentRequest} from './subagents';

afterEach(()=>{vi.useRealTimers();Object.defineProperty(document,'hidden',{configurable:true,value:false});});
it('keeps one polling chain when visibility changes during an unresolved refresh',async()=>{
 vi.useFakeTimers();let release!:(value:unknown)=>void;
 const request=vi.fn().mockImplementationOnce(()=>new Promise(resolve=>{release=resolve;})).mockResolvedValue({subagents:[]}) as unknown as SubagentRequest;
 const store=new SubagentStore({project_id:'p',session_id:'s'},request);store.start();
 Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));
 Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));
 await vi.advanceTimersByTimeAsync(0);release({subagents:[]});await vi.advanceTimersByTimeAsync(0);
 expect(vi.getTimerCount()).toBe(1);expect(request).toHaveBeenCalledTimes(2);
 await vi.advanceTimersByTimeAsync(1000);expect(request).toHaveBeenCalledTimes(3);expect(vi.getTimerCount()).toBe(1);store.stop();
});
it('polls reads only while visible and aborts pending reads and writes on scope disposal',async()=>{
 vi.useFakeTimers();const signals:AbortSignal[]=[];
 const request=vi.fn(async(_scope, path,_body,signal)=>{if(signal)signals.push(signal);if(path.endsWith('/messages'))return await new Promise(()=>{});return {subagents:[]};}) as unknown as SubagentRequest;
 const store=new SubagentStore({project_id:'p',session_id:'s'},request);store.start();await vi.advanceTimersByTimeAsync(1000);
 expect(request).toHaveBeenCalledTimes(2);
 Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));await vi.advanceTimersByTimeAsync(2000);expect(request).toHaveBeenCalledTimes(2);
 Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));await vi.advanceTimersByTimeAsync(0);expect(request).toHaveBeenCalledTimes(3);
 void store.send('child','one','follow_up','event-1');store.stop();expect(signals.every(signal=>signal.aborted)).toBe(true);
 await expect(store.send('child','two','steer','event-2')).rejects.toThrow('父会话已切换');
});

it('does not retry uncertain sends and retains caller event identity for explicit retry',async()=>{
 const request=vi.fn(async(_scope,path,body)=>{if(path.endsWith('/messages'))throw new Error('结果未知');return {subagents:[]};}) as unknown as SubagentRequest;
 const store=new SubagentStore({session_id:'main',generation:'g'},request);store.start();
 await expect(store.send('c','review','follow_up','stable')).rejects.toThrow('结果未知');
 expect(vi.mocked(request).mock.calls.filter(call=>call[1].endsWith('/messages'))).toHaveLength(1);
 await expect(store.send('c','review','follow_up','stable')).rejects.toThrow();
 const sends=vi.mocked(request).mock.calls.filter(call=>call[1].endsWith('/messages'));expect(sends.map(call=>call[2])).toEqual([{message:'review',mode:'follow_up',event_id:'stable'},{message:'review',mode:'follow_up',event_id:'stable'}]);
 store.stop();
});
