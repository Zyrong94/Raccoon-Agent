export type ConfigurationDomain='models'|'mcp'|'skills'|'subagents';
export type ResourceConfig=Record<string,unknown>;
export type McpServerStatus={name:string;state:'disabled'|'connecting'|'connected'|'failed';tool_count:number;error?:string|null;tools:string[]};
export type ConfigurationRecord={id:string;revision:string;config:ResourceConfig;content?:string;source:'managed'|'builtin'|'global'|'project'|'shared';scope?:'global'|'project'|'shared';overridden?:boolean;read_only?:boolean;warning?:string;path?:string;import_path?:string;origin?:'project'|'global'|'unknown';origin_path?:string|null};
export type ConfigurationListing={models:ConfigurationRecord[];mcp:ConfigurationRecord[];skills:ConfigurationRecord[];subagents:ConfigurationRecord[];root:string;warnings?:string[];mcp_status?:{servers:McpServerStatus[]};options?:{tools:{name:string;description:string}[];skill_names:string[];provider_types:string[]}};
export type ConfigurationDraft={config:ResourceConfig;content:string;scope?:'global'|'project'};
export const configurationLabels:Record<ConfigurationDomain,string>={models:'模型',mcp:'MCP',skills:'技能包',subagents:'子代理'};
export function configurationKey(item:ConfigurationRecord){return item.scope?`${item.scope}:${item.id}`:item.id;}
export function configurationName(item:ConfigurationRecord){return String(item.config.label||item.config.name||item.config.model_id||item.id);}
export function configurationDraft(domain:ConfigurationDomain,item?:ConfigurationRecord):ConfigurationDraft{
 const defaults:Record<ConfigurationDomain,ResourceConfig>={models:{provider_id:'',model_id:'',label:'',default:false},mcp:{name:'',type:'local',command:[],enabled:true,timeout_ms:5000},skills:{name:'',description:''},subagents:{name:'',description:'',allow_background:false,requires_worktree:false}};
 const config=structuredClone(item?.config||defaults[domain]);
 if(domain==='mcp'&&item)config.name=item.id;
 return {config,...(domain==='skills'?{scope:item?.scope==='project'?'project' as const:'global' as const}:{}),content:item?.content||(domain==='skills'?'# 使用说明\n\n描述此技能的适用场景、工作流程与交付要求。\n':domain==='subagents'?'请描述此子代理的职责、工作流程与交付要求。':'')};
}
export function configurationBody(draft:ConfigurationDraft){
 const config=Object.fromEntries(Object.entries(draft.config).filter(([,value])=>value!==''&&value!==undefined));
 return {config,...(draft.scope?{scope:draft.scope}:{}),...(draft.content?{content:draft.content}:{})};
}
export function configurationPayload(draft:ConfigurationDraft,original:ConfigurationRecord|null){
 return {...(original?{id:original.id,revision:original.revision}:{}),...configurationBody(draft)};
}
export type ProviderGroup={id:string;name:string;baseUrl:string;apiKeyEnv:string;models:ConfigurationRecord[]};
export type ModelDraft={record:ConfigurationRecord|null;modelId:string;label:string;contextWindow:string;reasoningEffort:string;supportedReasoningEfforts:string;parallelToolCalls:boolean|null;vision:boolean|null;pdfInput:boolean|null;isDefault:boolean};
export type ProviderDraft={id:string;name:string;baseUrl:string;apiKeyEnv:string;models:ModelDraft[]};
// A provider is the connection record shared by its models; the listing only exposes one record per model.
export function groupProviders(records:ConfigurationRecord[]):ProviderGroup[]{
 const groups=new Map<string,ProviderGroup>();
 for(const record of records){
  const config=record.config,id=String(config.provider_id||record.id.split('/')[0]);
  const group=groups.get(id)||{id,name:String(config.provider_label||id),baseUrl:String(config.base_url||''),apiKeyEnv:String(config.api_key_env||''),models:[]};
  group.baseUrl=group.baseUrl||String(config.base_url||'');
  group.apiKeyEnv=group.apiKeyEnv||String(config.api_key_env||'');
  if(config.provider_label)group.name=String(config.provider_label);
  group.models.push(record);
  groups.set(id,group);
 }
 for(const group of groups.values())group.models.sort((left,right)=>String(left.config.model_id).localeCompare(String(right.config.model_id)));
 return [...groups.values()].sort((left,right)=>left.name.localeCompare(right.name));
}
export function modelDraft(record?:ConfigurationRecord):ModelDraft{
 const config=record?.config||{},text=(value:unknown)=>value==null?'':String(value);
 return {record:record||null,modelId:text(config.model_id),label:text(config.label),contextWindow:text(config.context_window),reasoningEffort:text(config.reasoning_effort),supportedReasoningEfforts:(config.supported_reasoning_efforts as string[]||[]).join(', '),parallelToolCalls:config.parallel_tool_calls===undefined?null:!!config.parallel_tool_calls,vision:config.vision===undefined?null:!!config.vision,pdfInput:config.pdf_input===undefined?null:!!config.pdf_input,isDefault:!!config.default};
}
export function providerDraft(group?:ProviderGroup):ProviderDraft{
 return {id:group?.id||'',name:group?.name||'',baseUrl:group?.baseUrl||'',apiKeyEnv:group?.apiKeyEnv||'',models:group?group.models.map(record=>modelDraft(record)):[{...modelDraft(),isDefault:true}]};
}
export function providerSnapshot(draft:ProviderDraft){return JSON.stringify({...draft});}
function size(value:string){const parsed=Number(value);return value.trim()&&Number.isFinite(parsed)&&parsed>0?parsed:null;}
export function providerModelConfig(draft:ProviderDraft,model:ModelDraft):ResourceConfig{
 const config:Record<string,unknown>={provider_id:draft.id.trim(),model_id:model.modelId.trim(),provider_label:draft.name.trim(),label:model.label.trim(),base_url:draft.baseUrl.trim(),api_key_env:draft.apiKeyEnv.trim(),parallel_tool_calls:model.parallelToolCalls,context_window:size(model.contextWindow),vision:model.vision,pdf_input:model.pdfInput,default:model.isDefault,supported_reasoning_efforts:model.supportedReasoningEfforts.split(/[,，\s]+/).filter(Boolean)};
 if(model.reasoningEffort.trim())config.reasoning_effort=model.reasoningEffort.trim();
 return Object.fromEntries(Object.entries(config).filter(([,value])=>value!==''&&value!==null&&value!==undefined));
}
export function modelRef(providerId:string,modelId:string){return `${providerId}/${modelId}`;}

