import {enterPage,currentAccess,denyPage} from '/static/shared/access.js?v=identity-20261111';
import {api,element,table,link,status} from './common.js?v=identity-20261111';
if(await enterPage('overview')){
 if(currentAccess().principal.role!=='platform')denyPage('医疗数据统计目前仅对平台方开放，请通过自己的需求和授权结果查看数据。');
 else{
  async function refresh(){
   const b=document.querySelector('#medical-refresh');b.disabled=true;
   try{const data=await api('/api/medical-overview'),body=document.querySelector('#medical-body');body.replaceChildren();
    function panel(title,description){const p=element('section','','mf-panel');p.style.padding='20px';p.style.marginBottom='16px';p.append(element('h2',title),element('p',description,'mf-muted'));body.append(p);return p;}
    panel('科室数据分布','患者在科室内去重。检验与影像按科室患者关联，不代表科室绩效。').append(table(['科室','患者 / 人','就诊 / 次','关联异常检验 / 项','关联影像 / 项'],data.dept_distribution.map(r=>[r.name,r.patients,r.visits,r.abnormal_items,r.studies])));
    panel('诊断分布','HIS 非空诊断，按就诊次数排序的前 8 项。').append(table(['诊断','就诊次数'],data.top_diags.map(r=>[r.name,r.n])),link('进入患者检索','/static/platform/patients.html'));
    panel('来源记录规模','不同来源的记录粒度不同，不相加为患者数。扫描明细与资源结构在资源目录中查看。').append(table(['来源','记录数'],data.sources.map(r=>[r.name,r.rows])),link('查看资源目录','/static/platform/resources.html'));
    status('数据已更新 · '+new Date().toLocaleTimeString());
   }catch(error){status(error.message,true);}finally{b.disabled=false;}
  }
  document.querySelector('#medical-refresh').onclick=refresh;await refresh();
 }
}
