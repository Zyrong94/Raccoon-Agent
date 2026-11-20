"use client";

// One tool-call row presentation for both the live parent thread and the
// read-only subagent transcript, so the two never drift into different formats.
import type {
  ToolCallMessagePartProps,
  ToolCallMessagePartStatus,
} from "@assistant-ui/react";
import { FileText, Search, Terminal, Wrench } from "lucide-react";
import { ToolFallback } from "@/components/assistant-ui/elements/tool-fallback.aui";
import {
  formatToolValue,
  ToolStatusCard,
  ToolTraceCard,
} from "./tool-trace";

export type ToolRowStatus = ToolCallMessagePartStatus;

export type ToolRow = {
  id: string;
  name: string;
  args?: Record<string, unknown> | undefined;
  argsText?: string | undefined;
  result?: unknown;
  isError?: boolean | undefined;
  status?: ToolRowStatus | undefined;
};

type ToolMeta = { label: string; active: string; arg?: string };

const TOOL_META: Record<string, ToolMeta> = {
  view: { label: "读取文件", active: "读取文件中", arg: "path" },
  read_multi: { label: "读取多个文件", active: "读取多个文件中", arg: "paths" },
  write: { label: "写入文件", active: "写入文件中", arg: "path" },
  edit: { label: "修改文件", active: "修改文件中", arg: "path" },
  apply_patch: { label: "应用补丁", active: "应用补丁中", arg: "patch" },
  delete: { label: "删除文件", active: "删除文件中", arg: "path" },
  ls: { label: "查看目录", active: "查看目录中", arg: "path" },
  tree: { label: "查看目录树", active: "查看目录树中", arg: "path" },
  glob: { label: "查找文件", active: "查找文件中", arg: "pattern" },
  grep: { label: "搜索内容", active: "搜索内容中", arg: "pattern" },
  shell: { label: "运行命令", active: "运行命令中", arg: "command" },
  python_exec: { label: "运行 Python", active: "运行 Python 中", arg: "code" },
  diagnostics: { label: "运行检查", active: "运行检查中", arg: "command" },
  git_status: { label: "查看 Git 状态", active: "查看 Git 状态中" },
  git_diff: { label: "查看 Git 差异", active: "查看 Git 差异中" },
  git_log: { label: "查看 Git 历史", active: "查看 Git 历史中" },
  web_search: { label: "搜索网页", active: "搜索网页中", arg: "query" },
  fetch: { label: "抓取网页", active: "抓取网页中", arg: "url" },
  ask_user: { label: "询问用户", active: "等待回答" },
  load_skill: { label: "加载技能", active: "加载技能中", arg: "name" },
};

function oneLine(value: unknown, limit = 120): string {
  if (value === null || value === undefined) return "";
  const text =
    typeof value === "string"
      ? value
      : Array.isArray(value)
        ? value.join(", ")
        : (JSON.stringify(value) ?? "");
  const line = text.replace(/\s+/g, " ").trim();
  return line.length > limit ? `${line.slice(0, limit)}…` : line;
}

function requestText(row: ToolRow): string {
  if (row.args && Object.keys(row.args).length) return formatToolValue(row.args);
  return row.argsText?.trim() ? formatToolValue(row.argsText) : "{}";
}

function humanizeToolName(name: string): string {
  if (name.startsWith("mcp_"))
    return `MCP · ${name.slice(4).replace(/[_-]+/g, " ").trim()}`;
  return name.replace(/[_-]+/g, " ").trim();
}

const PREFERRED_ARG_KEYS = [
  "path",
  "file_path",
  "command",
  "pattern",
  "query",
  "url",
  "name",
  "prompt",
];

function pickArgKey(
  args: Record<string, unknown> | undefined,
  preferred?: string,
): string | undefined {
  if (!args) return undefined;
  if (preferred && typeof args[preferred] === "string") return preferred;
  const preferredKey = PREFERRED_ARG_KEYS.find(
    (key) => typeof args[key] === "string",
  );
  if (preferredKey) return preferredKey;
  return Object.keys(args).find((key) => typeof args[key] === "string");
}

export function toToolRow(props: ToolCallMessagePartProps): ToolRow {
  return {
    id: props.toolCallId,
    name: props.toolName,
    args: props.args as Record<string, unknown> | undefined,
    argsText: props.argsText,
    result: props.result,
    isError: props.isError,
    status: props.isPreliminary ? { type: "running" } : props.status,
  };
}

export function ToolRowView({ row }: { row: ToolRow }) {
  const meta = TOOL_META[row.name];
  const label = meta?.label ?? humanizeToolName(row.name);
  const args = row.args;
  const argKey = pickArgKey(args, meta?.arg);
  const query = argKey && args ? oneLine(args[argKey]) : "";
  const running = row.status?.type === "running";
  const icon =
    row.name === "shell" || row.name === "python_exec" ? (
      <Terminal />
    ) : row.name.includes("search") || row.name === "grep" ? (
      <Search />
    ) : meta?.arg === "path" ? (
      <FileText />
    ) : (
      <Wrench />
    );
  const result = formatToolValue(row.result);

  // A child transcript has no runtime to answer approvals, so an unfinished
  // call reuses the parent's cancelled/failed presentation without callbacks.
  if (row.status?.type === "incomplete") {
    const fallback = {
      toolCallId: row.id,
      toolName: row.name,
      args: row.args ?? {},
      argsText: row.argsText,
      result: row.result,
      isError: row.isError,
      status: row.status,
    } as ToolCallMessagePartProps;
    return <ToolFallback {...fallback} />;
  }

  if (running && !row.isError)
    return (
      <ToolStatusCard
        icon={icon}
        signature={meta?.active ?? `${label}…`}
        message={query}
        loading
      />
    );
  return (
    <ToolTraceCard
      icon={icon}
      signature={label}
      description={row.isError ? oneLine(result) : query}
      parameters={requestText(row)}
      result={result || "没有返回内容"}
      error={row.isError}
    />
  );
}
