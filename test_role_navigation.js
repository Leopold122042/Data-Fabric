import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {roleDestination,userHome} from './static/shared/role-navigation.js';
import {switchRole} from './static/shared/access.js';
const policy=JSON.parse(readFileSync(new URL('./static/shared/access-policy.json',import.meta.url),'utf8'));
const access=role=>({principal:{role},actions:{},resources:Object.fromEntries(Object.entries(policy.resources).map(([key,value])=>[key,value[role]||'none']))});

test('all four roles retain the cockpit so it reloads their own overview',()=>{
 for(const role of ['platform','provider','requester','auditor'])assert.equal(roleDestination('http://localhost/static/portal/cockpit.html',access(role)),'/static/portal/cockpit.html');
});
test('switching to a non-manager exits admin pages, including selected query reviews',()=>{
 for(const role of ['provider','requester'])assert.equal(roleDestination('http://localhost/static/portal/index.html?page=admin&flow=private',access(role)),userHome);
 assert.equal(roleDestination('http://localhost/static/portal/index.html?page=admin',access('platform')),'/static/portal/index.html?page=admin');
});
test('inaccessible backend resources return home, permitted pages retain selection',()=>{
 assert.equal(roleDestination('http://localhost/static/platform/security.html',access('requester')),userHome);
 assert.equal(roleDestination('http://localhost/static/execution/index.html?tab=grant&selected=G1',access('platform')),'/static/execution/index.html?tab=grant&selected=G1');
});
test('role switch validates permissions before persisting and navigating; failures keep identity',async()=>{
 const originals={fetch:globalThis.fetch,location:globalThis.location,localStorage:globalThis.localStorage};
 const calls=[];
 globalThis.location={href:'http://localhost/static/portal/index.html?page=admin',assign:url=>calls.push(['navigate',url])};
 globalThis.localStorage={setItem:(key,value)=>calls.push(['save',key,value])};
 globalThis.fetch=async(url,options)=>{calls.push(['fetch',options.headers['X-Demo-Role']]);return {ok:true,json:async()=>access('requester')};};
 try{
  await switchRole('requester');
  assert.deepEqual(calls,[['fetch','requester'],['save','mf-demo-role','requester'],['navigate',userHome]]);
  calls.length=0;
  await assert.rejects(switchRole('provider')); // mismatched server identity
  assert.deepEqual(calls,[['fetch','provider']]);
  calls.length=0;globalThis.fetch=async()=>{throw Error('offline');};
  await assert.rejects(switchRole('platform'));assert.deepEqual(calls,[]);
  await assert.rejects(switchRole('unknown'));assert.deepEqual(calls,[]);
 }finally{for(const [key,value] of Object.entries(originals)){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}}
});
