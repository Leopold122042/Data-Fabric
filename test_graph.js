const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function graph(animate=false) {
  const events = {}, draws = [], visits = [], elements = {};
  let now=0, nextFrame=null;
  let current;
  const ctx = new Proxy({
    globalAlpha: 1,
    clearRect() { draws.length = 0; },
    arc(x) { current = {kind:'node', x}; },
    moveTo(x) { current = {kind:'edge', x}; },
    fill() { draws.push({...current, alpha:this.globalAlpha}); },
    stroke() { if (current.kind === 'edge') draws.push({...current, alpha:this.globalAlpha}); },
    measureText() { return {width:20}; }
  }, {get: (target, key) => target[key] ?? (()=>{})});
  const canvas = {
    clientWidth:800, clientHeight:600, style:{},
    getContext:()=>ctx, getBoundingClientRect:()=>({left:0, top:0}),
    addEventListener:(name, fn)=>events[name]=fn
  };
  elements['#gcv'] = canvas;
  const context = vm.createContext({
    performance:{now:()=>now}, matchMedia:()=>({matches:!animate}),
    requestAnimationFrame:fn=>{nextFrame=fn; return 1;},
    devicePixelRatio:1, CATALOG:{entity_types:{}}, esc:x=>x,
    document:{querySelector:id=>elements[id] ??= {style:{}}},
    jumpToPatient:id=>visits.push(id)
  });
  const source = fs.readFileSync(path.join(__dirname, 'static/exploration.js'), 'utf8');
  vm.runInContext(source.slice(0, source.indexOf('let trendAnchor')), context);
  vm.runInContext(`
    G.nodes = [
      {id:'patient:P1',type:'patient',label:'P1',x:100,y:100},
      {id:'visit:V1',type:'visit',label:'V1',x:200,y:100},
      {id:'patient:P2',type:'patient',label:'P2',x:300,y:100},
      {id:'visit:V2',type:'visit',label:'V2',x:400,y:100}
    ];
    G.byId = Object.fromEntries(G.nodes.map(n=>[n.id,n]));
    G.edges = [{s:'patient:P1',t:'visit:V1'},{s:'patient:P2',t:'visit:V2'}];
  `, context);
  const event = x=>({clientX:x,clientY:100,detail:1});
  const advance=ms=>{now+=ms; const frame=nextFrame; nextFrame=null; frame?.();};
  return {events, draws, visits, event, advance, pending:()=>nextFrame!==null};
}

test('hover fades unrelated nodes and edges to 25% opacity without hiding them', ()=>{
  const {events, draws, event} = graph();
  events.pointermove(event(100));
  assert.deepEqual(draws.filter(d=>d.kind==='node').map(d=>[d.x,d.alpha]), [[100,1],[200,1],[300,.25],[400,.25]]);
  assert.deepEqual(draws.filter(d=>d.kind==='edge').map(d=>d.alpha), [1,.25]);
  events.pointerleave();
  assert.ok(draws.every(d=>d.alpha===1));
});

test('focus smoothly fades and rapid switching continues from the current opacity', ()=>{
  const {events, draws, event, advance, pending} = graph(true);
  const alpha=x=>draws.find(d=>d.kind==='node' && d.x===x).alpha;
  events.click(event(100));
  assert.equal(alpha(300),1);
  advance(140);
  assert.equal(alpha(300),.625);
  events.pointermove(event(300));
  assert.equal(alpha(300),.625);
  advance(140);
  assert.equal(alpha(300),.8125);
  advance(140);
  assert.equal(alpha(300),1);
  assert.equal(alpha(100),.25);
  assert.equal(pending(),false);
  events.pointerleave();
  advance(280);
  assert.equal(alpha(100),1);
  assert.equal(alpha(300),.25);
  events.click(event(700));
  advance(140);
  assert.equal(alpha(300),.625);
  advance(140);
  assert.ok(draws.every(d=>d.alpha===1));
  assert.equal(pending(),false);
});

test('single click persists focus, muted nodes stay interactive, only double click navigates', ()=>{
  const {events, draws, visits, event} = graph();
  events.click(event(100));
  events.pointerleave();
  assert.equal(draws.find(d=>d.kind==='node' && d.x===300).alpha,.25);
  assert.deepEqual(visits,[]);
  events.pointermove(event(300));
  assert.equal(draws.find(d=>d.kind==='node' && d.x===100).alpha,.25);
  events.pointerleave();
  assert.equal(draws.find(d=>d.kind==='node' && d.x===100).alpha,1);
  events.dblclick(event(100));
  assert.deepEqual(visits,['P1']);
  events.dblclick(event(200));
  assert.deepEqual(visits,['P1']);
  events.click(event(700));
  assert.ok(draws.every(d=>d.alpha===1));
});
