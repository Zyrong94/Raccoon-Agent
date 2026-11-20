// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {AssistantRuntimeProvider,useExternalStoreRuntime,type ThreadMessageLike} from '@assistant-ui/react';
import {Suggestions} from './suggestions';
afterEach(()=>{cleanup();document.documentElement.classList.remove('dark');});
it('changes theme locally and fills suggestions without sending or moving the composer',async()=>{
 const send=vi.fn();let runtime:ReturnType<typeof useExternalStoreRuntime>;
 function Harness(){runtime=useExternalStoreRuntime({messages:[] as ThreadMessageLike[],convertMessage:message=>message,onNew:send});return <AssistantRuntimeProvider runtime={runtime}><Suggestions/></AssistantRuntimeProvider>;}
 render(<Harness/>);
 fireEvent.click(screen.getByRole('button',{name:'切换深色模式'}));
 expect(document.documentElement.classList.contains('dark')).toBe(true);
 expect(runtime!.thread.composer.getState().text).toBe('');
 fireEvent.click(screen.getByRole('button',{name:'了解项目结构'}));
 await waitFor(()=>expect(runtime!.thread.composer.getState().text).toBe('介绍一下这个项目的结构'));
 expect(send).not.toHaveBeenCalled();
 expect(screen.getByRole('button',{name:'了解项目结构',hidden:true}).parentElement?.getAttribute('inert')).not.toBeNull();
});
