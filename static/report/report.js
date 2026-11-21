import {guideHtml} from './topic2-guide.js?v=019';
import {caseIntro,caseContent} from './topic2-case.js?v=case-017';
import {renderTopicFour} from './topic4.js?v=topic4-r2-20261113';
import {renderTopicThree} from './topic3.js?v=topic3-v1-20261118';
const $=selector=>document.querySelector(selector);
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const topics={
 '1':{title:'医疗数据编织平台总体框架',label:'课题 01 / 04 · 总体框架',summary:'从数据准备与语义资产出发，串联需求解析、方案编译、受控执行和结果反馈；点击图中节点直达对应平台功能。'},
 '2':{title:'多源数据编织与可信执行',label:'课题 02 / 04 · 能力演示',summary:'从异构接入、语义统一到合规执行、运行反馈，沿同一条演示链路展示关键能力。'},
 '3':{title:'服务供给与可信交付',label:'课题 03 / 04 · 服务编排演示',summary:'从需求澄清与任务规格开始，按用途组合服务图，经合约策略校验后执行和交付，并以同范围复测沉淀改进证据。'},
 '4':{title:'医疗统计安全使用演示',label:'课题 04 / 04 · 子课题四对接子课题五',summary:'以 T01 为例，现场核对 12 条关联、人数阈值、六类授权判断、到期后拒绝，以及来源和交付证据。'}
};
const stages=[
 {id:'sources',label:'多源接入'},
 {id:'semantics',label:'语义关联'},
 {id:'virtual',label:'逻辑集成与合约'},
 {id:'orchestration',label:'编排与调度'},
 {id:'feedback',label:'运行反馈与证据'}
];
const frameworkLinks=[
 {label:'步骤12：沉淀证据并触发复核',href:'/static/management/feedback.html',x:22.5,y:15.3,w:29.0,h:10.0},
 {label:'步骤11：核验并交付结果',href:'/static/execution/index.html',x:53.4,y:15.3,w:29.0,h:10.0},
 {label:'步骤08：编译逻辑视图与方案',href:'/static/execution/index.html',x:13.5,y:32.2,w:22.4,h:9.8},
 {label:'步骤09：核验授权与执行约束',href:'/static/execution/contracts.html',x:39.2,y:32.2,w:22.5,h:9.8},
 {label:'步骤10：编排并只读执行',href:'/static/execution/index.html',x:65.0,y:32.2,w:22.3,h:9.8},
 {label:'步骤07：澄清并确认规格',href:'/static/management/intake.html',x:12.3,y:52.1,w:18.4,h:8.2},
 {label:'步骤06：匹配候选字段',href:'/static/management/intake.html',x:31.2,y:52.1,w:18.4,h:8.2},
 {label:'步骤05：解析结构化意图',href:'/static/management/intake.html',x:50.8,y:52.1,w:18.4,h:8.2},
 {label:'步骤04：提交查询需求',href:'/static/management/intake.html',x:70.0,y:52.1,w:18.4,h:8.2},
 {label:'步骤01：登记数据源',href:'/static/management/catalog.html',x:13.5,y:67.0,w:22.4,h:9.8},
 {label:'步骤02：形成目录与字段库',href:'/static/management/field-library.html',x:39.2,y:67.0,w:22.5,h:9.8},
 {label:'步骤03：确认概念与实体关系',href:'/static/management/semantic.html',x:65.0,y:67.0,w:22.3,h:9.8},
 {label:'跨流程反馈回流',href:'/static/management/feedback.html',x:2.2,y:43.7,w:4.8,h:3.8},
 {label:'贯穿约束：版本绑定',href:'/static/management/field-library.html',x:13.1,y:89.0,w:23.5,h:4.0},
 {label:'贯穿约束：策略复核',href:'/static/execution/contracts.html',x:38.0,y:89.0,w:23.5,h:4.0},
 {label:'贯穿约束：证据回流',href:'/static/management/feedback.html',x:62.8,y:89.0,w:24.0,h:4.0}
];
const workflowDefinition=[
 {name:'读取医院诊断表',detail:'医院源 · diagnosis_record',description:'读取本次用途允许的诊断字段'},
 {name:'关联医保结算记录',detail:'医保源 · claim_header',description:'使用已确认的统一患者标识关联'},
 {name:'按统一概念计算队列统计',detail:'概念 · diagnosis.pneumonia',description:'将异构诊断编码归一后按月聚合'},
 {name:'小样本抑制并脱敏交付',detail:'交付 · monthly_summary',description:'执行输出策略后生成汇总结果'}
];
const objects=[
 {id:'data',kind:'数据对象',name:'异构来源数据对象',ref:'OBJ-HOSP-001',summary:'医院、医保、科研源登记后的对象快照，随对象保存主体、用途和有效期约束。',props:()=>[['来源','医院 HIS / 医保 / 科研'],['主体','临床科研中心'],['用途','肺炎病例队列统计'],['版本',state.sourceChanged?'v1.5 · 检测到变化':'v1.4 · 当前快照']]},
 {id:'concept',kind:'统一业务概念',name:'统一诊断概念',ref:'CONCEPT-DX-001',summary:'通过代码体系、名称、字段类型与业务上下文等多项证据，把不同源编码关联到统一概念。',props:()=>[['概念编码','diagnosis.pneumonia'],['匹配状态',state.semanticMatched?'已生成示例映射':'候选待匹配'],['来源映射','HIS · 医保 · 科研']]},
 {id:'view',kind:'合约策略视图',name:'肺炎队列逻辑视图',ref:'VIEW-COHORT-01',summary:'跨源逻辑视图和合约策略共同限定可访问主体、用途、期限与结果粒度。',props:()=>[['逻辑视图','LV-PNEUMONIA-MONTHLY'],['策略状态',state.policy?'已生成本地策略草案':'等待生成策略'],['敏感级别','健康信息']]},
 {id:'plan',kind:'执行编排',name:'队列统计执行计划',ref:'FLOW-COHORT-017',summary:'把需求拆成读取、关联、计算、脱敏交付四个步骤，记录每步状态与模拟耗时。',props:()=>[['步骤数','4 步'],['执行状态',state.workflow.complete?'已完成':state.workflow.running?'执行中':'待运行'],['本次耗时',state.workflow.complete?`${state.workflow.elapsed} ms（演示）`:'—']]},
 {id:'evidence',kind:'证据链',name:'全程运行证据',ref:'EVID-TRACE-082',summary:'汇集访问放行、违规拦截、变更处置和资源计量记录，支持按演示流程回看。',props:()=>[['事件数',`${state.events.length} 条本地演示事件`],['变化处置',state.feedback.complete?'已完成并回写版本':'待触发'],['审计记录','访问 · 拦截 · 处置 · 计量']]}
];
const state={
 catalogRegistered:false,accessChecks:{},routeChecks:new Set(),completedModes:new Set(),topic:'2',stage:'sources',activeObject:'data',contractText:'委托方：医院数据中心；受托方：临床科研中心；使用用途：肺炎病例队列统计；有效期至：2027-03-31；输出粒度：按月汇总；禁止直接识别、再识别和跨用途使用。',
 sourceChanged:false,semanticMatched:false,policy:null,policyResult:null,sensitivity:'health',routeResult:null,
 workflow:{running:false,complete:false,mode:'normal',elapsed:0,steps:workflowDefinition.map(()=>({status:'pending',duration:null,attempt:1}))},
 feedback:{running:false,phase:0,complete:false},evidenceVisible:false,
 events:[]
};

