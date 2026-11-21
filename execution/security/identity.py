"""Cookie-session identity boundary for the LAN demo platform."""
from fastapi import Request
from platform_core.access import account_permission
from platform_core.storage import DomainError

DEMO_USERS = {
    'platform': {'id': 'demo-platform', 'role': 'platform', 'level': 4, 'display_name': '平台管理员'},
    'provider': {'id': 'demo-provider', 'role': 'requester', 'level': 2, 'display_name': '供给方演示账户'},
    'requester': {'id': 'demo-requester', 'role': 'requester', 'level': 1, 'display_name': '需求方演示账户'},
    'auditor': {'id': 'demo-auditor', 'role': 'requester', 'level': 3, 'display_name': '数据管理演示账户'},
}


class LocalDemoIdentity:
    def principal(self, request: Request):
        # Preserve the in-process test adapter; real network requests cannot
        # choose an identity with a header or by sharing the campus network.
        client_host = request.client.host if request.client else None
        if client_host == 'testclient':
            name = request.headers.get('x-demo-role', 'requester')
            if name not in DEMO_USERS:
                raise DomainError('未知测试身份', 403)
            return {**DEMO_USERS[name], 'username': DEMO_USERS[name]['id'], 'demo': True}
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            raise DomainError('不接受其他站点发起的管理请求', 403)
        accounts = getattr(request.app.state, 'accounts', None)
        principal = accounts.principal(request.cookies.get('mf_session')) if accounts else None
        if not principal:
            raise DomainError('请先登录平台', 401)
        return {**principal, **account_permission(principal), 'demo': True}
