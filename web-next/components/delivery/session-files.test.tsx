// @vitest-environment jsdom
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {AuiProvider,AuiConfig} from '@assistant-ui/react';
import {getFileHref} from '@/components/assistant-ui/elements/file';
import {CanvasWorkspace} from './canvas-workspace';
import {ArtifactPreview} from './artifact-preview';
import type {Artifact} from '@/lib/workbench/types';

const file=(id:string,suffix='.txt'):Artifact=>({id,name:`${id}${suffix}`,suffix,size:512,mime:'text/plain',content_url:`/api/projects/0123456789abcdef/sessions/history-b/artifacts/${id}/content?v=1`,download_url:`/api/projects/0123456789abcdef/sessions/history-b/artifacts/${id}/download`});
const preview=(artifact:Artifact)=><AuiProvider config={AuiConfig({})}><ArtifactPreview artifact={artifact}/></AuiProvider>;
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();});

it('opens each non-Canvas file from the content card and returns to the updated list',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>({ok:true,text:async()=>url.includes('/notes/')?'hello world':'second file'})));const user=userEvent.setup();
 const text=file('notes'),second=file('second');const {rerender}=render(<AuiProvider config={AuiConfig({})}><CanvasWorkspace artifacts={[text,second]}><p>聊天</p></CanvasWorkspace></AuiProvider>);
 expect(screen.getByRole('region',{name:'会话文件'}).className).toContain('hidden');
 expect(screen.queryByRole('button',{name:/会话文件/})).toBeNull();
 await user.click(screen.getByRole('button',{name:'打开文件：notes.txt'}));
 expect(await screen.findByText('hello world')).toBeTruthy();
 expect(screen.getByText('512 B')).toBeTruthy();
 expect(screen.getByRole('link',{name:'下载文件'}).getAttribute('href')).toBe(text.download_url);
 await user.click(screen.getByRole('button',{name:'展开会话内容'}));
 await user.click(screen.getByRole('button',{name:'打开文件：second.txt'}));
 expect(await screen.findByText('second file')).toBeTruthy();expect(screen.queryByText('hello world')).toBeNull();
 expect(screen.getByRole('link',{name:'下载文件'}).getAttribute('href')).toBe(second.download_url);
 await user.click(screen.getByRole('button',{name:'返回文件列表'}));
 expect(screen.getByRole('link',{name:'下载 notes.txt'}).getAttribute('href')).toBe(text.download_url);
 rerender(<AuiProvider config={AuiConfig({})}><CanvasWorkspace artifacts={[]}><p>聊天</p></CanvasWorkspace></AuiProvider>);
 expect(screen.getByText('这个会话还没有生成文件')).toBeTruthy();
 expect(screen.queryByText('notes.txt')).toBeNull();
});

it('falls back to the list when the selected file disappears',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,text:async()=> 'hello'}));const user=userEvent.setup();
 const {rerender}=render(<AuiProvider config={AuiConfig({})}><CanvasWorkspace artifacts={[file('a')]}><p>聊天</p></CanvasWorkspace></AuiProvider>);
 await user.click(screen.getByRole('button',{name:'打开文件：a.txt'}));
 await screen.findByText('hello');
 rerender(<AuiProvider config={AuiConfig({})}><CanvasWorkspace artifacts={[file('b')]}><p>聊天</p></CanvasWorkspace></AuiProvider>);
 expect(await screen.findByRole('button',{name:'打开 b.txt'})).toBeTruthy();
 expect(screen.queryByText('hello')).toBeNull();
});

it('renders Markdown and ignores obsolete reads after a file revision changes',async()=>{
 let finish!:(r:unknown)=>void;
 vi.stubGlobal('fetch',vi.fn().mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;})).mockResolvedValueOnce({ok:true,text:async()=> '# 最新报告'}));
 const doc=file('report','.md');const {rerender}=render(preview(doc));
 rerender(preview({...doc,content_url:doc.content_url.replace('v=1','v=2')}));
 expect(await screen.findByRole('heading',{name:'最新报告'})).toBeTruthy();
 finish({ok:true,text:async()=> '# 旧报告'});
 await waitFor(()=>expect(screen.queryByRole('heading',{name:'旧报告'})).toBeNull());
});

it('reports missing/oversized text and uses the official image preview',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:false}));
 const {rerender}=render(preview(file('missing')));
 expect(await screen.findByRole('alert')).toBeTruthy();
 rerender(preview({...file('large'),size:3*1024*1024}));
 expect(await screen.findByText('文件较大，请下载后查看')).toBeTruthy();
 rerender(preview({...file('photo','.png'),mime:'image/png'}));
 expect(screen.getByRole('img',{name:'photo.png'}).getAttribute('src')).toContain('/api/projects/0123456789abcdef/sessions/history-b/artifacts/photo/content');
 fireEvent.error(screen.getByRole('img',{name:'photo.png'}));
 expect(await screen.findByText('图片无法读取，请刷新或下载后查看')).toBeTruthy();
});

