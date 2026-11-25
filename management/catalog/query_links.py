"""Catalogue links validated for query execution, and bounded join-path search."""
from collections import defaultdict

from management.catalog.field_library import FieldLibrary
from platform_core.storage import DomainError


MAX_JOIN_TABLES = 4
_STRUCTURAL_KEYS = ('kind','name','parent_id','source_id','table','structure','status')
_DEMO_KEYS = (
    (('his','visit','patient_id'), ('his','patient','patient_id')),
    (('his','orders','visit_id'), ('his','visit','visit_id')),
    (('lis','test_order','patient_id'), ('his','patient','patient_id')),
    (('lis','result','test_no'), ('lis','test_order','test_no')),
    (('pacs','study','patient_id'), ('his','patient','patient_id')),
    (('emr','dossier(病历文书)','patient_id'), ('his','patient','patient_id')),
    (('iot','vitals','patient_id'), ('his','patient','patient_id')),
)


def relationship_is_current(store, relation, fields):
    versions=relation.get('field_versions') or {}
    for field_id in (relation.get('source'),relation.get('target')):
        item=fields.get(field_id)
        if not item or item.get('status')!='active':
            return False
        version=versions.get(field_id)
        if version is not None and version!=item.get('version'):
            prior=store.get('object',field_id,version)
            if not prior or any(prior.get(key)!=item.get(key) for key in _STRUCTURAL_KEYS):
                return False
    return True


def executable_links(store, visible_tables=None):
    objects=store.list('object')
    tables={o['id']:o for o in objects if o['kind']=='table' and o.get('status')=='active'
            and (visible_tables is None or o['id'] in visible_tables)}
    confirmed=FieldLibrary(store).confirmed()
    fields={o['id']:o for o in objects if o['kind']=='field' and o.get('status')=='active'
            and o.get('parent_id') in tables and o['id'] in confirmed}
    by_key=defaultdict(set)
    for field in fields.values():
        table=tables[field['parent_id']]
        for name in {table['table'],table.get('structure',{}).get('physical_name')} - {None}:
            by_key[(table['source_id'],name.casefold(),field['name'].casefold())].add(field['id'])

    def resolve(source,table,field):
        ids=by_key.get((source,str(table).casefold(),str(field).casefold()),())
        return next(iter(ids)) if len(ids)==1 else None

    edges={}
    def add(left,right,basis):
        if left in fields and right in fields and fields[left]['parent_id']!=fields[right]['parent_id']:
            edges.setdefault(tuple(sorted((left,right))),basis)

    for relation in store.list('relation'):
        if (relation.get('type')=='same_patient_id' and relation.get('status')=='confirmed'
                and relationship_is_current(store,relation,fields)):
            add(relation['source'],relation['target'],'已确认患者标识')
    for table in tables.values():
        source=table['source_id']
        foreign_keys=table.get('structure',{}).get('foreign_keys',[])
        key_width=defaultdict(int)
        for fk in foreign_keys:
            if 'id' in fk:
                key_width[fk['id']]+=1
        for fk in foreign_keys:
            if 'id' in fk and key_width[fk['id']]!=1:
                continue
            add(resolve(source,table['table'],fk.get('from')),
                resolve(source,fk.get('table'),fk.get('to')),'来源声明的参照键')
    if store.environment=='demo':
        for left,right in _DEMO_KEYS:
            add(resolve(*left),resolve(*right),'模拟数据生成规则')
    return tables,fields,edges


def orient_path(start, required, pairs, fields):
    pending=list(pairs)
    reached={start}
    path=[]
    while pending:
        for i,(left,right) in enumerate(pending):
            lt,rt=fields[left]['parent_id'],fields[right]['parent_id']
            if (lt in reached)==(rt in reached):
                continue
            if lt in reached:
                path.append((left,right))
                reached.add(rt)
            else:
                path.append((right,left))
                reached.add(lt)
            pending.pop(i)
            break
        else:
            raise DomainError('关联路径必须逐步连接表且不得形成循环',409)
    if not required<=reached or len(reached)>MAX_JOIN_TABLES:
        raise DomainError('关联路径未覆盖请求字段或超过四表范围',409)
    return path


def shortest_paths(start, required, fields, edges):
    if required<={start}:
        return [()]
    states={(frozenset({start}),())}
    for _ in range(MAX_JOIN_TABLES-1):
        next_states=set()
        for tables,path in states:
            for pair in edges:
                lt,rt=(fields[field_id]['parent_id'] for field_id in pair)
                if (lt in tables)==(rt in tables):
                    continue
                next_states.add((tables|{lt,rt},tuple(sorted((*path,pair)))))
                if len(next_states)>6000:
                    raise DomainError('可用关联过多，请先指定数据来源或关联口径',409)
        matches=[path for tables,path in next_states if required<=tables]
        if matches:
            return sorted(matches,key=lambda path:(
                sum(edges[pair]!='已确认患者标识' for pair in path),
                tuple(sorted(path))))[:5]
        states=next_states
    return []


def describe_links(path, fields, edges):
    return [{'from_table': fields[left]['table'], 'from_field':fields[left]['name'],
             'to_table':fields[right]['table'], 'to_field':fields[right]['name'],
             'basis':edges[tuple(sorted((left,right)))]} for left,right in path]
