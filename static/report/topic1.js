import {placeSources, routeSources, softenPath} from './topic1-layout.js?v=0.1.42';

const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[char]));

const sourceNames = {
  his: '医院 HIS', lis: '检验 LIS', pacs: '影像 PACS', iot: '监护 IoT', emr: '电子病历 EMR',
  'src-clinical-operations': '临床运营主题库', 'doris-nju': '南大医疗 Doris', openalex: 'OpenAlex 文献'
};
const sourceOrder = Object.keys(sourceNames);
const relationLabels = {
  confirmed: '已确认关联',
  reference: '已登记参照',
  candidate: '语义候选',
  overlap: '同名候选'
};
const openalexLabels = {
  openalex_works: '论文', openalex_authors: '作者', openalex_institutions: '研究机构',
  openalex_sources: '出版来源', openalex_authorships: '论文署名', openalex_topics: '论文主题',
  openalex_references: '引用关系', openalex_yearly: '年度统计'
};
const registered = relation => relation.kind === 'confirmed' || relation.kind === 'reference';
const sourceLabel = source => sourceNames[String(source).toLowerCase()] || source;
const sessions = new WeakMap();

export async function renderKnowledgeGraph(host) {
  if (!host) return;
  const previous = sessions.get(host);
  previous?.controller.abort();
  previous?.observer?.disconnect();
  const session = {controller: new AbortController()};
  sessions.set(host, session);
  const {signal} = session.controller;
  host.innerHTML = '<div class="kg-loading" role="status">正在读取目录并整理字段关联…</div>';

  try {
    const response = await fetch('/api/portal/catalog?include_relations=true', {
      credentials: 'same-origin', headers: {Accept: 'application/json'}, signal
    });
    if (!response.ok) throw new Error('目录接口返回 ' + response.status);
    const catalog = await response.json();
    if (signal.aborted) return;
    const tables = (catalog.tables || []).map(table => {
      const shortName = (table.physical_name || table.name).replace(/^cdm\./, '').split('(')[0];
      const display = table.label && table.label !== table.name ? table.label : openalexLabels[table.name] || shortName;
      return {...table, shortName, display, fieldsById: new Map(table.fields.map(field => [field.id, field]))};
    }).sort((a, b) => sourceRank(a.source) - sourceRank(b.source)
      || String(a.source).localeCompare(String(b.source)) || a.name.localeCompare(b.name, 'zh-CN'));
    const tableMap = new Map(tables.map((table, index) => [table.id, {...table, index}]));
    const relations = (catalog.relations || []).map(relation => ({
      ...relation,
      members: relation.members.flatMap(member => {
        const table = tableMap.get(member.table_id);
        const field = table?.fieldsById.get(member.field_id);
        return field ? [{table, field}] : [];
      })
    })).filter(relation => new Set(relation.members.map(member => member.table.id)).size > 1);
    mountGraph(host, session, [...tableMap.values()], relations);
  } catch (error) {
    if (signal.aborted) return;
    host.innerHTML = `<section class="kg-error" role="alert"><p>无法读取字段图谱</p><small>${esc(error.message)}</small>
      <button type="button" class="kg-retry">重试</button></section>`;
    host.querySelector('.kg-retry').addEventListener('click', () => renderKnowledgeGraph(host), {signal});
  }
}

function sourceRank(source) {
  const index = sourceOrder.indexOf(String(source).toLowerCase());
  return index < 0 ? sourceOrder.length : index;
}