export function parseLines(value:string){return value.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);}
export function parsePairs(value:string){return Object.fromEntries(parseLines(value).map(line=>{const index=line.indexOf('=');return index<0?[line,'']:[line.slice(0,index).trim(),line.slice(index+1)];}));}
export function pairLines(value:unknown){return value&&typeof value==='object'?Object.entries(value).map(([key,item])=>`${key}=${item}`).join('\n'):'';}
// Masked secrets stay as env references so a copied snippet never carries a placeholder value.
export function mcpToml(record:ConfigurationRecord){
 const config=record.config,quote=(value:string)=>JSON.stringify(value),lines=[`[mcp.${record.id}]`];
 for(const key of ['type','url','bearer_token_env_var','enabled','timeout_ms']){
  const value=config[key];
  if(value===undefined||value===null||value==='')continue;
  lines.push(`${key} = ${typeof value==='string'?quote(value):String(value)}`);
 }
 for(const key of ['command','allowed_tools']){
  const value=config[key];
  if(Array.isArray(value)&&value.length)lines.push(`${key} = [${value.map(item=>quote(String(item))).join(', ')}]`);
 }
 for(const field of ['env','headers'] as const){
  const pairs=config[field] as Record<string,string>|undefined;
  if(!pairs||!Object.keys(pairs).length)continue;
  lines.push('',`[mcp.${record.id}.${field}]`);
  for(const [key,value] of Object.entries(pairs))lines.push(`${key} = ${quote(value==='••••••'?`{env:${key}}`:value)}`);
 }
 return lines.join('\n')+'\n';
}
