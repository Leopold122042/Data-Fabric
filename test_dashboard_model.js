import test from 'node:test';
import assert from 'node:assert/strict';
import {distribution,format,orderURL,statusName,stagePresentation,authorizationSummary} from './static/dashboard-model.js';
test('distribution preserves totals when long tails are combined',()=>{
 const rows=distribution({a:10,b:9,c:8,d:7,e:6,f:5,g:4});
 assert.equal(rows.length,6);assert.equal(rows.reduce((n,r)=>n+r.value,0),49);
 assert.ok(rows.every(r=>r.width>=0&&r.width<=100));
});
test('missing statistics differ from actual zero',()=>{assert.equal(format(null),'—');assert.equal(format(undefined),'—');assert.equal(format(0),'0');});
test('a stored receipt never asserts current read authorization',()=>{assert.equal(statusName('available'),'已有凭证');});
test('work order links cannot inject extra URL parameters',()=>{assert.equal(new URL(orderURL('a&role=platform'),'http://local').searchParams.get('selected'),'a&role=platform');});
test('authorization chart uses saved decisions and has no success rate without observations',()=>{
 assert.deepEqual(authorizationSummary({total:91,rejected:4}),{total:91,rejected:4,passed:87,rate:87/91});
 assert.equal(authorizationSummary({total:0,rejected:0}).rate,null);
 assert.equal(authorizationSummary({total:4,rejected:4}).rate,0);
 assert.equal(authorizationSummary({total:4,rejected:0}).rate,1);
});
test('exception stage retains its warning and unknown states never imply completion',()=>{
 assert.deepEqual(stagePresentation('attention'),{state:'attention',label:'需关注',icon:'alert'});
 assert.equal(stagePresentation('done').icon,'check');
 assert.equal(stagePresentation('current').label,'当前阶段');
 assert.equal(stagePresentation('unexpected').state,'pending');
});
