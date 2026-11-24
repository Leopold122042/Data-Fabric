const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const endpoint='/api/report/topic3';
const stages=[['request','需求与规格'],['compose','服务编排'],['policy','合约与授权'],['execute','执行与复测'],['delivery','交付与证据']];
const scenarios=[
 {id:'object',title:'院内对象级服务',subtitle:'A医院医生 · 对象 S001 · T0 风险辅助评估',mode:'单个对象',tone:'teal'},
 {id:'group',title:'跨机构群体统计',subtitle:'B机构研究员 · C01 队列 · 仅返回风险分层汇总',mode:'人群统计',tone:'blue'},
 {id:'dataset',title:'科研固定版本数据集',subtitle:'P-DEMO-01 · 40–65岁 · T0前90天特征与12个月结局',mode:'数据集',tone:'gold'}
];
const fields=[
 ['age_years','年龄（岁）'],['bmi','BMI（kg/m²）'],['fasting_glucose','空腹血糖（mmol/L）'],
 ['hba1c','糖化血红蛋白（%）'],['family_history','家族史'],['followup_outcome','随访结局']
];
const stageForState=value=>({request_draft:0,spec_confirmed:1,supply_gap:1,supply_ready:1,plan_draft:1,plan_confirmed:2,authorized:3,run_success:4,run_failed:3,quality_rule_updated:1,authorization_revoked:2,delivered:4,improved:4}[value]??0);
const stateLabel=value=>({request_draft:'待核对需求',spec_confirmed:'规格已确认',supply_gap:'检测到服务缺口',supply_ready:'服务已补充',plan_draft:'方案待确认',plan_confirmed:'方案已确认',authorized:'已授权',run_success:'运行成功',run_failed:'运行失败',quality_rule_updated:'质量服务已改进',authorization_revoked:'授权已撤销',delivered:'已交付',improved:'同范围复测完成'}[value]||value||'待开始');
const fieldNames=Object.fromEntries(fields);

