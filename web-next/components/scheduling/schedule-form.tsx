"use client";

import {Select} from '@base-ui/react/select';
import {CheckIcon,ChevronDownIcon} from 'lucide-react';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {Textarea} from '@/components/ui/textarea';
import {weekdays,type ScheduleDraft} from '@/lib/scheduling';

function Choice({id,value,onChange,options,disabled}:{id:string;value:string;onChange:(value:string)=>void;options:{value:string;label:string}[];disabled?:boolean}){
 return <Select.Root value={value} disabled={disabled} onValueChange={value=>{if(value!==null)onChange(value);}} items={options}>
  <Select.Trigger id={id} className="border-input bg-background focus-visible:ring-ring/40 flex h-9 w-full items-center justify-between gap-2 rounded-lg border px-3 text-sm outline-none focus-visible:ring-2"><Select.Value/><ChevronDownIcon className="text-muted-foreground size-3.5"/></Select.Trigger>
  <Select.Portal><Select.Positioner className="z-[70]" sideOffset={4} alignItemWithTrigger={false}><Select.Popup className="bg-popover text-popover-foreground ring-foreground/10 min-w-[var(--anchor-width)] rounded-lg p-1 text-sm shadow-sm ring-1 outline-none"><Select.List>{options.map(option=><Select.Item key={option.value} value={option.value} className="data-highlighted:bg-accent relative flex cursor-default items-center rounded-md py-2 pr-8 pl-2.5 outline-none"><Select.ItemText>{option.label}</Select.ItemText><Select.ItemIndicator className="absolute right-2"><CheckIcon className="size-3.5"/></Select.ItemIndicator></Select.Item>)}</Select.List></Select.Popup></Select.Positioner></Select.Portal>
 </Select.Root>;
}

export function ScheduleForm({draft,editing,busy,conflict,sourceLabel,onChange,onSave,onCancel}:{draft:ScheduleDraft;editing:boolean;busy:boolean;conflict:boolean;sourceLabel?:string;onChange:(draft:ScheduleDraft)=>void;onSave:()=>void;onCancel:()=>void}){
 const set=(key:keyof ScheduleDraft,value:string)=>onChange({...draft,[key]:value});
 const label='mb-2 block text-[13px] font-medium';
 return <form onSubmit={event=>{event.preventDefault();onSave();}} className="mx-auto w-full max-w-[640px] p-7 lg:p-10">
  <div className="mb-8 flex items-center justify-between"><h2 className="text-xl font-semibold tracking-tight">{editing?'编辑任务':'新建任务'}</h2><Button type="button" variant="ghost" size="sm" disabled={busy} onClick={onCancel}>取消</Button></div>
  {sourceLabel&&<p className="text-muted-foreground mb-5 text-sm">{editing?'任务来源':'新建来源'}：{sourceLabel}。结果回到该来源会话。</p>}
  {conflict&&<p role="alert" className="text-destructive mb-5 text-sm">任务已在其他入口修改。草稿已保留，请核对最新任务后重新编辑。</p>}
  <fieldset disabled={busy} className="space-y-6 disabled:opacity-60">
   <div><label htmlFor="schedule-title" className={label}>任务名称</label><Input id="schedule-title" required maxLength={160} value={draft.title} onChange={e=>set('title',e.target.value)} placeholder="例如：每日项目简报" className="h-9"/></div>
   <div><label htmlFor="schedule-prompt" className={label}>执行指令</label><Textarea id="schedule-prompt" required maxLength={20000} value={draft.prompt} onChange={e=>set('prompt',e.target.value)} placeholder="到时间后，需要 Raccoon 做什么？" className="min-h-32 resize-y"/></div>
   <div className="grid grid-cols-2 gap-4">
    <div><label htmlFor="schedule-frequency" className={label}>执行频率</label><Choice id="schedule-frequency" value={draft.preset} onChange={value=>set('preset',value)} options={[{value:'daily',label:'每天'},{value:'weekly',label:'每周'},{value:'interval',label:'固定间隔'},{value:'once',label:'仅一次'},{value:'custom',label:'自定义 Cron'}]}/></div>
    {(draft.preset==='daily'||draft.preset==='weekly')&&<div><label htmlFor="schedule-time" className={label}>执行时间</label><Input id="schedule-time" type="time" required value={draft.time} onChange={e=>set('time',e.target.value)} className="h-9 tabular-nums"/></div>}
    {draft.preset==='interval'&&<div><label htmlFor="schedule-minutes" className={label}>间隔（分钟）</label><Input id="schedule-minutes" type="number" required min="1" max="43200" step="any" value={draft.minutes} onChange={e=>set('minutes',e.target.value)} className="h-9"/></div>}
    {draft.preset==='weekly'&&<div><label htmlFor="schedule-weekday" className={label}>星期</label><Choice id="schedule-weekday" value={draft.weekday} onChange={value=>set('weekday',value)} options={weekdays.map((day,index)=>({value:String(index),label:`星期${day}`}))}/></div>}
    {draft.preset==='once'&&<div><label htmlFor="schedule-once" className={label}>执行日期与时间</label><Input id="schedule-once" required type="datetime-local" step="any" value={draft.at} onChange={e=>set('at',e.target.value)} className="h-9"/></div>}
    {draft.preset==='custom'&&<div><label htmlFor="schedule-cron" className={label}>Cron 表达式</label><Input id="schedule-cron" required value={draft.cron} onChange={e=>set('cron',e.target.value)} className="h-9 font-mono" placeholder="0 9 * * *"/></div>}
    <div className="col-span-2"><label htmlFor="schedule-timezone" className={label}>时区</label><Input id="schedule-timezone" required list="schedule-timezones" value={draft.timezone} onChange={e=>set('timezone',e.target.value)} className="h-9"/><datalist id="schedule-timezones"><option value="Asia/Shanghai"/><option value="Asia/Tokyo"/><option value="UTC"/></datalist></div>
   </div>
   <div><label className={label}>执行会话</label><p className="text-muted-foreground text-sm">到期后进入来源会话队列，回复保存在该会话。</p></div>
   <div><label htmlFor="schedule-model" className={label}>模型（可选）</label><Input id="schedule-model" maxLength={200} value={draft.model} onChange={e=>set('model',e.target.value)} placeholder="留空沿用会话模型，例如 provider/model" className="h-9"/></div>
   <div><label htmlFor="schedule-permission" className={label}>权限</label><Choice id="schedule-permission" value={draft.permission_mode} onChange={value=>set('permission_mode',value)} options={[{value:'inherit',label:'沿用会话权限'},{value:'ask',label:'需要时询问'},{value:'auto',label:'自动审核'},{value:'bypass',label:'全部允许'}]}/></div>
   <div className="border-foreground/10 flex justify-end border-t pt-5"><Button type="submit" disabled={busy}>{busy?'正在保存…':'保存任务'}</Button></div>
  </fieldset>
 </form>;
}
