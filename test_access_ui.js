import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {recordPreview} from './static/shared/record-model.js';
import {visibleNavigation,visibleExecutionTabs,level,canRead,pageResource,actionAllowed,managementActions,managementRule,presentation,executionActions,buttonState,paginationState} from './static/shared/access-model.js';

const policy=JSON.parse(readFileSync(new URL('./static/shared/access-policy.json',import.meta.url),'utf8'));
const snapshot=role=>({principal:{id:'demo-'+role,role},resources:Object.fromEntries(Object.entries(policy.resources).map(([k,v])=>[k,v[role]||'none'])),actions:Object.fromEntries(Object.entries(policy.actions).map(([k,v])=>[k,v.includes(role)]))});
const navKeys=role=>visibleNavigation(snapshot(role)).flatMap(([,items])=>items.map(x=>x[2]));

test('business previews label local actors without changing stored identifiers',()=>{
 const source={created_by:'demo-platform',provider:'demo-provider',parties:['demo-requester','demo-auditor'],nested:{subject:'demo-requester'},id:'REQ-202609-001'};
 const before=JSON.stringify(source);
 const preview=recordPreview(source);
 assert.equal(preview.created_by,'数据服务中心');
 assert.equal(preview.provider,'医院数据中心');
 assert.deepEqual(preview.parties,['临床研究中心','数据合规岗']);
 assert.equal(preview.nested.subject,'临床研究中心');
 assert.equal(preview.id,source.id);
 assert.equal(JSON.stringify(source),before);
});

test('business previews omit only internal fixture fields and retain authorization and quality facts',()=>{
 const source={demo:true,demo_seed:'HEALTHCARE-SCENARIOS-V2',seed_version:2,environment:'demo',legacy_seed:false,
  checks:{quality:false,authorization:true},restriction:'仅可交付汇总结果',remote_invocation:false,
  nested:[{seed_id:'internal',name:'检验报告完整率复核',reason:'报告状态字段缺失'}]};
 assert.deepEqual(recordPreview(source),{environment:'本机环境',checks:{quality:false,authorization:true},restriction:'仅可交付汇总结果',remote_invocation:false,
  nested:[{name:'检验报告完整率复核',reason:'报告状态字段缺失'}]});
 assert.equal(source.demo,true);
});

test('business previews preserve unknown identities, numbers, null values and business text',()=>{
 const source={subject:'hospital-user-17',count:0,ratio:0.965,missing:null,comment:'2026年9月随访汇总'};
 assert.deepEqual(recordPreview(source),source);
});

test('four roles have consistent navigation and tab visibility, including inaccessible modules',()=>{
 for(const role of ['platform','provider','requester','auditor']){
  const view=snapshot(role);
  for(const [name,items] of visibleNavigation(view)){assert.ok(items.length,name);for(const [,,key] of items)assert.notEqual(level(view,key),'none');}
  for(const [key] of visibleExecutionTabs(view))assert.ok(canRead(view,key));
 }
 assert.ok(navKeys('platform').includes('case'));
 assert.ok(navKeys('auditor').includes('case'));
 assert.ok(!navKeys('provider').includes('demand'));
 assert.ok(!navKeys('provider').includes('plan'));
 assert.ok(!navKeys('requester').includes('case'));
 assert.ok(!visibleExecutionTabs(snapshot('provider')).some(([key])=>key==='delivery'));
 assert.ok(visibleExecutionTabs(snapshot('auditor')).some(([key])=>key==='delivery'));
});

test('read-only service access retains reading but hides every service write action',()=>{
 for(const role of ['requester','auditor']){
  const view=snapshot(role);assert.equal(level(view,'service'),'read');
  assert.equal(actionAllowed(view,'service.read'),true);
  for(const verb of ['create','update','validate','transition','regularize','compose'])assert.equal(actionAllowed(view,'service.'+verb),false,role+' '+verb);
 }
});

test('provider can maintain only its service while publication and grants remain platform-only',()=>{
 const provider=snapshot('provider');
 assert.equal(actionAllowed(provider,'service.update',{provider:'demo-provider'}),true);
 assert.equal(actionAllowed(provider,'service.update',{provider:'another-provider'}),false);
 assert.equal(actionAllowed(provider,'service.transition'),false);
 for(const role of ['provider','requester','auditor'])assert.equal(actionAllowed(snapshot(role),'grant.create'),false);
 assert.equal(actionAllowed(snapshot('platform'),'grant.create'),true);
});

test('requester operations follow record ownership and terminal task state',()=>{
 const view=snapshot('requester');
 assert.equal(actionAllowed(view,'demand.update',{created_by:'demo-requester'}),true);
 assert.equal(actionAllowed(view,'demand.update',{created_by:'demo-platform'}),false);
 assert.equal(actionAllowed(view,'job.cancel',{actor:'demo-requester',state:'running'}),true);
 assert.equal(actionAllowed(view,'job.cancel',{actor:'demo-requester',state:'success'}),false);
 assert.equal(actionAllowed(view,'job.retry',{actor:'demo-requester',state:'failed'}),true);
 assert.equal(actionAllowed(view,'job.retry',{actor:'demo-platform',state:'failed'}),false);
});