export function renderTopicThree(host){
 const ui={cases:[],current:null,stage:0,providers:[],busy:false,error:'',notice:'',result:null,gaps:[]};
 const request=async(path,options={})=>{
  const url=path.startsWith('/api/')?path:endpoint+path;
  const response=await fetch(url,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})},cache:'no-store'});
  const type=response.headers.get('content-type')||'';
  let value;
  if(type.includes('application/json'))value=await response.json();
  else value=await response.text();
  if(!response.ok)throw new Error(value?.error||value?.detail||String(value)||`HTTP ${response.status}`);
  return value;
 };
 const state=()=>ui.current;
 const sc=()=>scenarios.find(x=>x.id===state()?.case?.scenario)||scenarios[0];
 const redraw=()=>{host.innerHTML=page();bind();};
 const refresh=async(id=state()?.case?.id)=>{
  const list=await request('/cases');ui.cases=list.items||[];
  if(id){ui.current=await request(`/cases/${encodeURIComponent(id)}`);ui.stage=stageForState(ui.current.case.state);}
  else ui.current=null;
 };
 const reportError=error=>{ui.error=error?.message||String(error);ui.busy=false;host.removeAttribute('aria-busy');const banner=document.createElement('div');banner.className='t3-error';banner.setAttribute('role','alert');banner.textContent=ui.error;host.querySelector('.t3-error')?.remove();host.prepend(banner);host.querySelector('.t3-busy')?.remove();};
 const run=async(work,success)=>{
  if(ui.busy)return;ui.busy=true;ui.error='';ui.notice='';host.setAttribute('aria-busy','true');host.querySelector('.t3-error')?.remove();
  if(!host.querySelector('.t3-busy')){const busy=document.createElement('div');busy.className='t3-busy';busy.innerHTML='<i></i>正在保存并刷新服务端记录…';host.prepend(busy);}
  try{await work();ui.busy=false;host.removeAttribute('aria-busy');if(success&&!ui.notice)ui.notice=success;redraw();}
  catch(error){reportError(error);}
 };
 const postAction=async(action,payload={})=>{
  const id=state()?.case?.id;if(!id)throw new Error('请先创建演示任务');
  const value=await request(`/cases/${encodeURIComponent(id)}/actions/${action}`,{method:'POST',body:JSON.stringify(payload)});
  if(value?.state?.case){ui.current=value.state;ui.gaps=value.gaps||[];}
  else if(value?.case){ui.current=value;ui.gaps=[];}
  return value;
 };
 const scenarioCards=()=>`<div class="t3-scenarios">${scenarios.map(item=>`<button type="button" class="t3-scenario ${item.tone}" data-create="${item.id}" ${ui.busy?'disabled':''}><span class="t3-scenario-mode">${item.mode}</span><strong>${item.title}</strong><small>${item.subtitle}</small><b>开始此流程 <span>↗</span></b></button>`).join('')}</div>`;
 const casePicker=()=>`<div class="t3-case-picker"><label for="t3-case-select">已保存的演示任务</label><select id="t3-case-select"><option value="">选择历史任务</option>${ui.cases.map(item=>`<option value="${esc(item.id)}" ${item.id===state()?.case?.id?'selected':''}>${esc(scenarios.find(s=>s.id===item.scenario)?.title||item.scenario)} · ${esc(stateLabel(item.state))} · ${esc(item.id.slice(-6))}</option>`).join('')}</select><button type="button" class="t3-button quiet" data-refresh ${ui.busy?'disabled':''}>刷新记录</button></div>`;
 const progress=()=>`<div class="t3-progress" role="tablist" aria-label="课题三五阶段流程">${stages.map(([id,label],index)=>`<button type="button" role="tab" aria-selected="${ui.stage===index}" class="t3-progress-step ${ui.stage===index?'active':''} ${ui.current&&index<stageForState(ui.current.case.state)?'complete':''}" data-stage="${index}"><i>${index<stageForState(ui.current?.case?.state)?'✓':String(index+1).padStart(2,'0')}</i><span>${label}</span></button>`).join('')}</div>`;
 const innovation=()=>`<div class="t3-innovation-strip"><span><b>01</b> 需求澄清与约束固化</span><span><b>02</b> 服务图按粒度重组</span><span><b>03</b> 合约策略分节点执行</span><span><b>04</b> 失败改进与同范围复测</span></div>`;
 const objects=()=>{
  const data=state();const c=data?.case;const p=data?.plan;const run=data?.latest_run;const delivery=data?.deliveries?.at(-1);const entries=[
   ['01','任务规格',c?.spec_id||'尚未创建',data?.spec?`v${data.spec.version} · ${stateLabel(c.state)}`:'意图与限制'],
   ['02','服务组合图',c?.plan_id||'尚未生成',p?`v${p.version} · ${p.nodes.length} 个节点`:'依赖、参数、选择理由'],
   ['03','合约策略',c?.contract_id||'尚未固化',data?.contract?`v${data.contract.version} · ${data.contract.circulation_mode}`:'主体、用途、范围、期限'],
   ['04','运行证据',run?.id||'等待执行',run?`${run.status} · ${run.latency_ms??'—'} ms`:'节点状态与耗时'],
   ['05','交付与数据集',delivery?.id||c?.gap_service_id||'等待交付',data?.dataset?`${data.dataset.dataset_version} · ${data.dataset.record_count} 条`:'血缘、质量、接收方式']
  ];
  return `<aside class="t3-object-rail"><div class="t3-rail-head"><small>贯穿全程的中间对象</small><strong>任务 · 方案 · 策略 · 证据</strong></div>${entries.map(([n,title,ref,detail])=>`<article><i>${n}</i><div><b>${title}</b><small>${esc(ref)}</small><em>${esc(detail)}</em></div></article>`).join('')}<p>数据行按当前账户等级投影；本页所有样例均为虚构合成记录。</p></aside>`;
 };
 const empty=()=>`<section class="t3-start"><div class="t3-start-title"><span>PROJECT 03 · SERVICE SUPPLY</span><h2>从需求到可信交付，走完一条可追溯服务链</h2><p>先选择一个场景。每次点击都会在平台后端创建并保存演示任务，需求规格、服务图、策略决定、执行节点和交付记录都可回看。</p></div>${scenarioCards()}</section>`;
 const commonHeader=()=>`<div class="t3-current-head"><div><span class="t3-kicker">${esc(sc().mode)} · ${esc(state().case.id)}</span><h2>${esc(sc().title)}</h2><p>${esc(sc().subtitle)}</p></div><span class="t3-state-pill">${esc(stateLabel(state().case.state))}</span></div>`;
 const input=(id,label,value,type='text',extra='')=>`<label class="t3-field" for="${id}"><span>${label}</span><input id="${id}" type="${type}" value="${esc(value??'')}" ${extra}></label>`;
 const specForm=()=>{
  const spec=state().spec||state().case.draft||{};const dataset=state().case.scenario==='dataset';
  const localTime=spec.expires_at?new Date(new Date(spec.expires_at).getTime()-new Date(spec.expires_at).getTimezoneOffset()*60000).toISOString().slice(0,16):'';
  return `<div class="t3-form-grid">${input('t3-purpose','业务用途',spec.purpose)}${input('t3-scope','数据范围',spec.scope_id,'text','readonly')}<label class="t3-field"><span>跨方流通模式</span><select id="t3-circulation"><option value="intra_hospital" ${spec.circulation_mode==='intra_hospital'?'selected':''}>院内共享</option><option value="cross_institution" ${spec.circulation_mode==='cross_institution'?'selected':''}>跨机构协作</option><option value="source_domain_compute" ${spec.circulation_mode==='source_domain_compute'?'selected':''}>源域内计算</option></select></label><label class="t3-field"><span>交付方式（独立于流通模式）</span><select id="t3-delivery"><option value="controlled_access" ${spec.delivery_method==='controlled_access'?'selected':''}>受控访问</option><option value="api_reference" ${spec.delivery_method==='api_reference'?'selected':''}>授权接口引用</option><option value="approved_file" ${spec.delivery_method==='approved_file'?'selected':''}>单独批准的文件交付</option></select></label>${input('t3-feature-window','T0前特征窗（天）',spec.feature_window_days??90,'number','min="1" max="365"')}${input('t3-outcome-window','T0后结局窗（天）',spec.outcome_window_days??365,'number','min="1" max="1095"')}${dataset?input('t3-quality','质量完整率下限',Math.max(.95,Number(spec.quality_min_complete??.95)),'number','min="0.95" max="1" step="0.01"'):''}${input('t3-min-group','最小分组人数',Math.max(5,Number(spec.min_group_size??5)),'number','min="5" max="100"')}${input('t3-expires','授权有效期',localTime,'datetime-local')}</div><div class="t3-field-picker"><b>申请字段</b><div>${fields.map(([id,label])=>`<label><input type="checkbox" name="t3-field" value="${id}" ${(spec.fields||[]).includes(id)?'checked':''} ${id==='followup_outcome'&&!dataset?'disabled':''}><span>${label}</span></label>`).join('')}</div></div><p class="t3-note">范围被固定为当前案例的演示对象。所有规格由人核对并确认；AI 仅生成草案，不代替授权、方案确认或执行。</p>`;
 };
 const requestPanel=()=>{
  const draft=state().case.draft||{};const provider=ui.providers.find(p=>p.configured);
  return `<section class="t3-section"><div class="t3-section-title"><span>01 / REQUEST</span><h3>需求澄清与约束固化</h3><p>固定的场景请求会形成初稿；可选已配置的模型帮助抽取用途、对象、字段、时间窗和质量门槛。</p></div><label class="t3-field t3-full" for="t3-request"><span>自然语言需求</span><textarea id="t3-request" rows="3">${esc(state().case.request_text)}</textarea></label><div class="t3-toolbar"><label class="t3-provider">AI 草案服务<select id="t3-provider">${ui.providers.map(p=>`<option value="${esc(p.id)}" ${p.default?'selected':''} ${!p.configured?'disabled':''}>${esc(p.name)} · ${esc(p.model||'未配置')}${p.configured?'':'（不可用）'}</option>`).join('')||'<option value="">没有已配置模型</option>'}</select></label><button type="button" class="t3-button secondary" data-action="draft" ${ui.busy||!provider?'disabled':''}>AI 解析为待核对草案</button>${draft.ai_provider?`<span class="t3-ai-source">上次草案：${esc(draft.ai_provider)} / ${esc(draft.ai_model)}</span>`:''}</div><h4 class="t3-subhead">核对结构化任务规格</h4>${specForm()}<div class="t3-actions"><button type="button" class="t3-button primary" data-action="confirm_spec" ${ui.busy||state().case.spec_confirmed?'disabled':''}>${state().case.spec_confirmed?'规格已确认':'确认任务规格'}</button></div></section>`;
 };
 const graphPanel=()=>{
  const data=state();const isDataset=data.case.scenario==='dataset';const graph=data.plan;
  return `<section class="t3-section"><div class="t3-section-title"><span>02 / COMPOSITION</span><h3>服务选择与执行图生成</h3><p>同一组基础服务会按单对象评估、人群统计或科研数据集生成不同节点、参数和交付粒度。</p></div>${data.case.gap_service_id?`<div class="t3-alert warn"><b>检测到服务缺口</b><span>缺少项目定义的 T0 后12个月随访结局服务；数据集方案暂不生成。</span><button type="button" class="t3-button secondary" data-action="supply_service" ${ui.busy?'disabled':''}>补充并发布合成随访服务</button></div>`:''}${isDataset&&data.case.gap_service_id?'':`<div class="t3-actions"><button type="button" class="t3-button primary" data-action="generate_plan" ${ui.busy||!data.case.spec_confirmed?'disabled':''}>${graph?'重新生成方案图':'生成服务方案图'}</button>${graph?.status==='draft'?`<button type="button" class="t3-button secondary" data-action="confirm_plan" ${ui.busy?'disabled':''}>确认方案 v${graph.version}</button>`:''}</div>`}${graph?`<div class="t3-plan-summary"><div><b>PLAN v${graph.version}</b><span>${graph.nodes.length}个真实后端服务节点 · 图摘要 ${esc((graph.graph_digest||'').slice(0,12))}</span></div><span class="t3-state-pill">${graph.status==='confirmed'?'已确认':'待确认'}</span></div><div class="t3-node-list">${graph.nodes.map((node,index)=>`<article><i>${String(index+1).padStart(2,'0')}</i><div><b>${esc(node.service_name)}</b><span>${esc(node.service_id)} · v${node.service_version}</span><small>依赖：${node.depends_on.length?node.depends_on.map(esc).join(' → '):'起始节点'}</small><code>${esc(JSON.stringify(node.parameters))}</code></div></article>`).join('')}</div><div class="t3-reasons"><b>系統选择理由</b>${(graph.selection_reasons||[]).map(x=>`<p>${esc(x)}</p>`).join('')}</div>`:`<div class="t3-empty-state">确认任务规格后生成可核对的服务图。数据集场景会先检查随访服务供给。</div>`}<div class="t3-mode-pair"><div><small>跨方流通</small><b>${esc(data.spec?.circulation_mode||'待规格确认')}</b></div><div><small>结果交付</small><b>${esc(data.spec?.delivery_method||'待规格确认')}</b></div></div></section>`;
 };
 const policyPanel=()=>{
  const data=state();const decisions=[...(data.decisions||[])].reverse();
  return `<section class="t3-section"><div class="t3-section-title"><span>03 / CONTRACT POLICY</span><h3>合约策略与授权决定</h3><p>以已确认任务规格生成策略对象。院内/跨机构/源域计算是流通模式，受控访问/API/获准文件是交付方式，两者独立校验。</p></div>${data.contract?`<div class="t3-contract"><div><small>策略对象 / v${data.contract.version}</small><b>${esc(data.contract.principal)} · ${esc(data.contract.purpose)}</b><span>对象 ${esc(data.contract.scope_id)} · 截止 ${esc(data.contract.expires_at)}</span></div><div><small>流通 / 交付</small><b>${esc(data.contract.circulation_mode)} / ${(data.contract.delivery_methods||[]).map(esc).join('、')}</b><span>约束：${(data.contract.restrictions||[]).map(esc).join(' · ')}</span></div></div>`:'<div class="t3-empty-state">先确认任务规格，再生成策略对象。</div>'}<div class="t3-actions"><button type="button" class="t3-button primary" data-action="check_access" data-kind="allow" ${ui.busy||!data.plan||data.plan.status!=='confirmed'?'disabled':''}>核验本次意图并授权</button><button type="button" class="t3-button danger" data-action="check_access" data-kind="purpose" ${ui.busy||!data.plan?'disabled':''}>演示跨用途拦截</button><button type="button" class="t3-button danger" data-action="check_access" data-kind="expired" ${ui.busy||!data.plan?'disabled':''}>演示超期拦截</button><button type="button" class="t3-button danger" data-action="check_access" data-kind="detail" ${ui.busy||!data.plan||data.case.scenario==='object'?'disabled':''}>演示明细粒度拦截</button></div>${data.grant?`<div class="t3-alert ${data.grant.status==='active'?'success':'error'}"><b>授权状态：${esc(data.grant.status)}</b><span>${esc(data.grant.subject)} · ${esc(data.grant.purpose)} · 到期 ${esc(data.grant.expires_at)}</span><button type="button" class="t3-button quiet" data-action="revoke" ${ui.busy||data.grant.status!=='active'?'disabled':''}>撤销授权</button></div>`:''}<div class="t3-decision-list">${decisions.length?decisions.map(d=>`<article class="${d.allowed?'allow':'deny'}"><i>${d.allowed?'✓':'×'}</i><div><b>${esc(d.kind==='allow'?'合规放行':d.kind==='purpose'?'跨用途拦截':d.kind==='expired'?'超期拦截':d.kind==='detail'?'输出粒度拦截':'决定')}</b><span>${esc(d.reason)}</span></div><small>${esc(d.created_at)}</small></article>`).join(''):'访问检查和授权决定会进入可查询证据链。'}</div></section>`;
 };
 const executePanel=()=>{
  const data=state();const latest=data.latest_run;const dataset=data.case.scenario==='dataset';const hasFailure=(data.runs||[]).some(r=>r.error_code==='quality_threshold');const hasSuccess=(data.runs||[]).some(r=>r.status==='success');
  const nodes=latest?.nodes||{};
  return `<section class="t3-section"><div class="t3-section-title"><span>04 / EXECUTION & IMPROVEMENT</span><h3>按确认图执行，并留下节点状态和耗时</h3><p>每次执行前重新核验方案版本和授权；质量失败保留为基线，修复后按同一任务范围复测。</p></div><div class="t3-run-banner"><div><small>本次方案</small><b>${data.plan?`v${data.plan.version} · ${data.plan.nodes.length} 个节点`:'尚未确认'}</b></div><div><small>授权</small><b>${esc(data.grant?.status||'未授权')}</b></div><div><small>运行</small><b>${latest?esc(latest.status):'等待执行'}</b></div></div>${dataset&&!hasFailure?`<div class="t3-alert warn"><b>质量门槛演示</b><span>注入两个合成 BMI 缺失值，验证系统在缺失率超过5%时拒绝生成数据集。</span><button type="button" class="t3-button secondary" data-action="run" data-failure="quality_fail" ${ui.busy||!data.grant||data.grant.status!=='active'||data.plan?.status!=='confirmed'?'disabled':''}>运行并注入质量失败</button></div>`:''}${dataset&&hasFailure&&!data.case.repair_strategy?`<div class="t3-alert error"><b>验收未通过 · 缺失率 ${latest?.missing_rate==null?'—':(latest.missing_rate*100).toFixed(1)+'%'}</b><span>保留失败基线，不降低5%阈值；请补充经核对的来源并升级质量服务。</span><label class="t3-inline-input">处置说明<input id="t3-repair-note" value="合并经核对的合成补充来源；质量上限保持5%"></label><button type="button" class="t3-button secondary" data-action="repair_quality" ${ui.busy?'disabled':''}>登记服务修复</button></div>`:''}${dataset&&data.case.repair_strategy?`<div class="t3-alert success"><b>质量服务已更新</b><span>重新生成并确认新服务图，再重新核验授权后进行同范围复测。</span><button type="button" class="t3-button secondary" data-action="generate_plan" ${ui.busy?'disabled':''}>生成修订方案</button>${data.plan?.status==='draft'?`<button type="button" class="t3-button primary" data-action="confirm_plan" ${ui.busy?'disabled':''}>确认新方案 v${data.plan.version}</button>`:''}<button type="button" class="t3-button quiet" data-goto-stage="2">重新授权</button></div>`:''}<div class="t3-actions">${!dataset?`<button type="button" class="t3-button primary" data-action="run" ${ui.busy||!data.grant||data.grant.status!=='active'||data.plan?.status!=='confirmed'?'disabled':''}>确认执行当前方案</button>`:''}${dataset&&hasFailure&&data.case.repair_strategy&&data.plan?.status==='confirmed'&&(!data.grant||data.grant.status!=='active')?`<button type="button" class="t3-button primary" data-goto-stage="2">进入授权核验</button>`:''}${dataset&&hasFailure&&data.case.repair_strategy&&data.plan?.status==='confirmed'&&data.grant?.status==='active'?`<button type="button" class="t3-button primary" data-action="run" ${ui.busy?'disabled':''}>同范围质量复测</button>`:''}${dataset&&hasFailure&&hasSuccess&&!data.improvement?`<button type="button" class="t3-button secondary" data-action="compare" ${ui.busy?'disabled':''}>保存失败基线与复测对比</button>`:''}</div>${latest?`<div class="t3-plan-summary"><div><b>运行 ${esc(latest.id)}</b><span>${latest.status==='success'?'成功':latest.status==='failed'?'失败':'受阻'} · ${latest.latency_ms??'—'} ms · 任务范围 ${esc(latest.task_scope)}</span></div><span class="t3-state-pill">${latest.quality_passed?'质量通过':latest.quality_passed===false?'质量未通过':'已完成'}</span></div><div class="t3-node-list run-nodes">${Object.entries(nodes).map(([id,node])=>`<article><i class="${node.status==='success'?'ok':node.status==='failed'?'bad':''}">${node.status==='success'?'✓':node.status==='failed'?'×':'·'}</i><div><b>${esc(node.service_id||id)}</b><span>${esc(node.status)} · v${esc(node.service_version||'—')} · ${esc(node.duration_ms??'—')} ms</span><small>${esc(node.detail||`依赖参数：${JSON.stringify(node.parameters||{})}`)}</small></div></article>`).join('')}</div>`:'<div class="t3-empty-state">执行详情会显示本次服务图共几个步骤、每个节点的状态和实际运行耗时。</div>'}${data.improvement?`<div class="t3-improvement"><b>同范围复测记录 · ${esc(data.improvement.id)}</b><span>基线成功率 ${(data.improvement.baseline.success_rate*100).toFixed(0)}% → 复测 ${(data.improvement.retest.success_rate*100).toFixed(0)}%；缺失率 ${data.improvement.baseline.missing_rate==null?'—':(data.improvement.baseline.missing_rate*100).toFixed(1)+'%'} → ${data.improvement.retest.missing_rate==null?'—':(data.improvement.retest.missing_rate*100).toFixed(1)+'%'}</span><small>${esc(data.improvement.change_note)}</small></div>`:''}</section>`;
 };
 const deliveryPanel=()=>{
  const data=state();const latestDelivery=data.deliveries?.at(-1);const result=ui.result;
  return `<section class="t3-section"><div class="t3-section-title"><span>05 / DELIVERY & EVIDENCE</span><h3>交付复核、服务结果与证据链</h3><p>授权接口和受控访问直接按权限读取结果；文件出域需单独记录批准人、接收方和依据。</p></div><div class="t3-delivery-form"><label class="t3-field"><span>本次交付方式</span><select id="t3-delivery-method"><option value="controlled_access" ${data.spec?.delivery_method==='controlled_access'?'selected':''}>受控访问</option><option value="api_reference" ${data.spec?.delivery_method==='api_reference'?'selected':''}>授权接口引用</option><option value="approved_file" ${data.spec?.delivery_method==='approved_file'?'selected':''}>获准文件交付</option></select></label>${data.case.file_delivery_approved?`<span class="t3-approved">文件出域已批准</span>`:`<label class="t3-inline-input">文件审批依据<input id="t3-file-reason" value="仅用于 P-DEMO-01 项目约定研究，接收方受控保管"></label><button type="button" class="t3-button quiet" data-action="approve_file" ${ui.busy||!data.plan||!data.grant?'disabled':''}>单独批准文件交付</button>`}<button type="button" class="t3-button primary" data-action="deliver" ${ui.busy||!data.latest_run||data.latest_run.status!=='success'||!data.grant||data.grant.status!=='active'?'disabled':''}>交付当前成功结果</button>${latestDelivery?`<button type="button" class="t3-button secondary" data-result>读取交付结果</button>${latestDelivery.method==='approved_file'?`<a class="t3-button secondary" href="${endpoint}/cases/${encodeURIComponent(data.case.id)}/download">下载获准文件</a>`:''}`:''}</div>${latestDelivery?`<div class="t3-plan-summary"><div><b>交付记录 ${esc(latestDelivery.id)}</b><span>${esc(latestDelivery.method)} · ${esc(latestDelivery.state)} · 摘要 ${esc((latestDelivery.result_digest||'').slice(0,16))}</span></div><span class="t3-state-pill">复核完成</span></div>`:''}${result?renderResult(result):''}<div class="t3-evidence-grid"><div><h4>访问和交付决定</h4>${(data.decisions||[]).slice().reverse().map(item=>`<p class="t3-evidence-row"><b class="${item.allowed?'ok':'bad'}">${item.allowed?'放行':'拦截'}</b><span>${esc(item.reason)}</span><small>${esc(item.created_at)}</small></p>`).join('')||'<small>尚无访问决定。</small>'}</div><div><h4>全程事件</h4>${(data.events||[]).slice().reverse().map(item=>`<p class="t3-evidence-row"><b>${esc(item.action)}</b><span>${esc(item.detail)} · ${esc(item.result)}</span><small>${esc(item.updated_at)}</small></p>`).join('')||'<small>尚无证据事件。</small>'}</div></div><p class="t3-note">服务调用、策略决定、节点耗时、数据集固定版本、质量报告、来源血缘和交付摘要均保存在服务端。模型规则只用于合成数据演示，不具有临床效度。</p></section>`;
 };
 const renderResult=value=>{
  const dataset=value.dataset;const table=dataset||value.result;
  if(!table)return `<div class="t3-empty-state">结果已生成，但当前账户等级不展示结果行。</div>`;
  const columns=table.columns||[];const rows=table.rows||[];
  return `<div class="t3-result"><div class="t3-result-head"><b>本次交付结果</b><span>${value.permission?.level?`${value.permission.level}级账户`:'按当前账户等级展示'} · 合成样例</span></div>${table.message?`<p>${esc(table.message)}</p>`:''}${dataset?`<p>数据集 ${esc(dataset.dataset_version)} · ${esc(dataset.record_count)}条 · SHA-256 ${esc(dataset.content_digest)}</p><p>质量缺失率 ${dataset.quality_report?.max_missing_rate==null?'—':(dataset.quality_report.max_missing_rate*100).toFixed(1)+'%'} · 血缘方案 ${esc(dataset.lineage?.plan_id)} v${esc(dataset.lineage?.plan_version)}</p>`:''}${columns.length?`<div class="t3-table-scroll"><table><thead><tr>${columns.map(c=>`<th>${esc(fieldNames[c]||c)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${(Array.isArray(row)?row:columns.map(c=>row[c])).map(cell=>`<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:''}</div>`;
 };
 const stageContent=()=>{
  if(!state())return empty();
  return `${commonHeader()}${ui.stage===0?requestPanel():ui.stage===1?graphPanel():ui.stage===2?policyPanel():ui.stage===3?executePanel():deliveryPanel()}`;
 };
 const page=()=>`<div class="t3-page">${innovation()}<div class="t3-topbar">${casePicker()}<div class="t3-demo-label"><i></i>服务端持久记录 · 仅合成数据</div></div>${ui.error?`<div class="t3-error" role="alert">${esc(ui.error)}</div>`:''}${ui.notice?`<div class="t3-notice" role="status">${esc(ui.notice)}</div>`:''}${ui.busy?'<div class="t3-busy"><i></i>正在保存并刷新服务端记录…</div>':''}${ui.current?progress():''}<div class="t3-layout"><main class="t3-main">${stageContent()}${state()?`<div class="t3-stage-nav"><button type="button" class="t3-button quiet" data-move="-1" ${ui.stage===0?'disabled':''}>← 上一步</button><span>流程 ${String(ui.stage+1).padStart(2,'0')} / 05</span><button type="button" class="t3-button quiet" data-move="1" ${ui.stage===4?'disabled':''}>下一步 →</button></div>`:''}</main>${state()?objects():''}</div><div class="t3-disclosure"><b>演示边界</b><span>本模块展示平台内可追溯的需求、合约、服务图、节点执行和改进记录。当前服务只处理固定合成样例；实际医院、医保、科研机构服务与临床预测能力尚未接入。</span></div></div>`;
 const readSpec=()=>{
  const expires=document.querySelector('#t3-expires')?.value;
  const picked=[...host.querySelectorAll('input[name="t3-field"]:checked')].map(x=>x.value);
  return {purpose:document.querySelector('#t3-purpose')?.value,scope_id:document.querySelector('#t3-scope')?.value,
   fields:picked,feature_window_days:Number(document.querySelector('#t3-feature-window')?.value),
   outcome_window_days:Number(document.querySelector('#t3-outcome-window')?.value),
   quality_min_complete:Number(document.querySelector('#t3-quality')?.value||.95),
   min_group_size:Number(document.querySelector('#t3-min-group')?.value),
   circulation_mode:document.querySelector('#t3-circulation')?.value,
   delivery_method:document.querySelector('#t3-delivery')?.value,
   delivery_methods:['controlled_access','api_reference','approved_file'],
   expires_at:expires?new Date(expires).toISOString():undefined};
 };
 const bind=()=>{
  host.querySelectorAll('[data-stage]').forEach(el=>el.addEventListener('click',()=>{ui.stage=Number(el.dataset.stage);ui.error='';redraw();}));
  host.querySelectorAll('[data-move]').forEach(el=>el.addEventListener('click',()=>{ui.stage=Math.min(4,Math.max(0,ui.stage+Number(el.dataset.move)));redraw();}));
  host.querySelectorAll('[data-goto-stage]').forEach(el=>el.addEventListener('click',()=>{ui.stage=Number(el.dataset.gotoStage);redraw();}));
  host.querySelectorAll('[data-create]').forEach(el=>el.addEventListener('click',()=>run(async()=>{
   const requestText=document.querySelector('#t3-request')?.value;
   const value=await request('/cases',{method:'POST',body:JSON.stringify({scenario:el.dataset.create,...(requestText?{request_text:requestText}:{})})});
   ui.current=value;ui.stage=0;await refresh(value.case.id);
  },'已创建并保存演示任务。')));
  host.querySelectorAll('[data-refresh]').forEach(el=>el.addEventListener('click',()=>run(()=>refresh(), '已从服务端刷新任务记录。')));
  const picker=host.querySelector('#t3-case-select');if(picker)picker.addEventListener('change',()=>run(()=>refresh(picker.value||null),'已打开保存的演示任务。'));
  host.querySelectorAll('[data-action]').forEach(el=>el.addEventListener('click',()=>{
   const action=el.dataset.action;
   run(async()=>{
    let payload={};
    if(action==='draft')payload={request_text:document.querySelector('#t3-request')?.value||'',provider:document.querySelector('#t3-provider')?.value||undefined};
    if(action==='confirm_spec')payload={spec:readSpec()};
    if(action==='check_access')payload={kind:el.dataset.kind};
    if(action==='run')payload={failure_mode:el.dataset.failure||'normal'};
    if(action==='repair_quality')payload={change_note:document.querySelector('#t3-repair-note')?.value||''};
    if(action==='approve_file')payload={reason:document.querySelector('#t3-file-reason')?.value||'',recipient:'P-DEMO-01 项目研究员'};
    if(action==='deliver')payload={method:document.querySelector('#t3-delivery-method')?.value||'controlled_access'};
    const value=await postAction(action,payload);ui.result=null;
    if(action==='generate_plan'&&value.gaps?.length)ui.notice='发现服务供给缺口。补充发布后重新生成方案图。';
    if(action==='check_access'&&value.grant?.status==='active')ui.notice='意图已确认并生成限期授权；尚未执行。';
    if(action==='run'&&value.latest_run?.status==='failed')ui.notice='当前质量门槛拒绝交付；失败证据已保存。';
    if(action==='run'&&value.latest_run?.status==='success')ui.notice='服务图执行完成，节点状态和耗时已写入运行证据。';
   },'操作已保存。');
  }));
  host.querySelectorAll('[data-result]').forEach(el=>el.addEventListener('click',()=>run(async()=>{ui.result=await request(`/cases/${encodeURIComponent(state().case.id)}/result`);},'已按当前账户权限读取交付结果。')));
 };
 redraw();
 (async()=>{try{const providerInfo=await request('/ai/providers').catch(()=>({providers:[]}));ui.providers=providerInfo.providers||[];await refresh();redraw();}catch(error){reportError(error);}})();
}
