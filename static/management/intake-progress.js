const STATUS={
 done:'已完成',current:'进行中',pending:'待开始',declined:'暂不授权',failed:'未完成',
 completed:'已完成',waiting_authorization:'等待授权',waiting_manual_spec_confirmation:'待确认规格',
 waiting_field_classification:'待平台确认字段分类',
 awaiting_execution_adapter:'待建立受控方案',awaiting_plan_confirmation:'待确认方案',
 awaiting_execution_authorization:'等待授权',awaiting_authorization_request:'待授权处理',
 awaiting_platform_authorization:'等待平台审批',awaiting_query_submission:'等待需求方提交查询',
 query_queued:'已提交 · 排队中',query_running:'执行中',query_completed:'已完成',
 query_failed:'执行失败',blocked:'已阻断',gap:'缺少资源',waiting_spec:'等待规格',
 waiting_connection:'待连接',proposed:'待开始',matched:'待确认匹配',
 authorization_declined:'平台拒绝申请'
};

const capabilityState=(step)=>{
 if(!step)return 'pending';
 if(step.status==='completed')return 'done';
 if(['blocked','gap'].includes(step.status))return 'failed';
 return ['proposed','matched','waiting_manual_spec_confirmation','waiting_field_classification','awaiting_execution_adapter',
  'awaiting_plan_confirmation','waiting_authorization','awaiting_execution_authorization',
  'awaiting_authorization_request','awaiting_platform_authorization','awaiting_query_submission'].includes(step.status)?'pending':'current';
};

export function renderIntakeProgress(flow,{compact=false}={}){
 const sourceSteps=flow.steps||[];
 const byCapability=Object.fromEntries(sourceSteps.map(step=>[step.capability,step]));
 const confirmed=Boolean(flow.confirmation?.confirmed);
 const progress=[{
  label:'确认需求理解与资源候选',status:confirmed?'done':flow.state==='awaiting_chain_confirmation'?'current':'pending'
 }];
 for(const capability of ['catalog_search','connected_source_discovery']){
  const step=byCapability[capability];
  if(step)progress.push({label:step.label,status:capabilityState(step),detail:step.detail,capability});
 }
 if(flow.field_lookup){
  const classification=flow.field_lookup.classification;
  const outputNames=(flow.field_lookup.output_fields||[]).map(item=>item.field_name).filter(Boolean);
  const classifiedValues=['public','internal','health','identifier','sensitive','公开','内部','健康信息','个人标识','敏感'];
  const status=classifiedValues.includes(classification)?'done':
   flow.state==='waiting_field_classification'?'current':flow.state==='attention'?'failed':'pending';
  const autoClassified=flow.classification_review?.decision==='auto_classified_from_catalog_context';
  const basis=flow.field_lookup.classification_basis||flow.classification_review?.classification_basis;
  const detail=`${flow.field_lookup.table_name||''}.${outputNames.join('、')||flow.field_lookup.field_name||''} · ${classification||'待确认'}`+
   (basis?' · 分类依据：'+basis:'');
  progress.push({label:autoClassified?'自动分类与依据':'平台确认输出字段分类',status,detail});
 }
 for(const step of sourceSteps.filter(item=>!['catalog_search','connected_source_discovery','data_query'].includes(item.capability)))
  progress.push({label:step.label,status:capabilityState(step),detail:step.detail});

 const queryFlow=sourceSteps.some(step=>['data_query','trend_analysis'].includes(step.capability))||
  Boolean(flow.query_draft||flow.spec_id||flow.execution_plan_id||flow.job_id||flow.delivery_id);
 if(queryFlow){
  const specReady=Boolean(flow.spec_id&&flow.spec_version);
  const specCurrent=flow.state==='waiting_manual_spec_confirmation'||
   (flow.state==='completed'&&!specReady&&!flow.execution_plan_id);
  progress.push({label:'确认任务规格',status:specReady?'done':specCurrent?'current':'pending'});
  const planReady=Boolean(flow.plan_confirmed_by);
  progress.push({label:'生成并确认固定方案',status:planReady?'done':flow.execution_plan_id?'current':'pending'});
  const authStatus=flow.execution_authorized?'done':flow.state==='authorization_declined'?'declined':
   ['awaiting_execution_authorization','awaiting_authorization_request','awaiting_platform_authorization'].includes(flow.state)?'current':'pending';
  progress.push({label:'授权确认',status:authStatus});
  const queryStatus=flow.state==='query_completed'?'done':flow.state==='query_failed'?'failed':
   ['query_queued','query_running','awaiting_query_submission'].includes(flow.state)?'current':'pending';
  progress.push({label:'单独提交并执行查询',status:queryStatus});
  const resultStatus=flow.delivery_result||flow.delivery_result_read_at?'done':flow.state==='query_failed'?'failed':
   flow.state==='query_completed'?'current':'pending';
  progress.push({label:'读取查询交付结果',status:resultStatus});
 }

 const section=document.createElement('section');section.className=compact?'intake-progress intake-progress-compact':'intake-progress';
 const current=progress.find(step=>['current','declined','failed'].includes(step.status))||
  progress.find(step=>step.status==='pending');
 const done=progress.filter(step=>step.status==='done').length;
 const heading=document.createElement('div');heading.className='intake-progress-heading';
 const title=document.createElement('strong');title.textContent=`完整办理链路 · ${done}/${progress.length} 步已完成`;
 const now=document.createElement('span');now.textContent=current?`当前：${current.label} · ${STATUS[current.status]}`:'当前：全部完成';now.hidden=compact;
 heading.append(title,now);section.append(heading);
 const list=document.createElement('ol');list.className='intake-progress-list';
 for(const step of progress){
  const shownStatus=step===current&&step.status==='pending'?'current':step.status;
  const item=document.createElement('li');item.className='intake-progress-step is-'+shownStatus;
  if(step===current)item.setAttribute('aria-current','step');
  const name=document.createElement('strong');name.textContent=step.label;
  const status=document.createElement('span');status.textContent=STATUS[shownStatus]||shownStatus;
  item.append(name,status);
  if(!compact&&step.detail){const detail=document.createElement('small');detail.textContent=flow.model==='platform-catalog-gap-rule'&&step.capability==='catalog_search'&&['gap','failed'].includes(step.status)?'平台登记目录中没有匹配的微信指数候选；未调用模型或读取源数据行。':step.detail;item.append(detail);}
  list.append(item);
 }
 section.append(list);return section;
}

