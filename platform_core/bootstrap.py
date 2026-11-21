import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from management.facade import Management
from platform_core.runtime import Runtime
from platform_core.storage import DomainError
from platform_core.accounts import AccountStore
from execution.evidence import Evidence
from execution.security.identity import LocalDemoIdentity
from execution.validation import validator
from external_api import router as external_router
from execution.control_plane import Execution
from api.execution.routes import create_router as execution_router
from api import platform_routes
from api.management import legacy_routes as management_legacy
from api.execution import legacy_routes as execution_legacy
from api.management.routes import create_router

ROOT=Path(__file__).resolve().parents[1]


def create_app(control_root=None,start_runtime=True,simulate=None,identity=None):
    root=Path(control_root or os.environ.get('MEDFABRIC_CONTROL_DIR',str(ROOT/'runtime')))
    environment=os.environ.get('MEDFABRIC_ENV','demo')
    if environment!='demo' and identity is None:
        raise RuntimeError('Real environments require an injected trusted identity provider; demo identities are not authentication.')
    evidence=Evidence(root/'execution'/'control.db',environment)
    management=Management(root/'management'/'control.db',evidence,environment)
    from management.catalog.sources import SourceRegistry
    import medsim
    management.catalog.sources = SourceRegistry(management.store, medsim.DATA_DIR)
    runtime=Runtime(management,simulate=(os.environ.get('MEDFABRIC_SIMULATE')=='1') if simulate is None else simulate)
    runtime.execution=Execution(runtime)
    from execution.external.governance import ExternalGovernance
    runtime.external_governance=ExternalGovernance(runtime)
    management.services.plan_reader=runtime.execution
    management.reconciliation.controller=runtime.execution.control
    management.services.validator=validator(runtime)
    @asynccontextmanager
    async def lifespan(app):
        try:
            if start_runtime:
                runtime.startup(start_background=False)
                from platform_core.field_confirmation import confirm_current_demo_fields
                field_migration=confirm_current_demo_fields(runtime,root/'security'/'field-confirmation-v1.json')
                if not field_migration['already_applied']:
                    print('[permissions] confirmed current demo field definitions:', field_migration['changed'], flush=True)
                if environment=='demo' and os.environ.get('MEDFABRIC_DEMO_SCENARIOS','1')!='0':
                    from platform_core.healthcare_scenarios import seed_healthcare
                    from platform_core.scenario_replacement import replace_legacy_scenarios
                    from platform_core.work_order_scenarios import seed_work_orders
                    runtime.demo_scenarios=seed_healthcare(runtime)
                    runtime.scenario_replacement=replace_legacy_scenarios(runtime)
                    runtime.work_order_scenarios=seed_work_orders(runtime)
                runtime.start_background()
                runtime.execution.start()
                runtime.external_governance.start()
            yield
        finally:
            runtime.external_governance.close()
            runtime.execution.close()
            runtime.shutdown()
    app=FastAPI(title='数据编织平台',lifespan=lifespan)
    app.state.runtime=runtime
    app.state.accounts=AccountStore(root/'security'/'accounts.sqlite3')
    runtime.account_store=app.state.accounts
    app.state.identity=identity or LocalDemoIdentity()

    @app.middleware('http')
    async def account_gate(request:Request,call_next):
        path=request.url.path
        test_client=bool(request.client and request.client.host=='testclient')
        login_page='/static/portal/login.html'
        public_api=path in ('/api/health','/api/auth/login','/api/auth/register','/api/auth/logout','/api/auth/password','/api/auth/session')
        if not test_client and not public_api:
            user=app.state.accounts.principal(request.cookies.get('mf_session'))
            if path.startswith('/api/') and not user:
                return JSONResponse({'error':'请先登录平台'},status_code=401,headers={'Cache-Control':'no-store'})
            if (path=='/' or path.endswith('.html')) and not user and path!=login_page:
                from urllib.parse import quote
                target=path+('?' + request.url.query if request.url.query else '')
                return RedirectResponse(login_page+'?return='+quote(target,safe=''),status_code=303)
            if user and user.get('must_change_password') and (path.startswith('/api/') and path not in ('/api/auth/password','/api/auth/logout','/api/auth/session')):
                return JSONResponse({'error':'首次登录后请先修改临时密码'},status_code=403,headers={'Cache-Control':'no-store'})
            if user and user.get('must_change_password') and path.endswith('.html') and path not in (login_page,):
                return RedirectResponse(login_page+'?change=1',status_code=303)
            if user and int(user.get('level',1))<4:
                legacy_private=path.startswith(('/api/external/','/api/patients','/api/search','/api/query/',
                    '/api/services/','/api/data-services','/api/circulation/','/api/tasks','/api/security/'))
                legacy_private = legacy_private or path in {
                    '/api/overview', '/api/events', '/api/stream', '/api/cockpit',
                    '/api/medical-overview', '/api/orchestrator',
                } or path.startswith(('/api/discovery/', '/api/rules/'))
                if legacy_private:
                    return JSONResponse({'error':'当前账户等级不能访问该旧版接口；请使用权限范围内的工作台查询。'},
                                        status_code=403,headers={'Cache-Control':'no-store'})
        return await call_next(request)
    from execution.security.legacy_guard import install
    install(app)
    @app.exception_handler(DomainError)
    async def domain_error(request:Request,exc:DomainError):
        return JSONResponse({'error':str(exc)},status_code=exc.status)
    from api.auth import create_router as auth_router
    for router in (auth_router(),platform_routes.create_router(runtime),management_legacy.create_router(runtime,app.state.identity),
                   execution_legacy.create_router(runtime),create_router(runtime,identity or LocalDemoIdentity())):
        app.include_router(router)
    app.include_router(external_router)
    from execution.external.routes import create_router as external_governance_router
    app.include_router(external_governance_router(runtime,app.state.identity))
    app.include_router(execution_router(runtime,app.state.identity))
    from api.portal import create_router as portal_router
    app.include_router(portal_router(runtime,app.state.identity))
    from api.topic3_routes import create_router as topic3_router
    app.include_router(topic3_router(runtime,app.state.identity))
    app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
    return app
