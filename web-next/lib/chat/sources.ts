import type { ThreadMessageLike } from '@assistant-ui/react';

type Part = Exclude<ThreadMessageLike['content'], string>[number];
export type SourcePart = Extract<Part, { type: 'source'; sourceType: 'url' }>;

const MARKDOWN_LINK = /\[([^\]]{1,160})\]\((https?:\/\/[^\s)]+)\)/g;
// Chinese text often follows a URL with no space; stop at CJK and fullwidth
// punctuation instead of swallowing the sentence.
const BARE_URL = /https?:\/\/[^\s<>"'`）)\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+/g;

function trimUrl(url: string): string {
  return url.replace(/[),.;:!?，。；：！？、）】」』]+$/, '');
}

function cleanJsonTitle(title: string): string {
  return title
    .replace(/[*_`]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^["“](.*)["”]$/, '$1');
}

function cleanTitle(title: string): string {
  const cleaned = title
    .replace(/[*_`]/g, '')
    .replace(/^[\s"'“”‘’\[\]]+/, '')
    .replace(/[\s"'“”‘’\[\]]+$/, '')
    .trim();
  // A title that still carries bracket syntax came from malformed source
  // markup; the hostname is a safer label.
  return /[\[\]]/.test(cleaned) ? '' : cleaned;
}

/**
 * Pull citable URLs out of a web_search tool result. Search providers return
 * either markdown links or bare URLs, often with trailing punctuation.
 */
function fromSearchJson(text: string, limit: number): SourcePart[] | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith('{')) return null;
  let data: unknown;
  try {
    data = JSON.parse(trimmed);
  } catch {
    return null;
  }
  const results = (data as { results?: unknown })?.results;
  if (!Array.isArray(results)) return null;
  const found: SourcePart[] = [];
  const seen = new Set<string>();
  for (const entry of results) {
    if (found.length >= limit) break;
    if (!entry || typeof entry !== 'object') continue;
    const url = (entry as { url?: unknown }).url;
    if (typeof url !== 'string') continue;
    const clean = trimUrl(url);
    if (!/^https?:\/\//i.test(clean) || seen.has(clean)) continue;
    seen.add(clean);
    const rawTitle = (entry as { title?: unknown }).title;
    const title = typeof rawTitle === 'string' ? cleanJsonTitle(rawTitle).slice(0, 160) : '';
    found.push({
      type: 'source',
      sourceType: 'url',
      id: clean,
      url: clean,
      ...(title ? { title } : {}),
    });
  }
  return found;
}

export function extractSources(text: string, limit = 6): SourcePart[] {
  if (!text) return [];
  const structured = fromSearchJson(text, limit);
  if (structured) return structured;
  const found: SourcePart[] = [];
  const seen = new Set<string>();
  const add = (url: string, title?: string) => {
    if (found.length >= limit) return;
    const clean = trimUrl(url);
    if (!/^https?:\/\//i.test(clean) || seen.has(clean)) return;
    seen.add(clean);
    const label = title ? cleanTitle(title) : '';
    found.push({
      type: 'source',
      sourceType: 'url',
      id: clean,
      url: clean,
      ...(label ? { title: label } : {}),
    });
  };
  for (const match of text.matchAll(MARKDOWN_LINK)) add(match[2]!, match[1]);
  if (found.length < limit) for (const match of text.matchAll(BARE_URL)) add(match[0]);
  return found;
}

export const SEARCH_TOOL_NAMES = new Set(['web_search', 'web_search_exa']);

/** Whether the answer text actually contains this URL (not a longer URL that starts with it). */
export function isUrlCited(text: string, url: string): boolean {
  let from = 0;
  for (;;) {
    const at = text.indexOf(url, from);
    if (at === -1) return false;
    const next = text[at + url.length];
    if (next === undefined || !/[\w#?/-]/.test(next)) return true;
    from = at + url.length;
  }
}
