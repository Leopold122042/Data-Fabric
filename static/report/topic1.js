import {focusLayout, overviewLayout, seededRandom} from './topic1-layout.js?v=0.1.44';

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
function sourceLabel(source) {
  const id = String(source ?? '').toLowerCase();
  return Object.hasOwn(sourceNames, id) ? sourceNames[id] : source || '未登记来源';
}
const sessions = new WeakMap();
const medicalSources = new Set(['his', 'lis', 'pacs', 'iot', 'emr', 'src-clinical-operations']);
const galaxyHues = [187, 35, 213, 173, 46, 202];

function galaxyFor(source) {
  const id = String(source ?? '').toLowerCase();
  if (medicalSources.has(id)) return {id: 'group:medical', name: '合成医疗演示', hue: galaxyHues[0]};
  if (id === 'doris-nju') return {id: 'source:doris-nju', name: sourceLabel(source), hue: galaxyHues[1]};
  if (id === 'openalex') return {id: 'source:openalex', name: sourceLabel(source), hue: galaxyHues[2]};
  const hash = [...id].reduce((value, char) => (value * 31 + char.codePointAt(0)) >>> 0, 0);
  return {id: 'source:' + id, name: sourceLabel(source), hue: galaxyHues[3 + hash % 3]};
}

export async function renderKnowledgeGraph(host) {
  if (!host) return;
  const previous = sessions.get(host);
  previous?.controller.abort();
  previous?.observer?.disconnect();
  const session = {controller: new AbortController()};
  sessions.set(host, session);
  const {signal} = session.controller;
  host.innerHTML = '<div class="kg-loading" role="status">正在载入星图…</div>';

  try {
    const response = await fetch('/api/portal/catalog?include_relations=true', {
      credentials: 'same-origin', headers: {Accept: 'application/json'}, signal
    });
    if (!response.ok) throw new Error('目录接口返回 ' + response.status);
    const catalog = await response.json();
    if (signal.aborted) return;
    const catalogTables = catalog.tables || [];
    const maxFields = catalogTables.reduce((max, table) => Math.max(max, table.fields.length), 1);
    const tables = catalogTables.map(table => {
      const shortName = (table.physical_name || table.name).replace(/^cdm\./, '').split('(')[0];
      const display = table.label && table.label !== table.name ? table.label : openalexLabels[table.name] || shortName;
      // Scale the visible area with field count while keeping the smallest planets clickable.
      const planetDiameter = Math.sqrt(10 ** 2 + (26 ** 2 - 10 ** 2) * table.fields.length / maxFields);
      return {...table, shortName, display, planetDiameter, galaxy: galaxyFor(table.source),
        fieldsById: new Map(table.fields.map(field => [field.id, field]))};
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
  const preferred = tables.find(table => table.source === 'his' && table.shortName === 'patient');
  const state = {tables, relations, table: preferred || tables[0], fieldId: null,
    search: '', source: '', filter: 'all', mode: 'overview', overviewFilters: {}, details: true};
  const sources = [...new Set(tables.map(table => table.source))];
  host.innerHTML = `<section class="kg-panel" aria-label="数据世界">
    <div class="kg-toolbar">
      <div class="kg-modes" role="group" aria-label="图谱视图">
        <button type="button" data-graph-mode="overview">全部表关系</button>
        <button type="button" data-graph-mode="focus" ${tables.length ? '' : 'disabled'}>选中表关系</button>
      </div>
      <label>来源<select class="kg-search" data-graph-source>
        <option value="">全部来源</option>${sources.map(source => `<option value="${esc(source)}">${esc(sourceLabel(source))}</option>`).join('')}
      </select></label>
      <label>查找<input class="kg-search" data-graph-search type="search" placeholder="表名、字段或来源"></label>
      <label class="kg-relation-filter">连线<select class="kg-search" data-graph-filter>
        <option value="all">全部关系</option><option value="registered">已确认与已登记</option>
        <option value="candidates">同名与语义候选</option>
      </select></label>
      <button type="button" class="kg-command" data-graph-details aria-controls="kg-fields">字段详情</button>
      <p class="kg-counts" aria-live="polite"></p>
    </div>
    <div class="kg-layout"><div class="kg-scene" aria-label="逻辑表星图">
      <div class="kg-atlas"></div>
      <footer class="kg-legend">
        <span><i></i>已确认或已登记</span>
        <span><i class="kg-legend-candidate"></i>同名或语义候选</span>
      </footer>
    </div><aside id="kg-fields" class="kg-inspector" aria-label="字段与关联详情"></aside></div>
  </section>`;

  const draw = () => renderGraph(host, state);
  const {signal} = session.controller;
  const search = host.querySelector('[data-graph-search]');
  const sourceSelect = host.querySelector('[data-graph-source]');
  const setBrowseFilters = ({source = '', search: term = '', input = term} = {}) => {
    state.source = source;
    state.search = term;
    sourceSelect.value = source;
    search.value = input;
  };
  const switchMode = mode => {
    if (mode === state.mode) return;
    if (mode === 'focus') {
      state.overviewFilters = {source: state.source, search: state.search, input: search.value};
      setBrowseFilters();
    } else {
      setBrowseFilters(state.overviewFilters);
    }
    state.mode = mode;
  };

  search.addEventListener('input', () => {
    state.search = search.value.trim().toLowerCase();
    draw();
  }, {signal});
  sourceSelect.addEventListener('change', () => {
    state.source = sourceSelect.value;
    const next = tables.find(table => table.source === state.source);
    if (state.mode === 'focus' && next && state.table.source !== state.source) {
      state.table = next;
      state.fieldId = null;
    }
    draw();
  }, {signal});
  host.querySelector('[data-graph-filter]').addEventListener('change', event => {
    state.filter = event.target.value;
    draw();
  }, {signal});
  host.addEventListener('click', event => {
    const mode = event.target.closest('[data-graph-mode]');
    const node = event.target.closest('[data-table-index]');
    const field = event.target.closest('[data-field-index]');
    const related = event.target.closest('[data-related-table]');
    const details = event.target.closest('[data-graph-details]');
    const start = node?.querySelector('.kg-orb').getBoundingClientRect();
    if (mode) {
      switchMode(mode.dataset.graphMode);
    } else if (node || related) {
      state.table = tables[Number(node?.dataset.tableIndex ?? related.dataset.relatedTable)];
      state.fieldId = related?.dataset.relatedField || null;
      switchMode('focus');
      setBrowseFilters();
    } else if (field) {
      const id = state.table.fields[Number(field.dataset.fieldIndex)]?.id;
      state.fieldId = state.fieldId === id ? null : id;
    } else if (details) {
      state.details = !state.details;
    } else {
      return;
    }
    draw();
    if (node || related) {
      const selected = host.querySelector('.kg-planet.is-selected');
      if (node) {
        host.querySelector('.kg-toolbar').scrollIntoView({block: 'nearest'});
        flyToPlanet(start, selected.querySelector('.kg-orb'));
      }
      selected.focus({preventScroll: true});
    } else if (field) {
      host.querySelector(`[data-field-index="${Number(field.dataset.fieldIndex)}"]`)?.focus({preventScroll: true});
    }
  }, {signal});
  host.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || state.mode !== 'focus' || event.target.matches('input, select')) return;
    switchMode('overview');
    draw();
    host.querySelector('[data-graph-mode="overview"]').focus();
  }, {signal});
  if (globalThis.ResizeObserver) {
    const panel = host.querySelector('.kg-scene');
    session.width = panel.clientWidth;
    session.observer = new ResizeObserver(() => {
      if (panel.clientWidth === session.width) return;
      session.width = panel.clientWidth;
      draw();
    });
    session.observer.observe(panel);
  }
  window.addEventListener('resize', draw, {signal});
  draw();
}

