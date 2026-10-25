"""Versioned metadata and impact graph; no raw patient records returned."""
from collections import deque
from management.store import DomainError, new_id


class Catalog:
    def __init__(self, store):
        self.store = store
        self.sources = None

    def objects(self, **filters):
        rows = self.store.list('object', filters.get('at'))
        for key in ('source_id','kind','domain','owner','status'):
            if filters.get(key):
                rows = [r for r in rows if r.get(key)==filters[key]]
        q = str(filters.get('q','')).lower()
        return [r for r in rows if not q or q in (r.get('name','')+' '+r.get('meaning','')).lower()]

    def ingest(self, source_id, snapshot):
        with self.store.atomic():
            existing = [r for r in self.store.list('object') if r.get('source_id')==source_id]
            index = {(r['kind'],r.get('table'),r['name']): r for r in existing}
            seen = set(); changes = []
            for table, info in snapshot.get('tables',{}).items():
                items = [('table',table, {'columns': info.get('columns',[]), 'foreign_keys':info.get('foreign_keys',[])})]
                details = info.get('column_details') or [{'name':c,'type':'unknown'} for c in info.get('columns',[])]
                items += [('field',c['name'],c) for c in details]
                parent = None
                for kind,name,structure in items:
                    key=(kind,table,name); old=index.get(key)
                    oid=old['id'] if old else new_id('OBJ')
                    seen.add(oid)
                    data={**(old or {}),'source_id':source_id,'kind':kind,'table':table,'name':name,
                          'structure':structure,'status':'active','parent_id':parent if kind=='field' else None,
                          'meaning':(old or {}).get('meaning',''),'domain':(old or {}).get('domain',snapshot.get('domain','医疗演示')),
                          'owner':(old or {}).get('owner','待指定'),'constraints':(old or {}).get('constraints',{}),
                          'classification':(old or {}).get('classification','待确认')}
                    # Volatile row counts are scan facts, not immutable structural versions.
                    changed=not old or any(data.get(k)!=old.get(k) for k in ['structure','status','parent_id'])
                    if changed:
                        obj=self.store.save('object',oid,data,old['version'] if old else 0,
                                            reason='结构发现' if not old else '结构变更')
                        changes.append({'id':oid,'type':'created' if not old else 'changed','version':obj['version']})
                    if kind=='table': parent=oid
            for old in existing:
                if old['id'] not in seen and old.get('status')!='deleted':
                    self.store.save('object',old['id'],{**old,'status':'deleted'},old['version'],reason='源对象删除')
                    changes.append({'id':old['id'],'type':'deleted'})
            for change in changes:
                affected=set(self.impact(change['id'])['downstream'])
                for mapping in self.store.list('mapping'):
                    if any(f in affected|{change['id']} for f in mapping.get('fields',[])) and mapping.get('status')=='confirmed':
                        self.store.save('mapping',mapping['id'],{**mapping,'status':'review_required'},mapping['version'],reason='源结构变化，需复核')
            return changes

    def scan_engine(self, engine):
        changes=[]
        adapters = dict(engine.adapters)
        if self.sources is not None:
            adapters.update(self.sources.adapters())
        for sid,adapter in adapters.items():
            try:
                snapshot=adapter.snapshot()
                changes.extend(self.ingest(sid,snapshot))
                old=self.store.get('scan',sid)
                data={'status':'success','tables':snapshot.get('tables',{})}
                if old is None or old.get('status')!='success' or old.get('tables')!=data['tables']:
                    self.store.save('scan',sid,data,old['version'] if old else 0,reason='元数据扫描状态或内容变化')
            except Exception as exc:
                old=self.store.get('scan',sid)
                if old is None or old.get('status')!='failed' or old.get('error')!=str(exc):
                    self.store.save('scan',sid,{'status':'failed','error':str(exc)},old['version'] if old else 0,reason='扫描失败')
        from management.catalog.external import sync
        changes.extend(sync(self))
        return changes

    def annotate(self, oid, body, actor):
        old=self.store.require('object',oid)
        allowed={'meaning','domain','owner','update_period','classification','constraints','semantic_baseline'}
        return self.store.save('object',oid,{**old,**{k:v for k,v in body.items() if k in allowed}},body.get('expected_version'),actor,'元数据说明更新')

    def confirm_rename(self, oid, name, expected, actor):
        if not name or not name.strip():raise DomainError('新名称不能为空')
        old=self.store.require('object',oid)
        if any(r['id']!=oid and r.get('status')=='active' and r['source_id']==old['source_id'] and
               r['kind']==old['kind'] and r.get('table')==old.get('table') and r['name']==name for r in self.objects()):
            raise DomainError('目标名称已存在，不能覆盖',409)
        if old['kind']!='field':raise DomainError('当前本地改名确认仅支持字段')
        return self.store.save('object',oid,{**old,'name':name,'structure':{**old['structure'],'name':name},
                                            'renamed_from':old['name']},expected,actor,'人工确认源字段改名，保留ID')

    def edges(self):
        edges=[]
        for obj in self.store.list('object'):
            if obj.get('parent_id'):edges.append((obj['parent_id'],obj['id'],'contains'))
        objects=self.store.list('object')
        field_index={(o['source_id'],o.get('table'),o['name']):o['id'] for o in objects if o['kind']=='field'}
        for table in objects:
            if table['kind']=='table':
                for fk in table.get('structure',{}).get('foreign_keys',[]):
                    source=field_index.get((table['source_id'],fk['table'],fk['to']))
                    target=field_index.get((table['source_id'],table['name'],fk['from']))
                    if source and target:edges.append((source,target,'foreign_key'))
        for m in self.store.list('mapping'):
            edges.extend((f,m['id'],'mapping') for f in m.get('fields',[]))
            edges.append((m['id'],m['concept_id'],'concept'))
        for relation in self.store.list('relation'):
            edges.append((relation['source'],relation['target'],relation.get('type','depends_on')))
        for s in self.store.list('service'):
            edges.extend((r,s['id'],'service') for r in s.get('source_objects',[]))
        return edges

    def impact(self, oid):
        edges=self.edges()
        def walk(reverse=False):
            found=set(); queue=deque([oid])
            while queue:
                current=queue.popleft()
                for src,dst,_ in edges:
                    a,b=(dst,src) if reverse else (src,dst)
                    if a==current and b!=oid and b not in found:found.add(b);queue.append(b)
            return sorted(found)
        return {'object_id':oid,'upstream':walk(True),'downstream':walk(),
                'edges':[{'source':a,'target':b,'type':t} for a,b,t in edges]}

    def identifier(self, body, actor):
        local,global_id=body.get('local_id'),body.get('global_id')
        if not local or not global_id:raise DomainError('本地和全局标识均必填')
        for x in self.store.list('identifier'):
            if (x['local_id']==local or x['global_id']==global_id) and x['id']!=body.get('id'):
                raise DomainError('标识已映射，不能覆盖历史',409)
        return self.store.save('identifier',body.get('id') or new_id('ID'),{'local_id':local,'global_id':global_id,
                               'schema_version':'local-demo-v1','standard_verified':False},body.get('expected_version',0),actor,'标识映射')

    def profile(self,oid,actor):
        from pathlib import Path
        from management.catalog.sources import SQLiteSource
        import medsim
        obj=self.store.require('object',oid)
        if obj['kind']!='field':raise DomainError('请选择字段')
        adapters=self.sources.adapters() if self.sources else {}
        source=adapters.get(obj['source_id'])
        if source is None and obj['source_id'] in ('his','lis','pacs','iot'):
            source=SQLiteSource(Path(medsim.DATA_DIR)/(obj['source_id']+'.db'))
        if source is None or not hasattr(source,'profile'):raise DomainError('该连接器暂不支持字段剖析')
        profile=source.profile(obj['table'],obj['name'])
        return self.store.save('object',oid,{**obj,'profile':profile},obj['version'],actor,'字段统计剖析（不输出原始值）')

    def link(self,body,actor):
        if not body.get('source') or not body.get('target') or body['source']==body['target']:raise DomainError('需指定不同的上游和下游ID')
        return self.store.save('relation',body.get('id') or new_id('REL'),body,body.get('expected_version',0),actor,'登记跨对象依赖')
