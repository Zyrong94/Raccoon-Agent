// Adapted from assistant-ui/apps/docs/components/pages/home/demo/styles.ts; see docs/home-demo-provenance.md.
// Compact icon chrome: 20px hit box with a 14px glyph, matching the version
// steppers so the footer row stays one short line instead of a button strip.
export const actionButtonClass =
  "text-muted-foreground/70 hover:text-foreground hover:bg-foreground/[0.06] disabled:pointer-events-none disabled:opacity-40 grid size-5 place-items-center rounded-md transition-colors";

// User messages keep one quiet action row under the bubble so the pencil stops
// floating in the thread gutter.
export const userActionButtonClass =
  "text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] disabled:pointer-events-none disabled:opacity-40 grid size-6 place-items-center rounded-md transition-colors";

export const menuContentClass =
  "bg-popover text-popover-foreground border-foreground/10 data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:animate-in data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:animate-out rounded-surface z-50 min-w-28 overflow-hidden border p-1";

export const menuItemClass =
  "hover:bg-muted focus:bg-muted flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-[13px] outline-none select-none";
