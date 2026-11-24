export function seededRandom(seed) {
  let value = [...String(seed)].reduce((hash, char) => (Math.imul(hash, 31) + char.codePointAt(0)) >>> 0, 2789);
  return () => {
    value = (Math.imul(value, 1664525) + 1013904223) >>> 0;
    return value / 4294967296;
  };
}

const planetHeight = 49;

function scatter(count, width, height, labelWidth, core, seed, focus) {
  const random = seededRandom(seed);
  const positions = [];
  const cx = width / 2, cy = height / 2;
  const rx = cx - labelWidth / 2 - 7, ry = cy - 31;
  const available = point => (Math.abs(point.x - cx) > (core.width + labelWidth) / 2 + 7
    || Math.abs(point.y - cy) > (core.height + planetHeight) / 2 + 5)
    && !positions.some(other => Math.abs(point.x - other.x) < labelWidth + 7
      && Math.abs(point.y - other.y) < planetHeight + 6);

  for (let index = 0; index < count; index++) {
    let best = null, score = -Infinity;
    for (let sample = 0; sample < 170; sample++) {
      const angle = random() * Math.PI * 2;
      const distance = Math.sqrt((focus ? .31 : .08) + random() * (focus ? .69 : .92));
      const point = {x: Math.round(cx + rx * distance * Math.cos(angle)),
        y: Math.round(cy + ry * distance * Math.sin(angle))};
      if (!available(point)) continue;
      const separation = positions.reduce((closest, other) =>
        Math.min(closest, Math.hypot((point.x - other.x) * .75, point.y - other.y)), 130);
      const rating = Math.min(separation, 100) + random() * 18;
      if (rating > score) {
        best = point;
        score = rating;
      }
    }
    if (!best) return null;
    positions.push(best);
  }
  return positions;
}

function arrange(count, width, minHeight, core, seed, focus = false) {
  const labelWidth = Math.min(110, Math.max(72, Math.floor(width * .23)));
  const lanes = Math.max(1, Math.floor((width - 24) / (labelWidth + 12)));
  const base = Math.max(minHeight, 142 + Math.ceil(count / lanes) * 51);
  for (let attempt = 0; attempt < 12; attempt++) {
    const height = base + attempt * 35;
    const points = scatter(count, width, height, labelWidth, core, seed + attempt, focus);
    if (points) return {width, height, labelWidth, points, center: {x: width / 2, y: height / 2}};
  }

  // Large sources expand vertically and retain predictable spacing.
  const rows = Math.ceil(count / lanes);
  const step = planetHeight + 13;
  const center = {x: width / 2, y: (rows * step + core.height + 98) / 2};
  const height = Math.max(minHeight, center.y * 2);
  center.y = height / 2;
  const spread = (width - 18) / lanes;
  const jitter = seededRandom(seed);
  const points = Array.from({length: count}, (_, index) => {
    const row = Math.floor(index / lanes), col = index % lanes;
    const y = row - (rows - 1) / 2;
    return {x: 9 + (col + .5) * spread + (jitter() - .5) * 8,
      y: center.y + y * step + (y < 0 ? -1 : 1) * (core.height / 2 + 35)};
  });
  return {width, height, labelWidth, points, center};
}

export function overviewLayout(groups, width, minHeight) {
  const useColumns = width >= 750 && groups.length > 1;
  const split = Math.round(width * .55);
  const columns = useColumns
    ? [{x: 0, width: split, groups: [groups[0], ...groups.slice(3).filter((_, i) => i % 2 === 0)]},
      {x: split, width: width - split, groups: groups.slice(1, 3).concat(groups.slice(3).filter((_, i) => i % 2 === 1))}]
    : [{x: 0, width, groups}];
  const sizes = columns.map(column => {
    const layouts = column.groups.map(group => {
      const core = {width: Math.min(column.width * .77, Math.max(116, [...group.name].reduce((sum, c) => sum + (c.codePointAt(0) > 127 ? 25 : 14), 27))), height: 52};
      return {group, core, ...arrange(group.tables.length, column.width,
        groups.length === 1 ? Math.min(minHeight, 590) : 204, core, group.id)};
    });
    return {...column, layouts, height: layouts.reduce((sum, layout) => sum + layout.height, 0)};
  });
  const height = Math.max(minHeight, ...sizes.map(column => column.height));
  return {height, groups: sizes.flatMap(column => {
    let y = (height - column.height) / 2;
    return column.layouts.map(layout => {
      const placed = {...layout, x: column.x, y};
      y += layout.height;
      return placed;
    });
  })};
}

export function focusLayout(count, width, minHeight) {
  const core = {width: Math.min(174, width * .52), height: 103};
  return {core, ...arrange(count, width, minHeight, core, 'focused-planets', true)};
}
