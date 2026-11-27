"use client";

import {useId,useState,type ReactNode} from 'react';
import {ChevronRight,Plus,Trash2} from 'lucide-react';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {Select} from '@/components/ui/select';
import {cn} from '@/lib/utils';
import {modelDraft,type ModelDraft,type ProviderDraft} from '@/lib/configuration';

function Field({label,hint,children}:{label:string;hint?:string;children:(id:string)=>ReactNode}){
 const id=useId();
 return <div className="space-y-1.5"><label htmlFor={id} className="block text-[13px] font-medium">{label}</label>{children(id)}{hint&&<p className="text-muted-foreground text-[12px] leading-5">{hint}</p>}</div>;
}
function Toggle({label,value,onChange,disabled}:{label:string;value:boolean|null;onChange:(next:boolean|null)=>void;disabled?:boolean}){
 return <Field label={label}>{id=><Select id={id} value={value==null?'default':value?'on':'off'} disabled={disabled} options={[{value:'default',label:'使用默认值'},{value:'on',label:'启用'},{value:'off',label:'禁用'}]} onValueChange={next=>onChange(next==='default'?null:next==='on')}/>}</Field>;
}
function Check({label,checked,onChange,disabled}:{label:string;checked:boolean;onChange:(next:boolean)=>void;disabled?:boolean}){
 return <label className="flex items-center gap-1.5 text-[12.5px]"><input type="checkbox" className="accent-foreground size-3.5" checked={checked} disabled={disabled} onChange={event=>onChange(event.target.checked)}/>{label}</label>;
}

