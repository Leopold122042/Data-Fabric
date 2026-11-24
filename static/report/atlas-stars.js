// Keep each catalogue object attached to its own button across camera moves.
// Measuring the current (possibly in-flight) position makes rapid navigation reversible.
export function mountStarLayer(layer, reducedMotion, signal) {
  const records = new Map();
  const clusters = new Map();
  const easing = 'cubic-bezier(.2,.75,.2,1)';
  let paused = false, grabbing = false;

  function syncOrbits() {
    const still = paused || grabbing || reducedMotion.matches || document.hidden
      || !!layer.querySelector('.kg-table-star:is(:hover,:focus-visible):not(:disabled)');
    for (const {button, orbit} of records.values()) {
      if (!orbit) continue;
      if (still || button.disabled) orbit.pause();
      else orbit.play();
    }
  }
  for (const name of ['pointerover', 'pointerout', 'focusin', 'focusout']) {
    layer.addEventListener(name, () => queueMicrotask(syncOrbits), {signal});
  }
  document.addEventListener('visibilitychange', syncOrbits, {signal});
  reducedMotion.addEventListener('change', syncOrbits, {signal});
  signal.addEventListener('abort', () => {
    for (const {button} of records.values()) button.getAnimations().forEach(animation => animation.cancel());
    records.clear();
  }, {once: true});

  function snapshot() {
    const positions = new Map();
    for (const [key, {button}] of records) {
      const rect = button.getBoundingClientRect();
      positions.set(key, {x: rect.left + rect.width / 2, y: rect.top + 14, opacity: Number(getComputedStyle(button).opacity)});
    }
    return positions;
  }

  function draw(nodes, previous, origin, animate) {
    const bounds = layer.getBoundingClientRect();
    for (const cluster of clusters.values()) cluster.style.translate = '';
    const visible = new Set();
    const motion = animate && !reducedMotion.matches;
    for (const node of nodes) {
      const {key, x, y} = node;
      visible.add(key);
      let record = records.get(key);
      const isNew = !record;
      if (isNew) {
        const button = document.createElement('button');
        button.type = 'button';
        button.innerHTML = '<span class="kg-marker" aria-hidden="true"><i class="kg-light"></i></span><strong></strong><small></small>';
        record = {button};
        records.set(key, record);
      }
      const {button} = record;
      let parent = layer;
      if (node.cluster != null) {
        if (!clusters.has(node.cluster)) {
          const cluster = document.createElement('div');
          cluster.className = 'kg-cluster-stars';
          layer.append(cluster);
          clusters.set(node.cluster, cluster);
        }
        parent = clusters.get(node.cluster);
      }
      if (button.parentElement !== parent) parent.append(button);
      record.motion?.cancel();
      record.exit?.cancel();
      record.orbit?.cancel();
      record.exit = null;
      record.orbit = null;
      record.motion = null;
      button.disabled = false;
      button.className = 'kg-star' + (node.field ? ' kg-field-star' : ' kg-table-star')
        + (node.core ? ' is-core' : '') + (node.selected ? ' is-active' : '');
      button.dataset[node.field ? 'starField' : 'tableIndex'] = node.index;
      button.style.left = x + 'px';
      button.style.top = y + 'px';
      button.style.width = node.width + 'px';
      button.style.setProperty('--kg-hue', node.hue);
      button.style.setProperty('--kg-size', node.size + 'px');
      button.style.setProperty('--kg-phase', (-node.index * 1.79).toFixed(2) + 's');
      button.style.setProperty('--kg-period', (3.8 + node.index % 7 * .43).toFixed(2) + 's');
      button.querySelector('strong').textContent = node.label;
      const caption = button.querySelector('small');
      caption.textContent = node.caption || '';
      caption.hidden = !node.caption;
      const movable = node.field || node.cluster != null;
      button.title = node.description + (movable ? ' · 拖动或按方向键移动' : '');
      if (movable) button.setAttribute('aria-keyshortcuts', 'ArrowLeft ArrowRight ArrowUp ArrowDown');
      else button.removeAttribute('aria-keyshortcuts');
      button.setAttribute('aria-label', node.description);
      if (node.field) button.setAttribute('aria-pressed', String(node.selected));
      if (node.core) button.setAttribute('aria-current', 'true');
      else button.removeAttribute('aria-current');
      if (node.orbit && button.animate) {
        record.orbit = button.animate(node.orbit.keyframes, {duration: node.orbit.duration, iterations: Infinity,
          easing: 'linear', composite: 'add'});
      }
      const start = previous?.get(key) || origin;
      if (!motion || !button.animate) continue;
      if (!start) {
        record.motion = button.animate([{opacity: 0, scale: .9}, {opacity: 1, scale: 1}], {duration: 320, easing});
        continue;
      }
      const dx = start.x - bounds.left - x, dy = start.y - bounds.top - y;
      if (!isNew && Math.hypot(dx, dy) < 1) continue;
      record.motion = button.animate([
        {translate: dx + 'px ' + dy + 'px', opacity: isNew ? 0 : (start.opacity ?? 1)},
        {translate: '0px 0px', opacity: 1}
      ], {duration: isNew ? 530 : 490, easing});
    }
    for (const [key, record] of records) {
      if (visible.has(key) || record.button.disabled) continue;
      const {button} = record;
      button.disabled = true;
      const remove = () => {
        button.getAnimations().forEach(animation => animation.cancel());
        button.remove();
        records.delete(key);
      };
      if (motion && button.animate) {
        record.orbit?.pause();
        const fade = button.animate([{opacity: previous?.get(key)?.opacity ?? 1}, {opacity: 0}], {duration: 150, fill: 'forwards'});
        record.exit = fade;
        fade.onfinish = () => { if (record.exit === fade && button.disabled) remove(); };
      } else {
        remove();
      }
    }
    syncOrbits();
  }
  return {snapshot, draw, setPaused(value) { paused = value; syncOrbits(); },
    setGrabbing(value) { grabbing = value; syncOrbits(); },
    moveCluster(id, dx, dy) { clusters.get(id).style.translate = dx + 'px ' + dy + 'px'; },
    stopFlight(index) {
      const record = records.get('t:' + index);
      record.orbit?.cancel();
      record.motion?.cancel();
      record.orbit = record.motion = null;
    }
  };
}

