"""Versioned, reviewed field semantics, bound to discovered source structure."""
import hashlib
import json
import re
from management.store import DomainError, now

# Local demo schema descriptions; these are proposals, never automatic approvals.
DEFINITIONS = {
'patient_id':('患者编号','来源系统中标识患者的编号；跨系统关联需另行确认身份关系','患者ID,患者号',''),
'name':('患者姓名','患者主索引登记的姓名；同名不能作为唯一身份依据','姓名',''),
'gender':('性别','患者主索引登记的性别编码或文本','性别',''),
'birth_date':('出生日期','患者登记的出生日期；年龄需结合基准日期计算','生日,出生日期',''),
'id_card':('身份证件号','患者登记的身份证件号码，属于个人标识','身份证号,证件号',''),
'insurance_type':('医保类型','患者登记的医疗保险类别','医保类别',''),
'visit_id':('就诊编号','标识一次就诊记录的编号；用于关联就诊与医嘱或检验申请','就诊号',''),
'vtype':('就诊类型','门诊或住院等就诊类别','就诊类别',''),
'dept':('科室','该业务记录所属的诊疗科室','科室,就诊科室',''),
'doctor':('医生','就诊记录中的接诊医生姓名','接诊医生',''),
'admission_time':('接诊或入院时间','本次就诊开始时间；结合就诊类型区分门诊接诊与住院入院','入院时间,就诊时间',''),
'diagnosis_code':('诊断编码','就诊诊断的编码；本地生成器使用 ICD-10 演示编码','诊断代码',''),
'diagnosis_name':('诊断名称','就诊记录中的诊断名称','诊断,疾病名称',''),
'chief_complaint':('主诉','患者就诊时陈述的主要症状与持续时间','主诉',''),
'order_id':('医嘱编号','标识一条医嘱记录的编号','医嘱号',''),
'otype':('医嘱类型','医嘱的业务类别，如西药、中成药、护理或材料','医嘱类别',''),
'item_name':('项目名称','该明细记录对应的业务项目名称','项目名',''),
'test_no':('检验申请单号','标识一张检验申请单并关联检验结果明细','检验单号',''),
'sample_type':('标本类型','检验申请记录中采集的标本类别','样本类型',''),
'collect_time':('标本采集时间','检验标本的采集时间','采样时间',''),
'report_time':('检验报告时间','检验申请单对应的报告时间','报告时间',''),
'result_id':('检验结果编号','标识一条检验项目结果明细','结果编号',''),
'item_code':('检验项目编码','检验项目的标识编码，如本地演示的 GLU、WBC','检验编码',''),
'value_num':('检验结果数值','检验项目的数值结果，须与同条记录的项目和单位一起解释','检验值,结果值','由 unit 字段指定'),
'unit':('检验单位','当前检验项目结果值的计量单位','计量单位',''),
'ref_range':('检验参考区间','当前检验项目记录的参考值范围；不作为诊断结论','参考范围',''),
'flag':('检验异常标志','检验结果与参考区间比较后的标志；具体编码由数据源确认','异常标记',''),
'study_uid':('影像检查唯一标识','标识一次影像检查的唯一编号','影像检查编号',''),
'modality':('影像检查模态','影像采集设备或技术类型，如 CT、MR、DR、US','检查类型,影像类型',''),
'body_part':('检查部位','影像检查对应的身体部位','影像部位',''),
'exam_date':('影像检查时间','影像检查记录的检查日期或时间','检查日期',''),
'accession_no':('影像申请编号','影像检查业务登记或申请编号','影像申请号',''),
'files':('影像文件数','本次影像检查关联的文件数量','文件数量','个'),
'size_mb':('影像文件大小','本次影像检查关联文件的大小数值','影像大小','MB（源端口径待确认）'),
'report_summary':('影像报告摘要','影像检查报告的文字摘要','影像报告',''),
'device_id':('设备编号','标识一台监测设备的编号','设备ID',''),
'device_type':('设备类型','监测设备的类别','设备类别',''),
'ward':('病区','设备登记所属的病区','病区',''),
'vital_id':('体征记录编号','标识一条生命体征采样记录','体征编号',''),
'ts':('体征采样时间','当前生命体征记录的采样时间','采样时间,监测时间',''),
'heart_rate':('心率','当前采样时刻记录的心率','心率,心跳','次/分钟'),
'spo2':('血氧饱和度','当前采样时刻记录的外周血氧饱和度','血氧,血氧饱和度','%'),
'temp':('体温','当前采样时刻记录的体温','体温,温度','℃'),
'auxiliary_examination':('辅助检查','病历中记载的辅助检查情况','辅助检查',''),
'physical_exam':('体格检查','病历中记载的体格检查内容','查体,体格检查',''),
'preliminary_diagnosis':('初步诊断','病历中记载的初步诊断意见','初诊,初步诊断',''),
'treatment_plan':('诊疗计划','病历中记载的后续诊疗安排','治疗计划,诊疗计划',''),
'present_illness_history':('现病史','病历中记载的本次疾病发生、发展及诊治经过','现病史',''),
}

