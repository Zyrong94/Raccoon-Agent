// Adapted from assistant-ui/apps/docs/components/pages/home/demo/greeting.tsx; see docs/home-demo-provenance.md.
"use client";

import type {ReactNode} from 'react';
export function Greeting():ReactNode {
 return <div className="animate-in fade-in slide-in-from-bottom-1 mx-auto mb-8 flex w-full max-w-(--thread-max-width) flex-col items-center text-center duration-200">
  <p className="font-display text-[1.5rem] leading-snug font-[550] tracking-[-0.01em] text-balance lg:text-[1.75rem]">今天需要我帮你做什么？</p>
 </div>;
}
