import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { catalogMatches, mountFocusControls, resolveTerms, splitTerms } from '../src/focus_controls.mjs';

const catalog = [
  {key:'industry:881001.TI',name:'消费电子'},
  {key:'cn_concept:886001.TI',name:'消费电子'},
  {key:'cn_concept:886002.TI',name:'光刻胶'},
  {key:'cn_concept:886003.TI',name:'AI手机'},
];

test('paste mixed punctuation and deduplicate', () => {
  assert.deepEqual(splitTerms('光刻胶、AI手机，光刻胶; 物理AI\n'), ['光刻胶','AI手机','物理AI']);
});
test('ambiguous same name and unavailable fine theme stay unresolved', () => {
  assert.deepEqual(resolveTerms(catalog,'消费电子、物理AI、光刻胶'), {selection:['cn_concept:886002.TI'],unresolved:['消费电子','物理AI']});
});
test('explicit identity resolves ambiguity; duplicates preserve original order', () => {
  assert.deepEqual(resolveTerms(catalog,'industry:881001.TI、光刻胶、ＡＩ手机',['cn_concept:886002.TI']), {selection:['cn_concept:886002.TI','industry:881001.TI','cn_concept:886003.TI'],unresolved:[]});
});
test('substring suggests rather than silently adding a board', () => {
  assert.deepEqual(catalogMatches(catalog,'光刻').map(e=>e.key), ['cn_concept:886002.TI']);
  assert.deepEqual(resolveTerms(catalog,'光刻'), {selection:[],unresolved:['光刻']});
});
test('empty query does not expand scope', () => {
  assert.equal(catalogMatches(catalog,' ').length, catalog.length);
  assert.deepEqual(resolveTerms(catalog,' ',['industry:881001.TI']), {selection:['industry:881001.TI'],unresolved:[]});
});

test('unified local search ranks exact names, aliases and ordered matches without auto-selecting them', () => {
  const expanded = [...catalog, { key:'industry:884091.TI',name:'半导体材料',path:['半导体','材料'] },
    { key:'cn_concept:886069.TI',name:'人形机器人',aliases:['物理AI'] }];
  assert.equal(catalogMatches(expanded,'半材')[0].name,'半导体材料');
  assert.equal(catalogMatches(expanded,'物理AI')[0].name,'人形机器人');
  assert.deepEqual(resolveTerms(expanded,'物理AI'),{selection:[],unresolved:['物理AI']});
  assert.deepEqual(catalogMatches(expanded,'消费电子','industry').map(e=>e.key),['industry:881001.TI']);
  assert.deepEqual(catalogMatches(expanded,'物理AI','industry'),[]);
});

test('EastMoney tree opens two levels and searches day themes without consulting a provider', async () => {
  const dom = new JSDOM('<main></main>');
  const { document, Event } = dom.window;
  const root = 'dc:industry:BK0001.DC', middle = 'dc:industry:BK0002.DC', leaf = 'dc:industry:BK0003.DC';
  const theme = 'dc:theme:000004.DC';
  const node = document.querySelector('main');
  const requested = [];
  const ui = mountFocusControls(node, {
    onLookup: async args => {
      requested.push(args);
      return { board: { name: '材料', data_day: '2026-11-24', path: ['电子', '半导体', '材料'] },
        rows: [{symbol: '600000.SH', name: '示例', reason: '分类解释'}], total: 1, offset: 0 };
    },
  });
  ui.update({ revision: 0, members: {}, sectors: [], groups: [], aliases: {},
    taxonomy: { run: 'demo', complete: true, hierarchy_status: 'DERIVED_FROM_DC_MEMBERSHIP',
      market_day: '2026-11-24', stock_total: 1 },
    catalog: [
      { key: root, name: '电子', level: 1, count: 1 },
      { key: middle, name: '半导体', level: 2, parent: root, count: 1 },
      { key: leaf, name: '材料', level: 3, parent: middle, count: 1 },
      { key: theme, name: '材料', kind: 'theme', count: 1 },
    ],
  });
  const toggle = item => { item.open = true; item.dispatchEvent(new Event('toggle')); };
  const tree = node.querySelector('[data-role="tree"]');
  toggle(tree.querySelector('details'));
  toggle(tree.querySelector('details details'));
  tree.querySelector('[aria-label="查看 材料 成分"]').click();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(requested, [{ board: leaf, offset: 0, run: 'demo' }]);
  assert.match(node.querySelector('[data-role="detail-rows"]').textContent, /示例 · 600000.SH · 分类解释/);
  const search = node.querySelector('[data-role="query"]'), filter = node.querySelector('[data-role="kind"]');
  filter.value = 'theme';
  search.value = '000004';
  search.dispatchEvent(new Event('input'));
  assert.match(node.querySelector('[data-role="matches"]').textContent, /000004\.DC · 题材/);
  assert.equal(requested.length, 1);
  ui.dispose();
  dom.window.close();
});
