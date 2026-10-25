"""Replaceable B identity boundary. Local demo identities are never production authentication."""
from fastapi import Request
from platform_core.storage import DomainError

DEMO_USERS={'platform':{'id':'demo-platform','role':'platform'},'provider':{'id':'demo-provider','role':'provider'},
            'requester':{'id':'demo-requester','role':'requester'},'auditor':{'id':'demo-auditor','role':'auditor'}}

class LocalDemoIdentity:
    def principal(self,request:Request):
        if request.client and request.client.host not in ('127.0.0.1','::1','testclient'):
            raise DomainError('本地演示入口仅允许本机访问；真实身份接口尚未接入',403)
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):
            raise DomainError('不接受其他站点发起的管理请求',403)
        name=request.headers.get('x-demo-role','platform')
        if name not in DEMO_USERS:raise DomainError('未知演示身份',403)
        return dict(DEMO_USERS[name],demo=True)
