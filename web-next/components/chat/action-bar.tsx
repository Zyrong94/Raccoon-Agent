// Adapted from assistant-ui/apps/docs/components/pages/home/demo/action-bar.tsx; see docs/home-demo-provenance.md.
"use client";

import {
  ActionBarMorePrimitive,
  ActionBarPrimitive,
  AuiIf,
  BranchPickerPrimitive,
  useAuiState,
} from "@assistant-ui/react";
import {
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CopyIcon,
  DownloadIcon,
  MoreHorizontalIcon,
  RefreshCwIcon,
  SquareIcon,
  Volume2Icon,
} from "lucide-react";
import {useContext,useState,type ReactNode} from "react";
import {WorkbenchContext} from '../workbench/context';
import {MessageVersions} from './message-versions';


import { actionButtonClass, menuContentClass, menuItemClass } from "../shared/workbench-styles";
import { cn } from "@/lib/utils";

export function AssistantActionBar(): ReactNode {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const integration=useContext(WorkbenchContext);
  const currentVersionControls=useAuiState(state=>state.message.metadata.custom.currentVersionControls===true);
  const retry=useAuiState(state=>state.message.status?.type==='incomplete');

  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide={feedbackOpen ? "never" : "not-last"}
      className="flex items-center gap-1"
    >
      {currentVersionControls&&<MessageVersions showRetry={false}/>}
      <ActionBarPrimitive.Copy
        aria-label="复制回复"
        className={actionButtonClass}
      >
        <AuiIf condition={(s) => s.message.isCopied}>
          <CheckIcon className="size-3.5" strokeWidth={1.8} />
        </AuiIf>
        <AuiIf condition={(s) => !s.message.isCopied}>
          <CopyIcon className="size-3.5" strokeWidth={1.8} />
        </AuiIf>
      </ActionBarPrimitive.Copy>
      
      <AuiIf
        condition={(s) =>
          s.thread.capabilities.speech && s.message.speech == null
        }
      >
        <ActionBarPrimitive.Speak
          aria-label="Read aloud"
          className={actionButtonClass}
        >
          <Volume2Icon className="size-3.5" strokeWidth={1.8} />
        </ActionBarPrimitive.Speak>
      </AuiIf>
      <AuiIf condition={(s) => s.message.speech != null}>
        <ActionBarPrimitive.StopSpeaking
          aria-label="Stop reading"
          className={cn(actionButtonClass, "text-foreground")}
        >
          <SquareIcon className="size-3 fill-current" />
        </ActionBarPrimitive.StopSpeaking>
      </AuiIf>
      <AuiIf condition={(s)=>s.thread.capabilities.reload&&s.message.metadata.custom.canRevise!==false}><ActionBarPrimitive.Reload
        aria-label={retry?'重试回答':'重新生成回答'}
        title={retry?'重试回答':'重新生成回答'}
        disabled={integration?.blocked}
        className={actionButtonClass}
      >
        <RefreshCwIcon className="size-3.5" strokeWidth={1.8} />
      </ActionBarPrimitive.Reload></AuiIf>
      <ActionBarMorePrimitive.Root>
        <ActionBarMorePrimitive.Trigger asChild>
          <button
            type="button"
            aria-label="更多消息操作"
            className={cn(
              actionButtonClass,
              "data-[state=open]:text-foreground",
            )}
          >
            <MoreHorizontalIcon className="size-3.5" strokeWidth={1.8} />
          </button>
        </ActionBarMorePrimitive.Trigger>
        <ActionBarMorePrimitive.Content
          side="bottom"
          align="start"
          sideOffset={6}
          className={menuContentClass}
        >
          <ActionBarPrimitive.ExportMarkdown asChild>
            <ActionBarMorePrimitive.Item className={menuItemClass}>
              <DownloadIcon className="size-3.5" />
              导出 Markdown
            </ActionBarMorePrimitive.Item>
          </ActionBarPrimitive.ExportMarkdown>
        </ActionBarMorePrimitive.Content>
      </ActionBarMorePrimitive.Root>

    </ActionBarPrimitive.Root>
  );
}

export function BranchPicker({ className }: { className?: string }): ReactNode {
  return (
    <AuiIf condition={(s)=>s.thread.capabilities.switchToBranch}>
    <BranchPickerPrimitive.Root
      hideWhenSingleBranch
      className={cn(
        "text-muted-foreground inline-flex items-center font-mono text-[11px] tabular-nums",
        className,
      )}
    >
      <BranchPickerPrimitive.Previous
        aria-label="Previous version"
        className={actionButtonClass}
      >
        <ChevronLeftIcon className="size-3.5" strokeWidth={1.8} />
      </BranchPickerPrimitive.Previous>
      <span>
        <BranchPickerPrimitive.Number /> / <BranchPickerPrimitive.Count />
      </span>
      <BranchPickerPrimitive.Next
        aria-label="Next version"
        className={actionButtonClass}
      >
        <ChevronRightIcon className="size-3.5" strokeWidth={1.8} />
      </BranchPickerPrimitive.Next>
    </BranchPickerPrimitive.Root>
    </AuiIf>
  );
}
