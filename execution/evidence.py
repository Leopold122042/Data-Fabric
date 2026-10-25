"""B-owned evidence port. Isolated from A's writable control database."""
from platform_core.storage import Store, DomainError, new_id

class Evidence:
    def __init__(self,path,environment='demo'):
        self.store=Store(path,environment)
    def list(self):return self.store.list('run')
    def get(self,oid):return self.store.require('run',oid)
    def record(self,data):
        for key in ('object_kind','object_id','object_version','environment','status','task_scope'):
            if key not in data:raise DomainError('运行证据缺少：'+key)
        if data['environment']!=self.store.environment:raise DomainError('运行环境不匹配')
        oid=data.get('id') or new_id('RUN')
        if self.store.get('run',oid):return self.store.require('run',oid)
        return self.store.save('run',oid,data,0,'execution','执行证据')
