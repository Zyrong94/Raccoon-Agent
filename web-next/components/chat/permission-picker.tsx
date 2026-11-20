"use client";

import {useState} from 'react';
import {Menu} from '@base-ui/react/menu';
import {CheckIcon,ShieldIcon,ShieldAlertIcon,ShieldCheckIcon} from 'lucide-react';
import {cn} from '@/lib/utils';
import {menuContentClass,menuItemClass} from '../shared/workbench-styles';
import type {PermissionMode} from '@/lib/workbench/types';

const modes=[
 {value:'ask',label:'需要时询问',Icon:ShieldIcon},
 {value:'auto',label:'自动审核',Icon:ShieldCheckIcon},
 {value:'bypass',label:'全部允许',Icon:ShieldAlertIcon},
] as const;

// Quiet layout: the trigger is the mode's shield plus its name, and the menu
// hugs its content instead of the old fixed 12rem panel.
export function PermissionPicker({value,blocked,onChange}:{value:PermissionMode;blocked:boolean;onChange:(mode:PermissionMode)=>Promise<void>}){
 const [open,setOpen]=useState(false);
 const current=modes.find(mode=>mode.value===value);
 const Icon=current?.Icon;
 return <Menu.Root open={open} onOpenChange={setOpen}>
  <Menu.Trigger disabled={blocked||!current} aria-label={`权限模式：${current?.label??'未知'}`} render={<button type="button" className={cn('text-muted-foreground hover:text-foreground hover:bg-muted/60 rounded-control flex h-7 shrink-0 items-center gap-1.5 px-1 text-[13px] transition-colors disabled:opacity-40',value==='bypass'&&'text-orange-600 dark:text-orange-400')}/>}>
   {Icon&&<Icon className="size-3.5 shrink-0"/>}
   <span className="whitespace-nowrap">{current?.label??'权限模式'}</span>
  </Menu.Trigger>
  <Menu.Portal><Menu.Positioner side="top" align="start" sideOffset={6} className="z-50 outline-none"><Menu.Popup className={cn(menuContentClass,'w-max min-w-[140px] rounded-xl shadow-lg')}>
   <Menu.RadioGroup value={value} onValueChange={next=>{setOpen(false);if(blocked||next===value||!modes.some(mode=>mode.value===next))return;void onChange(next as PermissionMode).catch(()=>{/* store surfaces the error */});}}>
    {modes.map(({value:mode,label,Icon:ModeIcon})=><Menu.RadioItem key={mode} value={mode} disabled={blocked} className={cn(menuItemClass,'h-8 gap-2 rounded-lg px-2 whitespace-nowrap data-[checked]:bg-foreground/[0.04] data-[disabled]:opacity-40',mode==='bypass'&&'text-orange-600 dark:text-orange-400')}>
     <ModeIcon className="size-3.5 shrink-0 opacity-70"/>
     <span className="flex-1">{label}</span>
     <Menu.RadioItemIndicator><CheckIcon className="size-3.5 shrink-0"/></Menu.RadioItemIndicator>
    </Menu.RadioItem>)}
   </Menu.RadioGroup>
  </Menu.Popup></Menu.Positioner></Menu.Portal>
 </Menu.Root>;
}
