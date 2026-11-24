import test from 'node:test';
import assert from 'node:assert/strict';
import {overviewLayout} from './static/report/topic1-layout.js';
import {fieldLayout} from './static/report/field-constellation.js';

// Bounds include the text below the light at (x, y).
function bounds({x, y, width}, top = -14, bottom = 35) {
  return {left: x - width / 2, right: x + width / 2, top: y + top, bottom: y + bottom};
}

function assertClear(boxes, width, height) {
  boxes.forEach((box, i) => {
    assert.ok(box.left >= 0 && box.right <= width && box.top >= 0 && box.bottom <= height, 'label outside atlas');
    for (const other of boxes.slice(0, i)) {
      assert.ok(box.right <= other.left || box.left >= other.right ||
        box.bottom <= other.top || box.top >= other.bottom, 'labels overlap');
    }
  });
}

test('both overview galaxies keep table buttons and titles clear throughout an orbit', () => {
  const groups = [
    {id: 'group:medical', name: '合成医疗演示', tables: Array(5)},
    {id: 'source:openalex', name: 'OpenAlex 文献', tables: Array(8)}
  ];
  const atlas = overviewLayout(groups, 1280, 520);
  assert.equal(atlas.groups.length, 2);
  for (const galaxy of atlas.groups) {
    const {points, labelWidth, center, core} = galaxy;
    const tracks = points.map(({x, y, orbit}) => {
      assert.ok(orbit && orbit.duration === points[0].orbit.duration, 'stars must share an orbital clock');
      return orbit.keyframes.map(({transform}) => {
        const [dx, dy] = transform.match(/[-\d.]+/g).map(Number);
        return {x: x + dx, y: y + dy};
      });
    });
    const steps = tracks[0].length - 1;
    for (const track of tracks) {
      assert.deepEqual(track[0], track.at(-1));
      assert.ok(Math.hypot(track[0].x - track[steps >> 1].x, track[0].y - track[steps >> 1].y) > 100);
    }
    for (let i = 0; i < steps; i++) {
      for (const t of [0, .25, .5, .75]) {
        const boxes = tracks.map(track => bounds({
          x: track[i].x * (1 - t) + track[i + 1].x * t,
          y: track[i].y * (1 - t) + track[i + 1].y * t,
          width: labelWidth
        }));
        boxes.push(bounds({...center, width: core.width}, -17, 66));
        assertClear(boxes, galaxy.width, galaxy.height);
      }
    }
  }
});

test('narrow focus view gives every field a visible star and separates related tables', () => {
  const table = {id: 'wide-table', fields: Array.from({length: 80}, (_, id) => ({id}))};
  const neighbours = Array.from({length: 7}, () => []);
  for (const width of [240, 360]) {
    const {fields, center, core, points, height, labelWidth, relationTop} = fieldLayout(table, neighbours, width, 480, new Map());
    assert.equal(fields.length, table.fields.length);
    assert.equal(points.length, neighbours.length);
    assertClear([...fields.map(p => bounds(p)), bounds({...center, width: core.width}, -32, 62)], width, relationTop);
    assert.ok(points.every(p => p.y - 14 >= relationTop));
    assertClear(points.map(p => bounds({...p, width: labelWidth})), width, height);
  }
});

test('an uneven catalog places every source and table in a non-overlapping group on wide and narrow screens', () => {
  const groups = [5, 8, 2, 180, 1, 3, 6].map((size, index) => ({
    id: 'source:' + index, name: '来源 ' + index, tables: Array(size)
  }));
  for (const width of [390, 1100, 1536]) {
    const atlas = overviewLayout(groups, width, 480);
    assert.equal(atlas.groups.length, groups.length);
    assert.equal(new Set(atlas.groups.map(g => g.x)).size, width === 390 ? 1 : width === 1100 ? 2 : 3);
    const boxes = atlas.groups.map(g => {
      assert.equal(g.points.length, g.group.tables.length);
      assertClear(g.points.map(p => bounds({...p, width: g.labelWidth})), g.width, g.height);
      return {left: g.x, top: g.y, right: g.x + g.width, bottom: g.y + g.height};
    });
    assertClear(boxes, width, atlas.height);
  }
});

test('a saved drag position follows the field ID and viewport size only in its own table', () => {
  const table = {id: 'labs', fields: [{id: 'name'}, {id: 'result'}]};
  const pins = new Map([['labs', new Map([['result', {x: .72, y: .39}]])]]);
  for (const [width, minHeight] of [[900, 700], [350, 430]]) {
    const reordered = {...table, fields: [...table.fields].reverse()};
    const layout = fieldLayout(reordered, [], width, minHeight, pins);
    assert.equal(layout.fields[0].x, .72 * width);
    assert.equal(layout.fields[0].y, .39 * layout.fieldAreaHeight);
    const other = {...reordered, id: 'studies'};
    assert.deepEqual(fieldLayout(other, [], width, minHeight, pins).fields,
      fieldLayout(other, [], width, minHeight, new Map()).fields);
  }
});
