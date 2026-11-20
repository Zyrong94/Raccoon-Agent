"use client";

export const threadCommands = [
  { id: "new", label: "新建会话", shortcut: "Alt ⇧ O" },
  { id: "previous", label: "上一个会话", shortcut: "Alt ↑" },
  { id: "next", label: "下一个会话", shortcut: "Alt ↓" },
  { id: "archive", label: "归档 / 恢复", shortcut: "Alt ⇧ X" },
  { id: "pin", label: "置顶 / 取消置顶", shortcut: "Alt ⇧ P" },
  { id: "sidebar", label: "切换会话侧栏", shortcut: "Alt ⇧ H" },
  { id: "composer", label: "聚焦输入框", shortcut: "Alt ⇧ C" },
] as const;

export type ThreadCommand = (typeof threadCommands)[number]["id"];

export function getThreadShortcut(
  event: KeyboardEvent,
): ThreadCommand | undefined {
  if (event.defaultPrevented || event.isComposing || event.repeat) return;
  if (event.altKey && event.shiftKey && !event.ctrlKey && !event.metaKey)
    return (
      {
        KeyO: "new",
        KeyX: "archive",
        KeyH: "sidebar",
        KeyP: "pin",
        KeyC: "composer",
      } as Record<string, ThreadCommand | undefined>
    )[event.code];
  if (event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
    if (event.key === "ArrowUp") return "previous";
    if (event.key === "ArrowDown") return "next";
  }
  return undefined;
}
