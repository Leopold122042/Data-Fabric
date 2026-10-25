"""Bounded local federated queries. Identifier whitelist from A, values bound separately."""
import hashlib,json,sqlite3,time
from pathlib import Path
import medsim
from platform_core.storage import DomainError,new_id
from execution.query.expressions import compile_expression,mapping_expression


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
        data={'created_by':(old or {}).get('created_by',actor),'name':body.get('name') or oid,'fields':selected,'field_versions':{f['id']:f['version'] for f in fields},'mapping_versions':mapping_versions,
              'projections':projections,'output_fields':list(dict.fromkeys(body.get('fields',[])+list(projections))),'joins':joins,'filters':body.get('filters',[]),'group_by':body.get('group_by',[]),'output_mode':body.get('output_mode','aggregate')}
        if data['output_mode'] not in ('aggregate','object'):raise DomainError('输出粒度非法')
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
            else:
                registration=self.management.store.get('source',sid)
                if not registration or registration.get('connector')!='sqlite':raise DomainError('此逻辑查询当前支持本地SQLite连接器')
                path=(Path(medsim.DATA_DIR)/registration['path']).resolve()
            if not path.is_file():raise DomainError('源不可达',503)
            root=Path(medsim.DATA_DIR).resolve()
            if not path.resolve().is_relative_to(root):raise DomainError('源不在允许的数据目录',403)
            sources.append((sid,table,path.resolve(),columns))
        return sources
    def execute(self,view,mode,min_group=5):
        import duckdb
        if mode!=view['output_mode']:raise DomainError('视图输出粒度与授权不一致',403)
        sources=self.sources(view)
        if mode=='object' and any(f['name'].lower() in ('patient_id','record_id','name','phone','身份证','姓名') for _,_,_,fields in sources for f in fields):raise DomainError('对象级结果包含受限标识字段；请改为汇总输出',403)
        stamp=[(sid,table,str(path.stat().st_mtime_ns),[(f['id'],f['version']) for f in fields],Path(str(path)+'-wal').stat().st_mtime_ns if Path(str(path)+'-wal').exists() else 0) for sid,table,path,fields in sources]
        key=hashlib.sha256(json.dumps([view['id'],view['version'],stamp,mode,min_group]).encode()).hexdigest()
        cached=self.store.get('materialization',key)
        if cached and cached['expires_epoch']>time.time():return cached['result'],{'cache_hit':True,'materialization_id':key,'sources':cached['sources']}
        con=duckdb.connect(':memory:');deadline=time.monotonic()+5;locations={};trace=[]
        try:
            con.execute("SET enable_external_access=false");con.execute("SET memory_limit='128MB'");con.execute('SET threads=1')
            for index,(sid,table,path,fields) in enumerate(sources):
                params=[];where=[];byid={f['id']:f for f in fields}
                for condition in view['filters']:
                    if condition['field'] in byid:
                        where.append(quote(byid[condition['field']]['name'])+' '+condition['op']+' ?');params.append(condition.get('value'))
                sql='SELECT '+','.join(quote(f['name']) for f in fields)+' FROM '+quote(table)+(' WHERE '+' AND '.join(where) if where else '')+' LIMIT 10001'
                db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
                try:
                    db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
                    rows=db.execute(sql,params).fetchall()
                finally:db.close()
                if len(rows)>10000:raise DomainError('源返回超过本地查询预算，请增加过滤条件')
                alias='s'+str(index)
                def sql_type(f):
                    dtype=f['structure'].get('type','').lower()
                    if 'int' in dtype:return ' BIGINT'
                    if any(t in dtype for t in ('real','double','numeric','float','decimal')):return ' DOUBLE'
                    return ' VARCHAR'
                con.execute('CREATE TABLE '+alias+'('+','.join('c'+str(i)+sql_type(f) for i,f in enumerate(fields))+')')
                if rows:con.executemany('INSERT INTO '+alias+' VALUES ('+','.join('?' for _ in fields)+')',rows)
                for i,f in enumerate(fields):locations[f['id']]=alias+'.c'+str(i)
                trace.append({'source_id':sid,'table':table,'rows_read':len(rows),'pushed_projection':[f['name'] for f in fields],'pushed_filters':len(where),'local_alias':alias})
            predicates=[locations[a]+'='+locations[b] for a,b in view['joins']]
            tables=','.join('s'+str(i) for i in range(len(sources)))
            selected=view['group_by'] if mode=='aggregate' else view.get('output_fields',view['fields'])
            projections=view.get('projections',{})
            names=[projections[oid]['name'] if oid in projections else self.management.store.require('object',oid)['name'] for oid in selected]
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
            if len(rows)>200:raise DomainError('结果超过交付预算，请缩小查询范围')
            result={'columns':columns,'rows':[list(r) for r in rows],'notice':'部分分组可能未满足输出条件' if mode=='aggregate' else '本地对象级结果'}
            old=self.store.get('materialization',key)
            self.store.save('materialization',key,{'result':result,'sources':trace,'expires_epoch':time.time()+60,'view_id':view['id']},old['version'] if old else 0,'execution','按版本与源指纹缓存，命中仍需重新授权')
            return result,{'cache_hit':False,'materialization_id':key,'sources':trace,'optimizer':'DuckDB本地代价优化','physical_plan':physical_plan,'chosen_plan':'字段/过滤下推后本地等值连接','alternatives':[{'name':'全表拉取','selected':False,'reason':'传输与内存成本较高'}]}
        finally:con.close()
