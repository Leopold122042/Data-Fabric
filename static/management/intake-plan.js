import {api,message} from './ui.js';
import {role,currentAccess} from '/static/shared/access.js';

const el=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
const actionButton=(text,handler)=>{const button=el('button',text,'mf-button mf-button-primary');button.type='button';button.onclick=handler;return button;};
const formatDate=value=>{const date=new Date(value);return Number.isNaN(date.valueOf())?value:new Intl.DateTimeFormat('zh-CN',{dateStyle:'short',timeStyle:'short'}).format(date);};
const COMPLETENESS_LABELS=new Set(['完整','无缺失','complete','no missing values','完整率','completeness']);
export const requiresQualityReview=quality=>{const label=String(quality||'').trim().replace(/[。.!！?？]+$/,'').trim().toLowerCase();return Boolean(label)&&!COMPLETENESS_LABELS.has(label);};
export function appendQualityRequirementNotice(quality,host,threshold=1){
 if(!requiresQualityReview(quality))return null;
 host.append(el('p',`质量要求“${quality}”会保留在任务规格中。当前仅自动检查完整率（阈值 ${threshold}）；准确性、时效性等其他要求不会自动验证。`,'intake-result-blocker'));
 const input=document.createElement('input');input.type='checkbox';input.id='quality-requirement-reviewed';
 const label=el('label');label.append(input,document.createTextNode('我已知晓本次其他质量要求尚未由系统自动验证。'));host.append(label);
 return input;
}

export function appendPseudonymRevision(flow,body,onSaved){
 const alreadyConfirmed=flow.pseudonym_spec_revision_confirmed===true;
 const section=el('section',undefined,'intake-result-confirmation');
 section.append(el('h3',alreadyConfirmed?'新规格已保存，继续生成方案':'确认对象级伪名输出规格'));
 section.append(el('p',alreadyConfirmed?'新规格已经保存为对象级伪名查询：按姓名精确匹配，不按出生日期筛选。现在只需继续生成固定方案草案；这一步不会查询源数据。':'当前已确认规格是汇总输出，并把出生日期当作数据时间筛选；它不能返回患者ID。按你的选择，将创建新规格版本：保留已确认SQL中的姓名精确匹配，移除出生日期筛选，只输出HMAC伪名ID。'));
 let check=null,note=null,qualityReview=null;
 if(!alreadyConfirmed){
  qualityReview=appendQualityRequirementNotice(flow.draft?.quality,section,flow.draft?.execution_constraints?.quality_min_complete??1);
  check=document.createElement('input');check.type='checkbox';
  const label=el('label');label.append(check,document.createTextNode('我确认上述规格修订，并理解伪名ID仍可在本平台内稳定关联同一患者。'));section.append(label);
  const noteLabel=el('label',undefined,'intake-confirm-note');noteLabel.textContent='修订确认说明';
  note=document.createElement('textarea');note.rows=2;note.value='已确认：姓名精确匹配；移除出生日期数据筛选；仅返回患者HMAC伪名ID。';noteLabel.append(note);section.append(noteLabel);
 }
 const error=el('p',undefined,'intake-confirm-error');error.setAttribute('role','alert');
 const submit=actionButton(alreadyConfirmed?'继续生成方案预览':'确认新规格并生成方案预览',async()=>{
  submit.disabled=true;error.textContent='正在保存新规格并生成只读方案草案…';
  try{const result=await api('/intake/flows/'+flow.id+'/revise-pseudonymized-id',{expected_version:flow.version,expected_spec_version:flow.spec_version,confirmed:true,quality_requirement_reviewed:qualityReview?.checked===true,note:note?.value.trim()||'继续准备已确认规格的伪名查询方案。'});
   onSaved(result,'患者伪名ID方案预览');message(result.note);
  }catch(cause){error.textContent=cause.message||'规格修订或方案预览失败，请刷新后重试。';sync();}
 });
 const sync=()=>{submit.disabled=!alreadyConfirmed&&(!check.checked||!note.value.trim()||Boolean(qualityReview&&!qualityReview.checked));};
 if(check){check.addEventListener('change',sync);note.addEventListener('input',sync);qualityReview?.addEventListener('change',sync);}
 const actions=el('div',undefined,'intake-result-actions');actions.append(submit);section.append(error,actions);body.append(section);sync();
}