it('pages PDF in its owning history session and releases blob URLs',async()=>{
 const create=vi.fn().mockReturnValueOnce('blob:page1').mockReturnValueOnce('blob:page2');const revoke=vi.fn();
 vi.stubGlobal('URL',class extends URL{static createObjectURL=create;static revokeObjectURL=revoke;});
 const fetcher=vi.fn().mockResolvedValue({ok:true,headers:new Headers({'X-PDF-Pages':'2'}),blob:async()=>new Blob(['image'])});vi.stubGlobal('fetch',fetcher);
 const {unmount}=render(preview({...file('report','.pdf'),mime:'application/pdf'}));const user=userEvent.setup();
 await screen.findByRole('img',{name:'PDF 第 1 页'});
 expect(fetcher.mock.calls[0][0]).toBe('/api/projects/0123456789abcdef/sessions/history-b/artifacts/report/pdf-page/0?v=1');
 expect(screen.getByRole('button',{name:'PDF 上一页'}).hasAttribute('disabled')).toBe(true);
 await user.click(screen.getByRole('button',{name:'PDF 下一页'}));
 await screen.findByRole('img',{name:'PDF 第 2 页'});
 expect(fetcher.mock.calls[1][0]).toBe('/api/projects/0123456789abcdef/sessions/history-b/artifacts/report/pdf-page/1?v=1');
 expect(revoke).toHaveBeenCalledWith('blob:page1');
 expect(screen.getByRole('button',{name:'PDF 下一页'}).hasAttribute('disabled')).toBe(true);
 unmount();expect(revoke).toHaveBeenCalledWith('blob:page2');
});

it('lets the user return to a previous PDF page when a later page fails',async()=>{
 vi.stubGlobal('URL',class extends URL{static createObjectURL=()=> 'blob:page';static revokeObjectURL=vi.fn();});
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce({ok:true,headers:new Headers({'X-PDF-Pages':'2'}),blob:async()=>new Blob(['image'])}).mockResolvedValueOnce({ok:false}));
 render(preview({...file('report','.pdf'),mime:'application/pdf'}));const user=userEvent.setup();
 await screen.findByRole('img',{name:'PDF 第 1 页'});
 await user.click(screen.getByRole('button',{name:'PDF 下一页'}));
 await screen.findByRole('alert');
 expect(screen.getByRole('button',{name:'PDF 上一页'}).hasAttribute('disabled')).toBe(false);
});

it.each([['.mp3','audio/mpeg','audio'],['.mp4','video/mp4','video']])('previews %s through the assistant-ui player without autoplay',(_suffix,mime,tag)=>{
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 const play=vi.spyOn(HTMLMediaElement.prototype,'play').mockResolvedValue();
 const media={...file('recording',_suffix),mime};const {container}=render(preview(media));
 const element=container.querySelector(tag) as HTMLMediaElement;
 expect(container.querySelector('[data-slot="file-player"]')).toBeTruthy();
 expect(container.querySelector(`[data-slot="${tag}-player"]`)).toBeTruthy();
 expect(element.getAttribute('src')).toBe(media.content_url);
 expect(element.autoplay).toBe(false);expect(element.preload).toBe('metadata');
 if(tag==='audio')expect(screen.getByRole('button',{name:`Play ${media.name}`})).toBeTruthy();
 else expect(element.controls).toBe(true);
 expect(play).not.toHaveBeenCalled();expect(fetcher).not.toHaveBeenCalled();
});

it.each([
 ['.docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
 ['.pptx','application/vnd.openxmlformats-officedocument.presentationml.presentation'],
 ['.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
 ['.zip','application/zip'],
])('offers file information and download for %s without fetching binary content',(suffix,mime)=>{
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);
 const binary={...file('report',suffix),mime};render(preview(binary));
 expect(screen.getByText(binary.name)).toBeTruthy();expect(screen.getByText('512 B')).toBeTruthy();
 expect(screen.getByText('此文件暂不支持在线预览，请下载后查看')).toBeTruthy();
 const download=screen.getByRole('link',{name:`下载 ${binary.name}`});
 expect(download.getAttribute('href')).toBe(binary.download_url);
 expect(download.getAttribute('download')).toBe(binary.name);
 expect(fetcher).not.toHaveBeenCalled();
});

it('accepts only safe root-relative file URLs in addition to existing absolute URLs',()=>{
 expect(getFileHref('/api/projects/0123456789abcdef/sessions/history-b/artifacts/file/content?v=1','audio/mpeg','url')).toBe('/api/projects/0123456789abcdef/sessions/history-b/artifacts/file/content?v=1');
 for(const value of ['//example.com/file','/\\example.com/file','/\n/example.com/file','relative/file','javascript:alert(1)','file:///secret']){
  expect(getFileHref(value,'audio/mpeg','url')).toBeNull();
 }
});