const svgNS = 'http://www.w3.org/2000/svg';
const svgNode = tag => document.createElementNS(svgNS, tag);

function controlPoint(start, end, bend, layout) {
  const dx = end.x - start.x, dy = end.y - start.y;
  return {x: Math.max(0, Math.min(layout.width, start.x + dx * .53 - dy * bend)),
    y: Math.max(0, Math.min(layout.height, start.y + dy * .53 + dx * bend))};
}

function curvePoint(start, mid, end, t) {
  const a = (1 - t) ** 2, b = 2 * (1 - t) * t, c = t * t;
  return {x: a * start.x + b * mid.x + c * end.x, y: a * start.y + b * mid.y + c * end.y};
}

// Only nearby vertical bands can collide, even in a tall, many-field atlas.
function labelSpace(width, height) {
  const rows = new Map();
  function check(x, y, w, h, reserve) {
    const left = x - w / 2, right = x + w / 2, top = y - h / 2, bottom = y + h / 2;
    if (!reserve && (left < 3 || right > width - 3 || top < 3 || bottom > height - 3)) return false;
    for (let i = Math.floor(top / 40); i <= Math.floor(bottom / 40); i++) {
      if (reserve) {
        if (!rows.has(i)) rows.set(i, []);
        rows.get(i).push({left, right, top, bottom});
      } else if (rows.get(i)?.some(box => box.left < right && left < box.right && box.top < bottom && top < box.bottom)) {
        return false;
      }
    }
    return true;
  }
  return {reserve: (x, y, w, h) => check(x, y, w, h, true), fits: (x, y, w, h) => check(x, y, w, h, false)};
}

const onCurve = [[0, 0]];
const callouts = [...onCurve];
for (const d of [16, 32, 52, 78, 112]) {
  const diagonal = Math.round(d / Math.SQRT2);
  callouts.push([-d, 0], [d, 0], [0, -d], [0, d],
    [-diagonal, -diagonal], [diagonal, -diagonal], [-diagonal, diagonal], [diagonal, diagonal]);
}

