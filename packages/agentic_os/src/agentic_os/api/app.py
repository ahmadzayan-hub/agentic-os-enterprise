"""FastAPI application factory.

Security headers, CORS, rate limiting and a uniform error envelope are applied
here so that no individual router can forget them.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agentic_os.api.ratelimit import build_rate_limiter
from agentic_os.core.config import get_settings
from agentic_os.core.db import healthcheck
from agentic_os.core.errors import AgenticError
from agentic_os.core.ids import correlation_id as new_correlation_id

API_PREFIX = "/api/v1"


class NumericJSONResponse(JSONResponse):
    """Serialise PostgreSQL ``numeric`` as a JSON number, not a string.

    FastAPI's default encoder renders ``Decimal`` as a string, which forces every
    consumer to remember to coerce a cost or a score before doing arithmetic on
    it — and produces a runtime error the first time someone forgets. Money and
    scores are numbers in the API contract; the database remains the authority
    for exact decimal arithmetic.
    """

    def render(self, content: Any) -> bytes:
        import json

        def default(value: Any) -> Any:
            if isinstance(value, Decimal):
                return float(value)
            raise TypeError(f"object of type {type(value).__name__} is not JSON serialisable")

        return json.dumps(
            content, ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=default
        ).encode("utf-8")


#: The API returns JSON only, so its policy is maximally restrictive. The web
#: application ships its own policy suited to rendering.
_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": _CSP,
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}


def create_app(*, include_docs: bool = True) -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Governed enterprise agentic operating system. Every action is "
            "identity-aware, policy-controlled, risk-assessed, audited and "
            "evidence-backed."
        ),
        docs_url=f"{API_PREFIX}/docs" if include_docs else None,
        openapi_url=f"{API_PREFIX}/openapi.json" if include_docs else None,
        redoc_url=None,
        default_response_class=NumericJSONResponse,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["authorization", "content-type", "x-correlation-id"],
        max_age=600,
    )

    # Shared across replicas when Redis is configured, so the effective limit
    # is the deployment's rather than one pod's.
    limiter = build_rate_limiter(settings.rate_limit_per_minute, settings.redis_url)

    @app.middleware("http")
    async def security_and_limits(request: Request, call_next: Any) -> Any:
        correlation = request.headers.get("x-correlation-id") or new_correlation_id()

        identity = request.headers.get("authorization", "")
        key = identity[-32:] if identity else (request.client.host if request.client else "anon")
        allowed, remaining = limiter.allow(key)
        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "RATE_LIMITED",
                    "message": "too many requests",
                    "retryable": True,
                    "correlation_id": correlation,
                },
                headers={**_SECURITY_HEADERS, "Retry-After": "60"},
            )

        started = time.perf_counter()
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload"
            )
        response.headers["x-correlation-id"] = correlation
        response.headers["x-ratelimit-remaining"] = str(remaining)
        response.headers["server-timing"] = f"app;dur={(time.perf_counter() - started) * 1000:.1f}"
        return response

    @app.exception_handler(AgenticError)
    async def agentic_error_handler(request: Request, exc: AgenticError) -> Any:
        return JSONResponse(
            status_code=exc.http_status, content=exc.to_dict(), headers=dict(_SECURITY_HEADERS)
        )

    from agentic_os.api.v1 import auth, catalog, governance, knowledge, operations, runs

    for router in (
        auth.router,
        runs.router,
        governance.router,
        catalog.router,
        knowledge.router,
        operations.router,
    ):
        # The response class must be passed here as well as on the app: a router
        # created with a bare APIRouter() carries its own JSONResponse default,
        # which would otherwise win and re-introduce Decimal-as-string.
        app.include_router(router, prefix=API_PREFIX, default_response_class=NumericJSONResponse)

    @app.get("/health", tags=["platform"])
    def health() -> dict[str, Any]:
        database = healthcheck()
        return {
            "status": "ok" if database["status"] == "ok" else "degraded",
            "service": "agentic-os-api",
            "version": settings.app_version,
            "environment": settings.app_env,
            "database": database,
        }

    @app.get("/ready", tags=["platform"])
    def ready() -> JSONResponse:
        database = healthcheck()
        ok = database["status"] == "ok" and database.get("pgvector", False)
        return JSONResponse(status_code=200 if ok else 503, content={"ready": ok, "database": database})

    @app.get(f"{API_PREFIX}/capabilities", tags=["platform"])
    def capabilities() -> dict[str, Any]:
        """What this deployment can actually do.

        Reports configuration honestly so an operator never has to guess whether
        an external provider is live or whether the local deterministic engine
        is serving requests.
        """
        from agentic_os.core.registry import load_registries

        registries = load_registries()
        implemented = [k for k, t in registries.tools.items() if t["implementation_status"] == "IMPLEMENTED"]
        declared_only = [
            k for k, t in registries.tools.items() if t["implementation_status"] != "IMPLEMENTED"
        ]
        return {
            "agents": sorted(registries.agents),
            "skills": {
                "deterministic": sorted(
                    k for k, s in registries.skills.items() if s["execution_mode"] == "DETERMINISTIC"
                ),
                "model_backed": sorted(
                    k for k, s in registries.skills.items() if s["execution_mode"] != "DETERMINISTIC"
                ),
            },
            "tools": {
                "implemented": sorted(implemented),
                "declared_not_implemented": sorted(declared_only),
            },
            "models": {
                key: {
                    "provider": model["provider"],
                    "deployment": model["deployment"],
                    "approval_state": model["approval_state"],
                }
                for key, model in registries.models.items()
            },
            "external_model_providers_enabled": settings.model_allow_external_providers,
            "embedding_provider": settings.embedding_provider,
            "policy_mode": settings.policy_mode,
            "kms_backend": settings.kms_backend,
            "secret_backend": settings.secret_backend,
        }

    return app


app = create_app()
