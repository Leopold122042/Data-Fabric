// Self-contained synthetic fixture; these rows are not institution records.
export const hospital=Array.from({length:24},(_,i)=>({visit_id:`V${String(i+1).padStart(3,'0')}`,patient_id:`P${String(i+1).padStart(3,'0')}`,month:i<12?'2026-09':'2026-10',dept:i%2?'呼吸内科':'急诊科',diagnosis:i%6===0?'J20.9':'J18.9'}));
export const claims=hospital.map((r,i)=>({claim_id:`C${i+1}`,visit_ref:r.visit_id,insured_id:`M${i+1}`,diagnosis:r.diagnosis==='J18.9'?'J18.900':'J20.900',amount:1000+i*100}));
export const research=hospital.map((r,i)=>({subject_id:`R${i+1}`,patient_ref:r.patient_id,cohort:r.diagnosis==='J18.9'?'PNEU-03':'OTHER',consented:i%8!==0}));
const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function caseTable(headers,rows){return `<div class="case-table-scroll"><table class="case-table"><thead><tr>${headers.map(h=>`<th>${escape(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(v=>`<td>${escape(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
export function computeCase(unitChanged=false){
 const cohort=hospital.filter(row=>row.diagnosis==='J18.9').map(row=>({...row,claim:claims.find(c=>c.visit_ref===row.visit_id),subject:research.find(s=>s.patient_ref===row.patient_id)})).filter(row=>row.claim&&row.subject?.consented&&row.subject.cohort==='PNEU-03');
 const groups=new Map();for(const row of cohort){const key=row.month+' / '+row.dept;if(!groups.has(key))groups.set(key,{month:row.month,dept:row.dept,patients:new Set(),amount:0});const g=groups.get(key);g.patients.add(row.patient_id);const sourceAmount=unitChanged?row.claim.amount*100:row.claim.amount;g.amount+=unitChanged?sourceAmount/100:sourceAmount;}
 return {cohort:cohort.length,rows:[...groups.values()].sort((a,b)=>(a.month+a.dept).localeCompare(b.month+b.dept)).map(g=>[g.month,g.dept,g.patients.size,g.patients.size>=5?g.amount.toFixed(2):'小样本抑制',g.patients.size>=5?'可交付':'人数少于 5，费用不输出'])};
}
export function caseIntro(){return `<section class="case-intro"><span>贯穿案例 · 合成数据现场计算</span><h2>肺炎队列跨源统计：从一句需求到可核对的交付结果</h2><p>“统计 2026 年 9—10 月已同意科研使用的肺炎患者，按月份和科室给出患者数及医保结算总额；每组至少 5 人，仅交付汇总。”</p><div class="case-badges"><b>医院 24 条就诊</b><b>医保 24 条结算</b><b>科研 24 条入组记录</b><b>同一案例贯穿五阶段</b></div><small>医院、医保、科研数据由本页独立合成；下面展示的匹配、计算和策略判断在浏览器内完成，实际平台入口保留在各阶段底部。</small></section>`;}
export function caseContent(stage,state){
 if(stage==='sources')return `<section class="case-detail"><h3>本步输入：三份不同结构的记录</h3><p>先核对源字段和数据对象约束。下表预览各源前 4 条；全部 72 条合成记录参与后续计算。</p><h4>医院 · diagnosis_record</h4>${caseTable(['就诊号','患者编号','月份','科室','诊断编码'],hospital.slice(0,4).map(r=>[r.visit_id,r.patient_id,r.month,r.dept,r.diagnosis]))}<h4>医保 · claim_header</h4>${caseTable(['结算号','医院就诊引用','医保身份','诊断编码','金额 / 元'],claims.slice(0,4).map(r=>[r.claim_id,r.visit_ref,r.insured_id,r.diagnosis,r.amount]))}<h4>科研 · study_subject</h4>${caseTable(['受试者号','医院患者引用','入组组别','同意科研使用'],research.slice(0,4).map(r=>[r.subject_id,r.patient_ref,r.cohort,r.consented?'是':'否']))}<p class="case-output">产出：3 个数据对象快照，记录主体“临床科研中心”、用途“肺炎病例队列统计”、到期日 2027-03-31；原始字段保留各自编码。</p></section>`;
 if(stage==='semantics')return `<section class="case-detail"><h3>本步核对：身份关联与业务口径</h3>${caseTable(['字段 / 代码','统一含义','证据与规则'],[['patient_id ↔ patient_ref','患者关联','合成场景显式引用映射；不按姓名合并'],['visit_id ↔ visit_ref','就诊与结算关联','就诊引用相等；一条结算归属一条就诊'],['J18.9 / J18.900 / PNEU-03','肺炎队列','场景词典、来源业务定义和诊断上下文共同限定'],['consented = true','科研使用范围','未同意记录排除，不进入统计'],['month + dept','统计分组','同一月份、同一科室内按患者编号去重']])}<p class="case-output">${state.semanticMatched?'已生成本案例映射：身份引用、诊断编码和统计口径已绑定。下一步将这些约束写入逻辑视图。':'点击上方“运行多证据匹配”生成本案例的映射结果。编码映射是本合成案例的约定词典。'}</p></section>`;
 if(stage==='virtual')return `<section class="case-detail"><h3>本步产出：受合同约束的跨源逻辑视图</h3><pre class="case-sql">VIEW LV-PNEUMONIA-MONTHLY v1
FROM hospital h
JOIN claims c ON h.visit_id = c.visit_ref
JOIN research r ON h.patient_id = r.patient_ref
WHERE h.diagnosis = 'J18.9' AND r.cohort = 'PNEU-03'
  AND r.consented = true
GROUP BY h.month, h.dept
OUTPUT COUNT(DISTINCT h.patient_id), SUM(c.amount)
POLICY subject + purpose + expires_at + minimum_group_size=5</pre>${caseTable(['现场操作','预期结果','核对点'],[['生成策略 → 合规访问','放行','主体、用途和期限匹配'],['跨用途访问：商业营销','拦截','用途不一致'],['超期访问：到期后请求','拦截','超过合同有效期'],['切换敏感等级','展示路径选择','汇总：源端聚合；健康信息：受控计算；标识：限制汇出']])}<p>以上为可审阅的逻辑视图规格；本页不提交生产 SQL，沙箱与联邦路径是调度决策演示。</p></section>`;
 if(stage==='orchestration'){const result=computeCase();return `<section class="case-detail"><h3>本步交付：可以逐项核对的计算结果</h3>${state.workflow.complete?`<p>72 条输入记录按显式引用连接；筛选后共有 <strong>${result.cohort} 名</strong>符合诊断和科研使用范围的患者。</p>${caseTable(['月份','科室','患者数','结算总额 / 元','交付检查'],result.rows)}<p class="case-output">交付字段仅保留月份、科室、人数和允许输出的费用汇总。小于 5 人的分组抑制费用；完整计算依据来自本页合成输入。</p>`:'<p>先完成字段映射和合同合规检查，再点击“确认需求并编排”。完成后这里展示由合成输入实际计算得到的分组结果。</p>'}</section>`;}
 return `<section class="case-detail"><h3>本步演示：金额单位变化如何影响结果</h3>${caseTable(['对象','变更前','变更后 / 处置'],[['claim_header.amount','元','源端改为分，数值放大 100 倍'],['逻辑视图费用汇总','直接 SUM(amount)','命中单位口径变化；暂停复用旧计算口径'],['受影响范围','费用总额','仅费用表达式调整为 SUM(amount / 100)'],['不受影响范围','患者数、诊断映射','不重做身份匹配和患者计数'],['版本回写','逻辑视图 v1','新单位适配草案 v2，待业务复核']])}${state.feedback.complete?`<p class="case-output">局部调整演示完成：将“分”除以 100 后重新汇总，结果与原始“元”口径一致。下方证据链可回看访问、拦截、执行和变更处置。</p>${caseTable(['月份','科室','患者数','归一后费用 / 元','复核结果'],computeCase(true).rows)}`:'<p>点击上方“注入变更并完成局部处置”，观察五步状态变化，再查询全程证据。</p>'}</section>`;
}
