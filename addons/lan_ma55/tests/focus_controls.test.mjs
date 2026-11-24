import test from 'node:test';
import assert from 'node:assert/strict';
import { catalogMatches, resolveTerms, splitTerms } from '../src/focus_controls.mjs';

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
