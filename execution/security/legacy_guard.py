"""Prevent request-body identity spoofing on compatibility endpoints."""
from fastapi.responses import JSONResponse
from platform_core.storage import DomainError


def install(app):
    @app.middleware('http')
    async def guard(request,call_next):
        path=request.url.path
        if path.startswith('/api/') and not path.startswith(('/api/external/','/api/management/','/api/execution/')):
            try:
                user=app.state.identity.principal(request);request.state.principal=user
                restricted=path.startswith(('/api/patients','/api/search','/api/query/','/api/graph','/api/tasks','/api/security/')) or '/invoke' in path or path.endswith('/run') or '/toggle' in path or path.endswith('/patrol')
                if restricted and app.state.runtime.management.store.environment!='demo':raise DomainError('真实执行授权尚未接入，旧演示入口关闭',503)
                if restricted and user['role']!='platform':raise DomainError('旧演示入口仅供本机平台方；请通过B方案授权执行',403)
            except DomainError as exc:return JSONResponse({'error':str(exc)},status_code=exc.status)
        return await call_next(request)
