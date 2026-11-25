"""Bounded federated queries with catalog-bound fields and separate values."""
import hashlib,json,sqlite3,time
from contextlib import closing
from pathlib import Path
import medsim
from platform_core.storage import DomainError,new_id
from execution.query.expressions import compile_expression,mapping_expression
from execution.security.output_policy import field_type,field_classification
from contracts.execution.constraints import validate_calculation
from management.intake.calculations import person_identifier, summary_count_kind, SUMMARY_SCOPES, validate_summary_scope
from contracts.execution.filters import bind_filter, filter_values


def quote(value):return '"'+str(value).replace('"','""')+'"'


def load_emr(db,path,table,fields,deadline,candidate_ids=()):
    types={'integer':' BIGINT','number':' DOUBLE','boolean':' BOOLEAN','string':' VARCHAR'}
    declared=','.join(quote(field['name'])+types[field_type(field)] for field in fields)
    db.execute('CREATE TABLE '+quote(table)+' ('+declared+')')
    if candidate_ids:
        documents=(path/(patient_id+'.json') for patient_id in candidate_ids)
    else:
        documents=path.glob('*.json')
    def records():
        for document in documents:
            if time.monotonic()>deadline:
                raise DomainError('本地查询超过时间预算，请增加筛选条件',503)
            if not document.is_file() or not document.resolve().is_relative_to(path):
                continue
            try:
                with document.open(encoding='utf-8') as stream:
                    record=json.load(stream)
            except (OSError,ValueError) as exc:
                raise DomainError('EMR 文书文件无法解析，查询已停止') from exc
            if not isinstance(record,dict) or (candidate_ids and str(record.get('patient_id')) not in candidate_ids):
                continue
            values=[record.get(field['name']) for field in fields]
            yield tuple(json.dumps(value,ensure_ascii=False,separators=(',',':'))
                        if isinstance(value,(dict,list)) else value for value in values)
    db.executemany('INSERT INTO '+quote(table)+' VALUES ('+','.join('?' for _ in fields)+')',records())


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
            if condition.get('field') not in selected:
                raise DomainError('过滤字段或运算符非法')
            filter_values(condition['op'],condition.get('value'))
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
        if body.get('join_validation')=='catalog':
            data['join_validation']='catalog'
        if body.get('distinct') is True:
            data['distinct']=True
        if data['output_mode'] not in ('aggregate','object'):raise DomainError('输出粒度非法')
        if 'calculation' in body:
            calculation=validate_calculation(body['calculation'])
            table=self.management.store.require('object',calculation['table'])
            if (data['output_mode']!='aggregate' or data['group_by'] or output_fields
                    or table.get('kind')!='table' or table.get('status')!='active'
                    or (calculation['kind']!='person_count' and
                        (joins or any(f.get('parent_id')!=table['id'] for f in fields)))
                    or (joins and data.get('join_validation')!='catalog')):
                raise DomainError('计数视图需要已登记的数据输入和关联口径')
            by_id={field['id']:field for field in fields}
            measure=by_id.get(calculation['field'])
            if calculation['kind']=='person_count' and not (measure and measure['parent_id']==table['id']
                                                            and person_identifier(table,measure)):
                raise DomainError('去重人数需要已确认的人员标识字段')
            if calculation['kind'] in SUMMARY_SCOPES and not (measure and summary_count_kind(table,measure)==calculation['kind']):
                raise DomainError('人数汇总仅可使用已登记的汇总视图')
            data['calculation']=calculation
        if 'row_limit' in body or 'order_by' in body:
            row_limit=body.get('row_limit',200);ordering=body.get('order_by',[])
            if data['output_mode']!='object' or type(row_limit) is not int or not 1<=row_limit<=200:
                raise DomainError('排序和返回条数仅支持对象查询，条数为1至200')
            if not isinstance(ordering,list) or len(ordering)>10:
                raise DomainError('排序字段最多10个')
            for item in ordering:
                if (not isinstance(item,dict) or set(item)!={'field','direction','nulls_first'}
                        or item['field'] not in selected or item['direction'] not in ('asc','desc')
                        or type(item['nulls_first']) is not bool):
                    raise DomainError('排序字段或方向不合法')
            data.update(row_limit=row_limit,order_by=ordering)
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
        if view.get('join_validation')=='catalog':
            from management.catalog.query_links import executable_links
            _,_,edges=executable_links(self.management.store)
            if any(tuple(sorted(pair)) not in edges for pair in view['joins']):
                raise DomainError('目录关联已变化，请重新核对查询路径',409)
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
            if sid.startswith('doris-'):
                doris=self.management.catalog.doris
                if doris is None:
                    raise DomainError('Doris 连接器未启动',503)
                sources.append((sid,table,doris.for_table(sid,table,columns),columns))
                continue
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

    def calculate(self,view,sources,constraints,min_group):
        if len(sources)!=1:
            raise DomainError('计数服务需要单一可用数据源')
        source_id,table,source,fields=sources[0]
        calculation=view['calculation']
        summary=calculation['kind'] in SUMMARY_SCOPES
        by_id={f['id']:f for f in fields}
        conditions=view['filters']+constraints.get('filters',[])
        if summary:
            cohort=next((field['id'] for field in fields if field['name']=='cohort'),None)
            validate_summary_scope(calculation['kind'],conditions,cohort)
        remote=source_id.startswith('doris-')
        if remote:
            from management.catalog.doris import identifier
            column=identifier
            relation=source.table_sql(table)
            placeholder='%s'
        else:
            column=quote
            relation=quote(table)
            placeholder='?'
        measure=by_id.get(calculation['field'])
        if calculation['kind']=='row_count':
            metric='COUNT(*)'
        elif summary:
            metric='MAX('+column(measure['name'])+')'
        else:
            field=column(measure['name'])
            if field_type(measure)=='string':
                field="NULLIF(TRIM("+field+"), '')"
            metric='COUNT(DISTINCT '+field+')'
        predicates=[]
        values=[]
        for item in conditions:
            predicate,args=bind_filter(column(by_id[item['field']]['name']),item['op'],item['value'],placeholder)
            predicates.append(predicate)
            values.extend(args)
        sql='SELECT '+('/*+ SET_VAR(query_timeout=6) */ ' if remote else '')+metric+' FROM '+relation
        if predicates:sql+=' WHERE '+' AND '.join(predicates)
        if remote:
            with source.cursor() as cursor:
                cursor.execute(sql,tuple(values))
                count=cursor.fetchone()[0]
        else:
            target=':memory:' if source_id=='emr' else source.as_uri()+'?mode=ro'
            deadline=time.monotonic()+6
            with closing(sqlite3.connect(target,uri=True)) as connection:
                if source_id=='emr':
                    load_emr(connection,source,table,fields,deadline)
                else:
                    from management.catalog.derived_fields import attach_age_view
                    attach_age_view(connection,table,fields,quote)
                connection.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
                try:count=connection.execute(sql,values).fetchone()[0]
                except sqlite3.OperationalError as exc:
                    raise DomainError('源端计数无法完成，请检查数据表或缩小统计范围',503) from exc
        if count is None and summary and min_group==0:
            raise DomainError('Doris 未返回该口径的人数，请联系管理员检查人数汇总视图',503)
        if count is not None and (type(count) is not int or count<0):
            raise DomainError('源端统计结果格式不正确')
        suppressed=count is None or count<min_group
        result={'columns':['count'],'rows':[] if suppressed else [[count]]}
        return result,{'cache_hit':False,'calculation':calculation['kind'],'sources':[{
            'source_id':source_id,'table':table,'connector':'doris' if remote else 'local',
            'pushed_filters':len(conditions)}]}

    def execute(self,view,mode,min_group=5,constraints=None,max_rows=200,authorized_health_fields=(),
                authorized_identifier_fields=()):
        import duckdb
        if mode!=view['output_mode']:raise DomainError('视图输出粒度与授权不一致',403)
        sources=self.sources(view)
        constraints=constraints or {}
        calculation=view.get('calculation')
        if calculation and len(sources)==1:
            return self.calculate(view,sources,constraints,min_group)
        requested_limit=view.get('row_limit')
        if requested_limit and requested_limit>max_rows:raise DomainError('查询条数超过当前授权上限',403)
        selective_fields={condition['field'] for condition in view['filters']+constraints.get('filters',[])}
        if constraints.get('time_field'):selective_fields.add(constraints['time_field'])
        sources.sort(key=lambda source:(
            not any(field['id'] in selective_fields for field in source[3]),source[0],source[1]))
        if view.get('join_validation')=='catalog' and len(sources)>2:
            pending=sources[1:]
            sources=sources[:1]
            reached={field['id'] for field in sources[0][3]}
            while pending:
                adjacent={right if left in reached else left for left,right in view['joins']
                          if (left in reached)!=(right in reached)}
                index=next(i for i,source in enumerate(pending) if any(field['id'] in adjacent for field in source[3]))
                chosen=pending.pop(index)
                reached.update(field['id'] for field in chosen[3])
                sources.append(chosen)
        from execution.security.output_policy import view_output_policy,sensitive_output_allowed
        fields_by_id={f['id']:f for _,_,_,fields in sources for f in fields}
        output_policy=view_output_policy(view,fields_by_id)
        required={c['field'] for c in constraints.get('filters',[])}|({constraints['time_field']} if constraints.get('time_field') else set())
        if not required<=set(view['field_versions']):raise DomainError('查询缺少执行条件字段',403)
        pseudonymizer=None
        if view.get('pseudonymize_fields'):
            from execution.security.pseudonym import PatientPseudonymizer
            pseudonymizer=PatientPseudonymizer(self.store)
        live=any(sid.startswith('doris-') for sid,_,_,_ in sources)
        key=cached=None
        if not live:
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
            pseudonym_key_id=hashlib.sha256(pseudonymizer.key).hexdigest() if pseudonymizer else None
            key=hashlib.sha256(json.dumps(['sqlite-types-v4',view['id'],view['version'],stamp,mode,min_group,max_rows,constraints,
                sorted(set(authorized_health_fields)),sorted(set(authorized_identifier_fields)),pseudonym_key_id],sort_keys=True).encode()).hexdigest()
            cached=self.store.get('materialization',key)
        if cached and cached['expires_epoch']>time.time():
            if not sensitive_output_allowed(cached['result'],{'output_policy':output_policy},authorized_health_fields,
                    authorized_identifier_fields=authorized_identifier_fields):
                raise DomainError('缓存查询输出未通过当前字段授权复核，未返回结果',403)
            return cached['result'],{'cache_hit':True,'materialization_id':key,'sources':cached['sources']}
        con=duckdb.connect(':memory:');deadline=time.monotonic()+(25 if live else 5);locations={};trace=[];loaded_values={}
        try:
            con.execute("SET enable_external_access=false");con.execute("SET memory_limit='128MB'");con.execute('SET threads=1')
            for index,(sid,table,path,fields) in enumerate(sources):
                if time.monotonic()>deadline:
                    raise DomainError('查询超过执行时间预算，请缩小查询范围',503)
                params=[];where=[];byid={f['id']:f for f in fields};emr_patient_candidates=[]
                remote_filters=[];remote_candidates=[];time_window=None
                for condition in view['filters']+constraints.get('filters',[]):
                    if condition['field'] in byid:
                        name=byid[condition['field']]['name']
                        predicate,args=bind_filter(quote(name),condition['op'],condition['value'],'?')
                        where.append(predicate)
                        params.extend(args)
                        remote_filters.append((name,condition['op'],condition.get('value')))
                if constraints.get('time_field') in byid:
                    time_field=byid[constraints['time_field']]
                    column=quote(time_field['name'])
                    where.extend(['julianday('+column+') >= julianday(?)','julianday('+column+') < julianday(?)'])
                    params.extend([constraints['time_start'],constraints['time_end_exclusive']])
                    time_window=(time_field,constraints['time_start'],constraints['time_end_exclusive'])
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
                    remote_candidates.append((byid[current_id]['name'],values))
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
                source_budget=min(10000,max_rows) if mode=='object' and len(sources)==1 and not view.get('row_limit') else 10000
                sql='SELECT '+','.join(quote(f['name']) for f in fields)+' FROM '+quote(table)+(' WHERE '+' AND '.join(where) if where else '')+' LIMIT '+str(source_budget+1)
                sql_types={'integer':' BIGINT','number':' DOUBLE','boolean':' BOOLEAN','string':' VARCHAR'}
                types=[field_type(f) for f in fields]
                if sid.startswith('doris-'):
                    remote_order=[]
                    remote_limit=source_budget+1
                    if mode=='object' and len(sources)==1 and requested_limit and not view.get('distinct'):
                        remote_order=[(byid[item['field']]['name'],item['direction'],item['nulls_first'])
                                      for item in view.get('order_by',[])]
                        remote_limit=requested_limit
                    rows=path.read(table,fields,remote_filters,remote_candidates,time_window,remote_limit,remote_order)
                elif sid=='emr':
                    db=sqlite3.connect(':memory:')
                    candidates={str(value) for value in emr_patient_candidates[0]} if emr_patient_candidates else set()
                    try:
                        load_emr(db,path,table,fields,deadline,candidates)
                    except Exception:
                        db.close()
                        raise
                else:
                    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
                    from management.catalog.derived_fields import attach_age_view
                    attach_age_view(db,table,fields,quote)
                if not sid.startswith('doris-'):
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
                trace.append({'source_id':sid,'table':table,'rows_read':len(rows),'pushed_projection':[f['name'] for f in fields],'pushed_filters':len(where),'local_alias':alias,
                              'connector':'doris' if sid.startswith('doris-') else 'local'})
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
            metric='COUNT(*)'
            if calculation and calculation['kind']=='person_count':
                field_id=calculation['field']
                value=locations[field_id]
                if field_type(fields_by_id[field_id])=='string':
                    value="NULLIF(TRIM("+value+"), '')"
                metric='COUNT(DISTINCT '+value+')'
            if mode=='aggregate':projection.append(metric+' AS count')
            sql='SELECT '+('DISTINCT ' if view.get('distinct') else '')+','.join(projection)+' FROM '+tables+(' WHERE '+' AND '.join(predicates) if predicates else '')
            if mode=='aggregate':sql+=(' GROUP BY '+','.join(str(i+1) for i in range(len(selected))) if selected else '')+' HAVING '+metric+' >= '+str(int(min_group))
            if view.get('order_by'):
                sql+=' ORDER BY '+','.join(locations[item['field']]+' '+item['direction'].upper()+
                    (' NULLS FIRST' if item['nulls_first'] else ' NULLS LAST') for item in view['order_by'])
            sql+=' LIMIT '+str(requested_limit or 201)
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
            result={'columns':columns,'rows':output_rows,'notice':'部分分组可能未满足输出条件' if mode=='aggregate' else '源库实时读取' if live else '本地对象级结果'}
            if not sensitive_output_allowed(result,{'output_policy':output_policy},authorized_health_fields,
                    authorized_identifier_fields=authorized_identifier_fields):
                raise DomainError('查询输出未通过字段分类或敏感内容检查，未缓存结果',403)
            if key is not None:
                old=self.store.get('materialization',key)
                self.store.save('materialization',key,{'result':result,'sources':trace,'expires_epoch':time.time()+60,'view_id':view['id']},old['version'] if old else 0,'execution','按版本与源指纹缓存，命中仍需重新授权')
            return result,{'cache_hit':False,'materialization_id':key,'sources':trace,'optimizer':'DuckDB本地代价优化','physical_plan':physical_plan,'chosen_plan':'字段/过滤下推后本地等值连接','alternatives':[{'name':'全表拉取','selected':False,'reason':'传输与内存成本较高'}]}
        finally:con.close()
