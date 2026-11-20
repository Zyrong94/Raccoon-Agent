import { describe, expect, it } from 'vitest';
import { extractSources } from './sources';

describe('web search sources', () => {
  it('extracts markdown links and bare urls, deduped and without trailing punctuation', () => {
    const text = `- [宁德天气](https://weather.com.cn/ningde) 显示多云。
参考 https://example.com/a?b=1。另见 https://weather.com.cn/ningde`;
    const sources = extractSources(text);
    expect(sources.map((source) => source.url)).toEqual([
      'https://weather.com.cn/ningde',
      'https://example.com/a?b=1',
    ]);
    expect(sources[0]!.title).toBe('宁德天气');
  });

  it('caps the list and ignores non-http text', () => {
    const text = Array.from({ length: 9 }, (_, index) => `https://example.com/${index}`).join(' ') + ' ftp://nope';
    expect(extractSources(text)).toHaveLength(6);
    expect(extractSources('')).toEqual([]);
  });

  it('parses structured search json and keeps its titles', () => {
    const text = JSON.stringify({ results: [
      { url: 'https://x.com/a', title: 'A on X: "hello"' },
      { url: 'https://b.com', title: 'B' },
    ] });
    expect(extractSources(text).map((source) => source.title)).toEqual(['A on X: "hello"', 'B']);
  });

  it('strips quote and bracket syntax from malformed link titles', () => {
    const [source] = extractSources('[" [雷达图](https://example.com/a)');
    expect(source?.title).toBe('雷达图');
  });
});
