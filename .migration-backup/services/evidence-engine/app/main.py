"""Evidence engine deployable.

In v3.0 this service re-implemented the maturity arithmetic inline. Two copies
of a scoring rule is one copy too many when the score is what a certification
decision rests on, so the arithmetic now lives in
:mod:`agentic_os.assurance.evidence` and this service calls it. The behaviour
that matters is unchanged and now has one home: score is verified applicable
weight over total applicable weight, and a critical control that is not
VERIFIED blocks certification whatever the score.

The evidence surfaces of the platform API (``/api/v1/evidence``) are the
supported way to read recorded evidence. This service exists for the narrower
job of scoring a control set supplied by a caller — a pipeline gate, for
instance, that wants the score for a candidate release before recording it.
"""

from __future__ import annotations

from typing import Any

from agentic_os.assurance.evidence import ControlEvidence, calculate_maturity
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="Agentic OS Evidence Engine", version="3.1.0")


class ControlInput(BaseModel):
    control_id: str
    domain: str = "unspecified"
    title: str = ""
    weight: float = Field(ge=0)
    critical: bool = False
    applicable: bool = True
    status: str
    test_id: str = ""
    reason: str = ""


class ScoreRequest(BaseModel):
    controls: list[ControlInput]
    environment: str = "unspecified"
    commit_sha: str = ""


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "evidence-engine", "version": "3.1.0"}


@app.post("/v1/maturity/score")
def score(request: ScoreRequest) -> dict[str, Any]:
    controls = [
        ControlEvidence(
            control_id=control.control_id,
            domain=control.domain,
            title=control.title,
            weight=control.weight,
            critical=control.critical,
            applicable=control.applicable,
            status=control.status,
            test_id=control.test_id,
            reason=control.reason,
        )
        for control in request.controls
    ]
    report = calculate_maturity(controls, environment=request.environment, commit_sha=request.commit_sha)
    return {
        "score": report.score,
        "certified": report.certified,
        "critical_blockers": report.critical_blockers,
        "domain_scores": report.domain_scores,
        "generated_at": report.generated_at,
    }
