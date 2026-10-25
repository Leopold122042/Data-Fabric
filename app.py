"""Composition entry point. A owns this file; A/B business logic lives in separate packages.
Run: python -m uvicorn app:app --host 127.0.0.1 --port 8321
"""
from platform_core.bootstrap import create_app

app = create_app()
