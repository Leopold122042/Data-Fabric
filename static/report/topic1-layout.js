const BASE_MARGIN = 24;
const SOURCE_GAP = 48;
const BORDER_CLEARANCE = 12;
const TABLE_HEIGHT = 47;
const TABLE_ROW = 62;
const TABLE_GAP = 34;

export function placeSources(blocks, routes, width) {
  const laneCount = Math.min(blocks.length || 1, width >= 900 ? 3 : 2);
  const gap = SOURCE_GAP;
  const laneWidth = (width - BASE_MARGIN * 2 - gap * (laneCount - 1)) / laneCount;
  const laneOffsets = [24, 51, 36];
  const laneBottoms = laneOffsets.slice(0, laneCount);
  const connections = new Map(blocks.map(block => [block, new Map()]));
  for (const route of routes) {
    const weight = 1 + Math.sqrt(route.pairs.length) + (route.registered ? .65 : 0);
    connections.get(route.first).set(route.second, weight);
    connections.get(route.second).set(route.first, weight);
  }
  const rank = block => [...connections.get(block).values()].reduce((total, weight) => total + weight, 0);
  const ordered = [...blocks].sort((a, b) => rank(b) - rank(a));
  const placed = new Set();
  const tables = new Map();

  for (const block of ordered) {
    const tableCount = block.tables.length;
    const panelWidth = Math.min(laneWidth, laneCount === 1 ? 462 : tableCount === 1 ? laneWidth * .79 : laneWidth);
    const columns = tableCount === 1 ? 1 : Math.min(tableCount, panelWidth >= 405 ? 3 : 2);
    const rows = Math.ceil(tableCount / columns);
    block.w = Math.round(panelWidth);
    block.h = 70 + rows * TABLE_ROW;
    const laneCost = (lane, x, y) => {
      const neighbours = connections.get(block);
      let cost = laneBottoms[lane] + Math.abs(lane - (laneCount - 1) / 2) * 16;
      for (const [other, weight] of neighbours) {
        if (!placed.has(other)) continue;
        const xGap = Math.max(x - other.x - other.w, other.x - x - block.w, 0);
        const yGap = Math.max(y - other.y - other.h, other.y - y - block.h, 0);
        cost += Math.hypot(xGap, yGap) * Math.min(weight, 5) * .10;
      }
      return cost;
    };
    const candidates = laneBottoms.map((y, lane) => {
      const free = laneWidth - block.w;
      const offset = laneCount === 1 ? free / 2 : free * (lane === 0 ? .80 : lane === laneCount - 1 ? .20 : .50);
      const x = Math.round(BASE_MARGIN + lane * (laneWidth + gap) + offset);
      return {lane, x, y, cost: laneCost(lane, x, y)};
    });
    const position = candidates.reduce((best, item) => item.cost < best.cost ? item : best);
    Object.assign(block, position);
    laneBottoms[position.lane] += block.h + gap;
    placed.add(block);

    const nodeWidth = Math.floor((block.w - 28 - TABLE_GAP * (columns - 1)) / columns);
    block.tables.forEach((table, index) => {
      const column = index % columns;
      tables.set(table.index, {block, column, columns, x: block.x + 14 + column * (nodeWidth + TABLE_GAP),
        y: block.y + 65 + Math.floor(index / columns) * TABLE_ROW, w: nodeWidth, h: TABLE_HEIGHT});
    });
  }
  const height = Math.max(140, ...blocks.map(block => block.y + block.h + BASE_MARGIN));
  return {height, tables};
}

function portSide(block, target) {
  const dx = target.x + target.w / 2 - block.x - block.w / 2;
  const dy = target.y + target.h / 2 - block.y - block.h / 2;
  if (block.lane === target.lane) return dy > 0 ? 'bottom' : 'top';
  if (Math.abs(dy) > Math.abs(dx) * 1.9) return dy > 0 ? 'bottom' : 'top';
  return dx > 0 ? 'right' : 'left';
}

function positionPorts(block) {
  for (const side of ['left', 'right', 'top', 'bottom']) {
    const vertical = side === 'left' || side === 'right';
    const ports = block.ports.filter(port => port.side === side);
    ports.sort((a, b) => vertical ? a.target.y - b.target.y : a.target.x - b.target.x);
    ports.forEach((port, index) => {
      const fraction = (index + 1) / (ports.length + 1);
      const x = vertical ? block.x + (side === 'right' ? block.w : 0)
        : block.x + Math.round(20 + fraction * (block.w - 40));
      const y = vertical ? block.y + Math.round(27 + fraction * (block.h - 51))
        : block.y + (side === 'bottom' ? block.h : 0);
      const dx = side === 'left' ? -BORDER_CLEARANCE : side === 'right' ? BORDER_CLEARANCE : 0;
      const dy = side === 'top' ? -BORDER_CLEARANCE : side === 'bottom' ? BORDER_CLEARANCE : 0;
      port.point = {x, y};
      port.outside = {x: x + dx, y: y + dy};
      port.axis = vertical ? 0 : 1;
    });
  }
}

