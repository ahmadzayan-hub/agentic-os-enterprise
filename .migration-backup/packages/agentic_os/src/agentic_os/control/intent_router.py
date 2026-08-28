"""Intent Router and Goal Interpreter.

Turns a free-text objective into a structured intent: what kind of work it is,
which domain agent owns it, what data classification it implies and how complex
it looks. The routing is deterministic and inspectable — it is a dispatch
decision with governance consequences, so it does not depend on a model.

The interpreter's output is a *proposal*. Ownership is confirmed against the
agent registry and the caller's permissions before anything is dispatched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agentic_os.core.registry import load_registries

#: Domain signals per agent. Ordered specificity: the most distinctive terms
#: first, so a generic word cannot outvote a domain-defining one.
_DOMAIN_SIGNALS: dict[str, tuple[tuple[str, float], ...]] = {
    "finance": (
        ("invoice", 3.0),
        ("payment", 3.0),
        ("refund", 3.0),
        ("budget", 2.0),
        ("reconcil", 3.0),
        ("cost", 1.5),
        ("expenditure", 2.0),
        ("variance", 2.0),
        ("supplier payment", 3.0),
        ("financial", 2.5),
    ),
    "operations": (
        ("work order", 3.0),
        ("maintenance", 3.0),
        ("asset", 2.5),
        ("failure", 2.5),
        ("breakdown", 2.5),
        ("preventive", 3.0),
        ("backlog", 2.5),
        ("escalator", 2.0),
        ("rolling stock", 3.0),
        ("track", 2.0),
        ("depot", 2.0),
        ("availability", 2.0),
        ("downtime", 2.5),
        ("reliability", 2.0),
    ),
    "engineering": (
        ("design", 2.5),
        ("standard", 2.0),
        ("specification", 2.5),
        ("compliance", 2.0),
        ("change impact", 3.0),
        ("obsolescence", 3.0),
        ("technical review", 3.0),
        ("dependency", 2.5),
        ("interface", 2.0),
    ),
    "knowledge": (
        ("document", 2.5),
        ("procedure", 2.5),
        ("manual", 2.5),
        ("policy document", 2.5),
        ("what does", 2.0),
        ("according to", 2.5),
        ("find in", 2.0),
        ("cite", 2.5),
    ),
    "analytics": (
        ("kpi", 3.0),
        ("trend", 2.5),
        ("forecast", 3.0),
        ("metric", 2.5),
        ("dashboard", 2.0),
        ("data quality", 3.0),
        ("statistic", 2.5),
        ("correlation", 2.5),
    ),
    "customer": (
        ("complaint", 3.0),
        ("customer", 2.5),
        ("passenger", 2.5),
        ("case", 2.0),
        ("satisfaction", 2.5),
        ("service quality", 2.5),
    ),
    "communications": (
        ("briefing", 3.0),
        ("announce", 2.5),
        ("notify", 2.5),
        ("stakeholder update", 3.0),
        ("press", 2.5),
        ("bulletin", 2.5),
    ),
    "sales": (
        ("pipeline", 3.0),
        ("contract renewal", 3.0),
        ("proposal", 2.0),
        ("tender", 2.5),
        ("framework agreement", 3.0),
        ("opportunity", 2.0),
    ),
    "marketing": (
        ("campaign", 3.0),
        ("audience", 2.5),
        ("engagement rate", 2.5),
        ("brand", 2.5),
    ),
}

_TASK_KINDS: dict[str, tuple[str, ...]] = {
    "question": ("what", "which", "who", "when", "where", "why", "how many", "how much", "is there"),
    "analysis": ("analyse", "analyze", "assess", "evaluate", "investigate", "review", "diagnose"),
    "report": ("report", "summarise", "summarize", "brief", "overview", "digest"),
    "draft": ("draft", "write", "compose", "prepare a letter", "prepare an email"),
    "action": ("create", "update", "close", "approve", "execute", "send", "issue", "schedule"),
    "forecast": ("forecast", "project", "predict", "estimate future"),
}

_SENSITIVE_TERMS = {
    "RESTRICTED": ("salary", "payroll", "medical", "disciplinary", "personal data", "national id"),
    "CONFIDENTIAL": (
        "contract value",
        "tender price",
        "commercial",
        "supplier pricing",
        "incident",
        "security",
        "safety case",
        "litigation",
    ),
}

_CONSEQUENTIAL_TERMS = (
    "execute",
    "pay",
    "refund",
    "transfer",
    "delete",
    "remove",
    "decommission",
    "publish",
    "send to",
    "close the work order",
    "sign",
    "commit",
    "authorise",
    "authorize",
)


@dataclass(slots=True)
class Intent:
    objective: str
    task_kind: str
    owner_agent: str
    candidate_agents: list[str] = field(default_factory=list)
    classification: str = "INTERNAL"
    complexity: float = 0.5
    consequential: bool = False
    entities: dict[str, list[str]] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_kind": self.task_kind,
            "owner_agent": self.owner_agent,
            "candidate_agents": self.candidate_agents,
            "classification": self.classification,
            "complexity": self.complexity,
            "consequential": self.consequential,
            "entities": self.entities,
            "scores": {k: round(v, 2) for k, v in self.scores.items()},
            "rationale": self.rationale,
        }


_ID_PATTERNS = {
    "work_order": re.compile(r"\bWO[- ]?\d{3,}\b", re.IGNORECASE),
    "asset": re.compile(r"\b(?:AST|ASSET)[- ]?\d{3,}\b", re.IGNORECASE),
    "document": re.compile(r"\bDOC[- ]?\d{3,}\b", re.IGNORECASE),
    "invoice": re.compile(r"\bINV[- ]?\d{3,}\b", re.IGNORECASE),
    "date": re.compile(r"\b(?:20\d{2}-\d{2}-\d{2}|Q[1-4]\s*20\d{2})\b"),
    "money": re.compile(r"\b(?:AED|USD|\$)\s?[\d,]+(?:\.\d{2})?\b", re.IGNORECASE),
}


def extract_entities(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name, pattern in _ID_PATTERNS.items():
        matches = sorted({m.group(0) for m in pattern.finditer(text)})
        if matches:
            found[name] = matches
    return found


def classify_sensitivity(text: str) -> str:
    lowered = text.lower()
    for level in ("RESTRICTED", "CONFIDENTIAL"):
        if any(term in lowered for term in _SENSITIVE_TERMS[level]):
            return level
    return "INTERNAL"


def estimate_complexity(text: str, entity_count: int) -> float:
    """Heuristic 0..1 complexity used to steer model routing."""
    words = len(text.split())
    clauses = len(re.findall(r"\b(and|then|also|as well as|after|before|compare)\b", text.lower()))
    questions = text.count("?")
    score = 0.2 + min(0.3, words / 200) + min(0.3, clauses * 0.08) + min(0.1, questions * 0.05)
    score += min(0.1, entity_count * 0.02)
    return round(min(1.0, score), 3)


def interpret(objective: str, *, available_agents: set[str] | None = None) -> Intent:
    """Interpret an objective into a routable intent."""
    text_value = (objective or "").strip()
    lowered = text_value.lower()
    registries = load_registries()
    available = available_agents or set(registries.agents)

    scores: dict[str, float] = {}
    for agent, signals in _DOMAIN_SIGNALS.items():
        if agent not in available:
            continue
        score = sum(weight for term, weight in signals if term in lowered)
        if score:
            scores[agent] = score

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    if ranked:
        owner = ranked[0][0]
        rationale = f"domain terms matched '{owner}' with score {ranked[0][1]:.1f}" + (
            f"; runners-up {[a for a, _ in ranked[1:3]]}" if len(ranked) > 1 else ""
        )
    elif "knowledge" in available:
        owner = "knowledge"
        rationale = "no domain-specific terms matched; routed to governed retrieval"
    else:
        owner = sorted(available)[0]
        rationale = "no domain-specific terms matched; routed to the first available agent"

    task_kind = "analysis"
    for kind, triggers in _TASK_KINDS.items():
        if any(lowered.startswith(t) or f" {t}" in lowered for t in triggers):
            task_kind = kind
            break

    entities = extract_entities(text_value)
    entity_count = sum(len(v) for v in entities.values())

    return Intent(
        objective=text_value,
        task_kind=task_kind,
        owner_agent=owner,
        candidate_agents=[a for a, _ in ranked[:3]],
        classification=classify_sensitivity(text_value),
        complexity=estimate_complexity(text_value, entity_count),
        consequential=any(term in lowered for term in _CONSEQUENTIAL_TERMS),
        entities=entities,
        scores=scores,
        rationale=rationale,
    )
