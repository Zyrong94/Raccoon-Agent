"use client";

import {useId,useState,type ReactNode} from 'react';
import {CheckIcon} from 'lucide-react';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {Textarea} from '@/components/ui/textarea';
import {Select,type SelectOption} from '@/components/ui/select';
import {cn} from '@/lib/utils';
import {configurationLabels,type ConfigurationDomain,type ConfigurationDraft,type ConfigurationListing} from '@/lib/configuration';


function checklist(known:{name:string;description?:string}[],selected:string[],excluded:string[]=[]){
 const items=new Map<string,{name:string;description?:string}>(known.filter(item=>!excluded.includes(item.name)).map(item=>[item.name,{name:item.name,description:item.description}]));
 for(const name of selected)if(!excluded.includes(name)&&!items.has(name))items.set(name,{name});
 return [...items.values()];
}
type ToolItem={name:string;description?:string};
type ToolGroup={name:string;items:ToolItem[]};
function splitMcpTools(items:ToolItem[]){
 const local:ToolItem[]=[],groups=new Map<string,ToolGroup>();
 for(const item of items){
  const server=item.name.startsWith('mcp__')?item.name.split('__')[1]:undefined;
  if(!server){local.push(item);continue;}
  const group=groups.get(server)||{name:server,items:[]};
  group.items.push(item);
  groups.set(server,group);
 }
 return {local,groups:[...groups.values()].sort((left,right)=>left.name.localeCompare(right.name))};
}
function ChipPicker({label,unit,items,groups=[],selected,onChange,empty,hint}:{label:string;unit:string;items:ToolItem[];groups?:ToolGroup[];selected:string[];onChange:(next:string[])=>void;empty:string;hint:string}){
 const labelId=useId();
 const [query,setQuery]=useState(''),[only,setOnly]=useState(false);
 const lowered=query.trim().toLocaleLowerCase();
 const picked=new Set(selected);
 const matches=(item:ToolItem)=>!lowered||item.name.toLocaleLowerCase().includes(lowered)||(item.description||'').toLocaleLowerCase().includes(lowered);
 const visible=items.filter(item=>(!only||picked.has(item.name))&&matches(item));
 const groupMatches=(group:ToolGroup)=>lowered?group.items.filter(matches).length:0;
 const visibleGroups=groups.filter(group=>(!only||group.items.some(item=>picked.has(item.name)))&&(!lowered||group.name.toLocaleLowerCase().includes(lowered)||groupMatches(group)>0));
 const total=items.length+groups.reduce((sum,group)=>sum+group.items.length,0);
 const visibleNames=[...visible.map(item=>item.name),...visibleGroups.flatMap(group=>group.items.map(item=>item.name))];
 const toggle=(name:string)=>onChange(picked.has(name)?selected.filter(value=>value!==name):[...selected,name]);
 const toggleGroup=(group:ToolGroup)=>{const names=group.items.map(item=>item.name);return onChange(names.every(name=>picked.has(name))?selected.filter(name=>!names.includes(name)):[...new Set([...selected,...names])]);};
 const action='focus-visible:outline-ring hover:bg-foreground/[0.06] text-muted-foreground hover:text-foreground h-8 shrink-0 rounded-md px-2 text-xs disabled:pointer-events-none disabled:opacity-40';
 return <div role="group" aria-labelledby={labelId} className="space-y-2">
  <div className="flex items-baseline justify-between gap-3"><span id={labelId} className="text-sm font-medium">{label}</span><span className="text-muted-foreground text-xs tabular-nums">已选 {selected.length} / {total}</span></div>
  <div className="overflow-hidden rounded-lg border">
   <div className="bg-foreground/[0.02] flex items-center gap-1 border-b p-2">
    <input type="search" aria-label={`搜索${unit}`} placeholder={`搜索${unit}`} value={query} onChange={event=>setQuery(event.target.value)} className="border-foreground/15 bg-background focus-visible:outline-ring h-8 min-w-0 flex-1 rounded-md border px-2 text-xs"/>
    <button type="button" disabled={!total} className={action} onClick={()=>onChange([...new Set([...selected,...visibleNames])])}>全选</button>
    <button type="button" disabled={!selected.length} className={action} onClick={()=>onChange(selected.filter(name=>!visibleNames.includes(name)))}>清空</button>
    <button type="button" aria-pressed={only} className={cn(action,only&&'bg-foreground text-background hover:bg-foreground hover:text-background')} onClick={()=>setOnly(value=>!value)}>只看已选</button>
   </div>
   <div className="p-2.5">
    <div className="flex flex-wrap gap-1.5">
     {visible.map(item=><button key={item.name} type="button" aria-pressed={picked.has(item.name)} title={item.description||undefined} onClick={()=>toggle(item.name)} className={cn('focus-visible:outline-ring inline-flex h-8 items-center gap-1 rounded-md border px-2 font-mono text-xs',picked.has(item.name)?'border-foreground bg-foreground text-background':'hover:bg-foreground/[0.04]')}>{picked.has(item.name)&&<CheckIcon className="size-3"/>}{item.name}</button>)}
    </div>
    {!!visibleGroups.length&&<div className="border-foreground/10 mt-2 space-y-1 border-t pt-2">
     <p className="text-muted-foreground px-1 text-[11px]">MCP 服务 · 整组授权</p>
     {visibleGroups.map(group=>{const count=group.items.filter(item=>picked.has(item.name)).length,all=count===group.items.length,matched=groupMatches(group);return <button key={group.name} type="button" aria-pressed={all} onClick={()=>toggleGroup(group)} className={cn('focus-visible:outline-ring flex min-h-8 w-full items-center gap-2 rounded-md border px-2 py-1 text-left',all?'border-foreground/40 bg-foreground/[0.045]':'hover:bg-foreground/[0.04]')}>
      <span className={cn('grid size-3.5 shrink-0 place-items-center rounded-[3px] border',all?'border-foreground bg-foreground text-background':'border-foreground/30')}>{all?<CheckIcon className="size-2.5"/>:count?<span className="bg-foreground size-1.5 rounded-[1px]"/>:null}</span>
      <span className="font-mono text-xs">{group.name}</span>
      <span className="text-muted-foreground text-xs">{group.items.length} 个工具</span>
      {!all&&count>0&&<span className="text-muted-foreground ml-auto text-xs tabular-nums">部分 {count}/{group.items.length}</span>}
      {!count&&matched>0&&<span className="text-muted-foreground ml-auto text-xs">含 {matched} 个匹配</span>}
     </button>;})}
    </div>}
    {!visible.length&&!visibleGroups.length&&<p className="text-muted-foreground px-1 py-2 text-sm">{!total?empty:only&&!selected.length?`还没有选择${unit}`:`没有匹配的${unit}`}</p>}
   </div>
  </div>
  <p className="text-muted-foreground text-xs leading-5">{hint}</p>
 </div>;
}
export function ResourceForm({domain,draft,onChange,busy,readOnly,data,onSave,onValidate,onCancel,editing=false}:{domain:ConfigurationDomain;draft:ConfigurationDraft;onChange:(draft:ConfigurationDraft)=>void;busy:boolean;readOnly:boolean;data:ConfigurationListing;onSave:()=>void;onValidate:()=>void;onCancel:()=>void;editing?:boolean}){
 const prefix=useId();
 const config=draft.config;
 const update=(key:string,value:unknown)=>onChange({...draft,config:{...config,[key]:value}});
 const text=(key:string)=>String(config[key]??'');
 const field=(label:string,child:(id:string)=>ReactNode,hint?:string)=>{const id=prefix+label;return <div className="space-y-2"><label htmlFor={id} className="block text-sm font-medium">{label}</label>{child(id)}{hint&&<p className="text-muted-foreground text-xs leading-5">{hint}</p>}</div>;};
 const input=(key:string,label:string,required=false,hint?:string)=>field(label,id=><Input id={id} className="h-10" value={text(key)} required={required} disabled={editing&&['name','provider_id','model_id'].includes(key)} onChange={event=>update(key,event.target.value)}/>,hint);
 const checkbox=(key:string,label:string)=> <label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" checked={!!config[key]} onChange={event=>update(key,event.target.checked)} className="size-4 accent-current"/>{label}</label>;
 const chosen=(key:string)=>Array.isArray(config[key])?config[key] as string[]:[];
 const picked=(key:string)=>Array.isArray(config[key])?(config[key] as string[]):[];
 const scope=(key:string,label:string,inherit:string)=>{
  const selected=picked(key);
  const options:SelectOption[]=[
   {value:'inherit',label:inherit},
   {value:'none',label:'全部禁用'},
   ...(selected.length?[{value:'selected',label:'仅允许所选项'}]:[]),
  ];
  return field(label,id=><Select id={id} value={config[key]==null?'inherit':selected.length?'selected':'none'} options={options} onValueChange={value=>update(key,value==='inherit'?null:[])}/>);
 };
 const skillOptions=(data.options?.skill_names||data.skills.map(item=>String(item.config.name||item.id))).map(name=>{const description=data.skills.find(item=>String(item.config.name||item.id)===name)?.config.description;return {name,description:description?String(description):undefined};});
 const toolCatalog=checklist(data.options?.tools||[],chosen('tools'),['delegate']);
 const {local:localTools,groups:mcpGroups}=splitMcpTools(toolCatalog);
 return <form onSubmit={event=>{event.preventDefault();onSave();}} className="mx-auto w-full max-w-[860px] p-5 md:p-8 lg:p-10">
  <div className="mb-7"><h2 className="text-xl font-semibold">{editing?'编辑':'新建'}{configurationLabels[domain]}</h2>{editing&&<p className="text-muted-foreground mt-2 text-sm">标识不可修改。</p>}</div>
  <fieldset disabled={busy||readOnly} className="space-y-5">
   {domain==='skills'&&field('保存位置',id=><select id={id} className="border-input bg-background h-10 w-full rounded-lg border px-3" value={draft.scope||'global'} disabled={editing} onChange={event=>onChange({...draft,scope:event.target.value as 'global'|'project'})}><option value="global">Raccoon 全局（~/.raccoon/skills）</option><option value="project">当前项目（.agents/skills）</option></select>,'项目同名技能优先于全局技能；共享全局目录只读。')}
   {(domain==='skills'||domain==='subagents')&&<><div className="grid gap-5 sm:grid-cols-2">{input('name',domain==='skills'?'技能名称':'角色名称',true,domain==='subagents'?'使用字母、数字与短横线，例如 code-review。':undefined)}{input('description','说明')}</div></>}
   {domain==='subagents'&&<>
    <p className="rounded-lg bg-foreground/[0.035] px-4 py-3 text-sm">模型继承父代理。工具和技能可按角色调整。</p>
    <div className="grid gap-5 sm:grid-cols-2">{scope('tools','工具范围','继承父代理工具（除 delegate）')}{scope('skills','技能范围','继承父代理技能')}</div>
    <ChipPicker label="允许的工具" unit="工具" items={localTools} groups={mcpGroups} selected={chosen('tools')} onChange={value=>update('tools',value)} empty="暂无可用工具。" hint="选择工具后，仅允许所选工具；MCP 服务按整组授权；全部取消代表不使用工具。"/>
    <ChipPicker label="引用技能" unit="技能" items={checklist(skillOptions,chosen('skills'))} selected={chosen('skills')} onChange={value=>update('skills',value)} empty="暂无技能包。" hint="选择技能后，仅引用所选技能。全部取消会禁用技能。"/>
    <details className="text-xs"><summary className="cursor-pointer py-2">查看可用工具</summary><div className="max-h-48 space-y-2 overflow-y-auto">{localTools.map(tool=><p key={tool.name}><strong>{tool.name}</strong> · {tool.description}</p>)}{mcpGroups.map(group=><p key={group.name}><strong>{group.name}</strong> · {group.items.length} 个 MCP 工具，整组授权</p>)}</div></details>
    <div className="flex flex-wrap gap-x-6">{checkbox('allow_background','允许后台执行')}{checkbox('requires_worktree','要求隔离工作树')}</div>
   </>}
   {(domain==='skills'||domain==='subagents')&&field(domain==='skills'?'SKILL.md':'SOUL.md',id=><Textarea id={id} rows={14} required spellCheck={false} className="font-mono text-sm leading-6" value={draft.content} onChange={event=>onChange({...draft,content:event.target.value})}/>,domain==='skills'?'可直接编写 Markdown；如有 YAML 元数据，其中的 name 须与技能名称一致。本地目录导入会保留 scripts、references 等资源。':'定义角色职责、工作流程与交付标准。')}
  </fieldset>
  <div className="border-foreground/10 mt-7 flex flex-wrap gap-2 border-t pt-5 [&_button]:min-h-11 md:[&_button]:min-h-8">{domain!=='skills'&&<Button type="button" variant="outline" disabled={busy} onClick={onValidate}>校验配置</Button>}<Button type="button" variant="ghost" disabled={busy} onClick={onCancel}>取消</Button>{!readOnly&&<Button type="submit" disabled={busy} className="ml-auto">{busy?'正在处理…':'保存配置'}</Button>}</div>
 </form>;
}

