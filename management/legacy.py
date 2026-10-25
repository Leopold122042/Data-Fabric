# Extracted compatibility implementation. See docs/development-ownership.md.
import asyncio
import json
import os
import sqlite3
import random
import threading
import time
from collections import deque
from pathlib import Path
from fastapi import Request
from fastapi.responses import FileResponse, StreamingResponse
import medsim
from discovery import BUS, DiscoveryEngine
from semantics import build_graph, catalog_payload
from virtualization import VirtualLayer
from orchestration import Orchestrator
from governance import GovernanceState
BASE_DIR = str(Path(__file__).resolve().parents[1])

class ManagementHandlers:

    async def discovery_run(self, request: Request):
        body = await request.json() if await request.body() else {}
        sid = (body or {}).get('source_id')
        evs = self.engine.scan_source(sid) if sid and sid in self.engine.adapters else self.engine.scan_all()
        self.management.catalog.scan_engine(self.engine)
        p = self.publish(evs)
        return {'events': p}

    def catalog(self):
        payload = dict(catalog_payload(self.engine))
        payload['logical_views'] = self.vlayer.logical_views() if self.vlayer else []
        return payload

    def graph(self, patients: int=100):
        g = build_graph(self.engine, limit_patients=min(int(patients), 100))
        n_nodes = len(g['nodes'])
        n_edges = len(g['edges'])
        type_counts = {}
        for nd in g['nodes']:
            type_counts[nd['type']] = type_counts.get(nd['type'], 0) + 1
        return {**g, 'stats': {'nodes': n_nodes, 'edges': n_edges, 'by_type': type_counts}}

    def services_list(self):
        with self.SERVICE_STATS_LOCK:
            stats = {k: {'calls': v['calls'], 'avg_ms': round(v['total_ms'] / v['calls'], 2) if v['calls'] else None} for k, v in self.SERVICE_STATS.items()}
        return {'services': self.SERVICES, 'stats': stats}

    def data_services(self):
        with self.SERVICE_STATS_LOCK:
            legacy = {k: {'calls': v['calls'], 'avg_ms': round(v['total_ms'] / v['calls'], 2) if v['calls'] else None} for k, v in self.SERVICE_STATS.items()}
        return self.gov.snapshot(legacy)

    async def data_service_register(self, request: Request):
        from execution.security.identity import LocalDemoIdentity
        from management.store import DomainError
        user = LocalDemoIdentity().principal(request)
        if user['role'] not in ('platform', 'provider'):
            raise DomainError('当前角色不能登记服务', 403)
        body = await request.json()
        sid = str(body.get('id') or '').strip().upper()
        existing = self.management.store.get('service', sid)
        if existing:
            raise DomainError('已有服务请在管理工作台按版本修改', 409)
        item = self.management.services.save({**body, 'id': sid or None, 'provider': user['id']}, user['id'])
        return {'service': item, 'message': '已保存草稿，请进入管理工作台完成验证与审核'}