export function ProviderEditor({draft,onChange,busy,editing,dirty,onSave,onCancel,onTest,onDelete}:{
 draft:ProviderDraft;onChange:(next:ProviderDraft)=>void;busy:boolean;editing:boolean;dirty:boolean;
 onSave:()=>void;onCancel:()=>void;onTest:()=>void;onDelete:()=>void;
}){
 const [open,setOpen]=useState<number[]>(editing?[]:[0]);
 const [connection,setConnection]=useState(!editing);
 const update=(patch:Partial<ProviderDraft>)=>onChange({...draft,...patch});
 const updateModel=(index:number,patch:Partial<ModelDraft>)=>update({models:draft.models.map((model,position)=>position===index?{...model,...patch}:model)});
 const setDefault=(index:number)=>update({models:draft.models.map((model,position)=>({...model,isDefault:position===index}))});
 const toggle=(index:number)=>setOpen(current=>current.includes(index)?current.filter(value=>value!==index):[...current,index]);
 const addModel=()=>{update({models:[...draft.models,{...modelDraft(),isDefault:!draft.models.some(model=>model.isDefault)}]});setOpen(current=>[...current,draft.models.length]);};
 const removeModel=(index:number)=>{
  const models=draft.models.filter((_,position)=>position!==index);
  if(models.length&&!models.some(model=>model.isDefault))models[0]={...models[0],isDefault:true};
  update({models});
  setOpen(current=>current.filter(value=>value!==index).map(value=>value>index?value-1:value));
 };
 const models=draft.models;
 const identifiers=models.map(model=>model.modelId.trim()).filter(Boolean);
 const problem=!draft.id.trim()?'填写提供商标识':!/^[A-Za-z0-9_.-]+$/.test(draft.id.trim())?'提供商标识只能使用字母、数字、点、下划线与短横线':!draft.apiKeyEnv.trim()?'填写密钥环境变量名称':!models.length?'至少添加一个模型':models.some(model=>!model.modelId.trim())?'填写模型 ID':new Set(identifiers).size!==identifiers.length?'模型 ID 不能重复':models.some(model=>model.reasoningEffort&&!model.supportedReasoningEfforts.split(/[,，\s]+/).includes(model.reasoningEffort))?'默认推理强度必须包含在支持列表中':'';
 const hint=problem||'有未保存的修改';
 const fallback=models.find(model=>model.isDefault)||models[0];
 return <div className="mx-auto flex w-full max-w-[820px] flex-col px-5 py-7 md:px-8 md:py-8">
  <div className="flex items-start gap-3">
   <h2 className="text-[20px] font-semibold tracking-[-0.01em]">{editing?draft.name||draft.id:'新建供应商'}</h2>
   {editing&&<div className="ml-auto flex shrink-0 gap-1.5"><Button variant="outline" size="sm" disabled={busy} onClick={onTest}>测试连接</Button></div>}
  </div>
  <p className="text-muted-foreground mt-1.5 text-[13px] leading-5">{editing?`${models.length} 个模型${fallback?.isDefault?` · 默认 ${fallback.modelId}`:''}`:'连接信息填一次，之后在这个供应商下添加模型。'}</p>

  {connection&&<div className="mt-7">
   <div className="grid gap-4 sm:grid-cols-2">
    <Field label="显示名称" hint="列表里显示的名字，可以写中文。">{id=><Input id={id} className="h-10" value={draft.name} placeholder="如 DeepSeek" disabled={busy} onChange={event=>update({name:event.target.value})}/>}</Field>
    <Field label="提供商标识" hint="模型内部编号的前半段，保存后不能修改。">{id=><Input id={id} className="h-10" value={draft.id} placeholder="custom_3ad57f7e" disabled={busy||editing} onChange={event=>update({id:event.target.value})}/>}</Field>
    <Field label="API 地址">{id=><Input id={id} className="h-10" value={draft.baseUrl} placeholder="https://api.example.com/v1" disabled={busy} onChange={event=>update({baseUrl:event.target.value})}/>}</Field>
    <Field label="密钥环境变量" hint="只填写环境变量名称，不在这里保存密钥。">{id=><Input id={id} className="h-10" value={draft.apiKeyEnv} placeholder="DEEPSEEK_API_KEY" disabled={busy} onChange={event=>update({apiKeyEnv:event.target.value})}/>}</Field>
   </div>
   {editing&&<button type="button" className="text-muted-foreground hover:text-foreground mt-2 rounded-md text-[12.5px]" onClick={()=>setConnection(false)}>收起连接设置</button>}
  </div>}

  <section className="mt-8">
   <div className="flex items-baseline gap-2">
    <h3 className="text-[13px] font-medium">模型</h3>
    <span className="text-muted-foreground text-[12px] tabular-nums">{models.length}</span>
    <button type="button" disabled={busy} onClick={addModel} className="text-muted-foreground hover:text-foreground focus-visible:outline-ring ml-auto flex items-center gap-1 rounded-md px-1 text-[12.5px]"><Plus className="size-3"/>添加模型</button>
   </div>
   <ul className="border-foreground/10 mt-1 border-t">
    {models.map((model,index)=>{
     const openModel=open.includes(index),existing=!!model.record;
     return <li key={model.record?.id||`new-${index}`} className="border-foreground/[0.06] border-b last:border-b-0">
      <div className="flex items-center gap-1">
       <button type="button" aria-expanded={openModel} aria-label={openModel?`收起模型 ${model.modelId}`:`展开模型 ${model.modelId||'新模型'}`} onClick={()=>toggle(index)} className="focus-visible:outline-ring hover:bg-foreground/[0.025] flex min-w-0 flex-1 items-center gap-2 rounded-md py-2.5 pr-1 text-left">
        <span className="text-muted-foreground grid size-6 shrink-0 place-items-center"><ChevronRight className={cn('size-3.5 transition-transform',openModel&&'rotate-90')} strokeWidth={2}/></span>
        <span className={cn('shrink-0 font-mono text-[12.5px]',!model.modelId&&'text-muted-foreground')}>{model.modelId||'新模型'}</span>
        <span className="text-muted-foreground hidden min-w-0 truncate text-[12.5px] sm:block">{model.label}</span>
       </button>
       <button type="button" aria-label={`删除模型 ${model.modelId||''}`.trim()} disabled={busy} onClick={()=>removeModel(index)} className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive grid size-7 shrink-0 place-items-center rounded-md"><Trash2 className="size-3.5" strokeWidth={1.75}/></button>
      </div>
      {openModel&&<div className="pr-1 pb-5 pl-7">
       <div className="grid gap-x-5 gap-y-3 sm:grid-cols-2">
        <Field label="模型 ID" hint={existing?'保存后不能修改。':undefined}>{id=><Input id={id} className="h-9" value={model.modelId} placeholder="deepseek-chat" disabled={busy||existing} onChange={event=>updateModel(index,{modelId:event.target.value})}/>}</Field>
        <Field label="模型名称">{id=><Input id={id} className="h-9" value={model.label} placeholder="DeepSeek Chat" disabled={busy} onChange={event=>updateModel(index,{label:event.target.value})}/>}</Field>
        <Field label="上下文窗口（tokens）">{id=><Input id={id} className="h-9" inputMode="numeric" value={model.contextWindow} placeholder="128000" disabled={busy} onChange={event=>updateModel(index,{contextWindow:event.target.value})}/>}</Field>
       </div>
       <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1">
        <Check label="文本" checked disabled onChange={()=>{}}/>
        <Check label="图片" checked={!!model.vision} disabled={busy} onChange={value=>updateModel(index,{vision:value})}/>
        <Check label="PDF" checked={!!model.pdfInput} disabled={busy} onChange={value=>updateModel(index,{pdfInput:value})}/>
       </div>
       <div className="mt-3 grid gap-x-5 gap-y-3 sm:grid-cols-2">
        <Field label="支持的推理强度" hint="用逗号分隔；留空表示不支持设置。">{id=><Input id={id} className="h-9" value={model.supportedReasoningEfforts} placeholder="low, medium, high" disabled={busy} onChange={event=>updateModel(index,{supportedReasoningEfforts:event.target.value})}/>}</Field>
        <Field label="默认推理强度">{id=><Select id={id} value={model.reasoningEffort||'__default__'} disabled={busy} options={[{value:'__default__',label:'使用服务默认值'},...model.supportedReasoningEfforts.split(/[,，\s]+/).filter(Boolean).map(value=>({value,label:value}))]} onValueChange={value=>updateModel(index,{reasoningEffort:value==='__default__'?'':value})}/>}</Field>
        <Toggle label="并行工具调用" value={model.parallelToolCalls} onChange={value=>updateModel(index,{parallelToolCalls:value})} disabled={busy}/>
       </div>
       <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1">
        <Check label="设为默认模型" checked={model.isDefault} disabled={busy} onChange={()=>setDefault(index)}/>
       </div>
      </div>}
     </li>;
    })}
    {!models.length&&<li className="text-muted-foreground py-6 text-center text-[12.5px]">还没有模型，至少添加一个才能保存。</li>}
   </ul>
  </section>

  {editing&&!connection&&<section className="mt-8">
   <div className="flex items-baseline gap-2">
    <h3 className="text-[13px] font-medium">连接</h3>
    <button type="button" disabled={busy} onClick={()=>setConnection(true)} className="text-muted-foreground hover:text-foreground focus-visible:outline-ring ml-auto rounded-md px-1 text-[12.5px]">编辑</button>
   </div>
   <dl className="bg-foreground/[0.035] divide-foreground/10 mt-2 grid overflow-hidden rounded-lg text-[13px] sm:grid-cols-3 sm:divide-x">
    {[['协议','OpenAI Responses'],['API 地址',draft.baseUrl||'使用默认地址'],['API 密钥',draft.apiKeyEnv?`环境变量 ${draft.apiKeyEnv}`:'未配置']].map(([name,value])=><div key={name} className="px-3 py-2.5">
     <dt className="text-muted-foreground text-[11.5px]">{name}</dt>
     <dd className="mt-0.5 break-words">{value}</dd>
    </div>)}
   </dl>
  </section>}

  {editing&&<section className="border-foreground/10 mt-10 border-t pt-5">
   <h3 className="text-[13px] font-medium">移除供应商</h3>
   <p className="text-muted-foreground mt-1 text-[12.5px] leading-5">删除后，这个供应商下的 {models.length} 个模型都不再使用；已开始的任务保留。</p>
   <Button variant="outline" size="sm" disabled={busy} onClick={onDelete} className="border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive mt-3">删除供应商</Button>
  </section>}

  {(dirty||!editing)&&<div className="border-foreground/10 bg-background sticky bottom-0 z-10 -mx-5 mt-10 flex items-center gap-3 border-t px-5 py-3 md:-mx-8 md:px-8">
   <p className={cn('min-w-0 text-[12px]',problem?'text-destructive':'text-muted-foreground')}>{hint}</p>
   <div className="ml-auto flex shrink-0 gap-2">
    <Button variant="ghost" size="sm" disabled={busy} onClick={onCancel}>{editing?'撤销':'取消'}</Button>
    <Button size="sm" disabled={busy||!!problem} onClick={onSave}>{busy?'正在保存…':'保存并应用'}</Button>
   </div>
  </div>}
 </div>;
}
