import {bindAtlasDrag} from './atlas-drag.js?v=0.1.57';

const fieldHeight = 54;
const edgeInset = 10;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

// Measure separation in label widths and heights. This leaves more room near
// the top and bottom of an ellipse, where adjacent names would otherwise touch.
function orbit(count, rx, ry, phase, center, labelWidth) {
  const arc = [0];
  const samples = 96;
  let lastX = rx / labelWidth, lastY = 0;
  for (let i = 1; i <= samples; i++) {
    const angle = i * 2 * Math.PI / samples;
    const x = rx * Math.cos(angle) / labelWidth;
    const y = ry * Math.sin(angle) / fieldHeight;
    arc.push(arc[i - 1] + Math.hypot(x - lastX, y - lastY));
    lastX = x;
    lastY = y;
  }
  return Array.from({length: count}, (_, i) => {
    const distance = ((phase + i / count) % 1) * arc[samples];
    const next = Math.max(1, arc.findIndex(length => length >= distance));
    const fraction = (distance - arc[next - 1]) / (arc[next] - arc[next - 1]);
    const angle = (next - 1 + fraction) * 2 * Math.PI / samples;
    return {x: Math.round(center.x + rx * Math.cos(angle)),
      y: Math.round(center.y + ry * Math.sin(angle)), width: labelWidth};
  });
}

function orbitalFields(count, width, areaHeight, labelWidth, core) {
  if (!count || count > 30) return null;
  const center = {x: width / 2, y: areaHeight / 2 - 3};
  const rx = Math.max(1, (width - labelWidth) / 2 - edgeInset - width * .025);
  const ry = Math.max(1, areaHeight / 2 - 51);
  const innerCount = count > 11 ? Math.round(count * .33) : 0;
  for (const phase of [.62, .69, .75, .81, .87, .94]) {
    const fields = [
      ...orbit(innerCount, rx * .55, ry * .55, phase + .08, center, labelWidth),
      ...orbit(count - innerCount, rx, ry, phase, center, labelWidth)
    ];
    const crowded = fields.some((p, i) =>
      (Math.abs(p.x - center.x) < (core.width + labelWidth) / 2 + 6 &&
        Math.abs(p.y - center.y) < 79) ||
      fields.slice(0, i).some(q => Math.abs(p.x - q.x) < labelWidth + 5 &&
        Math.abs(p.y - q.y) < fieldHeight));
    if (!crowded) return {center, fields};
  }
  return null;
}

// Dense catalogs or narrow viewports get staggered rows above and below the
// selected table. More fields extend the atlas vertically at constant type size.
function staggeredFields(count, width, areaHeight, columns, labelWidth) {
  const center = {x: width / 2, y: areaHeight / 2 - 3};
  const upper = Math.ceil(count / 2);
  const fields = Array.from({length: count}, (_, i) => {
    const above = i < upper;
    const index = above ? i : i - upper;
    const row = Math.floor(index / columns);
    const inRow = Math.min(columns, (above ? upper : count - upper) - row * columns);
    const pitch = Math.min(labelWidth * 1.55, (width - 2 * edgeInset) / inRow);
    return {x: Math.round(center.x + ((index % columns) - (inRow - 1) / 2) * pitch),
      y: Math.round(center.y + (above ? -1 : 1) * (87 + 62 * row)),
      width: labelWidth};
  });
  return {center, fields};
}

function boundedPoint(x, y, fieldWidth, width, areaHeight) {
  const margin = fieldWidth / 2 + edgeInset;
  return {x: clamp(x, margin, width - margin), y: clamp(y, 24, areaHeight - 39), width: fieldWidth};
}

export function fieldLayout(table, neighbours, width, minHeight, positions) {
  const count = table.fields.length;
  const fieldWidth = Math.min(width - 2 * edgeInset, clamp(Math.round(width * .18), 78, 118));
  const columns = Math.max(1, Math.floor((width - 2 * edgeInset) / (fieldWidth + 10)));
  const rowsPerSide = Math.ceil(Math.ceil(count / 2) / columns);
  const fieldAreaHeight = Math.ceil(Math.max(minHeight * .79, 278, 258 + 124 * Math.max(0, rowsPerSide - 1)));
  const core = {width: Math.min(172, width * .50), height: 100};
  const {center, fields} = orbitalFields(count, width, fieldAreaHeight, fieldWidth, core) ||
    staggeredFields(count, width, fieldAreaHeight, columns, fieldWidth);
  const saved = positions.get(table.id);
  for (let i = 0; i < fields.length; i++) {
    const pin = saved?.get(table.fields[i].id);
    if (pin) fields[i] = boundedPoint(pin.x * width, pin.y * fieldAreaHeight, fieldWidth, width, fieldAreaHeight);
  }

  const labelWidth = Math.min(width - 2 * edgeInset, clamp(Math.round(width * .19), 86, 134));
  const tableColumns = Math.max(1, Math.floor((width - 2 * edgeInset) / (labelWidth + 12)));
  const relationTop = fieldAreaHeight;
  const points = neighbours.map((_, i) => {
    const row = Math.floor(i / tableColumns);
    const inRow = Math.min(tableColumns, neighbours.length - row * tableColumns);
    const pitch = Math.min(labelWidth * 1.68, (width - 2 * edgeInset) / inRow);
    return {x: Math.round(center.x + ((i % tableColumns) - (inRow - 1) / 2) * pitch),
      y: relationTop + 42 + 66 * row};
  });
  const height = Math.max(minHeight, (points.at(-1)?.y ?? fieldAreaHeight) + 42);
  return {width, height, fieldAreaHeight, relationTop, center, core, labelWidth, points, fields};
}

export function bindFieldDrag(atlas, signal, positions, onMove, onCommit) {
  let table, layout;
  const cancel = bindAtlasDrag(atlas, '[data-star-field]', signal, node => {
    if (!layout) return;
    const index = Number(node.dataset.starField);
    const start = layout.fields[index];
    if (!start) return;
    // Grab a field in mid-flight from its displayed position.
    const flights = node.getAnimations().filter(a => a.playState === 'running'
      && a.effect?.getKeyframes().some(frame => 'translate' in frame));
    for (const flight of flights) flight.pause();
    const offset = flights.length ? getComputedStyle(node).translate.split(' ').map(parseFloat) : [];
    return {
      start() { for (const flight of flights) flight.cancel(); },
      move(dx, dy) {
        const point = boundedPoint(start.x + (offset[0] || 0) + dx, start.y + (offset[1] || 0) + dy,
          start.width, layout.width, layout.fieldAreaHeight);
        layout.fields[index] = point;
        onMove(index, point);
      },
      finish(moved, commit, repaint) {
        if (!moved) {
          for (const flight of flights) if (flight.playState === 'paused') flight.play();
        } else if (commit) {
          if (!positions.has(table.id)) positions.set(table.id, new Map());
          const {x, y} = layout.fields[index];
          positions.get(table.id).set(table.fields[index].id, {x: x / layout.width, y: y / layout.fieldAreaHeight});
          onCommit?.();
        } else {
          layout.fields[index] = start;
          if (repaint) onMove(index, start);
        }
      }
    };
  });
  return {setLayout(nextTable, nextLayout) {
    cancel(false);
    table = nextTable;
    layout = nextLayout;
  }};
}
