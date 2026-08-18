from enum import Enum
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="Agentic OS Control Plane", version="3.0.0")

class AutonomyLevel(str, Enum):
    A0 = "A0"
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    A4 = "A4"

class PlanRequest(BaseModel):
    tenant_id: str
    user_id: str
    objective: str = Field(min_length=3)
    requested_autonomy: AutonomyLevel = AutonomyLevel.A1

@app.get("/health")
def health():
    return {"status": "ok", "service": "control-plane", "version": "3.0.0"}

@app.post("/v1/plans")
def create_plan(request: PlanRequest):
    # Scaffold only: production implementation must call policy, risk,
    # workflow and evidence services before any side effect is permitted.
    return {
        "status": "PLANNED",
        "tenant_id": request.tenant_id,
        "user_id": request.user_id,
        "objective": request.objective,
        "requested_autonomy": request.requested_autonomy,
        "execution_allowed": False,
        "next_gate": "POLICY_EVALUATION"
    }
