import {api,message} from './ui.js';

const el=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
const actionButton=(text,handler)=>{const button=el('button',text,'mf-button mf-button-primary');button.type='button';button.onclick=handler;return button;};
const formatDate=value=>{const date=new Date(value);return Number.isNaN(date.valueOf())?value:new Intl.DateTimeFormat('zh-CN',{dateStyle:'short',timeStyle:'short'}).format(date);};

export function appendPseudonymRevision(flow,body,onSaved){
 const alreadyConfirmed=flow.pseudonym_spec_revision_confirmed===true;
 const section=el('section',undefined,'intake-result-confirmation');
 section.append(el('h3',alreadyConfirmed?'新规格已保存，继续生成方案':'确认对象级伪名输出规格'));
 section.append(el('p',alreadyConfirmed?'新规格已经保存为对象级伪名查询：按姓名精确匹配，不按出生日期筛选。现在只需继续生成固定方案草案；这一步不会查询源数据。':'当前已确认规格是汇总输出，并把出生日期当作数据时间筛选；它不能返回患者ID。按你的选择，将创建新规格版本：保留已确认SQL中的姓名精确匹配，移除出生日期筛选，只输出HMAC伪名ID。'));
 let check=null,note=null;
 if(!alreadyConfirmed){
  check=document.createElement('input');check.type='checkbox';
  const label=el('label');label.append(check,document.createTextNode('我确认上述规格修订，并理解伪名ID仍可在本平台内稳定关联同一患者。'));section.append(label);
  const noteLabel=el('label',undefined,'intake-confirm-note');noteLabel.textContent='修订确认说明';
  note=document.createElement('textarea');note.rows=2;note.value='已确认：姓名精确匹配；移除出生日期数据筛选；仅返回患者HMAC伪名ID。';noteLabel.append(note);section.append(noteLabel);
 }
 const error=el('p',undefined,'intake-confirm-error');error.setAttribute('role','alert');
 const submit=actionButton(alreadyConfirmed?'继续生成方案预览':'确认新规格并生成方案预览',async()=>{
  submit.disabled=true;error.textContent='正在保存新规格并生成只读方案草案…';
  try{const result=await api('/intake/flows/'+flow.id+'/revise-pseudonymized-id',{expected_version:flow.version,expected_spec_version:flow.spec_version,confirmed:true,note:note?.value.trim()||'继续准备已确认规格的伪名查询方案。'});
   onSaved(result,'患者伪名ID方案预览');message(result.note);
  }catch(cause){error.textContent=cause.message||'规格修订或方案预览失败，请刷新后重试。';sync();}
 });
 const sync=()=>{submit.disabled=!alreadyConfirmed&&(!check.checked||!note.value.trim());};
 if(check){check.addEventListener('change',sync);note.addEventListener('input',sync);}
 const actions=el('div',undefined,'intake-result-actions');actions.append(submit);section.append(error,actions);body.append(section);sync();
}

export function appendExecutionPlanConfirmation(flow,body,onSaved){
 const admissionTime=flow.output_preference==='admission_time';
 const section=el('section',undefined,'intake-result-confirmation');
 section.append(el('h3','请确认固定执行方案'));
 section.append(el('p','方案由已确认规格和目录元数据自动预填。此处只保存方案确认，不读取源数据、不提交查询，也不签发授权。'));
 const preview=flow.execution_plan_preview||{};const summary=el('div',undefined,'intake-result-summary');
 const filters=(preview.filter_names||[]).map((name,index)=>name+' = '+(preview.filters?.[index]?.value??'')).join('；')||'无';
 const rows=[['方案',`${preview.name||(admissionTime?'患者入院时间受控查询':'患者伪名ID受控查询')} · ${preview.plan_id||''} v${preview.version||''}`],
  ['来源',`${preview.source_id||'本地'} / ${preview.table||'已确认表'}`],['筛选',filters],
  ['输出',preview.output_description||(admissionTime?'仅返回入院时间；不返回姓名或患者ID':'patient_token（HMAC-SHA256 本地密钥）')],
  ['查询状态',preview.reads_source_rows===false?'尚未读取源数据行；需要授权后才可提交':'仅方案草案']];
 for(const [name,value] of rows)summary.append(el('p',`${name}：${value}`));section.append(summary);
 const check=document.createElement('input');check.type='checkbox';const label=el('label');
 label.append(check,document.createTextNode(admissionTime?'我已核对表关联、姓名与住院筛选，并确认只交付入院时间。':'我已核对来源表、姓名筛选和伪名输出，并确认此固定方案。'));section.append(label);
 const error=el('p',undefined,'intake-confirm-error');error.setAttribute('role','alert');
 const submit=actionButton('确认方案，进入单独授权',async()=>{
  submit.disabled=true;error.textContent='正在记录方案确认…';
  try{const result=await api('/intake/flows/'+flow.id+'/confirm-execution-plan',{expected_version:flow.version,confirmed:true,note:admissionTime?'已核对自动预填的患者入院时间方案；仅交付入院时间。':'已核对自动预填的单表精确查询方案；只输出患者伪名ID。'});
   onSaved(result,'方案已确认 · 等待授权');message(result.note);
  }catch(cause){error.textContent=cause.message||'方案确认失败，请刷新后重试。';sync();}
 });
 const sync=()=>{submit.disabled=!check.checked;};check.addEventListener('change',sync);
 const actions=el('div',undefined,'intake-result-actions');actions.append(submit);section.append(error,actions);body.append(section);sync();
}

