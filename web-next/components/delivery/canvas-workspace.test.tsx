// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {CanvasWorkspace} from './canvas-workspace';
import {reduceEvent} from '@/lib/workbench/state';
import {snapshot} from '@/lib/workbench/fixtures';

const artifact={id:'html',name:'chart.html',suffix:'.html',size:100,content_url:'/api/projects/0123456789abcdef/sessions/history-b/artifacts/html/content',canvas_url:'/api/projects/0123456789abcdef/sessions/history-b/artifacts/html/canvas/chart.html',download_url:'/api/projects/0123456789abcdef/sessions/history-b/artifacts/html/download',mime:'text/html'};
const canvas={artifact_id:'html',title:'交互图表',request_id:'open-1',revision:'r1'};
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
const setupFetch=()=>vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,text:async()=>'<h1>Chart</h1>',body:{cancel:async()=>{}}}));

it('keeps artifacts and canvas together and ignores another session event',()=>{
 const event={kind:'artifacts' as const,project_id:'p',session_id:'s',artifacts:[artifact],canvas};
 const result=reduceEvent(snapshot(),event);
 expect(result?.artifacts).toEqual([artifact]);
 expect(result?.canvas).toEqual(canvas);
 expect(reduceEvent(result,{...event,session_id:'other',artifacts:[]})).toBe(result);
});

it('opens a sandboxed preview, stays closed on edits, reopens from the card and refreshes revision',async()=>{
 setupFetch();const user=userEvent.setup();
 const {rerender}=render(<CanvasWorkspace artifacts={[artifact]} canvas={canvas}><p>聊天内容</p></CanvasWorkspace>);
 expect(screen.queryByRole('complementary',{name:'会话内容'})).toBeNull();
 await user.click(screen.getByRole('button',{name:'展开会话内容'}));
 expect(screen.getByRole('complementary',{name:'会话内容'})).toBeTruthy();
 await user.click(screen.getByRole('button',{name:'收起会话内容'}));
 expect(screen.queryByRole('complementary',{name:'会话内容'})).toBeNull();
 const frame=await screen.findByTitle('交互图表');
 expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
 expect(frame.getAttribute('src')).toContain('r1');
 fireEvent.load(frame);
 await user.click(screen.getByRole('button',{name:'关闭画布'}));
 rerender(<CanvasWorkspace artifacts={[artifact]} canvas={{...canvas,revision:'r2'}}><p>聊天内容</p></CanvasWorkspace>);
 expect(screen.queryByTitle('交互图表')).toBeNull();
 await user.click(screen.getByRole('button',{name:'展开会话内容'}));
 await user.click(screen.getByRole('button',{name:'打开文件：chart.html'}));
 expect(screen.queryByRole('complementary',{name:'会话内容'})).toBeNull();
 expect((await screen.findByTitle('交互图表')).getAttribute('src')).toContain('r2');
 const beforeRefresh=screen.getByTitle('交互图表').getAttribute('src');
 await user.click(screen.getByRole('button',{name:'刷新画布'}));
 await waitFor(()=>expect(screen.getByTitle('交互图表').getAttribute('src')).not.toBe(beforeRefresh));
 await user.click(screen.getByRole('tab',{name:'源码'}));
 expect(await screen.findByText('<h1>Chart</h1>')).toBeTruthy();
 expect(screen.getByRole('link',{name:'下载文件'}).getAttribute('href')).toBe(artifact.download_url);
});

it('removes the old preview on session switch and resets dismissal on returning',async()=>{
 setupFetch();const user=userEvent.setup();
 const {rerender}=render(<CanvasWorkspace key="session-a" artifacts={[artifact]} canvas={canvas}><p>会话 A</p></CanvasWorkspace>);
 await screen.findByTitle('交互图表');
 await user.click(screen.getByRole('button',{name:'关闭画布'}));
 rerender(<CanvasWorkspace key="session-b" artifacts={[]} canvas={null}><p>会话 B</p></CanvasWorkspace>);
 expect(screen.queryByRole('button',{name:'打开文件：chart.html'})).toBeNull();
 expect(screen.queryByTitle('交互图表')).toBeNull();
 expect(screen.getByRole('complementary',{name:'会话内容'})).toBeTruthy();
 expect(screen.getByText('暂无输出文件')).toBeTruthy();expect(screen.getByText('暂无子智能体')).toBeTruthy();
 rerender(<CanvasWorkspace key="session-a" artifacts={[artifact]} canvas={canvas}><p>会话 A</p></CanvasWorkspace>);
 expect(await screen.findByTitle('交互图表')).toBeTruthy();
});

it('opens a new canvas request after dismissal and reports missing files',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:false}));const user=userEvent.setup();
 const {rerender}=render(<CanvasWorkspace artifacts={[artifact]} canvas={canvas}><p>聊天</p></CanvasWorkspace>);
 expect(await screen.findByRole('alert')).toBeTruthy();
 await user.click(screen.getByRole('button',{name:'关闭画布'}));
 rerender(<CanvasWorkspace artifacts={[artifact]} canvas={{...canvas,request_id:'open-2'}}><p>聊天</p></CanvasWorkspace>);
 await waitFor(()=>expect(screen.getByRole('button',{name:'关闭画布'})).toBeTruthy());
});