function neighboursFor(relations, visible, table, fieldId) {
  const neighbours = new Map();
  for (const relation of relations) {
    if (!relation.members.some(member => member.table.id === table.id && (!fieldId || member.field.id === fieldId))) continue;
    const partners = new Set(relation.members.map(member => member.table).filter(other => other.id !== table.id && visible.has(other.id)));
    for (const partner of partners) {
      if (!neighbours.has(partner)) neighbours.set(partner, []);
      neighbours.get(partner).push(relation);
    }
  }
  return [...neighbours].sort(([a], [b]) => a.index - b.index);
}

function renderGraph(host, state) {
  const {tables, table, search, source, fieldId, mode} = state;
  const focus = mode === 'focus';
  const panel = host.querySelector('.kg-panel');
  panel.classList.toggle('kg-is-focus', focus);
  host.querySelectorAll('[data-graph-mode]').forEach(button =>
    button.setAttribute('aria-pressed', String(button.dataset.graphMode === mode)));
  const details = host.querySelector('[data-graph-details]');
  details.setAttribute('aria-expanded', String(state.details && focus));
  const aside = host.querySelector('.kg-inspector');
  aside.hidden = !focus || !state.details;

  const scene = host.querySelector('.kg-scene');
  const atlas = scene.querySelector('.kg-atlas');
  const counts = host.querySelector('.kg-counts');
  const width = scene.clientWidth;
  if (width < 120) return;
  const pageTop = scene.getBoundingClientRect().top + window.scrollY;
  const minHeight = Math.max(420, Math.ceil(document.documentElement.clientHeight - pageTop - 22));
  const matched = tables.filter(item => (!source || item.source === source)
    && (!search || [item.source, sourceLabel(item.source), item.galaxy.name, item.display, item.name, item.physical_name,
      ...item.fields.flatMap(field => [field.name, field.label, field.definition])].join(' ').toLowerCase().includes(search)));
  if (!focus) {
    const galaxies = new Map();
    for (const item of matched) {
      const {galaxy} = item;
      if (!galaxies.has(galaxy.id)) galaxies.set(galaxy.id, {...galaxy, tables: []});
      galaxies.get(galaxy.id).tables.push(item);
    }
    const layout = overviewLayout([...galaxies.values()], width, minHeight);
    atlas.style.height = layout.height + 'px';
    const empty = tables.length ? '没有符合筛选条件的逻辑表。' : '当前账户没有可查看的逻辑表。';
    atlas.innerHTML = starfield(width, layout.height) + (layout.groups.length
      ? layout.groups.map(renderGalaxy).join('') : `<p class="kg-empty kg-no-matches">${empty}</p>`);
    counts.textContent = `${galaxies.size} 个来源组 · ${matched.length} 张表`;
    return;
  }

  const relations = state.relations.filter(relation => state.filter === 'all'
    || (state.filter === 'registered') === registered(relation));
  const visible = new Set(matched.map(item => item.id));
  if (search) {
    const matches = new Set(visible);
    for (const relation of relations) {
      if (relation.members.some(member => matches.has(member.table.id))) {
        for (const member of relation.members) if (!source || member.table.source === source) visible.add(member.table.id);
      }
    }
  }
  const neighbours = neighboursFor(relations, visible, table, fieldId);
  const layout = focusLayout(neighbours.length, width, minHeight);
  atlas.style.height = layout.height + 'px';
  atlas.innerHTML = starfield(width, layout.height) + nebula(0, 0, width, layout.height, table.galaxy.hue, -17)
    + relationLayer(neighbours, layout)
    + planet(table, layout.center, layout.core.width, true)
    + neighbours.map(([item], index) => planet(item, layout.points[index], layout.labelWidth)).join('');
  counts.textContent = `${table.display} · ${neighbours.length} 张关联表`;
  const oldScroll = aside.dataset.table === table.id ? aside.scrollTop : 0;
  const fieldListOpen = aside.dataset.table !== table.id || aside.querySelector('details')?.open;
  aside.innerHTML = inspector(table, relations, fieldId);
  aside.querySelector('.kg-table-fields').open = fieldListOpen;
  aside.dataset.table = table.id;
  aside.scrollTop = oldScroll;
}

