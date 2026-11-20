"use client";

import type { ReactNode } from "react";
import { ShimmerLabel } from "@/components/assistant-ui/elements/surfaces";
import { cn } from "@/lib/utils";

/**
 * The mark every trace line carries. A disclosure rotates it and a plain line
 * does not, so the two read as the same grammar rather than two symbols; it
 * stays a glyph rather than an icon because an icon on a line that cannot open
 * reads as an affordance.
 */
export function TraceMarker({
  live,
  className,
}: {
  live: boolean;
  className?: string;
}): ReactNode {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block shrink-0 transition-transform",
        live ? "text-blue-500" : "text-muted-foreground/50",
        className,
      )}
    >
      {">"}
    </span>
  );
}

export function TraceLine({ label }: { label: string }): ReactNode {
  return (
    <div className="my-1 flex items-baseline gap-2 font-mono text-[12px] [font-variant-ligatures:none]">
      <TraceMarker live />
      <span className="text-muted-foreground min-w-0 flex-1 truncate">
        <ShimmerLabel>{label}</ShimmerLabel>
      </span>
    </div>
  );
}
