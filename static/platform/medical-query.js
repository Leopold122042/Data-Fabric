import {api,element,table,status} from './common.js';

const $=selector=>document.querySelector(selector);
const run=$('#medicalRemoteRun');
const queryStatus=$('#medicalRemoteStatus');
const resultHost=$('#medicalRemoteResults');
const evidenceHost=$('#medicalRemoteEvidence');

run?.addEventListener('click',async()=>{
  run.disabled=true;
  queryStatus.textContent='正在调用查询服务并读取所选来源…';
  resultHost.replaceChildren();
  evidenceHost.replaceChildren();
  try{
    const source_ids=[...$('#medical-source-choices').querySelectorAll('input:checked')].map(x=>x.value);
    if(!source_ids.length)throw new Error('请至少选择一个医疗数据源。');
    const response=await api('/api/data-services/SVC-MEDICAL-REMOTE-QUERY/invoke',{
      params:{source_ids,sql:$('#medicalRemoteSql').value},
      purpose:'research',
      cross_org:false
    });
    if(response.error)throw new Error(response.error);
    const result=response.result||{};
    resultHost.replaceChildren(table(result.columns||[],result.rows||[]));
    const sources=result.sources||[];
    evidenceHost.replaceChildren(table(
      ['来源','服务端','文件位置','载入行数','读取字节','SHA-256'],
      sources.map(x=>[x.source_name,x.provider,x.location,x.rows_loaded,x.bytes_read,x.sha256])
    ));
    queryStatus.textContent=`服务 ${response.service_id} · 源端读取 ${sources.length} 个来源 · 返回 ${(result.rows||[]).length} 行${result.truncated?'（已截断到 200 行）':''} · 服务耗时 ${response.latency_ms} ms · SQL ${result.elapsed_ms} ms${result.fetched_at?' · '+result.fetched_at:''}`;
    status('六源医疗数据查询完成；来源摘要与服务计量已返回。');
  }catch(error){
    resultHost.replaceChildren();
    evidenceHost.replaceChildren();
    queryStatus.textContent='查询未完成：'+error.message;
    status(error.message,true);
  }finally{
    run.disabled=false;
  }
});