export function mountEdgeLayer(svg, relationLabels, readout) {
  const measure = document.createElement('canvas').getContext('2d');
  if (measure) measure.font = '600 10px system-ui, sans-serif';
  let layout, edges = [], byField = new Map(), active, hovered, frame;

  function setPath(edge) {
    const start = layout.fields[edge.index], {end} = edge;
    const mid = edge.mid = controlPoint(start, end, edge.bend, layout);
    edge.path.setAttribute('d', `M${start.x} ${start.y} Q${mid.x} ${mid.y} ${end.x} ${end.y}`);
  }

  function placeLabels() {
    frame = 0;
    const space = labelSpace(layout.width, layout.height);
    space.reserve(layout.center.x, layout.center.y + 17, layout.core.width + 3, 89);
    for (const p of layout.fields) space.reserve(p.x, p.y + 9, p.width + 3, 47);
    for (const p of layout.points) space.reserve(p.x, p.y + 17, layout.labelWidth + 3, 64);
    if (layout.points.length) space.reserve(layout.width / 2, layout.relationTop + 7, 68, 19);
    const focused = active != null || hovered != null;
    const membershipLimit = !focused && layout.fields.length > 8 ? 2 : Infinity;
    let memberships = 0;
    const missing = new Map();
    function rank(edge) {
      const ownership = edge.kind === 'membership';
      if (edge.index === active) return ownership ? 0 : 1;
      if (edge.index === hovered) return ownership ? 2 : 3;
      return ownership ? 5 : 4;
    }
    for (const edge of [...edges].sort((a, b) => rank(a) - rank(b))) {
      const urgent = edge.index === active || edge.index === hovered;
      const ownership = edge.kind === 'membership';
      const show = !ownership || urgent || (!focused && memberships < membershipLimit);
      const start = layout.fields[edge.index], {mid, end} = edge;
      const samples = show ? [.51, .65, .37, .76, .24, .85, .15, .93, .07].map(t => curvePoint(start, mid, end, t)) : [];
      let chosen;
      // Keep the full name and status on a selected relationship. Dense rows
      // often leave space beside their endpoints rather than directly above.
      const options = urgent ? [edge.options[0]] : edge.options;
      for (const [dx, dy] of urgent ? callouts : onCurve) {
        for (const option of options) {
          for (const p of samples) {
            const x = dx || dy ? Math.max(option.width / 2 + 4, Math.min(layout.width - option.width / 2 - 4, p.x + dx)) : p.x;
            const y = p.y + dy;
            if (!space.fits(x, y, option.width, 16)) continue;
            chosen = {x, y, p, shifted: x !== p.x || y !== p.y, option};
            break;
          }
          if (chosen) break;
        }
        if (chosen) break;
      }
      const {label, leader} = edge;
      if (!chosen) {
        if (urgent) {
          if (!missing.has(edge.summary)) missing.set(edge.summary, new Set());
          if (edge.destination) missing.get(edge.summary).add(edge.destination);
        }
        if (edge.placement !== null) {
          label.setAttribute('display', 'none');
          leader.setAttribute('display', 'none');
          edge.placement = null;
        }
        continue;
      }
      if (ownership) memberships++;
      const {x, y, p, shifted, option} = chosen;
      space.reserve(x, y, option.width, 16);
      const previous = edge.placement;
      if (previous?.x === x && previous.y === y && previous.option === option &&
        previous.p.x === p.x && previous.p.y === p.y) continue;
      edge.placement = chosen;
      label.removeAttribute('display');
      edge.text.data = option.text;
      label.setAttribute('x', x);
      label.setAttribute('y', y);
      if (shifted) {
        leader.setAttribute('x1', p.x);
        leader.setAttribute('y1', p.y);
        leader.setAttribute('x2', x);
        leader.setAttribute('y2', y);
        leader.removeAttribute('display');
      } else leader.setAttribute('display', 'none');
    }
    if (readout) {
      const text = [...missing].map(([summary, destinations]) =>
        destinations.size ? summary + ' ↔ ' + [...destinations].join('、') : summary).join('\n');
      if (readout.textContent !== text) readout.textContent = text;
      readout.hidden = !text;
    }
  }

  function clear() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    layout = null;
    edges = [];
    byField.clear();
    hovered = null;
    svg.replaceChildren();
    svg.classList.remove('has-selection');
    if (readout) {
      readout.hidden = true;
      readout.textContent = '';
    }
  }

  function draw(nextLayout, table, neighbours, fieldId) {
    clear();
    layout = nextLayout;
    const paths = document.createDocumentFragment(), labels = document.createDocumentFragment();
    const indexById = new Map(table.fields.map((field, i) => [field.id, i]));
    const textWidths = new Map();
    active = indexById.get(fieldId);
    function add(index, end, kind, bend, captions, description, summary, destination) {
      const path = svgNode('path'), label = svgNode('text'), leader = svgNode('line');
      path.setAttribute('class', kind === 'membership' ? 'kg-membership'
        : ['confirmed', 'reference'].includes(kind) ? 'kg-link-registered' : 'kg-link-candidate');
      label.setAttribute('class', 'kg-edge-label');
      leader.setAttribute('class', 'kg-label-leader');
      for (const node of [path, label, leader]) {
        node.dataset.kind = kind;
        node.dataset.fieldIndex = index;
        node.classList.toggle('is-active', index === active);
      }
      for (const node of [path, label]) {
        const title = svgNode('title');
        title.textContent = description;
        node.append(title);
      }
      label.setAttribute('dominant-baseline', 'central');
      label.setAttribute('text-anchor', 'middle');
      const text = document.createTextNode('');
      label.append(text);
      paths.append(path);
      labels.append(leader, label);
      const options = [...new Set(captions)].map(text => {
        if (!textWidths.has(text)) {
          const width = measure?.measureText(text).width ??
            Array.from(text).reduce((sum, char) => sum + (char.codePointAt(0) < 128 ? 6 : 10), 0);
          textWidths.set(text, 11 + Math.ceil(width));
        }
        return {text, width: textWidths.get(text)};
      });
      const edge = {index, end, kind, bend, path, leader, label, text, options, summary, destination};
      edges.push(edge);
      if (!byField.has(index)) byField.set(index, []);
      byField.get(index).push(edge);
      setPath(edge);
    }
    table.fields.forEach((field, i) => {
      const name = field.label && field.label !== field.name ? `${field.label}（${field.name}）` : field.name;
      const summary = name + ' 属于 ' + table.display;
      add(i, layout.center, 'membership', .075, ['属于 ' + table.display, '属于本表'], summary, summary);
    });
    neighbours.forEach(([partner, links], i) => {
      const groups = new Map();
      for (const link of links) {
        for (const member of link.members) {
          if (member.table.id !== table.id || (active != null && member.field.id !== fieldId)) continue;
          const index = indexById.get(member.field.id);
          if (!groups.has(index)) groups.set(index, new Set());
          groups.get(index).add(link);
        }
      }
      for (const [index, group] of groups) {
        const entries = [...group], own = table.fields[index];
        entries.forEach((link, lane) => {
          const kindName = relationLabels?.[link.kind] || link.kind;
          const targets = link.members.filter(m => m.table.id === partner.id).map(m => m.field.name).join('、');
          const description = [`${table.display}.${own.name} ↔ ${partner.display}.${targets}`,
            `${link.label} · ${kindName}`, link.note].filter(Boolean).join('\n');
          const bend = .075 + (lane - (entries.length - 1) / 2) * .14;
          const short = Array.from(link.label);
          const captions = [link.label + ' · ' + kindName, link.label];
          if (short.length > 10) captions.push(short.slice(0, 9).join('') + '…');
          const summary = (own.name === link.label ? '' : own.name + ' · ') + captions[0];
          add(index, layout.points[i], link.kind, bend, captions, description, summary, partner.display + '.' + targets);
        });
      }
    });
    svg.replaceChildren(paths, labels);
    svg.classList.toggle('has-selection', active != null);
    placeLabels();
  }
  function move(index, point) {
    if (!byField.has(index)) return;
    layout.fields[index] = point;
    for (const edge of byField.get(index)) setPath(edge);
    if (!frame) frame = requestAnimationFrame(placeLabels);
  }
  function highlight(index) {
    if (!layout || index === hovered) return;
    for (const edge of [...(byField.get(hovered) || []), ...(byField.get(index) || [])]) {
      for (const node of [edge.path, edge.label, edge.leader]) node.classList.toggle('is-highlighted', edge.index === index);
    }
    hovered = index;
    if (!frame) frame = requestAnimationFrame(placeLabels);
  }
  return {draw, move, highlight, clear};
}
