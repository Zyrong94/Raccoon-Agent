// Adapted from assistant-ui/apps/docs/components/pages/home/demo/suggestions.tsx; see docs/home-demo-provenance.md.
"use client";

import { AuiIf, ThreadPrimitive, useAuiState } from "@assistant-ui/react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { toggleTheme } from "../workbench/theme";

const SUGGESTIONS = [
 {label:'了解项目结构',prompt:'介绍一下这个项目的结构'},
 {label:'解释运行流程',prompt:'解释项目的核心运行流程'},
 {label:'分析报错',prompt:'帮我定位并分析一个错误'},
 {label:'检查代码',prompt:'帮我检查代码中可能存在的问题'},
 {label:'编写测试',prompt:'帮我为一个功能编写测试'},
 {label:'切换深色模式',prompt:'切换深色模式'},
 {label:'绘制流程图',prompt:'用 Mermaid 绘制这个项目的运行流程'},
 {label:'规划新功能',prompt:'帮我规划一个功能的实现步骤'},
 {label:'解释一段代码',prompt:'帮我解释一段代码'}
];

const suggestionChipClass =
  "border-foreground/10 bg-background text-muted-foreground hover:border-foreground/25 hover:text-foreground rounded-control h-8 border px-3 text-[13px] transition-colors";

export function Suggestions(): ReactNode {
  const isComposerEmpty = useAuiState((s) => s.composer.isEmpty);

  return (
    // Typing hides the suggestions without unmounting them: the empty view is
    // centred, so collapsing a block this tall moves the composer out from
    // under the cursor. inert keeps the hidden chips out of the tab order.
    <div
      inert={!isComposerEmpty}
      className={cn(
        "animate-in fade-in mx-auto flex max-w-[34rem] flex-wrap items-center justify-center gap-2 transition-opacity duration-200 motion-reduce:animate-none",
        !isComposerEmpty && "opacity-0",
      )}
    >
      {SUGGESTIONS.map((suggestion) => (
        suggestion.prompt==='切换深色模式' ? <button key={suggestion.prompt} type="button" onClick={()=>{toggleTheme();}} className={suggestionChipClass}>{suggestion.label}</button> : <ThreadPrimitive.Suggestion
          key={suggestion.prompt}
          prompt={suggestion.prompt}
          className={suggestionChipClass}
        >
          {suggestion.label}
        </ThreadPrimitive.Suggestion>
      ))}
    </div>
  );
}

export function FollowUps(): ReactNode {
  const suggestions = useAuiState((s) => s.thread.suggestions);

  return (
    <AuiIf
      condition={(s) =>
        !s.thread.isEmpty &&
        !s.thread.isRunning &&
        s.thread.suggestions.length > 0
      }
    >
      <div className="animate-in fade-in flex [scrollbar-width:none] gap-2 overflow-x-auto duration-200 motion-reduce:animate-none [&::-webkit-scrollbar]:hidden">
        {suggestions.map((suggestion) => (
          <ThreadPrimitive.Suggestion
            key={suggestion.prompt}
            prompt={suggestion.prompt}
              className={cn(suggestionChipClass, "shrink-0 whitespace-nowrap")}
          >
            {suggestion.prompt}
          </ThreadPrimitive.Suggestion>
        ))}
      </div>
    </AuiIf>
  );
}
