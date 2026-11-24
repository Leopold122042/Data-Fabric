const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const BASE={actor:'研究组B',purpose:'统计研究',operation:'汇总查询',labelsComplete:true,extraField:false};
const CASES=[
 {id:'C01',name:'正常统计',status:'allow',expected:'附条件允许',change:'基准条件'},
 {id:'C02',name:'明细导出',status:'reject',expected:'拒绝：操作超范围',change:'操作改为明细导出'},
 {id:'C03',name:'用途改变',status:'reject',expected:'拒绝：用途不符',change:'用途改为商业营销'},
 {id:'C04',name:'授权到期',status:'reject',expected:'拒绝：授权到期',change:'模拟时刻改为 10'},
 {id:'C05',name:'标签缺失',status:'review',expected:'待复核：新增字段未标注',change:'新增未标注字段 extra_field'},
 {id:'C06',name:'主体不符',status:'reject',expected:'拒绝：主体不符',change:'申请主体改为研究组C'}
];
const OVERRIDES={
 C01:{},C02:{operation:'明细导出'},C03:{purpose:'商业营销'},C04:{},
 C05:{labelsComplete:false,extraField:true},C06:{actor:'研究组C'}
};
const DATA=Array.from({length:12},(_,index)=>{
 const n=index+1,id=`S${String(n).padStart(3,'0')}`;
 const ageGroup=n<=3?'30—39岁':n<=8?'40—49岁':'50—59岁';
 const diagnosis=n<=8?'类别甲':'类别乙';
 return {record_id:id,age_group:ageGroup,diagnosis,test_category:n%2?'检验乙':'检验丙'};
});
const GROUPS=[
 {age_group:'30—39岁',diagnosis:'类别甲',count:3},
 {age_group:'40—49岁',diagnosis:'类别甲',count:5},
 {age_group:'50—59岁',diagnosis:'类别乙',count:4}
];
const OBJECTS={
 D01:{name:'原始诊断表',kind:'源数据对象',fields:'record_id · age_group · diagnosis',upstream:'医疗统计模拟数据源',purpose:'标签：diagnosis 为健康信息；record_id 仅用于内部关联。'},
 D02:{name:'原始检验表',kind:'源数据对象',fields:'record_id · test_category',upstream:'医疗统计模拟数据源',purpose:'标签：test_category 为健康信息；record_id 仅用于内部关联。'},
 D03:{name:'字段投影表',kind:'中间处理对象',fields:'record_id · age_group · diagnosis',upstream:'D01 → 字段选择',purpose:'保留统计需要字段，record_id 仅用于关联。'},
 D04:{name:'关联明细表',kind:'中间处理对象',fields:'D03 字段 + test_category',upstream:'D03 + D02 → record_id 一一关联',purpose:'关联后 12 行；仅内部处理，不对研究组交付。'},
 O01:{name:'受控统计结果',kind:'交付对象',fields:'age_group · diagnosis · count',upstream:'D04 → 分组统计 → 人数阈值抑制',purpose:'仅限 T01 统计研究；少于 5 人的分组不交付。'}
};
const state={tab:'task',caseId:'C01',params:{...BASE},now:9,view:'operator',runs:[],runNumber:0,currentRunId:null,selectedRunId:null,selectedObject:'D01',batch:null,deliveryMessage:null,missingDeliveryRunId:null,verification:null};
let host=null;

