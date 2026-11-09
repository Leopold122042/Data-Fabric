"""Auditable, non-executable demand workflow proposals and gated preparation."""
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from management.store import DomainError, new_id
from management.intake.ollama import explicitly_mentions_cost

CAPABILITY_LABELS = {
    'catalog_search': '检索平台目录',
    'connected_source_discovery': '检查已连接数据源结构',
    'data_query': '查询数据',
    'trend_analysis': '分析指标趋势',
}


def normalized_field_output_ids(constraints):
    values=constraints.get('field_outputs')
    if values is None and constraints.get('field_output'):
        values=[constraints['field_output']]
    return set(values or [])


def validate_query_draft(sql, catalog):
    if sql is None:return {'status':'missing','errors':['模型未提供SQL草案；需补充查询意图和可用字段。'],'normalized_sql':None}
    try:
        import sqlglot
        from sqlglot import exp
        parsed=sqlglot.parse(sql,read='duckdb')
    except Exception:
        return {'status':'rejected','errors':['SQL无法解析。'],'normalized_sql':None}
    if len(parsed)!=1 or not isinstance(parsed[0],(exp.Select,exp.Union,exp.Intersect,exp.Except)):
        return {'status':'rejected','errors':['只允许单条只读SELECT查询。'],'normalized_sql':None}
    tree=parsed[0]
    selects=[tree] if isinstance(tree,exp.Select) else list(tree.find_all(exp.Select))
    # A SQL table must resolve to a registered physical/logical table, not a field name.
    allowed_tables={str(x.get('table') or x.get('name') or '').lower() for x in catalog
                    if x.get('kind') in ('table','connected_external_table')}
    tables={t.name.lower() for t in tree.find_all(exp.Table)}
    if not tables or not tables<=allowed_tables:
        return {'status':'rejected','errors':['SQL引用了目录之外或尚未匹配的逻辑表。'],'normalized_sql':None}
    if any(isinstance(item,(exp.Star,exp.Column)) and (isinstance(item,exp.Star) or item.is_star)
           for select in selects for item in select.expressions):
        return {'status':'rejected','errors':['禁止SELECT *，请明确列出输出字段。'],'normalized_sql':None}
    allowed_columns={str(column).lower() for x in catalog
                     if str(x.get('table') or x.get('name') or '').lower() in tables
                     for column in (x.get('columns') or [])}
    allowed_columns|={str(x.get('name') or '').lower() for x in catalog
                      if x.get('kind')=='field' and str(x.get('table') or '').lower() in tables}
    columns={c.name.lower() for c in tree.find_all(exp.Column) if c.name}
    if not columns<=allowed_columns:
        return {'status':'rejected','errors':['SQL引用了目录中不存在的字段。'],'normalized_sql':None}
    limit=tree.args.get('limit')
    if limit:
        value=limit.expression
        try:
            if int(value.this)>200:return {'status':'rejected','errors':['查询行数上限为200。'],'normalized_sql':None}
        except (AttributeError,TypeError,ValueError):return {'status':'rejected','errors':['LIMIT必须为不超过200的整数。'],'normalized_sql':None}
    else:
        tree=tree.limit(200)
    return {'status':'valid_pending_human_confirmation','errors':[],
            'normalized_sql':tree.sql(dialect='duckdb')}


