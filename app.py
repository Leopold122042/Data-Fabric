"""Composition entry point. A owns this file; A/B business logic lives in separate packages.
Local LAN demo: MEDFABRIC_DEMO_ALLOW_PRIVATE_NETWORK=1 python -m uvicorn app:app --host 0.0.0.0 --port 8321
"""
from platform_core.bootstrap import create_app

app = create_app()
