"use client";

// Adapter for the official Sources element (pill + expandable cards).
// Source parts are produced by lib/chat/messages.ts from web_search
// results; this map only shapes them for the element.
import { useState } from "react";
import { useAuiState } from "@assistant-ui/react";
import {
  Sources as SourcesView,
  type Source,
} from "@/components/assistant-ui/elements/sources";
import { hostOf } from "@/components/assistant-ui/utils/href";

export function Sources() {
  const content = useAuiState((s) => s.message.content);
  const [open, setOpen] = useState(false);
  const seen = new Set<string>();
  const sources: Source[] = [];
  for (const part of content) {
    if (part.type !== "source" || part.sourceType !== "url" || seen.has(part.url)) continue;
    seen.add(part.url);
    sources.push({
      title: part.title || hostOf(part.url) || part.url,
      url: part.url,
    });
  }
  if (!sources.length) return null;
  return (
    <SourcesView
      sources={sources}
      open={open}
      onOpenChange={setOpen}
      locale="zh-CN"
      className="mt-1.5"
    />
  );
}
