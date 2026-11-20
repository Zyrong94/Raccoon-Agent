"use client";

import Image from 'next/image';
import {CheckIcon} from 'lucide-react';
import {
 ModelSelectorRoot,
 ModelSelectorTrigger,
 ModelSelectorContent,
 ModelSelectorFocusAnchor,
 ModelSelectorList,
 ModelSelectorItem,
 type ModelOption,
} from '@/components/assistant-ui/elements/model-selector.aui';
import type {Snapshot} from '@/lib/workbench/types';

type Option=ModelOption&{provider:string};

const providerLabels:Record<string,string>={openai:'OpenAI',anthropic:'Anthropic',deepseek:'DeepSeek',google:'Google',moonshot:'Moonshot',xai:'xAI'};

function providerLabel(id:string){
 return providerLabels[id.toLowerCase()]??id;
}

// Backend labels arrive as ids ("gpt-6-luna"); show a readable name instead
// ("GPT-6 Luna") without touching the ref used for selection.
function modelName(label:string){
 const cleaned=label.replace(/^gpt/i,'GPT');
 const cut=cleaned.lastIndexOf('-');
 if(cut<1)return cleaned;
 const tail=cleaned.slice(cut+1);
 if(!/^[a-z][a-z0-9]*$/.test(tail))return cleaned;
 return `${cleaned.slice(0,cut)} ${tail[0].toUpperCase()}${tail.slice(1)}`;
}

// Quiet layout: text-weight trigger, one-line rows with the provider on the
// right and a fixed check column so names never shift when the selection moves.
export function BackendModelPicker({state,blocked,onChange}:{state:Pick<Snapshot,'model'|'models'>;blocked:boolean;onChange:(model:string)=>void}){
 const options:Option[]=state.models.map(model=>{
  const parts=model.ref.split('/');
  const leaf=parts.at(-1)??'';
  return {
   id:model.ref,
   name:modelName(model.label),
   provider:parts.length>1?providerLabel(parts[0]):'',
   ...(/^gpt-/i.test(leaf)?{icon:<Image src="/icons/openai.svg" alt="" aria-hidden width={14} height={14} className="size-3.5"/>}:{}),
  };
 });
 const available=options.length>0;
 const selected=options.find(option=>option.id===state.model);
 const shown:Option[]=available?options:[{id:'unconfigured',name:'未配置模型',provider:'',disabled:true}];
 return <fieldset disabled={blocked||!available} className="flex min-w-0 items-center gap-1 border-0 p-0" aria-label="模型选择">
  <ModelSelectorRoot models={shown} value={selected?.id} onValueChange={model=>{if(!blocked&&available)onChange(model);}}>
   <ModelSelectorTrigger variant="ghost" size="sm"
    className="text-muted-foreground hover:text-foreground hover:bg-muted/60 flex h-7 min-w-0 items-center gap-1.5 rounded-lg px-1 text-[13px] font-normal [&>svg]:size-3 [&>svg]:opacity-50">
    <span className="flex min-w-0 items-center gap-1.5">
     {selected?.icon}
     <span className="max-w-36 truncate">{selected?.name??(available?state.model:'未配置模型')??'未配置模型'}</span>
    </span>
   </ModelSelectorTrigger>
   <ModelSelectorContent align="start" sideOffset={8} className="border-foreground/10 w-[252px] min-w-0 rounded-xl border p-1.5 shadow-lg">
    <ModelSelectorFocusAnchor/>
    <ModelSelectorList>
     {shown.map(option=><ModelSelectorItem key={option.id} model={option}
      className="data-[selected=true]:bg-muted/50 items-center gap-2 rounded-lg py-1.5 ps-2 pe-2 text-[13px] [&>span.absolute]:hidden">
      <span className="flex w-full min-w-0 items-center gap-2">
       <span aria-hidden className="grid size-3.5 shrink-0 place-items-center">{option.icon}</span>
       <span className="min-w-0 flex-1 truncate">{option.name}</span>
       {option.provider&&<span className="text-muted-foreground/70 shrink-0 text-[11px]">{option.provider}</span>}
       <span aria-hidden className="grid size-3.5 shrink-0 place-items-center">{option.id===selected?.id&&<CheckIcon className="size-3.5"/>}</span>
      </span>
     </ModelSelectorItem>)}
    </ModelSelectorList>
   </ModelSelectorContent>
  </ModelSelectorRoot>
 </fieldset>;
}
