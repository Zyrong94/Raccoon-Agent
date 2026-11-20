// @vitest-environment jsdom
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {WorkbenchContext} from '../workbench/context';
import {MessageVersions} from './message-versions';
import {WorkbenchStore,type Transport} from '@/lib/workbench/client';
import {snapshot} from '@/lib/workbench/fixtures';
import type {ServerEvent,Snapshot} from '@/lib/workbench/types';
afterEach(cleanup);

async function setup(patch:Partial<Snapshot>={}){
 let receive!:(event:ServerEvent)=>void;
 const request=vi.fn<Transport['request']>(async()=>({token:'token'}));
 const store=new WorkbenchStore({request,subscribe:fn=>{receive=fn;return()=>{};},retry:()=>()=>{}});
 await store.start();receive({kind:'snapshot',state:snapshot({history_revision:'rev',versions:{current:'s',items:[{session_id:'s',label:'原始版本',operation:'original'},{session_id:'v2',label:'编辑版本',operation:'edit'}]},...patch})});
 render(<WorkbenchContext.Provider value={{blocked:false,modelControl:null,headerExtra:null,notice:null,pendingSlot:null,workbench:{projectId:'p',sessionId:'s',store}}}><MessageVersions/></WorkbenchContext.Provider>);
 return {store,request};
}


it('steps to a persisted conversation version without issuing a model request',async()=>{
 const {request}=await setup();
 await userEvent.setup().click(screen.getByRole('button',{name:'下一个版本'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/messages/version',expect.objectContaining({target_session_id:'v2',project_id:'p',session_id:'s'}),'token','POST',undefined));
 expect(request.mock.calls.some(call=>call[0]==='/messages/regenerate')).toBe(false);
});

it('offers an explicit retry when a failure has no assistant message to attach controls to',async()=>{
 const {request}=await setup({status:'error',messages:[{id:'u',role:'user',content:'需要重试'}]});
 await userEvent.setup().click(screen.getByRole('button',{name:'重试回答'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/messages/regenerate',expect.objectContaining({message_id:'u',project_id:'p',session_id:'s'}),'token','POST',undefined));
});

it('never exposes project version controls to the personal session',()=>{
 render(<WorkbenchContext.Provider value={{blocked:false,modelControl:null,headerExtra:null,notice:null,pendingSlot:null}}><MessageVersions/></WorkbenchContext.Provider>);
 expect(screen.queryByRole('combobox')).toBeNull();expect(screen.queryByRole('button')).toBeNull();
});

it('disables version stepping and explicit retry for an archived project',async()=>{
 await setup({project:{id:'p',name:'Project',path:'/project',archived:true},status:'error',messages:[{id:'u',role:'user',content:'saved'}]});
 expect(screen.getByRole('button',{name:'下一个版本'}).hasAttribute('disabled')).toBe(true);
 expect(screen.getByRole('button',{name:'重试回答'}).hasAttribute('disabled')).toBe(true);
});

it('disables the stepper at the ends of the version list',async()=>{
 await setup();
 expect(screen.getByRole('button',{name:'上一个版本'}).hasAttribute('disabled')).toBe(true);
 expect(screen.getByRole('button',{name:'下一个版本'}).hasAttribute('disabled')).toBe(false);
});
