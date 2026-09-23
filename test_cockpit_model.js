'use strict';

import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

// Load the UMD export independently of the surrounding workspace's module type.
const commonjs = {exports: {}};
new Function('module', 'exports', readFileSync(new URL('./static/cockpit-model.js', import.meta.url), 'utf8'))(commonjs, commonjs.exports);
const model = commonjs.exports;

function sourceRows(overrides = {}) {
  return {his: 2729, lis: 3086, pacs: 174, emr: 125, iot: 12132, ...overrides};
}

function point(t, overrides = {}) {
  return {t, by_source: sourceRows(), ...overrides};
}

function freezeDeep(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freezeDeep);
    Object.freeze(value);
  }
  return value;
}

test('missing and invalid numeric fields remain unknown; numeric zero remains zero', () => {
  for (const value of [null, undefined, '', '   ', '\t\n', false, true, NaN, Infinity, -Infinity, 'not a number']) {
    assert.equal(model.number(value), null, `invalid value ${JSON.stringify(value)} must not become zero`);
    assert.equal(model.format(value), '—');
  }
  for (const value of [0, '0', '0.0']) {
    assert.equal(model.number(value), 0);
    assert.equal(model.format(value), '0');
  }
  assert.equal(model.number('12.5'), 12.5);
  assert.equal(model.format(18246), '18,246');
});

test('percentages distinguish unknown, genuine zero and complete success', () => {
  assert.equal(model.percentage(null), '—');
  assert.equal(model.percentage(undefined), '—');
  assert.equal(model.percentage(0), '0.0%');
  assert.equal(model.percentage(1), '100.0%');
  assert.equal(model.percentage(1 / 3), '33.3%');
});

test('task rate uses only terminal outcomes and has no result for an empty denominator', () => {
  assert.equal(model.terminalRate({done: 0, failed: 0, total: 9, queued: 8, running: 1}), null);
  assert.equal(model.terminalRate({done: 0, failed: 4, total: 9}), 0);
  assert.equal(model.terminalRate({done: 3, failed: 1, total: 20}), .75);
  assert.equal(model.terminalRate({done: 4, failed: 0}), 1);
  for (const counters of [undefined, {}, {done: 3}, {failed: 2}, {done: null, failed: 0}]) {
    assert.equal(model.terminalRate(counters), null);
  }
  assert.equal(model.percentage(model.terminalRate({done: 0, failed: 0})), '—');
});

test('department selection follows its name through ranking changes and refreshed counts', () => {
  const initial = [{name: '心血管内科', visits: 12}, {name: '呼吸内科', visits: 10}];
  const selected = model.chooseDepartment(initial, '呼吸内科');
  assert.equal(selected.name, '呼吸内科');
  const refreshed = [{name: '呼吸内科', visits: 17}, {name: '心血管内科', visits: 12}];
  const result = model.chooseDepartment(refreshed, selected.name);
  assert.strictEqual(result, refreshed[0]);
  assert.equal(result.visits, 17);
  assert.strictEqual(model.chooseDepartment(refreshed, '已不存在科室'), refreshed[0]);
  assert.equal(model.chooseDepartment([], '呼吸内科'), null);
});

test('SSE reconnect deduplicates numeric sequence IDs and keeps the latest event content', () => {
  const previous = freezeDeep([{seq: 3, title: 'old'}, {seq: 1, title: 'first'}]);
  const incoming = freezeDeep([{seq: 2, title: 'second'}, {seq: '3', title: 'updated'}, {seq: 4, title: 'fourth'}]);
  const merged = model.mergeEvents(previous, incoming);
  assert.deepEqual(merged.map(event => Number(event.seq)), [4, 3, 2, 1]);
  assert.equal(merged.find(event => Number(event.seq) === 3).title, 'updated');
  assert.equal(previous[0].title, 'old');
  assert.equal(previous.length, 2);
});

