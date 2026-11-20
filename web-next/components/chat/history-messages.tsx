"use client";

// Composition from assistant-ui's with-virtualized-thread example; see docs/home-demo-provenance.md.
import {createThreadRowsSelector, MessagePrimitive, ThreadPrimitive, useAuiState, type AssistantState, type ThreadRow} from '@assistant-ui/react';
import {defaultRangeExtractor, useVirtualizer, type VirtualItem} from '@tanstack/react-virtual';
import {Fragment, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode} from 'react';
import {ArrowDownIcon} from 'lucide-react';
import {cn} from '@/lib/utils';
import {TraceLine} from '@/components/shared/trace-line';
import {AssistantMessage, AssistantMessageFooter, groupAssistantParts, renderAssistantPart, UserMessage} from './messages';
import {EditComposer} from './composer';
import {RowResidencyProvider} from './row-residency';
import {WorkbenchContext} from '../workbench/context';
import {ParallelReplies} from '../collaboration/parallel-replies';

type Props={scope:string;empty?:ReactNode;footer:ReactNode;isEmpty?:boolean};
const AT_BOTTOM=4;

function AssistantPartRow({info}:{info:Extract<ThreadPrimitive.Row.RenderInfo,{type:'part'}>}){
  const last=useAuiState(s=>s.message.parts.length-1);
  return <MessagePrimitive.Root data-role="assistant" className="mx-auto w-full max-w-(--thread-max-width)">
    <div className="text-[15px] leading-relaxed wrap-break-word">{renderAssistantPart(info)}</div>
    {info.row.indices.includes(last)&&<AssistantMessageFooter/>}
  </MessagePrimitive.Root>;
}

const renderRow=(activeEditId:string|null):ThreadPrimitive.Row.Props['children']=>info=>{
  if(info.type==='message'){
    // Every message owns a composer, so an older edit would keep a second editor
    // open; only the newest one gets the edit form.
    if(info.message.composer.isEditing&&info.message.id===activeEditId)return <EditComposer/>;
    if(info.message.role==='user')return <UserMessage/>;
    if(info.message.role==='assistant')return <AssistantMessage/>;
    return null;
  }
  if(info.type==='turn-end')return null;
  // Row invokes this renderer for nested group children too; only the root owns a footer.
  const root=!info.row.group||info.part.type===info.row.group&&'indices' in info.part&&info.part.indices.length===info.row.indices.length;
  return root?<AssistantPartRow info={info}/>:renderAssistantPart(info);
};

function MeasuredRow({item,row,measure,retainRow,onFocusRow,activeEditId}:{item:VirtualItem;row:ThreadRow;measure:(element:HTMLDivElement|null)=>void;retainRow:(key:string,active:boolean)=>void;onFocusRow:(key:string|null)=>void;activeEditId:string|null}){
  const retain=useCallback((active:boolean)=>retainRow(row.key,active),[retainRow,row.key]);
  const render=useCallback(renderRow(activeEditId),[activeEditId]);
  return <div ref={measure} data-index={item.index} data-virtual-row-key={row.key}
    className={row.type==='message'?'pt-3':'py-1'}
    onFocusCapture={()=>onFocusRow(row.key)} onBlurCapture={event=>{if(!event.currentTarget.contains(event.relatedTarget))onFocusRow(null);}}>
    <RowResidencyProvider retain={retain}><ThreadPrimitive.Row row={row}>{render}</ThreadPrimitive.Row></RowResidencyProvider>
  </div>;
}

export function HistoryMessages(props:Props){return <VirtualHistory key={props.scope} {...props}/>;}

