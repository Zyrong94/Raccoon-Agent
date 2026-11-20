// Adapted from assistant-ui/apps/docs/components/pages/home/demo/thread.tsx; see docs/home-demo-provenance.md.
"use client";

import {
  type AssistantState,
  AuiIf,
  ThreadPrimitive,
  useAuiState,
} from "@assistant-ui/react";
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { Composer } from "./composer";

import { useWorkbenchView } from "../workbench/context";
import { Greeting } from "./greeting";
import { HistoryMessages } from "./history-messages";
import { FollowUps, Suggestions } from "./suggestions";
const isNewChatView = (s: AssistantState) =>
  s.thread.messages.length === 0 &&
  (!s.thread.isLoading || s.threads.isLoading);

const isHistoryLoadingView = (s: AssistantState) =>
  s.thread.messages.length === 0 &&
  s.thread.isLoading &&
  !s.thread.isDisabled &&
  !s.threads.isLoading;

export function Thread(): ReactNode {
  const isEmpty = useAuiState(isNewChatView);
  const threadId = useAuiState((s) => s.threads.mainThreadId);
  const { pendingSlot } = useWorkbenchView();

  return (
    <ThreadPrimitive.Root className="flex h-full flex-col pt-6 md:pt-0">
      <HistoryMessages scope={threadId} isEmpty={isEmpty} empty={<>
        <AuiIf condition={isNewChatView}>
          <Greeting />
        </AuiIf>
        <AuiIf condition={isHistoryLoadingView}>
          <div
            role="status"
            className="animate-in fade-in fill-mode-both mx-auto flex w-full max-w-(--thread-max-width) flex-col gap-y-6 [animation-delay:150ms] [animation-duration:200ms]"
          >
            <span className="sr-only">Loading conversation</span>
            <Skeleton className="rounded-thread ml-auto h-9 w-2/5 motion-reduce:animate-none" />
            <div className="flex flex-col gap-y-2">
              <Skeleton className="h-4 w-11/12 motion-reduce:animate-none" />
              <Skeleton className="h-4 w-4/5 motion-reduce:animate-none" />
              <Skeleton className="h-4 w-3/5 motion-reduce:animate-none" />
            </div>
            <Skeleton className="rounded-thread ml-auto h-9 w-1/3 motion-reduce:animate-none" />
            <div className="flex flex-col gap-y-2">
              <Skeleton className="h-4 w-10/12 motion-reduce:animate-none" />
              <Skeleton className="h-4 w-2/3 motion-reduce:animate-none" />
            </div>
          </div>
        </AuiIf>

        </>} footer={<>
          <FollowUps />
          {pendingSlot}
          <Composer />
          <AuiIf condition={isNewChatView}>
            <Suggestions />
          </AuiIf>
        </>} />
    </ThreadPrimitive.Root>
  );
}
