// Adapted from assistant-ui/apps/docs/components/pages/home/demo/messages.tsx; see docs/home-demo-provenance.md.
"use client";

import {
  ActionBarPrimitive,
  AuiIf,
  type AssistantState,
  ErrorPrimitive,
  type FileMessagePartComponent,
  groupPartByType,
  type ImageMessagePartComponent,
  MessagePrimitive,
  useAuiState,
} from "@assistant-ui/react";
import { CheckIcon, CopyIcon, SquarePenIcon } from "lucide-react";
import {useContext,type ReactNode} from "react";
import {WorkbenchContext} from '../workbench/context';
import { UserMessageAttachments } from "@/components/assistant-ui/elements/attachment.aui";

import { File } from "@/components/assistant-ui/elements/file";
import { Image } from "@/components/assistant-ui/elements/image";
import { MarkdownText } from "@/components/assistant-ui/elements/markdown-text";

import {
  Reasoning,
  ReasoningContent,
  ReasoningRoot,
  ReasoningText,
} from "@/components/assistant-ui/elements/reasoning.aui";
import { TraceLine } from "@/components/shared/trace-line";

import { cn } from "@/lib/utils";
import { AssistantActionBar, BranchPicker } from "./action-bar";
import {ToolCall} from "./tool-call";
import {ToolTraceGroupView,displayToolStatus,toolGroupFailed,toolGroupLabel} from "./tool-trace-group";
import { Sources } from "./sources";
import { userActionButtonClass } from "../shared/workbench-styles";
import { DisclosureTrigger, disclosureContentClass } from "./trace";

export const groupAssistantParts = groupPartByType({
  reasoning: ["group-chainOfThought", "group-reasoning"],
  source: ["group-chainOfThought", "group-source"],
  "tool-call": ["group-chainOfThought"],
  "standalone-tool-call": [],
});

const getMessageErrorText = (s: AssistantState): string | undefined => {
  const status = s.message.status;
  if (status?.type !== "incomplete" || status.reason !== "error") {
    return undefined;
  }
  const error = status.error;
  if (typeof error === "string") return error;
  if (
    typeof error === "object" &&
    error !== null &&
    "message" in error &&
    typeof error.message === "string"
  ) {
    return error.message;
  }
  return undefined;
};

const UserFilePart: FileMessagePartComponent = (part) => (
  <div className="py-1">
    <File {...part} />
  </div>
);

const UserImagePart: ImageMessagePartComponent = (part) => (
  <div className="py-1">
    <Image {...part} />
  </div>
);

export function UserMessage(): ReactNode {
  const integration=useContext(WorkbenchContext);
  return (
    <MessagePrimitive.Root
      data-role="user"
      className="animate-in fade-in slide-in-from-bottom-1 mx-auto flex w-full max-w-(--thread-max-width) flex-col items-end duration-150"
    >
      <div className="w-full has-[.aui-attachment-root]:mb-2">
        <UserMessageAttachments />
      </div>
      <div className="max-w-[80%]">
        <div className="bg-muted rounded-thread px-4 py-2 text-[15px] whitespace-pre-wrap break-all empty:hidden">
          <MessagePrimitive.Parts
            components={{
              Text: ({text}) => <>{text}</>,
              File: UserFilePart,
              Image: UserImagePart,
            }}
          />
        </div>
      </div>
      <div className="mt-0.5 flex h-6 items-center justify-end gap-2">
        <BranchPicker />
        <ActionBarPrimitive.Root
          hideWhenRunning
          autohide="not-last"
          className="flex items-center gap-1.5"
        >
          <ActionBarPrimitive.Copy aria-label="复制消息" className={userActionButtonClass}>
            <AuiIf condition={(s) => s.message.isCopied}>
              <CheckIcon className="size-3.5" strokeWidth={1.8} />
            </AuiIf>
            <AuiIf condition={(s) => !s.message.isCopied}>
              <CopyIcon className="size-3.5" strokeWidth={1.8} />
            </AuiIf>
          </ActionBarPrimitive.Copy>
          <AuiIf condition={(s)=>s.thread.capabilities.edit&&s.message.metadata.custom.canRevise!==false}><ActionBarPrimitive.Edit
            aria-label="编辑消息"
            disabled={integration?.blocked}
            className={userActionButtonClass}
          >
            <SquarePenIcon className="size-3.5" strokeWidth={1.8} />
          </ActionBarPrimitive.Edit></AuiIf>
        </ActionBarPrimitive.Root>
      </div>
    </MessagePrimitive.Root>
  );
}

export function AssistantMessage(): ReactNode {
  return (
    <MessagePrimitive.Root
      data-role="assistant"
      className="animate-in fade-in slide-in-from-bottom-1 mx-auto w-full max-w-(--thread-max-width) duration-150"
    >
      <div className="text-[15px] leading-relaxed wrap-break-word">
        <MessagePrimitive.GroupedParts groupBy={groupAssistantParts}>
          {renderAssistantPart}
        </MessagePrimitive.GroupedParts>
        <Sources />
        <MessageStopped />
        <MessageError />
      </div>
      <AssistantMessageActions />
    </MessagePrimitive.Root>
  );
}

