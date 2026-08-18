from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Literal

app = FastAPI(title="Agentic OS Evidence Engine", version="3.0.0")

CountedStatus = Literal["VERIFIED", "PRODUCTION_PROVEN"]

class Control(BaseModel):
    control_id: str
    weight: float
    critical: bool = False
    status: str

class ScoreRequest(BaseModel):
    controls: List[Control]

@app.get("/health")
def health():
    return {"status": "ok", "service": "evidence-engine", "version": "3.0.0"}

@app.post("/v1/maturity/score")
def score(request: ScoreRequest):
    total = sum(c.weight for c in request.controls)
    verified = sum(c.weight for c in request.controls if c.status in {"VERIFIED", "PRODUCTION_PROVEN"})
    critical_blockers = [c.control_id for c in request.controls if c.critical and c.status in {"FAILED", "EXPIRED", "NOT_EVIDENCED"}]
    numeric = round((verified / total * 100), 2) if total else 0.0
    return {
        "score": numeric,
        "certified": numeric == 100.0 and not critical_blockers,
        "critical_blockers": critical_blockers
    }