function cloneBase(){return {...BASE};}
function activeRun(){return state.runs.find(run=>run.id===state.currentRunId)||null;}
function evidenceRun(){return state.runs.find(run=>run.id===state.selectedRunId)||activeRun()||state.runs[0]||null;}
function decisionFor(params,time){
 if(params.actor!=='研究组B')return {status:'reject',label:'拒绝：主体不符',reason:'申请主体不是规则 P01 允许的研究组B。'};
 if(!params.labelsComplete||params.extraField)return {status:'review',label:'待复核：新增字段未标注',reason:'检测到新增字段 extra_field 未配置字段标签，任务进入待复核，不运行统计、不返回数据。'};
 if(params.purpose!=='统计研究')return {status:'reject',label:'拒绝：用途不符',reason:'当前用途与授权用途“统计研究”不一致。'};
 if(params.operation!=='汇总查询')return {status:'reject',label:'拒绝：操作超范围',reason:'规则只允许汇总查询，不允许明细导出。'};
 if(time>=10)return {status:'reject',label:'拒绝：授权到期',reason:'当前模拟时刻为 10，规则 P01 仅在当前时刻小于 10 时有效。'};
 return {status:'allow',label:'附条件允许',reason:'主体、标签、用途、操作和期限均符合；仅交付汇总结果，并执行人数阈值检查。'};
}
function expectedParams(caseItem){return {...cloneBase(),...(OVERRIDES[caseItem.id]||{}),time:caseItem.id==='C04'?10:9};}
function testCase(caseItem){const p=expectedParams(caseItem),decision=decisionFor(p,p.time);return {...caseItem,actual:decision.label,statusActual:decision.status,pass:decision.status===caseItem.status,roleOnly:p.actor==='研究组B'?'允许':'拒绝'};}
function rowsFor(table){return DATA.map(row=>table==='D01'?{record_id:row.record_id,age_group:row.age_group,diagnosis:row.diagnosis}:{record_id:row.record_id,test_category:row.test_category});}
function eventTime(code){return code==='E06'?'10:00':code==='E07'?'10:01':({E01:'09:00',E02:'09:00',E03:'09:01',E04:'09:02',E05:'09:03'})[code.split('-')[0]]||'09:00';}
function addEvent(run,code,title,detail,result,extra={}){
 const base=code.split('-')[0],count=run.events.filter(item=>item.code.split('-')[0]===base).length;
 const uniqueCode=count?`${base}-${count+1}`:base;
 const item={code:uniqueCode,time:eventTime(base),title,detail,result,run_id:run.id,...extra};run.events.push(item);return item;
}
function applyCase(caseId){
 const item=CASES.find(value=>value.id===caseId);if(!item)return;
 state.caseId=caseId;state.params={...cloneBase(),...(OVERRIDES[caseId]||{})};state.now=caseId==='C04'?10:9;
 state.currentRunId=null;state.deliveryMessage=null;state.batch=null;state.verification=null;
 state.selectedRunId=state.runs[0]?.id||null;render();
}
function startRequest(){
 state.runNumber+=1;const id=`RUN-${String(state.runNumber).padStart(3,'0')}`;
 const snapshot={...state.params};const decision=decisionFor(snapshot,state.now);
 const run={id,caseId:state.caseId,params:snapshot,startedAt:state.now,decision,events:[],result:null,delivered:false};
 state.runs.unshift(run);state.currentRunId=id;state.selectedRunId=id;state.deliveryMessage=null;state.verification=null;
 if(state.now>=10)addEvent(run,'E06','授权到期',`模拟时刻已到 ${state.now}，授权状态为到期。`,'已记录');
 addEvent(run,'E01','申请',`T01 · ${snapshot.actor} · ${snapshot.purpose} · ${snapshot.operation} · 来源 D01、D02。`,'已受理',{subject:snapshot.actor,purpose:snapshot.purpose,operation:snapshot.operation,sources:['D01','D02']});
 addEvent(run,'E02','决策',`A01 · P01 · L01 · ${decision.label} · 授权期限 10。`,decision.reason,'已核验',{authorization_id:'A01',policy_id:'P01',label_id:'L01',decision:decision.status});
 if(decision.status==='allow'){
  run.result={joinedRows:12,groups:GROUPS.map(group=>({...group})),delivered:GROUPS.filter(group=>group.count>=5).map(group=>({...group})),columns:['age_group','diagnosis','count']};
  addEvent(run,'E03','加工','D01 → D03；D03 + D02 → D04；record_id 一一关联。','关联后 12 条',{objects:['D01','D02','D03','D04'],joined_rows:12});
  addEvent(run,'E04','输出','O01 内部分组人数 3、5、4；低于 5 人的分组抑制。','仅一条分组满足交付阈值',{object_id:'O01',internal_counts:[3,5,4],delivered_groups:1});
 }else if(decision.label==='拒绝：授权到期'){
  addEvent(run,'E07','再申请','T01 在时刻 10 发起新的执行 / 交付请求。','拒绝：授权到期',{decision:'reject',reason:'授权到期'});
 }
 state.tab='result';render();
}
function advanceTime(){
 if(state.now>=10)return;
 state.now=10;state.verification=null;
 const run=activeRun();if(run&&!run.events.some(item=>item.code.split('-')[0]==='E06'))addEvent(run,'E06','授权到期','模拟时刻由 9 推进到 10；已生成的历史交付记录保留。','授权已到期');
 state.deliveryMessage=null;render();
}
function gateDelivery(method){
 const run=activeRun();
 if(!run?.result){state.deliveryMessage={status:'block',title:'当前没有可交付结果',reason:'先在有效授权下执行 C01 统计任务。'};render();return;}
 const decision=decisionFor(run.params,state.now);
 if(decision.status!=='allow'){
  state.deliveryMessage={status:'block',title:decision.label,reason:decision.reason};
  if(decision.label==='拒绝：授权到期')addEvent(run,'E07','再申请','T01 对已生成的 O01 发起新的交付请求。','拒绝：授权到期',{decision:'reject',reason:'授权到期'});
  render();return;
 }
 addEvent(run,'E05','交付',`O01 → 研究组B · ${method==='download'?'下载':'结果预览'} · age_group、diagnosis、count。`,'已按当前授权重新核验',{object_id:'O01',receiver:'研究组B',fields:['age_group','diagnosis','count'],row_count:1});
 run.delivered=true;state.deliveryMessage={status:'allow',title:'交付允许',reason:'授权在当前模拟时刻仍有效；仅返回 40—49岁、类别甲、5。',row:{age_group:'40—49岁',diagnosis:'类别甲',count:5}};
 if(method==='download'){
  const blob=new Blob([JSON.stringify({columns:run.result.columns,rows:[["40—49岁","类别甲",5]],demo:true},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`T01-${run.id}-O01.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }
 state.selectedRunId=run.id;state.verification=null;render();
}
function runBatch(){state.batch=CASES.map(testCase);state.currentRunId=null;state.deliveryMessage=null;state.tab='result';render();}
function currentConditions(){return `<div class="t4-condition-list"><div><span>任务</span><b>T01 医疗统计</b></div><div><span>主体</span><b>${esc(state.params.actor)}</b></div><div><span>用途</span><b>${esc(state.params.purpose)}</b></div><div><span>操作</span><b>${esc(state.params.operation)}</b></div><div><span>模拟时刻</span><b class="${state.now>=10?'is-expired':''}">${state.now} / 到期点 10</b></div><div><span>字段标签</span><b>${state.params.labelsComplete&&!state.params.extraField?'完整':'存在未标注字段'}</b></div><div><span>策略 / 标签</span><b>P01 / L01</b></div></div>`;}
function tablePreview(table){const rows=rowsFor(table);return `<details class="t4-data-details"><summary>${table} · ${rows.length} 条模拟记录</summary><div class="t4-table-scroll"><table class="t4-data-table"><thead><tr>${Object.keys(rows[0]).map(key=>`<th>${esc(key)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${Object.values(row).map(value=>`<td>${esc(value)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`;}
function taskPanel(){return `<section class="t4-panel-card"><div class="t4-panel-head"><div><span class="t4-kicker">任务设置 · 预置数据与规则</span><h2>T01 医疗统计安全使用演示</h2><p>两张各 12 条的模拟表，按 record_id 一一关联；字段标签、策略 P01 和模拟时钟作为固定输入。</p></div><span class="t4-chip">合成数据</span></div>
<div class="t4-conditions-grid"><label>申请主体<select id="t4-actor"><option ${state.params.actor==='研究组B'?'selected':''}>研究组B</option><option ${state.params.actor==='研究组C'?'selected':''}>研究组C</option></select></label><label>使用用途<select id="t4-purpose"><option ${state.params.purpose==='统计研究'?'selected':''}>统计研究</option><option ${state.params.purpose==='商业营销'?'selected':''}>商业营销</option></select></label><label>请求操作<select id="t4-operation"><option ${state.params.operation==='汇总查询'?'selected':''}>汇总查询</option><option ${state.params.operation==='明细导出'?'selected':''}>明细导出</option></select></label><label class="t4-check-label"><input id="t4-labels" type="checkbox" ${state.params.labelsComplete&&!state.params.extraField?'checked':''}><span>字段标签完整</span><small>${state.params.extraField?'演示副本含未标注字段 extra_field':'诊断类别、检验类别已标记健康信息'}</small></label></div>
<div class="t4-case-heading"><strong>六类预置请求</strong><small>切换时先恢复基准条件，再只改动该案例的一项条件。</small></div><div class="t4-case-grid">${CASES.map(item=>`<button type="button" class="t4-case ${state.caseId===item.id?'selected':''}" data-case="${item.id}"><span>${item.id}</span><strong>${esc(item.name)}</strong><small>${esc(item.change)}</small></button>`).join('')}</div>
<div class="t4-actions"><button class="t4-button primary" data-action="run">执行请求</button><button class="t4-button" data-action="batch">一键检查六类案例</button><button class="t4-button" data-action="advance" ${state.now>=10?'disabled':''}>推进到到期时刻 ${state.now<10?'9 → 10':'（已到期）'}</button><button class="t4-button quiet" data-action="reset">恢复初始状态</button></div>
<div class="t4-data-counts"><div><small>D01 诊断表</small><strong>12 条</strong><span>record_id · age_group · diagnosis</span></div><div><small>D02 检验表</small><strong>12 条</strong><span>record_id · test_category</span></div><div><small>预期关联</small><strong>12 条</strong><span>record_id 唯一且一一对应</span></div></div>
<div class="t4-data-preview">${tablePreview('D01')}${tablePreview('D02')}</div>
<div class="t4-rule-note"><strong>固定规则</strong><span>主体：研究组B</span><span>用途：统计研究</span><span>操作：汇总查询</span><span>有效：当前时刻 &lt; 10</span><span>小样本阈值：5</span></div>
<p class="t4-disclosure">页面交互只使用浏览器内的模拟数据和模拟时钟，不访问医疗数据源；操作员可查看内部统计，研究组只可见通过复核的交付结果。</p></section>`;}
function currentRunDecision(){const run=activeRun();return run?.decision||null;}
function deliveryResult(run){if(!run?.result)return '';return `<div class="t4-delivery-box"><div class="t4-delivery-title"><span>O01 · 允许交付结果</span><small>只含通过阈值的分组</small></div><table class="t4-result-table"><thead><tr><th>age_group</th><th>diagnosis</th><th>count</th></tr></thead><tbody><tr><td>40—49岁</td><td>类别甲</td><td>5</td></tr></tbody></table><p>未返回 record_id、test_category，也未返回人数为 3 和 4 的分组。</p></div>`;}
function batchMarkup(){if(!state.batch)return '';const passed=state.batch.filter(item=>item.pass).length;return `<section class="t4-batch"><div class="t4-case-heading"><strong>六类案例检查</strong><span class="t4-batch-total">本次演示通过 ${passed}/6 项</span></div><div class="t4-table-scroll"><table class="t4-batch-table"><thead><tr><th>案例</th><th>预期完整规则</th><th>实际完整规则</th><th>仅核对角色</th><th>检查</th></tr></thead><tbody>${state.batch.map(item=>`<tr><td><b>${item.id}</b> ${esc(item.name)}</td><td>${esc(item.expected)}</td><td>${esc(item.actual)}</td><td>${esc(item.roleOnly)}</td><td><span class="t4-result-pill ${item.pass?'pass':'fail'}">${item.pass?'通过':'未通过'}</span></td></tr>`).join('')}</tbody></table></div><p>“待复核”单独作为第三种结果；本检查只校验六类预置规则，不把演示统计成报告中的实验运行量。</p></section>`;}
function resultsPanel(){const run=activeRun(),decision=currentRunDecision();const decisionBlock=decision?`<div class="t4-decision ${decision.status}"><span class="t4-decision-state">本轮执行判断 · ${esc(decision.label)}</span><strong>${esc(decision.status==='allow'?'附条件允许：仅交付汇总结果，并执行人数阈值检查':decision.label)}</strong><p>${esc(decision.reason)}</p><small>运行 ${esc(run.id)} · 执行时模拟时刻 ${run.startedAt} · 期限点 10</small></div>`:'<div class="t4-empty">尚未执行请求。选择一个案例后点击“执行请求”，查看判断和结果。</div>';
const liveGate=run?decisionFor(run.params,state.now):null;const expiryBanner=run?.result&&liveGate?.status!=='allow'&&!state.deliveryMessage?`<div class="t4-delivery-message block"><strong>当前授权已到期</strong><p>本轮结果在时刻 ${run.startedAt} 生成；现在是时刻 ${state.now}。新的预览或下载请求将被拒绝，原时刻 9 的合法交付记录继续保留。</p></div>`:'';
const internal=run?.result?`<section class="t4-internal"><div class="t4-section-head"><div><strong>内部统计视图</strong><small>仅演示操作员可见；小于 5 的分组不交付</small></div><span>关联 12 行</span></div><div class="t4-group-grid">${run.result.groups.map(group=>{const allowed=group.count>=5;return `<div class="t4-group ${allowed?'deliverable':'suppressed'}"><small>${esc(group.age_group)} · ${esc(group.diagnosis)}</small><strong>${group.count}</strong><span>${allowed?'达到阈值，可交付':'低于阈值，抑制'}</span></div>`;}).join('')}</div><div class="t4-suppression-note">人数阈值 = 5 · 内部人数 3、5、4 · 对外只保留 5 人分组</div></section>`:'';
const recipient=run?.result?`<section class="t4-recipient"><div class="t4-section-head"><div><strong>研究组B交付视图</strong><small>预览与下载都会重新核验当前主体、用途、操作、标签和期限</small></div><span class="t4-lock">受控交付</span></div><div class="t4-actions"><button class="t4-button primary" data-action="preview">预览 O01</button><button class="t4-button" data-action="download">下载 O01</button></div>${state.deliveryMessage?`<div class="t4-delivery-message ${state.deliveryMessage.status}"><strong>${esc(state.deliveryMessage.title)}</strong><p>${esc(state.deliveryMessage.reason)}</p>${state.deliveryMessage.status==='allow'?`<div class="t4-inline-result"><b>${esc(state.deliveryMessage.row.age_group)}</b><b>${esc(state.deliveryMessage.row.diagnosis)}</b><b>${state.deliveryMessage.row.count}</b></div>`:''}</div>`:'<p class="t4-muted">尚未预览或下载。运行任务不会自动交付，需单独通过交付复核。</p>'}</section>`:`<section class="t4-recipient"><div class="t4-section-head"><div><strong>研究组B交付视图</strong><small>只有完整规则通过后才会生成或读取交付数据</small></div></div><p class="t4-muted">当前请求被拒绝或待复核，没有交付结果。</p></section>`;
return `<section class="t4-panel-card"><div class="t4-panel-head"><div><span class="t4-kicker">判断与结果</span><h2>看得到判断原因，也看得到输出边界</h2><p>完整规则顺序：主体 → 标签 → 用途 → 操作 → 期限。拒绝或待复核均不返回数据。</p></div><span class="t4-chip">T01</span></div>${decisionBlock}${expiryBanner}<div class="t4-view-switch" role="group" aria-label="选择查看视角"><span>查看视角</span><button class="${state.view==='operator'?'active':''}" data-view="operator">演示操作员</button><button class="${state.view==='requester'?'active':''}" data-view="requester">研究组B</button></div>${state.view==='operator'?internal:recipient}${state.view==='operator'&&run?.result?`<div class="t4-operator-delivery">${recipient}</div>`:''}${batchMarkup()}<section class="t4-role-compare"><div class="t4-section-head"><div><strong>与“仅核对角色”的方案对照</strong><small>对照列只呈现判断差异，不作为数据交付依据</small></div></div><div class="t4-compare-grid">${CASES.map(item=>{const onlyRole=item.id==='C06'?'拒绝':'允许',full=testCase(item).actual;return `<div><span>${item.id}</span><b>${esc(onlyRole)}</b><em>${esc(full)}</em></div>`;}).join('')}</div></section><div class="t4-report-result"><strong>报告实验结果 · 只读展示</strong><span>16 个加工案例中核验 48 项属性判断，按加工方式更新的结果均与预设答案一致。</span><small>该项为报告实验结果，与本页六类现场演示分开。</small></div></section>`;}
function objectGraph(){const item=OBJECTS[state.selectedObject];return `<div class="t4-object-detail"><span>${esc(item.kind)}</span><h3>${state.selectedObject} · ${esc(item.name)}</h3><p><b>字段</b>${esc(item.fields)}</p><p><b>来源 / 处理</b>${esc(item.upstream)}</p><p><b>使用边界</b>${esc(item.purpose)}</p></div>`;}
function currentEvidence(){const run=evidenceRun();if(!run)return null;const hidden=state.missingDeliveryRunId===run.id;return {run,hidden,events:run.events.filter(item=>!(hidden&&item.code.split('-')[0]==='E05'))};}
function lineagePanel(){const selected=currentEvidence();const allRuns=state.runs;const current=selected?.run||null;const hasE03=Boolean(current?.events.some(item=>item.code.split('-')[0]==='E03'));const evidence=selected?.events.find(item=>item.code.split('-')[0]==='E05');const deliveryComplete=Boolean(evidence?.receiver&&evidence?.fields?.length);const verified=state.verification?.runId===current?.id;
return `<section class="t4-panel-card"><div class="t4-panel-head"><div><span class="t4-kicker">来源与记录</span><h2>从结果 O01 回到来源，也核对交付去向</h2><p>五个对象、四条有向关系；缺少交付证据时不推断实际接收方。</p></div><span class="t4-chip">${current?esc(current.id):'尚无运行'}</span></div>
<div class="t4-graph" aria-label="数据对象血缘关系"><div class="t4-graph-row"><button class="t4-object-node ${state.selectedObject==='D01'?'selected':''}" data-object="D01"><small>D01</small><b>诊断表</b></button><span>→</span><button class="t4-object-node ${state.selectedObject==='D03'?'selected':''}" data-object="D03"><small>D03</small><b>字段投影</b></button><span>→</span><button class="t4-object-node ${state.selectedObject==='D04'?'selected':''}" data-object="D04"><small>D04</small><b>关联明细</b></button><span>→</span><button class="t4-object-node ${state.selectedObject==='O01'?'selected':''}" data-object="O01"><small>O01</small><b>统计交付</b></button></div><div class="t4-graph-branch"><button class="t4-object-node ${state.selectedObject==='D02'?'selected':''}" data-object="D02"><small>D02</small><b>检验表</b></button><span>└──────────────→ D04</span></div></div>${objectGraph()}
<div class="t4-verify-bar"><label>核验运行<select id="t4-evidence-run">${allRuns.length?allRuns.map(run=>`<option value="${esc(run.id)}" ${run.id===current?.id?'selected':''}>${esc(run.id)} · ${esc(run.caseId)} · ${esc(run.decision.label)}</option>`).join(''):'<option value="">尚无运行记录</option>'}</select></label><div class="t4-actions"><button class="t4-button primary" data-action="verify" ${current?'':'disabled'}>核验来源与交付</button><button class="t4-button" data-action="remove-e05" ${evidence?'':'disabled'}>模拟缺失交付记录</button><button class="t4-button" data-action="restore-e05" ${selected?.hidden?'':'disabled'}>恢复交付记录</button></div></div>
${verified?`<div class="t4-verification"><div class="${hasE03?'ok':'pending'}"><b>来源关系</b><span>${hasE03?'完整：D01、D02 → D03、D04 → O01':'尚未形成完整加工记录'}</span></div><div class="${deliveryComplete?'ok':selected?.hidden?'missing':'pending'}"><b>交付证据</b><span>${deliveryComplete?`完整：接收方 ${esc(evidence.receiver)} · 字段 ${esc(evidence.fields.join('、'))}`:selected?.hidden?'交付证据缺失，无法确认接收方及交付范围':'尚未形成成功交付记录'}</span></div></div>`:''}
<div class="t4-event-head"><strong>任务事件</strong><span>事件 ID 在每个 run_id 内唯一；失败请求保留决策记录。</span></div>${current?`<div class="t4-table-scroll"><table class="t4-events"><thead><tr><th>事件</th><th>模拟时刻</th><th>动作</th><th>记录</th><th>结果</th></tr></thead><tbody>${(selected?.events||[]).map(item=>`<tr><td>${esc(item.code)}</td><td>${esc(item.time)}</td><td>${esc(item.title)}</td><td>${esc(item.detail)}</td><td>${esc(item.result)}</td></tr>`).join('')||'<tr><td colspan="5">该运行暂无事件</td></tr>'}</tbody></table></div>`:'<div class="t4-empty">先执行一个案例，再查看该轮运行的事件记录。</div>'}
<p class="t4-disclosure">“模拟缺失交付记录”只从本轮核验副本中移除 E05；不会删除原始演示事件。缺项表示无法确认接收方和范围，不代表已经发生数据泄露。</p></section>`;}
function panelMarkup(){return state.tab==='task'?taskPanel():state.tab==='result'?resultsPanel():lineagePanel();}
function summaryMarkup(){const run=activeRun(),decision=run?decisionFor(run.params,state.now):null;return `<aside class="t4-summary-card"><div class="t4-summary-title"><small>当前演示状态</small><strong>${decision?esc(decision.label):'等待执行请求'}</strong></div>${currentConditions()}<div class="t4-summary-object"><span>当前 run_id</span><b>${run?esc(run.id):'—'}</b></div><div class="t4-summary-note"><strong>交付前再次检查</strong><p>时刻 9 有效；推进到 10 后，新的预览或下载请求必须拒绝。先前合法交付历史仍保留。</p></div><button class="t4-button quiet full" data-action="reset">恢复初始状态</button></aside>`;}
function render(){if(!host)return;const root=host.querySelector('.t4-root');if(!root)return;root.querySelector('#t4-panel').innerHTML=panelMarkup();root.querySelector('#t4-summary').innerHTML=summaryMarkup();root.querySelectorAll('[data-t4-tab]').forEach(button=>{const active=button.dataset.t4Tab===state.tab;button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));});}
function changeParams(key,value){state.params={...state.params,[key]:value};state.caseId='CUSTOM';state.currentRunId=null;state.deliveryMessage=null;state.batch=null;state.verification=null;}
function clickHandler(event){const tab=event.target.closest('[data-t4-tab]');if(tab){state.tab=tab.dataset.t4Tab;render();return;}const caseButton=event.target.closest('[data-case]');if(caseButton){applyCase(caseButton.dataset.case);return;}const viewButton=event.target.closest('[data-view]');if(viewButton){state.view=viewButton.dataset.view;render();return;}const objectButton=event.target.closest('[data-object]');if(objectButton){state.selectedObject=objectButton.dataset.object;state.verification=null;render();return;}const button=event.target.closest('[data-action]');if(!button)return;
 const action=button.dataset.action;
 if(action==='run'){startRequest();return;}
 if(action==='batch'){runBatch();return;}
 if(action==='advance'){advanceTime();return;}
 if(action==='reset'){reset();return;}
 if(action==='preview'){gateDelivery('preview');return;}
 if(action==='download'){gateDelivery('download');return;}
 if(action==='verify'){state.verification={runId:evidenceRun()?.id||null};render();return;}
 if(action==='remove-e05'){const run=evidenceRun();if(run?.events.some(item=>item.code.split('-')[0]==='E05')){state.missingDeliveryRunId=run.id;state.verification={runId:run.id};render();}return;}
 if(action==='restore-e05'){state.missingDeliveryRunId=null;state.verification={runId:evidenceRun()?.id||null};render();}
}
function changeHandler(event){const target=event.target;
 if(target.id==='t4-actor'){changeParams('actor',target.value);render();}
 if(target.id==='t4-purpose'){changeParams('purpose',target.value);render();}
 if(target.id==='t4-operation'){changeParams('operation',target.value);render();}
 if(target.id==='t4-labels'){changeParams('labelsComplete',target.checked);state.params.extraField=!target.checked;render();}
 if(target.id==='t4-evidence-run'){state.selectedRunId=target.value;state.verification=null;render();}
}
function reset(){state.tab='task';state.caseId='C01';state.params=cloneBase();state.now=9;state.view='operator';state.runs=[];state.runNumber=0;state.currentRunId=null;state.selectedRunId=null;state.selectedObject='D01';state.batch=null;state.deliveryMessage=null;state.missingDeliveryRunId=null;state.verification=null;render();}

export function renderTopicFour(target){
 host=target;
 host.innerHTML=`<div class="t4-root"><div class="t4-topline"><div><span>医疗统计安全使用演示</span><small>结题报告 T01 · 固定样例与模拟时钟</small></div><span class="t4-demo-pill"><i></i>不连接真实医疗源</span></div><nav class="t4-tabs" role="tablist" aria-label="医疗统计演示区域"><button data-t4-tab="task" role="tab" aria-selected="${state.tab==='task'}" type="button">01 任务设置</button><button data-t4-tab="result" role="tab" aria-selected="${state.tab==='result'}" type="button">02 判断与结果</button><button data-t4-tab="lineage" role="tab" aria-selected="${state.tab==='lineage'}" type="button">03 来源与记录</button></nav><div class="t4-layout"><section id="t4-panel" role="tabpanel"></section><div id="t4-summary"></div></div></div>`;
 host.onclick=clickHandler;host.onchange=changeHandler;render();
}