export function appendFieldClassification(flow,body,onSaved){
 const lookup=flow.field_lookup||{};const outputFields=lookup.output_fields||[];const outputNames=outputFields.map(item=>item.field_name).filter(Boolean);const section=el('section',undefined,'intake-result-confirmation');
 if(role()!=='platform'){
  section.append(el('h3','等待平台确认输出字段分类'));
  section.append(el('p',`目录匹配到 ${lookup.table_name||'数据表'}中的 ${outputNames.join('、')||lookup.field_name||'目标字段'}；分类尚未确认。平台方确认前不会建立方案或读取源数据。`));
  body.append(section);return;
 }
 section.append(el('h3',`请确认待处理字段 ${lookup.table_name||''}.${lookup.field_name||'输出字段'} 的分类`));
 section.append(el('p',`本次所有输出：${outputNames.join('、')||lookup.field_name||'目录字段'}（${lookup.target_phrase||lookup.field_meaning||'目录字段'}）。姓名字段 ${lookup.name_field_name||'name'} 只用于精确筛选，不会输出。多个输出字段会逐项确认分类。`));
 const select=document.createElement('select');select.setAttribute('aria-label','输出字段分类');
 const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='选择分类';select.append(placeholder);
 for(const [value,label] of [['公开','公开'],['内部','内部'],['健康信息','健康信息'],['个人标识','个人标识'],['敏感','敏感']]){
  const option=document.createElement('option');option.value=value;option.textContent=label;select.append(option);
 }
 const label=el('label','平台登记分类');label.append(select);section.append(label);
 const error=el('p',undefined,'intake-confirm-error');error.setAttribute('role','alert');
 const submit=actionButton('登记分类并继续',async()=>{
  submit.disabled=true;error.textContent='正在登记字段分类…';
  try{const result=await api('/intake/flows/'+flow.id+'/confirm-output-field-classification',
   {expected_version:flow.version,classification:select.value});
   onSaved(result.flow,'字段分类已登记');message(result.note);
  }catch(cause){error.textContent=cause.message||'分类登记失败，请刷新后重试。';submit.disabled=false;}
 });
 const sync=()=>{submit.disabled=!select.value;};select.addEventListener('change',sync);
 const actions=el('div',undefined,'intake-result-actions');actions.append(submit);
 section.append(error,actions);body.append(section);sync();
}

