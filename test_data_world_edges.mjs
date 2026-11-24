import test from 'node:test';
import assert from 'node:assert/strict';
import {mountEdgeLayer} from './static/report/atlas-stars.js';
import {fieldLayout} from './static/report/field-constellation.js';

// The renderer uses basic SVG nodes and precomputed catalog coordinates.
// Exercise those nodes directly without requiring a browser or its font stack.
function node(tag) {
  const classes = new Set();
  return {tag, dataset: {}, attrs: {}, children: [],
    classList: {toggle(name, on) { if (on) classes.add(name); else classes.delete(name); },
      remove: name => classes.delete(name), contains: name => classes.has(name)},
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children.flatMap(n => n.tag === 'fragment' ? n.children : [n]); },
    setAttribute(key, value) { this.attrs[key] = String(value); },
    removeAttribute(key) { delete this.attrs[key]; }};
}

function mockDocument(t) {
  let scheduled;
  globalThis.requestAnimationFrame = fn => { scheduled = fn; return 1; };
  globalThis.cancelAnimationFrame = () => { scheduled = null; };
  globalThis.document = {createElement: () => ({getContext: () => null}),
    createTextNode: data => ({data}), createDocumentFragment: () => node('fragment'), createElementNS: (_, tag) => node(tag)};
  t.after(() => {
    delete globalThis.document;
    delete globalThis.requestAnimationFrame;
    delete globalThis.cancelAnimationFrame;
  });
  return () => { const draw = scheduled; scheduled = null; draw?.(); };
}

test('ownership and each real relationship keep their own label, style and moving anchor', t => {
  const flush = mockDocument(t);
  const fields = [{id: 'patient', name: 'patient_id'}, {id: 'time', name: 'taken_at'}];
  const table = {id: 'labs', display: '检验', fields};
  const partner = {id: 'visits', display: '就诊'};
  const kinds = {confirmed: '已确认关联', reference: '已登记参照', candidate: '语义候选', overlap: '同名候选'};
  const links = Object.keys(kinds).map((kind, i) => ({kind, label: '关联' + i, note: '登记说明',
    members: [{table, field: fields[0]}, {table: partner, field: {name: 'patient_key'}}]}));
  const layout = {width: 800, height: 620, center: {x: 420, y: 240}, core: {width: 130},
    fields: [{x: 105, y: 110, width: 98}, {x: 690, y: 120, width: 98}],
    points: [{x: 370, y: 550}], labelWidth: 126, relationTop: 490};
  const svg = node('svg');
  const layer = mountEdgeLayer(svg, kinds);
  layer.draw(layout, table, [[partner, links]], null);
  const paths = svg.children.filter(n => n.tag === 'path');
  const labels = svg.children.filter(n => n.tag === 'text');
  assert.equal(paths.length, 6);
  assert.equal(labels.length, paths.length);
  for (const link of links) {
    const line = paths.find(n => n.dataset.kind === link.kind);
    assert.equal(line.attrs.class, ['reference', 'confirmed'].includes(link.kind) ? 'kg-link-registered' : 'kg-link-candidate');
    assert.ok(line.children[0].textContent.includes(link.label + ' · ' + kinds[link.kind]));
    assert.ok(line.children[0].textContent.includes('就诊.patient_key'));
    const label = labels.find(n => n.dataset.kind === link.kind);
    assert.notEqual(label.attrs.display, 'none');
    assert.ok(label.children[1].data.includes(link.label));
  }
  const ownership = labels.find(n => n.dataset.kind === 'membership' && Number(n.dataset.fieldIndex) === 1);
  layer.highlight(1);
  flush();
  assert.ok(ownership.classList.contains('is-highlighted'));
  assert.equal(ownership.children[1].data, '属于 检验');
  const oldX = ownership.attrs.x;
  layer.move(1, {x: 550, y: 85, width: 98});
  flush();
  assert.notEqual(ownership.attrs.x, oldX);
  assert.notEqual(ownership.attrs.display, 'none');
  assert.match(paths.find(n => Number(n.dataset.fieldIndex) === 1).attrs.d, /^M550 85 /);
  layer.clear();
  assert.equal(svg.children.length, 0);
});

test('a dragged source_id on the 390px mobile view keeps its full reference and one ownership label', t => {
  const flush = mockDocument(t);
  const fields = Array.from({length: 16}, (_, index) => ({id: index, name: index ? 'field_' + index : 'source_id'}));
  const table = {id: 'works', display: '论文', fields};
  const partner = {id: 'sources', display: '出版来源'};
  const link = {kind: 'reference', label: 'source_id', members: [
    {table, field: fields[0]}, {table: partner, field: {name: 'source_id'}}]};
  const neighbours = [[partner, [link]]];
  const layout = fieldLayout(table, neighbours, 368, 380, new Map());
  const svg = node('svg'), readout = node('div');
  const layer = mountEdgeLayer(svg, {reference: '已登记参照'}, readout);
  const visible = kind => svg.children.filter(n => n.tag === 'text' && n.dataset.kind === kind && n.attrs.display !== 'none');
  layer.draw(layout, table, neighbours, null);
  assert.ok(visible('membership').length <= 2);
  layer.draw(layout, table, neighbours, fields[0].id);
  for (const [x, y] of [[102, 192], [184, 250], [68, 430]]) {
    layer.move(0, {x, y, width: 78});
    flush();
    assert.equal(visible('membership').length, 1);
    assert.equal(visible('reference')[0]?.children[1].data, 'source_id · 已登记参照', `reference near (${x},${y})`);
    assert.equal(readout.hidden, true);
  }
  // Text that cannot fit even across the whole chart must still be available
  // on touch screens, including the destination, without relying on a tooltip.
  link.label = '完整登记关系名称'.repeat(9);
  layer.draw(layout, table, neighbours, fields[0].id);
  assert.equal(readout.hidden, false);
  assert.equal(readout.textContent, 'source_id · ' + link.label + ' · 已登记参照 ↔ 出版来源.source_id');
  layer.clear();
  assert.equal(readout.hidden, true);
  assert.equal(readout.textContent, '');
});
