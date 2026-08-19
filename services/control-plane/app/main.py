"""Control plane deployable.

In v3.0 this was a scaffold: it accepted a plan request and returned a canned
``execution_allowed: false`` without consulting anything. That stub is gone.
The control plane is now the real governed API — identity, authorization, risk,
policy, approval, execution gateway, verification, audit — and this module is
the thin entry point that serves it, kept so existing deployment descriptors
that point at ``services/control-plane`` keep working.

    uvicorn app.main:app

is equivalent to

    uvicorn agentic_os.api.app:app
"""

from __future__ import annotations

from agentic_os.api.app import app

__all__ = ["app"]