export function appendIntakeDelivery(flow,body,onRead){
 const section=document.createElement('section');section.className='intake-result-confirmation';
 const heading=document.createElement('h3');heading.textContent=flow.delivery_result?'受控查询交付结果':'查询已完成，结果已进入平台交付';section.append(heading);
 const outputNames=(flow.field_lookup?.output_fields||[]).map(item=>item.field_name).filter(Boolean);
 const fieldSummary=outputNames.join('、')||flow.field_lookup?.field_name||'';
 const resultDescription=flow.output_preference==='admission_time'?'已确认的入院时间':
  flow.output_preference==='catalog_field'?'已确认的目录字段 '+fieldSummary:
  flow.output_preference==='catalog_field_with_patient_id'?'已确认的目录字段 '+(flow.field_lookup?.field_name||'')+' 与原始 patient_id':'已确认的患者伪名ID';
 const intro=document.createElement('p');intro.textContent=flow.delivery_result?
  `交付编号：${flow.delivery_result.delivery_id} · 本次只展示${resultDescription}。`:
  '新提交的查询完成后会自动弹出结果；历史交付可点此读取。';section.append(intro);
 const quality=String(flow.draft?.quality||'').trim(),qualityRule=quality.replace(/[。.!！?？]+$/,'').trim().toLowerCase();
 if(quality&&!['完整','无缺失','complete','no missing values','完整率','completeness'].includes(qualityRule)){
  const note=document.createElement('p');note.className='intake-result-blocker';
  note.textContent=`质量要求“${quality}”已保留；本次自动检查仅覆盖完整率，其他质量要求未自动验证。`;
  section.append(note);
 }
 if(flow.query_error){const error=document.createElement('p');error.className='intake-result-blocker';error.textContent='执行说明：'+flow.query_error;section.append(error);}
 if(!flow.delivery_result){
  const actions=document.createElement('div');actions.className='intake-result-actions';
  const button=document.createElement('button');button.type='button';button.className='mf-button mf-button-primary';button.textContent='读取本次交付结果';button.onclick=()=>onRead(flow);actions.append(button);section.append(actions);
 }else{
  const result=flow.delivery_result.result||{},columns=result.columns||[],rows=result.rows||[];
  const summary=document.createElement('p');
  summary.textContent=rows.length?`本次返回 ${rows.length} 条记录。`:
   '查询已执行，返回 0 条记录。当前数据源中没有满足本次筛选和关联条件的记录；这不代表该患者从未有过相关记录。';
  if(!rows.length)summary.className='intake-result-blocker';
  section.append(summary);
  const table=document.createElement('table');table.className='intake-delivery-table';
  const head=document.createElement('thead'),headerRow=document.createElement('tr');
  for(const column of columns){const th=document.createElement('th');th.textContent=column;headerRow.append(th);}head.append(headerRow);table.append(head);
  const tbody=document.createElement('tbody');
  for(const row of rows){const tr=document.createElement('tr');for(const value of row){const td=document.createElement('td');td.textContent=value==null?'':String(value);tr.append(td);}tbody.append(tr);}
  table.append(tbody);section.append(table);
  const expiry=document.createElement('p');expiry.textContent=`交付有效期至：${flow.delivery_result.expires_at||'按授权期限'}`;section.append(expiry);
 }
 body.append(section);
}
