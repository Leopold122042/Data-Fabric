/* Shared, DOM-free data semantics for the new cockpit. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MedFabricCockpit = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SOURCES = ['his', 'lis', 'pacs', 'emr', 'iot'];
  const EVENT_NAMES = {
    data_update: '数据增量', schema_change: '结构变更', quality_anomaly: '体征业务提示',
    semantic_link_discovered: '患者编号关联', catalog_built: '目录纳管',
    service_invoked: '服务调用', orchestrated_action: '任务执行反馈',
    security_risk: '访问安全事件', data_circulation: '服务流通',
    data_service_registered: '服务登记', service_flow_completed: '组合执行反馈',
    security_risk_resolved: '风险核查记录'
  };
  function number(value) {
    return value !== null && value !== undefined && typeof value !== 'boolean' && !(typeof value === 'string' && !value.trim()) && Number.isFinite(Number(value)) ? Number(value) : null;
  }
  function format(value, digits = 0) {
    const n = number(value);
    return n === null ? '—' : n.toLocaleString('zh-CN', {maximumFractionDigits: digits});
  }
  function percentage(value) {
    const n = number(value);
    return n === null ? '—' : (n * 100).toFixed(1) + '%';
  }
  function terminalRate(counters) {
    const done = number(counters?.done), failed = number(counters?.failed);
    return done !== null && failed !== null && done + failed > 0 ? done / (done + failed) : null;
  }
  function mergeEvents(previous, incoming, limit = 80) {
    const map = new Map();
    for (const event of [...previous, ...incoming]) {
      if (number(event.seq) !== null) map.set(Number(event.seq), event);
    }
    return [...map.values()].sort((a, b) => b.seq - a.seq).slice(0, limit);
  }
  function eventName(type) { return Object.hasOwn(EVENT_NAMES, type) ? EVENT_NAMES[type] : String(type || '未知事件'); }
  function chooseDepartment(rows, selected) {
    return rows.find(row => row.name === selected) || rows[0] || null;
  }
  function seriesTotal(point) {
    const values = SOURCES.map(id => number(point.by_source?.[id]));
    return values.some(v => v === null) ? null : values.reduce((sum, v) => sum + v, 0);
  }
  function epochOf(point, anchor) {
    if (number(point.epoch) !== null) return Number(point.epoch);
    if (number(anchor?.epoch) !== null) return Number(anchor.epoch) + point.t - anchor.t;
    return null;
  }
  function buildHistory(history = [], origin, includeSimulated = false) {
    const valid = history.filter(p => number(p.t) !== null).slice().sort((a, b) => a.t - b.t);
    if (!valid.length) return {points: [], boundary: null, anchor: null};
    const anchor = origin && number(origin.t) !== null ? origin : valid[0];
    const real = valid.map(p => ({...p, simulated: false, epoch: epochOf(p, anchor)}));
    if (!includeSimulated) return {points: real, boundary: anchor.t, anchor};
    const profiles = SOURCES.map((_, j) => {
      let seed = 90210 + j * 127;
      const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
      const weights = Array.from({length: 96}, (_, i) => .08 + Math.pow(random(), 5) * 15 + ((Math.floor(i / 8) + j) % 4 === 0 ? random() * 4 : 0));
      const sum = weights.reduce((a, b) => a + b, 0);
      let running = 0;
      return [0, ...weights.map(v => (running += v) / sum)];
    });
    const simulated = Array.from({length: 97}, (_, i) => {
      const point = {t: anchor.t - 86400 + i * 900, by_source: {}, simulated: true};
      SOURCES.forEach((id, j) => {
        const value = number(anchor.by_source?.[id]);
        point.by_source[id] = value === null ? null : Math.round(value * (.22 + .78 * profiles[j][i]));
      });
      point.epoch = epochOf(point, anchor);
      return point;
    });
    return {points: [...simulated, ...real.filter(p => p.t > anchor.t)], boundary: anchor.t, anchor};
  }
  function taskState(status) {
    const states = {queued: '排队中', running: '执行中', done: '已完成', failed: '执行失败'};
    return Object.hasOwn(states, status) ? states[status] : '状态未知';
  }
  return {SOURCES, number, format, percentage, terminalRate, mergeEvents, eventName, chooseDepartment, seriesTotal, epochOf, buildHistory, taskState};
});
