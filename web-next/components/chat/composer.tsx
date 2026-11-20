"use client";

import {ComposerPrimitive,useAui,useAuiState} from '@assistant-ui/react';
import {useEffect,useLayoutEffect,useRef,useSyncExternalStore} from 'react';
import {CheckIcon,XIcon} from 'lucide-react';
import {useWorkbenchView} from '../workbench/context';
import {ComposerFrame,ComposerInput} from './composer-frame';
import {ProjectPicker} from '../workbench/project-picker';

const emptySubscribe=()=>()=>{};
const emptyView=()=>null;

export function Composer(){
 const {modelControl,blocked:scopeBlocked,workbench,chatUnavailable}=useWorkbenchView();
 const view=useSyncExternalStore(workbench?.store.subscribe??emptySubscribe,workbench?.store.get??emptyView,workbench?.store.get??emptyView);
 const state=view?.snapshot;
 const blocked=!!chatUnavailable||(view?!view.connected||view.navigating||!!state?.busy&&!state.cancellable&&state.status!=='stopping'||!!state?.pending:scopeBlocked);
 const composing=useRef(false);
 const aui=useAui();
 const captureSettings=()=>{
   if(!workbench||!aui.composer().getState().canSend)return;
   const {model,permissionMode}=workbench.store.composerSettings();
   // The SDK carries runConfig through asynchronous attachment uploads.
   aui.composer().setRunConfig({custom:{selection:{model,permissionMode}}});
 };
 return <ComposerPrimitive.Root className="relative w-full">
  <ProjectPicker/>
  <ComposerFrame blocked={blocked} controls={modelControl} pendingMessages={state?.pending_messages} stopping={state?.status==='stopping'} beforeSend={captureSettings}
   input={<ComposerInput disabled={view?.navigating||chatUnavailable} aria-label="消息输入" placeholder="输入消息…"
    onKeyDown={event=>{
      if(event.key!=='Enter')return;
      if(composing.current||event.nativeEvent.isComposing||event.keyCode===229){event.preventDefault();return;}
      if(!event.shiftKey)captureSettings();
    }}
    onCompositionStart={()=>{composing.current=true;}} onCompositionEnd={()=>{composing.current=false;}}/>}/>
 </ComposerPrimitive.Root>;
}

export function EditComposer(){
 const {blocked:scopeBlocked,chatUnavailable}=useWorkbenchView();
 const blocked=scopeBlocked||!!chatUnavailable;
 const attachments=useAuiState(state=>state.composer.attachments);
 const text=useAuiState(state=>state.composer.text);
 const editorRef=useRef<HTMLTextAreaElement>(null);
 // The edit box replaces the bubble in place; a collapsing textarea would shrink
 // the row and throw the reader's scroll position, so grow it to fit the text.
 useLayoutEffect(()=>{
  const element=editorRef.current;if(!element)return;
  element.style.height='auto';
  element.style.height=`${element.scrollHeight}px`;
 },[text]);
 // Focusing without scroll keeps the surrounding messages where the reader left them.
 useEffect(()=>{editorRef.current?.focus({preventScroll:true});},[]);
 // The row spans the whole pane, so the edit box needs the thread column wrapper
 // to line up with the thread instead of the right edge of the viewport.
 return <div className="mx-auto flex w-full max-w-(--thread-max-width) justify-end">
  <ComposerPrimitive.Root className="bg-muted rounded-thread w-[80%] px-4 py-2">
   <ComposerPrimitive.Input asChild aria-label="编辑消息" disabled={blocked} addAttachmentOnPaste={false} className="max-h-64 w-full resize-none bg-transparent text-[15px] leading-relaxed outline-none break-all">
    <textarea ref={editorRef} rows={1}/>
   </ComposerPrimitive.Input>
   {attachments.length>0&&<p className="text-muted-foreground mt-1 text-xs">保留原附件：{attachments.map(attachment=>attachment.name).join('、')}</p>}
   <div className="mt-1.5 flex items-center justify-end gap-1.5">
    <ComposerPrimitive.Cancel aria-label="取消编辑" className="text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] grid size-6 place-items-center rounded-md transition-colors">
     <XIcon className="size-3.5" strokeWidth={1.8}/>
    </ComposerPrimitive.Cancel>
    <ComposerPrimitive.Send aria-label="发送" disabled={blocked} className="text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] grid size-6 place-items-center rounded-md transition-colors disabled:pointer-events-none disabled:opacity-40">
     <CheckIcon className="size-3.5" strokeWidth={1.8}/>
    </ComposerPrimitive.Send>
   </div>
  </ComposerPrimitive.Root>
 </div>;
}
