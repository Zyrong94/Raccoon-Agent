"use client";

import {AuiIf,ComposerPrimitive,useAui,useAuiState} from '@assistant-ui/react';
import {useRef,type ComponentProps,type ReactNode,type Ref} from 'react';
import {ArrowUpIcon,MicIcon,PlusIcon,SquareIcon} from 'lucide-react';
import {ComposerAttachments} from '@/components/assistant-ui/elements/attachment.aui';
import {cn} from '@/lib/utils';
import type {PendingMessage} from '@/lib/workbench/types';

export function ComposerInput({inputRef,onKeyDown,...props}:ComponentProps<'textarea'>&{inputRef?:Ref<HTMLTextAreaElement>}){
 const aui=useAui();
 return <ComposerPrimitive.Input asChild onKeyDown={event=>{onKeyDown?.(event);if(event.defaultPrevented||event.key!=='Enter'||event.shiftKey||event.nativeEvent.isComposing||event.keyCode===229)return;if(aui.thread().getState().isRunning){event.preventDefault();aui.composer().send();}}}><textarea {...props} ref={inputRef} data-composer-input rows={1} className="placeholder:text-muted-foreground/70 field-sizing-content max-h-48 w-full resize-none bg-transparent px-4 pt-3.5 pb-1.5 text-[15px] leading-6 break-all focus:outline-none"/></ComposerPrimitive.Input>;
}

function CancelGeneration({label}:{label:string}){
 return <ComposerPrimitive.Cancel asChild><button type="button" aria-label={label} className="bg-primary text-primary-foreground rounded-control grid size-7 place-items-center disabled:opacity-40"><SquareIcon className="size-3 fill-current"/></button></ComposerPrimitive.Cancel>;
}

function SendMessage({label,cancelLabel,beforeSend}:{label:string;cancelLabel:string;beforeSend?:()=>void}){
 const aui=useAui(),canSend=useAuiState(state=>state.composer.canSend),running=useAuiState(state=>state.thread.isRunning),empty=useAuiState(state=>state.composer.isEmpty);
 // 运行中且输入框为空（无文字、无附件）时，主按钮就是停止；开始输入后自动换回发送键。
 // 这样“停止”不会一直占着一个按钮，也不会出现“空输入还能点火”的歧义。
 if(running&&empty)return <CancelGeneration label={cancelLabel}/>;
 // Dim on "nothing to send" only. `canSend` also goes false for the ~200ms write
 // behind a control switch, and fading the button for that reads as a flash.
 return <button type="button" aria-label={label} disabled={!canSend} onClick={()=>{beforeSend?.();aui.composer().send();}} className={cn('bg-primary text-primary-foreground rounded-control grid size-7 place-items-center transition-opacity',empty&&'opacity-40')}><ArrowUpIcon className="size-4"/></button>;
}

// Owns the file input instead of ComposerPrimitive.AddAttachment: that primitive
// disables itself from `thread.isDisabled || !composer.isEditing`, which flips for
// a frame on every snapshot and made the plus button flash.
function AddAttachmentButton({disabled}:{disabled:boolean}){
 const aui=useAui();
 const inputRef=useRef<HTMLInputElement>(null);
 return <>
  <button type="button" aria-label="添加图片或文件" title="添加图片或文件" disabled={disabled} onClick={()=>inputRef.current?.click()} className="text-muted-foreground hover:text-foreground rounded-control grid size-7 shrink-0 place-items-center transition-colors disabled:opacity-40"><PlusIcon className="size-4"/></button>
  <input ref={inputRef} type="file" multiple hidden tabIndex={-1} aria-hidden onChange={async event=>{
   const files=Array.from(event.target.files??[]);
   event.target.value='';
   for(const file of files){try{await aui.composer().addAttachment(file);}catch{/* the runtime reports upload errors */}}
  }}/>
 </>;
}

// Both personal and project conversations use the official homepage composer layout.
export function ComposerFrame({input,controls,blocked,attachments=true,sendControl,sendLabel='发送消息',cancelLabel='停止生成',pendingMessages=[],stopping=false,beforeSend}:{input:ReactNode;controls:ReactNode;blocked:boolean;attachments?:boolean;sendControl?:ReactNode;sendLabel?:string;cancelLabel?:string;pendingMessages?:PendingMessage[];stopping?:boolean;beforeSend?:()=>void}){
 const running=useAuiState(state=>state.thread.isRunning);
 return <>{(pendingMessages.length>0||stopping)&&<div role="status" className="text-muted-foreground mb-2 px-1 text-xs">{stopping?'正在停止当前任务…':`${pendingMessages.length} 条待处理消息`}{pendingMessages.length>0&&<ul className="mt-1 max-h-24 space-y-1 overflow-y-auto">{pendingMessages.map(message=><li key={message.id} className="truncate" title={message.text}>{(message.mode==='auto'?'自动处理':message.mode==='steer'?'下个模型步骤':'下一轮处理')} · {message.text||'附件消息'}</li>)}</ul>}</div>}<ComposerPrimitive.AttachmentDropzone asChild disabled={blocked||!attachments}>
  <div className="border-foreground/10 bg-background focus-within:border-foreground/20 data-[dragging=true]:border-foreground/40 flex flex-col rounded-[14px] border shadow-sm transition-colors data-[dragging=true]:border-dashed">
   {attachments&&<div className="has-[.aui-attachment-root]:px-3 has-[.aui-attachment-root]:pt-3"><ComposerAttachments/></div>}
   {input}
   <div className="flex items-center justify-between gap-2 px-2 pb-2">
    <div className="flex min-w-0 items-center gap-0.5">
     {attachments&&<AddAttachmentButton disabled={blocked}/>}
     {controls}
    </div>
    <div className="flex shrink-0 items-center gap-1">
     <AuiIf condition={s=>s.thread.capabilities.dictation&&s.composer.dictation==null}><ComposerPrimitive.Dictate asChild><button type="button" aria-label="语音输入" className="text-muted-foreground hover:text-foreground rounded-control grid size-7 place-items-center transition-colors disabled:opacity-40"><MicIcon className="size-4"/></button></ComposerPrimitive.Dictate></AuiIf>
     <AuiIf condition={s=>s.composer.dictation!=null}><ComposerPrimitive.StopDictation asChild><button type="button" aria-label="停止语音输入" className="text-destructive rounded-control grid size-7 place-items-center"><SquareIcon className="size-3 animate-pulse fill-current"/></button></ComposerPrimitive.StopDictation></AuiIf>
     {sendControl?<>{sendControl}{running&&<CancelGeneration label={cancelLabel}/>}</>:<SendMessage label={sendLabel} cancelLabel={cancelLabel} beforeSend={beforeSend}/>}
    </div>
   </div>
  </div>
 </ComposerPrimitive.AttachmentDropzone></>;
}
