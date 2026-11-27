// @vitest-environment jsdom
import {createRef} from 'react';
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,expect,it,vi} from 'vitest';
import {ConfigurationManager,type ConfigurationNavigationHandle} from './configuration-manager';
import {WorkbenchStore} from '@/lib/workbench/client';
import {configurationDraft,type ConfigurationListing,type ConfigurationRecord} from '@/lib/configuration';

afterEach(()=>{cleanup();vi.restoreAllMocks();});
const model:ConfigurationRecord={id:'local/test-model',revision:'r1',source:'managed',config:{provider_id:'local',api_key_env:'TEST_API_KEY',supported_reasoning_efforts:[],provider_label:'本地供应商',model_id:'test-model',label:'本地模型',default:true}};
const listing=(changes:Partial<ConfigurationListing>={}):ConfigurationListing=>({root:'isolated-test',models:[model],mcp:[],skills:[],subagents:[],options:{tools:[{name:'view',description:'读取文件'},{name:'delegate',description:'派发任务'}],skill_names:['research'],provider_types:['openai-responses']},...changes});
function setup(data=listing()){
 const store=new WorkbenchStore();
 const request=vi.spyOn(store,'configurationRequest').mockImplementation(async(path='',body)=>{if(!path||path.startsWith('?domains='))return data as never;if(path.endsWith('/import'))return {records:[model]} as never;if(path.endsWith('/save'))return {...model,config:(body as {config:object}).config} as never;return {ok:true,message:'校验通过'} as never;});
 const onClose=vi.fn(),ref=createRef<ConfigurationNavigationHandle>();
 render(<ConfigurationManager ref={ref} store={store} onClose={onClose}/>);
 return {store,request,onClose,ref,user:userEvent.setup()};
}
async function pickOption(user:ReturnType<typeof userEvent.setup>,label:string,name:RegExp){
 await user.click(screen.getByLabelText(label));
 await user.click(await screen.findByRole('option',{name}));
}

