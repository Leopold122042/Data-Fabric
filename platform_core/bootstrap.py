import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from management.facade import Management
from platform_core.runtime import Runtime
from platform_core.storage import DomainError
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
    app=FastAPI(title='MedFabric',lifespan=lifespan)
    app.state.runtime=runtime
    app.state.identity=identity or LocalDemoIdentity()
    from execution.security.legacy_guard import install
    install(app)
    @app.exception_handler(DomainError)
    async def domain_error(request:Request,exc:DomainError):
        return JSONResponse({'error':str(exc)},status_code=exc.status)
    for router in (platform_routes.create_router(runtime),management_legacy.create_router(runtime),
                   execution_legacy.create_router(runtime),create_router(runtime,identity or LocalDemoIdentity())):
        app.include_router(router)
    app.include_router(external_router)
    from execution.external.routes import create_router as external_governance_router
    app.include_router(external_governance_router(runtime,app.state.identity))
    app.include_router(execution_router(runtime,app.state.identity))
    app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
    return app