function mountGraph(host, session, tables, relations) {
  if (!tables.length) {
    host.innerHTML = '<p class="kg-empty">当前账户没有可查看的逻辑表。</p>';
    return;
  }
  const preferred = tables.find(table => table.source === 'his' && table.shortName === 'patient');
  const state = {tables, relations, table: preferred || tables[0], fieldId: null,
    search: '', source: '', filter: 'all', mode: 'overview', sourceLink: null};
  const sources = [...new Set(tables.map(table => table.source))];
  const confirmed = relations.filter(registered).length;
  host.innerHTML = `<section class="kg-panel" aria-labelledby="kg-title">
    <header class="kg-heading"><div><p class="kg-eyebrow">数据目录与字段关系</p><h2 id="kg-title">字段与逻辑表知识图谱</h2>
      <p class="kg-overview-help">每个色块对应一个数据源。点击跨来源连线，查看相连的表和字段。</p>
      <p class="kg-focus-help">点击表查看字段，点击字段查看它连接的表。总图中同一对表共用一条线。</p></div>
      <div class="kg-counts"><strong>${tables.length}</strong><span>张表</span><i></i>
        <strong>${confirmed}</strong><span>条已登记关系</span><i></i>
        <strong>${relations.length - confirmed}</strong><span>组候选</span></div>
    </header>
    <div class="kg-toolbar">
      <div class="kg-modes" role="group" aria-label="图谱视图">
        <button type="button" data-graph-mode="overview">全部表关系</button>
        <button type="button" data-graph-mode="focus">选中表关系</button>
      </div>
      <label>来源<select class="kg-search" data-graph-source>
        <option value="">全部来源</option>${sources.map(source => `<option value="${esc(source)}">${esc(sourceLabel(source))}</option>`).join('')}
      </select></label>
      <label>连线<select class="kg-search" data-graph-filter>
        <option value="all">全部关系</option><option value="registered">已确认与已登记</option>
        <option value="candidates">同名与语义候选</option>
      </select></label>
      <label>查找<input class="kg-search" data-graph-search type="search" placeholder="表名、字段或来源"></label>
    </div>
    <div class="kg-layout"><div class="kg-graph-scroll" aria-label="逻辑表关系图">
      <div class="kg-canvas"><svg class="kg-edges" aria-hidden="true"></svg><svg class="kg-source-network" aria-label="来源分区与连接"></svg><div class="kg-source-grid"></div></div>
    </div><div class="kg-source-link-detail" hidden aria-live="polite"></div><aside class="kg-inspector" aria-live="polite"></aside></div>
    <footer class="kg-legend">
      <span><i class="kg-legend-line kg-solid"></i>已确认关联或已登记参照</span>
      <span><i class="kg-legend-line"></i>同名或语义候选</span>
      <small>候选线供核对字段口径。跨表查询还需满足平台的关联与权限规则。</small>
    </footer>
  </section>`;

  const draw = () => renderGraph(host, state);
  const {signal} = session.controller;
  const search = host.querySelector('[data-graph-search]');
  search.addEventListener('input', () => {
    state.search = search.value.trim().toLowerCase();
    state.sourceLink = null;
    draw();
  }, {signal});
  host.querySelector('[data-graph-source]').addEventListener('change', event => {
    state.source = event.target.value;
    state.sourceLink = null;
    const next = tables.find(table => table.source === state.source);
    if (next && state.table.source !== state.source) {
      state.table = next;
      state.fieldId = null;
    }
    draw();
  }, {signal});
  host.querySelector('[data-graph-filter]').addEventListener('change', event => {
    state.filter = event.target.value;
    state.sourceLink = null;
    draw();
  }, {signal});
  const toggleSourceLink = (route, keepFocus = false) => {
    const key = route.dataset.sourceLink;
    state.sourceLink = state.sourceLink === key ? null : key;
    draw();
    if (keepFocus) {
      const replacement = [...host.querySelectorAll('[data-source-link]')]
        .find(item => item.dataset.sourceLink === key);
      replacement?.focus({preventScroll: true});
    } else if (state.sourceLink) {
      host.querySelector('.kg-source-link-detail').scrollIntoView({block: 'nearest'});
    }
  };
  host.addEventListener('keydown', event => {
    const route = event.target.closest('[data-source-link]');
    if (route && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      toggleSourceLink(route, true);
    }
  }, {signal});
  host.addEventListener('click', event => {
    const route = event.target.closest('[data-source-link]');
    if (route) {
      toggleSourceLink(route);
      return;
    }
    if (event.target.closest('[data-source-link-close]')) {
      state.sourceLink = null;
      draw();
      return;
    }
    const mode = event.target.closest('[data-graph-mode]');
    const node = event.target.closest('[data-table-index]');
    const field = event.target.closest('[data-field-index]');
    const related = event.target.closest('[data-related-table]');
    if (mode) {
      state.mode = mode.dataset.graphMode;
    } else if (node || related) {
      state.table = tables[Number(node?.dataset.tableIndex ?? related.dataset.relatedTable)];
      state.fieldId = related?.dataset.relatedField || null;
      state.sourceLink = null;
      if (related) {
        state.source = '';
        state.search = '';
        search.value = '';
        host.querySelector('[data-graph-source]').value = '';
      }
    } else if (field) {
      const id = state.table.fields[Number(field.dataset.fieldIndex)]?.id;
      state.fieldId = state.fieldId === id ? null : id;
      state.mode = 'focus';
    } else {
      return;
    }
    draw();
  }, {signal});
  if (globalThis.ResizeObserver) {
    const panel = host.querySelector('.kg-graph-scroll');
    session.width = panel.clientWidth;
    session.observer = new ResizeObserver(() => {
      if (panel.clientWidth === session.width) return;
      session.width = panel.clientWidth;
      draw();
    });
    session.observer.observe(panel);
  }
  draw();
}

