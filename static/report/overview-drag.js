import {bindAtlasDrag} from './atlas-drag.js?v=0.1.57';

const clamp = (n, min, max) => Math.max(min, Math.min(max, n));

function groupOrigin(group, layout, x, y) {
  const half = group.core.width / 2 + 7;
  return {x: clamp(x + group.center.x, half, layout.width - half) - group.center.x,
    y: clamp(y + group.center.y, 22, layout.height - 61) - group.center.y};
}

// Table pins are local to their source. A cluster can be moved, filtered out,
// or laid out at a different width without detaching its pinned table stars.
function tablePoint(group, layout, x, y) {
  const margin = group.labelWidth / 2 + 4;
  return {x: clamp(x, Math.max(margin, margin - group.x), Math.min(group.width - margin, layout.width - group.x - margin)),
    y: clamp(y, Math.max(16, 16 - group.y), Math.min(group.height - 34, layout.height - group.y - 34))};
}

export function mountOverviewDrag(atlas, signal, stars, sky, saved, redraw, changed) {
  const {groups: offsets, tables: pins} = saved;
  const byGroup = new Map(), byTable = new Map();
  let layout;
  function prepare(next) {
    layout = next;
    byGroup.clear();
    byTable.clear();
    for (const group of next.groups) {
      const id = group.group.id;
      byGroup.set(id, group);
      const saved = offsets.get(id);
      group.baseX = group.x;
      group.baseY = group.y;
      if (saved) Object.assign(group, groupOrigin(group, next, group.x + saved.x * group.width, group.y + saved.y * group.height));
      group.drawX = group.x;
      group.drawY = group.y;
      group.group.tables.forEach((table, index) => {
        byTable.set(table.index, {table, group, index});
        const pin = pins.get(table.id);
        if (pin) group.points[index] = tablePoint(group, next, pin.x * group.width, pin.y * group.height);
      });
    }
  }
  function grabGroup(handle, group) {
    const title = handle.closest('.kg-galaxy-core');
    const id = group.group.id;
    const startX = group.x, startY = group.y;
    return {
      move(dx, dy) {
        Object.assign(group, groupOrigin(group, layout, startX + dx, startY + dy));
        const x = group.x + group.center.x, y = group.y + group.center.y;
        title.style.left = x + 'px';
        title.style.top = y + 'px';
        stars.moveCluster(id, group.x - group.drawX, group.y - group.drawY);
        sky.moveGalaxy(id, x, y);
      },
      finish(moved, commit, repaint) {
        stars.setGrabbing(false);
        if (!moved) return;
        if (commit) {
          offsets.set(id, {x: (group.x - group.baseX) / group.width, y: (group.y - group.baseY) / group.height});
          changed();
        } else if (repaint) redraw();
      }
    };
  }
  function grabTable(button, {table, group, index}) {
    const bounds = atlas.getBoundingClientRect(), rect = button.getBoundingClientRect();
    const x = rect.left + rect.width / 2 - bounds.left - group.x, y = rect.top + 14 - bounds.top - group.y;
    let point;
    return {
      start() { stars.stopFlight(table.index); },
      move(dx, dy) {
        point = tablePoint(group, layout, x + dx, y + dy);
        button.style.left = (group.drawX + point.x) + 'px';
        button.style.top = (group.drawY + point.y) + 'px';
      },
      finish(moved, commit, repaint) {
        stars.setGrabbing(false);
        if (!moved) return;
        if (commit) {
          group.points[index] = point;
          pins.set(table.id, {x: point.x / group.width, y: point.y / group.height});
          changed();
        } else if (repaint) redraw();
      }
    };
  }
  const cancel = bindAtlasDrag(atlas, '[data-drag-galaxy], [data-table-index]', signal, node => {
    if (!layout) return;
    const group = byGroup.get(node.dataset.dragGalaxy);
    const entry = byTable.get(Number(node.dataset.tableIndex));
    if (!group && !entry) return;
    stars.setGrabbing(true);
    return group ? grabGroup(node, group) : grabTable(node, entry);
  });
  return {
    prepare,
    clearScene() { cancel(false); layout = null; },
    hasPins() { return offsets.size > 0 || pins.size > 0; },
    reset() { offsets.clear(); pins.clear(); }
  };
}
