"""Opt-in metadata bridge. Never copies cloud credentials, paths or data rows."""
import json
import re
from management.store import DomainError


def sync(catalog):
    with catalog.store.atomic():
        return _sync(catalog)


def _sync(catalog):
    import external_discovery as discovery
    changes=[]
    for registration in catalog.store.list('external_registration'):
        owner=registration['owner_hash']
        if not re.fullmatch(r'[a-f0-9]{64}',owner):
            continue
        path=discovery.DIRECTORY/(owner+'.json')
        with discovery.LOCK:
            records=json.loads(path.read_text()) if path.exists() else {}
        tables={}
        for key in registration['table_ids']:
            row=records.get(key)
            if row is None:continue
            columns=row.get('columns',[])
            types=row.get('types',[])
            tables[key]={'columns':columns,'column_details':[{'name':c,'type':types[i] if i<len(types) else 'unknown'} for i,c in enumerate(columns)]}
        changes.extend(catalog.ingest(registration['id'],{'tables':tables,'domain':'外部数据'}))
        for obj in catalog.objects(source_id=registration['id']):
            record=records.get(obj['table'])
            if record and obj['kind']=='table' and not obj.get('meaning'):
                catalog.annotate(obj['id'],{'meaning':record.get('name',obj['name']),'domain':'外部数据','expected_version':obj['version']},'external-metadata-sync')
    return changes


def publish(catalog, username, table_ids, actor):
    import external_discovery as discovery
    if not isinstance(table_ids,list) or not table_ids or any(not isinstance(x,str) for x in table_ids):
        raise DomainError('请至少选择一张已保存的来源表')
    with discovery.LOCK:
        records=discovery.read_registry(username)
    if any(key not in records for key in table_ids):raise DomainError('包含不属于当前云盘账户的来源表',403)
    owner=discovery.owner(username);sid='cloud-'+owner[:24]
    with catalog.store.atomic():
        old=catalog.store.get('external_registration',sid)
        ids=list(dict.fromkeys((old or {}).get('table_ids',[])+table_ids))
        catalog.store.save('external_registration',sid,{'owner_hash':owner,'table_ids':ids},old['version'] if old else 0,actor,'确认将选中表结构纳入平台目录')
        changes=sync(catalog)
    return {'source_id':sid,'registered_tables':len(ids),'changes':changes,
            'message':'表结构已纳入平台目录，可用于概念映射、血缘和服务来源。未复制数据或授予查询权限。'}