test('event retention keeps the newest 80 unique events and ignores unusable IDs', () => {
  const rows = Array.from({length: 100}, (_, index) => ({seq: index + 1})).reverse();
  const invalid = [{seq: null}, {}, {seq: ''}, {seq: 'not a sequence'}, {seq: Infinity}];
  const merged = model.mergeEvents(rows.slice(0, 50), [...rows.slice(50), {seq: 90, title: 'repeat'}, ...invalid]);
  assert.equal(merged.length, 80);
  assert.deepEqual(merged.map(event => Number(event.seq)), Array.from({length: 80}, (_, index) => 100 - index));
  assert.equal(merged.find(event => Number(event.seq) === 90).title, 'repeat');
  assert.deepEqual(model.mergeEvents([], []), []);
});

test('total records includes all five sources and propagates missing source values', () => {
  assert.equal(model.seriesTotal(point(1)), 18246);
  assert.equal(model.seriesTotal(point(1, {by_source: sourceRows({his: 0})})), 15517);
  assert.equal(model.seriesTotal(point(1, {by_source: {his: 0, lis: 0, pacs: 0, emr: 0, iot: 0}})), 0);
  for (const value of [null, undefined, '', NaN]) {
    assert.equal(model.seriesTotal(point(1, {by_source: sourceRows({pacs: value})})), null);
  }
  assert.equal(model.seriesTotal({t: 1}), null);
});

test('empty or entirely invalid histories do not invent samples', () => {
  assert.deepEqual(model.buildHistory([], undefined, true), {points: [], boundary: null, anchor: null});
  assert.deepEqual(model.buildHistory([point(null), point(NaN)], undefined, true), {points: [], boundary: null, anchor: null});
});

test('runtime history is ordered without altering original samples or source measurements', () => {
  const origin = freezeDeep(point(100, {epoch: 1700000000}));
  const history = freezeDeep([
    point(108, {by_source: sourceRows({his: 0, pacs: null}), marker: 'later'}),
    point(104, {by_source: sourceRows({his: 2719}), epoch: 1700000004.125, marker: 'earlier'}),
    point(null)
  ]);
  const before = JSON.stringify(history);
  const result = model.buildHistory(history, origin, false);
  assert.deepEqual(result.points.map(sample => sample.t), [104, 108]);
  assert.deepEqual(result.points.map(sample => sample.epoch), [1700000004.125, 1700000008]);
  assert.equal(result.points[0].by_source.his, 2719);
  assert.equal(result.points[1].by_source.his, 0);
  assert.equal(result.points[1].by_source.pacs, null);
  assert.ok(result.points.every(sample => sample.simulated === false));
  assert.equal(result.boundary, 100);
  assert.equal(JSON.stringify(history), before);
});

test('opt-in simulated history has exactly 97 deterministic monotone samples over 24 hours', () => {
  const origin = freezeDeep(point(100000, {epoch: 1700000000}));
  const history = freezeDeep([point(100004)]);
  const first = model.buildHistory(history, origin, true);
  const second = model.buildHistory(history, origin, true);
  assert.deepEqual(first, second);
  const simulated = first.points.filter(sample => sample.simulated);
  assert.equal(simulated.length, 97);
  assert.equal(simulated[0].t, origin.t - 86400);
  assert.equal(simulated.at(-1).t, origin.t);
  for (let index = 1; index < simulated.length; index++) {
    assert.equal(simulated[index].t - simulated[index - 1].t, 900);
    for (const id of model.SOURCES) {
      assert.ok(simulated[index].by_source[id] >= simulated[index - 1].by_source[id], `${id} should not fall in the synthetic buildup`);
    }
  }
  for (const id of model.SOURCES) {
    assert.equal(simulated.at(-1).by_source[id], origin.by_source[id]);
  }
  assert.equal(model.buildHistory(history, origin).points.length, 1, 'simulation must be explicitly selected');
});