function planet(table, point, width, selected = false) {
  const size = (table.planetDiameter + (selected ? 22 : 0)).toFixed(2);
  return `<button type="button" class="kg-planet ${selected ? 'is-selected' : ''}" data-table-index="${table.index}"
    ${selected ? 'aria-current="true"' : ''}
    aria-label="${esc(table.display)}，${esc(sourceLabel(table.source))}，${table.fields.length} 个字段"
    title="${esc(table.display)} · ${esc(sourceLabel(table.source))} · ${table.fields.length} 个字段"
    style="left:${point.x}px; top:${point.y}px; width:${width}px; --kg-hue:${table.galaxy.hue}; --kg-planet-size:${size}px">
    <span class="kg-planet-marker" aria-hidden="true"><i class="kg-orb"></i></span>
    <strong>${esc(table.display)}</strong>
    ${selected ? `<small>${esc(sourceLabel(table.source))}</small>` : ''}
  </button>`;
}

function flyToPlanet(start, orb) {
  if (!orb?.animate || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const end = orb.getBoundingClientRect();
  const x = start.left + start.width / 2 - end.left - end.width / 2;
  const y = start.top + start.height / 2 - end.top - end.height / 2;
  if (Math.abs(y) > window.innerHeight * .85) return;
  orb.animate([{transform: `translate(${x}px,${y}px) scale(${start.width / end.width})`},
    {transform: 'none'}], {duration: 390, easing: 'cubic-bezier(.22,.72,.25,1)'});
}

function starfield(width, height) {
  const random = seededRandom('medfabric-atlas-dust');
  const dots = Array.from({length: Math.min(800, Math.ceil(width * height / 1700))}, () => {
    const x = Math.round(random() * width), y = Math.round(random() * height);
    return `<circle cx="${x}" cy="${y}" r="${(.32 + random() * 1.05).toFixed(2)}" opacity="${(.1 + random() * .45).toFixed(2)}"/>`;
  });
  return `<svg class="kg-starfield" width="${width}" height="${height}" aria-hidden="true">${dots.join('')}</svg>`;
}

function nebula(x, y, width, height, hue, angle) {
  return `<div class="kg-nebula" aria-hidden="true" style="left:${x}px; top:${y}px; width:${width}px; height:${height}px; --kg-hue:${hue}; --kg-tilt:${angle}deg"></div>`;
}

function renderGalaxy(layout) {
  const {group, x, y, width, height, core, center, labelWidth, points} = layout;
  const tilt = group.id === 'group:medical' ? -14 : group.id === 'source:doris-nju' ? 6 : 12;
  return `<section class="kg-galaxy" data-galaxy="${esc(group.id)}" aria-label="${esc(group.name)}"
    style="left:${x}px; top:${y}px; width:${width}px; height:${height}px; --kg-hue:${group.hue}">
    ${nebula(0, 0, width, height, group.hue, tilt)}
    <div class="kg-galaxy-core" style="left:${center.x}px;top:${center.y}px;width:${core.width}px">
      <h2 title="${esc(group.name)}">${esc(group.name)}</h2><span>${group.tables.length} 张表</span>
    </div>
    ${group.tables.map((table, i) => planet(table, points[i], labelWidth)).join('')}
  </section>`;
}

function relationLayer(neighbours, layout) {
  const {width, height, center, points, core, labelWidth} = layout;
  const labels = [];
  const edges = neighbours.map(([table, links], i) => {
    const end = points[i], start = center;
    const dx = end.x - start.x, dy = end.y - start.y;
    const bend = i % 2 ? .1 : -.1;
    const mid = {x: (start.x + end.x) / 2 + dy * bend, y: (start.y + end.y) / 2 - dx * bend};
    const names = [...new Set(links.map(link => link.label))];
    const label = names.length === 1 ? names[0] : names.length + ' 个字段';
    const span = Math.min(110, label.length * 10 + 10);
    const pos = [.51, .63, .39, .73, .29].map(t => ({
      x: (1-t)**2 * start.x + 2*t*(1-t) * mid.x + t*t * end.x,
      y: (1-t)**2 * start.y + 2*t*(1-t) * mid.y + t*t * end.y
    })).find(point =>
      (Math.abs(point.x - center.x) > (core.width + span) / 2 || Math.abs(point.y - center.y) > 51)
      && points.every(p => Math.abs(point.x - p.x) > (labelWidth + span) / 2 || Math.abs(point.y - p.y) > 32)
      && labels.every(p => Math.abs(point.x - p.x) > (p.span + span) / 2 || Math.abs(point.y - p.y) > 18));
    if (pos) labels.push({...pos, span});
    return `<g class="kg-connection ${links.some(registered) ? 'kg-connection-solid' : ''}" style="--kg-edge-hue:${table.galaxy.hue}">
      <title>${esc(names.join('、'))}</title>
      <path d="M${start.x},${start.y} Q${mid.x},${mid.y} ${end.x},${end.y}"/>
      ${pos ? `<text x="${pos.x}" y="${pos.y}" dominant-baseline="middle" text-anchor="middle">${esc(label)}</text>` : ''}
    </g>`;
  });
  return `<svg class="kg-edges" width="${width}" height="${height}" aria-hidden="true">${edges.join('')}</svg>`;
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
