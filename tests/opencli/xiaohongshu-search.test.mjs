import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import test from 'node:test';

// Optional local adapter checks: npm install --prefix .raccoon/tools/opencli --save-dev jsdom@29.0.2
const packageRoot = resolve('.raccoon/tools/opencli/node_modules/@jackwener/opencli');
const require = createRequire(pathToFileURL(resolve('.raccoon/tools/opencli/package.json')));
const { JSDOM } = require('jsdom');
const { command } = await import(pathToFileURL(resolve(packageRoot, 'clis/xiaohongshu/search.js')));

function pageFixture({ copies = 1, hidden = false, separated = false, wait = 'content' } = {}) {
  const dom = new JSDOM(`<body>
    <div class="search-layout__top"><div class="filter"><div class="filter-panel">
      <div class="filters"><span>排序依据</span><div class="tag-container">
        ${Array.from({ length: copies }, (_, i) => `<div class="tags active" ${hidden && i ? 'aria-hidden="true"' : ''}>最新</div>`).join('')}
      </div></div>
    </div></div></div>
    <section class="note-item"><a href="/explore/68e90be80000000004022e66">咖啡</a></section>
  </body>`, { url: 'https://www.xiaohongshu.com/search_result?keyword=test' });
  const document = dom.window.document;
  let panelClicks = 0;
  const options = [...document.querySelectorAll('.tags')];
  for (const element of document.querySelectorAll('*')) {
    const top = separated && options.indexOf(element) > 0 ? 120 : 0;
    element.getBoundingClientRect = () => ({ left: 0, top, width: 100, height: 30 });
  }
  document.querySelector('.filter').addEventListener('click', () => panelClicks++);
  Object.defineProperty(document.body, 'innerText', { get: () => document.body.textContent });
  const calls = [];
  const page = {
    goto: async url => { calls.push(url); },
    evaluate: async script => {
      if (script.includes('findNoteCard')) return wait;
      if (script.includes('const requestedFilters =')) {
        return Function('document', 'window', 'getComputedStyle', `return (${script});`)(
          document, dom.window, dom.window.getComputedStyle.bind(dom.window),
        );
      }
      if (script.includes('const targetCount =')) return {
        rows: [{ title: '咖啡', author: '作者', likes: '1', author_url: '', url: 'https://www.xiaohongshu.com/explore/68e90be80000000004022e66?xsec_token=test' }],
        diag: { securityBlock: false, stopReason: 'target', scrollHeight: 900, clientHeight: 600, cardCount: 1, feedClientHeight: 600, distinctCardTops: 1 },
      };
      throw new Error('Unexpected browser script');
    },
  };
  return { page, calls, document, panelClicks: () => panelClicks };
}

test('default search does not inspect or open a filter panel', async () => {
  const fixture = pageFixture();
  fixture.document.querySelector('.filter-panel').remove();
  // A click would show the old implementation tried to apply defaults.
  const result = await command.func(fixture.page, { query: '咖啡', limit: 1 });
  assert.equal(result[0].title, '咖啡');
  assert.equal(fixture.panelClicks(), 0);
});

test('explicit defaults also leave the panel alone', async () => {
  const fixture = pageFixture();
  fixture.document.querySelector('.filter-panel').remove();
  const result = await command.func(fixture.page, { query: '咖啡', limit: 1, sort: 'comprehensive', 'note-type': 'all', 'publish-time': 'anytime', scope: 'all', location: 'all' });
  assert.equal(result.length, 1);
  assert.equal(fixture.panelClicks(), 0);
});

test('an explicit filter accepts overlapping copies of one option', async () => {
  const fixture = pageFixture({ copies: 2 });
  assert.equal((await command.func(fixture.page, { query: '咖啡', limit: 1, sort: 'latest' })).length, 1);
});

test('an explicit filter ignores an aria-hidden duplicate at another position', async () => {
  const fixture = pageFixture({ copies: 2, hidden: true, separated: true });
  assert.equal((await command.func(fixture.page, { query: '咖啡', limit: 1, sort: 'latest' })).length, 1);
});

test('distinct visible options remain an ambiguity error', async () => {
  const fixture = pageFixture({ copies: 2, separated: true });
  await assert.rejects(command.func(fixture.page, { query: '咖啡', limit: 1, sort: 'latest' }), /ambiguous_option/);
});

test('invalid filter is rejected before navigating', async () => {
  const fixture = pageFixture();
  await assert.rejects(command.func(fixture.page, { query: '咖啡', limit: 1, sort: 'oldest' }), /--sort/);
  assert.equal(fixture.calls.length, 0);
});

test('login walls and rate limits retain their typed errors', async () => {
  for (const [wait, code] of [['login_wall', 'AUTH_REQUIRED'], ['security_block', 'SECURITY_BLOCK']]) {
    const fixture = pageFixture({ wait });
    await assert.rejects(command.func(fixture.page, { query: '咖啡', limit: 1 }), error => error.code === code);
  }
});