export function appendCatalogFieldSpecConfirmation(flow,body,onSaved){
 const lookup=flow.field_lookup||{},preview=flow.execution_plan_preview||{};
 const outputFields=lookup.output_fields||[];
 const outputNames=outputFields.map(item=>item.field_name).filter(Boolean);
 const section=el('section',undefined,'intake-result-confirmation');
 section.append(el('h3','平台已匹配字段并预填查询规格，请核对后确认'));
 section.append(el('p','SQL只读草案已按目录规则校验。姓名仅作精确筛选；输出字段、范围和用途均由当前规格固定。确认规格不会执行查询或授予权限。'));
 const summary=el('div',undefined,'intake-result-summary');
 for(const [name,value] of [['数据来源',`${lookup.source_id||preview.source_id||''} / ${lookup.table_name||preview.table||''}`],
  ['姓名匹配',`${lookup.name_field_name||'姓名'} = ${lookup.name||''}（精确匹配）`],
  ['输出字段',outputFields.length?outputFields.map(item=>`${item.field_name}${item.classification?' · '+item.classification:''}`).join('、'):`${lookup.field_name||''}${lookup.classification?' · '+lookup.classification:''}`],
  ['用途',flow.draft?.purpose||'按已确认任务规格'],['交付方式','对象级受控查看']])summary.append(el('p',`${name}：${value}`));
 section.append(summary);
 const qualityReview=appendQualityRequirementNotice(flow.draft?.quality,section,flow.draft?.execution_constraints?.quality_min_complete??1);
 const sql=el('pre',flow.query_validation?.normalized_sql||flow.query_draft||'缺少只读SQL草案');
 const details=document.createElement('details');details.append(el('summary','只读 SQL 草案'),sql);section.append(details);
 const check=document.createElement('input');check.type='checkbox';const label=el('label');
 label.append(check,document.createTextNode(`我确认表、姓名筛选、输出字段（${outputNames.join('、')||lookup.field_name||'目录字段'}）和用途符合本次需求。`));section.append(label);
 const error=el('p',undefined,'intake-confirm-error');error.setAttribute('role','alert');
 const submit=actionButton('确认规格并生成固定方案',async()=>{
  submit.disabled=true;error.textContent='正在保存规格并生成方案草案…';
  try{const result=await api('/demands/'+flow.demand_id+'/confirm',{expected_version:flow.demand_version,
   confirmations:{content:true,attachments:true,gaps:true,execution_constraints:true,query_sql_confirmed:true,
    ...(qualityReview?{quality_requirement_reviewed:qualityReview.checked}:{})},
   query_sql_confirmed:true,query_draft:flow.query_validation?.normalized_sql||flow.query_draft,
   note:'已核对平台按目录字段匹配预填的姓名精确查询规格。'});
   onSaved(result.intake_flow||flow,'固定方案已生成 · 请确认');message(result.next_action||'规格已确认；固定方案草案已生成，尚未授权或执行。');
  }catch(cause){error.textContent=cause.message||'规格确认失败，请刷新后重试。';sync();}
 });
 const sync=()=>{submit.disabled=!check.checked||Boolean(qualityReview&&!qualityReview.checked);};check.addEventListener('change',sync);qualityReview?.addEventListener('change',sync);
 const actions=el('div',undefined,'intake-result-actions');actions.append(submit);
 section.append(error,actions);body.append(section);sync();
}

