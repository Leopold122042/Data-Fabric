import {api,message} from './ui.js';

const el=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
const ROLE={output:'输出',filter:'筛选',group:'分组',time:'时间',join:'关联',metric:'指标',unknown:'待确认'};
const STATUS={suggested:'字段库定义已确认 · 最优推荐',ambiguous:'有相近候选，请核对选择',missing:'字段库没有匹配项',
 definition_unconfirmed:'字段定义尚未确认',low_confidence:'语义证据不足',sql_missing:'SQL没有覆盖该字段',model_missing:'模型没有拆出字段'};
const TEXT_REQUIRED=new Set(['missing','model_missing','sql_missing']);
const RESPONSE_LABEL={
 missing:'补充该字段的业务含义、数据来源或实际字段名称',
 model_missing:'说明本次查询需要返回、筛选、分组或统计什么',
 sql_missing:'补充本次查询的筛选条件、统计口径和输出方式',
};
const RESPONSE_PLACEHOLDER={
 missing:'请说明字段含义、数据来源或实际字段名称。',
 model_missing:'请说明本次查询需要返回、筛选、分组或统计什么。',
 sql_missing:'例如：按姓名精确筛选，只返回性别；如需统计，请说明统计口径。',
};

export function appendSemanticFieldMatches(flow,body,onSaved){
 const bindings=flow.field_bindings||[];
 if(!bindings.length)return;
 const section=el('section',undefined,'intake-result-confirmation');
 section.append(el('h3','字段语义匹配结果'));
 section.append(el('p','字段定义统一在字段库审核，不在每次查询重复确认定义。本次查询仍会展示最优推荐和相近候选，最优项默认勾选；你可以保留推荐、改选或多选。患者唯一标识按目录规则自动加入。'));
 const pending=bindings.filter(item=>item.status!=='suggested');
 const controls=[];
 if(flow.draft?.field_selection_error){
  section.append(el('p',flow.draft.field_selection_error,'intake-result-blocker'));
 }
 for(const binding of bindings){
  const card=el('article',undefined,'intake-flow-step');
  card.append(el('strong',`需求字段“${binding.phrase||'未识别'}” · ${ROLE[binding.role]||binding.role} · ${STATUS[binding.status]||binding.status}`));
  if(binding.meaning)card.append(el('p',`模型解析的含义：${binding.meaning}`));
  const candidates=binding.candidates||[];
  const pendingBinding=binding.status!=='suggested';
  const hasAlternatives=candidates.length>1&&!binding.selection_locked;
  const needsResponse=pendingBinding||hasAlternatives;
  let checkboxes=[];
  if(candidates.length){
   const chosen=binding.selected_field_ids?.length?binding.selected_field_ids:
    binding.selected_field_id?[binding.selected_field_id]:
    binding.suggested_field_id?[binding.suggested_field_id]:[candidates[0].field_id];
   if(needsResponse&&!binding.selection_locked){
    const group=document.createElement('fieldset');group.className='intake-semantic-options';
    const legend=document.createElement('legend');legend.textContent='本次查询字段（可多选；最优推荐默认勾选）';group.append(legend);
    candidates.forEach((candidate,index)=>{
     const label=document.createElement('label');label.className='intake-semantic-option';
     const input=document.createElement('input');input.type='checkbox';input.value=candidate.field_id;
     input.checked=chosen.includes(candidate.field_id);input.dataset.intentId=binding.intent_id;
     const title=document.createElement('span');title.className='intake-semantic-option-title';
     title.textContent=`${index===0?'最优推荐（默认勾选）':'相近候选'}：${candidate.table_name||candidate.table||''}.${candidate.field_label||candidate.field_name}`;
     const detail=document.createElement('span');detail.className='intake-semantic-option-detail';
     detail.textContent=`${candidate.definition||'暂无定义'} · 字段库${candidate.definition_status==='confirmed'?'已确认':'待确认'} · 匹配分 ${candidate.score}`;
     label.append(input,title,detail);group.append(label);checkboxes.push(input);
    });
    card.append(group);
   }else{
    const selected=candidates.find(item=>item.field_id===binding.suggested_field_id)||candidates[0];
    if(selected)card.append(el('p',`${binding.selection_locked?'固定加入':'最优推荐'}：${selected.table_name||selected.table}.${selected.field_name} · ${selected.definition||'暂无定义'} · 字段库定义${selected.definition_status==='confirmed'?'已确认':'待确认'}`,'intake-flow-candidate'));
   }
  }
  const requiresText=TEXT_REQUIRED.has(binding.status)||!candidates.length;
  let answerInput=null;
  if(binding.question){
   const questionText=binding.status==='ambiguous'
    ?`“${binding.phrase||'该字段'}”存在相近候选，系统首选已默认勾选。可保留首选、改选其他候选或多选；系统会按勾选结果检查字段关联。`
    :binding.question;
   card.append(el('p',questionText,'intake-result-blocker'));
   if(binding.status==='definition_unconfirmed'){
    const link=el('a','打开字段库确认定义');link.href='/static/management/field-library.html';link.target='_blank';link.rel='noopener';
    card.append(link);
   }
  }
  if(requiresText){
   const label=el('label',RESPONSE_LABEL[binding.status]||`补充“${binding.phrase||'该字段'}”的业务含义或查询口径`);
   answerInput=document.createElement('textarea');answerInput.rows=2;answerInput.maxLength=1000;
   answerInput.placeholder=RESPONSE_PLACEHOLDER[binding.status]||'请写明字段含义、业务口径或查询条件。';
   answerInput.dataset.intentId=binding.intent_id;
   label.append(answerInput);card.append(label);
  }
  if(needsResponse)controls.push({binding,checkboxes,answerInput,requiresText});
  section.append(card);
 }
 if(controls.length){
  if(pending.length){
   const queryPlanOnly=pending.every(item=>item.status==='sql_missing');
   section.append(el('p',queryPlanOnly
    ?'字段已匹配；查询计划还需补充筛选、统计口径或输出方式。'
    :'仍有字段匹配或查询说明需要补充；完成字段选择和必要说明后才会继续。','intake-result-blocker'));
  }
  const error=el('p',undefined,'intake-confirm-error');error.setAttribute('role','alert');
  const submit=el('button','按本次选择重新匹配','mf-button mf-button-primary');submit.type='button';submit.disabled=true;
  const selectionFor=control=>control.checkboxes.filter(input=>input.checked).map(input=>input.value);
  function syncSubmit(){
   submit.disabled=controls.some(control=>{
    const hasSelection=selectionFor(control).length>0;
    const hasText=Boolean(control.answerInput?.value.trim());
    return (control.checkboxes.length>0&&!hasSelection)||(control.checkboxes.length===0&&control.requiresText&&!hasText)
      ||(control.requiresText&&control.checkboxes.length>0&&!hasText);
   });
  }
  controls.forEach(control=>{
   control.checkboxes.forEach(input=>input.addEventListener('change',syncSubmit));
   control.answerInput?.addEventListener('input',syncSubmit);
  });
  submit.onclick=async()=>{
   syncSubmit();if(submit.disabled)return;submit.disabled=true;error.textContent='正在按你的字段选择重新匹配并检查关联条件…';
   try{
    const result=await api(`/intake/flows/${flow.id}/clarify`,{expected_version:flow.version,
     answers:controls.map(control=>({intent_id:control.binding.intent_id,
      selected_field_ids:selectionFor(control),answer:control.answerInput?.value.trim()||''}))});
    message(result.note||'已按勾选结果重新匹配字段');onSaved(result.flow,'字段选择后的匹配结果');
   }catch(cause){error.textContent=cause.message||'重新匹配失败，请刷新后重试。';syncSubmit();}
  };
  section.append(error,submit);syncSubmit();
 }
 body.append(section);
}