function buildPairs(relations, visible, focus, fieldId) {
  const pairs = new Map();
  for (const relation of relations) {
    const members = relation.members.filter(member => visible.has(member.table.id));
    if (fieldId && !members.some(member => member.field.id === fieldId)) continue;
    const indexes = [...new Set(members.map(member => member.table.index))].sort((a, b) => a - b);
    for (let i = 0; i < indexes.length; i++) {
      for (let j = i + 1; j < indexes.length; j++) {
        const [a, b] = [indexes[i], indexes[j]];
        if (focus != null && a !== focus && b !== focus) continue;
        const key = a + ':' + b;
        if (!pairs.has(key)) pairs.set(key, {a, b, links: []});
        pairs.get(key).links.push(relation);
      }
    }
  }
  return [...pairs.values()];
}

function renderGraph(host, state) {
  const {tables, table, search, source, fieldId, mode} = state;
  const relations = state.relations.filter(relation => state.filter === 'all'
    || (state.filter === 'registered') === registered(relation));
  const focus = mode === 'focus';
  host.classList.toggle('kg-overview', !focus);
  host.querySelectorAll('[data-graph-mode]').forEach(button =>
    button.setAttribute('aria-pressed', String(button.dataset.graphMode === mode)));

  const visible = new Set(tables.filter(item => (!source || item.source === source)
    && (!search || [item.source, sourceLabel(item.source), item.display, item.name,
      ...item.fields.flatMap(field => [field.name, field.label, field.definition])].join(' ').toLowerCase().includes(search)))
    .map(item => item.id));
  // Keep the other end of a matching relation visible while searching.
  if (search) {
    const matches = new Set(visible);
    for (const relation of relations) {
      if (relation.members.some(member => matches.has(member.table.id))) {
        for (const member of relation.members) if (!source || member.table.source === source) visible.add(member.table.id);
      }
    }
  }
  if (focus) visible.add(table.id);
  const pairs = buildPairs(relations, visible, focus ? table.index : null, focus ? fieldId : null);
  const neighbours = new Set(pairs.flatMap(pair => [pair.a, pair.b]));
  const shown = tables.filter(item => visible.has(item.id) && (!focus || item.id === table.id || neighbours.has(item.index)));
  if (focus) renderCanvas(host, shown, pairs, state);
  else renderSourceOverview(host, shown, pairs, state);
  host.querySelector('.kg-inspector').innerHTML = inspector(table, relations, fieldId);
}

function tableNode(table, state, count) {
  const selected = state.table.id === table.id;
  return `<button type="button" class="kg-table-node ${selected ? 'is-selected' : ''}" data-table-index="${table.index}"
    aria-pressed="${selected}" title="${esc(table.physical_name || table.name)}">
    <span class="kg-table-source">${esc(sourceLabel(table.source))}</span>
    <strong>${esc(table.display)}</strong><small>${table.fields.length} 个字段 · ${count} 张关联表</small>
  </button>`;
}