def signature(field):
    payload={k:field.get(k) for k in ('source_id','table','name','structure','status')}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

class FieldLibrary:
    def __init__(self,store):self.store=store

    def sync(self,actor='system'):
        changed=[]
        with self.store.atomic():
            for field in self.store.list('object'):
                if field.get('kind')!='field':continue
                old=self.store.get('field_definition',field['id'])
                sig=signature(field)
                if old and old.get('source_signature')==sig:continue
                if not old:
                    if field.get('status')!='active':continue
                    name=field['name']; known=field.get('source_id') in ('his','lis','pacs','iot','emr')
                    label,meaning,aliases,unit=DEFINITIONS.get(name, (name, '', '', '')) if known else (name,'','','')
                    if name=='status' and known:
                        label='医嘱状态' if field['table']=='orders' else '检验申请状态'
                        meaning=label+'，具体状态值及流转规则需由来源负责人确认'
                    if name=='item_name' and known:
                        label='检验项目名称' if field['table']=='result' else '医嘱项目名称'
                        meaning=label+'；结合所属记录确定具体项目'
                    data={'field_id':field['id'],'label':label,'definition':field.get('meaning') or meaning,
                          'aliases':list(dict.fromkeys([label]+aliases.split(','))) if aliases else [label],
                          'unit':unit,'notes':'','basis':'现有目录说明' if field.get('meaning') else '本地演示结构与生成器草案' if known else '新字段，待业务负责人补充定义',
                          'status':'draft'}
                else:
                    data={**old,'status':'retired' if field.get('status')!='active' else 'review_required',
                          'confirmed_by':None,'confirmed_at':None,'confirmation_note':None}
                data.update(source_signature=sig,source_id=field.get('source_id'),table=field.get('table'),name=field['name'],data_type=field.get('structure',{}).get('type'))
                changed.append(self.store.save('field_definition',field['id'],data,old['version'] if old else 0,actor,'发现字段或源结构变更，等待定义确认'))
        return changed

    def items(self):
        fields={x['id']:x for x in self.store.list('object')}
        result=[]
        for row in self.store.list('field_definition'):
            field=fields.get(row['id'])
            status=row['status']
            if not field or field.get('status')!='active':status='retired'
            elif row.get('source_signature')!=signature(field):status='review_required'
            result.append({**row,'status':status})
        return result

    def save(self,oid,body,actor,confirm=False):
        with self.store.atomic():
            field=self.store.require('object',oid)
            old=self.store.require('field_definition',oid)
            if field.get('status')!='active' or field.get('kind')!='field':raise DomainError('仅可定义有效字段',409)
            if old.get('source_signature')!=signature(field):raise DomainError('源结构已变更，请同步字段库后重新审核',409)
            data=dict(old)
            if not confirm:
                for key in ('label','definition','unit','notes','basis'):
                    value=body.get(key,old.get(key,''))
                    if not isinstance(value,str) or len(value)>4000:raise DomainError('字段定义应为不超过4000字的文本')
                    data[key]=value.strip()
                aliases=body.get('aliases',old.get('aliases',[]))
                if not isinstance(aliases,list) or len(aliases)>50 or any(not isinstance(x,str) or len(x)>100 for x in aliases):raise DomainError('别名应为最多50项的文本列表')
                data.update(aliases=list(dict.fromkeys(x.strip() for x in aliases if x.strip())),status='draft',confirmed_by=None,confirmed_at=None,confirmation_note=None)
            else:
                note=body.get('confirmation_note')
                if not isinstance(note,str) or not note.strip():raise DomainError('请填写确认依据')
                if not data.get('label') or not data.get('definition') or not data.get('basis'):raise DomainError('请先补充中文名称、业务定义和定义依据')
                data.update(status='confirmed',confirmed_by=actor,confirmed_at=now(),confirmation_note=note.strip())
            return self.store.save('field_definition',oid,data,body.get('expected_version'),actor,'人工确认字段定义' if confirm else '编辑字段定义，等待确认')

    def confirmed(self):return {r['id']:r for r in self.items() if r['status']=='confirmed'}

    def match(self,text):
        if not isinstance(text,str) or not text.strip():raise DomainError('请输入需求或字段描述')
        q=text.casefold();hits=[]
        for row in self.confirmed().values():
            terms=[row['label'],row['name'],*row.get('aliases',[])]
            matched=sorted({x for x in terms if x and x.casefold() in q},key=len,reverse=True)
            if matched:hits.append({**row,'matched_terms':matched,'match_reason':'命中已确认名称或别名：'+ '、'.join(matched)})
        return {'items':sorted(hits,key=lambda x:len(x['matched_terms'][0]),reverse=True), 'note':'候选保留来源表；同名字段需结合业务上下文确认，不自动执行查询。'}
