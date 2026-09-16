const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'static/exploration.js'),'utf8');

function model() {
  const context = vm.createContext({structuredClone});
  vm.runInContext(source.slice(source.indexOf('let trendAnchor'),source.indexOf('function drawRowsChart')),context);
  return context.trendHistory;
}

test('simulated history is monotonic, nonnegative and joins the real baseline',()=>{
  const history = model();
  const first = {t:5000,by_source:{his:6000,lis:15000,pacs:1500,emr:2000,iot:320000}};
  const result = history([first]);
  assert.equal(result.points.length,97);
  assert.equal(result.points[0].t,5000-86400);
  for (const sid of Object.keys(first.by_source)) {
    const values = result.points.map(p=>p.by_source[sid]);
    assert.equal(values.at(-1),first.by_source[sid]);
    assert.ok(values.every((v,i)=>v>=0 && (!i || v>=values[i-1])));
  }
});

test('polling retains the synthetic baseline and actual samples unchanged',()=>{
  const history = model();
  const first = {t:5000,by_source:{his:100}};
  const before = history([first]);
  const live = {t:5004,by_source:{his:102}};
  const after = history([live]);
  assert.equal(after.boundary,5000);
  assert.deepEqual(after.points.slice(0,97),before.points);
  assert.equal(after.points.at(-1),live);
  assert.equal(first.by_source.his,100);
});

test('empty and zero series are supported',()=>{
  const history = model();
  assert.equal(history([]),null);
  assert.ok(history([{t:1,by_source:{}}]).points.every(p=>Object.values(p.by_source).every(v=>v===0)));
});

test('independent batch growth has multiple crossings on a shared relative scale',()=>{
  const result=model()([{t:90000,by_source:{his:10000,lis:10000,pacs:10000,emr:10000,iot:10000}}]);
  let crossings=0,previous=0;
  for(const p of result.points){
    const sign=Math.sign(p.by_source.his-p.by_source.lis);
    if(sign && previous && sign!==previous)crossings++;
    if(sign)previous=sign;
  }
  assert.ok(crossings>=3, 'Expected distinct, crossing growth trajectories');
});

test('startup anchor survives history eviction and changes on a new server session',()=>{
  const history=model(), origin={t:5000,epoch:1700000000,by_source:{his:100}};
  const result=history([{t:8000,by_source:{his:120}}],origin);
  assert.equal(result.boundary,5000);
  assert.equal(result.points[0].t,5000-86400);
  const restarted=history([{t:9001,by_source:{his:121}}],{...origin,t:9000});
  assert.equal(restarted.boundary,9000);
});

test('logarithmic ticks cover actual counts and handle zero without logarithm errors',()=>{
  const context=vm.createContext({structuredClone});
  vm.runInContext(source.slice(source.indexOf('let trendAnchor'),source.indexOf('function drawRowsChart')),context);
  const domain=context.trendLogDomain([{by_source:{his:6500,emr:2000,iot:338500,empty:0}}]);
  assert.equal(domain.lo,3);assert.equal(domain.hi,6);
  assert.equal(context.trendLogDomain([{by_source:{empty:0}}]).hi,1);
});

test('wall-clock labels backtrack 24 hours across midnight from the server timestamp',()=>{
  const context=vm.createContext({structuredClone});
  vm.runInContext(source.slice(source.indexOf('let trendAnchor'),source.indexOf('function drawRowsChart')),context);
  const origin={t:5000,epoch:new Date(2026,8,16,2,15,0).getTime()/1000};
  assert.equal(context.trendTimeLabel(5000-86400,origin,true),'09/15 02:15');
  assert.equal(context.trendTimeLabel(5000,origin,true),'09/16 02:15');
  assert.equal(context.trendTimeLabel(5000+3600,origin),'03:15');
});