test('simulation preserves genuine zero sources and propagates missing values', () => {
  const origin = point(100000, {epoch: 1700000000, by_source: {his: 0, lis: 10, pacs: null, emr: undefined, iot: 1000}});
  const result = model.buildHistory([point(100004)], origin, true);
  const simulated = result.points.filter(sample => sample.simulated);
  assert.equal(simulated.length, 97);
  for (const sample of simulated) {
    assert.equal(sample.by_source.his, 0);
    assert.equal(sample.by_source.pacs, null);
    assert.equal(sample.by_source.emr, null);
    assert.equal(model.seriesTotal(sample), null);
  }
});

test('server restart uses the new origin without retaining the former simulation anchor', () => {
  const oldOrigin = point(300000, {epoch: 1700000000, by_source: sourceRows({his: 100})});
  const newOrigin = point(20, {epoch: 1700100000, by_source: sourceRows({his: 20})});
  const oldResult = model.buildHistory([point(300004)], oldOrigin, true);
  const newResult = model.buildHistory([point(24)], newOrigin, true);
  assert.equal(oldResult.boundary, 300000);
  assert.equal(newResult.boundary, 20);
  assert.equal(newResult.points[0].t, 20 - 86400);
  assert.equal(newResult.points[0].epoch, 1700100000 - 86400);
  assert.equal(newResult.points.filter(sample => sample.simulated).at(-1).by_source.his, 20);
  assert.equal(oldResult.points.filter(sample => sample.simulated).at(-1).by_source.his, 100);
});

test('adding simulation never reshapes real source data beyond the boundary', () => {
  const origin = point(100, {epoch: 1700000000});
  const samples = freezeDeep([
    point(104, {by_source: sourceRows({his: 2500, lis: 0}), epoch: 1700000004.5}),
    point(108, {by_source: sourceRows({his: 2480, pacs: null})})
  ]);
  const plain = model.buildHistory(samples, origin, false).points;
  const withSimulation = model.buildHistory(samples, origin, true).points.filter(sample => !sample.simulated);
  assert.deepEqual(withSimulation, plain);
  assert.equal(withSimulation[1].by_source.his, 2480, 'a genuine decline must not be smoothed away');
  assert.equal(withSimulation[0].by_source.lis, 0);
  assert.equal(withSimulation[1].by_source.pacs, null);
});

test('epoch conversion honors supplied timestamps and exact monotonic offsets', () => {
  const anchor = {t: 500.125, epoch: 1700000000.25};
  assert.equal(model.epochOf({t: 502.875}, anchor), 1700000003);
  assert.equal(model.epochOf({t: 502.875, epoch: 1700000008.75}, anchor), 1700000008.75);
  assert.equal(model.epochOf({t: 502.875, epoch: 0}, anchor), 0);
  assert.equal(model.epochOf({t: 500}, {t: 500}), null);
  const result = model.buildHistory([point(502.875)], point(anchor.t, {epoch: anchor.epoch}), true);
  assert.equal(result.points[0].epoch, anchor.epoch - 86400);
  assert.equal(result.points[96].epoch, anchor.epoch);
  assert.equal(result.points[97].epoch, 1700000003);
});

test('known and unknown task states never turn an unrecognized value into success', () => {
  assert.equal(model.taskState('queued'), '排队中');
  assert.equal(model.taskState('running'), '执行中');
  assert.equal(model.taskState('done'), '已完成');
  assert.equal(model.taskState('failed'), '执行失败');
  for (const value of [undefined, null, '', 'cancelled', 'pending', 'DONE', 'constructor', '__proto__', 'toString']) {
    assert.equal(model.taskState(value), '状态未知', `unexpected status ${String(value)}`);
  }
});

test('event labels preserve unknown codes and distinguish business alerts from data quality', () => {
  assert.equal(model.eventName('quality_anomaly'), '体征业务提示');
  assert.equal(model.eventName('semantic_link_discovered'), '患者编号关联');
  assert.equal(model.eventName('future_event'), 'future_event');
  assert.equal(model.eventName('constructor'), 'constructor');
  assert.equal(model.eventName('__proto__'), '__proto__');
  assert.equal(model.eventName(undefined), '未知事件');
});
