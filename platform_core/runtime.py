"""Composition context; implementations never import app.py."""
import threading
from collections import deque
from platform_core.legacy_platform import PlatformHandlers
from management.legacy import ManagementHandlers
from execution.legacy import ExecutionHandlers
from management.services.seed import SERVICES
from execution.seed import CIRC_ACTORS, CIRC_SVC_PARAMS, SERVICE_ASSET
from governance import GovernanceState

class Runtime(PlatformHandlers, ManagementHandlers, ExecutionHandlers):
    def __init__(self, management, simulate=False):
        self.management = management
        self.simulate = simulate
        self.stop_event = threading.Event()
        self.threads = []
        self.SCAN_INTERVAL = 4.0
        self.engine = self.vlayer = self.sim = self.orch = None
        self.SERVICES = SERVICES
        self.CIRC_ACTORS, self.CIRC_SVC_PARAMS, self.SERVICE_ASSET = CIRC_ACTORS, CIRC_SVC_PARAMS, SERVICE_ASSET
        self.gov = GovernanceState(services=management.legacy_services)
        self.HIST, self.HIST_LOCK = deque(maxlen=400), threading.Lock()
        self.HISTORY_ORIGIN = self.SERVER_STARTED_AT = self.SERVER_STARTED_MONOTONIC = None
        self.SERVICE_STATS_LOCK = threading.Lock()
        self.SERVICE_STATS = {s['id']: {'calls': 0, 'total_ms': 0.0} for s in SERVICES}

    def shutdown(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=5)
        if self.orch is not None:
            self.orch.stop()