function VirtualHistory({scope,empty,footer,isEmpty=false}:Props){
  const [selectRows]=useState(()=>{
    const select=createThreadRowsSelector({groupBy:groupAssistantParts});
    let previous:readonly ThreadRow[]=[];
    const emptyRows=new Map<string,ThreadRow>();
    return (state:AssistantState)=>{
      const byMessage=new Map<string,ThreadRow[]>();
      for(const row of select(state))if(row.type!=='turn-end'){
        const group=byMessage.get(row.messageId)??[];group.push(row);byMessage.set(row.messageId,group);
      }
      // The SDK has no part row for an empty failed reply; retain its error and retry actions.
      const next=state.thread.messages.flatMap(message=>{
        if(message.role!=='assistant'||message.parts.length)return byMessage.get(message.id)??[];
        let row=emptyRows.get(message.id);
        if(!row){row={type:'message',key:`message:${message.id}`,messageId:message.id,turnMessageId:message.id};emptyRows.set(message.id,row);}
        return [row];
      });
      if(next.length===previous.length&&next.every((row,index)=>row===previous[index]))return previous;
      previous=next;return next;
    };
  });
  const rows=useAuiState(selectRows);
  const editing=useAuiState(s=>s.thread.messages.filter(message=>message.composer.isEditing).map(message=>message.id).join('\0'));
  const activeEditId=editing?editing.split('\0').at(-1)!:null;
  const isRunning=useAuiState(s=>s.thread.isRunning);
  const runStatus=useContext(WorkbenchContext)?.runStatus;
  const phase=runStatus?.run_phase;
  const waitingForTools=isRunning&&phase==='waiting_tools';
  const needsIndicator=useAuiState(s=>{
    const last=s.thread.messages.at(-1);
    if(!s.thread.isRunning||(phase!==undefined&&phase!=='thinking'))return false;
    if(last?.role==='assistant'&&!last.parts.length)return false;
    const tail=last?.parts.at(-1);
    if(tail?.type==='reasoning'&&tail.status?.type==='running')return false;
    return phase==='thinking'||(tail?.type!=='text'&&tail?.type!=='reasoning');
  });
  const scroller=useRef<HTMLDivElement>(null),content=useRef<HTMLDivElement>(null),sticky=useRef(true);
  const [isAtBottom,setIsAtBottom]=useState(true);
  const [retained,setRetained]=useState<Map<string,number>>(()=>new Map());
  const [focused,setFocused]=useState<string|null>(null);
  const retainRow=useCallback((key:string,active:boolean)=>setRetained(previous=>{
    const next=new Map(previous),count=(next.get(key)??0)+(active?1:-1);
    if(count>0)next.set(key,count);else next.delete(key);
    return next;
  }),[]);
  const rangeExtractor=useCallback((range:Parameters<typeof defaultRangeExtractor>[0])=>{
    const indices=new Set(defaultRangeExtractor(range)),edited=new Set(editing.split('\0'));
    rows.forEach((row,index)=>{if(retained.has(row.key)||focused===row.key||edited.has(row.messageId))indices.add(index);});
    return [...indices].sort((a,b)=>a-b);
  },[rows,retained,focused,editing]);
  const virtualizer=useVirtualizer<HTMLDivElement,HTMLDivElement>({
    count:rows.length,getItemKey:index=>rows[index]!.key,estimateSize:()=>80,
    getScrollElement:()=>scroller.current,initialRect:{height:800,width:800},overscan:4,rangeExtractor,
    scrollToFn:(offset,{adjustments=0,behavior},instance)=>{
      const element=instance.scrollElement;if(!element)return;
      const top=offset+adjustments,max=element.scrollHeight-element.clientHeight;
      if(sticky.current&&max-element.scrollTop<=AT_BOTTOM&&top<max)return;
      element.scrollTo({top,...(behavior?{behavior}:{})});
    },
  });
  // Reading intent also cancels an initial/end alignment still waiting for measured sizes.
  const disarm=useCallback(()=>{sticky.current=false;virtualizer.cancelScroll();},[virtualizer]);
  const jumpToBottom=useCallback(()=>{
    sticky.current=true;setIsAtBottom(true);
    if(rows.length)virtualizer.scrollToIndex(rows.length-1,{align:'end'});
    requestAnimationFrame(()=>{const element=scroller.current;if(element&&sticky.current)element.scrollTop=element.scrollHeight;});
  },[rows.length,virtualizer]);
  useEffect(()=>{
    const element=scroller.current;if(!element)return;
    let top=element.scrollTop,height=element.scrollHeight,client=element.clientHeight;
    const scroll=()=>{
      const bottom=element.scrollHeight-element.scrollTop-element.clientHeight<=AT_BOTTOM;
      // A collapse can clamp upward to the bottom; only a downward return resumes follow.
      if(bottom&&element.scrollTop>top&&element.scrollHeight===height&&Math.abs(element.clientHeight-client)<=1)sticky.current=true;
      else if(element.scrollTop<top&&element.scrollHeight===height&&Math.abs(element.clientHeight-client)<=1)disarm();
      top=element.scrollTop;height=element.scrollHeight;client=element.clientHeight;setIsAtBottom(bottom);
    };
    const wheel=(event:WheelEvent)=>{
      if(event.deltaY<0)disarm();
      else if(event.deltaY>0&&element.scrollHeight-element.scrollTop-element.clientHeight<=AT_BOTTOM)sticky.current=true;
    };
    const key=(event:KeyboardEvent)=>{
      if((event.target as HTMLElement).closest('input,textarea,select,[contenteditable="true"]'))return;
      if(event.key==='Home'){
        event.preventDefault();disarm();setIsAtBottom(false);
        // Native keyboard scrolling animates through estimates; newly measured rows move its target.
        virtualizer.scrollToIndex(0,{align:'start',behavior:'auto'});
        element.scrollTop=0;
        requestAnimationFrame(()=>{if(!sticky.current)element.scrollTop=0;});
        return;
      }
      if(event.key==='End'){event.preventDefault();jumpToBottom();return;}
      if(['ArrowUp','PageUp'].includes(event.key)||event.key===' '&&event.shiftKey)disarm();
    };
    element.addEventListener('scroll',scroll,{passive:true});element.addEventListener('wheel',wheel,{passive:true});
    element.addEventListener('touchmove',disarm,{passive:true});element.addEventListener('keydown',key);
    return()=>{element.removeEventListener('scroll',scroll);element.removeEventListener('wheel',wheel);element.removeEventListener('touchmove',disarm);element.removeEventListener('keydown',key);};
  },[jumpToBottom,virtualizer,disarm]);
  useEffect(()=>{
    const element=scroller.current,body=content.current;if(!element||!body)return;
    const observer=new ResizeObserver(()=>{
      if(sticky.current)element.scrollTop=element.scrollHeight;
      setIsAtBottom(element.scrollHeight-element.scrollTop-element.clientHeight<=AT_BOTTOM);
    });
    observer.observe(body);observer.observe(element);return()=>observer.disconnect();
  },[]);
  const initialized=useRef(false),previousRunning=useRef(false);
  useLayoutEffect(()=>{
    if(rows.length&&!initialized.current){initialized.current=true;jumpToBottom();}
    if(isRunning&&!previousRunning.current)jumpToBottom();
    previousRunning.current=isRunning;
  },[rows.length,isRunning,jumpToBottom]);
  const items=virtualizer.getVirtualItems();
  return <div className={cn('flex h-full min-h-0 flex-col',isEmpty&&'justify-center')}>
    <div ref={scroller} data-history-viewport role="region" aria-label="聊天记录" tabIndex={0}
      onClickCapture={event=>{if((event.target as HTMLElement).closest('[aria-expanded]'))disarm();}}
      className={cn('relative overflow-y-auto overscroll-contain [scrollbar-gutter:stable_both-edges] px-4 outline-offset-[-2px] md:px-6',!isEmpty&&'min-h-0 flex-1')}
      style={{overflowAnchor:'none'}}>
      <div ref={content} className="py-4">
        {empty}
        <div data-thread-history={scope} data-total-rows={rows.length}>
          {items.map((item,index)=><Fragment key={item.key}>
            <div aria-hidden="true" style={{height:Math.max(0,item.start-(items[index-1]?.end??0))}}/>
            <MeasuredRow item={item} row={rows[item.index]!} measure={virtualizer.measureElement} retainRow={retainRow} onFocusRow={setFocused} activeEditId={activeEditId}/>
          </Fragment>)}
          <div aria-hidden="true" style={{height:Math.max(0,virtualizer.getTotalSize()-(items.at(-1)?.end??0))}}/>
        </div>
        <ParallelReplies replies={runStatus?.parallel_replies}/>
        {(waitingForTools||needsIndicator)&&<div className="mx-auto w-full max-w-(--thread-max-width)" role="status"><TraceLine label={waitingForTools?'等待后台工具完成':'thinking'}/></div>}
      </div>
    </div>
    <div className="bg-background relative mx-auto flex w-full max-w-(--thread-max-width) shrink-0 flex-col gap-3 px-4 pb-5 md:px-0">
      {!isAtBottom&&<button type="button" onClick={jumpToBottom} aria-label="滚动到最新消息" className="border-foreground/10 bg-background hover:border-foreground/25 rounded-control absolute -top-11 z-10 grid size-8 place-items-center self-center border transition-colors"><ArrowDownIcon className="size-4"/></button>}
      {footer}
    </div>
  </div>;
}
