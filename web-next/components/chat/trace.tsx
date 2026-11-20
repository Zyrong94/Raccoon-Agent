// Adapted from assistant-ui/apps/docs/components/pages/home/demo/trace.tsx; see docs/home-demo-provenance.md.
"use client";

import type { ReactNode } from "react";
import { ShimmerLabel } from "@/components/assistant-ui/elements/surfaces";
import { CollapsibleTrigger } from "@/components/assistant-ui/elements/collapsible-root";
import { TraceMarker } from "@/components/shared/trace-line";

export const disclosureContentClass =
  "border-foreground/10 ms-[5px] border-s ps-4";

export function DisclosureTrigger({
  live,
  label,
  detail,
  error=false,
}: {
  live: boolean;
  label: string;
  detail?: string;
  error?: boolean;
}): ReactNode {
  return (
    <CollapsibleTrigger className="group/trigger my-1 flex max-w-full items-baseline gap-2 text-left font-mono text-[12px] outline-none [font-variant-ligatures:none] focus-visible:underline">
      <TraceMarker
        live={live}
        className="group-data-open/trigger:rotate-90 group-data-panel-open/trigger:rotate-90"
      />
      <span className={error?"text-destructive min-w-0 truncate transition-colors":"text-muted-foreground group-hover/trigger:text-foreground min-w-0 truncate transition-colors"}>
        {live ? (
          <ShimmerLabel>
            {label}
            {detail ? ` ${detail}` : null}
          </ShimmerLabel>
        ) : (
          <>
            {label}
            {detail ? ` ${detail}` : null}
          </>
        )}
      </span>
    </CollapsibleTrigger>
  );
}
