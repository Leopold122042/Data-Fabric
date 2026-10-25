import math
from .matching import rank,expression_type
from management.store import DomainError, new_id


def family(dtype):
    dtype=str(dtype).lower()
    if any(x in dtype for x in ('int','float','real','decimal','numeric','double')):return 'number'
    if any(x in dtype for x in ('char','text','str')):return 'text'
    if 'bool' in dtype:return 'boolean'
    if 'date' in dtype or 'time' in dtype:return 'time'
    return 'unknown'


class Semantic:
    def __init__(self,store):self.store=store

    def concept(self,body,actor):
        for k in ('name','meaning','domain','data_type'):
            if not str(body.get(k,'')).strip():raise DomainError('概念缺少：'+k)
        for key in ('aliases','context_keywords'):
            if key in body and (not isinstance(body[key],list) or any(not isinstance(v,str) or not v.strip() for v in body[key])):raise DomainError(key+'必须为非空字符串列表')
        bounds=body.get('range',{}) or {}
        if not isinstance(bounds,dict):raise DomainError('取值范围必须为对象')
        for key in ('min','max','min_length','max_length'):
            if key in bounds and (isinstance(bounds[key],bool) or not isinstance(bounds[key],(int,float)) or not math.isfinite(bounds[key])):raise DomainError('范围边界必须为有限数字')
        for lo,hi in [('min','max'),('min_length','max_length')]:
            if lo in bounds and hi in bounds and bounds[lo]>bounds[hi]:raise DomainError('范围下界不能大于上界')
        if body.get('parent_id'):

            parent=self.store.require('concept',body['parent_id'])
            if not parent.get('aggregate_only'):raise DomainError('请先将父概念设置为归集概念')
        oid=body.get('id') or new_id('CON')
        if body.get('aggregate_only') and any(m['concept_id']==oid and m.get('status')=='confirmed' for m in self.store.list('mapping')):
            raise DomainError('请先停用父概念的直接映射，再拆分子概念')
        return self.store.save('concept',oid,body,body.get('expected_version',0),actor,'概念定义')

    def evidence(self,concept,fields):
        veto=[];unknown=[]
        expected=family(concept['data_type'])
        for f in fields:
            actual=family(f.get('structure',{}).get('type','unknown'))
            if f.get('status')!='active':veto.append(f['name']+'：字段已删除')
            if expected!='unknown' and actual!='unknown' and expected!=actual:veto.append(f['name']+'：类型不兼容')
            if 'unknown' in (actual,expected):unknown.append(f['name']+'：类型证据不足')
            a,b=f.get('profile',{}),concept.get('range',{}) or {}
            if a.get('max') is not None and b.get('min') is not None and a['max']<b['min']:veto.append(f['name']+'：取值范围不重叠')
            if a.get('min') is not None and b.get('max') is not None and a['min']>b['max']:veto.append(f['name']+'：取值范围不重叠')
            if a.get('max_length') is not None and b.get('min_length') is not None and a['max_length']<b['min_length']:veto.append(f['name']+'：文本长度范围不重叠')
            if a.get('min_length') is not None and b.get('max_length') is not None and a['min_length']>b['max_length']:veto.append(f['name']+'：文本长度范围不重叠')
        return veto,unknown

    def candidates(self,cid):
        c=self.store.require('concept',cid);rows=[]
        if c.get('aggregate_only'):raise DomainError('请先选择业务对象下的具体属性概念，如客户编号、机构名称或指标值')
        objects=self.store.list('object');tables={o['id']:o for o in objects if o.get('kind')=='table'}
        for f in objects:
            if f.get('kind')!='field' or f.get('status')!='active':continue
            veto,unknown=self.evidence(c,[f])
            table=tables.get(f.get('parent_id'),{})
            peers=[o['name'] for o in objects if o.get('parent_id')==f.get('parent_id')]
            score,coverage,components=rank(c,f,table,peers,family)
            if not components['values']['available']:unknown.append('缺少可比较的取值剖析；请运行字段剖析或外部元数据检测')
            if not components['context']['available']:unknown.append('缺少业务说明、业务域或上下文关键词')
            rows.append({'field_id':f['id'],'field_version':f['version'],'concept_version':c['version'],'name':f['name'],'source':f['source_id'],'table':f['table'],
                         'score':0 if veto else score,'coverage':coverage,'components':components,
                         'veto':veto,'uncertainty':unknown,'status':'rejected' if veto else 'needs_confirmation',
                         'score_note':'规则匹配分，不代表统计概率；所有映射需人工确认'})
        rows.sort(key=lambda x:(bool(x['veto']),-x['score'],x['field_id']))
        viable=[r for r in rows if not r['veto']]
        for row in viable:
            rivals=[r['score'] for r in viable if r is not row and r['source']==row['source'] and r['table']==row['table']]
            margin=row['score']-max(rivals,default=0)
            row['margin']=round(margin,3)
            if row['score']>=.8 and row['coverage']>=.8 and margin>=.1 and not row['uncertainty']:row['status']='suggested'
            elif margin<.1:row['uncertainty'].append('同表候选接近或存在更优候选，需人工辨别')
        return rows

    def mapping(self,body,actor):
        c=self.store.require('concept',body.get('concept_id',''))
        if c.get('aggregate_only'):raise DomainError('归集概念不能直接映射字段')
        fields=[self.store.require('object',f) for f in body.get('fields',[])]
        if not fields or any(f.get('kind')!='field' for f in fields):raise DomainError('请选择有效字段')
        kind=body.get('mapping_type')
        if kind not in ('direct','conditional','derived','join'):raise DomainError('映射类型必须为direct/conditional/derived/join')
        if kind=='direct' and len(fields)!=1:raise DomainError('直接映射只能有一个字段')
        expression=body.get('expression')
        if kind!='direct' and not isinstance(expression,dict):raise DomainError('复杂映射需结构化表达式，不接收可执行代码')
        if kind=='conditional' and not expression.get('predicate'):raise DomainError('条件映射缺少predicate')
        if kind=='derived' and not expression.get('operator'):raise DomainError('派生映射缺少operator')
        if kind=='join' and (len({(f['source_id'],f['table']) for f in fields})<2 or not expression.get('on')):raise DomainError('跨表映射需两张表和on关联条件')
        if kind=='join':
            declared={f['id']:f for f in fields};pairs=expression['on']
            if not isinstance(pairs,list) or not pairs:raise DomainError('关联条件必须为字段对列表')
            for pair in pairs:
                if not isinstance(pair,list) or len(pair)!=2 or any(not isinstance(k,str) or k not in declared for k in pair):raise DomainError('关联字段必须在所选字段中声明')
                left,right=(declared[k] for k in pair)
                if (left['source_id'],left['table'])==(right['source_id'],right['table']):raise DomainError('关联条件必须跨表')
                types={family(x.get('structure',{}).get('type','unknown')) for x in (left,right)}-{'unknown'}
                if len(types)>1:raise DomainError('关联键类型不兼容')
        output_fields=fields
        if kind=='join':
            output_id=expression.get('field',fields[0]['id'])
            output_fields=[f for f in fields if f['id']==output_id]
            if not output_fields:raise DomainError('跨表输出字段必须包含在声明字段中')
        elif kind in ('derived','conditional'):
            from execution.query.expressions import mapping_expression,compile_expression
            compile_expression(mapping_expression(body),{f['id']:f['id'] for f in fields},[])
            result_type=expression_type(mapping_expression(body),{f['id']:f for f in fields},family)
            if result_type!='unknown' and family(c['data_type']) not in ('unknown',result_type):raise DomainError('表达式输出类型与概念不匹配')
            # Expressions may consume condition/join fields of a different type.
            output_fields=[]
        veto,unknown=self.evidence(c,output_fields)
        if any(f.get('status')!='active' for f in fields):veto.append('包含已删除字段')
        if kind in ('derived','conditional'):unknown.append('复杂映射输出类型及取值范围需人工验证')
        if veto:raise DomainError('；'.join(veto))
        status=body.get('status','draft')
        if status not in ('draft','confirmed','disabled'):raise DomainError('映射状态非法')
        if status=='confirmed' and not str(body.get('confirmation_note','')).strip():raise DomainError('确认映射必须填写复核依据')
        data={**body,'concept_version':c['version'],'field_versions':{f['id']:f['version'] for f in fields},
              'status':status,'uncertainty':unknown,'confirmed_by':actor if status=='confirmed' else None}
        item=self.store.save('mapping',body.get('id') or new_id('MAP'),data,body.get('expected_version',0),actor,'映射复核')
        if status=='confirmed':
            for f in fields:
                rid=c['id']+':'+f['name'];old=self.store.get('mapping_rule',rid)
                self.store.save('mapping_rule',rid,{'concept_id':c['id'],'field_name':f['name'],'type':f.get('structure',{}).get('type'),'mapping_id':item['id']},old['version'] if old else 0,actor,'复用已确认映射')
        return item

    def resolve(self,cid):
        c=self.store.require('concept',cid)
        if c.get('aggregate_only'):raise DomainError('归集概念不可执行')
        mappings=[m for m in self.store.list('mapping') if m['concept_id']==cid and m.get('status')=='confirmed']
        if not mappings:raise DomainError('没有已确认映射',409)
        for m in mappings:
            if m['concept_version']!=c['version']:raise DomainError('概念版本已变化，映射需复核',409)
            for oid,v in m['field_versions'].items():
                obj=self.store.require('object',oid)
                if obj['version']!=v or obj.get('status')!='active':raise DomainError('字段版本已变化，映射需复核',409)
        return {'concept':c,'mappings':mappings,'environment':self.store.environment}

    def import_legacy(self):
        from semantics import FIELD_MAP,ENTITY_TYPES
        fields={(o['source_id'],o.get('table'),o['name']):o for o in self.store.list('object') if o['kind']=='field' and o['status']=='active'}
        imported=0
        for src,table,col,entity,attr in FIELD_MAP:
            field=fields.get((src,table,col))
            if not field:continue
            cid='CON-legacy-'+entity+'-'+col
            if not self.store.get('concept',cid):
                self.concept({'id':cid,'name':attr,'meaning':ENTITY_TYPES[entity]['desc']+' / '+attr,
                    'domain':'医疗演示','data_type':field.get('structure',{}).get('type') or 'unknown'},'migration')
            mid='MAP-legacy-'+src+'-'+table+'-'+col
            if not self.store.get('mapping',mid):
                self.mapping({'id':mid,'concept_id':cid,'fields':[field['id']],'mapping_type':'direct','status':'draft'},'migration')
                imported+=1
        return {'imported':imported,'status':'draft','note':'保留原映射来源，人工确认后供新接口解析'}
