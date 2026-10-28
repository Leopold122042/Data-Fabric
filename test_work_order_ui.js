import test from 'node:test';
import assert from 'node:assert/strict';
import {filterOrders,attention,recordUrl,orderedTimeline,messagePayload} from './static/management/work-order-model.js';

const items=[{id:'REQ-1',title:'科研目录覆盖核验',purpose:'研究可行性',requester_name:'临床研究中心',status:'delivered',counts:{exceptions:1},service_ids:['SVC-A']},{id:'REQ-2',title:'随访范围确认',requester_name:'医院数据中心',status:'draft',counts:{exceptions:0},service_ids:['SVC-B']}];
test('work orders search scope and attention include completed work with unresolved exceptions',()=>{
 assert.deepEqual(filterOrders(items,{query:'研究中心'}),[items[0]]);
 assert.deepEqual(filterOrders(items,{filter:'attention'}),[items[0]]);
 assert.deepEqual(filterOrders(items,{service:'SVC-B'}),[items[1]]);
 assert.deepEqual(filterOrders(items,{filter:'active'}),[items[1]]);
 assert.deepEqual(filterOrders(items,{query:'不存在'}),[]);
 assert.equal(attention({status:'attention'}),true);
 assert.deepEqual(filterOrders([{status:'attention'}],{filter:'active'}),[]);
});
test('record links encode identifiers and select only supported destination kinds',()=>{
 assert.equal(recordUrl('job','JOB /1'),'/static/execution/index.html?tab=job&selected=JOB%20%2F1');
 assert.equal(recordUrl('demand','REQ#A'),'/static/management/intake.html?selected=REQ%23A');
 assert.equal(recordUrl('service','SVC1'),'/static/management/services.html?selected=SVC1');
 assert.equal(recordUrl('unknown','x'),null);
});
test('timeline preserves historical ordering without mutating data',()=>{
 const rows=[{id:'b',time:'2026-10-27T12:00Z'},{id:'a',time:'2026-10-27T10:00Z'}];
 assert.deepEqual(orderedTimeline(rows).map(r=>r.id),['a','b']);assert.equal(rows[0].id,'b');
});
test('messages require explicit visible scope and bounded content',()=>{
 const scopes=[{id:'SVC-A'}];
 assert.deepEqual(messagePayload(' 已补全口径 ','SVC-A',scopes),{body:'已补全口径',service_id:'SVC-A'});
 assert.throws(()=>messagePayload('说明','',scopes));
 assert.throws(()=>messagePayload('说明','SVC-B',scopes));
 assert.throws(()=>messagePayload(' ','SVC-A',scopes));
 assert.throws(()=>messagePayload('字'.repeat(2001),'SVC-A',scopes));
 assert.deepEqual(messagePayload('内部确认','',[{id:''}]),{body:'内部确认',service_id:''});
});
