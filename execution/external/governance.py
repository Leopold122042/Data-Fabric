"""Session-bound cloud monitoring and explicit resource grants. No secrets on disk."""
import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from platform_core.storage import DomainError, new_id, now
from execution.security.control import timestamp


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def schema(record):
    return digest([record.get('columns',[]),record.get('types',[]),record.get('header_row'),record.get('mapping_mode')])


class ExternalGovernance:
    def __init__(self,runtime):
        self.runtime=runtime
        self.store=runtime.management.evidence.store
        self.lock=threading.RLock()
        self.stop=threading.Event()
        self.thread=None
        self.watches={}  # Session references and credentials never enter Store.

    def start(self):
        self.thread=threading.Thread(target=self.loop,daemon=True,name='cloud-metadata')
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=2)
        self.watches.clear()

    def owner(self,current):
        if not current.cloud_credentials:raise DomainError('请先连接云盘',401)
        import external_discovery as d
        return d.owner(current.cloud_credentials[0])

    def records(self,current):
        import external_discovery as d
        self.owner(current)
        with d.LOCK:return d.read_registry(current.cloud_credentials[0])

    def selected_tables(self,records,ids):
        if not isinstance(ids,list) or any(not isinstance(i,str) or not i for i in ids):raise DomainError('来源表编号格式无效，请刷新列表后重新选择')
        ids=list(dict.fromkeys(ids))
        if not ids:raise DomainError('尚未选择来源表，请先在来源表列表中选择至少一张表')
        if len(ids)>100:raise DomainError(f'已选择{len(ids)}张来源表，单次最多100张，请分批检测或授权')
        missing=[i for i in ids if i not in records]
        if missing:raise DomainError(f'所选表中有{len(missing)}张不在当前云盘目录中，可能已切换账户或更新目录；请刷新列表后重新选择',409)
        return ids

    def grant(self,current,body,actor):
        if self.store.environment!='demo':raise DomainError('真实外部授权适配器尚未接入',503)
        records=self.records(current)
        ids=self.selected_tables(records,body.get('table_ids'))
        if body.get('subject') not in ('demo-platform','demo-provider','demo-requester','demo-auditor'):raise DomainError('未知演示主体')
        if not isinstance(body.get('purpose'),str) or not body['purpose'].strip():raise DomainError('必须填写用途')
        expiry=timestamp(body.get('expires_at'))
        if expiry<=datetime.now(timezone.utc):raise DomainError('授权期限必须在未来')
        return self.store.save('external_grant',new_id('EXTGRANT'),{'owner':self.owner(current),'subject':body['subject'],'purpose':body['purpose'].strip(),'expires_at':body['expires_at'],'table_ids':list(dict.fromkeys(ids)),'schemas':{i:schema(records[i]) for i in ids},'status':'active','scope':'local-demo','permission':'read'},0,actor,'外部资源读取授权；固定来源表与结构版本')

    def revoke(self,gid,actor):
        with self.lock:
            g=self.store.require('external_grant',gid)
            return self.store.save('external_grant',gid,{**g,'status':'revoked','revoked_at':now()},g['version'],actor,'撤销外部读取授权')

    def check(self,current,gid,ids,actor,purpose,stage):
        with self.lock:
            grant=self.store.get('external_grant',gid or '')
            records=self.records(current)
            reason=None
            if not grant:reason='请先选择外部数据访问授权'
            elif grant['owner']!=self.owner(current):reason='授权不属于当前云盘'
            elif grant['subject']!=actor:reason='授权主体不匹配'
            elif grant['purpose']!=purpose:reason='查询用途与授权不匹配'
            elif grant['status']!='active':reason='授权已撤销'
            elif timestamp(grant['expires_at'])<=datetime.now(timezone.utc):reason='授权已到期'
            elif not ids or not set(ids)<=set(grant['table_ids']):reason='查询来源超出授权范围'
            elif any(i not in records or schema(records[i])!=grant['schemas'].get(i) for i in ids):reason='来源结构已变化，请重新审核授权'
            self.store.save('decision',new_id('EXTDEC'),{'actor':actor,'grant_id':gid,'table_ids':ids,'purpose':purpose,'stage':stage,'allowed':reason is None,'reason':reason or '外部资源授权通过','scope':'external-local-demo'},0,actor,'外部数据访问核验')
            if reason:raise DomainError(reason,403)
            return grant

    def watch(self,current,ids,interval,actor):
        records=self.records(current)
        ids=self.selected_tables(records,ids)
        if not isinstance(interval,int) or isinstance(interval,bool) or not 60<=interval<=86400:raise DomainError('检测间隔应为60—86400秒')
        owner=self.owner(current)
        with self.lock:self.watches[id(current)]={'session':current,'owner':owner,'ids':list(dict.fromkeys(ids)),'interval':interval,'next':0,'actor':actor,'status':'waiting','checked_at':None,'error':None}
        return self.watch_status(current)

    def watch_status(self,current):
        with self.lock:
            w=self.watches.get(id(current))
            if not w:return {'enabled':False}
            return {k:v for k,v in w.items() if k not in ('session','owner','actor','next')}|{'enabled':True}

    def unwatch(self,current):
        with self.lock:self.watches.pop(id(current),None)
        return {'enabled':False}

    def scan(self,current,ids,actor):
        import external_discovery as d
        from external_sources import fetch,box_url
        changes=[]
        with current.virtual_lock:
            records=self.records(current)
            ids=self.selected_tables(records,ids)
            username,token=current.cloud_credentials
            owner=d.owner(username);cache={}
            for tid in ids:
                if self.stop.is_set():raise DomainError('检测服务正在停止',503)
                old_record=records[tid]
                key='EXTMETA-'+digest([owner,tid])[:24]
                before=self.store.get('external_metadata',key)
                try:
                    path=old_record['path']
                    if path not in cache:
                        if sum(len(v) for v in cache.values())>40*1024*1024:cache.clear()
                        cache[path]=fetch(box_url(path),username=username,token=token)
                    updated,table=d.current_table(old_record,username,token,cache)
                    fields={'name':updated['name'],'columns':updated['columns'],'types':updated['types'],'rows':len(table[4]),'schema_hash':schema(updated),'content_hash':digest(table[4]),'state':'available'}
                    from management.semantic.matching import profile_columns
                    fields['profiles']=profile_columns(table[2],table[4])
                    error=None
                except Exception:
                    fields={k:before[k] for k in ('name','columns','types','rows','schema_hash','content_hash') if before and k in before}
                    fields.update(name=old_record.get('name',tid),state='unavailable')
                    error='无法读取来源，请检查连接、权限、文件路径或格式；未将不可用推断为删除。'
                if fields['state']=='available':
                    with self.lock:
                        for grant in self.store.list('external_grant'):
                            if grant['owner']==owner and grant['status']=='active' and tid in grant['table_ids'] and grant['schemas'].get(tid)!=fields['schema_hash']:
                                self.store.save('external_grant',grant['id'],{**grant,'status':'invalidated','invalidated_at':now()},grant['version'],actor,'外部结构变化，需重新授权')
                changed=before is None or any(before.get(k)!=v for k,v in fields.items())
                latest=self.store.save('external_metadata',key,{**fields,'table_id':tid,'owner':owner,'checked_at':now(),'error':error},before['version'] if before else 0,actor,'外部元数据检测')
                if changed:
                    kind='baseline' if before is None else 'unavailable' if fields['state']=='unavailable' else 'restored' if before.get('state')=='unavailable' else 'schema' if before.get('schema_hash')!=fields['schema_hash'] else 'content'
                    event=self.store.save('external_change',new_id('EXTCHANGE'),{'owner':owner,'table_id':tid,'name':fields['name'],'change_type':kind,'before_version':before['version'] if before else None,'after_version':latest['version'],'before_rows':before.get('rows') if before else None,'after_rows':fields.get('rows'),'before_columns':before.get('columns',[]) if before else [],'after_columns':fields.get('columns',[]),'error':error},0,actor,'外部数据变更')
                    changes.append(event)
            # Existing structural scan performs mapping invalidation and downstream notices.
            from management.catalog.external import sync
            sync(self.runtime.management.catalog)
            catalog=self.runtime.management.catalog
            for meta in self.store.list('external_metadata'):
                if meta.get('owner')!=owner or meta.get('state')!='available':continue
                for obj in catalog.objects(kind='field',source_id='cloud-'+owner[:24]):
                    if obj['table']!=meta['table_id']:continue
                    profile=meta.get('profiles',{}).get(obj['name'])
                    if profile is not None and obj.get('profile')!=profile:
                        catalog.store.save('object',obj['id'],{**obj,'profile':profile},obj['version'],actor,'外部字段取值剖析更新；不保存原始值')
                        for mapping in catalog.store.list('mapping'):
                            if mapping.get('status')=='confirmed' and obj['id'] in mapping.get('fields',[]):
                                catalog.store.save('mapping',mapping['id'],{**mapping,'status':'review_required'},mapping['version'],actor,'字段取值证据变化，需复核映射')

        if changes:
            self.runtime.publish([{'source_id':'external','etype':'external_metadata_changed','severity':'warning' if c['change_type'] in ('schema','unavailable') else 'info','title':c['name']+' · '+c['change_type'],'detail':'外部元数据变化；详情见数据接入治理','table_id':c['table_id']} for c in changes])
        return {'checked':len(ids),'changes':changes}

    def loop(self):
        while not self.stop.wait(1):
            with self.lock:watches=list(self.watches.items())
            for key,w in watches:
                if self.stop.is_set():return
                current=w['session']
                # Never keep credentials alive merely because monitoring is enabled.
                if not current.cloud_credentials or time.monotonic()-current.touched>1800:
                    with self.lock:self.watches.pop(key,None)
                    continue
                if not current.cloud_credentials or __import__('external_discovery').owner(current.cloud_credentials[0])!=w['owner']:
                    with self.lock:self.watches.pop(key,None)
                    continue
                if time.monotonic()<w['next']:continue
                w['next']=time.monotonic()+w['interval'];w['status']='checking'
                try:self.scan(current,w['ids'],w['actor']);w.update(status='waiting',checked_at=now(),error=None)
                except Exception:w.update(status='error',error='检测失败，请检查云盘连接和所选来源')