export function renderAssistantPart({part, children}: MessagePrimitive.GroupedParts.RenderInfo): ReactNode {
  switch (part.type) {
    case "group-chainOfThought":
      return <ToolTraceGroup indices={part.indices}>{children}</ToolTraceGroup>;
    case "group-reasoning": {
      const running = part.status.type === "running";
      return (
        <ReasoningRoot
          variant="ghost"
          streaming={running}
          className="my-1 mb-3"
        >
          <DisclosureTrigger
            live={running}
            label={running ? "thinking" : "reasoning"}
          />
          <ReasoningContent
            aria-busy={running}
            className={disclosureContentClass}
          >
            <ReasoningText className="ps-0">{children}</ReasoningText>
          </ReasoningContent>
        </ReasoningRoot>
      );
    }
    case "group-source":
      return null;
    case "text":
      return part.text === "" && part.status?.type === "running" ? (
        <ThinkingTrace />
      ) : (
        <MarkdownText />
      );
    case "reasoning":
      return <Reasoning {...part} />;
    case "source":
      return null;
    case "tool-call":
      return part.toolUI ?? <ToolCall {...part} />;
    case "data":
      return part.dataRendererUI;
    case "image":
      return (
        <div className="py-1">
          <Image {...part} />
        </div>
      );
    case "file":
      return (
        <div className="py-1">
          <File {...part} />
        </div>
      );
    case "indicator":
      return <ThinkingTrace />;
    default:
      return null;
  }
}

function ThinkingTrace(){
  const phase=useContext(WorkbenchContext)?.runStatus?.run_phase;
  return phase===undefined||phase==='thinking'?<TraceLine label="thinking"/>:null;
}

export function AssistantMessageFooter(): ReactNode {
  return <><Sources /><MessageStopped /><MessageError /><AssistantMessageActions /></>;
}

function ToolTraceGroup({indices,children}:{indices:readonly number[];children:ReactNode}){
  const count=useAuiState(s=>indices.filter(index=>s.message.content[index]?.type==='tool-call').length);
  const failed=useAuiState(s=>toolGroupFailed(indices.map(index=>{
    const part=s.message.parts[index];
    return part?.type==='tool-call'?{status:part.status,isError:part.isError}:{};
  })));
  const runningToolIds=useContext(WorkbenchContext)?.runStatus?.running_tool_call_ids;
  const label=useAuiState(s=>toolGroupLabel(indices.map(index=>{
    const part=s.message.parts[index];
    return part?.type==='tool-call'?{status:displayToolStatus(part.status,part.toolCallId,part.result,runningToolIds),isError:part.isError}:{};
  })));
  return <ToolTraceGroupView label={label} count={count} failed={failed}>{children}</ToolTraceGroupView>;
}

// Tool-only turns are rows, not replies: copy/export/branch controls there are
// noise and add a reserved strip under every tool line.
function AssistantMessageActions(): ReactNode {
  const toolOnly = useAuiState((s) => {
    const content = s.message.content;
    return content.length > 0 && content.every((part) => part.type === "tool-call"||part.type==='data');
  });
  const incomplete=useAuiState(state=>state.message.status?.type==='incomplete');
  if (toolOnly&&!incomplete) return null;
  // The bar itself unmounts while auto-hidden, so this row owns the height:
  // a reserved line keeps hover-reveal from resizing a virtualized row.
  return (
    <div className="mt-1.5 flex h-5 items-center gap-1.5">
      <BranchPicker />
      <AssistantActionBar />
    </div>
  );
}

// 停止生成后保留已经输出的正文，只补一行说明；正文本身不写入“已停止”字样，
// 因此历史记录和下一轮上下文里仍然是模型真实写出的内容。
function MessageStopped(): ReactNode {
  const stopped = useAuiState(
    (s) => s.message.status?.type === "incomplete" && s.message.status.reason === "cancelled",
  );
  if (!stopped) return null;
  return <p className="text-muted-foreground mt-2 text-xs leading-5">已停止生成</p>;
}

function MessageError(): ReactNode {
  const errorText = useAuiState(getMessageErrorText);

  return (
    <MessagePrimitive.Error>
      <ErrorPrimitive.Root
        className={cn(
          "mt-2 border-l-2 pl-3 text-[13px]",
          "border-destructive/60 text-destructive",
        )}
      >
        {errorText !== undefined ? (
          <p className="line-clamp-2">{errorText}</p>
        ) : (
          <ErrorPrimitive.Message className="line-clamp-2" />
        )}
      </ErrorPrimitive.Root>
    </MessagePrimitive.Error>
  );
}