export function appendExecutionAuthorization(flow,body,onSaved){
 const resultDescription=flow.execution_plan_preview?.output_description||
  (flow.output_preference==='admission_time'?'仅返回入院时间；不返回姓名或患者ID':'仅返回 HMAC-SHA256 患者伪名ID');
 const section=el('section',undefined,'intake-result-confirmation');
 const existingGrant=flow.existing_execution_grant;
 section.append(el('h3',existingGrant?'当前方案已有本地授权':'授权范围'));
 section.append(el('p',existingGrant?
  `此固定方案已有一条本地授权（${existingGrant.id}，有效至 ${formatDate(existingGrant.expires_at)}）。选“是”将沿用它；选“否”会撤销它并停止。无论选哪项，都不会提交查询。`:
  '只为已确认的固定方案登记一次本地授权。选“是”只会保存授权，不会查询；查询还要在下一步单独提交。'));
 const preview=flow.execution_plan_preview||{};
 const filters=(preview.filter_names||[]).map((name,index)=>name+' = '+(preview.filters?.[index]?.value??'')).join('；')||'已确认筛选条件';
 const summary=el('div',undefined,'intake-result-summary');
 for(const [name,value] of [['固定方案',`${preview.plan_id||flow.execution_plan_id||''} v${preview.version||flow.execution_plan_version||''}`],
  ['数据来源',`${preview.source_id||'已连接数据源'} / ${preview.table||'已确认表'}`],['筛选',filters],
  ['结果',resultDescription],['授权期限',existingGrant?`现有授权有效至 ${formatDate(existingGrant.expires_at)}`:'自动设为1小时或规格截止时间，以较早者为准']])summary.append(el('p',`${name}：${value}`));
 section.append(summary);
 section.append(el('p','授权仅适用于此方案版本和当前账号。演示环境登记的是本地演示授权。'));
 const actions=el('div',undefined,'intake-result-actions');
 const error=el('p',undefined,'intake-confirm-error');error.setAttribute('role','alert');
 const decide=async(decision,button)=>{
  for(const item of actions.querySelectorAll('button'))item.disabled=true;
  error.textContent=decision==='yes'?'正在登记本次授权…':'正在记录暂不授权…';
  try{const result=await api('/intake/flows/'+flow.id+'/execution-authorization',{expected_version:flow.version,decision});
   onSaved(result.flow,decision==='yes'?'授权已登记 · 等待单独提交查询':'已选择暂不授权');message(result.note);
  }catch(cause){error.textContent=cause.message||'保存授权选择失败，请刷新后重试。';
   for(const item of actions.querySelectorAll('button'))item.disabled=false;button.focus();}
 };
 const yes=actionButton(existingGrant?'是，沿用这条授权':'是，登记本次授权',()=>decide('yes',yes));
 const no=el('button',existingGrant?'否，撤销授权并停止':'否，暂不授权','mf-button');no.type='button';no.onclick=()=>decide('no',no);
 actions.append(yes,no);section.append(error,actions);body.append(section);
}

export function appendQuerySubmission(flow,body,onSaved){
 const resultDescription=flow.execution_plan_preview?.output_description||
  (flow.output_preference==='admission_time'?'仅返回入院时间；不返回姓名或患者ID':'patient_token（HMAC-SHA256）');
 const section=el('section',undefined,'intake-result-confirmation');
 section.append(el('h3','授权已登记；查询仍未提交'));
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
