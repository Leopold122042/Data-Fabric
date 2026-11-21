"""Login, password update, and level-4 account administration endpoints."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from platform_core.access import account_permission
from platform_core.storage import DomainError

COOKIE = 'mf_session'


def _same_origin(request):
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise DomainError('不接受其他站点发起的账户请求', 403)


def _principal(request):
    user = request.app.state.identity.principal(request)
    return {**user, **account_permission(user)}


def _admin(request):
    user = _principal(request)
    if int(user.get('level', 0)) != 4:
        raise DomainError('只有4级管理员可以管理账户', 403)
    return user


def _set_cookie(response, request, token):
    response.set_cookie(COOKIE, token, httponly=True, secure=request.url.scheme == 'https',
                        samesite='lax', path='/', max_age=12 * 60 * 60)
    response.headers['Cache-Control'] = 'no-store'


def create_router():
    router = APIRouter(tags=['账户与权限'])

    @router.post('/api/auth/register', status_code=201)
    async def register(request: Request):
        _same_origin(request)
        try:
            body = await request.json()
        except ValueError:
            raise DomainError('请填写注册信息')
        allowed = {'username', 'display_name', 'password', 'confirm_password'}
        if not isinstance(body, dict) or set(body) - allowed:
            raise DomainError('注册信息无效；新账户固定从1级开始')
        if body.get('password') != body.get('confirm_password'):
            raise DomainError('两次输入的密码不一致')
        username = request.app.state.accounts.validate_username(body.get('username'))
        password = body.get('password')
        remote_key = request.client.host if request.client else 'local'
        request.app.state.accounts.register_account(username, password, body.get('display_name'), remote_key)
        token, user = request.app.state.accounts.authenticate(username, password, remote_key)
        response = JSONResponse({'principal': user, 'permission': account_permission(user),
                                 'registered': True}, status_code=201)
        _set_cookie(response, request, token)
        return response

    @router.post('/api/auth/login')
    async def login(request: Request):
        _same_origin(request)
        try:
            body = await request.json()
        except ValueError:
            raise DomainError('请填写账号和密码')
        if not isinstance(body, dict):
            raise DomainError('请填写账号和密码')
        ip = request.client.host if request.client else 'local'
        token, user = request.app.state.accounts.authenticate(body.get('username'), body.get('password'), ip)
        response = JSONResponse({'principal': user, 'permission': account_permission(user)})
        _set_cookie(response, request, token)
        return response

    @router.get('/api/auth/session')
    def session(request: Request):
        user = _principal(request)
        response = JSONResponse({'principal': user, 'permission': account_permission(user)})
        response.headers['Cache-Control'] = 'no-store'
        return response

    @router.post('/api/auth/logout')
    def logout(request: Request):
        _same_origin(request)
        token = request.cookies.get(COOKIE)
        user = request.app.state.accounts.principal(token)
        request.app.state.accounts.revoke_session(token,
            actor=(user or {}).get('username'), actor_level=(user or {}).get('level'))
        response = JSONResponse({'ok': True})
        response.delete_cookie(COOKIE, path='/', httponly=True, samesite='lax')
        response.headers['Cache-Control'] = 'no-store'
        return response

    @router.post('/api/auth/password')
    async def change_password(request: Request):
        _same_origin(request)
        user = _principal(request)
        try:
            body = await request.json()
        except ValueError:
            raise DomainError('密码修改内容无效')
        password = body.get('new_password') if isinstance(body, dict) else None
        confirmation = body.get('confirm_password') if isinstance(body, dict) else None
        if password != confirmation:
            raise DomainError('两次输入的新密码不一致')
        if not user.get('must_change_password'):
            current = body.get('current_password')
            if not request.app.state.accounts.verify_password(user['username'], current):
                raise DomainError('当前密码不正确', 401)
        request.app.state.accounts.change_password(user['username'], password,
            actor=user['username'], actor_level=user['level'])
        token, updated = request.app.state.accounts.authenticate(user['username'], password,
            request.client.host if request.client else 'local')
        response = JSONResponse({'principal': updated, 'permission': account_permission(updated)})
        _set_cookie(response, request, token)
        return response

    @router.get('/api/accounts')
    def accounts(request: Request):
        _admin(request)
        response = JSONResponse({'accounts': request.app.state.accounts.list_accounts()})
        response.headers['Cache-Control'] = 'no-store'
        return response

    @router.get('/api/accounts/audit')
    def account_audit(request: Request, limit: int = 200):
        _admin(request)
        response = JSONResponse({'events': request.app.state.accounts.audit_events(limit)})
        response.headers['Cache-Control'] = 'no-store'
        return response

    @router.post('/api/accounts')
    async def create_account(request: Request):
        _same_origin(request)
        user = _admin(request)
        try:
            body = await request.json()
        except ValueError:
            raise DomainError('账户信息无效')
        if not isinstance(body, dict):
            raise DomainError('账户信息无效')
        if 'level' in body:
            raise DomainError('新账户固定从1级开始，由管理员另行调整')
        created = request.app.state.accounts.create_account(body.get('username'), body.get('password'),
            body.get('display_name'), actor=user['username'], actor_level=user['level'])
        return {'account': created}

    @router.patch('/api/accounts/{username}')
    async def update_account(username: str, request: Request):
        _same_origin(request)
        user = _admin(request)
        try:
            body = await request.json()
        except ValueError:
            raise DomainError('账户权限更新内容无效')
        if not isinstance(body, dict) or set(body) - {'level', 'enabled'}:
            raise DomainError('只允许调整权限等级或账户状态')
        return {'account': request.app.state.accounts.update_account(username,
                body.get('level'), body.get('enabled'), actor=user['username'], actor_level=user['level'])}

    @router.post('/api/accounts/{username}/reset-password')
    async def reset_password(username: str, request: Request):
        _same_origin(request)
        user = _admin(request)
        try:
            body = await request.json()
        except ValueError:
            raise DomainError('请填写临时密码')
        if not isinstance(body, dict):
            raise DomainError('请填写临时密码')
        return request.app.state.accounts.reset_password(username, body.get('password'),
            actor=user['username'], actor_level=user['level'])

    return router
