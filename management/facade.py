from management.store import Store
from management.catalog.service import Catalog
from management.semantic.service import Semantic
from management.services.service import Services
from management.services.repository import LegacyServices
from management.services.seed import SERVICES
from management.intake.service import Intake
from management.reconciliation.service import Reconciliation
from management.feedback.service import Feedback


class Management:
    def __init__(self,path,evidence,environment='demo'):
        self.store=Store(path,environment)
        self.catalog=Catalog(self.store);self.semantic=Semantic(self.store)
        self.services=Services(self.store);self.services.evidence=evidence;self.intake=Intake(self.store)
        self.reconciliation=Reconciliation(self.store,evidence,self.catalog)
        self.feedback=Feedback(self.store,evidence)
        self.legacy_services=LegacyServices(self.store)
        self.evidence=evidence
        for s in SERVICES:
            if not self.store.get('service',s['id']):
                self.store.save('service',s['id'],{**s,'description':s['desc'],'lifecycle':'published',
                    'legacy_seed':True,'validation_status':'legacy_unverified','class':'L3','scope':'院内演示',
                    'quality_score':None,'reuse_score':None,'tags':['既有演示服务'],'provider':'demo-provider',
                    'visibility':'internal','handler_id':s['id']},0,reason='导入既有演示服务，不代表新发布验证通过')

    def register_design(self,definition,actor='execution'):
        """A-owned ingestion port for B's immutable design snapshots; never overwrite a version."""
        from management.store import DomainError
        from hashlib import sha256
        import json
        for key in ('object_id','object_kind','object_version','environment','definition'):
            if key not in definition:raise DomainError('设计快照缺少：'+key)
        if definition['environment']!=self.store.environment:raise DomainError('设计环境不匹配')
        if definition['object_kind'] not in ('view','workflow','constraint','plan'):raise DomainError('不允许外部覆盖A的设计对象')
        oid=sha256(json.dumps([definition[k] for k in ('object_kind','object_id','object_version','environment')]).encode()).hexdigest()
        existing=self.store.get('external_design',oid)
        if existing:
            if existing['definition']!=definition['definition']:raise DomainError('已发布版本不可覆盖',409)
            return existing
        with self.store.atomic():
            result=self.store.save('external_design',oid,definition,0,actor,'接收外部设计快照')
            for source in definition.get('dependencies',[]):
                self.catalog.link({'source':source,'target':definition['object_id'],'type':'depends_on'},actor)
            return result