function inspector(table, relations, fieldId) {
  const links = relations.filter(relation => relation.members.some(member => member.table.id === table.id));
  const counts = new Map();
  for (const link of links) {
    for (const member of link.members) {
      if (member.table.id === table.id) counts.set(member.field.id, (counts.get(member.field.id) || 0) + 1);
    }
  }
  const field = table.fieldsById.get(fieldId);
  const fields = table.fields.map((item, index) => {
    const count = counts.get(item.id) || 0;
    return `<button type="button" class="kg-field-row ${count ? 'has-relation' : ''} ${item.id === fieldId ? 'is-active' : ''}"
      data-field-index="${index}" aria-pressed="${item.id === fieldId}">
      <span class="kg-field-heading"><b>${esc(item.label)}</b><code>${esc(item.name)}</code><em>${esc(item.type)}</em></span>
      <small>${esc(item.definition || '字段含义待补充')}</small>
      <span class="kg-field-status ${item.status === 'confirmed' ? 'confirmed' : ''}">
        ${count ? count + ' 组关联 · ' : ''}${item.status === 'confirmed' ? '已确认定义' : '定义待确认'}
      </span></button>`;
  }).join('');
  const selected = field ? links.filter(link => link.members.some(member => member.field.id === fieldId)) : links;
  const details = selected.map(relation => {
    const ownFields = relation.members.filter(member => member.table.id === table.id).map(member => member.field.name);
    const partners = relation.members.filter(member => member.table.id !== table.id);
    return `<section class="kg-related-panel">
      <h4>${esc(relation.label)} <span class="kg-relation-kind" data-kind="${esc(relation.kind)}">${esc(relationLabels[relation.kind])}</span></h4>
      <p>${esc(ownFields.join('、'))} · ${esc(relation.note)}</p>
      <ul>${partners.map(member => `<li><button type="button" data-related-table="${member.table.index}"
        data-related-field="${esc(member.field.id)}">
        <span>${esc(sourceLabel(member.table.source))} · ${esc(member.table.display)}</span>
        <b>${esc(member.field.label)} <code>${esc(member.field.name)}</code></b></button></li>`).join('')}</ul>
    </section>`;
  }).join('');
  return `<div class="kg-inspector-heading"><p>已选逻辑表</p><h3>${esc(table.display)}</h3>
      <span>${esc(sourceLabel(table.source))} / ${esc(table.physical_name || table.name)} / ${table.fields.length} 个字段</span>
    </div>
    <details class="kg-table-fields" open><summary>表字段</summary><div class="kg-field-list">${fields}</div></details>
    <h4 class="kg-relationship-heading">${field ? esc(field.label) : '本表'}的关联</h4>
    ${details || '<p class="kg-empty">当前筛选下没有关联字段。</p>'}`;
}

