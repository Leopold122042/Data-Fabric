from fastapi import APIRouter

def create_router(runtime):
    router = APIRouter()
    router.add_api_route('/api/patients', runtime.patients, methods=['GET'])
    router.add_api_route('/api/query/patient_360', runtime.patient_360, methods=['POST'])
    router.add_api_route('/api/search', runtime.search, methods=['GET'])
    router.add_api_route('/api/services/{sid}/invoke', runtime.service_invoke, methods=['POST'])
    router.add_api_route('/api/data-services/{sid}/invoke', runtime.governed_service_invoke, methods=['POST'])
    router.add_api_route('/api/circulation/recent', runtime.circulation_recent, methods=['GET'])
    router.add_api_route('/api/data-services/flows/{flow_id}/run', runtime.data_service_flow_run, methods=['POST'])
    router.add_api_route('/api/security/overview', runtime.security_overview, methods=['GET'])
    router.add_api_route('/api/security/authorize', runtime.security_authorize, methods=['POST'])
    router.add_api_route('/api/security/mask-preview', runtime.security_mask_preview, methods=['POST'])
    router.add_api_route('/api/security/audit', runtime.security_audit, methods=['GET'])
    router.add_api_route('/api/security/risks', runtime.security_risks, methods=['GET'])
    router.add_api_route('/api/security/risks/{rid}/resolve', runtime.security_risk_resolve, methods=['POST'])
    router.add_api_route('/api/orchestrator', runtime.orchestrator_status, methods=['GET'])
    router.add_api_route('/api/rules/{rid}/toggle', runtime.rule_toggle, methods=['POST'])
    router.add_api_route('/api/tasks', runtime.tasks, methods=['GET'])
    router.add_api_route('/api/orchestrator/patrol', runtime.patrol_now, methods=['POST'])
    return router
