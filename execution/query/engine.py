"""Bounded local federated queries. Identifier whitelist from A, values bound separately."""
import hashlib,json,sqlite3,time
from pathlib import Path
import medsim
from platform_core.storage import DomainError,new_id
from execution.query.expressions import compile_expression,mapping_expression
from execution.security.output_policy import field_type,field_classification


def quote(value):return '"'+str(value).replace('"','""')+'"'


class Queries:
    def __init__(self,management,store):self.management,self.store=management,store
    def define(self,body,actor):
        selected=list(body.get('fields',[]));mapping_versions={};projections={};joins=list(body.get('joins',[]))
        for cid in body.get('concept_ids',[]):
            resolved=self.management.semantic.resolve(cid)
            mappings=resolved['mappings']
            if len(mappings)!=1:raise DomainError('概念存在多条映射，请先明确当前唯一可执行口径')
            for mapping in mappings:
                selected+=mapping['fields'];mapping_versions[mapping['id']]=mapping['version']
                expression=mapping_expression(mapping)
                compile_expression(expression,{fid:'field' for fid in mapping['fields']},[])
                projections[cid]={'name':resolved['concept']['name'],'expression':expression}
                if mapping['mapping_type']=='join':joins.extend(mapping['expression']['on'])
        selected=list(dict.fromkeys(selected))
        if not selected or len(selected)>30:raise DomainError('视图需选择1至30个字段')
        fields=[self.management.store.require('object',oid) for oid in selected]
        if any(f['kind']!='field' or f['status']!='active' for f in fields):raise DomainError('字段已删除或不是字段')
        if any(f['source_id'].startswith('cloud-') for f in fields):raise DomainError('云盘查询请使用外部数据工作台的会话授权入口；当前本地视图不复制云盘凭据')
        groups={(f['source_id'],f['table']) for f in fields}
        if len(groups)>4:raise DomainError('本地查询最多4张源表')
        for pair in joins:
            if not isinstance(pair,list) or len(pair)!=2 or any(x not in selected for x in pair):raise DomainError('连接需使用已选字段的等值关联')
        reached={next(iter(groups))}
        for _ in groups:
            for a,b in joins:
                x=next(f for f in fields if f['id']==a);y=next(f for f in fields if f['id']==b)
                gx,gy=(x['source_id'],x['table']),(y['source_id'],y['table'])
                if gx in reached:reached.add(gy)
                if gy in reached:reached.add(gx)
        if reached!=groups:raise DomainError('跨表视图必须显式关联，拒绝隐式笛卡尔积')
        for condition in body.get('filters',[]):
            if condition.get('field') not in selected or condition.get('op') not in ('=','!=','>','>=','<','<='):raise DomainError('过滤字段或运算符非法')
        if any(x not in selected and x not in projections for x in body.get('group_by',[])):raise DomainError('分组字段不在视图中')
        oid=body.get('id') or new_id('VIEW');old=self.store.get('view',oid)

        if old and old.get('created_by')!=actor and actor!='demo-platform':raise DomainError('不能修改他人视图',403)
        requested_output_fields=body.get('output_fields',body.get('fields',[]))
        if not isinstance(requested_output_fields,list):raise DomainError('视图输出字段需为字段编号列表')
        output_fields=list(dict.fromkeys(requested_output_fields+list(projections)))
        if any(field_id not in selected and field_id not in projections for field_id in output_fields):raise DomainError('输出字段必须包含在逻辑视图字段或已确认映射中')
        pseudonymize_fields=body.get('pseudonymize_fields',[])
        if (not isinstance(pseudonymize_fields,list) or len(pseudonymize_fields)>1
                or any(field_id not in output_fields for field_id in pseudonymize_fields)):
            raise DomainError('当前只支持将一个已选择的输出字段映射为患者伪名ID')
        data={'created_by':(old or {}).get('created_by',actor),'name':body.get('name') or oid,'fields':selected,'field_versions':{f['id']:f['version'] for f in fields},'mapping_versions':mapping_versions,
              'projections':projections,'output_fields':output_fields,'pseudonymize_fields':list(dict.fromkeys(pseudonymize_fields)),'joins':joins,'filters':body.get('filters',[]),'group_by':body.get('group_by',[]),'output_mode':body.get('output_mode','aggregate')}
        if data['output_mode'] not in ('aggregate','object'):raise DomainError('输出粒度非法')
        if pseudonymize_fields:
            if data['output_mode']!='object':raise DomainError('患者伪名ID只允许对象级受控输出')
            by_id={f['id']:f for f in fields}
            if pseudonymize_fields[0] not in by_id:raise DomainError('伪名字段必须是目录中的直接字段，不能使用未审查映射')
            field=by_id[pseudonymize_fields[0]]
            if field_classification(field)!='identifier' or field_type(field) not in ('integer','string'):
                raise DomainError('只有已识别的患者标识字段可以生成患者伪名ID')
            if str(field.get('name','')).casefold() not in ('patient_id','person_id','medical_record_no','mrn'):
                raise DomainError('患者伪名ID转换仅支持患者ID类目录字段')
        result=self.store.save('view',oid,data,body.get('expected_version',0),actor,'逻辑视图定义')
        self.management.register_design({'object_kind':'view','object_id':oid,'object_version':result['version'],'environment':self.store.environment,'definition':result,'dependencies':selected})
        return result
    def sources(self,view):
        fields=[]
        for oid,version in view['field_versions'].items():
            f=self.management.store.require('object',oid)
            if f['version']!=version or f['status']!='active':raise DomainError('源结构版本变化，请重建视图并确认方案',409)
            fields.append(f)
        for mid,version in view['mapping_versions'].items():
            m=self.management.store.require('mapping',mid)
            if m['version']!=version or m['status']!='confirmed':raise DomainError('概念映射需要重新确认',409)
            self.management.semantic.resolve(m['concept_id'])
        groups={}
        for f in fields:groups.setdefault((f['source_id'],f['table']),[]).append(f)
        sources=[]
        for (sid,table),columns in sorted(groups.items()):
            if sid in ('his','lis','pacs','iot'):path=Path(medsim.DATA_DIR)/(sid+'.db')
            elif sid=='emr':path=Path(medsim.DATA_DIR)/'emr'
            else:
                registration=self.management.store.get('source',sid)
                if not registration or registration.get('connector')!='sqlite':raise DomainError('此逻辑查询当前支持本地SQLite连接器')
                path=(Path(medsim.DATA_DIR)/registration['path']).resolve()
            if sid=='emr':
                if not path.is_dir() or not any(path.glob('*.json')):raise DomainError('EMR 文书目录不可达',503)
            elif not path.is_file():raise DomainError('源不可达',503)
            root=Path(medsim.DATA_DIR).resolve()
            if not path.resolve().is_relative_to(root):raise DomainError('源不在允许的数据目录',403)
            sources.append((sid,table,path.resolve(),columns))
        return sources
    def execute(self,view,mode,min_group=5,constraints=None,max_rows=200,authorized_health_fields=(),
                authorized_identifier_fields=()):
        import duckdb
        if mode!=view['output_mode']:raise DomainError('视图输出粒度与授权不一致',403)
        sources=self.sources(view)
        constraints=constraints or {}
        selective_fields={condition['field'] for condition in view['filters']+constraints.get('filters',[])}
        if constraints.get('time_field'):selective_fields.add(constraints['time_field'])
        sources.sort(key=lambda source:(
            not any(field['id'] in selective_fields for field in source[3]),source[0],source[1]))
        from execution.security.output_policy import view_output_policy,sensitive_output_allowed
        fields_by_id={f['id']:f for _,_,_,fields in sources for f in fields}
        output_policy=view_output_policy(view,fields_by_id)
        required={c['field'] for c in constraints.get('filters',[])}|({constraints['time_field']} if constraints.get('time_field') else set())
        if not required<=set(view['field_versions']):raise DomainError('查询缺少执行条件字段',403)
        pseudonymizer=None
        if view.get('pseudonymize_fields'):
            from execution.security.pseudonym import PatientPseudonymizer
            pseudonymizer=PatientPseudonymizer(self.store)
        stamp=[]
        for sid,table,path,fields in sources:
            if sid=='emr' and path.is_dir():
                source_stamp=[(entry.name,entry.stat().st_mtime_ns,entry.stat().st_size)
                        for entry in sorted(path.glob('*.json')) if entry.is_file()]
                wal_stamp=0
            else:
                source_stamp=path.stat().st_mtime_ns
                wal=Path(str(path)+'-wal');wal_stamp=wal.stat().st_mtime_ns if wal.exists() else 0
            stamp.append((sid,table,source_stamp,[(f['id'],f['version']) for f in fields],wal_stamp))
        # Exclude old materializations that serialized SQLite booleans as strings.
        pseudonym_key_id=hashlib.sha256(pseudonymizer.key).hexdigest() if pseudonymizer else None
        key=hashlib.sha256(json.dumps(['sqlite-types-v4',view['id'],view['version'],stamp,mode,min_group,max_rows,constraints,
            sorted(set(authorized_health_fields)),sorted(set(authorized_identifier_fields)),pseudonym_key_id],sort_keys=True).encode()).hexdigest()
        cached=self.store.get('materialization',key)
        if cached and cached['expires_epoch']>time.time():
            if not sensitive_output_allowed(cached['result'],{'output_policy':output_policy},authorized_health_fields,
                    authorized_identifier_fields=authorized_identifier_fields):
                raise DomainError('缓存查询输出未通过当前字段授权复核，未返回结果',403)
            return cached['result'],{'cache_hit':True,'materialization_id':key,'sources':cached['sources']}
        con=duckdb.connect(':memory:');deadline=time.monotonic()+5;locations={};trace=[];loaded_values={}
        try:
            con.execute("SET enable_external_access=false");con.execute("SET memory_limit='128MB'");con.execute('SET threads=1')
            for index,(sid,table,path,fields) in enumerate(sources):
                params=[];where=[];byid={f['id']:f for f in fields};emr_patient_candidates=[]
                for condition in view['filters']+constraints.get('filters',[]):
                    if condition['field'] in byid:
                        where.append(quote(byid[condition['field']]['name'])+' '+condition['op']+' ?');params.append(condition.get('value'))
                if constraints.get('time_field') in byid:
                    column=quote(byid[constraints['time_field']]['name'])
                    where.extend(['julianday('+column+') >= julianday(?)','julianday('+column+') < julianday(?)'])
                    params.extend([constraints['time_start'],constraints['time_end_exclusive']])
                # Push exact filters through registered equality joins before
                # scanning a related source table.
                for left_id,right_id in view.get('joins',[]):
                    current_id=prior_id=None
                    if left_id in byid and right_id in loaded_values:
                        current_id,prior_id=left_id,right_id
                    elif right_id in byid and left_id in loaded_values:
                        current_id,prior_id=right_id,left_id
                    if not current_id:continue
                    values=list(dict.fromkeys(value for value in loaded_values[prior_id] if value is not None))
                    if not values:
                        where.append('1=0')
                    elif len(values)>900:
                        raise DomainError('关联候选超过本地参数预算，请增加更精确的筛选条件')
                    else:
                        where.append(quote(byid[current_id]['name'])+' IN ('+','.join('?' for _ in values)+')')
                        params.extend(values)
                        # EMR documents are stored as <patient_id>.json. Keep
                        # the propagated join values so the connector can open
                        # only the matching documents instead of scanning the
                        # entire directory before SQLite applies the filter.
                        if (sid=='emr' and
                                str(byid[current_id].get('name','')).casefold()=='patient_id'):
                            emr_patient_candidates.append(values)
                source_budget=min(10000,max_rows) if mode=='object' and len(sources)==1 else 10000
                sql='SELECT '+','.join(quote(f['name']) for f in fields)+' FROM '+quote(table)+(' WHERE '+' AND '.join(where) if where else '')+' LIMIT '+str(source_budget+1)
                sql_types={'integer':' BIGINT','number':' DOUBLE','boolean':' BOOLEAN','string':' VARCHAR'}
                types=[field_type(f) for f in fields]
                if sid=='emr':
                    db=sqlite3.connect(':memory:')
                    declared=','.join(quote(field['name'])+sql_types[dtype]
                        for field,dtype in zip(fields,types))
                    db.execute('CREATE TABLE '+quote(table)+' ('+declared+')')
                    projected=[]
                    if emr_patient_candidates:
                        # Multiple confirmed equality joins are conjunctive in
                        # SQL. Any one patient_id candidate set narrows the
                        # file lookup; SQLite still verifies every predicate.
                        candidate_ids=list(dict.fromkeys(str(value)
                            for value in emr_patient_candidates[0] if value is not None))
                        documents=[]
                        for patient_id in candidate_ids:
                            document=(path/(patient_id+'.json')).resolve()
                            if document.is_relative_to(path.resolve()) and document.is_file():
                                documents.append(document)
                    else:
                        documents=sorted(path.glob('*.json'))
                    for document in documents:
                        if time.monotonic()>deadline:
                            db.close()
                            raise DomainError('本地查询超过时间预算，请增加筛选条件')
                        if not document.is_file() or not document.resolve().is_relative_to(path.resolve()):continue
                        try:
                            with document.open(encoding='utf-8') as stream:record=json.load(stream)
                        except (OSError,ValueError) as exc:
                            db.close()
                            raise DomainError('EMR 文书文件无法解析，查询已停止') from exc
                        if not isinstance(record,dict):continue
                        if emr_patient_candidates:
                            patient_field=next((field for field in fields
                                if str(field.get('name','')).casefold()=='patient_id'),None)
                            if (patient_field is None or
                                    str(record.get(patient_field['name'])) not in candidate_ids):
                                continue
                        values=[]
                        for field in fields:
                            value=record.get(field['name'])
                            if isinstance(value,(dict,list)):value=json.dumps(value,ensure_ascii=False,separators=(',',':'))
                            elif value is not None and not isinstance(value,(str,int,float,bool,bytes)):
                                value=str(value)
                            values.append(value)
                        projected.append(tuple(values))
                    if projected:
                        db.executemany('INSERT INTO '+quote(table)+' VALUES ('+
                            ','.join('?' for _ in fields)+')',projected)
                else:
                    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
                try:
                    db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
                    rows=db.execute(sql,params).fetchall()
                finally:db.close()
                if len(rows)>source_budget:raise DomainError('源返回超过本次授权行数或本地查询预算，请增加筛选条件')
                alias='s'+str(index)
                boolean_columns=[i for i,dtype in enumerate(types) if dtype=='boolean']
                if boolean_columns:
                    converted=[]
                    for row in rows:
                        values=list(row)
                        for i in boolean_columns:
                            value=values[i]
                            if value is not None:
                                if type(value) not in (int,bool) or value not in (0,1):
                                    raise DomainError('布尔字段只能包含0、1或NULL，拒绝隐式类型转换')
                                values[i]=bool(value)
                        converted.append(values)
                    rows=converted
                con.execute('CREATE TABLE '+alias+'('+','.join('c'+str(i)+sql_types[dtype] for i,dtype in enumerate(types))+')')
                if rows:con.executemany('INSERT INTO '+alias+' VALUES ('+','.join('?' for _ in fields)+')',rows)
                for i,f in enumerate(fields):locations[f['id']]=alias+'.c'+str(i)
                for i,f in enumerate(fields):loaded_values[f['id']]=[row[i] for row in rows]
                trace.append({'source_id':sid,'table':table,'rows_read':len(rows),'pushed_projection':[f['name'] for f in fields],'pushed_filters':len(where),'local_alias':alias})
            predicates=[locations[a]+'='+locations[b] for a,b in view['joins']]
            tables=','.join('s'+str(i) for i in range(len(sources)))
            selected=view['group_by'] if mode=='aggregate' else view.get('output_fields',view['fields'])
            projections=view.get('projections',{})
            pseudonymize_fields=set(view.get('pseudonymize_fields',[]))
            names=['patient_token' if oid in pseudonymize_fields else
                   projections[oid]['name'] if oid in projections else self.management.store.require('object',oid)['name'] for oid in selected]
            # Stable deduplication avoids ambiguous response columns.
            names=[name if names.count(name)==1 else name+'_'+str(i) for i,name in enumerate(names)]
            if mode=='aggregate':names=['count_group' if name=='count' else name for name in names]
            bound=[]
            projection=[compile_expression(projections.get(oid,{'expression':{'field':oid}})['expression'],locations,bound)+' AS '+quote(name) for oid,name in zip(selected,names)]
            if mode=='aggregate':projection.append('COUNT(*) AS count')
            sql='SELECT '+','.join(projection)+' FROM '+tables+(' WHERE '+' AND '.join(predicates) if predicates else '')
            if mode=='aggregate':sql+=(' GROUP BY '+','.join(str(i+1) for i in range(len(selected))) if selected else '')+' HAVING COUNT(*) >= '+str(int(min_group))
            sql+=' LIMIT 201'
            physical_plan=con.execute('EXPLAIN '+sql,bound).fetchall()[-1][-1]
            import threading
            timer=threading.Timer(max(.01,deadline-time.monotonic()),con.interrupt);timer.start()
            try:cursor=con.execute(sql,bound);rows=cursor.fetchall();columns=[x[0] for x in cursor.description]
            finally:timer.cancel()
            if len(rows)>min(200,max_rows):raise DomainError('结果超过本次授权行数或交付预算，请缩小查询范围')
            output_rows=[list(r) for r in rows]
            if pseudonymize_fields:
                for field_id in pseudonymize_fields:
                    if field_id not in selected:raise DomainError('伪名字段没有进入输出，拒绝执行',403)
                    index=selected.index(field_id)
                    output_rows=[row[:index]+[pseudonymizer.token(row[index])]+row[index+1:] for row in output_rows]
            result={'columns':columns,'rows':output_rows,'notice':'部分分组可能未满足输出条件' if mode=='aggregate' else '本地对象级结果'}
            if not sensitive_output_allowed(result,{'output_policy':output_policy},authorized_health_fields,
                    authorized_identifier_fields=authorized_identifier_fields):
                raise DomainError('查询输出未通过字段分类或敏感内容检查，未缓存结果',403)
            old=self.store.get('materialization',key)
            self.store.save('materialization',key,{'result':result,'sources':trace,'expires_epoch':time.time()+60,'view_id':view['id']},old['version'] if old else 0,'execution','按版本与源指纹缓存，命中仍需重新授权')
            return result,{'cache_hit':False,'materialization_id':key,'sources':trace,'optimizer':'DuckDB本地代价优化','physical_plan':physical_plan,'chosen_plan':'字段/过滤下推后本地等值连接','alternatives':[{'name':'全表拉取','selected':False,'reason':'传输与内存成本较高'}]}
        finally:con.close()