function renderCanvas(host, tables, pairs, state) {
  const scroll = host.querySelector('.kg-graph-scroll');
  const canvas = host.querySelector('.kg-canvas');
  const grid = host.querySelector('.kg-source-grid');
  const svg = host.querySelector('.kg-edges');
  host.querySelector('.kg-source-network').innerHTML = '';
  host.querySelector('.kg-source-link-detail').hidden = true;
  const width = Math.max(620, scroll.clientWidth - 48);
  const nodeHeight = 72;
  const positions = new Map();
  const counts = new Map();
  for (const {a, b} of pairs) {
    counts.set(a, (counts.get(a) || 0) + 1);
    counts.set(b, (counts.get(b) || 0) + 1);
  }
  const listed = tables.filter(table => table.id !== state.table.id);
  const w = Math.min(220, Math.floor(width * .32));
  const height = Math.max(155, listed.length * 107 + 35);
  positions.set(state.table.index, {x: 23, y: (height - nodeHeight) / 2, w, h: nodeHeight});
  listed.forEach((table, index) => positions.set(table.index,
    {x: width - w - 25, y: 28 + index * 107, w, h: nodeHeight}));
  Object.assign(canvas.style, {width: width + 'px', minWidth: '0'});
  Object.assign(grid.style, {minWidth: '0', width: width + 'px', height: height + 'px'});
  grid.innerHTML = tables.map(table => tableNode(table, state, counts.get(table.index) || 0)).join('')
    || '<p class="kg-empty">没有符合筛选条件的逻辑表。</p>';
  for (const node of grid.querySelectorAll('[data-table-index]')) {
    const p = positions.get(Number(node.dataset.tableIndex));
    Object.assign(node.style, {position: 'absolute', left: p.x + 'px', top: p.y + 'px',
      width: p.w + 'px', height: p.h + 'px'});
  }
  svg.setAttribute('width', String(width));
  svg.setAttribute('height', String(height));
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.innerHTML = pairs.map(pair => {
    let a = positions.get(pair.a), b = positions.get(pair.b);
    if (!a || !b) return '';
    if (a.x > b.x || (a.x === b.x && a.y > b.y)) [a, b] = [b, a];
    const solid = pair.links.some(registered);
    const highlighted = pair.a === state.table.index || pair.b === state.table.index;
    const labelSet = [...new Set(pair.links.map(link => link.label))];
    const label = labelSet.length === 1 ? labelSet[0] : labelSet.length + ' 个字段';
    const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
    const mid = (x1 + x2) / 2;
    const path = `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
    const labelX = x2 - Math.min(72, (x2 - x1) / 4);
    const labelY = y2 - 7;
    const classes = ['kg-edge-group', solid ? 'kg-edge-registered' : 'kg-edge-candidate',
      highlighted ? 'kg-edge-highlight' : ''].join(' ');
    return `<g class="${classes}"><title>${esc(labelSet.join('、'))}</title><path d="${path}"/>
      <text x="${labelX}" y="${labelY}" text-anchor="middle">${esc(label)}</text></g>`;
  }).join('');
}

const sourceHues = [163, 198, 221, 142, 257, 39, 181, 233];

function withinSourcePath(a, b, index) {
  const middleA = a.y + a.h / 2, middleB = b.y + b.h / 2;
  const laneOffset = (index % 5 - 2) * 3;
  if (middleA === middleB) {
    const [left, right] = a.x < b.x ? [a, b] : [b, a];
    return `M ${left.x + left.w} ${middleA} H ${right.x}`;
  }
  const sharedColumn = a.column === b.column;
  const dirA = sharedColumn ? (a.column === a.columns - 1 ? -1 : 1) : (a.x < b.x ? 1 : -1);
  const dirB = sharedColumn ? dirA : -dirA;
  const x1 = dirA > 0 ? a.x + a.w : a.x, x2 = dirB > 0 ? b.x + b.w : b.x;
  const gutter = sharedColumn ? x1 + dirA * (14 + laneOffset) : (x1 + x2) / 2 + laneOffset;
  return softenPath([{x: x1, y: middleA}, {x: gutter, y: middleA},
    {x: gutter, y: middleB}, {x: x2, y: middleB}]);
}

function renderSourceOverview(host, tables, pairs, state) {
  const scroll = host.querySelector('.kg-graph-scroll');
  const canvas = host.querySelector('.kg-canvas');
  const grid = host.querySelector('.kg-source-grid');
  const network = host.querySelector('.kg-source-network');
  host.querySelector('.kg-edges').innerHTML = '';

  const sources = new Map(), byIndex = new Map(), counts = new Map();
  for (const table of tables) {
    if (!sources.has(table.source)) sources.set(table.source, {source: table.source, tables: [], ports: []});
    sources.get(table.source).tables.push(table);
    byIndex.set(table.index, table);
  }
  const blocks = [...sources.values()];
  const routes = new Map(), local = [];
  for (const pair of pairs) {
    counts.set(pair.a, (counts.get(pair.a) || 0) + 1);
    counts.set(pair.b, (counts.get(pair.b) || 0) + 1);
    const a = byIndex.get(pair.a).source, b = byIndex.get(pair.b).source;
    if (a === b) {
      local.push(pair);
      continue;
    }
    const ordered = [a, b].sort();
    const key = JSON.stringify(ordered);
    if (!routes.has(key)) {
      const [first, second] = ordered.map(source => sources.get(source));
      routes.set(key, {key, first, second, pairs: [], registered: false, start: {target: second}, end: {target: first}});
    }
    const route = routes.get(key);
    route.pairs.push(pair);
    route.registered ||= pair.links.some(registered);
  }

  const width = Math.max(blocks.length > 1 ? 610 : 280, scroll.clientWidth - 48);
  const routeList = [...routes.values()];
  const {height, tables: positions} = placeSources(blocks, routeList, width);
  routeSources(blocks, routeList, width, height);
  Object.assign(canvas.style, {width: width + 'px', minWidth: '0'});
  Object.assign(grid.style, {minWidth: '0', width: width + 'px', height: height + 'px'});

  grid.innerHTML = [...blocks].sort((a, b) => a.y - b.y || a.x - b.x).flatMap(block => [
    `<header class="kg-overview-source-title" style="left:${block.x + 14}px; top:${block.y + 17}px;
      width:${block.w - 28}px; --kg-source-hue:${sourceHues[sourceRank(block.source) % sourceHues.length]}">
      <b>${esc(sourceLabel(block.source))}</b><span>${block.tables.length} 张表</span></header>`,
    ...block.tables.map(table => tableNode(table, state, counts.get(table.index) || 0))
  ]).join('') || '<p class="kg-empty">没有符合筛选条件的逻辑表。</p>';
  for (const node of grid.querySelectorAll('[data-table-index]')) {
    const p = positions.get(Number(node.dataset.tableIndex));
    Object.assign(node.style, {position: 'absolute', left: p.x + 'px', top: p.y + 'px',
      width: p.w + 'px', height: p.h + 'px'});
  }

  network.setAttribute('width', String(width));
  network.setAttribute('height', String(height));
  network.setAttribute('viewBox', `0 0 ${width} ${height}`);
  const selected = routes.get(state.sourceLink);
  const activeBlocks = selected ? [selected.first, selected.second] : [];
  const blockShapes = blocks.map(block => {
    const hue = sourceHues[sourceRank(block.source) % sourceHues.length];
    return `<rect class="kg-overview-source-card ${activeBlocks.includes(block) ? 'kg-source-active' : ''}"
      data-source-region="${esc(block.source)}" style="--kg-source-hue:${hue}"
      x="${block.x}" y="${block.y}" width="${block.w}" height="${block.h}" rx="${block.tables.length === 1 ? 24 : 17}"/>`;
  }).join('');
  const localPaths = local.map((pair, index) => {
    const type = pair.links.some(registered) ? 'kg-edge-registered' : 'kg-edge-candidate';
    const active = pair.a === state.table.index || pair.b === state.table.index;
    const path = withinSourcePath(positions.get(pair.a), positions.get(pair.b), index);
    return `<g class="kg-edge-group kg-overview-inner ${type} ${active ? 'kg-edge-highlight' : ''}"><path d="${path}"/></g>`;
  }).join('');
  const crossPaths = routeList.sort((a, b) => Number(a === selected) - Number(b === selected)).map(route => {
    if (!route.track) return '';
    const path = softenPath(route.track);
    const a = route.start.point, b = route.end.point;
    const label = sourceLabel(route.first.source) + ' ↔ ' + sourceLabel(route.second.source);
    const active = route === selected;
    const tone = route.registered ? 'kg-edge-registered' : 'kg-edge-candidate';
    return `<g class="kg-edge-group kg-overview-route ${tone} ${active ? 'kg-route-selected' : ''} ${selected && !active ? 'kg-route-muted' : ''}"
      role="button" tabindex="0" aria-pressed="${active}" aria-label="${esc(label)}，${route.pairs.length} 对表"
      data-source-link="${esc(route.key)}">
      <title>${esc(label)} · ${route.pairs.length} 对表</title>
      <path class="kg-route-ink" d="${path}"/>
      <circle cx="${a.x}" cy="${a.y}" r="3.5"/><circle cx="${b.x}" cy="${b.y}" r="3.5"/>
      <path class="kg-route-hit" d="${path}"/>
    </g>`;
  }).join('');
  network.innerHTML = blockShapes + localPaths + crossPaths;

  const detail = host.querySelector('.kg-source-link-detail');
  detail.hidden = !selected;
  if (!selected) {
    detail.innerHTML = '';
    return;
  }
  const heading = sourceLabel(selected.first.source) + ' ↔ ' + sourceLabel(selected.second.source);
  const tableButton = table => `<button type="button" data-related-table="${table.index}">${esc(table.display)}</button>`;
  detail.innerHTML = `<div class="kg-source-link-heading"><h4>${esc(heading)}</h4>
    <button type="button" class="kg-retry" data-source-link-close>收起</button></div>
    <ul>${selected.pairs.map(pair => `<li>${tableButton(byIndex.get(pair.a))} <span aria-hidden="true">↔</span>
      ${tableButton(byIndex.get(pair.b))}<small>${esc([...new Set(pair.links.map(link => link.label))].join('、'))}</small></li>`).join('')}</ul>`;
}
