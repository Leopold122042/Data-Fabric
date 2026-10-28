import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {permissionRoles,roleGuidance,permissionSections} from './static/shared/permission-guide.js';

const policy=JSON.parse(readFileSync(new URL('./static/shared/access-policy.json',import.meta.url),'utf8'));

test('platform guide covers every policy resource once, including subpages',()=>{
 const sections=permissionSections(policy),rows=sections.flatMap(section=>section.rows);
 assert.equal(new Set(rows.map(row=>row.key)).size,rows.length);
 assert.deepEqual(rows.map(row=>row.key).sort(),Object.keys(policy.resources).sort());
 assert.equal(rows.find(row=>row.key==='spec').label,'任务规格');
 assert.equal(rows.find(row=>row.key==='validation').label,'发布验证');
 assert.equal(rows.find(row=>row.key==='delivery').label,'结果与凭证');
 assert.ok(rows.find(row=>row.key==='object').label.includes('元数据维护'));
 assert.ok(rows.find(row=>row.key==='view').label.includes('逻辑视图'));
 assert.ok(rows.find(row=>row.key==='concept').label.includes('语义图谱'));
 assert.equal(roleGuidance.length,permissionRoles.length);
});

test('permission guide follows actual policy across all four roles',()=>{
 const names={write:'可查看、可操作',read:'可查看',none:'不可查看'};
 for(const row of permissionSections(policy).flatMap(section=>section.rows)){
  assert.deepEqual(row.levels,permissionRoles.map(role=>names[policy.resources[row.key][role]]||'不可查看'));
 }
 const rows=permissionSections(policy).flatMap(section=>section.rows);
 assert.deepEqual(rows.find(row=>row.key==='service').levels,['可查看、可操作','可查看、可操作','可查看','可查看']);
 assert.deepEqual(rows.find(row=>row.key==='delivery').levels,['可查看、可操作','不可查看','可查看、可操作','可查看']);
});

test('permission guide follows policy updates without granting unknown or omitted levels',()=>{
 const updated=structuredClone(policy);
 updated.resources.service.requester='none';
 updated.resources.future_resource={platform:'write',provider:'unrecognized'};
 const rows=permissionSections(updated).flatMap(section=>section.rows);
 assert.equal(rows.find(row=>row.key==='service').levels[2],'不可查看');
 assert.deepEqual(rows.find(row=>row.key==='future_resource').levels,['可查看、可操作','不可查看','不可查看','不可查看']);
 assert.deepEqual(permissionSections(null),[]);
});
