export function seededRandom(seed) {
  let value = [...String(seed)].reduce((hash, char) => (Math.imul(hash, 31) + char.codePointAt(0)) >>> 0, 2789);
  return () => {
    value = (Math.imul(value, 1664525) + 1013904223) >>> 0;
    return value / 4294967296;
  };
}

const starHeight = 49;
const turn = 2 * Math.PI;

// In disk coordinates all stars rotate together. For any pair, their
// separation vector traces a circle there, which projects to an ellipse on
// screen. Inverting its label rectangle gives the clearance for a whole turn.
function revolvingStars(count, width, height, labelWidth, core, seed, tilt) {
  const rx = (width - labelWidth) / 2 - 12;
  const maxY = height / 2 - 53;
  const shear = Math.max(-maxY * .45, Math.min(maxY * .45, rx * Math.sin(tilt * Math.PI / 180) * .64));
  const ry = Math.sqrt(maxY * maxY - shear * shear);
  const clearance = (x, y) => Math.hypot(x / rx, (y + Math.abs(shear) * x / rx) / ry);
  const innerRadius = clearance((core.width + labelWidth) / 2 + 5, 70);
  const gap = clearance(labelWidth + 8, starHeight + 5);
  if (innerRadius >= 1 || (count > 1 && 2 * Math.sin(Math.PI / count) < gap)) return null;

  const random = seededRandom(seed);
  const initialAngle = random() * turn;
  const direction = random() < .5 ? -1 : 1;
  const duration = Math.round(146000 + random() * 32000);
  for (let attempt = 0; attempt < 16; attempt++) {
    const spread = (16 - attempt) / 16;
    const tracks = Array.from({length: count}, (_, i) => {
      const angle = initialAngle + turn / count * (i + (random() - .5) * .65 * spread);
      const radius = 1 - (1 - innerRadius) * random() * spread;
      return {angle, radius, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius};
    });
    if (tracks.some((a, i) => tracks.slice(0, i).some(b => Math.hypot(a.x - b.x, a.y - b.y) < gap))) continue;
    return tracks.map(track => {
      const x = rx * track.x, y = shear * track.x + ry * track.y;
      const keyframes = Array.from({length: 65}, (_, i) => {
        const angle = track.angle + direction * turn * i / 64;
        const u = Math.cos(angle) * track.radius, v = Math.sin(angle) * track.radius;
        const dx = (rx * u - x).toFixed(2), dy = (shear * u + ry * v - y).toFixed(2);
        return {transform: `translate(${dx}px, ${dy}px)`};
      });
      return {x: width / 2 + x, y: height / 2 + 14 + y, orbit: {duration, keyframes}};
    });
  }
  return null;
}

function scatter(count, width, height, labelWidth, core, seed) {
  const random = seededRandom(seed);
  const positions = [];
  const cx = width / 2, cy = height / 2;
  const rx = cx - labelWidth / 2 - 7, ry = cy - 31;
  const available = point => (Math.abs(point.x - cx) > (core.width + labelWidth) / 2 + 7
    || Math.abs(point.y - cy) > (core.height + starHeight) / 2 + 5)
    && !positions.some(other => Math.abs(point.x - other.x) < labelWidth + 7
      && Math.abs(point.y - other.y) < starHeight + 6);

  for (let index = 0; index < count; index++) {
    let best = null, score = -Infinity;
    for (let sample = 0; sample < 170; sample++) {
      const angle = random() * Math.PI * 2;
      const distance = Math.sqrt(.08 + random() * .92);
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

function arrange(count, width, minHeight, core, seed, tilt) {
  const labelWidth = Math.min(110, Math.max(72, Math.floor(width * .23)));
  const lanes = Math.max(1, Math.floor((width - 24) / (labelWidth + 12)));
  const base = Math.max(minHeight, 142 + Math.ceil(count / lanes) * 51);
  // Give sparse groups room for orbits without making dense catalogs scroll
  // simply to accommodate motion.
  if (count > 0 && count <= 12 && width >= 460) {
    for (const height of [base, base + 38]) {
      const points = revolvingStars(count, width, height, labelWidth, core, seed, tilt);
      if (points) return {width, height, labelWidth, points, center: {x: width / 2, y: height / 2}};
    }
  }
  for (let attempt = 0; count <= 48 && attempt < 12; attempt++) {
    const height = base + attempt * 35;
    const points = scatter(count, width, height, labelWidth, core, seed + attempt);
    if (points) return {width, height, labelWidth, points, center: {x: width / 2, y: height / 2}};
  }

  // Large sources expand vertically and retain predictable spacing.
  const rows = Math.ceil(count / lanes);
  const step = starHeight + 13;
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
  const capacity = width < 750 ? 1 : Math.max(2, Math.floor(width / 460));
  const count = Math.max(1, Math.min(groups.length, capacity));
  const columns = Array.from({length: count}, (_, i) => {
    const x = Math.round(width * i / count);
    return {x, width: Math.round(width * (i + 1) / count) - x, height: 0, layouts: []};
  });
  for (const [index, group] of groups.entries()) {
    // Place new sources in the shorter column, including very uneven catalogs.
    const column = columns.reduce((a, b) => a.height <= b.height ? a : b);
    const nameWidth = [...group.name].reduce((sum, char) => sum + (char.codePointAt(0) > 127 ? 25 : 14), 27);
    const core = {width: Math.min(column.width * .77, Math.max(128, nameWidth)), height: 118};
    const tilt = index % 2 ? 16 : -21;
    const layout = arrange(group.tables.length, column.width, Math.max(275, Math.min(minHeight, column.width * .77)),
      core, group.id, tilt);
    column.layouts.push({...layout, group, core, tilt, x: column.x, y: column.height});
    column.height += layout.height;
  }
  const height = Math.max(minHeight, ...columns.map(column => column.height));
  return {width, height, groups: columns.flatMap(column => {
    const alignment = groups.length <= 2 ? (columns.length === 2 ? (column.x ? .76 : .24) : .5) : 0;
    const offset = (height - column.height) * alignment;
    return column.layouts.map(layout => ({...layout, y: layout.y + offset}));
  })};
}