export function appendExecutionPlanConfirmation(flow,body,onSaved){
 const admissionTime=flow.output_preference==='admission_time';
 const patientIdentifier=flow.output_preference==='catalog_field_with_patient_id';
 const outputNames=(flow.field_lookup?.output_fields||[]).map(item=>item.field_name).filter(Boolean);
 const outputLabel=outputNames.join('、')||flow.field_lookup?.field_name||'目标字段';
 const section=el('section',undefined,'intake-result-confirmation');
 section.append(el('h3','请确认固定执行方案'));
 section.append(el('p','方案由已确认规格和目录元数据自动预填。此处只保存方案确认，不读取源数据、不提交查询，也不签发授权。'));
 const preview=flow.execution_plan_preview||{};const summary=el('div',undefined,'intake-result-summary');
 const filters=(preview.filter_names||[]).map((name,index)=>name+' = '+(preview.filters?.[index]?.value??'')).join('；')||'无';
 const rows=[['方案',`${preview.name||(admissionTime?'患者入院时间受控查询':'患者伪名ID受控查询')} · ${preview.plan_id||''} v${preview.version||''}`],
  ['来源',`${preview.source_id||'本地'} / ${preview.table||'已确认表'}`],['筛选',filters],
  ['输出',preview.output_description||(admissionTime?'仅返回入院时间；不返回姓名或患者ID':flow.output_preference==='catalog_field'?'仅返回已确认的目录字段':'patient_token（HMAC-SHA256 本地密钥）')],
  ['查询状态',preview.reads_source_rows===false?'尚未读取源数据行；需要授权后才可提交':'仅方案草案']];
 for(const [name,value] of rows)summary.append(el('p',`${name}：${value}`));section.append(summary);
 if(requiresQualityReview(flow.draft?.quality))section.append(el('p',`质量要求“${flow.draft.quality}”已保留在规格中；平台只自动检查完整率，其他质量要求不会自动验证。`,'intake-result-blocker'));
 const check=document.createElement('input');check.type='checkbox';const label=el('label');
 label.append(check,document.createTextNode(admissionTime?'我已核对表关联、姓名与住院筛选及入院时间输出字段；此确认不是原值交付授权。':patientIdentifier?`我已核对${flow.field_lookup?.field_name||'目标字段'}及原始patient_id输出；个人标识仍需平台逐方案授权。`:flow.output_preference==='catalog_field'?`我已核对来源表、姓名筛选和输出字段（${outputLabel}）；此处只确认方案，不代表授权或执行。`:'我已核对来源表、姓名筛选和伪名输出，并确认此固定方案。'));section.append(label);
 const error=el('p',undefined,'intake-confirm-error');error.setAttribute('role','alert');
 const submit=actionButton('确认方案，继续办理授权',async()=>{
  submit.disabled=true;error.textContent='正在记录方案确认…';
  const confirmationNote=admissionTime?'已核对自动预填的入院时间查询条件；后续由平台方授权或按需求方申请流程办理。':patientIdentifier?`已核对原姓名筛选、${flow.field_lookup?.field_name||'目标字段'}及目录patient_id输出；仍需按当前方案申请并批准原始标识字段。`:
   flow.output_preference==='catalog_field'?`已核对自动预填的 ${flow.field_lookup?.table_name||'目录表'} 输出字段（${outputLabel}）查询方案。`:'已核对自动预填的单表精确查询方案；只输出患者伪名ID。';
  try{const result=await api('/intake/flows/'+flow.id+'/confirm-execution-plan',{expected_version:flow.version,confirmed:true,note:confirmationNote});
   onSaved(result,'方案已确认 · 等待授权');message(result.note);
  }catch(cause){error.textContent=cause.message||'方案确认失败，请刷新后重试。';sync();}
 });
 const sync=()=>{submit.disabled=!check.checked;};check.addEventListener('change',sync);
 const actions=el('div',undefined,'intake-result-actions');actions.append(submit);section.append(error,actions);body.append(section);sync();
}