function logEvent(kind,detail,result='已记录'){
 const time=new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date());
 state.events.unshift({time,kind,detail,result});
 renderObjectRail();
}
function setTopic(topic){
 if(!topics[topic])return;
 state.topic=topic;
 document.querySelectorAll('.topic-tab').forEach(tab=>{
  const active=tab.dataset.topic===topic;tab.classList.toggle('active',active);tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;
 });
 $('#topic-content').setAttribute('aria-labelledby',`topic-tab-${topic}`);
 const data=topics[topic];$('#topic-eyebrow').textContent=data.label;$('#topic-title').textContent=data.title;$('#topic-summary').textContent=data.summary;
 $('.topic-heading').classList.toggle('topic-one-heading',topic==='1');
 document.querySelector('.heading-mark').textContent=topic.padStart(2,'0');
 const url=new URL(location.href);if(topic==='2')url.searchParams.delete('topic');else url.searchParams.set('topic',topic);history.replaceState(null,'',url);
 if(topic==='2')renderTopicTwo();
 else if(topic==='1')renderTopicOne();
 else if(topic==='3')renderTopicThree($('#topic-content'));
 else if(topic==='4')renderTopicFour($('#topic-content'));
 else $('#topic-content').innerHTML=`<article class="placeholder-card"><span class="placeholder-index">${topic.padStart(2,'0')}</span><h2>${esc(data.title)} · 内容待补充</h2><p>四个课题共用这套独立汇报界面。请把这一课题的目标、展示要点和希望演示的功能发来，我再补上对应内容。</p><small>当前先完成课题二的端到端交互样例</small></article>`;
}
function renderTopicOne(){
 const links=frameworkLinks.map(link=>`<a class="framework-hotspot" href="${link.href}" target="_blank" rel="noopener" aria-label="${esc(link.label)}，在新标签页打开" title="${esc(link.label)}" style="left:${link.x}%;top:${link.y}%;width:${link.w}%;height:${link.h}%"><span>打开对应功能 ↗</span></a>`).join('');
 $('#topic-content').innerHTML=`<article class="framework-panel graphic-only"><div class="framework-scroll"><div class="framework-board"><img src="/static/report/images/topic5-framework.png" alt="医疗数据编织平台总体框架图" width="2100" height="1500" loading="eager">${links}</div></div></article>`;
}
function renderTopicTwo(){
 $('#topic-content').innerHTML=`<div id="case-guide">${guideHtml(state)}</div><details class="case-fixture"><summary>查看本次演示案例与数据范围</summary>${caseIntro()}</details><div class="journey-tabs" role="tablist" aria-label="课题二演示阶段">${stages.map((stage,index)=>`<button class="journey-tab ${state.stage===stage.id?'active':''}" role="tab" aria-selected="${state.stage===stage.id}" aria-controls="stage-panel" data-stage="${stage.id}" type="button"><span>${String(index+1).padStart(2,'0')}</span>${esc(stage.label)}</button>`).join('')}</div><div class="demo-layout"><section class="stage-panel" id="stage-panel" role="tabpanel"></section><aside class="object-rail" id="object-rail" aria-label="贯穿全程的中间对象"></aside></div>`;
 document.querySelectorAll('[data-stage]').forEach(button=>button.addEventListener('click',()=>{state.stage=button.dataset.stage;renderTopicTwo();}));
 $('#case-guide').onclick=event=>{const step=event.target.closest('[data-guide-stage]');if(step){state.stage=step.dataset.guideStage;renderTopicTwo();$('#stage-panel').scrollIntoView({behavior:'smooth',block:'start'});return;}if(event.target.closest('[data-guide-reset]')){if(state.workflow.running||state.feedback.running){alert('请等待当前演示完成再重置。');return;}state.catalogRegistered=false;state.semanticMatched=false;state.policy=null;state.policyResult=null;state.accessChecks={};state.routeChecks=new Set();state.completedModes=new Set();state.events=[];state.evidenceVisible=false;state.sourceChanged=false;state.feedback={running:false,phase:0,complete:false};state.workflow={running:false,complete:false,mode:'normal',elapsed:0,steps:workflowDefinition.map(()=>({status:'pending',duration:null,attempt:1}))};state.stage='sources';renderTopicTwo();}};
 renderStage();
}
function refreshGuide(){const host=$('#case-guide');if(host)host.innerHTML=guideHtml(state);}
function header(index,title,subtitle,badge='演示样例',enhanced=false){return `<div class="stage-title-row"><div><span class="stage-kicker">课题二 · 阶段 ${String(index).padStart(2,'0')}</span><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div><span class="stage-badge ${enhanced?'enhanced':''}">${esc(badge)}</span></div>`;}
function platformLinks(items){return `<div class="platform-links"><span>对应平台功能</span>${items.map(([label,url])=>`<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label)} ↗</a>`).join('')}</div>`;}
function sourcesStage(){return `<div class="stage-content">${header(1,'多源接入与元数据组织','医院、医保、科研平台异构源进入统一目录；数据对象携带主体、用途和期限约束。')}
<div class="stage-actions"><button class="action-button primary" data-action="register-case">登记本案例目录快照</button></div><p class="section-label">异构来源 · 合成演示数据</p><div class="source-grid">
<article class="source-card"><div class="source-top"><h3>医院诊疗平台</h3><span class="source-tag">HIS</span></div><p>医院数据中心 · 已登记</p><div class="source-objects">patient_core · diagnosis_record</div></article>
<article class="source-card"><div class="source-top"><h3>医保结算平台</h3><span class="source-tag">医保</span></div><p>医保结算中心 · 已登记</p><div class="source-objects">claim_header · claim_item</div></article>
<article class="source-card"><div class="source-top"><h3>科研协作平台</h3><span class="source-tag">科研</span></div><p>临床科研中心 · 已登记</p><div class="source-objects">study_subject · study_visit</div></article></div>
<div class="object-constraints" aria-label="数据对象约束"><span class="constraint-chip">主体 <strong>临床科研中心</strong></span><span class="constraint-chip">用途 <strong>肺炎病例队列统计</strong></span><span class="constraint-chip">有效期 <strong>2027-03-31</strong></span></div>
<p class="section-label">统一目录与对象血缘</p><div class="lineage-strip"><div class="lineage-node"><small>源对象</small><strong>HIS / diagnosis_record</strong></div><span class="lineage-arrow">→</span><div class="lineage-node"><small>统一目录对象</small><strong>OBJ-HOSP-001 · 诊断记录</strong></div><span class="lineage-arrow">→</span><div class="lineage-node"><small>下游逻辑视图</small><strong>LV-PNEUMONIA-MONTHLY</strong></div></div>
${state.sourceChanged?'<div class="change-alert"><strong>已检测到源端变化：</strong>医保 claim_header.amount 的金额单位由元改为分；已保留旧目录版本并标记待评估下游影响。</div>':'<div class="change-alert success">目录快照稳定 · 血缘关系可追踪 · 主体 / 用途 / 期限约束已随对象登记。</div>'}
<div class="stage-actions"><button class="action-button primary" data-action="source-change">${state.sourceChanged?'再次注入源端变化':'模拟注入源端字段变化'}</button><button class="action-button" data-action="go-feedback">查看变化处置阶段</button></div>${platformLinks([['接入与资源目录','/static/platform/resources.html'],['元数据维护','/static/management/catalog.html']])}</div>`;}
function semanticStage(){return `<div class="stage-content">${header(2,'语义建模与关联','同一诊断在不同系统中使用不同编码；综合多项匹配证据生成统一业务概念。')}
<div class="semantic-grid"><div><p class="section-label">同一诊断 · 异构编码示例</p><div class="code-match-list">
<div class="code-match"><small>医院 HIS</small><code>J18.9</code><b>肺炎，未特指</b></div><div class="code-match"><small>医保 ICD</small><code>J18.900</code><b>肺炎编码扩展</b></div><div class="code-match"><small>科研平台</small><code>PNEU-03</code><b>入组诊断组别</b></div>
</div><ul class="evidence-list"><li><span>代码体系与编码关系</span><b>结构证据</b></li><li><span>字段名、别名与业务定义</span><b>文本证据</b></li><li><span>数据类型、来源域与上下文</span><b>结构 / 语境证据</b></li></ul></div>
<div class="concept-card"><small>统一业务概念 · 示例</small><strong>肺炎诊断</strong><code>diagnosis.pneumonia</code><div id="semantic-result" class="${state.semanticMatched?'access-result allow':'policy-empty'}">${state.semanticMatched?'3 个来源编码已关联到同一候选概念；映射依据可回看。':'运行匹配后展示多证据关联结果。'}</div></div></div>
<div class="stage-actions"><button class="action-button primary" data-action="semantic-match">${state.semanticMatched?'重新运行多证据匹配':'运行多证据匹配'}</button><button class="action-button" data-action="select-object" data-object="concept">查看统一概念对象</button></div>${platformLinks([['概念映射','/static/management/semantic.html'],['语义与血缘图谱','/static/platform/graph.html']])}</div>`;}
function virtualStage(){return `<div class="stage-content">${header(3,'虚拟化与逻辑集成','发布跨源逻辑视图，将委托合同中的主体、用途、期限和输出边界转成策略草案。')}
<div class="contract-layout"><div class="contract-box"><label for="contract-text">数据委托处理合同条款 · 可编辑的样例文本</label><textarea id="contract-text" spellcheck="false">${esc(state.contractText)}</textarea><div class="stage-actions"><button class="action-button primary" data-action="compile-policy">生成策略对象草案</button><button class="action-button" data-action="select-object" data-object="view">查看合约策略视图</button></div></div><div class="policy-preview"><h3>条款解析结果 · 本地规则演示</h3>${state.policy?`<pre>${esc(JSON.stringify(state.policy,null,2))}</pre>`:'<p class="policy-empty">生成后在此查看主体、用途、期限、输出粒度与限制。</p>'}</div></div>
${state.policyResult?`<div class="access-result ${state.policyResult.allowed?'allow':'block'}"><strong>${state.policyResult.allowed?'访问放行':'访问拦截'} · ${esc(state.policyResult.title)}</strong><br>${esc(state.policyResult.reason)}</div>`:''}
<div class="access-tests"><button class="action-button" data-action="access-allow">检查合规用途访问</button><button class="action-button" data-action="access-purpose">模拟跨用途访问</button><button class="action-button" data-action="access-expired">模拟超期访问</button></div>
<div class="path-choice"><label for="sensitivity">敏感等级路径示例</label><select id="sensitivity"><option value="aggregate" ${state.sensitivity==='aggregate'?'selected':''}>低敏汇总</option><option value="health" ${state.sensitivity==='health'?'selected':''}>健康信息</option><option value="identifier" ${state.sensitivity==='identifier'?'selected':''}>直接身份标识</option></select><button class="action-button" data-action="route">推荐执行路径</button>${state.routeResult?`<span class="path-result">${esc(state.routeResult.path)} · ${esc(state.routeResult.reason)}</span>`:''}</div>
<p class="report-disclosure">此页将样例条款解析成可视化策略草案；授权拦截和执行路径为本地演示，不签发真实授权或读取医疗数据。</p>${platformLinks([['逻辑视图与执行','/static/execution/index.html?tab=view'],['合作约定','/static/execution/contracts.html'],['访问授权','/static/execution/index.html?tab=grant']])}</div>`;}
function statusText(status){return({pending:'待执行',running:'执行中',waiting:'等待源数据',retrying:'自动重试',done:'已完成'})[status]||'待执行';}
function workflowRows(){return `<ol class="workflow-list" id="workflow-steps">${workflowDefinition.map((step,index)=>{const run=state.workflow.steps[index]||{};return `<li class="workflow-step"><span class="step-no">${String(index+1).padStart(2,'0')}</span><div><strong>${esc(step.name)}</strong><small>${esc(step.detail)}${run.attempt>1?` · 第 ${run.attempt} 次尝试`:''}</small></div><span class="workflow-state ${run.status==='done'?'done':run.status==='running'?'running':['waiting'].includes(run.status)?'waiting':run.status==='retrying'?'retry':''}">${statusText(run.status)}${run.duration?` · ${run.duration} ms`:''}</span><details><summary>步骤详情</summary>${esc(step.description)}；当前状态：${esc(statusText(run.status))}；耗时为演示值。</details></li>`;}).join('')}</ol>`;}
function orchestrationStage(){return `<div class="stage-content">${header(4,'编排与智能调度','需求确认后生成读取、关联、计算、脱敏交付步骤；未就绪时等待，失败时按原步骤重试。')}
<div class="workflow-head"><div><strong>本次回答共 4 个步骤</strong><br><small>${state.workflow.complete?`流程完成 · 演示总耗时 ${state.workflow.elapsed} ms`:state.workflow.running?'流程运行中 · 展示节点状态和演示耗时':'等待确认需求并生成执行计划'}</small></div><div class="workflow-controls"><select id="workflow-mode" aria-label="选择执行演示场景"><option value="normal" ${state.workflow.mode==='normal'?'selected':''}>正常完成</option><option value="wait" ${state.workflow.mode==='wait'?'selected':''}>源数据未就绪后恢复</option><option value="retry" ${state.workflow.mode==='retry'?'selected':''}>中间步骤失败后重试</option></select><button class="action-button primary" id="run-workflow" data-action="run-workflow" ${state.workflow.running?'disabled':''}>${state.workflow.running?'执行中…':'确认需求并编排'}</button></div></div>
${workflowRows()}<p class="report-disclosure">步骤状态和耗时在本页使用合成演示；实际执行、等待和重试记录请打开平台运行流程。</p>${platformLinks([['方案与执行流程','/static/execution/index.html?tab=workflow'],['运行证据','/static/execution/index.html?tab=run']])}</div>`;}
const feedbackLabels=['注入源端变更','检测语义偏差','定位下游视图','局部调整规则','保存适配草案'];
function feedbackStage(){const f=state.feedback;return `<div class="stage-content">${header(5,'多技术协同与运行反馈','从源端变化触发语义校验和血缘影响分析，再局部调整、版本回写，并查询全程证据。')}
<div class="feedback-flow" aria-label="变更反馈步骤">${feedbackLabels.map((label,index)=>{const done=index<f.phase||(f.complete&&index<5),current=f.running&&index===f.phase;return `<div class="feedback-node ${done?'done':''} ${current?'current':''}"><small>0${index+1} · ${done?'完成':current?'处理中':'待执行'}</small><strong>${esc(label)}</strong></div>`;}).join('')}</div>
<p class="report-disclosure">反馈链路使用本地合成事件演示。完成后可展开访问、拦截、处置与计量记录。</p>
<div class="stage-actions"><button class="action-button primary" data-action="run-feedback" ${f.running?'disabled':''}>${f.running?'正在处理…':f.complete?'重新演示变更处置':'注入变更并完成局部处置'}</button><button class="action-button" data-action="query-evidence">${state.evidenceVisible?'刷新证据链':'查询全程证据链'}</button><button class="action-button" data-action="select-object" data-object="evidence">查看证据链对象</button></div>
${state.evidenceVisible?renderEvidence():`<div class="evidence-empty">查询证据链后，访问放行、跨用途 / 超期拦截、变更处置和资源计量记录会汇总显示在这里。</div>`}
${platformLinks([['元数据事件','/static/platform/events.html'],['偏差校准','/static/management/reconciliation.html'],['运行证据','/static/execution/index.html?tab=run']])}</div>`;}
function renderEvidence(){if(!state.events.length)return '<div class="evidence-empty">目前没有本地演示事件。先运行访问校验、执行编排或变更处置，再查询证据链。</div>';return `<table class="evidence-table"><thead><tr><th>时间</th><th>类型</th><th>事件</th><th>结果</th></tr></thead><tbody>${state.events.map(event=>`<tr><td>${esc(event.time)}</td><td><span class="event-tag ${event.kind==='拦截'?'block':event.kind==='变更'?'change':event.kind==='放行'?'ok':''}">${esc(event.kind)}</span></td><td>${esc(event.detail)}</td><td>${esc(event.result)}</td></tr>`).join('')}</tbody></table>`;}
function stageHtml(){return({sources:sourcesStage,semantics:semanticStage,virtual:virtualStage,orchestration:orchestrationStage,feedback:feedbackStage}[state.stage]||sourcesStage)();}
function renderStage(){refreshGuide();const panel=$('#stage-panel');if(!panel)return;panel.innerHTML=stageHtml()+caseContent(state.stage,state)+`<div class="case-navigation"><button class="action-button" data-action="case-prev" ${state.stage==='sources'?'disabled':''}>上一步</button><span>阶段 ${stages.findIndex(s=>s.id===state.stage)+1} / 5</span><button class="action-button primary" data-action="case-next" ${state.stage==='feedback'?'disabled':''}>下一步</button></div>`;panel.onclick=handleAction;panel.oninput=event=>{if(event.target.id==='contract-text'){state.contractText=event.target.value;state.policy=null;state.policyResult=null;state.accessChecks={};state.workflow.complete=false;state.completedModes.clear();state.feedback.complete=false;refreshGuide();const preview=$('.policy-preview');if(preview)preview.innerHTML='<h3>合同已修改</h3><p>请重新生成策略并检查合规访问。</p>';}};panel.onchange=event=>{if(event.target.id==='workflow-mode')state.workflow.mode=event.target.value;if(event.target.id==='sensitivity')state.sensitivity=event.target.value;};renderObjectRail();}
function objectDetails(item){const related={data:'多源接入',concept:'语义关联',view:'逻辑集成与合约',plan:'编排与调度',evidence:'运行反馈与证据'}[item.id];return `<div class="object-detail"><span class="detail-kind">${esc(item.kind)}</span><h3>${esc(item.name)}</h3><code>${esc(item.ref)}</code><p>${esc(item.summary)}</p><div class="object-props">${item.props().map(([label,value])=>`<div class="object-prop"><span>${esc(label)}</span><b>${esc(value)}</b></div>`).join('')}</div><div class="object-props"><div class="object-prop"><span>关联阶段</span><b>${esc(related)}</b></div><div class="object-prop"><span>对象版本</span><b>${item.id==='data'&&state.sourceChanged?'v1.5':'v1.4'}</b></div></div></div>`;}
function renderObjectRail(){const rail=$('#object-rail');if(!rail)return;rail.innerHTML=`<div class="rail-heading"><strong>贯穿全程的五类中间对象</strong><small>在五段演示间持续保留 · 点击查看对象详情</small></div><div class="object-list">${objects.map((item,index)=>`<button class="object-tab ${state.activeObject===item.id?'active':''}" type="button" data-object="${item.id}" aria-pressed="${state.activeObject===item.id}"><span class="object-number">0${index+1}</span><span><strong>${esc(item.kind)}</strong><small>${esc(item.ref)}</small></span><em>查看</em></button>`).join('')}</div>${objectDetails(objects.find(item=>item.id===state.activeObject)||objects[0])}<div class="rail-note">五类对象名称按当前报告内容拟定，可按课题报告中的正式术语调整。</div>`;rail.onclick=event=>{const button=event.target.closest('[data-object]');if(!button)return;state.activeObject=button.dataset.object;renderObjectRail();};}
function parseContract(text){
 const pick=label=>{const match=text.match(new RegExp(`(?:${label})\\s*[：:]\\s*([^；;\\n]+)`));return match?.[1]?.trim()||'';};
 const date=text.match(/(?:有效期至|期限至|到期日)\s*[：:]\s*(\d{4}-\d{2}-\d{2})/);
 const subject=pick('受托方|使用主体|主体');const purpose=pick('使用用途|用途');const output=pick('输出粒度|输出范围');
 if(!subject||!purpose||!date)return null;
 return {subject,purpose,expires_at:date[1]+'T23:59:59+08:00',output_granularity:output||'待确认',restrictions:['禁止直接识别','禁止再识别','禁止跨用途使用'].filter(item=>text.includes(item)),source:'本页合同条款规则解析演示'};
}
function testAccess(kind){
 if(!state.policy){state.policyResult={allowed:false,title:'尚无策略对象',reason:'请先生成策略对象草案，再执行放行 / 拦截演示。'};return;}
 const purpose=kind==='purpose'?'商业营销':state.policy.purpose;const expires=kind==='expired'?'2025-01-01':state.policy.expires_at.slice(0,10);const now=new Date();const today=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;const expired=expires<today;const purposeMismatch=purpose!==state.policy.purpose;
 const allowed=!expired&&!purposeMismatch;const title=kind==='allow'?'合规访问':kind==='purpose'?'跨用途访问':'超期访问';
 const reason=allowed?`主体 ${state.policy.subject}、用途和期限均符合策略；示例请求可进入授权执行链路。`:expired?'访问期限已过，策略校验拦截请求。':'请求用途与合同约定不一致，策略校验拦截请求。';
 state.policyResult={allowed,title,reason};state.accessChecks[kind]=kind==='allow'?allowed:!allowed;logEvent(allowed?'放行':'拦截',`${title} · ${purpose}`,allowed?'策略条件匹配':'主体 / 用途 / 期限校验失败');
}
function routeFor(value){return value==='aggregate'?{path:'联邦查询（源端聚合）',reason:'统计结果跨源整合，明细留在来源侧。'}:value==='health'?{path:'隔离沙箱（受控计算）',reason:'健康信息在隔离环境内按策略计算。'}:{path:'源端联邦计算（禁止明细汇出）',reason:'直接标识保留在源端，仅返回允许的计算结果。'};}
function handleAction(event){
 const button=event.target.closest('[data-action]');if(!button)return;
 const action=button.dataset.action;
 if(action==='register-case'){state.catalogRegistered=true;logEvent('登记','医院、医保、科研3个合成数据对象，72条记录','约束与目录快照已登记到本页');renderStage();return;}
 if(action==='case-prev'||action==='case-next'){const index=stages.findIndex(s=>s.id===state.stage);state.stage=stages[Math.max(0,Math.min(4,index+(action==='case-next'?1:-1)))].id;renderTopicTwo();return;}
 if(action==='source-change'){
  state.sourceChanged=true;logEvent('变更','医保 claim_header.amount 检测到金额单位变化（元→分）','新目录快照已标记，旧版本保留');renderTopicTwo();return;
 }
 if(action==='go-feedback'){state.stage='feedback';renderTopicTwo();return;}
 if(action==='semantic-match'){if(!state.catalogRegistered){alert('请先登记本案例目录快照。');return;}state.semanticMatched=true;logEvent('语义','HIS / 医保 / 科研诊断编码匹配到 diagnosis.pneumonia','多证据候选映射已生成');renderStage();return;}
 if(action==='select-object'){state.activeObject=button.dataset.object;renderObjectRail();return;}
 if(action==='compile-policy'){
  if(!state.semanticMatched){alert('请先运行语义匹配，再编制合同策略。');return;}
  state.contractText=$('#contract-text')?.value||state.contractText;const policy=parseContract(state.contractText);
  if(!policy){state.policyResult={allowed:false,title:'条款信息不足',reason:'请补齐受托主体、使用用途和有效期至（YYYY-MM-DD），再生成策略草案。'};renderStage();return;}
  state.policy=policy;state.policyResult=null;state.accessChecks={};state.completedModes.clear();state.workflow.complete=false;state.feedback.complete=false;logEvent('策略','合同条款转换为主体 / 用途 / 期限 / 输出边界策略草案','本地演示草案');renderStage();return;
 }
 if(['access-allow','access-purpose','access-expired'].includes(action)){
  state.contractText=$('#contract-text')?.value||state.contractText;testAccess(action==='access-allow'?'allow':action==='access-purpose'?'purpose':'expired');renderStage();return;
 }
 if(action==='route'){state.sensitivity=$('#sensitivity')?.value||state.sensitivity;state.routeResult=routeFor(state.sensitivity);state.routeChecks.add(state.sensitivity);renderStage();return;}
 if(action==='run-workflow'){if(state.workflow.running||state.feedback.running)return;if(!state.semanticMatched||!state.policy||!state.policyResult?.allowed){alert('请先在语义关联阶段运行匹配，并在逻辑集成阶段生成策略、通过合规访问检查。');return;}const mode=$('#workflow-mode')?.value||'normal';startWorkflow(mode);return;}
 if(action==='run-feedback'){if(state.workflow.running)return;if(!state.workflow.complete){alert('请先在编排与调度阶段完成一次执行，再演示源端变更与结果复核。');return;}startFeedback();return;}
 if(action==='query-evidence'){state.evidenceVisible=true;logEvent('查询','读取本页演示证据链','已汇总访问、拦截、处置与计量记录');renderStage();return;}
}
function updateWorkflow(){
 const list=$('#workflow-steps');if(!list)return;
 list.innerHTML=workflowDefinition.map((step,index)=>{const run=state.workflow.steps[index]||{};return `<li class="workflow-step"><span class="step-no">${String(index+1).padStart(2,'0')}</span><div><strong>${esc(step.name)}</strong><small>${esc(step.detail)}${run.attempt>1?` · 第 ${run.attempt} 次尝试`:''}</small></div><span class="workflow-state ${run.status==='done'?'done':run.status==='running'?'running':run.status==='waiting'?'waiting':run.status==='retrying'?'retry':''}">${statusText(run.status)}${run.duration?` · ${run.duration} ms`:''}</span><details><summary>步骤详情</summary>${esc(step.description)}；当前状态：${esc(statusText(run.status))}；耗时为演示值。</details></li>`;}).join('');
 const runButton=$('#run-workflow');if(runButton){runButton.disabled=state.workflow.running;runButton.textContent=state.workflow.running?'执行中…':state.workflow.complete?'重新运行演示':'确认需求并编排';}
 const summary=$('.workflow-head small');if(summary)summary.textContent=state.workflow.complete?`流程完成 · 演示总耗时 ${state.workflow.elapsed} ms`:state.workflow.running?'流程运行中 · 展示节点状态和演示耗时':'等待确认需求并生成执行计划';
 renderObjectRail();
}
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function startWorkflow(mode){
 if(state.workflow.running)return;
 state.workflow={running:true,complete:false,mode,elapsed:0,steps:workflowDefinition.map(()=>({status:'pending',duration:null,attempt:1}))};
 logEvent('编排','需求已确认，生成读取 → 关联 → 计算 → 脱敏交付计划','共 4 个步骤');updateWorkflow();
 const bases=[360,520,470,330];
 for(let index=0;index<workflowDefinition.length;index++){
  const step=state.workflow.steps[index];
  if(mode==='wait'&&index===1){step.status='waiting';updateWorkflow();logEvent('等待','医保源尚未就绪，任务进入等待状态','自动等待源端恢复');await delay(1000);}
  if(mode==='retry'&&index===2){step.status='retrying';step.attempt=1;updateWorkflow();logEvent('重试','第 3 步首次执行失败','自动按原方案重试');await delay(650);step.attempt=2;}
  step.status='running';updateWorkflow();await delay(520);
  step.status='done';step.duration=bases[index]+(mode==='wait'&&index===1?1000:mode==='retry'&&index===2?650:0);state.workflow.elapsed+=step.duration;updateWorkflow();
 }
 state.workflow.running=false;state.workflow.complete=true;state.completedModes.add(mode);refreshGuide();logEvent('计量',`队列统计执行完成 · ${state.workflow.elapsed} ms（演示）`,'步骤状态与耗时已记录');updateWorkflow();if(state.topic==='2'&&state.stage==='orchestration')renderStage();
}
async function startFeedback(){
 if(state.feedback.running)return;
 state.feedback={running:true,phase:0,complete:false};
 const phases=[
  ['变更','医保金额单位由元改为分，数值放大100倍','旧版本保留，标记单位变化'],
  ['偏差','金额量纲与旧费用表达式不一致','费用汇总可能放大100倍'],
  ['血缘','定位 LV-PNEUMONIA-MONTHLY 及其下游任务','仅圈定受影响对象'],
  ['处置','金额表达式增加除以100的单位归一','患者计数与诊断映射不变'],
  ['回写','保存费用单位适配草案v2','待业务复核后发布']
 ];
 for(let index=0;index<phases.length;index++){
  state.feedback.phase=index;state.feedback.running=true;
  if(index===0)state.sourceChanged=true;
  if(index===1)state.semanticMatched=true;
  renderFeedbackOnly();logEvent(...phases[index]);await delay(560);
 }
 state.feedback={running:false,phase:5,complete:true};refreshGuide();renderFeedbackOnly();
 logEvent('计量','变更处置仅重算受影响逻辑视图','局部处置完成');renderObjectRail();
}
function renderFeedbackOnly(){if(state.topic==='2'&&state.stage==='feedback')renderStage();else renderObjectRail();}
function init(){
 document.querySelectorAll('.topic-tab').forEach(tab=>{
  tab.addEventListener('click',()=>setTopic(tab.dataset.topic));
  tab.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();const tabs=[...document.querySelectorAll('.topic-tab')];const index=tabs.indexOf(tab);const next=(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].focus();tabs[next].click();});
 });
 $('#presentation-mode').addEventListener('click',async event=>{
  if(!document.fullscreenElement&&document.documentElement.requestFullscreen){await document.documentElement.requestFullscreen();event.currentTarget.textContent='退出演示模式';}
  else if(document.exitFullscreen){await document.exitFullscreen();event.currentTarget.textContent='进入演示模式';}
 });
 document.addEventListener('fullscreenchange',()=>{$('#presentation-mode').textContent=document.fullscreenElement?'退出演示模式':'进入演示模式';});
 setTopic(new URLSearchParams(location.search).get('topic')||'2');
}
init();
