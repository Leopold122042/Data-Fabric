import {overviewLayout} from './topic1-layout.js?v=0.1.57';
import {mountGalaxySky} from './galaxy-sky.js?v=0.1.57';
import {fieldLayout, bindFieldDrag} from './field-constellation.js?v=0.1.57';
import {mountStarLayer, mountEdgeLayer} from './atlas-stars.js?v=0.1.57';
import {mountOverviewDrag} from './overview-drag.js?v=0.1.57';

const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[char]));

const sourceNames = {
  his: '医院 HIS', lis: '检验 LIS', pacs: '影像 PACS', iot: '监护 IoT', emr: '电子病历 EMR',
  'SRC-CLINICAL-OPERATIONS': '临床运营主题库', 'doris-nju': '南大医疗 Doris', openalex: 'OpenAlex 文献'
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
function sourceLabel(table) {
  return table.source_label || (Object.hasOwn(sourceNames, table.source) ? sourceNames[table.source] : table.source || '未登记来源');
}
const sessions = new WeakMap();
const medicalSources = new Set(['his', 'lis', 'pacs', 'iot', 'emr', 'SRC-CLINICAL-OPERATIONS']);
function galaxyFor(table) {
  const id = String(table.source ?? '');
  if (medicalSources.has(id)) return {id: 'group:medical', name: '合成医疗演示', hue: 186};
  const hash = [...id].reduce((value, char) => Math.imul(value ^ char.codePointAt(0), 16777619), 2166136261);
  const hue = id === 'openalex' ? 36 : id === 'doris-nju' ? 258 : (hash >>> 0) % 360;
  return {id: 'source:' + id, name: sourceLabel(table), hue};
}

export async function renderKnowledgeGraph(host) {
  if (!host) return;
  const previous = sessions.get(host);
  previous?.controller.abort();
  previous?.observer?.disconnect();
  const session = {controller: new AbortController(), pins: previous?.pins || {fields: new Map(), groups: new Map(), tables: new Map()}};
  sessions.set(host, session);
  const {signal} = session.controller;
  host.innerHTML = '<div class="kg-loading" role="status">正在载入星图…</div>';

  try {
    const response = await fetch('/api/portal/catalog?include_relations=true', {
      credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}, signal
    });
    if (!response.ok) throw new Error('目录接口返回 ' + response.status);
    const catalog = await response.json();
    if (signal.aborted) return;
    const catalogTables = catalog.tables || [];
    const maxFields = catalogTables.reduce((max, table) => Math.max(max, table.fields.length), 1);
    const tables = catalogTables.map(table => {
      const shortName = (table.physical_name || table.name).replace(/^cdm\./, '').split('(')[0];
      const alias = table.source === 'openalex' && Object.hasOwn(openalexLabels, table.name) && openalexLabels[table.name];
      const display = table.label && table.label !== table.name ? table.label : alias || shortName;
      const starSize = 19 + 17 * Math.sqrt(table.fields.length / maxFields);
      return {...table, shortName, display, starSize, galaxy: galaxyFor(table),
        fieldsById: new Map(table.fields.map(field => [field.id, field]))};
    }).sort((a, b) => sourceRank(a.source) - sourceRank(b.source)
      || String(a.source).localeCompare(String(b.source)) || a.name.localeCompare(b.name, 'zh-CN'));
    const tableMap = new Map(tables.map((table, index) => [table.id, {...table, index}]));
    // Keep hand-placed objects across catalog refreshes by ID. Removed tables
    // and fields must not leave a reset handle in a newly scoped catalog.
    for (const id of session.pins.tables.keys()) if (!tableMap.has(id)) session.pins.tables.delete(id);
    for (const [id, pins] of session.pins.fields) {
      for (const fieldId of pins.keys()) if (!tableMap.get(id)?.fieldsById.has(fieldId)) pins.delete(fieldId);
      if (!pins.size) session.pins.fields.delete(id);
    }
    const liveGroups = new Set(tables.map(table => table.galaxy.id));
    for (const id of session.pins.groups.keys()) if (!liveGroups.has(id)) session.pins.groups.delete(id);
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
  const index = sourceOrder.indexOf(source);
  return index < 0 ? sourceOrder.length : index;
}

function mountGraph(host, session, tables, relations) {
  const preferred = tables.find(table => table.source === 'his' && table.shortName === 'patient');
  const state = {table: preferred || tables[0], fieldId: null, mode: 'overview',
    search: '', source: '', filter: 'all', overviewFilters: {}, details: true, paused: false};
  const sources = new Map(tables.map(table => [table.source, sourceLabel(table)]));
  host.innerHTML = `<section class="kg-panel" aria-label="数据世界">
    <header class="kg-heading">
      <div><h1>数据星图</h1><p class="kg-instructions"></p></div>
      <dl class="kg-metrics">
        <div><dt>来源组</dt><dd>${new Set(tables.map(t => t.galaxy.id)).size}</dd></div>
        <div><dt>逻辑表</dt><dd>${tables.length}</dd></div>
        <div><dt>字段</dt><dd>${tables.reduce((sum, t) => sum + t.fields.length, 0)}</dd></div>
      </dl>
    </header>
    <div class="kg-window">
      <div class="kg-toolbar">
        <div class="kg-modes" role="group" aria-label="图谱视图">
          <button type="button" data-graph-mode="overview"><span aria-hidden="true">✧</span> 星系总览</button>
          <button type="button" data-graph-mode="focus" ${tables.length ? '' : 'disabled'}>字段探索</button>
        </div>
        <label class="kg-input-label"><span>来源</span><select class="kg-search" data-graph-source>
          <option value="">全部来源</option>${[...sources].map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`).join('')}
        </select></label>
        <label class="kg-input-label kg-table-picker"><span>当前表</span><select class="kg-search" data-graph-table>
          ${tables.map(t => `<option value="${t.index}">${esc(t.display)} · ${esc(sourceLabel(t))}</option>`).join('')}
        </select></label>
        <label class="kg-input-label kg-relation-filter"><span>连线</span><select class="kg-search" data-graph-filter>
          <option value="all">全部关系</option><option value="registered">已确认与已登记</option>
          <option value="candidates">同名与语义候选</option>
        </select></label>
        <label class="kg-search-box"><i aria-hidden="true"></i><input class="kg-search" aria-label="查找表名、字段或来源" data-graph-search type="search" placeholder="查找表、字段或来源"></label>
        <button type="button" class="kg-command" data-graph-details aria-controls="kg-fields">字段详情</button>
        <button type="button" class="kg-motion" data-graph-motion aria-pressed="false" aria-label="暂停星图动效" title="暂停星图动效"><i aria-hidden="true"></i></button>
      </div>
      <div class="kg-layout"><div class="kg-scene" aria-label="逻辑表星图">
        <div class="kg-atlas">
          <canvas class="kg-sky" aria-hidden="true"></canvas>
          <svg class="kg-edges" aria-hidden="true"></svg>
          <div class="kg-captions"></div>
          <div class="kg-stars"></div>
          <i class="kg-target" aria-hidden="true" hidden></i>
          <button type="button" class="kg-reset" data-reset-layout hidden>还原布局</button>
        </div><div class="kg-edge-readout" hidden></div>
      </div><aside id="kg-fields" class="kg-inspector" aria-label="字段与关联详情" hidden></aside></div>
      <footer class="kg-footer"><div class="kg-legend"></div><p class="kg-counts" role="status"></p></footer>
    </div>
  </section>`;

  const {signal} = session.controller;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const panel = host.querySelector('.kg-panel');
  const atlas = panel.querySelector('.kg-atlas');
  const aside = panel.querySelector('.kg-inspector');
  const search = panel.querySelector('[data-graph-search]');
  const sourceSelect = panel.querySelector('[data-graph-source]');
  const tablePicker = panel.querySelector('[data-graph-table]');
  const detailsButton = panel.querySelector('[data-graph-details]');
  const motionButton = panel.querySelector('[data-graph-motion]');
  const captions = atlas.querySelector('.kg-captions');
  const target = atlas.querySelector('.kg-target');
  const svg = atlas.querySelector('.kg-edges');
  const stars = mountStarLayer(atlas.querySelector('.kg-stars'), reducedMotion, signal);
  const edges = mountEdgeLayer(svg, relationLabels, panel.querySelector('.kg-edge-readout'));
  signal.addEventListener('abort', () => edges.clear(), {once: true});
  const fieldButton = 'button[data-star-field], button[data-field-index]';
  function highlightField(node) {
    const button = node?.closest?.(fieldButton);
    edges.highlight(button ? Number(button.dataset.starField ?? button.dataset.fieldIndex) : null);
  }
  for (const name of ['pointerover', 'focusin']) {
    panel.addEventListener(name, event => highlightField(event.target), {signal});
  }
  for (const name of ['pointerout', 'focusout']) {
    panel.addEventListener(name, event => highlightField(event.relatedTarget), {signal});
  }
  const sky = mountGalaxySky(atlas.querySelector('canvas'), signal);
  const positions = session.pins.fields;
  const resetButton = panel.querySelector('[data-reset-layout]');
  const overview = mountOverviewDrag(atlas, signal, stars, sky, session.pins, () => draw(false), updateReset);
  function updateReset() {
    resetButton.hidden = state.mode === 'focus' ? !positions.has(state.table?.id) : !overview.hasPins();
  }
  const drag = bindFieldDrag(atlas, signal, positions, (index, point) => {
    const button = atlas.querySelector(`[data-star-field="${index}"]:not(:disabled)`);
    if (button) {
      button.style.left = point.x + 'px';
      button.style.top = point.y + 'px';
    }
    if (state.table.fields[index].id === state.fieldId) {
      target.getAnimations().forEach(animation => animation.cancel());
      placeTarget(point);
    }
    edges.move(index, point);
  }, updateReset);
  const searchable = new Map(tables.map(t => [t.id, [t.source, sourceLabel(t), t.galaxy.name, t.display,
    t.name, t.physical_name, ...t.fields.flatMap(f => [f.name, f.label, f.definition])].join(' ').toLowerCase()]));
  let lastFocus, lastInspector, lastWidth, lastHeight;
  let resizeFrame = 0;

  function placeTarget(point) {
    target.style.left = point.x + 'px';
    target.style.top = point.y + 'px';
  }
  function setFilters({source = '', search: value = ''} = {}) {
    sourceSelect.value = state.source = source;
    search.value = value;
    state.search = value.trim().toLowerCase();
  }
  function switchMode(mode) {
    if (mode === state.mode) return;
    if (mode === 'focus') {
      state.overviewFilters = {source: state.source, search: search.value};
      setFilters();
    } else {
      setFilters(state.overviewFilters);
    }
    state.mode = mode;
  }
  function selectTable(table, field = null) {
    switchMode('focus');
    setFilters();
    state.table = table;
    state.fieldId = field;
  }

  function draw(animate = true) {
    const {table, fieldId, source, search: term} = state;
    const focus = state.mode === 'focus' && !!table;
    const motion = animate && !state.paused && !reducedMotion.matches;
    const previous = motion ? stars.snapshot() : null;
    const previousTarget = motion && !target.hidden ? target.getBoundingClientRect() : null;
    // Cancel a gesture against the old geometry before replacing any paths.
    drag.setLayout(null, null);
    overview.clearScene();
    panel.classList.toggle('kg-is-focus', focus);
    aside.hidden = !focus || !state.details;
    updateReset();
    const instructions = focus
      ? (table.fields.length ? '拖动字段星调整位置，点选字段查看关联。按 Esc 返回星系总览。' : '这张表在目录中暂无可见字段。按 Esc 返回星系总览。')
      : '拖动星系名称可移动整组，拖动表星可单独摆放，点选表星可展开字段。';
    panel.querySelector('.kg-instructions').textContent = instructions;
    panel.querySelectorAll('[data-graph-mode]').forEach(button =>
      button.setAttribute('aria-pressed', String(button.dataset.graphMode === state.mode)));
    detailsButton.setAttribute('aria-expanded', String(!aside.hidden));
    if (table) tablePicker.value = table.index;
    const width = atlas.clientWidth;
    if (width < 120) return;
    const top = atlas.getBoundingClientRect().top + window.scrollY;
    const minHeight = Math.max(380, Math.round(document.documentElement.clientHeight - top - 76));
    lastWidth = width;
    lastHeight = document.documentElement.clientHeight;
    const matched = tables.filter(t => (!source || source === t.source) && (!term || searchable.get(t.id).includes(term)));
    let nodes = [], skies = [], height, caption, countText, legend, origin;
    const sceneChanged = lastFocus !== (focus ? table.id : null);
    if (!focus) {
      const groups = new Map();
      for (const item of matched) {
        if (!groups.has(item.galaxy.id)) groups.set(item.galaxy.id, {...item.galaxy, tables: []});
        groups.get(item.galaxy.id).tables.push(item);
      }
      const layout = overviewLayout([...groups.values()], width, minHeight);
      overview.prepare(layout);
      height = layout.height;
      caption = layout.groups.map(({group, x, y, center, core}) => {
        const cx = x + center.x, cy = y + center.y;
        return `<div class="kg-galaxy-core" style="left:${cx}px;top:${cy}px;width:${core.width}px;--kg-hue:${group.hue}">
          <h2><button type="button" data-drag-galaxy="${esc(group.id)}" aria-label="移动星系 · ${esc(group.name)}" aria-keyshortcuts="ArrowLeft ArrowRight ArrowUp ArrowDown"
            title="${esc(group.name)} · 拖动或按方向键移动星系">${esc(group.name)}</button></h2><span>${group.tables.length} 张逻辑表</span></div>`;
      }).join('') || `<p class="kg-no-matches">${tables.length ? '没有找到匹配的表，请更换搜索词或来源。' : '当前账户没有可查看的逻辑表。'}</p>`;
      layout.groups.forEach(({group, x, y, width: w, height: h, center, points, labelWidth, tilt}) => {
        skies.push({id: group.id, x: x + center.x, y: y + center.y, rx: w * .48,
          ry: Math.min(h * .46, w * .29), hue: group.hue, tilt});
        group.tables.forEach((t, index) => nodes.push(tableStar(t,
          {...points[index], cluster: group.id, x: x + points[index].x, y: y + points[index].y}, labelWidth)));
      });
      countText = `${groups.size} 个星系 · ${matched.length} 张逻辑表`;
      legend = '<span><i class="kg-legend-star"></i>逻辑表</span><span>星芒大小随字段数量变化</span>';
      edges.clear();
      target.hidden = true;
    } else {
      const visibleRelations = relations.filter(r => state.filter === 'all' || (state.filter === 'registered') === registered(r));
      const visible = new Set(matched.map(item => item.id));
      if (term) {
        const matches = new Set(visible);
        for (const relation of visibleRelations) {
          if (relation.members.some(member => matches.has(member.table.id))) {
            for (const {table: partner} of relation.members) if (!source || partner.source === source) visible.add(partner.id);
          }
        }
      }
      const neighbours = neighboursFor(visibleRelations, visible, table, fieldId);
      const layout = fieldLayout(table, neighbours, width, minHeight, positions);
      height = layout.height;
      atlas.style.setProperty('--kg-hue', table.galaxy.hue);
      skies = [{id: table.galaxy.id, ...layout.center, rx: Math.min(width * .4, layout.fieldAreaHeight * .68),
        ry: layout.fieldAreaHeight * .31, hue: table.galaxy.hue, tilt: -17}];
      nodes = [tableStar(table, layout.center, layout.core.width, true),
        ...table.fields.map((field, index) => ({...layout.fields[index], key: `f:${table.index}:${index}`,
          field: true, index, hue: table.galaxy.hue, size: field.id === fieldId ? 24 : 17,
          label: field.label || field.name, description: `${field.label || field.name} · ${field.name} · ${field.type || '字段'} · 属于 ${table.display}`,
          selected: field.id === fieldId})),
        ...neighbours.map(([t, links], i) => {
          const star = tableStar(t, layout.points[i], layout.labelWidth);
          const names = [...new Set(links.map(link => link.label))];
          star.caption = names.length === 1 ? names[0] : names.length + ' 组关联';
          star.description += ' · ' + names.join('、');
          return star;
        })];
      origin = previous?.get('t:' + table.index);
      if (!origin) {
        const rect = atlas.getBoundingClientRect();
        origin = {x: rect.left + layout.center.x, y: rect.top + layout.center.y};
      }
      edges.draw(layout, table, neighbours, fieldId);
      const selectedIndex = fieldId == null ? -1 : table.fields.findIndex(f => f.id === fieldId);
      target.hidden = selectedIndex < 0;
      if (!target.hidden) {
        const point = layout.fields[selectedIndex];
        placeTarget(point);
        target.getAnimations().forEach(animation => animation.cancel());
        if (previousTarget && !sceneChanged) {
          const end = target.getBoundingClientRect();
          target.animate([{translate: `${previousTarget.left - end.left}px ${previousTarget.top - end.top}px`},
            {translate: '0px 0px'}], {duration: 300, easing: 'cubic-bezier(.22,.75,.24,1)'});
        }
      }
      drag.setLayout(table, layout);
      caption = neighbours.length ? `<div class="kg-relations-divider" style="top:${layout.relationTop}px"><span>关联表</span></div>` : '';
      countText = `${table.fields.length} 个字段 · ${neighbours.length} 张关联表`;
      legend = '<span><i class="kg-member-key"></i>字段归属</span><span><i></i>已确认或已登记</span><span><i class="kg-candidate-key"></i>候选关联</span>';
      const inspectorKey = JSON.stringify([table.id, fieldId, state.filter]);
      if (lastInspector !== inspectorKey) {
        const sameTable = aside.dataset.table === table.id;
        const oldScroll = sameTable ? aside.scrollTop : 0;
        const listOpen = !sameTable || aside.querySelector('details')?.open;
        aside.innerHTML = inspector(table, visibleRelations, fieldId);
        aside.querySelector('.kg-table-fields').open = listOpen;
        aside.dataset.table = table.id;
        aside.scrollTop = oldScroll;
        if (motion) aside.firstElementChild.animate([{opacity: .45}, {opacity: 1}], {duration: 230});
        lastInspector = inspectorKey;
      }
    }
    atlas.style.height = height + 'px';
    if (captions.innerHTML !== caption) captions.innerHTML = caption;
    if (sceneChanged && motion) captions.animate([{opacity: 0}, {opacity: 1}], {duration: 400});
    if (focus && motion) svg.animate([{opacity: .1}, {opacity: 1}], {duration: sceneChanged ? 490 : 230});
    sky.setScene({width, height, galaxies: skies});
    stars.draw(nodes, previous, origin, motion);
    panel.querySelector('.kg-counts').textContent = countText;
    panel.querySelector('.kg-legend').innerHTML = legend;
    lastFocus = focus ? table.id : null;
  }

  search.addEventListener('input', () => { state.search = search.value.trim().toLowerCase(); draw(); }, {signal});
  sourceSelect.addEventListener('change', () => {
    state.source = sourceSelect.value;
    const next = tables.find(t => t.source === state.source);
    if (state.mode === 'focus' && next && state.table.source !== state.source) {
      state.table = next;
      state.fieldId = null;
    }
    draw();
  }, {signal});
  tablePicker.addEventListener('change', () => { selectTable(tables[Number(tablePicker.value)]); draw(); }, {signal});
  panel.querySelector('[data-graph-filter]').addEventListener('change', event => {
    state.filter = event.target.value;
    draw();
  }, {signal});
  panel.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button || button.disabled) return;
    const data = button.dataset;
    if (button === resetButton) {
      if (state.mode === 'focus') positions.delete(state.table.id);
      else overview.reset();
      draw();
      return;
    }
    if (button === motionButton) {
      state.paused = !state.paused;
      panel.classList.toggle('kg-paused', state.paused);
      sky.setPaused(state.paused);
      stars.setPaused(state.paused);
      motionButton.setAttribute('aria-pressed', String(state.paused));
      motionButton.title = state.paused ? '继续星图动效' : '暂停星图动效';
      motionButton.setAttribute('aria-label', motionButton.title);
      return;
    }
    if (data.graphMode) {
      if (data.graphMode === state.mode) return;
      switchMode(data.graphMode);
    } else if (data.tableIndex != null || data.relatedTable != null) {
      selectTable(tables[Number(data.tableIndex ?? data.relatedTable)], data.relatedField || null);
    } else if (data.starField != null || data.fieldIndex != null) {
      const id = state.table.fields[Number(data.starField ?? data.fieldIndex)].id;
      state.fieldId = state.fieldId === id ? null : id;
    } else if (button === detailsButton) {
      state.details = !state.details;
    } else {
      return;
    }
    draw();
    if (data.starField != null || data.relatedTable != null) aside.scrollTop = 0;
    if (data.relatedTable != null || data.tableIndex != null) {
      const core = atlas.querySelector('.is-core:not(:disabled)');
      core?.focus({preventScroll: true});
      if (core) {
        const bounds = core.getBoundingClientRect();
        if (bounds.bottom < 0 || bounds.top > window.innerHeight) core.scrollIntoView({block: 'center'});
      }
    } else if (data.fieldIndex != null) {
      aside.querySelector(`[data-field-index="${Number(data.fieldIndex)}"]`)?.focus({preventScroll: true});
    }
  }, {signal});
  panel.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || state.mode !== 'focus' || event.target.matches('input, select')) return;
    switchMode('overview');
    draw();
    panel.querySelector('[data-graph-mode="overview"]').focus();
  }, {signal});
  const queueResize = () => {
    if (resizeFrame) return;
    resizeFrame = requestAnimationFrame(() => {
      resizeFrame = 0;
      if (!signal.aborted && (lastWidth !== atlas.clientWidth || lastHeight !== document.documentElement.clientHeight)) draw(false);
    });
  };
  if (globalThis.ResizeObserver) {
    session.observer = new ResizeObserver(queueResize);
    session.observer.observe(panel.querySelector('.kg-scene'));
  }
  window.addEventListener('resize', queueResize, {signal});
  signal.addEventListener('abort', () => cancelAnimationFrame(resizeFrame), {once: true});
  draw(false);
}

function tableStar(table, point, width, core = false) {
  return {...point, width, key: 't:' + table.index, index: table.index, core,
    size: table.starSize + (core ? 26 : 0), label: table.display, hue: table.galaxy.hue,
    caption: core ? sourceLabel(table) : '',
    description: `${table.display} · ${sourceLabel(table)} · ${table.fields.length} 个字段`};
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
  const selected = field ? links.filter(link => link.members.some(member => member.table.id === table.id && member.field.id === fieldId)) : links;
  const details = selected.map(relation => {
    const ownFields = relation.members.filter(member => member.table.id === table.id).map(member => member.field.name);
    const partners = relation.members.filter(member => member.table.id !== table.id);
    return `<section class="kg-related-panel">
      <h4>${esc(relation.label)} <span class="kg-relation-kind" data-kind="${esc(relation.kind)}">${esc(relationLabels[relation.kind])}</span></h4>
      <p>${esc(ownFields.join('、'))} · ${esc(relation.note)}</p>
      <ul>${partners.map(member => `<li><button type="button" data-related-table="${member.table.index}"
        data-related-field="${esc(member.field.id)}">
        <span>${esc(sourceLabel(member.table))} · ${esc(member.table.display)}</span>
        <b>${esc(member.field.label)} <code>${esc(member.field.name)}</code></b></button></li>`).join('')}</ul>
    </section>`;
  }).join('');
  const summary = field ? `<p>所属表 · ${esc(table.display)}</p><h3>${esc(field.label || field.name)}</h3>
      <span><code>${esc(field.name)}</code> · ${esc(field.type)}</span>
      <div class="kg-definition">${esc(field.definition || '字段含义待补充')}</div>
      <span>${field.status === 'confirmed' ? '已确认定义' : '定义待确认'} · ${selected.length} 组关联</span>`
    : `<p>已选逻辑表</p><h3>${esc(table.display)}</h3>
      <span>${esc(sourceLabel(table))} / ${esc(table.physical_name || table.name)} / ${table.fields.length} 个字段</span>`;
  return `<div class="kg-inspector-heading">${summary}
    </div>
    <details class="kg-table-fields" open><summary>表字段</summary><div class="kg-field-list">${fields || '<p class="kg-empty">目录中暂无可见字段。</p>'}</div></details>
    <h4 class="kg-relationship-heading">${field ? esc(field.label) : '本表'}的关联</h4>
    ${details || '<p class="kg-empty">当前筛选下没有关联字段。</p>'}`;
}