export function appendExecutionAuthorization(flow,body,onSaved){
 const admissionTime=flow.output_preference==='admission_time';
 const resultDescription=admissionTime?'入院时间原值（健康信息；需平台授权）':flow.execution_plan_preview?.output_description||
  '仅返回 HMAC-SHA256 患者伪名ID';
 const section=el('section',undefined,'intake-result-confirmation');
 const preview=flow.execution_plan_preview||{};
 const requestRecord=flow.authorization_request||{};
 const fields=requestRecord.fields?.length?requestRecord.fields:
  admissionTime?[{field_name:'入院时间',output_name:'admission_time'}]:[{field_name:resultDescription}];
 const filters=(preview.filter_names||[]).map((name,index)=>name+' = '+(preview.filters?.[index]?.value??'')).join('；')||'已确认筛选条件';
 const summary=el('div',undefined,'intake-result-summary');
 for(const [name,value] of [['固定方案',`v${requestRecord.plan_version||preview.version||flow.execution_plan_version||''}`],
  ['筛选',filters],['申请字段',fields.map(field=>field.field_name||field.output_name).join('、')||resultDescription],
  ['用途',requestRecord.purpose||flow.draft?.purpose||'按已确认任务规格']])summary.append(el('p',`${name}：${value}`));
 const currentRole=role();
 if(flow.state==='awaiting_platform_authorization'){
  if(currentRole==='platform'){
   section.append(el('h3','平台审核需求方的字段交付申请'));
   section.append(el('p',`需求方 ${requestRecord.requester_id||''} 已提交申请。批准后只登记本方案、用途及字段范围内的授权；不会自动查询，数据方无需审批。`));
   section.append(summary);
  }else{
   section.append(el('h3','授权申请已提交，等待平台批准'));
   section.append(el('p','平台方是最终审批方。批准前不会登记授权或读取数据。'));
   section.append(summary);body.append(section);return;
  }
 }else if(currentRole==='platform'&&flow.state==='awaiting_authorization_request'){
  section.append(el('h3','平台方直接授权本次查询'));
  section.append(el('p','你当前是平台方，可直接批准自己发起的本次查询，无需切换到需求方重复申请。批准只登记本方案和字段范围内的短时授权；仍需在下一步单独确认提交查询。'));
  section.append(summary);
 }else if(currentRole==='requester'&&['awaiting_authorization_request','authorization_declined'].includes(flow.state)){
  section.append(el('h3',flow.state==='authorization_declined'?'申请未获批准，可重新申请':'申请本次字段交付授权'));
  section.append(el('p',admissionTime?'需求方请求交付入院时间原值。平台方将作最终批准或拒绝；提交申请不会授权或查询。':'提交后由平台方审核本次固定方案授权；提交申请不会执行查询。'));
  section.append(summary);
 }else if(flow.state==='authorization_declined'){
  section.append(el('h3','平台方已拒绝本次授权申请'));
  section.append(el('p','查询未提交，未登记本次授权。'));
  section.append(summary);body.append(section);return;
 }else{
  section.append(el('h3','等待需求方提交授权申请'));
  section.append(el('p','请切换右上角身份为“需求方”，由需求方提交本次字段交付申请；随后由平台方最终审批。数据方不参与此流程。'));
  section.append(summary);body.append(section);return;
 }
 const actions=el('div',undefined,'intake-result-actions');
 const error=el('p',undefined,'intake-confirm-error');error.setAttribute('role','alert');
 const directPlatform=currentRole==='platform'&&flow.state==='awaiting_authorization_request';
 const saveChoice=async(decision,button)=>{
  for(const item of actions.querySelectorAll('button'))item.disabled=true;
  error.textContent=currentRole==='platform'?(decision==='yes'?'正在批准并登记本次授权…':'正在记录拒绝决定…'):'正在提交授权申请…';
  const path=currentRole==='platform'?'/intake/flows/'+flow.id+'/execution-authorization':'/intake/flows/'+flow.id+'/request-execution-authorization';
  const payload=currentRole==='platform'?{expected_version:flow.version,decision,...(directPlatform?{direct:true}:{})}:{expected_version:flow.version,request:true};
  try{const result=await api(path,payload);
   const title=currentRole==='platform'?(decision==='yes'?(directPlatform?'平台已授权 · 请确认提交查询':'平台已批准 · 等待需求方提交查询'):(directPlatform?'平台暂不授权':'平台已拒绝授权申请')):'授权申请已提交 · 等待平台批准';
   onSaved(result.flow,title);message(result.note);
  }catch(cause){error.textContent=cause.message||'处理授权申请失败，请刷新后重试。';
   for(const item of actions.querySelectorAll('button'))item.disabled=false;button.focus();}
 };
 if(currentRole==='platform'){
  const approve=actionButton(directPlatform?'平台授权并继续':'批准并登记授权',()=>saveChoice('yes',approve));
  const deny=el('button',directPlatform?'暂不授权':'拒绝申请','mf-button');deny.type='button';deny.onclick=()=>saveChoice('no',deny);
  actions.append(approve,deny);
 }else{
  const submit=actionButton(flow.state==='authorization_declined'?'重新提交申请':'提交授权申请',()=>saveChoice('request',submit));
  actions.append(submit);
 }
 section.append(error,actions);body.append(section);
}