function segmentIsOpen(a, b, obstacles) {
  return !obstacles.some(area => a.x === b.x
    ? a.x > area.left && a.x < area.right && Math.max(a.y, b.y) > area.top && Math.min(a.y, b.y) < area.bottom
    : a.y > area.top && a.y < area.bottom && Math.max(a.x, b.x) > area.left && Math.min(a.x, b.x) < area.right);
}

function findRoute(grid, start, end, useCount) {
  const {xGrid, yGrid, points, edges} = grid;
  const pointId = point => yGrid.indexOf(point.y) * xGrid.length + xGrid.indexOf(point.x);
  const goal = pointId(end.outside);
  const first = 2 * pointId(start.outside) + start.axis;
  const score = new Map([[first, 0]]);
  const parent = new Map();
  const waiting = [first];
  const estimate = id => Math.abs(points[id >> 1].x - end.outside.x) + Math.abs(points[id >> 1].y - end.outside.y);

  while (waiting.length) {
    let best = 0;
    for (let i = 1; i < waiting.length; i++) {
      if (score.get(waiting[i]) + estimate(waiting[i]) < score.get(waiting[best]) + estimate(waiting[best])) best = i;
    }
    const current = waiting.splice(best, 1)[0];
    const point = current >> 1, previousAxis = current % 2;
    if (point === goal) {
      const track = [end.point];
      let item = current;
      while (item !== undefined) {
        track.push(points[item >> 1]);
        const previous = parent.get(item);
        if (previous) useCount.set(previous.edge, (useCount.get(previous.edge) || 0) + 1);
        item = previous?.id;
      }
      track.push(start.point);
      return track.reverse();
    }
    for (const {to, axis, distance, key} of edges[point]) {
      const next = to * 2 + axis;
      const turnCost = axis === previousAxis ? 0 : 18;
      const occupied = (useCount.get(key) || 0) * (12 + distance * .6);
      const cost = score.get(current) + distance + turnCost + occupied;
      if (cost >= (score.get(next) ?? Infinity)) continue;
      score.set(next, cost);
      parent.set(next, {id: current, edge: key});
      if (!waiting.includes(next)) waiting.push(next);
    }
  }
  return null;
}

export function routeSources(blocks, routes, width, height) {
  if (!routes.length) return;
  const obstacles = blocks.map(block => ({
    left: block.x - BORDER_CLEARANCE, top: block.y - BORDER_CLEARANCE,
    right: block.x + block.w + BORDER_CLEARANCE, bottom: block.y + block.h + BORDER_CLEARANCE
  }));
  for (const {first, second, start, end} of routes) {
    start.side = portSide(first, second);
    end.side = portSide(second, first);
    first.ports.push(start);
    second.ports.push(end);
  }
  blocks.forEach(positionPorts);
  const unique = values => [...new Set(values)].sort((a, b) => a - b);
  const ports = routes.flatMap(route => [route.start.outside, route.end.outside]);
  const xGrid = unique([8, width - 8, ...obstacles.flatMap(o => [o.left, o.right]), ...ports.map(p => p.x)]);
  const yGrid = unique([8, height - 8, ...obstacles.flatMap(o => [o.top, o.bottom]), ...ports.map(p => p.y)]);
  const points = yGrid.flatMap(y => xGrid.map(x => ({x, y})));
  const stride = xGrid.length;
  const edges = points.map((point, i) => {
    const neighbours = [i - stride, i + stride];
    if (i % stride !== 0) neighbours.push(i - 1);
    if (i % stride !== stride - 1) neighbours.push(i + 1);
    return neighbours.filter(j => points[j] && segmentIsOpen(point, points[j], obstacles)).map(j => ({
      to: j, axis: point.x === points[j].x ? 1 : 0,
      distance: Math.abs(point.x - points[j].x) + Math.abs(point.y - points[j].y),
      key: Math.min(i, j) + ':' + Math.max(i, j)
    }));
  });
  const used = new Map();
  for (const route of [...routes].sort((a, b) => Number(b.registered) - Number(a.registered) || b.pairs.length - a.pairs.length)) {
    route.track = findRoute({xGrid, yGrid, points, edges}, route.start, route.end, used);
  }
}

export function softenPath(points) {
  const track = [];
  for (const point of points) {
    const last = track.at(-1), before = track.at(-2);
    if (last && point.x === last.x && point.y === last.y) continue;
    if (before && ((before.x === last.x && last.x === point.x) || (before.y === last.y && last.y === point.y))) {
      track.pop();
    }
    track.push(point);
  }
  const start = track[0], end = track.at(-1);
  const distance = (a, b) => Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
  const approach = (a, b, scale) => (a.x + (b.x - a.x) * scale) + ' ' + (a.y + (b.y - a.y) * scale);
  let path = 'M ' + start.x + ' ' + start.y;
  for (let i = 1; i < track.length - 1; i++) {
    const a = track[i - 1], b = track[i], c = track[i + 1];
    const before = distance(a, b), after = distance(b, c);
    const radius = Math.min(12, before / 2, after / 2);
    path += ' L ' + approach(b, a, radius / before)
      + ' Q ' + b.x + ' ' + b.y + ' ' + approach(b, c, radius / after);
  }
  return path + ' L ' + end.x + ' ' + end.y;
}
