"""Prevent request-body identity spoofing on compatibility endpoints."""
from fastapi.responses import JSONResponse
from platform_core.storage import DomainError
from platform_core.access import require_resource,require_action


def install(app):
    @app.middleware('http')
    async def guard(request,call_next):
        path=request.url.path
        if path.startswith('/api/') and not path.startswith(('/api/external/','/api/management/','/api/execution/')):
            try:
                user=app.state.identity.principal(request);request.state.principal=user
                # Auditing metadata is distinct from patient/query access and
                # from operations that mutate rules or risk dispositions.
                read_resource = ({'/api/security/overview':'risk','/api/security/audit':'risk',
                                  '/api/security/risks':'risk','/api/orchestrator':'rule',
                                  '/api/events':'event','/api/stream':'event'}.get(path) if request.method=='GET' else None)
                if read_resource:
                    require_resource(user,read_resource)
                    return await call_next(request)
                if path=='/api/orchestrator/patrol':require_action(user,'rule.patrol')
                elif path.startswith('/api/rules/') and path.endswith('/toggle'):require_action(user,'rule.toggle')
                elif path.startswith('/api/security/risks/') and path.endswith('/resolve'):require_action(user,'risk.resolve')
                restricted=path.startswith(('/api/patients','/api/search','/api/query/','/api/graph','/api/tasks','/api/security/')) or '/invoke' in path or path.endswith('/run') or '/toggle' in path or path.endswith('/patrol')
                if restricted and app.state.runtime.management.store.environment!='demo':raise DomainError('真实执行授权尚未接入，旧演示入口关闭',503)
                if restricted and user['role']!='platform':raise DomainError('旧演示入口仅供本机平台方；请通过B方案授权执行',403)
            except DomainError as exc:return JSONResponse({'error':str(exc)},status_code=exc.status)
        return await call_next(request)