class IntakeFlow:
    def __init__(self, runtime):
        self.runtime=runtime
        self.store=runtime.management.store

    def _catalog_metadata(self, external_catalog=()):
        # Give the local model structure metadata only. Never inspect source rows.
        from management.catalog.field_library import FieldLibrary
        definitions=FieldLibrary(self.store).confirmed()
        candidates=[]
        for row in self.store.list('object'):
            if row.get('status')=='deleted':continue
            structure=row.get('structure') or {}
            candidates.append({'id':row['id'],'version':row.get('version'),'kind':row.get('kind','object'),'name':row.get('name'),'table':row.get('table'),
                'parent_id':row.get('parent_id'),
                'source_id':row.get('source_id'),'meaning':row.get('meaning'),'domain':row.get('domain'),
                'data_type':row.get('data_type') or structure.get('type'),'status':row.get('status'),
                'classification':row.get('classification'),'classification_basis':row.get('classification_basis'),
                'columns':row.get('columns') or structure.get('columns',[])})
        for candidate in candidates:
            definition=definitions.get(candidate['id'])
            if definition:
                candidate.update(meaning=definition['definition'],field_label=definition['label'],
                    aliases=definition.get('aliases',[]),unit=definition.get('unit',''),
                    semantic_baseline={'label':definition['label']},
                    definition_status='confirmed',definition_version=definition['version'])
        for row in self.store.list('service'):

            candidates.append({'id':row['id'],'kind':'service','name':row.get('name'),'description':row.get('description'),
                'purpose':row.get('purpose'),'output':row.get('output'),'lifecycle':row.get('lifecycle')})
        for row in external_catalog:
            candidates.append({'id':row['id'],'kind':'connected_external_table','name':row.get('name'),
                'table':row['id'],'source_id':'connected-cloud','columns':row.get('columns',[]),'types':row.get('types',[])})
        return candidates[:300]

    @staticmethod
    def _is_wechat_index_request(text):
        request=str(text or '').casefold()
        return any(term in request for term in ('微信指数','wechat index','weixin index','weixin_index'))

    @staticmethod
    def _wechat_index_metadata_match(resource):
        markers=('微信指数','wechat index','weixin index','weixin_index','wx_index','wxindex')
        metadata=' '.join(str(resource.get(key) or '') for key in
            ('name','table','meaning','domain','description','purpose','output'))
        metadata+=' '+' '.join(str(value) for value in resource.get('columns',[]))
        metadata+=' '+' '.join(str(value) for value in resource.get('types',[]))
        return any(marker in metadata.casefold() for marker in markers)

    @staticmethod
    def _filter_unrelated_wechat_index_resources(text, draft, catalog):
        """Fail closed when a WeChat-index request only received unrelated catalog matches."""
        if not IntakeFlow._is_wechat_index_request(text):
            return draft
        by_id={str(row.get('id')):row for row in catalog}
        recommendations=draft.get('resource_recommendations') or []
        relevant=[]
        for recommendation in recommendations:
            resource=by_id.get(str(recommendation.get('id')))
            if not resource:
                continue
            if IntakeFlow._wechat_index_metadata_match(resource):
                relevant.append(recommendation)
        if recommendations and len(relevant)==len(recommendations):
            return draft
        result=dict(draft)
        result['resource_recommendations']=relevant
        result['uncertain_fields']=list(dict.fromkeys((draft.get('uncertain_fields') or [])+
            ['平台登记目录中没有找到明确标注为微信指数的数据源或字段；已排除无关候选，需登记/扫描数据源后重新检索。']))
        if not relevant:
            # A model-generated query is not meaningful until a real source and fields match.
            result['query_draft']=None
        return result

    def _source_connection_status(self, candidate, external_catalog=()):
        """Resolve metadata-source availability without opening or reading source rows."""
        source_id=str(candidate.get('source_id') or '')
        table_id=str(candidate.get('table') or candidate.get('id') or '')
        external_ids={str(row.get('id')) for row in external_catalog if row.get('id')}
        if table_id in external_ids:
            return 'connected'
        if source_id=='connected-cloud':return 'connected' if external_ids else 'cloud'
        if source_id in ('his','lis','pacs','iot'):
            import medsim
            return 'connected' if (Path(medsim.DATA_DIR)/(source_id+'.db')).is_file() else 'unavailable'
        if source_id=='emr':
            import medsim
            emr_path=Path(medsim.DATA_DIR)/'emr'
            return 'connected' if emr_path.is_dir() and any(emr_path.glob('*.json')) else 'unavailable'
        if source_id.startswith('cloud-'):
            return 'cloud'
        registration=self.store.get('source',source_id) if source_id else None
        registry=getattr(self.runtime.management.catalog,'sources',None)
        if registration and registry and registration.get('connector') in registry.factories:
            try:
                return 'connected' if registry.resolve_path(registration.get('path','')).is_file() else 'unavailable'
            except (DomainError, OSError, ValueError):
                return 'unavailable'
        return 'unknown'

    def _person_id_lookup(self, text, catalog):
        match=re.search(r'(?:查询|查找|搜索|检索|获取|找出|查看)\s*([\u4e00-\u9fff]{2,4})\s*的\s*(?:(?:患者|病人|住院|病案)\s*)?(?:patient[_\s-]*id|id|编号|标识|号码)',
                        str(text or ''),re.I)
        if not match:return None
        person_name=match.group(1)
        tables=[row for row in catalog if row.get('kind')=='table' and row.get('status')=='active'
                and {'name','patient_id'}<={str(column).casefold() for column in row.get('columns',[])}]
        tables.sort(key=lambda row:(row.get('source_id')!='his',row.get('name','')))
        for table in tables:
            name_field=next((row for row in catalog if row.get('kind')=='field' and row.get('table')==table.get('table')
                             and str(row.get('name','')).casefold()=='name'),None)
            id_field=next((row for row in catalog if row.get('kind')=='field' and row.get('table')==table.get('table')
                           and str(row.get('name','')).casefold()=='patient_id'),None)
            if not name_field or not id_field:continue
            safe_name=person_name.replace("'","''")
            sql=f"SELECT patient_id FROM {table.get('table') or table['name']} WHERE name = '{safe_name}' LIMIT 200"
            reason='目录规则匹配到同时含 name 和 patient_id 的表，可按姓名精确查找并返回患者ID。'
            return {'name':person_name,'table':table,'name_field':name_field,'id_field':id_field,
                    'sql':sql,'reason':reason,'kind':'pseudonymized_id'}
        return None

    def _has_confirmed_patient_id_link(self,left_id,right_id):
        expected={left_id,right_id}
        for relation in self.store.list('relation'):
            if relation.get('type')!='same_patient_id' or relation.get('status')!='confirmed':continue
            if {relation.get('source'),relation.get('target')}!=expected:continue
            current={left_id:self.store.get('object',left_id),right_id:self.store.get('object',right_id)}
            versions=relation.get('field_versions') or {}
            if any(not item or item.get('status')!='active'
                   or (versions.get(field_id) is not None and item.get('version')!=versions[field_id])
                   for field_id,item in current.items()):continue
            return True
        return False

    def _person_multi_field_lookup(self,text,catalog,draft=None):
        """Resolve every explicitly requested person attribute when the request names several."""
        text=str(text or '')
        fields=[row for row in catalog if row.get('kind')=='field' and row.get('status')=='active']
        tables=[row for row in catalog if row.get('kind')=='table' and row.get('status')=='active']
        table_by_id={row.get('id'):row for row in tables}
        source_labels={'emr':('电子病历系统','电子病历','病历文书','病案库'),
            'his':('医院信息系统','HIS'),'lis':('检验信息系统','LIS'),
            'pacs':('影像归档系统','PACS'),'iot':('院内监测设备','IoT')}
        parse_text=text
        source_markers=[marker for row in tables for marker in
            (row.get('source_id'),row.get('name'),row.get('table'),
             *source_labels.get(str(row.get('source_id') or '').casefold(),()))
            if isinstance(marker,str) and len(marker.strip())>=2]
        for marker in sorted(set(source_markers),key=len,reverse=True):
            parse_text=re.sub(re.escape(marker),' ',parse_text,flags=re.I)
        parse_text=re.sub(r'^(?:(?:我想|我要|我需要|我在|我正在)\s*)?(?:请问|请|帮我查一下|麻烦查一下|查找|查看|查一下|查询|搜索|检索|获取|找出|帮我|麻烦|查|找)\s*','',parse_text)
        request=re.search(r'(?P<name>[\u4e00-\u9fff]{2,4})\s*的\s*(?P<targets>.+?)\s*$',parse_text)
        if not request:return None
        targets=[item.strip(' \t，,、。.!！?？:：') for item in re.split(
            r'\s*(?:以及|并且|同时|和|与|及|、|，|,|&|\+)\s*',request.group('targets'))]
        targets=[item for item in targets if item]
        if len(targets)<2:return None

        aliases={
            'id':('patient_id',),'患者id':('patient_id',),'病人id':('patient_id',),
            'patientid':('patient_id',),'patient_id':('patient_id',),
            '患者编号':('patient_id',),'病人编号':('patient_id',),'患者标识':('patient_id',),
            '科室':('dept','department'),'就诊科室':('dept','department'),
            '部门':('dept','department'),'department':('department','dept'),'dept':('dept','department'),
            '性别':('gender','sex'),'gender':('gender','sex'),'sex':('gender','sex'),
            '生日':('birth_date','date_of_birth','dob'),
            '出生日期':('birth_date','date_of_birth','dob'),
        }
        def normalized(value):
            return re.sub(r'[\s_-]+','',str(value or '')).casefold()
        canonical={normalized(key):value for key,value in aliases.items()}
        recommended={item.get('id') for item in (draft or {}).get('resource_recommendations',[])
            if item.get('id')}
        requested=[]
        for target in targets:
            names=canonical.get(normalized(target))
            if not names:return None
            semantic_tokens={key for key,field_names in canonical.items()
                if any(name.casefold() in {value.casefold() for value in field_names} for name in names)}
            candidates=[]
            for field in fields:
                if field.get('parent_id') not in table_by_id:continue
                field_name=str(field.get('name','')).casefold()
                terms=[field_name,*field.get('aliases',[]),field.get('field_label',''),field.get('meaning','')]
                baseline=field.get('semantic_baseline')
                if isinstance(baseline,dict):terms.extend(value for value in baseline.values() if isinstance(value,str))
                elif isinstance(baseline,str):terms.append(baseline)
                if field_name in names or any(normalized(term) in semantic_tokens for term in terms if term):
                    candidates.append(field)
            requested.append((target,names,candidates))

        name_names={'name','patient_name','fullname','full_name','姓名','患者姓名'}
        def name_binding(target_table):
            target_fields=[row for row in fields if row.get('parent_id')==target_table.get('id')]
            name=next((row for row in target_fields if str(row.get('name','')).casefold() in name_names),None)
            if name:return name,None
            target_key=next((row for row in target_fields
                if str(row.get('name','')).casefold()=='patient_id'),None)
            if not target_key:return None,None
            related=[]
            for candidate in tables:
                if candidate.get('id')==target_table.get('id'):continue
                candidate_fields=[row for row in fields if row.get('parent_id')==candidate.get('id')]
                candidate_name=next((row for row in candidate_fields
                    if str(row.get('name','')).casefold() in name_names),None)
                candidate_key=next((row for row in candidate_fields
                    if str(row.get('name','')).casefold()=='patient_id'),None)
                same_source=candidate.get('source_id')==target_table.get('source_id')
                linked=(candidate_key and not same_source and
                    self._has_confirmed_patient_id_link(target_key['id'],candidate_key['id']))
                if candidate_name and candidate_key and (same_source or linked):
                    related.append((candidate,candidate_name))
            return (related[0][1],related[0][0]) if len(related)==1 else (None,None)

        # The current governed field-query plan supports one target table and, when
        # needed, one patient table for exact-name filtering. Choose a table that
        # contains all requested outputs before considering separate table matches.
        from itertools import product
        combinations=[]
        if all(candidates for _,_,candidates in requested):
            for selected in product(*(entry[2] for entry in requested)):
                target_ids={field.get('parent_id') for field in selected}
                if len(target_ids)!=1:continue
                target_table=table_by_id[next(iter(target_ids))]
                name_field,join_table=name_binding(target_table)
                if not name_field:continue
                if join_table and name_field.get('parent_id')!=join_table.get('id'):continue
                connection=self._source_connection_status(target_table)
                recommendation_penalty=sum(field.get('id') not in recommended for field in selected)
                combinations.append(((recommendation_penalty,connection!='connected',target_table.get('source_id')!='his',
                    target_table.get('name',''),tuple(field.get('name','') for field in selected)),
                    selected,target_table,name_field,join_table))
        if not combinations:
            if any(not candidates for _,_,candidates in requested):
                missing=[target for target,_,candidates in requested if not candidates]
                return {'kind':'field_gap','name':request.group('name'),'targets':targets,
                    'missing':missing,'reason':'目录没有为所有请求属性找到字段；未生成部分字段查询。'}
            return {'kind':'field_gap','name':request.group('name'),'targets':targets,
                'missing':targets,'reason':'目录无法通过姓名字段和已登记患者关联同时定位全部请求字段；未生成部分字段查询。'}
        combinations.sort(key=lambda item:item[0])
        _,selected,table,name_field,join_table=combinations[0]
        # Preserve the user's requested order while avoiding duplicate projections.
        selected_fields=list({field['id']:field for field in selected}.values())
        person_name=request.group('name')
        safe_name=person_name.replace("'","''")
        quote=lambda value:'"'+str(value).replace('"','""')+'"'
        table_name=table.get('table') or table.get('name')
        if join_table:
            left=next((field for field in fields if field.get('parent_id')==table.get('id')
                and str(field.get('name','')).casefold()=='patient_id'),None)
            right=next((field for field in fields if field.get('parent_id')==join_table.get('id')
                and str(field.get('name','')).casefold()=='patient_id'),None)
            if not left or not right:
                return {'kind':'field_gap','name':person_name,'targets':targets,'missing':targets,
                    'reason':'目录未能唯一确认姓名表与输出表的patient_id关联；未生成查询。'}
            sql=(f"SELECT {', '.join('t.'+quote(field.get('name')) for field in selected_fields)} "
                 f"FROM {quote(join_table.get('table') or join_table.get('name'))} p "
                 f"JOIN {quote(table_name)} t ON p.{quote(right.get('name'))} = t.{quote(left.get('name'))} "
                 f"WHERE p.{quote(name_field.get('name'))} = '{safe_name}' LIMIT 200")
        else:
            sql=(f"SELECT {', '.join(quote(field.get('name')) for field in selected_fields)} "
                 f"FROM {quote(table_name)} WHERE {quote(name_field.get('name'))} = '{safe_name}' LIMIT 200")

        output_labels=[]
        for target,_,candidates in requested:
            field=next((item for item in selected if item in candidates),None)
            output_labels.append(f'“{target}”→{table.get("name")}.{field.get("name")}')
        reason='目录规则已匹配全部请求字段：'+'；'.join(output_labels)+'；按姓名精确筛选。'
        resources=[]
        def add_resource(item,why):
            if item and not any(value['id']==item['id'] for value in resources):
                resources.append({'id':item['id'],'reason':why})
        add_resource(table,reason)
        for field in selected_fields:add_resource(field,reason)
        add_resource(name_field,'仅用于姓名精确筛选，不会作为输出字段。')
        join_fields=[]
        if join_table:
            add_resource(join_table,'按目录中patient_id等值关联到姓名字段所属表。')
            for field in fields:
                if field.get('parent_id') in (table.get('id'),join_table.get('id')) and str(field.get('name','')).casefold()=='patient_id':
                    join_fields.append(field)
                    add_resource(field,'仅用于已登记表之间的patient_id关联，不作为额外输出。')
        return {'kind':'field_value','name':person_name,'table':table,'field':selected_fields[0],
            'fields':selected_fields,'name_field':name_field,'sql':sql,'target_phrase':'、'.join(targets),
            'join_table':join_table,'join_fields':join_fields,'resources':resources,'reason':reason}

    def _person_field_lookup(self,text,catalog,draft=None):
        """Resolve a person's requested output field from catalog names and semantics."""
        multi=self._person_multi_field_lookup(text,catalog,draft)
        if multi:return multi
        text=str(text or '')
        aliases={'性别':('gender','sex'),'gender':('gender','sex'),'sex':('gender','sex'),
                 '科室':('dept','department'),'就诊科室':('dept','department'),
                 '部门':('dept','department'),'department':('department','dept'),
                 'dept':('dept','department'),
                 '生日':('birth_date','date_of_birth','dob'),
                 '出生日期':('birth_date','date_of_birth','dob'),
                 '病情':('diagnosis_name','preliminary_diagnosis','diagnosis'),
                 '疾病':('diagnosis_name','preliminary_diagnosis','diagnosis'),
                 '诊断':('diagnosis_name','preliminary_diagnosis','diagnosis')}
        fields=[row for row in catalog if row.get('kind')=='field' and row.get('status')=='active']
        tables=[row for row in catalog if row.get('kind')=='table' and row.get('status')=='active']
        recommendations={item.get('id'):item.get('reason','') for item in
            (draft or {}).get('resource_recommendations',[]) if item.get('id')}
        recommended=set(recommendations)
        table_by_id={row.get('id'):row for row in tables}
        source_labels={'emr':('电子病历系统','电子病历','病历文书','病案库'),'his':('医院信息系统','HIS'),
            'lis':('检验信息系统','LIS'),'pacs':('影像归档系统','PACS'),'iot':('院内监测设备','IoT')}
        request_fold=text.casefold()
        def explicitly_requested(table):
            source=str(table.get('source_id') or '').casefold()
            hints=[source,table.get('name',''),table.get('table',''),*source_labels.get(source,())]
            return any(str(hint).casefold() in request_fold for hint in hints if len(str(hint).strip())>=2)
        request_without_source=text
        source_markers=[marker for row in tables for marker in
            (row.get('source_id'),row.get('name'),row.get('table'),*source_labels.get(str(row.get('source_id') or '').casefold(),()))
            if isinstance(marker,str) and len(marker.strip())>=2]
        for marker in sorted(set(source_markers),key=len,reverse=True):
            request_without_source=re.sub(re.escape(marker),' ',request_without_source,flags=re.I)
        parse_text=re.sub(r'[()（）\[\]【】<>《》,，。.!！?？:：\s]+$','',request_without_source)
        parse_text=re.sub(r'^(?:(?:我想|我要|我需要|我在|我正在)\s*)?(?:请问|请|帮我查一下|麻烦查一下|查一下|帮我|麻烦|查询|查找|搜索|检索|获取|找出|查看)\s*','',parse_text)
        def name_binding(target_table):
            target_fields=[row for row in fields if row.get('parent_id')==target_table.get('id')]
            name=next((row for row in target_fields if str(row.get('name','')).casefold()
                in ('name','patient_name','fullname','full_name','姓名','患者姓名')),None)
            if name:return name,None
            target_key=next((row for row in target_fields
                if str(row.get('name','')).casefold()=='patient_id'),None)
            if not target_key:return None,None
            related=[]
            for candidate in tables:
                if candidate.get('id')==target_table.get('id'):
                    continue
                candidate_fields=[row for row in fields if row.get('parent_id')==candidate.get('id')]
                candidate_name=next((row for row in candidate_fields if str(row.get('name','')).casefold()
                    in ('name','patient_name','fullname','full_name','姓名','患者姓名')),None)
                candidate_key=next((row for row in candidate_fields
                    if str(row.get('name','')).casefold()=='patient_id'),None)
                same_source=candidate.get('source_id')==target_table.get('source_id')
                confirmed_cross_source=(candidate_key and not same_source and
                    self._has_confirmed_patient_id_link(target_key['id'],candidate_key['id']))
                if candidate_name and candidate_key and (same_source or confirmed_cross_source):
                    related.append((candidate,candidate_name))
            return (related[0][1],related[0][0]) if len(related)==1 else (None,None)
        matches=[]
        cleaned=parse_text
        entity_attribute=re.search(
            r'([\u4e00-\u9fff]{2,4})\s*(?:的\s*|(?:得了|患有|患上)\s*)([\u4e00-\u9fffA-Za-z_]{1,24})\s*$',cleaned)
        target_phrase=entity_attribute.group(2).strip() if entity_attribute else None
        requested_name=entity_attribute.group(1).strip() if entity_attribute else None
        for field in fields:
            table=table_by_id.get(field.get('parent_id'))
            if not table:continue
            name_field,join_table=name_binding(table)
            phrases=list(field.get('aliases') or [])
            field_name=str(field.get('name') or '').strip()
            meaning=str(field.get('meaning') or '').strip()
            if meaning:phrases.append(meaning)
            if re.search(r'[\u4e00-\u9fff]',field_name):phrases.append(field_name)
            for phrase,field_names in aliases.items():
                if field_name.casefold() in {name.casefold() for name in field_names}:phrases.append(phrase)
            if field.get('semantic_baseline'):
                baseline=field['semantic_baseline']
                phrases.extend([str(value) for value in baseline.values() if isinstance(value,str)]
                    if isinstance(baseline,dict) else [str(baseline)])
            for phrase in sorted(set(phrases),key=len,reverse=True):
                if not phrase or phrase.casefold() not in text.casefold():continue
                start=parse_text.casefold().find(phrase.casefold());prefix=parse_text[:start]
                prefix=re.sub(r'^(?:(?:我想|我要|我需要|我在|我正在)\s*)?(?:请问|请|帮我查一下|麻烦查一下|查一下|帮我|麻烦|查询|查找|搜索|检索|获取|找出|查看)\s*','',prefix)
                name_match=re.search(r'([\u4e00-\u9fff]{2,4})\s*(?:的)?\s*$',prefix)
                person_name=requested_name or (name_match.group(1).rstrip('的') if name_match else None)
                if not person_name:continue
                matches.append((field,table,name_field,person_name,phrase,field.get('id') in recommended,join_table))
                break
        # A model-selected field is usable only when its catalog recommendation explains
        # the requested attribute. The selected field and SQL remain subject to rule and
        # human review before any plan can be created.
        for field in fields:
                if field.get('id') not in recommendations:continue
                table=table_by_id.get(field.get('parent_id'))
                if not table:continue
                name_field,join_table=name_binding(table)
                reason=str(recommendations[field['id']]).casefold()
                semantic=[str(field.get('name','')),str(field.get('meaning','')),*field.get('aliases',[])]
                baseline=field.get('semantic_baseline')
                if isinstance(baseline,dict):semantic.extend(str(value) for value in baseline.values() if isinstance(value,str))
                elif baseline:semantic.append(str(baseline))
                semantic.extend(alias for alias,names in aliases.items()
                    if any(str(field.get('name','')).casefold()==name.casefold() for name in names))
                model_target=target_phrase
                model_name=requested_name
                if not model_target:
                    for name_length in (3,2,4):
                        if len(cleaned)<=name_length:continue
                        proposed_name=cleaned[:name_length].strip()
                        proposed_target=cleaned[name_length:].strip(' 的，。！？?')
                        if (re.fullmatch(r'[\u4e00-\u9fff]{2,4}',proposed_name)
                                and len(proposed_target)>=2 and proposed_target.casefold() in reason):
                            model_name=proposed_name;model_target=proposed_target;break
                if not model_name or not model_target:continue
                explains_target=model_target.casefold() in reason or any(
                    value and value.casefold() in reason for value in semantic)
                if not explains_target:continue
                if any(existing[0].get('id')==field.get('id') and existing[1].get('id')==table.get('id')
                       for existing in matches):continue
                matches.append((field,table,name_field,model_name,model_target,True,join_table))
        # Prefer direct metadata matches, then the model's explicit field/table match,
        # and finally the platform's HIS catalog when the evidence is otherwise equal.
        # An available catalog source outranks an unavailable model recommendation.
        matches.sort(key=lambda item:(not explicitly_requested(item[1]),
            self._source_connection_status(item[1])!='connected',not item[5],not bool(item[2]),
            item[1].get('source_id')!='his',str(item[1].get('name','')),
            str(item[0].get('name',''))))
        # Match diagnosis attributes in a related visit/record table. The
        # patient's name is resolved through the registered patient_id relation.
        if entity_attribute and any(word in entity_attribute.group(2) for word in ('什么病','疾病','诊断','病情')):
            patient_tables=[t for t in tables if t.get('source_id')==target_table.get('source_id')
                and any(f.get('parent_id')==t.get('id')
                and str(f.get('name','')).casefold()=='name' for f in fields)
                and any(f.get('parent_id')==t.get('id') and str(f.get('name','')).casefold()=='patient_id'
                    for f in fields)]
            diagnosis_rank={'diagnosis_name':0,'preliminary_diagnosis':1,'diagnosis':2,'diagnosis_code':3}
            for field in sorted(fields,key=lambda f:diagnosis_rank.get(str(f.get('name','')).casefold(),99)):
                fname=str(field.get('name','')).casefold()
                if fname not in diagnosis_rank:continue
                target_table=table_by_id.get(field.get('parent_id'))
                if not target_table or target_table.get('id') in {t.get('id') for t in patient_tables}:continue
                target_key=next((f for f in fields if f.get('parent_id')==target_table.get('id')
                    and str(f.get('name','')).casefold()=='patient_id'),None)
                patient=next((t for t in patient_tables if target_key),None)
                if not patient or not requested_name:continue
                name_field=next(f for f in fields if f.get('parent_id')==patient.get('id')
                    and str(f.get('name','')).casefold()=='name')
                if any(existing[0].get('id')==field.get('id') and existing[1].get('id')==target_table.get('id')
                       for existing in matches):continue
                matches.append((field,target_table,name_field,requested_name,entity_attribute.group(2),
                    field.get('id') in recommended,patient))
                break
        matches.sort(key=lambda item:(not explicitly_requested(item[1]),
            self._source_connection_status(item[1])!='connected',not item[5],
            item[1].get('id') not in recommended,not bool(item[2]),item[1].get('source_id')!='his',
            str(item[1].get('name','')),str(item[0].get('name',''))))
        if not matches:return None
        best=matches[0]
        field,table,name_field,person_name,phrase,_,join_table=best
        if not name_field:
            return {'kind':'catalog_candidate','name':person_name,'table':table,'field':field,
                'name_field':None,'join_table':join_table,'target_phrase':phrase,
                'reason':f"已匹配最接近目录字段 {table.get('name')}.{field.get('name')}；该表没有可用的姓名字段或已确认患者关联，暂不能生成查询。"}
        safe_name=person_name.replace("'","''")
        quote=lambda value:'"'+str(value).replace('"','""')+'"'
        if join_table:
            left=next(f for f in fields if f.get('parent_id')==table.get('id') and str(f.get('name','')).casefold()=='patient_id')
            right=next(f for f in fields if f.get('parent_id')==join_table.get('id') and str(f.get('name','')).casefold()=='patient_id')
            sql=(f"SELECT t.{quote(field.get('name'))} FROM {quote(join_table.get('table') or join_table.get('name'))} p "
                 f"JOIN {quote(table.get('table') or table.get('name'))} t ON p.{quote(right.get('name'))} = t.{quote(left.get('name'))} "
                 f"WHERE p.{quote(name_field.get('name'))} = '{safe_name}' LIMIT 200")
        else:
            sql=f"SELECT {quote(field.get('name'))} FROM {quote(table.get('table') or table.get('name'))} WHERE {quote(name_field.get('name'))} = '{safe_name}' LIMIT 200"
        return {'kind':'field_value','name':person_name,'table':table,'field':field,
            'name_field':name_field,'sql':sql,'target_phrase':phrase,
            'join_table':join_table,
            'reason':f"目录字段匹配“{phrase}”→ {table.get('name')}.{field.get('name')}；按姓名精确筛选，只输出该字段。"}

    def _admission_time_lookup(self, text, catalog):
        match=re.search(r'(?:查询|查找|搜索|检索|获取|找出|查看)\s*([\u4e00-\u9fff]{2,4}?)\s*(?:的\s*)?入院时间',
                        str(text or ''))
        if not match:return None
        person_name=match.group(1)
        tables=[row for row in catalog if row.get('kind')=='table' and row.get('status')=='active']
        patient=next((row for row in tables if {'name','patient_id'}<={str(c).casefold() for c in row.get('columns',[])}),None)
        visit=next((row for row in tables if {'patient_id','admission_time','vtype'}<={str(c).casefold() for c in row.get('columns',[])}
                    and row.get('source_id')==patient.get('source_id')) if patient else None,None)
        if not patient or not visit:return None
        def field(table,name):
            return next((row for row in catalog if row.get('kind')=='field' and row.get('status')=='active'
                         and row.get('parent_id')==table.get('id')
                         and str(row.get('name','')).casefold()==name.casefold()),None)
        name_field=field(patient,'name');patient_key=field(patient,'patient_id')
        visit_key=field(visit,'patient_id');admission_field=field(visit,'admission_time');visit_type=field(visit,'vtype')
        if not all((name_field,patient_key,visit_key,admission_field,visit_type)):return None
        safe_name=person_name.replace("'","''")
        sql=(f"SELECT v.admission_time FROM {visit.get('table') or visit['name']} AS v "
             f"JOIN {patient.get('table') or patient['name']} AS p ON v.patient_id = p.patient_id "
             f"WHERE p.name = '{safe_name}' AND v.vtype = '住院' LIMIT 200")
        return {'name':person_name,'patient_table':patient,'visit_table':visit,'name_field':name_field,
                'patient_key':patient_key,'visit_key':visit_key,'admission_field':admission_field,
                'visit_type_field':visit_type,'sql':sql}

    def repair_admission_time_query(self, flow, actor):
        """Rebuild an exact-name admission-time draft from table/field metadata only."""
        if (flow.get('output_preference')=='admission_time' and flow.get('auto_prepare_admission_time_plan')
                and flow.get('spec_id') and not flow.get('execution_plan_id')
                and flow.get('execution_output_blocker')):
            confirmed_spec=self.store.get('spec',flow['spec_id'],flow.get('spec_version'))
            if confirmed_spec and confirmed_spec.get('confirmed'):
                # Migrate flows blocked by the former blanket health-output rule.
                # This only compiles registered metadata; it never reads source rows.
                return self._prepare_admission_time_plan(flow,confirmed_spec,actor)
        if (flow.get('state') not in ('completed','waiting_manual_spec_confirmation')
                or flow.get('confirmation',{}).get('confirmed') is not True
                or flow.get('spec_id') or flow.get('execution_plan_id')):return flow
        catalog=self._catalog_metadata()
        lookup=self._admission_time_lookup(flow.get('request_text'),catalog)
        if not lookup:return flow
        demand=self.store.require('demand',flow['demand_id'])
        constraints={'scope':{'kind':'rows','filters':[
            {'field':lookup['name_field']['id'],'op':'=','value':lookup['name']},
            {'field':lookup['visit_type_field']['id'],'op':'=','value':'住院'}], 'all_rows':False},
            'time_field':lookup['admission_field']['id'],'output_mode':'object',
            'circulation_modes':['controlled_compute'],'quality_min_complete':1,
            'max_rows':200,'min_group_size':5}
        object_scope=(f"按患者姓名精确匹配 {lookup['name']}，关联患者与就诊记录，筛选住院记录并返回入院时间；"
                      '姓名、患者ID仅用于匹配和关联，不作为交付字段。')
        proposal={**demand,'expected_version':demand['version'],'object_scope':object_scope,
            'delivery':'对象级','execution_constraints':constraints}
        needs_update=any(demand.get(key)!=proposal.get(key)
                         for key in ('object_scope','delivery','execution_constraints'))
        updated=(self.runtime.management.intake.apply_draft(flow['demand_id'],
            {'expected_version':demand['version'],'proposal':proposal,'model':flow.get('model')},actor)['demand']
            if needs_update else demand)
        validation=validate_query_draft(lookup['sql'],catalog)
        from execution.security.output_policy import field_classification
        classification=field_classification(lookup['admission_field'])
        classification_blocker=None if classification!='unknown' else (
            f"目录字段“{lookup['visit_table'].get('name')}.admission_time”分类为“{lookup['admission_field'].get('classification') or '未登记'}”，"
            '请确认是否按“健康信息”登记。')
        execution_blocker=None if classification in ('public','internal','unknown','health') else (
            f"字段已分类为“{lookup['admission_field'].get('classification')}”；当前规则不允许直接交付该类原始字段。"
            '如需调整，请先复核字段分类或输出方式。')
        source_status=self._source_connection_status(lookup['visit_table'])
        resources=[]
        previous={item.get('id'):item for item in flow.get('resources',[])}
        for table in (lookup['patient_table'],lookup['visit_table']):
            accepted=previous.get(table['id'],{}).get('review_status')=='human_confirmed'
            resources.append({**table,'review_status':'human_confirmed' if accepted else
                'model_recommended_pending_human_confirmation',
                'reason':'字段目录确认该表参与患者姓名与入院时间查询。'})
        before_constraints=demand.get('execution_constraints') or {}
        old_time_id=before_constraints.get('time_field')
        old_time=next((item for item in catalog if item.get('id')==old_time_id),None)
        adjustments=[
            {'label':'数据表','before':', '.join(item.get('name','') for item in flow.get('resources',[])) or '未匹配',
             'after':f"{lookup['patient_table'].get('name')} + {lookup['visit_table'].get('name')}（按 patient_id 关联）"},
            {'label':'时间字段','before':f"{old_time.get('table')}.{old_time.get('name')}" if old_time else '未匹配',
             'after':f"{lookup['visit_table'].get('name')}.admission_time"},
            {'label':'查询范围','before':'全部记录',
             'after':f"姓名精确匹配 {lookup['name']}；就诊类型=住院"},
            {'label':'输出方式','before':before_constraints.get('output_mode') or '未设置',
             'after':'对象级；仅返回入院时间'},
            {'label':'数据时间范围','before':'未变更',
             'after':f"{updated.get('time_start')} 至 {updated.get('time_end')}（沿用当前需求窗口）"}
        ]
        if flow.get('auto_adjusted_spec'):
            history=self.store.history('intake_flow',flow['id'])
            original=next((item.get('spec_adjustments') for item in reversed(history)
                           if item.get('auto_adjusted_spec') and item.get('spec_adjustments')),[])
            original_before={item.get('label'):item.get('before') for item in original}
            for item in adjustments:
                if item.get('label') in original_before:
                    item['before']=original_before[item['label']]
        steps=[
            {'id':'step-1','capability':'catalog_search','label':CAPABILITY_LABELS['catalog_search'],
             'reason':'根据字段元数据将错误的 patient.birth_date 候选修正为 visit.admission_time，并补充 patient 与 visit 的 patient_id 关联。',
             'depends_on':[],'status':'completed','candidates':resources,
             'detail':'目录已找到姓名、住院类型和入院时间字段；仅 visit 表候选需要在当前窗口确认。'},
            {'id':'step-2','capability':'connected_source_discovery','label':CAPABILITY_LABELS['connected_source_discovery'],
             'reason':'检查 patient 与 visit 表所属本地医疗数据源连接状态。','depends_on':['step-1'],
             'status':'completed' if source_status=='connected' else 'blocked',
             'connection_kind':None if source_status=='connected' else 'local',
             'detail':'HIS 数据源已连接，沿用当前连接；无需重新登录，也未读取源数据行。' if source_status=='connected'
                      else 'HIS 数据源当前不可用，无法继续。'},
            {'id':'step-3','capability':'data_query','label':CAPABILITY_LABELS['data_query'],
             'reason':'按姓名和住院类型筛选，并使用入院时间作为数据时间字段。','depends_on':['step-2'],
             'status':'waiting_manual_spec_confirmation' if validation['status']=='valid_pending_human_confirmation' and source_status=='connected' else 'blocked',
             'detail':('入院时间属于健康信息；规格确认后由需求方提交原值交付申请，平台方最终审批；批准后仍需单独提交查询。数据方不参与审批。'
                       if classification=='health' else
                       '字段分类已确认；规格确认后按输出规则复核，未授权或执行查询。'
                       if execution_blocker else
                       '已生成并通过目录规则校验的只读关联查询；确认规格后自动建立固定方案草案。')}]
        state='waiting_manual_spec_confirmation' if steps[1]['status']=='completed' and steps[2]['status']=='waiting_manual_spec_confirmation' else 'attention'
        note='已自动把出生日期字段改为住院记录的入院时间，增加姓名精确筛选和 patient_id 关联，并将汇总输出改为仅返回入院时间。'
        revised_draft={**flow.get('draft',{}),'object_scope':object_scope,'delivery':'对象级',
            'execution_constraints':constraints,'query_draft':lookup['sql'],
            'time_start':updated.get('time_start'),'time_end':updated.get('time_end'),
            'purpose':updated.get('purpose'),'quality':updated.get('quality'),'deadline':updated.get('deadline'),
            'uncertain_fields':[item for item in flow.get('draft',{}).get('uncertain_fields',[])
                                if item not in ('入院时间',)]}
        changes={'steps':steps,'resources':resources,'query_draft':lookup['sql'],
            'query_validation':validation,'draft':revised_draft,'demand_version':updated['version'],
            'state':state,'auto_adjusted_spec':True,'auto_prepare_admission_time_plan':True,
            'spec_adjustments':adjustments,'query_output_blocker':classification_blocker,
            'execution_output_blocker':execution_blocker,
            'output_preference':'admission_time','draft_revision_note':note,
            'notes':list(flow.get('notes',[]))+([note] if note not in flow.get('notes',[]) else [])}
        if all(flow.get(key)==value for key,value in changes.items()):return flow
        return self.store.save('intake_flow',flow['id'],{**flow,**changes},flow['version'],actor,
            '按字段目录自动修正入院时间查询草案；等待人工确认必要调整')

    def _complete_query_intent(self, text, draft, catalog):
        query_intent=bool(re.search(r'查询|查找|搜索|检索|获取|找出|查看|统计|分析',str(text or '')))
        lookup=self._person_id_lookup(text,catalog) or self._person_field_lookup(text,catalog,draft)
        if lookup:query_intent=True
        if not query_intent and not lookup:return draft,None
        proposed={item.get('capability'):dict(item) for item in draft.get('capability_steps',[])
                  if item.get('capability')}
        defaults={
            'catalog_search':{'reason':'按已登记目录元数据寻找匹配的表和字段。','inputs':[]},
            'connected_source_discovery':{'reason':'检查匹配表所属数据源是否已连接；已连接来源直接沿用。','inputs':[]},
            'data_query':{'reason':'在目录候选和执行范围确认后准备受控查询。','inputs':[]},
        }
        for capability,value in defaults.items():proposed.setdefault(capability,{'capability':capability,**value})
        draft['capability_steps']=list(proposed.values())
        if lookup and lookup.get('kind')=='field_gap':
            note=lookup.get('reason') or '目录没有同时匹配到所有请求字段；未生成部分字段查询。'
            draft['query_draft']=None;draft['execution_constraints']={}
            draft['resource_recommendations']=[]
            draft['uncertain_fields']=list(dict.fromkeys((draft.get('uncertain_fields') or [])+[note]))
            proposed['catalog_search']['reason']=note
            proposed['connected_source_discovery']['reason']='全部目标字段匹配前不选择或连接数据源。'
            proposed['data_query']['reason']='所有请求字段尚未同时匹配；不会只查询其中一部分。'
            draft['capability_steps']=list(proposed.values())
            return draft,lookup
        if lookup and lookup.get('kind')=='pseudonymized_id':
            table=lookup['table'];name_field=lookup['name_field']
            draft['title']=draft.get('title') or f"查询{lookup['name']}的患者ID"
            draft['object_scope']=f"在 {table.get('name')} 表按姓名精确匹配；不按日期筛选；只输出患者伪名ID。"
            draft['delivery']='对象级'
            draft['query_draft']=lookup['sql']
            draft['execution_constraints']={'scope':{'kind':'lookup','filters':[
                {'field':name_field['id'],'op':'=','value':lookup['name']}]},
                'time_field':None,'output_mode':'object','identifier_output':'pseudonymized',
                'circulation_modes':['controlled_compute'],'quality_min_complete':1,'max_rows':200,'min_group_size':5}
            draft['resource_recommendations']=[{'id':table['id'],'reason':lookup['reason']}]
            draft['uncertain_fields']=[item for item in draft.get('uncertain_fields',[])
                                       if not re.search(r'(?:具体.*id|患者标识字段)',str(item),re.I)]
            proposed['catalog_search']['reason']='已用目录规则补全可按姓名匹配 patient_id 的表。'
            proposed['connected_source_discovery']['reason']=(
                f"检查 {table.get('name')} 表所属 {table.get('source_id') or '已连接'} 数据源连接状态。")
            proposed['data_query']['reason']='按姓名精确匹配并只返回 HMAC 患者伪名ID。'
            draft['capability_steps']=list(proposed.values())
        elif lookup and lookup.get('kind')=='catalog_candidate':
            table=lookup['table'];field=lookup['field']
            draft['title']=draft.get('title') or f"查询{lookup['name']}的{lookup['target_phrase']}"
            draft['object_scope']=f"最接近目录候选为 {table.get('name')}.{field.get('name')}；患者姓名与该来源的身份关联尚未确认。"
            draft['delivery']='对象级';draft['query_draft']=None;draft['execution_constraints']={}
            draft['catalog_match_resolution']={'status':'needs_patient_linkage',
                'table_id':table['id'],'field_id':field['id'],'source_id':table.get('source_id'),
                'target_phrase':lookup['target_phrase'],'reason':lookup['reason']}
            draft['resource_recommendations']=[{'id':table['id'],'reason':lookup['reason']},
                {'id':field['id'],'reason':lookup['reason']}]
            proposed['catalog_search']['reason']=lookup['reason']
            proposed['connected_source_discovery']['reason']=f"确认 {table.get('source_id')} 来源目录和连接状态；已返回最接近的字段候选。"
            proposed['data_query']['reason']='字段候选已找到，但患者姓名无法通过当前已确认的目录关系定位；不会猜测关联或生成SQL。'
            draft['capability_steps']=list(proposed.values())
        elif lookup:
            table=lookup['table'];field=lookup['field'];name_field=lookup['name_field']
            output_fields=lookup.get('fields') or [field]
            output_names=[item.get('meaning') or item.get('name') for item in output_fields]
            draft['title']=(f"查询{lookup['name']}的{lookup['target_phrase']}" if len(output_fields)>1
                else draft.get('title') or f"查询{lookup['name']}的{lookup['target_phrase']}")
            draft['object_scope']=f"在 {table.get('name')} 表按姓名精确匹配 {lookup['name']}；输出目录字段 {'、'.join(output_names)}。"
            draft['delivery']='对象级';draft['query_draft']=lookup['sql']
            output_ids=[item['id'] for item in output_fields]
            output_binding=({'field_output':output_ids[0]} if len(output_ids)==1
                else {'field_outputs':output_ids})
            draft['execution_constraints']={'scope':{'kind':'lookup','filters':[
                {'field':name_field['id'],'op':'=','value':lookup['name']}]},
                'time_field':None,'output_mode':'object',**output_binding,
                'circulation_modes':['controlled_compute'],'quality_min_complete':1,
                'max_rows':200,'min_group_size':5}
            recommendations=lookup.get('resources') or []
            if not recommendations:
                for item,reason in ((table,lookup['reason']),(field,lookup['reason']),
                        (name_field,'仅用于姓名精确筛选，不会作为输出字段。')):
                    if not any(row.get('id')==item.get('id') for row in recommendations):
                        recommendations.append({'id':item['id'],'reason':reason})
            draft['resource_recommendations']=recommendations
            proposed['catalog_search']['reason']=lookup['reason']
            proposed['connected_source_discovery']['reason']=f"检查 {table.get('name')} 表所属 {table.get('source_id') or '已连接'} 数据源；已连接来源直接沿用。"
            proposed['data_query']['reason']=f"按姓名精确匹配并返回 {', '.join(table.get('name','')+'.'+item.get('name','') for item in output_fields)}。"
            draft['capability_steps']=list(proposed.values())
        return draft,lookup

    def repair_field_query(self, flow, actor):
        """Return misrouted ordinary lookups to explicit field/specification review."""
        from execution.security.output_policy import infer_catalog_classification
        confirmed_spec=(self.store.get('spec',flow.get('spec_id',''),flow.get('spec_version'))
                       if flow.get('spec_id') else None)
        confirmed_constraints=((confirmed_spec or {}).get('specification') or {}).get('execution_constraints') or {}
        already_field_bound=bool(confirmed_spec and confirmed_spec.get('confirmed')
                                 and (confirmed_constraints.get('field_output') or confirmed_constraints.get('field_outputs')))
        prior_lookup=flow.get('field_lookup') or {}
        prior_field_ids=set(prior_lookup.get('field_ids') or [prior_lookup.get('field_id')])
        prior_field=self.store.get('object',prior_lookup.get('field_id','')) if prior_lookup.get('field_id') else None
        prior_inference=(infer_catalog_classification(prior_field)
                         if prior_field else ('unknown',None))
        validation=flow.get('query_validation') or {}
        previously_confirmed=any(
            spec.get('demand_id')==flow.get('demand_id') and spec.get('confirmed')
            and prior_field_ids <= normalized_field_output_ids(
                ((spec.get('specification') or {}).get('execution_constraints') or {}))
            and (spec.get('specification') or {}).get('query_draft')==validation.get('normalized_sql')
            for spec in self.store.list('spec'))
        if flow.get('state')=='waiting_manual_spec_confirmation' and previously_confirmed:
            return flow
        has_empty_execution_dates=any(
            isinstance((flow.get('draft') or {}).get(key),str)
            and (flow.get('draft') or {})[key].strip().casefold() in
                {'','null','none','nil','undefined','n/a','na'}
            for key in ('time_start','time_end','deadline'))
        needs_known_classification=(bool(prior_field)
            and prior_field.get('classification') in (None,'','待确认')
            and prior_inference[0]!='unknown')
        repairable_state=((flow.get('state')=='awaiting_execution_plan' and not already_field_bound) or
            (flow.get('state')=='waiting_manual_spec_confirmation'
             and not flow.get('spec_id') and not flow.get('execution_plan_id')
             and ((flow.get('query_validation') or {}).get('status')=='rejected'
                  or has_empty_execution_dates or needs_known_classification)) or
            (flow.get('state')=='attention' and not flow.get('spec_id')
             and not flow.get('execution_plan_id')
             and (flow.get('query_validation') or {}).get('status')=='rejected'))
        if (not repairable_state
                or flow.get('execution_plan_id') or flow.get('grant_id')
                or flow.get('execution_authorized')):
            return flow
        catalog = self._catalog_metadata()
        draft, lookup = self._complete_query_intent(
            flow.get('request_text'), dict(flow.get('draft') or {}), catalog)
        if not lookup or lookup.get('kind') != 'field_value':
            return flow
        draft=dict(draft)
        for key in ('time_start','time_end','deadline','quality','delivery'):
            if isinstance(draft.get(key),str) and draft[key].strip().casefold() in {
                    '', 'null', 'none', 'nil', 'undefined', 'n/a', 'na'}:
                draft[key]=None
        today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
        if not draft.get('deadline'):
            draft['deadline']=(today+timedelta(days=7)).isoformat()
        if not draft.get('time_start'):
            draft['time_start']=today.isoformat()
        if not draft.get('time_end'):
            draft['time_end']=draft['deadline']
        if not draft.get('quality'):
            draft['quality']='完整'
        # Keep historical specifications in the version store; require a fresh
        # confirmation before constructing a plan for the newly resolved field.
        pending = {**flow, 'spec_id': None, 'spec_version': None,
                   'auto_prepare_fixed_plan': False,
                   'pseudonym_spec_revision_confirmed': False,
                   'query_output_blocker': None, 'execution_output_blocker': None}
        return self._prepare_catalog_field_flow(pending, draft, lookup, actor, catalog)

    def continue_incomplete_query(self, flow, actor):
        """Repair a confirmed catalog-only flow when metadata can safely complete a person-ID lookup draft."""
        if (flow.get('state')!='completed'
                or flow.get('confirmation',{}).get('confirmed') is not True
                or flow.get('spec_id') or flow.get('execution_plan_id')):return flow
        catalog=self._catalog_metadata()
        draft,lookup=self._complete_query_intent(flow.get('request_text'),dict(flow.get('draft') or {}),catalog)
        if not lookup:return flow
        if lookup.get('kind')=='field_value':
            return self._prepare_catalog_field_flow(flow,draft,lookup,actor,catalog)
        if lookup.get('kind')=='catalog_candidate':
            return flow
        demand=self.store.require('demand',flow['demand_id'])
        proposal={**demand,'expected_version':demand['version'],
            'object_scope':draft['object_scope'],'delivery':'对象级',
            'execution_constraints':draft['execution_constraints']}
        needs_demand_update=any(demand.get(key)!=proposal.get(key)
                                for key in ('object_scope','delivery','execution_constraints'))
        updated=(self.runtime.management.intake.apply_draft(flow['demand_id'],
            {'expected_version':demand['version'],'proposal':{**draft,**proposal},'model':flow.get('model')},actor)['demand']
            if needs_demand_update else demand)
        validation=validate_query_draft(lookup['sql'],catalog)
        table=lookup['table'];source_status=self._source_connection_status(table)
        prior_resource=next((item for item in flow.get('resources',[]) if item.get('id')==table.get('id')),None)
        resource={**table,'reason':lookup['reason'],'review_status':
            'human_confirmed' if prior_resource and prior_resource.get('review_status')=='human_confirmed'
            else 'model_recommended_pending_human_confirmation'}
        candidate={**resource}
        old_names=[item.get('name') or item.get('id') for item in flow.get('resources',[])]
        replacement_needed=not prior_resource
        note=(f"已按姓名精确查询规则修正目录候选：改用含 name 与 patient_id 字段的 {table.get('name')} 表；"
              f"原候选 {', '.join(old_names)} 无法可靠完成姓名匹配。" if replacement_needed else
              f"已按姓名精确查询规则确认目录候选：{table.get('name')} 表同时登记了 name 与 patient_id 字段。")
        catalog_reason=(f"目录规则发现 {table.get('name')} 表包含姓名和 patient_id 字段；"
                        f"原先推荐的 {', '.join(old_names)} 候选不适合按姓名匹配。"
                        if replacement_needed else
                        f"目录规则确认 {table.get('name')} 表同时包含姓名和 patient_id 字段。")
        steps=[
            {'id':'step-1','capability':'catalog_search','label':CAPABILITY_LABELS['catalog_search'],
             'reason':catalog_reason,
             'depends_on':[],'status':'completed','candidates':[candidate],
             'detail':'已依据已登记表/字段元数据修正候选；等待你在规格弹窗中确认该表。未读取源数据行。'
                      if resource['review_status']!='human_confirmed' else
                      f"已依据已登记表/字段元数据确认并沿用 {table.get('name')} 表。未读取源数据行。"},
            {'id':'step-2','capability':'connected_source_discovery','label':CAPABILITY_LABELS['connected_source_discovery'],
             'reason':f"核对 {table.get('name')} 表所属 {table.get('source_id') or '已连接'} 数据源连接状态。",'depends_on':['step-1'],
             'status':'completed' if source_status=='connected' else 'waiting_authorization' if source_status=='cloud' else 'blocked',
             'connection_kind':'cloud' if source_status=='cloud' else 'local' if source_status!='connected' else None,
             'detail':f"{table.get('source_id') or '数据源'} 来源已连接，沿用现有连接继续；无需重新登录，也未读取源数据行。" if source_status=='connected'
                      else '该候选来源尚未连接，需先处理数据源连接。'},
            {'id':'step-3','capability':'data_query','label':CAPABILITY_LABELS['data_query'],
             'reason':lookup['reason'],'depends_on':['step-2'],
             'status':'waiting_manual_spec_confirmation' if validation['status']=='valid_pending_human_confirmation' and source_status=='connected' else 'blocked',
             'detail':'已预填姓名精确匹配的只读 SQL 和伪名ID输出；确认任务规格后平台会自动建立固定方案草案。查询尚未提交。'}]
        state='waiting_manual_spec_confirmation' if steps[1]['status']=='completed' and steps[2]['status']=='waiting_manual_spec_confirmation' else 'attention'
        revised_draft={**draft,'time_start':updated.get('time_start'),'time_end':updated.get('time_end'),
            'purpose':updated.get('purpose'),'quality':updated.get('quality'),'deadline':updated.get('deadline')}
        return self.store.save('intake_flow',flow['id'],{**flow,'steps':steps,'resources':[resource],
            'query_draft':lookup['sql'],'query_validation':validation,'draft':revised_draft,
            'demand_version':updated['version'],'state':state,'auto_prepared_spec':True,
            'auto_prepare_fixed_plan':True,'candidate_correction_pending':resource['review_status']!='human_confirmed',
            'candidate_correction':{'replaced':old_names,'selected':table.get('name'),'reason':note} if replacement_needed else None,
            'draft_revision_note':note,
            'notes':list(flow.get('notes',[]))+[note]},flow['version'],actor,
            '目录规则补全遗漏的查询步骤并修正患者姓名映射候选；等待人工确认规格')

    def _prepare_catalog_field_flow(self,flow,draft,lookup,actor,catalog=None):
        """Prepare a matched ordinary-field lookup without reading source rows."""
        from execution.security.output_policy import field_classification,infer_catalog_classification
        catalog=catalog or self._catalog_metadata()
        table=lookup['table'];name_field=lookup['name_field']
        output_fields=lookup.get('fields') or [lookup['field']]
        labels={'health':'健康信息','identifier':'个人标识','sensitive':'敏感',
                'public':'公开','internal':'内部'}
        refreshed=[];inferences={}
        for output_field in output_fields:
            inferred,basis=infer_catalog_classification(output_field)
            inferences[output_field['id']]=(inferred,basis)
            if inferred!='unknown' and output_field.get('classification') in (None,'','待确认'):
                output_field=self.runtime.management.catalog.annotate(output_field['id'],{
                    'expected_version':output_field['version'],'classification':labels[inferred],
                    'classification_basis':basis},actor)
            refreshed.append(output_field)
        output_fields=refreshed;field=output_fields[0]
        inferred,basis=inferences[field['id']]
        lookup={**lookup,'field':field,'fields':output_fields}
        demand=self.store.require('demand',flow['demand_id'])
        proposal={**demand,'expected_version':demand['version'],
            'title':draft.get('title') or f"查询{lookup['name']}的{lookup['target_phrase']}",
            'object_scope':draft['object_scope'],'delivery':'对象级',
            'execution_constraints':draft['execution_constraints']}
        for key in ('time_start','time_end','purpose','quality','deadline'):
            if draft.get(key):proposal[key]=draft[key]
        changed=any(demand.get(key)!=proposal.get(key) for key in
            ('title','object_scope','delivery','execution_constraints','time_start','time_end','purpose','quality','deadline'))
        updated=(self.runtime.management.intake.apply_draft(flow['demand_id'],
            {'expected_version':demand['version'],'proposal':draft|proposal,'model':flow.get('model')},actor)['demand']
            if changed else demand)
        validation=validate_query_draft(lookup['sql'],catalog)
        classifications=[field_classification(item) for item in output_fields]
        classification=classifications[0]
        has_unknown='unknown' in classifications
        allowed_classifications=('public','internal','health','identifier')
        all_outputs_allowed=all(value in allowed_classifications for value in classifications)
        source_status=self._source_connection_status(table)
        previous={item.get('id'):item for item in flow.get('resources',[])}
        resources=[]
        def add_resource(item,reason):
            if not item or any(resource.get('id')==item.get('id') for resource in resources):return
            resources.append({**item,'reason':reason,'review_status':
                'human_confirmed' if previous.get(item['id'],{}).get('review_status')=='human_confirmed'
                else 'model_recommended_pending_human_confirmation'})
        add_resource(table,lookup['reason'])
        for output_field in output_fields:add_resource(output_field,lookup['reason'])
        add_resource(name_field,'仅用于姓名精确筛选，不会作为输出字段。')
        join_table=lookup.get('join_table')
        join_fields=[]
        if join_table:
            join_fields=[row for row in catalog if row.get('kind')=='field'
                and row.get('status')=='active' and row.get('name')=='patient_id'
                and row.get('parent_id') in (table['id'],join_table['id'])]
            add_resource(join_table,'按目录中patient_id等值关联，用于定位目标患者。')
            for row in join_fields:add_resource(row,'仅用于已登记表之间的patient_id关联，不作为额外输出。')
        lookup_record={'kind':'field_value','table_id':table['id'],'table_name':table.get('name'),
            'source_id':table.get('source_id'),'field_id':field['id'],'field_name':field.get('name'),
            'field_meaning':field.get('meaning'),'name_field_id':name_field['id'],
            'field_ids':[item['id'] for item in output_fields],
            'output_fields':[{'field_id':item['id'],'field_name':item.get('name'),
                'table_id':item.get('parent_id'),'table_name':item.get('table'),
                'classification':field_classification(item)} for item in output_fields],
            'name_field_name':name_field.get('name'),'name':lookup['name'],
            'target_phrase':lookup['target_phrase'],'reason':lookup['reason'],
            'classification':field.get('classification'),
            'classification_basis':field.get('classification_basis') or basis,
            'join_table_id':join_table.get('id') if join_table else None,
            'join_field_ids':[row['id'] for row in join_fields]}
        steps=[
            {'id':'step-1','capability':'catalog_search','label':CAPABILITY_LABELS['catalog_search'],
             'reason':lookup['reason'],'depends_on':[],'status':'completed','candidates':resources,
             'detail':f"目录元数据已匹配 {table.get('name')}.{field.get('name')}；姓名字段只用于筛选。候选需在规格窗口核对，未读取源数据行。"},
            {'id':'step-2','capability':'connected_source_discovery','label':CAPABILITY_LABELS['connected_source_discovery'],
             'reason':f"检查 {table.get('name')} 表所属 {table.get('source_id') or '已登记'} 数据源连接。",
             'depends_on':['step-1'],'status':'completed' if source_status=='connected' else
                'waiting_authorization' if source_status=='cloud' else 'blocked',
             'connection_kind':'cloud' if source_status=='cloud' else 'local' if source_status!='connected' else None,
             'detail':f"{table.get('source_id') or '数据源'} 已连接，沿用当前连接；不要求重新登录，也未读取源数据行。"
                if source_status=='connected' else '所选字段所属数据源尚不可用，需先处理连接或登记。'},
            {'id':'step-3','capability':'data_query','label':CAPABILITY_LABELS['data_query'],
             'reason':lookup['reason'],'depends_on':['step-2'],
             'status':'waiting_field_classification' if has_unknown else
                'waiting_manual_spec_confirmation' if all_outputs_allowed and source_status=='connected' else 'blocked',
             'detail':('至少一个输出字段分类尚未确认；请平台方在当前窗口复核。确认前不生成可执行方案。'
                if has_unknown else
                '输出字段分类不允许直接输出；请平台方复核分类或调整输出方式。'
                if not all_outputs_allowed else
                '姓名与所有目标字段已匹配；个人标识字段仍需平台逐方案授权。确认任务规格不会执行查询。'
                if 'identifier' in classifications else
                '只读SQL已按目录字段和姓名精确匹配预填；确认任务规格后自动建立固定方案草案。查询尚未提交。')}]
        if source_status=='cloud':state='waiting_authorization'
        elif source_status!='connected' or validation['status']!='valid_pending_human_confirmation':state='attention'
        elif has_unknown:state='waiting_field_classification'
        elif all_outputs_allowed:state='waiting_manual_spec_confirmation'
        else:state='attention'
        revised_draft={**draft,'time_start':updated.get('time_start'),'time_end':updated.get('time_end'),
            'purpose':updated.get('purpose'),'quality':updated.get('quality'),'deadline':updated.get('deadline')}
        return self.store.save('intake_flow',flow['id'],{**flow,'steps':steps,'resources':resources,
            'query_draft':lookup['sql'],'query_validation':validation,'draft':revised_draft,
            'demand_version':updated['version'],'state':state,'field_lookup':lookup_record,
            'auto_prepared_field_lookup':True,'auto_prepare_field_plan':True,
            'output_preference':'catalog_field','execution_authorized':False,
            'notes':list(flow.get('notes',[]))+['目录字段匹配和只读SQL草案仅使用元数据；字段分类与交付授权仍需人工确认。']},
            flow['version'],actor,'按目录字段元数据补全普通字段查询链路；未读取源数据行')

    def reconcile_connected_sources(self, flow, actor, external_catalog=()):
        """Clear stale login waits when a confirmed candidate uses an available source."""
        confirmed_resources=[resource for resource in flow.get('resources',[])
                             if resource.get('review_status')=='human_confirmed']
        sources_available=not confirmed_resources or all(
            self._source_connection_status(resource,external_catalog)=='connected' for resource in confirmed_resources)
        if flow.get('state')=='preparing' and flow.get('steps') and all(
                step.get('status')=='completed' for step in flow.get('steps',[])) and sources_available:
            return self.store.save('intake_flow',flow['id'],{**flow,'state':'completed'},flow['version'],actor,
                '平台元数据链路已完成；继续办理时无需重复登录')
        if flow.get('state')!='waiting_authorization':return flow
        steps=[dict(step) for step in flow.get('steps',[])]
        changed=False
        accepted={resource.get('id'):resource for resource in flow.get('resources',[])
                  if resource.get('review_status')=='human_confirmed'}
        for step in steps:
            if step.get('capability')!='connected_source_discovery' or step.get('status')!='waiting_authorization':continue
            candidates=[candidate for candidate in step.get('candidates',[])
                        if candidate.get('id') in accepted]
            if not candidates:candidates=list(accepted.values())
            if not candidates:continue
            statuses=[self._source_connection_status(candidate,external_catalog) for candidate in candidates]
            if all(status=='connected' for status in statuses):
                step.update(status='completed',label=CAPABILITY_LABELS['connected_source_discovery'],connection_kind=None,
                    detail='已确认的数据表来自平台可用的数据源，沿用当前连接与目录结构继续办理，无需重新登录。本步骤只检查目录和连接状态，不代表查询授权。')
                changed=True
            elif any(status in ('unavailable','unknown') for status in statuses):
                step.update(status='blocked',label=CAPABILITY_LABELS['connected_source_discovery'],connection_kind='local',
                    detail='所选数据源当前不可用或未登记连接配置。请检查平台资源目录中的来源状态；这不是云盘登录问题。')
                changed=True
        if not changed:return flow
        if any(step.get('status') in ('gap','blocked') for step in steps):state='attention'
        elif any(step.get('status')=='waiting_authorization' for step in steps):state='waiting_authorization'
        elif any(step.get('status')=='waiting_manual_spec_confirmation' for step in steps):state='waiting_manual_spec_confirmation'
        else:state='completed'
        return self.store.save('intake_flow',flow['id'],{**flow,'steps':steps,'state':state},flow['version'],actor,
            '核对已确认数据源连接状态；可用的医疗数据源跳过重复登录')

    def reconcile_execution(self, flow, actor):
        """Mirror the controlled job state into the visible intake workflow."""
        job_id=flow.get('job_id')
        if (not job_id and flow.get('execution_authorized') and flow.get('grant_id')
                and flow.get('execution_plan_id')):
            authorization_actor=(flow.get('authorization_decision') or {}).get('actor')
            prior_jobs=[job for job in self.runtime.execution.store.list('job')
                        if job.get('plan_id')==flow.get('execution_plan_id')
                        and job.get('plan_version')==flow.get('execution_plan_version')
                        and job.get('grant_id')==flow.get('grant_id')
                        and (not authorization_actor or job.get('actor')==authorization_actor)]
            if prior_jobs:
                prior_jobs.sort(key=lambda job:job.get('updated_at',''))
                latest=prior_jobs[-1]
                flow={**flow,'job_id':latest['id'],'execution_submitted_by':latest.get('actor'),
                      'execution_submitted_at':latest.get('updated_at')}
                job_id=latest['id']
        if not job_id:return flow
        job=self.runtime.execution.store.get('job',job_id)
        if not job:return flow
        state_map={'queued':'query_queued','running':'query_running','success':'query_completed',
                   'failed':'query_failed','cancelled':'query_failed','interrupted':'query_failed'}
        next_state=state_map.get(job.get('state'))
        if not next_state:return {**flow,'job_state':job.get('state')}
        if flow.get('state')==next_state and flow.get('delivery_id')==job.get('delivery_id'):
            return {**flow,'job_state':job.get('state'),'query_error':job.get('error')}
        steps=[]
        lookup=flow.get('field_lookup') or {}
        output_fields=[lookup.get('field_name') or '已确认目录字段']
        if flow.get('output_preference')=='catalog_field_with_patient_id':
            output_fields.append(lookup.get('identifier_field_name') or 'patient_id')
        output_summary='本次只交付：'+'、'.join(output_fields)+'。'
        detail={'query_queued':'受控查询已进入队列，等待本地执行器。'+output_summary,
                'query_running':'本地执行器正在按已确认方案查询。'+output_summary,
                'query_completed':'受控查询完成，结果已保存为平台内待读取交付。'+output_summary,
                'query_failed':'查询未完成：'+str(job.get('error') or '执行器未交付结果。')}
        for step in flow.get('steps',[]):
            item=dict(step)
            if item.get('capability')=='data_query':
                item.update(status=next_state,detail=detail[next_state])
            steps.append(item)
        changes={'state':next_state,'job_state':job.get('state'),'steps':steps,
                 'delivery_id':job.get('delivery_id'),'query_error':job.get('error')}
        return self.store.save('intake_flow',flow['id'],{**flow,**changes},flow['version'],actor,
            '同步受控查询执行状态与交付标记')

    def external_catalog(self, request):
        """Read schema names only from this browser's existing external session."""
        from external_api import COOKIE, _lock, _sessions
        with _lock:
            session=_sessions.get(request.cookies.get(COOKIE))
            credentials=getattr(session,'cloud_credentials',None) if session else None
            username=credentials[0] if credentials else None
        if not username:return []
        from external_discovery import definitions
        return [{'id':row['id'],'name':row.get('name') or row['id'],'columns':row.get('columns',[]),
                 'types':row.get('types',[])} for row in definitions(username)]

    def propose(self, demand_id, text, actor, model_client, external_catalog=()):
        demand=self.store.require('demand',demand_id)
        if demand.get('created_by')!=actor and actor!='demo-platform':raise DomainError('无权修改他人需求',403)
        catalog=self._catalog_metadata(external_catalog)
        rule_gap=(self._is_wechat_index_request(text)
                  and not any(self._wechat_index_metadata_match(row) for row in catalog))
        model_name=model_client.model
        if rule_gap:
            # A deterministic catalog preflight avoids spending minutes asking a local
            # model to invent a source when the registered metadata has no such subject.
            gap_note='平台登记目录中没有微信指数数据源或字段；请登记并扫描实际来源后重新检索。'
            draft={key:demand.get(key) for key in ('title','entry','object_scope','time_start','time_end',
                'purpose','quality','deadline','delivery','service_ids','attachments','execution_constraints')}
            # This deterministic path is triggered by a missing source, not a cost
            # request. Leave cost empty so Intake can discard only a stale model guess.
            draft['cost']=None
            draft.update(capability_steps=[
                {'capability':'catalog_search','reason':'先按微信指数主题检索已登记目录。','inputs':['微信指数']},
                {'capability':'connected_source_discovery','reason':'确认匹配来源及其连接状态。','inputs':['微信指数']},
                {'capability':'trend_analysis','reason':'匹配时间与指数值字段后分析时间序列。','inputs':['微信指数']}],
                resource_recommendations=[],query_draft=None,uncertain_fields=[gap_note])
            model_name='platform-catalog-gap-rule'
        else:
            draft=model_client.extract(text,catalog)
        draft,person_lookup=self._complete_query_intent(text,draft,catalog)
        if not rule_gap:
            draft=self._filter_unrelated_wechat_index_resources(text,draft,catalog)
        today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
        assumptions=[]
        if draft.get('deadline'):
            try:
                deadline_date=datetime.fromisoformat(str(draft['deadline']).replace('Z','+00:00')).date()
                if deadline_date<today:
                    assumptions.append(f"原交付时限 {draft['deadline']} 已过期；查询前需由需求方更新新的交付时限。")
            except ValueError:
                assumptions.append('原交付时限格式无法识别，需修正后才能执行。')
        if not draft.get('title'):draft['title']=text.strip()[:80]
        if not draft.get('entry'):draft['entry']='goal'
        if not draft.get('object_scope'):draft['object_scope']='按需求描述匹配目录资源；范围由已确认执行条件约束'
        lookup_scope=((draft.get('execution_constraints') or {}).get('scope') or {}).get('kind')=='lookup'
        if not draft.get('deadline'):
            draft['deadline']=(today+timedelta(days=7)).isoformat()
            assumptions.append('执行期限默认7天')
        if lookup_scope:
            if not draft.get('time_start'):draft['time_start']=today.isoformat()
            if not draft.get('time_end'):draft['time_end']=draft['deadline']
        else:
            if not draft.get('time_end'):draft['time_end']=today.isoformat();assumptions.append('时间窗终点默认今天')
        if not draft.get('time_start'):
            match=re.search(r'近\s*(\d+)\s*天',text)
            days=int(match.group(1)) if match else (7 if '近一周' in text else 90 if '近三个月' in text else 30)
            draft['time_start']=(today-timedelta(days=days-1)).isoformat()
            assumptions.append(f'时间窗起点默认近{days}天')
        if not draft.get('purpose'):
            draft['purpose']='指标趋势分析' if 'trend_analysis' in [x.get('capability') for x in draft.get('capability_steps',[])] else '按需求进行数据查询'
            assumptions.append('用途由需求意图推断')
        if not draft.get('quality'):draft['quality']='完整'
        if not draft.get('delivery'):
            draft['delivery']='对象级' if any(word in text for word in ('明细','逐条','每条记录')) else '汇总'
            assumptions.append('交付形态默认汇总' if draft['delivery']=='汇总' else '交付形态按明细意图推断')
        if assumptions:
            draft['uncertain_fields']=list(dict.fromkeys(draft.get('uncertain_fields',[])+assumptions))
        if not draft.get('execution_constraints') and not draft.get('catalog_match_resolution'):
            recommended=set(x.get('id') for x in draft.get('resource_recommendations',[]))
            tables={x.get('table') for x in catalog if x.get('id') in recommended
                    and x.get('kind') in ('table','field') and x.get('table')}
            fields=[x for x in catalog if x.get('kind')=='field' and x.get('table') in tables]
            time_field=next((x for x in fields if re.search(r'(date|time|日期|时间)',str(x.get('name','')),re.I)
                             or re.search(r'(date|time)',str(x.get('data_type','')),re.I)),None)
            if time_field:
                output_mode='object' if draft['delivery']=='对象级' else 'aggregate'
                draft['execution_constraints']={'scope':{'kind':'rows','filters':[],'all_rows':True},
                    'time_field':time_field['id'],'output_mode':output_mode,'circulation_modes':['controlled_compute'],
                    'quality_min_complete':1,'max_rows':200,'min_group_size':5}
                assumptions.append('执行范围默认使用匹配表的全部记录并受时间窗限制；可在确认前修改')
                draft['uncertain_fields']=list(dict.fromkeys(draft.get('uncertain_fields',[])+[assumptions[-1]]))
        # Store model interpretation into the demand draft so its fields are prefilled,
        # editable and versioned before any task specification is confirmed.
        saved=self.runtime.management.intake.apply_draft(demand_id,{'expected_version':demand['version'],
            'proposal':draft,'model':model_name,'cost_explicit':explicitly_mentions_cost(text)},actor)
        demand=saved['demand']
        sql_validation=validate_query_draft(draft.get('query_draft'),catalog)
        by_id={x['id']:x for x in catalog}
        resources=[{**by_id[item['id']],'reason':item['reason'],'review_status':'model_recommended_pending_human_confirmation'}
                   for item in draft.get('resource_recommendations',[]) if item['id'] in by_id]
        proposed=draft.get('capability_steps',[])
        capabilities=list(dict.fromkeys(item['capability'] for item in proposed))
        if any(cap in capabilities for cap in ('data_query','trend_analysis')) and 'catalog_search' not in capabilities:
            capabilities.insert(0,'catalog_search')
        order={name:index for index,name in enumerate(('catalog_search','connected_source_discovery','data_query','trend_analysis'))}
        capabilities.sort(key=lambda cap:order[cap])
        reason_by_cap={item['capability']:item.get('reason','') for item in proposed}
        inputs_by_cap={item['capability']:item.get('inputs',[]) for item in proposed}
        steps=[]
        for index,cap in enumerate(capabilities,1):
            steps.append({'id':f'step-{index}','capability':cap,'label':CAPABILITY_LABELS[cap],
                'reason':reason_by_cap.get(cap,'规则要求先检索并校验资源目录。' if cap=='catalog_search' else ''),
                'inputs':inputs_by_cap.get(cap,[]),'depends_on':[f'step-{index-1}'] if index>1 else [],'status':'proposed'})
        if not steps:raise DomainError('模型未提出可识别的平台能力步骤，请补充需求描述')
        for step in steps:
            if step['capability']=='catalog_search':
                step['candidates']=resources
                step['status']='matched' if resources else 'gap'
                step['detail']='本地模型仅依据登记的表/字段/服务元数据推荐；待人工确认，未读取源数据行。' if resources else '当前登记目录中没有找到与需求匹配的候选项。'
            elif step['capability']=='connected_source_discovery':
                step['status']='matched' if resources else 'gap'
                step['detail']=('等待人工确认目录候选；确认后按所选表所属数据源检查连接。已连接的医疗数据源直接继续，只有尚未连接的外部来源才需要连接；不会读取源数据行。'
                    if resources else '目录没有找到与需求主题匹配的数据表或字段。请先登记并扫描实际的微信指数数据源，再重新检索；现有医疗表不会被当作替代来源。')
            elif step['capability'] in ('data_query','trend_analysis'):
                step['status']='gap' if draft.get('catalog_match_resolution') else 'waiting_spec' if resources else 'gap'
                step['detail']=('已返回最接近的目录表和字段，但患者姓名关联未确认；不能生成查询SQL或方案。'
                    if draft.get('catalog_match_resolution') else '需完成资源匹配并人工确认任务规格后，才能建立并运行查询方案。'
                    if resources else '尚无匹配的微信指数数据表和字段；完成目录登记与扫描后重新检索，才可编制查询。')
        field_lookup_record=None
        if person_lookup and person_lookup.get('kind')=='field_value':
            from execution.security.output_policy import field_classification
            output_fields=person_lookup.get('fields') or [person_lookup['field']]
            field_lookup_record={'kind':'field_value','table_id':person_lookup['table']['id'],
                'table_name':person_lookup['table'].get('name'),'source_id':person_lookup['table'].get('source_id'),
                'field_id':person_lookup['field']['id'],'field_name':person_lookup['field'].get('name'),
                'field_meaning':person_lookup['field'].get('meaning'),
                'field_ids':[item['id'] for item in output_fields],
                'output_fields':[{'field_id':item['id'],'field_name':item.get('name'),
                    'table_id':item.get('parent_id'),'table_name':item.get('table'),
                    'classification':field_classification(item)} for item in output_fields],
                'name_field_id':person_lookup['name_field']['id'],'name_field_name':person_lookup['name_field'].get('name'),
                'join_table_id':person_lookup.get('join_table',{}).get('id') if person_lookup.get('join_table') else None,
                'join_field_ids':[item['id'] for item in person_lookup.get('join_fields',[])],
                'name':person_lookup['name'],'target_phrase':person_lookup['target_phrase'],
                'reason':person_lookup['reason'],'classification':field_classification(person_lookup['field'])}
        is_pseudonym_lookup=bool(person_lookup and person_lookup.get('kind')=='pseudonymized_id')
        record=self.store.save('intake_flow',new_id('FLOW'),{
            'demand_id':demand_id,'demand_version':demand['version'],'created_by':actor,
            'request_text':text,'model':model_name,'draft':draft,'steps':steps,
            'resources':resources,'query_draft':draft.get('query_draft'),'query_validation':sql_validation,
            'state':'awaiting_chain_confirmation',
            'confirmation':None,'execution_authorized':False,
            'auto_prepared_spec':is_pseudonym_lookup,'auto_prepare_fixed_plan':is_pseudonym_lookup,
            'auto_prepared_field_lookup':bool(field_lookup_record),'auto_prepare_field_plan':bool(field_lookup_record),
            'field_lookup':field_lookup_record,'output_preference':'catalog_field' if field_lookup_record else None,
            'notes':[('目录规则未找到微信指数资源，已停止候选猜测；未读取源数据行。' if rule_gap else
                      '已找到最接近的目录表和字段；患者姓名关联缺失时会明确返回候选并说明阻塞，不再报告“无资源”。'
                      if draft.get('catalog_match_resolution') else
                      '模型只提出能力链路建议；未生成规格、未执行调用、未授予权限。'),
                     '字段/表结构变更历史追踪按用户要求暂不纳入。']},0,actor,'生成待确认需求能力链路')
        return record

    def confirm(self, flow_id, body, actor, external_catalog=()):
        with self.store.atomic():
            flow=self.store.require('intake_flow',flow_id)
            demand=self.store.require('demand',flow['demand_id'])
            requester_owner=demand.get('requester_id') or ('demo-requester' if demand.get('created_by')=='demo-platform' else None)
            if actor not in (demand.get('created_by'),requester_owner,'demo-platform'):
                raise DomainError('无权确认他人需求链路',403)
            if flow['state']!='awaiting_chain_confirmation':raise DomainError('该链路不在待确认状态',409)
            if body.get('expected_version')!=flow['version']:raise DomainError('链路版本已变化',409)
            if body.get('confirmed') is not True:raise DomainError('需明确确认完整链路')
            note=str(body.get('note','')).strip()
            if not note:raise DomainError('请记录链路确认说明')
            candidate_ids={candidate.get('id') for step in flow.get('steps',[])
                           for candidate in step.get('candidates',[]) if candidate.get('id')}
            reviewed_ids=body.get('reviewed_resource_ids')
            accepted_ids=body.get('accepted_resource_ids')
            if not isinstance(reviewed_ids,list) or not isinstance(accepted_ids,list):
                raise DomainError('请在确认窗口逐项核对目录候选')
            if any(not isinstance(resource_id,str) for resource_id in reviewed_ids+accepted_ids):
                raise DomainError('目录候选编号格式无效')
            if set(reviewed_ids)!=candidate_ids or not set(accepted_ids)<=candidate_ids:
                raise DomainError('目录候选确认内容与当前链路不一致，请刷新后重试',409)
            accepted_ids=set(accepted_ids)
            steps=[]
            blocked=False
            for step in flow['steps']:
                item=dict(step)
                if item.get('candidates'):
                    item['candidates']=[{**candidate,'review_status':
                        'human_confirmed' if candidate.get('id') in accepted_ids else 'human_rejected'}
                        for candidate in item['candidates']]
                    if any(candidate.get('id') in accepted_ids for candidate in item['candidates']):
                        item['status']='completed'
                        item['detail']=f"人工确认采用 {sum(candidate.get('id') in accepted_ids for candidate in item['candidates'])} 项目录候选。"
                    else:
                        item['status']='blocked';blocked=True
                        item['detail']='已逐项复核目录候选，但没有选中可用资源。请重新描述需求或补充目录后重新生成链路。'
                elif item.get('capability')=='connected_source_discovery':
                    selected=[resource for resource in flow.get('resources',[]) if resource.get('id') in accepted_ids]
                    statuses=[self._source_connection_status(resource,external_catalog) for resource in selected]
                    if selected and all(status=='connected' for status in statuses):
                        item['status']='completed';item.pop('connection_kind',None)
                        item['detail']='所选数据表来自平台可用的数据源，沿用现有连接继续办理，无需重新登录。本步骤只检查目录和连接状态，不代表查询授权。'
                    elif any(status=='cloud' for status in statuses):
                        item['status']='waiting_authorization';item['connection_kind']='cloud'
                        item['action_url']='/static/platform/connections.html?connect=1#extDiscoveryPanel'
                        item['detail']='所选候选来自云盘，但当前会话未连接。请连接云盘后重新检索目录。'
                    else:
                        item['status']='blocked';blocked=True;item['connection_kind']='local'
                        item['detail']='所选数据源当前不可用或未登记连接配置。请检查平台资源目录中的来源状态；这不是云盘登录问题。'
                elif item['status']=='matched':item['status']='completed'
                elif item['status']=='gap':item['status']='blocked';blocked=True
                elif item['status']=='waiting_connection':item['status']='waiting_authorization'
                elif item['status']=='waiting_spec':item['status']='waiting_manual_spec_confirmation'
                steps.append(item)
            resources=[{**resource,'review_status':'human_confirmed'} for resource in flow.get('resources',[])
                       if resource.get('id') in accepted_ids]
            state='completed'
            if blocked:state='attention'
            elif any(s['status']=='waiting_authorization' for s in steps):state='waiting_authorization'
            elif any(s['status']=='waiting_manual_spec_confirmation' for s in steps):state='waiting_manual_spec_confirmation'
            field_lookup=flow.get('field_lookup')
            if field_lookup and state not in ('attention','waiting_authorization'):
                output_fields=field_lookup.get('output_fields') or []
                output_ids=set(field_lookup.get('field_ids') or [field_lookup.get('field_id')])
                required_ids={field_lookup.get('table_id'),field_lookup.get('name_field_id'),
                    field_lookup.get('join_table_id'),*field_lookup.get('join_field_ids',[]),*output_ids}
                required_ids|={item.get('table_id') for item in output_fields if item.get('table_id')}
                required_ids.discard(None)
                if not required_ids<=accepted_ids:
                    state='attention'
                    for step in steps:
                        if step.get('capability')=='data_query':
                            step.update(status='blocked',detail='需同时确认全部输出字段、所属表、姓名筛选字段和患者关联字段；当前未生成查询规格。')
                else:
                    fields=[self.store.require('object',field_id) for field_id in output_ids]
                    from execution.security.output_policy import field_classification
                    classifications=[field_classification(field) for field in fields]
                    unknown_fields=[field for field,classification in zip(fields,classifications)
                        if classification=='unknown']
                    if unknown_fields:
                        field=unknown_fields[0]
                        field_lookup={**field_lookup,'field_id':field['id'],'field_name':field.get('name'),
                            'table_id':field.get('parent_id'),'table_name':field.get('table')}
                    data_step=next((step for step in steps if step.get('capability')=='data_query'),None)
                    source_step=next((step for step in steps if step.get('capability')=='connected_source_discovery'),None)
                    connected=bool(source_step and source_step.get('status')=='completed')
                    if unknown_fields:
                        state='waiting_field_classification'
                        if data_step:data_step.update(status='waiting_field_classification',
                            detail='至少一个目标字段分类待确认；请平台方逐字段登记分类。确认前不会建立方案或读取数据。')
                    elif all(classification in ('public','internal','health','identifier') for classification in classifications) and connected:
                        state='waiting_manual_spec_confirmation'
                        if data_step:data_step.update(status='waiting_manual_spec_confirmation',
                            detail='全部目标字段已登记分类；请核对预填SQL和范围，确认任务规格后自动建立固定方案。个人标识字段仍需平台逐方案授权。')
                    else:
                        state='attention'
                        if data_step:data_step.update(status='blocked',detail='至少一个字段分类不允许直接输出，或数据源当前不可用；请调整分类或输出方式。')
            return self.store.save('intake_flow',flow_id,{**flow,'steps':steps,'resources':resources,'state':state,
                'field_lookup':field_lookup,
                'confirmation':{'actor':actor,'note':note,'confirmed':True,
                    'reviewed_resource_ids':sorted(candidate_ids),
                    'accepted_resource_ids':sorted(accepted_ids)},
                'execution_authorized':False},flow['version'],actor,'人工确认完整能力链路；仅推进元数据准备')

    def specification_confirmed(self, flow, spec, actor):
        steps=[]
        for step in flow.get('steps',[]):
            item=dict(step)
            if item['capability'] in ('data_query','trend_analysis'):
                item['status']='awaiting_execution_adapter'
                item['detail']='只读SQL已通过目录规则并经人工确认，随任务规格版本保存。要执行查询，请在“方案与执行”中基于该规格建立逻辑视图和固定方案，再确认方案并单独申请授权。模型SQL不直接执行。'
            steps.append(item)
        needs_execution=any(step.get('capability') in ('data_query','trend_analysis') for step in steps)
        waiting_authorization=any(step.get('status')=='waiting_authorization' for step in steps)
        state='waiting_authorization' if waiting_authorization else 'awaiting_execution_plan' if needs_execution else 'completed'
        resources=[{**resource,'review_status':'human_confirmed'} for resource in flow.get('resources',[])]
        return self.store.save('intake_flow',flow['id'],{**flow,'steps':steps,'resources':resources,
            'state':state,'spec_id':spec['id'],'spec_version':spec['version'],
            'query_draft_confirmed_by':actor,'execution_authorized':False,
            'candidate_correction_pending':False},flow['version'],actor,
            '人工确认任务规格；平台继续自动准备固定方案草案')

    def revise_for_pseudonymized_id(self, flow_id, body, actor):
        """Revise a confirmed aggregate/name lookup only after an explicit human action."""
        flow=self.store.require('intake_flow',flow_id)
        demand=self.store.require('demand',flow['demand_id'])
        if demand.get('created_by')!=actor and actor!='demo-platform':raise DomainError('无权修订他人需求规格',403)
        if flow.get('state')!='awaiting_execution_plan':raise DomainError('当前流程不在执行方案准备阶段',409)
        spec=self.store.require('spec',flow.get('spec_id',''),flow.get('spec_version'))
        if not spec.get('confirmed') or spec['version']!=body.get('expected_spec_version'):
            raise DomainError('已确认规格版本变化，请刷新后重试',409)
        if body.get('expected_version')!=flow['version']:raise DomainError('需求链路版本已变化，请刷新后重试',409)
        current_constraints=spec.get('specification',{}).get('execution_constraints') or {}
        # Ordinary catalog-field lookups are object-level too. Route them from
        # the confirmed field binding, never from granularity alone.
        if current_constraints.get('field_output') or current_constraints.get('field_outputs'):
            return self.repair_field_query(flow, actor)
        if (current_constraints.get('scope',{}).get('kind')=='lookup'
                and current_constraints.get('output_mode')=='object'
                and current_constraints.get('identifier_output')=='pseudonymized'
                and spec.get('specification',{}).get('delivery')=='对象级'):
            return self._prepare_pseudonym_plan(flow,spec,actor)
        if body.get('confirmed') is not True or not str(body.get('note','')).strip():
            raise DomainError('请明确确认对象级伪名输出和姓名精确筛选，并填写确认说明')
        validation=flow.get('query_validation') or {}
        sql=spec.get('specification',{}).get('query_draft')
        if validation.get('status')!='valid_pending_human_confirmation' or sql!=validation.get('normalized_sql'):
            raise DomainError('已确认SQL与规则校验版本不一致，不能修订方案',409)
        from execution.query.intake_plan import query_shape
        approved=[resource['id'] for resource in flow.get('resources',[])
                  if resource.get('review_status')=='human_confirmed']
        shape=query_shape(sql,self.store,approved)
        if len(shape['filters'])!=1 or str(shape['filter_names'][0]).casefold() not in (
                'name','patient_name','fullname','full_name','姓名','患者姓名'):
            raise DomainError('当前SQL不是单一姓名精确查询；请先修订并确认查询草案',409)
        constraints=dict(spec['specification'].get('execution_constraints') or {})
        constraints.update(scope={'kind':'lookup','filters':shape['filters']},time_field=None,
                           output_mode='object',identifier_output='pseudonymized',
                           max_rows=min(int(constraints.get('max_rows',200)),shape['max_rows']))
        proposal={**demand,'expected_version':demand['version'],'object_scope':
            '在人工确认的数据表中按姓名字段精确匹配；不按出生日期或住院时间筛选；只返回患者伪名ID。',
            'delivery':'对象级','time_end':demand.get('deadline') or demand.get('time_end'),
            'execution_constraints':constraints}
        note=str(body['note']).strip()
        with self.store.atomic():
            updated=self.runtime.management.intake.save(proposal,actor)
            revised=self.runtime.management.intake.confirm(demand['id'],{
                'expected_version':updated['version'],
                'confirmations':{'content':True,'attachments':True,'gaps':True,'execution_constraints':True,
                    'quality_requirement_reviewed':body.get('quality_requirement_reviewed') is True},
                'query_sql_confirmed':True,'query_draft':sql,
                'note':note+'；新规格仅保留姓名精确筛选，移除出生日期数据筛选，结果为对象级HMAC患者伪名ID。'},actor)
            flow=self.specification_confirmed(flow,revised,actor)
        return self._prepare_pseudonym_plan(flow,revised,actor)

    def _prepare_pseudonym_plan(self, flow, revised, actor):
        from execution.query.intake_plan import create_plan
        view,plan,shape=create_plan(self.runtime,flow,revised,actor)
        steps=[]
        for step in flow.get('steps',[]):
            item=dict(step)
            if item.get('capability')=='data_query':
                item['status']='awaiting_plan_confirmation'
                item['detail']='已自动预填只读逻辑视图与固定方案草案。只用已确认的姓名精确筛选，输出本地密钥生成的HMAC伪名ID；未读取源数据行、未执行查询。请先确认方案，再另行申请授权。'
            steps.append(item)
        specification=revised['specification']
        current_draft={**flow.get('draft',{}),
            'object_scope':specification.get('object_scope'),
            'delivery':specification.get('delivery'),
            'time_end':specification.get('time_end'),
            'execution_constraints':specification.get('execution_constraints')}
        return self.store.save('intake_flow',flow['id'],{**flow,'steps':steps,
            'draft':current_draft,
            'draft_revision_note':'已按人工确认修订：姓名精确匹配；日期仅限制执行有效期，不作为患者数据筛选；输出患者伪名ID。',
            'state':'awaiting_plan_confirmation','execution_plan_id':plan['id'],
            'demand_version':revised['demand_version'],
            'execution_plan_version':plan['version'],'execution_view_id':view['id'],
            'execution_plan_preview':{'name':plan['name'],'plan_id':plan['id'],'version':plan['version'],
                'source_id':shape['table'].get('source_id'),'table':shape['table'].get('table') or shape['table'].get('name'),
                'filters':shape['filters'],'filter_names':shape['filter_names'],
                'output_columns':['patient_token'],'output_mode':'object',
                'transform':'HMAC-SHA256 · installation secret','reads_source_rows':False},
            'output_preference':'pseudonymized_patient_id','execution_authorized':False},
            flow['version'],actor,'人工确认修订规格并生成待确认伪名查询方案；未执行查询')

    def repair_output_field_classification(self,flow,actor):
        """Reopen classification review when a field was inferred only from its source."""
        lookup=flow.get('field_lookup') or {}
        if (not lookup or flow.get('execution_plan_id') or flow.get('execution_authorized')
                or flow.get('grant_id')):return flow
        field=self.store.get('object',lookup.get('field_id',''))
        if not field:return flow
        from execution.security.output_policy import infer_catalog_classification,field_classification
        inferred,_=infer_catalog_classification(field)
        classification=field_classification(field)
        manual_basis=str(field.get('classification_basis') or '').startswith('人工确认')
        if flow.get('state')=='waiting_field_classification' and manual_basis:
            allowed=classification in ('public','internal','health','identifier')
            newer_replacement=next((other for other in self.store.list('intake_flow')
                if other.get('id')!=flow.get('id') and other.get('demand_id')==flow.get('demand_id')
                and (other.get('field_lookup') or {}).get('field_id')==field['id']
                and other.get('execution_plan_id')
                and other.get('state') in ('awaiting_plan_confirmation','awaiting_authorization_request',
                    'awaiting_platform_authorization','awaiting_query_submission','query_queued','query_running',
                    'query_completed')
                and other.get('updated_at','')>flow.get('updated_at','')),None)
            confirmed_spec=(self.store.get('spec',flow.get('spec_id',''),flow.get('spec_version'))
                if flow.get('spec_id') else None)
            constraints=((confirmed_spec or {}).get('specification') or {}).get('execution_constraints') or {}
            confirmed_outputs=normalized_field_output_ids(constraints)
            already_confirmed=bool(confirmed_spec and confirmed_spec.get('confirmed')
                and set(lookup.get('field_ids') or [lookup.get('field_id')])<=confirmed_outputs
                and (confirmed_spec.get('specification') or {}).get('query_draft')==
                    (flow.get('query_validation') or {}).get('normalized_sql'))
            target_state='attention' if not allowed or newer_replacement else 'awaiting_execution_plan' if already_confirmed else 'waiting_manual_spec_confirmation'
            steps=[]
            for step in flow.get('steps',[]):
                item=dict(step)
                if item.get('capability')=='data_query':
                    item.update(status='blocked' if newer_replacement else
                        'awaiting_execution_adapter' if already_confirmed and allowed else
                        'waiting_manual_spec_confirmation' if allowed else 'blocked',
                        detail='此历史查询已由较新的字段查询流程取代；不会为它生成方案。'
                            if newer_replacement else
                        '字段分类已由人工确认；继续使用已确认SQL准备固定方案。'
                            if already_confirmed and allowed else
                        '字段分类已由人工确认；请确认输出字段规格后继续。' if allowed else
                        '平台方确认该字段不允许直接输出，当前流程已暂停。')
                steps.append(item)
            review={'field_id':field['id'],'field_name':field.get('name'),'classification':classification,
                'classification_basis':field.get('classification_basis'),'decision':'confirmed',
                'actor':field.get('updated_by') or actor,'at':field.get('updated_at')}
            notes=list(flow.get('notes',[]))
            if newer_replacement:notes.append('该历史查询已由较新的字段查询流程取代；未生成方案。')
            return self.store.save('intake_flow',flow['id'],{**flow,'steps':steps,'state':target_state,
                'field_lookup':{**lookup,'classification':field.get('classification'),
                    'classification_basis':field.get('classification_basis')},'classification_review':review,
                'notes':notes},flow['version'],actor,'恢复已人工确认的输出字段分类状态')
        auto_source_basis='字段名称/含义或所属医疗数据源表明其为健康信息'
        source_only_auto=(inferred=='unknown' and classification in ('public','internal','health')
            and field.get('classification_basis')==auto_source_basis)
        needs_review=(inferred=='unknown' and classification=='unknown') or source_only_auto
        if not needs_review or flow.get('state') not in (
                'awaiting_execution_plan','awaiting_plan_confirmation','waiting_manual_spec_confirmation'):
            return flow
        if source_only_auto:
            field=self.runtime.management.catalog.annotate(field['id'],{'expected_version':field['version'],
                'classification':'待确认','classification_basis':'待人工确认：字段名和含义不足以自动分类'},actor)
        steps=[]
        for step in flow.get('steps',[]):
            item=dict(step)
            if item.get('capability')=='data_query':
                item.update(status='waiting_field_classification',
                    detail='字段名或含义不足以可靠判断分类；平台方确认前不会生成方案或读取源数据。')
            steps.append(item)
        return self.store.save('intake_flow',flow['id'],{**flow,'steps':steps,
            'state':'waiting_field_classification',
            'field_lookup':{**lookup,'classification':'待确认',
                'classification_basis':'字段名/含义证据不足，需人工确认'},
            'classification_review':{'field_id':field['id'],'field_name':field.get('name'),
                'proposed_classification':None,'decision':'pending','reason':'自动判断依据不足'},
            'notes':list(flow.get('notes',[]))+['自动分类仅有数据源上下文依据，已恢复人工分类确认。']},
            flow['version'],actor,'字段分类依据不足；恢复人工分类确认')

    def _complete_confirmed_field_query_resources(self,flow,specification,actor):
        """Backfill catalog objects explicitly implied by the confirmed SQL join."""
        spec_body=specification.get('specification') or {}
        sql=spec_body.get('query_draft')
        validation=flow.get('query_validation') or {}
        if not specification.get('confirmed') or not sql or sql!=validation.get('normalized_sql'):
            raise DomainError('已确认规格与规则校验SQL不一致，不能补齐查询目录依赖',409)
        lookup=flow.get('field_lookup') or {}
        table=self.store.require('object',lookup.get('table_id',''))
        field=self.store.require('object',lookup.get('field_id',''))
        output_fields=[self.store.require('object',field_id) for field_id in
            (lookup.get('field_ids') or [field['id']])]
        name_field=self.store.require('object',lookup.get('name_field_id',''))
        if (table.get('kind')!='table' or field.get('kind')!='field'
                or field.get('parent_id')!=table['id'] or name_field.get('kind')!='field'):
            raise DomainError('已确认查询绑定的表或字段与当前目录不一致',409)
        if any(item.get('kind')!='field' or item.get('parent_id')!=table['id'] for item in output_fields):
            raise DomainError('多字段查询的输出字段必须属于同一张已确认目录表',409)
        required=[table,*output_fields,name_field];join_table=None;join_field_ids=[]
        if name_field.get('parent_id')!=table['id']:
            join_table=self.store.require('object',name_field.get('parent_id',''))
            if join_table.get('kind')!='table':
                raise DomainError('姓名字段所属目录对象不是已登记数据表，不能自动补齐关联',409)
            def patient_keys(parent_id):
                return [item for item in self.store.list('object') if item.get('kind')=='field'
                    and item.get('status')=='active' and item.get('parent_id')==parent_id
                    and str(item.get('name','')).casefold()=='patient_id']
            left,right=patient_keys(table['id']),patient_keys(join_table['id'])
            if len(left)!=1 or len(right)!=1:
                raise DomainError('目录无法唯一确认两张表的patient_id关联字段',409)
            if (join_table.get('source_id')!=table.get('source_id')
                    and not self._has_confirmed_patient_id_link(left[0]['id'],right[0]['id'])):
                raise DomainError('跨来源患者表关联尚未在目录中确认，不能生成固定方案',409)
            join_field_ids=[left[0]['id'],right[0]['id']]
            required.extend([join_table,*left,*right])
        from execution.query.intake_plan import field_query_shape
        prior={item.get('id'):item for item in flow.get('resources',[])}
        if any(prior.get(item['id'],{}).get('review_status')=='human_rejected' for item in required):
            raise DomainError('已确认SQL引用了曾被拒绝的目录对象，请重新确认目录候选',409)
        approved_ids={item.get('id') for item in flow.get('resources',[])
            if item.get('review_status')=='human_confirmed'}|{item['id'] for item in required}
        shape=field_query_shape(sql,self.store,approved_ids)
        policy=spec_body.get('execution_constraints') or {}
        expected_outputs=policy.get('field_outputs') or ([policy.get('field_output')] if policy.get('field_output') else [])
        expected_table_ids={table['id']}|({join_table['id']} if join_table else set())
        observed_table_ids={item['id'] for item in shape.get('tables',[])}
        expected_join_pairs={tuple(sorted(join_field_ids))} if join_field_ids else set()
        observed_join_pairs={tuple(sorted(pair)) for pair in shape.get('joins',[])}
        if (policy.get('scope',{}).get('filters')!=shape['filters']
                or set(expected_outputs)!={item['id'] for item in shape['output_fields']}
                or shape['output_field']['id']!=field['id']
                or shape['table']['id']!=table['id']
                or observed_table_ids!=expected_table_ids
                or observed_join_pairs!=expected_join_pairs):
            raise DomainError('已确认SQL的筛选或输出字段与目录绑定不一致',409)
        resources=[];resource_ids=set()
        for item in flow.get('resources',[]):
            current=self.store.get('object',item.get('id','')) or item
            resources.append({**current,**item,'review_status':item.get('review_status')})
            resource_ids.add(item.get('id'))
        for item in required:
            if item['id'] in resource_ids:continue
            reason=('已确认SQL使用的姓名精确筛选字段。' if item['id']==name_field['id'] else
                '已确认SQL引用的患者关联表；关系由目录patient_id字段验证。' if item['id']==(join_table or {}).get('id') else
                '已确认SQL使用的patient_id等值关联字段；不作为输出。' if item['id'] in join_field_ids else
                '已确认SQL绑定的输出表或字段。')
            resources.append({**item,'reason':reason,'review_status':'human_confirmed'})
        next_lookup={**lookup,'join_table_id':join_table.get('id') if join_table else None,
            'join_field_ids':join_field_ids}
        if resources==flow.get('resources') and next_lookup==lookup:return flow
        note='已按用户确认的SQL和目录patient_id关系补齐执行所需目录表与关联字段；未读取源数据行。'
        return self.store.save('intake_flow',flow['id'],{**flow,'resources':resources,'field_lookup':next_lookup,
            'notes':list(flow.get('notes',[]))+[note]},flow['version'],actor,
            '根据已确认SQL补齐目录patient_id关联依赖；未读取源数据行')

    def _prepare_catalog_field_plan(self,flow,specification,actor):
        from execution.query.intake_plan import create_field_plan
        from execution.security.output_policy import field_classification
        flow=self.repair_output_field_classification(flow,actor)
        if flow.get('state')=='waiting_field_classification':return flow
        if specification.get('confirmed'):
            flow={**flow,'resources':[{**resource,'review_status':'human_confirmed'}
                                      for resource in flow.get('resources',[])]}
        lookup=flow.get('field_lookup') or {}
        field=self.store.require('object',lookup.get('field_id',''))
        classification=field_classification(field)
        output_ids=lookup.get('field_ids') or [field['id']]
        output_fields=[self.store.require('object',field_id) for field_id in output_ids]
        output_classifications=[field_classification(item) for item in output_fields]
        if any(value not in ('public','internal','health','identifier') for value in output_classifications):
            raise DomainError('至少一个输出字段分类尚未确认或不允许输出，不能建立固定方案',403)
        policy=specification.get('specification',{}).get('execution_constraints') or {}
        expected_outputs=policy.get('field_outputs') or ([policy.get('field_output')] if policy.get('field_output') else [])
        if set(expected_outputs)!=set(output_ids):
            raise DomainError('任务规格输出字段与人工确认的目录匹配不一致',409)
        if specification.get('confirmed'):
            flow=self._complete_confirmed_field_query_resources(flow,specification,actor)
        view,plan,shape=create_field_plan(self.runtime,flow,specification,actor)
        steps=[]
        for step in flow.get('steps',[]):
            item=dict(step)
            if item.get('capability')=='data_query':
                output_names=[item.get('name') for item in output_fields]
                item.update(status='awaiting_plan_confirmation',detail=(
                    f"已建立 {shape['table'].get('name')} 固定方案草案；按姓名精确筛选并输出 {'、'.join(output_names)}。"
                    '未读取源数据行。个人标识需平台逐方案授权；其他敏感字段仍按各自分类审核。'))
            steps.append(item)
        description='输出 '+ '、'.join(
            f"{item.get('name')}（分类：{item.get('classification')}）" for item in output_fields)
        return self.store.save('intake_flow',flow['id'],{**flow,'steps':steps,
            'state':'awaiting_plan_confirmation','spec_id':specification['id'],
            'spec_version':specification['version'],'execution_plan_id':plan['id'],
            'execution_plan_version':plan['version'],'execution_view_id':view['id'],
            'execution_plan_preview':{'name':plan['name'],'plan_id':plan['id'],'version':plan['version'],
                'source_id':shape['table'].get('source_id'),'table':shape['table'].get('table') or shape['table'].get('name'),
                'filters':shape['filters'],'filter_names':shape['filter_names'],
                'output_columns':[item.get('name') for item in output_fields],'output_description':description,
                'output_mode':'object','reads_source_rows':False,
                'health_output_authorization_required':'health' in output_classifications,
                'identifier_output_authorization_required':'identifier' in output_classifications},
            'output_preference':'catalog_field','execution_authorized':False,
            'demand_version':specification.get('demand_version')},flow['version'],actor,
            '已按人工确认的目录字段和SQL建立固定方案草案；未读取源数据行')

    def _prepare_admission_time_plan(self, flow, specification, actor):
        from execution.security.output_policy import field_classification
        catalog=self._catalog_metadata()
        lookup=self._admission_time_lookup(flow.get('request_text'),catalog)
        if not lookup:raise DomainError('目录元数据无法重建入院时间字段和患者关联，暂不能生成方案',409)
        classification=field_classification(lookup['admission_field'])
        if classification not in ('public','internal','health'):
            raise DomainError('入院时间字段分类不支持固定方案；请先复核目录字段分类',409)
        constraints=specification.get('specification',{}).get('execution_constraints') or {}
        if constraints.get('scope',{}).get('filters')!=[
            {'field':lookup['name_field']['id'],'op':'=','value':lookup['name']},
            {'field':lookup['visit_type_field']['id'],'op':'=','value':'住院'}]:
            raise DomainError('已确认规格的姓名或住院类型筛选与目录查询草案不一致',409)
        if (constraints.get('time_field')!=lookup['admission_field']['id']
                or constraints.get('output_mode')!='object'):
            raise DomainError('已确认规格的时间字段或输出粒度与入院时间查询不一致',409)
        field_ids=[lookup['name_field']['id'],lookup['patient_key']['id'],lookup['visit_key']['id'],
                   lookup['visit_type_field']['id'],lookup['admission_field']['id']]
        view_id='VIEW-'+flow['id']+'-ADMISSION-TIME'
        old_view=self.runtime.execution.store.get('view',view_id)
        view_body={'id':view_id,'expected_version':old_view['version'] if old_view else 0,
            'name':'患者入院时间查询 · '+flow['id'],'fields':field_ids,
            'output_fields':[lookup['admission_field']['id']],
            'joins':[[lookup['patient_key']['id'],lookup['visit_key']['id']]],
            'filters':[],'group_by':[],'output_mode':'object'}
        view=self.runtime.execution.queries.define(view_body,actor)
        plan_id='PLAN-'+flow['id']+'-ADMISSION-TIME'
        old_plan=self.runtime.execution.store.get('plan',plan_id)
        plan=self.runtime.execution.plan({'id':plan_id,'expected_version':old_plan['version'] if old_plan else 0,
            'name':'患者入院时间受控查询 · '+flow['id'],'spec_id':specification['id'],
            'spec_version':specification['version'],'output_mode':'object',
            'circulation_mode':'controlled_compute','quality_min_complete':1,
            'nodes':[{'id':'admission-time','operation':'view','view_id':view['id'],
                      'depends_on':[],'params':{}}],'output_node':'admission-time'},actor)
        steps=[]
        for step in flow.get('steps',[]):
            item=dict(step)
            if item.get('capability')=='data_query':
                item['status']='awaiting_plan_confirmation'
                item['detail']=('固定方案已按姓名、住院类型和入院时间自动预填。未读取源数据行；方案确认后由需求方提交入院时间原值交付申请，平台方最终审批。'
                    if classification=='health' else
                    '固定方案已按姓名、住院类型和入院时间自动预填。未读取源数据行；请确认方案后再单独申请授权。')
            steps.append(item)
        filters=[{'field':lookup['name_field']['id'],'op':'=','value':lookup['name']},
                 {'field':lookup['visit_type_field']['id'],'op':'=','value':'住院'}]
        draft={**flow.get('draft',{}),'execution_constraints':constraints,
            'object_scope':specification['specification'].get('object_scope'),
            'delivery':'对象级'}
        return self.store.save('intake_flow',flow['id'],{**flow,'steps':steps,'draft':draft,
            'state':'awaiting_plan_confirmation','spec_id':specification['id'],
            'spec_version':specification['version'],'execution_plan_id':plan['id'],
            'execution_plan_version':plan['version'],'execution_view_id':view['id'],
            'execution_plan_preview':{'name':plan['name'],'plan_id':plan['id'],'version':plan['version'],
                'source_id':lookup['visit_table'].get('source_id'),'table':
                    lookup['patient_table'].get('name')+' + '+lookup['visit_table'].get('name'),
                'filters':filters,'filter_names':['name','vtype'],'output_columns':['admission_time'],
                'output_description':('仅返回入院时间原值（健康信息；待需求方申请并由平台方批准）' if classification=='health'
                                      else '仅返回入院时间；不返回姓名或患者ID'),
                'output_mode':'object','transform':'按已确认字段分类和平台方审批的需求方申请控制查看',
                'health_output_authorization_required':classification=='health','reads_source_rows':False},
            'execution_authorized':False,'output_preference':'admission_time',
            'execution_output_blocker':None,'auto_adjusted_spec':True,'auto_prepare_admission_time_plan':True},flow['version'],actor,
            '规格确认后自动生成入院时间固定方案草案；未执行查询')