export function appendQuerySubmission(flow,body,onSaved){
 const admissionTime=flow.output_preference==='admission_time';
 const resultDescription=admissionTime?'入院时间原值（健康信息；已由平台批准）':flow.execution_plan_preview?.output_description||
  'patient_token（HMAC-SHA256）';
 const section=el('section',undefined,'intake-result-confirmation');
 const platformDirect=role()==='platform'&&flow.authorization_request?.direct_platform_approval===true&&
  flow.authorization_request?.requester_role==='platform';
 if(role()!=='requester'&&!platformDirect){
  section.append(el('h3','等待需求方单独提交查询'));
  section.append(el('p','平台方已批准需求方申请。请由原申请人单独提交查询；数据方不参与审批。'));
  body.append(section);return;
 }
 section.append(el('h3',platformDirect?'平台已授权；确认后提交本次查询':'平台已批准；查询仍未提交'));
 section.append(el('p',`确认下面这项受控查询后，平台才会读取已连接数据源中的匹配记录。结果：${resultDescription}。`));
 const preview=flow.execution_plan_preview||{};
 const filters=(preview.filter_names||[]).map((name,index)=>name+' = '+(preview.filters?.[index]?.value??'')).join('；')||'已确认筛选条件';
 const summary=el('div',undefined,'intake-result-summary');
 for(const [name,value] of [['固定方案',`${preview.plan_id||flow.execution_plan_id||''} v${preview.version||flow.execution_plan_version||''}`],
  ['数据来源',`${preview.source_id||'已连接数据源'} / ${preview.table||'已确认表'}`],['筛选',filters],
  ['输出',resultDescription],['授权',flow.grant_id||'当前固定方案授权']])summary.append(el('p',`${name}：${value}`));
 section.append(summary);
 const error=el('p',undefined,'intake-confirm-error');error.setAttribute('role','alert');
 const submit=actionButton('确认并提交本次受控查询',async()=>{
  submit.disabled=true;error.textContent='正在提交到本地受控执行器…';
  try{const result=await api('/intake/flows/'+flow.id+'/submit-query',{expected_version:flow.version,confirmed:true});
   onSaved(result.flow,'受控查询已提交');message(result.note);
  }catch(cause){error.textContent=cause.message||'提交查询失败，请刷新后重试。';submit.disabled=false;}
 });
 const actions=el('div',undefined,'intake-result-actions');actions.append(submit);section.append(error,actions);body.append(section);
}

export function appendQueryRetry(flow,body,onSaved){
 const section=el('section',undefined,'intake-result-confirmation');
 const request=flow.authorization_request||{};
 const currentId=currentAccess()?.principal?.id;
 const platformDirect=role()==='platform'&&request.direct_platform_approval===true&&request.requester_id===currentId;
 const requesterOwns=role()==='requester'&&request.requester_id===currentId;
 section.append(el('h3','可在原授权范围内重试'));
 section.append(el('p','平台已修复筛选未沿患者 ID 关联下推的问题。重试沿用原方案、原授权、原筛选条件和原输出字段；不会扩大范围。'));
 if(!platformDirect&&!requesterOwns){
  section.append(el('p','请由原查询提交人重试。'));
  body.append(section);return;
 }
 const error=el('p',undefined,'intake-confirm-error');error.setAttribute('role','alert');
 const retry=actionButton('在原授权内重试查询',async()=>{
  retry.disabled=true;error.textContent='正在按原方案和授权重试…';
  try{const result=await api('/intake/flows/'+flow.id+'/submit-query',{expected_version:flow.version,confirmed:true});
   onSaved(result.flow,'受控查询已重试');message(result.note);
  }catch(cause){error.textContent=cause.message||'重试失败，请刷新后查看当前状态。';retry.disabled=false;}
 });
 const actions=el('div',undefined,'intake-result-actions');actions.append(retry);section.append(error,actions);body.append(section);
}
