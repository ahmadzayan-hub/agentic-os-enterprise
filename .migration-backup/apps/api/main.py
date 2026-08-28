"""ASGI entrypoint for the Agentic OS API.

uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
"""

from agentic_os.api.app import create_app

app = create_app()
