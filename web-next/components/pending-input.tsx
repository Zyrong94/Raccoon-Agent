"use client";
import {useRef,useState} from 'react';
import {ApprovalCard} from '@/components/assistant-ui/elements/approval-card';
import {ElicitationForm} from '@/components/assistant-ui/elements/elicitation-form';
import {ReviewableDiff} from '@/components/assistant-ui/elements/reviewable-diff';
import type {DiffLine} from '@/components/assistant-ui/elements/code-diff';
import type {Pending} from '@/lib/workbench/types';

// Inline decision card. Permission prompts use the compact official ApprovalCard
// (command chip + Deny / Always allow / Allow once); ask_user keeps its form.
export function PendingInput({pending,disabled,answer}:{pending:Pending;disabled:boolean;answer:(id:string,text:string)=>Promise<void>}){
  const [text,setText]=useState('');const [selected,setSelected]=useState('');const [submitting,setSubmitting]=useState(false);const [error,setError]=useState('');const lock=useRef(false);
  const view=pending.presentation;const permission=pending.kind==='permission_confirmation';const options=permission&&view?view.options:pending.options;
  const review=pending.payload.prewrite_review as {files?:{path:string;diff:string}[];error?:string}|undefined;
  async function submit(value:string){if(disabled||lock.current||!value.trim())return;lock.current=true;setSubmitting(true);setError('');try{await answer(pending.id,value);}catch(e){setError((e as Error).message);}finally{lock.current=false;setSubmitting(false);}}

  if(permission){
    const match=(pattern:RegExp)=>options.find(option=>pattern.test(option.id)||pattern.test(option.label));
    const deny=match(/拒绝|deny/i);
    const always=match(/始终|always/i);
    const once=match(/本次|一次|once/i)??options.find(option=>option!==deny&&option!==always);
    const mapped=[deny,always,once].filter((option):option is NonNullable<typeof deny>=>Boolean(option));
    const slots=mapped.length===options.length&&options.length>0&&new Set(mapped).size===options.length;
    const card={state:'request' as const,title:view?.title||pending.question,subtitle:view?.description||'需要你确认后继续',command:view?.target};
    return <section aria-label="待审批操作" className="flex flex-col gap-2">
      <fieldset disabled={disabled||submitting} className="min-w-0">
        {slots?<ApprovalCard {...card} onDeny={deny?()=>void submit(deny.id):undefined} onAlwaysAllow={always?()=>void submit(always.id):undefined} onAllowOnce={once?()=>void submit(once.id):undefined} denyLabel={deny?.label} alwaysAllowLabel={always?.label} allowOnceLabel={once?.label}/>
          :<ApprovalCard {...card} options={options.map(option=>({...option,onSelect:()=>void submit(option.id)}))}/>}
        {view?.details?<details className="mt-2 text-xs">
          <summary className="text-muted-foreground cursor-pointer select-none">操作详情</summary>
          <pre className="bg-foreground/[0.04] mt-2 overflow-auto rounded-lg p-3 whitespace-pre-wrap break-all">{view.details}</pre>
        </details>:null}
        {review?.error?<pre role="alert" className="text-destructive mt-2 text-xs whitespace-pre-wrap">{review.error}</pre>:null}
        {review?.files?.length?<details className="mt-2 text-xs">
          <summary className="text-muted-foreground cursor-pointer select-none">查看改动（{review.files.length} 个文件）</summary>
          <div className="mt-2 space-y-2">{review.files.map(file=><ReviewableDiff key={file.path} readOnly filename={file.path} className="max-w-none" hunks={[{id:file.path,range:'完整差异',decision:'pending',lines:file.diff.split('\n').map(line=>({kind:line.startsWith('+')&&!line.startsWith('+++')?'added':line.startsWith('-')&&!line.startsWith('---')?'removed':'context',text:/^[+-](?![+-]{2})/.test(line)?line.slice(1):line}) as DiffLine)}]}/>)}</div>
        </details>:null}
      </fieldset>
      {submitting&&<p role="status" className="text-muted-foreground text-xs">正在提交回答…</p>}
      {error&&<p role="alert" className="text-destructive text-sm">{error}</p>}
    </section>;
  }

  return <section aria-label="等待回答" className="flex flex-col gap-3">
    <fieldset disabled={disabled||submitting} className="min-w-0 space-y-3">
      <ElicitationForm server="Raccoon" message={pending.question} state="request"
        fields={[...(options.length?[{name:'choice',label:'选项',value:selected,kind:'choice' as const,options:options.map(o=>o.label)}]:[]),{name:'text',label:'自由回答',value:text,kind:'text'}]}
        onFieldChange={(name,value)=>name==='text'?setText(value):setSelected(value)} onAccept={()=>void submit(text.trim()||options.find(o=>o.label===selected)?.id||'')}/ >
      {options.some(o=>o.description)&&<dl className="text-muted-foreground space-y-1 text-xs">{options.filter(o=>o.description).map(o=><div key={o.id}><dt>{o.label}</dt><dd>{o.description}</dd></div>)}</dl>}
    </fieldset>
    {submitting&&<p role="status" className="text-muted-foreground text-xs">正在提交回答…</p>}
    {error&&<p role="alert" className="text-destructive text-sm">{error}</p>}
  </section>;
}