test('delivery actions expose only the owners result and its confirmed mode',()=>{
 const owner=snapshot('requester'),record={actor:'demo-requester',state:'available',circulation_mode:'controlled_compute'};
 assert.equal(actionAllowed(owner,'delivery.read',record),true);
 assert.equal(actionAllowed(owner,'delivery.reference',record),false);
 assert.equal(actionAllowed(owner,'delivery.download',record),false);
 const api={...record,circulation_mode:'api'};
 assert.equal(actionAllowed(owner,'delivery.reference',api),true);
 assert.equal(actionAllowed(owner,'delivery.read',api),false);
 assert.equal(actionAllowed(owner,'delivery.download',{...record,circulation_mode:'download'}),true);
 for(const role of ['platform','auditor'])for(const key of ['delivery.read','delivery.reference','delivery.download'])assert.equal(actionAllowed(snapshot(role),key,record),false);
 assert.equal(actionAllowed(snapshot('auditor'),'delivery.verify',record),true);
 assert.equal(actionAllowed(owner,'delivery.read',{...record,state:'destroyed'}),false);
 assert.equal(actionAllowed(owner,'job.result',{actor:'demo-requester',delivery_id:'DEL-1'}),true);
 assert.equal(actionAllowed(owner,'job.result',{actor:'demo-requester'}),false);
});

test('contract confirmation is available only to an unconfirmed participant',()=>{
 const record={parties:['demo-provider','demo-requester'],accepted:[]};
 assert.equal(actionAllowed(snapshot('provider'),'contract.accept',record),true);
 assert.equal(actionAllowed(snapshot('platform'),'contract.accept',record),false);
 assert.equal(actionAllowed(snapshot('provider'),'contract.accept',{...record,accepted:['demo-provider']}),false);
});

test('subtab changes remove irrelevant operations and selection is required for row actions',()=>{
 const view=snapshot('platform');
 assert.deepEqual(presentation(view,managementRule('编辑需求','demand'),'demand',null),{visible:true,disabled:true});
 assert.deepEqual(presentation(view,managementRule('编辑需求','spec'),'spec',{created_by:'demo-platform'}),{visible:false,disabled:true});
 assert.equal(presentation(snapshot('auditor'),managementRule('历史版本','service'),'service',{id:'SVC-1'}).visible,true);
 assert.equal(presentation(snapshot('auditor'),managementRule('编辑服务','service'),'service',{id:'SVC-1'}).visible,false);
});

test('missing permissions and unknown actions fail closed',()=>{
 assert.equal(canRead({},'plan'),false);assert.equal(actionAllowed({},'plan.create'),false);
 assert.deepEqual(visibleNavigation({}),[]);assert.deepEqual(visibleExecutionTabs({}),[]);
 assert.equal(actionAllowed(snapshot('platform'),'new-unreviewed.operation'),false);
 assert.equal(presentation(snapshot('platform'),null,'plan',null).visible,false);
});

test('every registered management and execution action is backed by the server policy',()=>{
 for(const [label,rule] of Object.entries(managementActions))assert.ok(policy.actions[rule.key],label+' has no policy');
 for(const [label,[key]] of Object.entries(executionActions))assert.ok(policy.actions[key==='job.result'?'delivery.read':key],label+' has no policy');
});

test('direct page paths resolve to the same resources as navigation',()=>{
 assert.equal(pageResource('/static/management/intake.html'),'demand');
 assert.equal(pageResource('/static/execution/index.html?tab=delivery'),'delivery');
 assert.equal(pageResource('/static/execution/index.html'),'plan');
 assert.equal(canRead(snapshot('provider'),pageResource('/static/management/intake.html')),false);
 assert.equal(canRead(snapshot('auditor'),pageResource('/static/execution/index.html?tab=delivery')),true);
});

test('pagination boundaries remain disabled after permission refresh and action completion',()=>{
 const permission={visible:true,disabled:false};
 const states=[[0,0,true,true],[0,12,true,true],[0,25,true,false],[1,25,false,false],[2,25,false,true]];
 for(const [page,total,previous,next] of states){
  const paging=paginationState(page,total,12);
  assert.equal(paging.previousDisabled,previous);
  assert.equal(paging.nextDisabled,next);
  for(const businessDisabled of [paging.previousDisabled,paging.nextDisabled]){
   // A refresh, an active click, and its finally block preserve the page boundary.
   assert.equal(buttonState(permission,{businessDisabled,busy:false}).disabled,businessDisabled);
   assert.equal(buttonState(permission,{businessDisabled,busy:true}).disabled,true);
   assert.equal(buttonState(permission,{businessDisabled,busy:false}).disabled,businessDisabled);
  }
 }
});

test('permission and selection restrictions cannot be cleared by business-enabled state',()=>{
 assert.deepEqual(buttonState({visible:false,disabled:false},{businessDisabled:false}),{hidden:true,disabled:true});
 assert.equal(buttonState({visible:true,disabled:true},{businessDisabled:false}).disabled,true);
 assert.equal(buttonState({visible:true,disabled:false},{businessDisabled:false,busy:false}).disabled,false);
 assert.throws(()=>paginationState(-1,25,12),RangeError);
});
