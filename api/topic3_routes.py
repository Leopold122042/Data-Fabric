import json

from fastapi import APIRouter, Request
from fastapi.responses import Response

from management.topic3_demo import Topic3Demo
from platform_core.access import require_action
from platform_core.storage import DomainError


ACTION_PERMISSIONS = {
    'draft': 'demand.understand',
    'confirm_spec': 'demand.update',
    'supply_service': 'service.create',
    'generate_plan': 'plan.create',
    'confirm_plan': 'plan.confirm',
    'check_access': 'grant.create',
    'approve_file': 'service.update',
    'revoke': 'grant.revoke',
    'repair_quality': 'service.update',
    'run': 'plan.run',
    'deliver': 'delivery.reference',
    'compare': 'improvement.create',
}


def create_router(runtime, identity):
    router = APIRouter(prefix='/api/report/topic3', tags=['课题三服务供给与可信交付'])
    demo = getattr(runtime, 'topic3_demo', None)
    if demo is None:
        demo = Topic3Demo(runtime)
        runtime.topic3_demo = demo

    def principal(request, action=None):
        user = identity.principal(request)
        if action:
            require_action(user, action)
        return user

    async def body(request):
        try:
            value = await request.json()
        except ValueError:
            raise DomainError('请求必须为有效JSON对象')
        if not isinstance(value, dict):
            raise DomainError('请求必须为JSON对象')
        return value

    @router.get('/ai/providers')
    def ai_providers(request: Request):
        principal(request, 'demand.understand')
        from management.intake.ollama import IntakeLLM
        return IntakeLLM.provider_options()

    @router.get('/cases')
    def cases(request: Request):
        user = principal(request, 'service.read')
        return {'items': demo.list_cases(user)}

    @router.post('/cases')
    async def create_case(request: Request):
        user = principal(request, 'demand.create')
        return demo.create_case(await body(request), user)

    @router.get('/cases/{case_id}')
    def get_case(case_id: str, request: Request):
        user = principal(request, 'service.read')
        return demo.state(case_id, user)

    @router.post('/cases/{case_id}/actions/{action}')
    async def action(case_id: str, action: str, request: Request):
        permission = ACTION_PERMISSIONS.get(action)
        if not permission:
            raise DomainError('未知的课题三操作', 404)
        user = principal(request, permission)
        return demo.action(case_id, action, await body(request), user)

    @router.get('/cases/{case_id}/result')
    def result(case_id: str, request: Request):
        user = principal(request, 'delivery.reference')
        return demo.result(case_id, user)

    @router.get('/cases/{case_id}/download')
    def download(case_id: str, request: Request):
        user = principal(request, 'delivery.download')
        export = demo.result(case_id, user)
        if export.get('delivery', {}).get('method') != 'approved_file':
            raise DomainError('当前交付不是单独批准的文件交付', 403)
        content = json.dumps(export, ensure_ascii=False, indent=2, default=str).encode('utf-8')
        return Response(content, media_type='application/json; charset=utf-8', headers={
            'Content-Disposition': f'attachment; filename="topic3-{case_id}.json"',
            'Cache-Control': 'no-store',
        })

    return router