it('creates skills in the selected scope and defaults to Raccoon global',async()=>{
 const {user,request}=setup(listing({models:[]}));
 await user.click(screen.getByRole('tab',{name:'技能包'}));
 await user.click(await screen.findByRole('button',{name:/新建技能包/}));
 expect((screen.getByLabelText('保存位置') as HTMLSelectElement).value).toBe('global');
 await user.selectOptions(screen.getByLabelText('保存位置'),'project');
 await user.type(screen.getByLabelText('技能名称'),'review');
 await user.click(screen.getByRole('button',{name:'保存配置'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/skills/save',expect.objectContaining({scope:'project',config:{name:'review'}}),undefined));
});

it('shows shared skills as read only and distinguishes same-name project skills',async()=>{
 const shared:ConfigurationRecord={id:'review',revision:'shared-1',source:'shared',scope:'shared',read_only:true,overridden:true,path:'/home/.agents/skills/review/SKILL.md',config:{name:'review'},content:'Shared instructions'};
 const project:ConfigurationRecord={...shared,revision:'project-1',scope:'project',source:'project',read_only:false,overridden:false,path:'/project/.agents/skills/review/SKILL.md',content:'Project instructions'};
 const {user,request}=setup(listing({skills:[shared,project]}));
 await user.click(screen.getByRole('tab',{name:'技能包'}));
 await user.click(await screen.findByRole('button',{name:/review.*共享全局/}));
 expect(screen.queryByRole('button',{name:'编辑'})).toBeNull();
 expect(screen.queryByRole('button',{name:'删除'})).toBeNull();
 expect(screen.getByText(/来源：共享全局/).textContent).toContain(shared.path);
 await user.click(screen.getByRole('button',{name:/review.*当前项目/}));
 await user.click(screen.getByRole('button',{name:'删除'}));
 expect(screen.getByRole('dialog').textContent).toContain('低优先级技能会重新生效');
 await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'删除配置'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/skills/review',{scope:'project',revision:'project-1'},'DELETE'));
});

it('imports skill packages into the selected project scope',async()=>{
 const {user,request}=setup();
 await user.click(screen.getByRole('tab',{name:'技能包'}));
 await user.click(await screen.findByRole('button',{name:'本地导入'}));
 expect((screen.getByLabelText('导入位置') as HTMLSelectElement).value).toBe('global');
 await user.selectOptions(screen.getByLabelText('导入位置'),'project');
 await user.type(screen.getByLabelText('本地路径'),'/incoming/review');
 await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'导入'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/skills/import',{path:'/incoming/review',scope:'project'},undefined));
});

it('edits a provider connection and saves its models with the original revision',async()=>{
 const {user,request}=setup();
 await user.click(await screen.findByRole('button',{name:/本地供应商/}));
 expect(screen.getByText('1 个模型 · 默认 test-model',{selector:'p'})).toBeTruthy();
 expect(screen.getByText('test-model')).toBeTruthy();
 await user.click(screen.getByRole('button',{name:'编辑'}));
 expect((screen.getByLabelText('提供商标识') as HTMLInputElement).disabled).toBe(true);
 expect(screen.queryByLabelText('API 密钥')).toBeNull();
 expect(screen.queryByLabelText('温度')).toBeNull();
 expect(screen.queryByLabelText('最大输出 tokens')).toBeNull();
 await user.type(screen.getByLabelText('显示名称'),' 二号');
 await user.click(screen.getByRole('button',{name:'保存并应用'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/models/save',{id:model.id,revision:'r1',config:{...model.config,provider_label:'本地供应商 二号'}},undefined));
 expect(request.mock.calls.some(call=>call[0]==='/models/validate')).toBe(false);
 expect(screen.queryByDisplayValue('test-key')).toBeNull();
});

it('creates a provider with several models in one save flow',async()=>{
 expect(configurationDraft('models').config).not.toHaveProperty('credential_ref');
 const {user,request}=setup(listing({models:[]}));
 await user.click(await screen.findByRole('button',{name:/新建供应商/}));
 expect(screen.queryByLabelText('协议')).toBeNull();
 await user.type(screen.getByLabelText('显示名称'),'中转站');
 await user.type(screen.getByLabelText('提供商标识'),'relay');
 await user.type(screen.getByLabelText('API 地址'),'https://relay.test/v1');
 await user.type(screen.getByLabelText('密钥环境变量'),'TEST_API_KEY');
 await user.type(screen.getByLabelText('模型 ID'),'luna');
 await user.click(screen.getByRole('button',{name:'添加模型'}));
 const identifiers=screen.getAllByLabelText('模型 ID');
 expect(identifiers).toHaveLength(2);
 await user.type(identifiers[1]!,'nova');
 await user.click(screen.getByRole('button',{name:'保存并应用'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/models/save',expect.objectContaining({config:expect.objectContaining({provider_id:'relay',api_key_env:'TEST_API_KEY',supported_reasoning_efforts:[],model_id:'luna',provider_label:'中转站',base_url:'https://relay.test/v1',default:true})}),undefined));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/models/save',expect.objectContaining({config:expect.objectContaining({model_id:'nova',default:false})}),undefined));
 const saves=request.mock.calls.filter(call=>call[0]==='/models/save');
 expect(saves).toHaveLength(2);
 expect(saves[1]?.[1]).not.toHaveProperty('api_key');
});

it('preserves conflicted drafts and guards tabs and shell navigation',async()=>{
 const {user,request,onClose,ref}=setup();
 await user.click(await screen.findByRole('button',{name:/本地供应商/}));
 await user.click(screen.getByRole('button',{name:'编辑'}));
 await user.type(screen.getByLabelText('显示名称'),' 草稿');
 request.mockRejectedValueOnce(new Error('配置已在其他入口修改'));
 await user.click(screen.getByRole('button',{name:'保存并应用'}));
 expect(await screen.findByRole('alert')).toHaveProperty('textContent',expect.stringContaining('配置已在其他入口修改'));
 expect((screen.getByLabelText('显示名称') as HTMLInputElement).value).toBe('本地供应商 草稿');
 await user.click(screen.getByRole('tab',{name:'MCP'}));
 await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'取消'}));
 expect(screen.getByRole('tab',{name:'模型'}).getAttribute('aria-selected')).toBe('true');
 ref.current!.requestLeave(onClose);
 await user.click(within(await screen.findByRole('dialog')).getByRole('button',{name:'放弃修改'}));
 expect(onClose).toHaveBeenCalledTimes(1);
});

it('confirms removing a provider together with all of its models',async()=>{
 const {user,request}=setup();
 await user.click(await screen.findByRole('button',{name:/本地供应商/}));
 expect(screen.queryByText(/只读|原有配置|共享库/)).toBeNull();
 await user.click(screen.getByRole('button',{name:'删除供应商'}));
 expect(screen.getByRole('dialog').textContent).toContain('将同时删除这个供应商下的 1 个模型');
 expect(request.mock.calls.some(call=>call[2]==='DELETE')).toBe(false);
 await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'删除供应商'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/models/local%2Ftest-model',{revision:'r1'},'DELETE'));
});

it('imports pasted configuration through the records response',async()=>{
 const {user,request}=setup();
 await user.click(await screen.findByRole('button',{name:'本地导入'}));
 await user.selectOptions(screen.getByLabelText('导入方式'),'text');
 await user.selectOptions(screen.getByLabelText('配置格式'),'json');
 fireEvent.change(screen.getByLabelText('配置内容'),{target:{value:'{"models":{}}'}});
 await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'导入'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/models/import',{text:'{"models":{}}',format:'json'},undefined));
});

it.each([
 ['skills','技能包',{name:'research',description:'检索资料'},null,'技能名称'],
 ['subagents','子代理',{name:'review',model:'inherit',description:'审查代码'},'提示词试运行','角色名称'],
] as const)('edits personal %s records and offers testing only for runnable resources',async(domain,label,config,testLabel,nameLabel)=>{
 const record:ConfigurationRecord={id:String(config.name),revision:'existing-1',source:'managed',config,content:'# Existing resource'};
 const {user,request}=setup(listing({[domain]:[record]}));
 await user.click(screen.getByRole('tab',{name:label}));
 expect(await screen.findAllByRole('button',{name:new RegExp(String(config.name))})).toHaveLength(1);
 await user.click(screen.getByRole('button',{name:new RegExp(String(config.name))}));
 expect(screen.queryByRole('button',{name:'复制'})).toBeNull();
 expect(screen.queryByText('名称',{selector:'dt'})).toBeNull();
 if(domain==='skills')expect(screen.getByRole('tabpanel').querySelector('dl')).toBeNull();
 expect(screen.getByRole('button',{name:'删除'})).toBeTruthy();
 if(testLabel){
  expect(screen.getByRole('button',{name:'校验配置'})).toBeTruthy();
  await user.click(screen.getByRole('button',{name:testLabel}));
  await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'开始测试'}));
  await waitFor(()=>expect(request).toHaveBeenCalledWith(`/${domain}/test`,{id:record.id,revision:record.revision,...(domain==='subagents'?{prompt:'请简要说明你的职责。'}:{})},undefined));
 }else{
  expect(screen.queryByRole('button',{name:'校验配置'})).toBeNull();
  expect(screen.queryByRole('button',{name:'测试可用性'})).toBeNull();
 }
 await user.click(screen.getByRole('button',{name:'编辑'}));
 if(domain==='skills')expect(screen.queryByRole('button',{name:'校验配置'})).toBeNull();
 expect((screen.getByLabelText(nameLabel) as HTMLInputElement).disabled).toBe(true);
 await user.type(screen.getByLabelText(domain==='skills'?'SKILL.md':'SOUL.md'),' Updated');
 await user.click(screen.getByRole('button',{name:'保存配置'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith(`/${domain}/save`,expect.objectContaining({id:record.id,revision:record.revision}),undefined));
 expect(request.mock.calls.some(call=>call[0]===`/${domain}/import`)).toBe(false);
 if(domain==='skills')expect(request.mock.calls.some(call=>call[0]==='/skills/validate'||call[0]==='/skills/test')).toBe(false);
});

it('shows MCP servers as read-only with runtime status, origin and tools',async()=>{
 const record:ConfigurationRecord={id:'browser',revision:'existing-1',source:'managed',origin:'project',origin_path:'/project/raccoon.toml',config:{name:'browser',type:'local',command:['npx','browser'],enabled:true,timeout_ms:9000}};
 const {user}=setup(listing({mcp:[record],mcp_status:{servers:[{name:'browser',state:'connected',tool_count:2,error:null,tools:['open_page','close_page']}]}}));
 await user.click(screen.getByRole('tab',{name:'MCP'}));
 expect(screen.queryByRole('button',{name:/本地导入/})).toBeNull();
 expect(screen.queryByRole('button',{name:/新建/})).toBeNull();
 await user.click(await screen.findByRole('button',{name:/browser/}));
 const panel=screen.getByRole('tabpanel');
 expect(panel.textContent).toContain('已连接');
 expect(panel.textContent).toContain('open_page、close_page');
 expect(panel.textContent).toContain('项目 raccoon.toml');
 expect(screen.getByRole('button',{name:'复制为 TOML'})).toBeTruthy();
 for(const name of ['编辑','删除','校验配置','测试连接'])expect(screen.queryByRole('button',{name})).toBeNull();
});

it('uses only builtin subagents as templates for creating a personal copy',async()=>{
 const template:ConfigurationRecord={id:'review',revision:'builtin-1',source:'builtin',read_only:true,config:{name:'review',model:'inherit',description:'审查代码'},content:'Review carefully.'};
 const {user,request}=setup(listing({subagents:[template]}));
 await user.click(screen.getByRole('tab',{name:'子代理'}));
 await user.click(await screen.findByRole('button',{name:/review/}));
 expect((screen.getByRole('button',{name:'提示词试运行'}) as HTMLButtonElement).disabled).toBe(true);
 expect(screen.queryByRole('button',{name:'编辑'})).toBeNull();
 expect(screen.queryByRole('button',{name:'删除'})).toBeNull();
 await user.click(screen.getByRole('button',{name:'基于模板创建'}));
 expect((screen.getByLabelText('角色名称') as HTMLInputElement).disabled).toBe(false);
 expect((screen.getByLabelText('角色名称') as HTMLInputElement).value).toBe('');
 await user.clear(screen.getByLabelText('角色名称'));
 await user.type(screen.getByLabelText('角色名称'),'my-review');
 await user.click(screen.getByRole('button',{name:'保存配置'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/subagents/save',{config:{...template.config,name:'my-review'},content:template.content},undefined));
});

it('runs a subagent prompt only after confirmation and displays its bounded result',async()=>{
 const subagent:ConfigurationRecord={id:'review',source:'managed',revision:'agent1',config:{name:'review',model:'inherit',description:'审查代码',tools:null,skills:[]},content:'Review carefully.'};
 const {user,request}=setup(listing({subagents:[subagent]}));
 await user.click(screen.getByRole('tab',{name:'子代理'}));
 await user.click(await screen.findByRole('button',{name:/review/}));
 fireEvent.change(screen.getByLabelText('试运行提示'),{target:{value:'介绍职责'}});
 await user.click(screen.getByRole('button',{name:'提示词试运行'}));
 expect(request.mock.calls.some(call=>call[0]==='/subagents/test')).toBe(false);
 expect(screen.getByRole('dialog').textContent).toContain('不执行工具');
 request.mockResolvedValueOnce({ok:true,message:'未执行工具，未验证完整工具工作流',output:'我负责审查代码。'} as never);
 await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'开始测试'}));
 expect(await screen.findByText('我负责审查代码。')).toBeTruthy();
 expect(request).toHaveBeenCalledWith('/subagents/test',{id:'review',revision:'agent1',prompt:'介绍职责'},undefined);
});

it('probes the default model of a provider with a fixed minimal request',async()=>{
 const {user,request}=setup();
 await user.click(await screen.findByRole('button',{name:/本地供应商/}));
 expect(screen.queryByLabelText('测试提示')).toBeNull();
 await user.click(screen.getByRole('button',{name:'测试连接'}));
 expect(screen.getByRole('dialog').textContent).toContain('用 local/test-model 发送一次固定的最小请求');
 await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'开始测试'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/models/test',{id:model.id,revision:'r1'},undefined));
});

it('retains the provider draft on refresh when the stored revision changes',async()=>{
 const {user,request}=setup();
 await user.click(await screen.findByRole('button',{name:/本地供应商/}));
 await user.click(screen.getByRole('button',{name:'编辑'}));
 await user.type(screen.getByLabelText('显示名称'),' 草稿');
 request.mockResolvedValueOnce(listing({models:[{...model,revision:'r2'}]}) as never);
 await user.click(screen.getByRole('button',{name:'刷新配置'}));
 expect(await screen.findByText(/草稿已保留/)).toBeTruthy();
 expect((screen.getByLabelText('显示名称') as HTMLInputElement).value).toBe('本地供应商 草稿');
});


it('preserves inheritance and explicit empty tool scopes in subagent forms',async()=>{
 const {user,request}=setup(listing({models:[]}));
 await user.click(screen.getByRole('tab',{name:'子代理'}));
 await user.click(await screen.findByRole('button',{name:'新建子代理'}));
 await user.type(screen.getByLabelText('角色名称'),'review');
 await pickOption(user,'工具范围',/^全部禁用/);
 await pickOption(user,'技能范围',/^继承父代理技能/);
 await user.click(screen.getByRole('button',{name:'校验配置'}));
 expect(request.mock.calls.at(-1)?.[1]).not.toHaveProperty('config.model');
 expect(request.mock.calls.at(-1)?.[1]).toMatchObject({config:{tools:[],skills:null}});
 await pickOption(user,'工具范围',/^继承父代理工具/);
 await user.click(screen.getByRole('button',{name:'校验配置'}));
 expect(request.mock.calls.at(-1)?.[1]).toMatchObject({config:{tools:null}});
});

it('selects subagent tools from the registered catalogue instead of free text',async()=>{
 const subagent:ConfigurationRecord={id:'review',revision:'agent-1',source:'managed',config:{name:'review',model:'inherit',tools:['retired-tool'],skills:[]},content:'Review carefully.'};
 const {user,request}=setup(listing({models:[],subagents:[subagent]}));
 await user.click(screen.getByRole('tab',{name:'子代理'}));
 await user.click(await screen.findByRole('button',{name:/review/}));
 await user.click(screen.getByRole('button',{name:'编辑'}));
 const group=screen.getByRole('group',{name:'允许的工具'});
 expect(within(group).queryByRole('button',{name:'delegate'})).toBeNull();
 expect(within(group).getByRole('button',{name:'retired-tool'}).getAttribute('aria-pressed')).toBe('true');
 await user.click(within(group).getByRole('button',{name:'view'}));
 await user.click(screen.getByRole('button',{name:'校验配置'}));
 expect(request.mock.calls.at(-1)?.[1]).toMatchObject({config:{tools:['retired-tool','view']}});
 await user.click(within(group).getByRole('button',{name:'view'}));
 await user.click(within(group).getByRole('button',{name:'retired-tool'}));
 expect(screen.getByLabelText('工具范围').textContent).toContain('全部禁用');
});

it('searches and bulk-selects tools inside the compact picker',async()=>{
 const {user}=setup(listing({models:[],subagents:[],options:{tools:[{name:'view',description:'读取文件'},{name:'grep',description:'搜索内容'},{name:'delegate',description:'派发任务'}],skill_names:['research','web-report'],provider_types:['openai-responses']}}));
 await user.click(screen.getByRole('tab',{name:'子代理'}));
 await user.click(await screen.findByRole('button',{name:'新建子代理'}));
 const tools=screen.getByRole('group',{name:'允许的工具'});
 expect(within(tools).getByText('已选 0 / 2')).toBeTruthy();
 expect(within(tools).getByRole('button',{name:'grep'}).getAttribute('title')).toBe('搜索内容');
 await user.type(within(tools).getByLabelText('搜索工具'),'gre');
 expect(within(tools).queryByRole('button',{name:'view'})).toBeNull();
 await user.click(within(tools).getByRole('button',{name:'全选'}));
 expect(within(tools).getByText('已选 1 / 2')).toBeTruthy();
 expect(screen.getByLabelText('工具范围').textContent).toContain('仅允许所选项');
 await user.click(within(tools).getByRole('button',{name:'只看已选'}));
 expect(within(tools).queryByRole('button',{name:'view'})).toBeNull();
 await user.click(within(tools).getByRole('button',{name:'清空'}));
 expect(within(tools).getByText('还没有选择工具')).toBeTruthy();
 expect(screen.getByLabelText('工具范围').textContent).toContain('全部禁用');
});

it('authorizes each MCP server as one whole group',async()=>{
 const subagent:ConfigurationRecord={id:'review',revision:'agent-mcp',source:'managed',config:{name:'review',model:'inherit',tools:['mcp__playwright__browser_click'],skills:[]},content:'Review carefully.'};
 const {user,request}=setup(listing({models:[],subagents:[subagent],options:{tools:[{name:'view',description:'读取文件'},{name:'mcp__playwright__browser_click',description:'点击元素'},{name:'mcp__playwright__browser_navigate',description:'打开页面'},{name:'mcp__github__issue_read',description:'读取 issue'},{name:'delegate',description:'派发任务'}],skill_names:[],provider_types:['openai-responses']}}));
 await user.click(screen.getByRole('tab',{name:'子代理'}));
 await user.click(await screen.findByRole('button',{name:/review/}));
 await user.click(screen.getByRole('button',{name:'编辑'}));
 const tools=screen.getByRole('group',{name:'允许的工具'});
 expect(within(tools).queryByRole('button',{name:/browser_click/})).toBeNull();
 expect(within(tools).getByText('已选 1 / 4')).toBeTruthy();
 const playwright=within(tools).getByRole('button',{name:/playwright/});
 expect(playwright.getAttribute('aria-pressed')).toBe('false');
 expect(playwright.textContent).toContain('部分 1/2');
 await user.click(playwright);
 expect(within(tools).getByText('已选 2 / 4')).toBeTruthy();
 expect(playwright.getAttribute('aria-pressed')).toBe('true');
 await user.click(screen.getByRole('button',{name:'校验配置'}));
 expect(request.mock.calls.at(-1)?.[1]).toMatchObject({config:{tools:['mcp__playwright__browser_click','mcp__playwright__browser_navigate']}});
 await user.click(playwright);
 expect(within(tools).getByText('已选 0 / 4')).toBeTruthy();
 expect(screen.getByLabelText('工具范围').textContent).toContain('全部禁用');
 await user.type(within(tools).getByLabelText('搜索工具'),'issue');
 expect(within(tools).queryByRole('button',{name:/playwright/})).toBeNull();
 expect(within(tools).getByRole('button',{name:/github/}).textContent).toContain('含 1 个匹配');
});

it('imports a subagent directory without offering pasted YAML',async()=>{
 const {user,request}=setup();
 await user.click(screen.getByRole('tab',{name:'子代理'}));
 await user.click(await screen.findByRole('button',{name:'本地导入'}));
 expect(screen.queryByLabelText('导入方式')).toBeNull();
 expect(screen.queryByLabelText('SOUL.md')).toBeNull();
 fireEvent.change(screen.getByLabelText('本地路径'),{target:{value:'/tmp/reviewer'}});
 await user.click(within(screen.getByRole('dialog')).getByRole('button',{name:'导入'}));
 await waitFor(()=>expect(request).toHaveBeenCalledWith('/subagents/import',{path:'/tmp/reviewer'},undefined));
});

it('shows partial library read warnings while keeping available resources actionable',async()=>{
 const {user}=setup(listing({warnings:['部分个人配置暂时无法读取。']}));
 expect(await screen.findByText('部分个人配置暂时无法读取。')).toBeTruthy();
 await user.click(screen.getByRole('button',{name:/本地供应商/}));
 expect(screen.getByRole('button',{name:'编辑'})).toBeTruthy();
 expect((screen.getByRole('button',{name:'测试连接'}) as HTMLButtonElement).disabled).toBe(false);
});

it('retries a failed initial read without leaving a permanent loading state',async()=>{
 const store=new WorkbenchStore();
 let finishRetry!:(value:ConfigurationListing)=>void;
 const retry=new Promise<ConfigurationListing>(resolve=>{finishRetry=resolve;});
 const request=vi.spyOn(store,'configurationRequest').mockRejectedValueOnce(new Error('暂时无法读取配置')).mockImplementationOnce(()=>retry as never);
 render(<ConfigurationManager store={store} onClose={()=>{}}/>);
 expect(await screen.findByRole('alert')).toHaveProperty('textContent',expect.stringContaining('暂时无法读取配置'));
 expect(screen.getByText('读取失败，请重新读取配置。')).toBeTruthy();
 expect(screen.queryByText('正在读取配置')).toBeNull();
 await userEvent.setup().click(screen.getByRole('button',{name:'重新读取'}));
 expect(screen.getByText('正在读取配置')).toBeTruthy();
 expect(screen.queryByRole('alert')).toBeNull();
 await act(async()=>finishRetry(listing()));
 expect(await screen.findByRole('button',{name:/本地供应商/})).toBeTruthy();
 expect(request).toHaveBeenCalledTimes(2);
});
