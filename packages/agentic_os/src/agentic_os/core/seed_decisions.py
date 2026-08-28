"""Demo decision cases, so a fresh checkout shows a working loop.

Everything here is ordinary seed data with one rule enforced throughout: no
figure is written that the platform could not compute. Confidence comes from
:func:`calculate_confidence` over the evidence and options actually inserted,
so a seeded case whose evidence is thin honestly reports "Not Calculated".
Writing a plausible-looking 0.86 into the seed would put an invented number in
front of every reviewer of this repository, which is precisely the failure the
brief names.

The cases are spread deliberately across the lifecycle — one still in analysis,
one awaiting review, one awaiting approval, one verified and closed — because a
queue where everything sits in one state demonstrates nothing about the loop.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.context import ExecutionContext, HumanIdentity
from agentic_os.core.ids import utcnow
from agentic_os.decisions.confidence import calculate_confidence
from agentic_os.decisions.lifecycle import create_decision, transition

DOMAINS: tuple[dict[str, str], ...] = (
    {
        "slug": "signalling",
        "name": "Signalling and Train Control",
        "description": "Interlockings, point machines, ATP and the control centre.",
    },
    {
        "slug": "rolling-stock",
        "name": "Rolling Stock",
        "description": "Train sets, bogies, doors, HVAC and depot maintenance.",
    },
    {
        "slug": "track-civils",
        "name": "Track and Civils",
        "description": "Permanent way, structures, drainage and station fabric.",
    },
)

#: Which seeded user belongs to which domain. Membership is what grants access,
#: so this table is also what makes the cross-domain behaviour visible in the
#: running console rather than only in the tests.
MEMBERSHIPS: tuple[tuple[str, str, str], ...] = (
    ("signalling", "systems.lead@rta.example", "LEAD"),
    ("signalling", "field.engineer@rta.example", "MEMBER"),
    ("signalling", "chief.engineer@rta.example", "MANAGER"),
    ("rolling-stock", "rollingstock.lead@rta.example", "LEAD"),
    ("rolling-stock", "chief.engineer@rta.example", "MANAGER"),
    ("track-civils", "chief.engineer@rta.example", "MANAGER"),
)

KPIS: tuple[dict[str, Any], ...] = (
    {
        "key": "decision.effectiveness_rate",
        "name": "Decision Effectiveness Rate",
        "description": (
            "Share of AI-supported decisions that were implemented and later "
            "verified as achieving their intended outcome."
        ),
        "formula": "count(state=VERIFIED and verdict=ACHIEVED) / count(reached verification)",
        "unit": "%",
        "direction": "UP_IS_GOOD",
        "target": 80.0,
        "warning": 60.0,
        "domain": None,
    },
    {
        "key": "decision.lead_time_days",
        "name": "Decision lead time",
        "description": "Mean days from a decision being detected to being closed.",
        "formula": "avg(closed_at - created_at) over decisions closed in the period",
        "unit": "days",
        "direction": "DOWN_IS_GOOD",
        "target": 21.0,
        "warning": 35.0,
        "domain": None,
    },
    {
        "key": "incident.mttr_hours",
        "name": "Incident mean time to resolve",
        "description": "Mean hours from an incident being detected to being resolved.",
        "formula": "avg(resolved_at - detected_at) over incidents resolved in the period",
        "unit": "hours",
        "direction": "DOWN_IS_GOOD",
        "target": 4.0,
        "warning": 12.0,
        "domain": None,
    },
    {
        "key": "run.success_rate",
        "name": "Agent run success rate",
        "description": "Share of finished agent runs that succeeded. Runs still in flight are excluded.",
        "formula": "count(status=SUCCEEDED) / count(status in (SUCCEEDED, FAILED))",
        "unit": "%",
        "direction": "UP_IS_GOOD",
        "target": 99.5,
        "warning": 97.0,
        "domain": None,
    },
    # The next two are defined by the business and the platform cannot measure
    # them: the incident register carries no asset class, and nothing feeds it
    # service hours. They are left here deliberately. Deleting them would make
    # the KPI surface look complete; leaving them visible and unmeasured is the
    # honest report of where the platform actually is.
    {
        "key": "signalling.point_machine_failures",
        "name": "Point machine failures per month",
        "description": "Recorded in-service failures of point machines across the network.",
        "formula": "count(incidents where asset_class='point_machine') per calendar month",
        "unit": "failures",
        "direction": "DOWN_IS_GOOD",
        "target": 4.0,
        "warning": 8.0,
        "domain": "signalling",
    },
    {
        "key": "rolling_stock.door_availability",
        "name": "Door system availability",
        "description": "Share of scheduled service hours with all door systems serviceable.",
        "formula": "serviceable door-hours / scheduled door-hours",
        "unit": "%",
        "direction": "UP_IS_GOOD",
        "target": 99.5,
        "warning": 98.5,
        "domain": "rolling-stock",
    },
)


def seed_decisions(session: Session, tenant_id: str) -> dict[str, Any]:
    """Create the demo domains, memberships, KPIs and decision cases."""
    domains = _seed_domains(session, tenant_id)
    memberships = _seed_memberships(session, tenant_id, domains)
    kpis = _seed_kpis(session, tenant_id, domains)
    cases = _seed_cases(session, tenant_id, domains, kpis)
    return {
        "domains": len(domains),
        "memberships": memberships,
        "kpi_definitions": len(kpis),
        "decisions": cases,
    }


def _seed_domains(session: Session, tenant_id: str) -> dict[str, str]:
    ids: dict[str, str] = {}
    for spec in DOMAINS:
        ids[spec["slug"]] = str(
            session.execute(
                text(
                    """
                    INSERT INTO domains (tenant_id, slug, name, description)
                    VALUES (:t, :slug, :name, :desc)
                    ON CONFLICT (tenant_id, slug)
                      DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description
                    RETURNING id
                    """
                ),
                {"t": tenant_id, "slug": spec["slug"], "name": spec["name"], "desc": spec["description"]},
            ).scalar_one()
        )
    return ids


def _seed_memberships(session: Session, tenant_id: str, domains: dict[str, str]) -> int:
    created = 0
    for domain_slug, email, role in MEMBERSHIPS:
        user_id = session.execute(
            text("SELECT id FROM users WHERE tenant_id = :t AND email = :e"),
            {"t": tenant_id, "e": email},
        ).scalar_one_or_none()
        if user_id is None:
            continue
        session.execute(
            text(
                """
                INSERT INTO team_members (tenant_id, domain_id, user_id, membership_role)
                VALUES (:t, :d, :u, :r)
                ON CONFLICT (tenant_id, domain_id, user_id)
                  DO UPDATE SET membership_role = EXCLUDED.membership_role
                """
            ),
            {"t": tenant_id, "d": domains[domain_slug], "u": user_id, "r": role},
        )
        created += 1
    return created


def _seed_kpis(session: Session, tenant_id: str, domains: dict[str, str]) -> dict[str, str]:
    ids: dict[str, str] = {}
    for spec in KPIS:
        ids[spec["key"]] = str(
            session.execute(
                text(
                    """
                    INSERT INTO kpi_definitions
                        (tenant_id, domain_id, kpi_key, name, description, formula, unit,
                         direction, target_value, warning_value)
                    VALUES (:t, :dom, :key, :name, :desc, :formula, :unit, :dir, :target, :warn)
                    ON CONFLICT (tenant_id, kpi_key) DO UPDATE
                       SET name = EXCLUDED.name, formula = EXCLUDED.formula,
                           target_value = EXCLUDED.target_value
                    RETURNING id
                    """
                ),
                {
                    "t": tenant_id,
                    "dom": domains.get(spec["domain"]) if spec["domain"] else None,
                    "key": spec["key"],
                    "name": spec["name"],
                    "desc": spec["description"],
                    "formula": spec["formula"],
                    "unit": spec["unit"],
                    "dir": spec["direction"],
                    "target": spec["target"],
                    "warn": spec["warning"],
                },
            ).scalar_one()
        )
    return ids


def _user(session: Session, tenant_id: str, email: str) -> str | None:
    row = session.execute(
        text("SELECT id FROM users WHERE tenant_id = :t AND email = :e"),
        {"t": tenant_id, "e": email},
    ).scalar_one_or_none()
    return str(row) if row else None


#: reference, domain, title, summary, final state, risk, evidence, options
CASES: tuple[dict[str, Any], ...] = (
    {
        "reference": "DEC-2026-0041",
        "domain": "signalling",
        "title": "Replace or overhaul the point machines at Al Rashidiya",
        "summary": (
            "Point machine failures at Al Rashidiya have risen for four consecutive "
            "months and are now the largest single contributor to morning peak delay "
            "minutes on the Red Line."
        ),
        "risk": "HIGH",
        "detected_by": "SIGNAL",
        "detection_source": "kpi:signalling.point_machine_failures breached warning threshold",
        "owner": "systems.lead@rta.example",
        "path": ["ANALYSING", "RECOMMENDATION_READY", "AWAITING_REVIEW", "AWAITING_APPROVAL"],
        "evidence": [
            (
                "METRIC",
                "kpi:signalling.point_machine_failures",
                "Nine failures in the last month against a target of four.",
                1.0,
                3,
            ),
            ("INCIDENT", "INC-2026-0311", "Points failure caused 41 delay minutes on 14 August.", 1.0, 14),
            (
                "DATASET",
                "asset-register:point-machines",
                "Eleven of fourteen units are past their design life.",
                0.9,
                21,
            ),
            (
                "DOCUMENT",
                "maintenance-policy-v4",
                "Policy requires overhaul at 80% of design life.",
                0.8,
                200,
            ),
        ],
        "options": [
            (
                "Replace all fourteen units",
                "Full replacement across the interlocking during a staged possession.",
                0.82,
                2_400_000,
                "MEDIUM",
                False,
                False,
            ),
            (
                "Overhaul the eleven aged units",
                "Workshop overhaul with the existing spares pool, retaining three newer units.",
                0.61,
                780_000,
                "MEDIUM",
                True,
                False,
            ),
            (
                "Continue reactive maintenance",
                "Do nothing beyond the current regime.",
                0.18,
                0,
                "HIGH",
                True,
                True,
            ),
        ],
        "recommendation": {
            "option": 0,
            "rationale": (
                "Replacement removes the failure mode rather than deferring it, and the "
                "delay-minute cost of a further year of reactive maintenance exceeds the "
                "capital difference between the two active options."
            ),
            "reasoning_summary": (
                "Compared three options against failure trend, asset age and delay-minute "
                "cost. Replacement scores highest on failure elimination; overhaul is "
                "cheaper but leaves eleven units near end of life within two years."
            ),
        },
    },
    {
        "reference": "DEC-2026-0038",
        "domain": "rolling-stock",
        "title": "Bring forward the door actuator inspection interval",
        "summary": (
            "Door availability has drifted below its 99.5% target for six weeks. "
            "Actuator seals are the dominant recorded cause."
        ),
        "risk": "MEDIUM",
        "detected_by": "AGENT",
        "detection_source": "reliability agent flagged a sustained drift in door availability",
        "owner": "rollingstock.lead@rta.example",
        "path": ["ANALYSING", "RECOMMENDATION_READY", "AWAITING_REVIEW"],
        "evidence": [
            (
                "METRIC",
                "kpi:rolling_stock.door_availability",
                "99.1% over six weeks against a 99.5% target.",
                1.0,
                5,
            ),
            (
                "DATASET",
                "workorders:door-systems",
                "Seal replacement accounts for 63% of door work orders.",
                0.9,
                12,
            ),
        ],
        "options": [
            (
                "Inspect every 90 days",
                "Halve the current interval on the affected fleet only.",
                0.74,
                145_000,
                "LOW",
                True,
                False,
            ),
            (
                "Inspect every 120 days",
                "A smaller reduction applied fleet-wide.",
                0.55,
                96_000,
                "LOW",
                True,
                False,
            ),
            ("Keep the 180-day interval", "No change; continue monitoring.", 0.22, 0, "MEDIUM", True, True),
        ],
        "recommendation": {
            "option": 0,
            "rationale": (
                "The shorter interval targets the fleet actually failing rather than "
                "spending inspection hours across trains that are performing."
            ),
            "reasoning_summary": (
                "Two intervals compared against seal failure distribution and inspection "
                "cost. The 90-day option concentrates effort on the affected fleet."
            ),
        },
    },
    {
        "reference": "DEC-2026-0022",
        "domain": "signalling",
        "title": "Adopt condition-based monitoring on the Green Line interlockings",
        "summary": "Trial of continuous monitoring on four interlockings, now complete.",
        "risk": "MEDIUM",
        "detected_by": "HUMAN",
        "detection_source": "raised by the systems section following the 2025 trial",
        "owner": "systems.lead@rta.example",
        "path": [
            "ANALYSING",
            "RECOMMENDATION_READY",
            "AWAITING_REVIEW",
            "AWAITING_APPROVAL",
            "APPROVED",
            "EXECUTING",
            "VERIFICATION_PENDING",
            "VERIFIED",
        ],
        "evidence": [
            (
                "DOCUMENT",
                "trial-report-2025-cbm",
                "Trial detected seven developing faults before failure.",
                1.0,
                60,
            ),
            (
                "METRIC",
                "kpi:signalling.point_machine_failures",
                "Failures on trial interlockings fell from six to two.",
                1.0,
                30,
            ),
            (
                "RUN",
                "run:cbm-benefit-analysis",
                "Modelled benefit over three years exceeds installation cost.",
                0.7,
                45,
            ),
        ],
        "options": [
            (
                "Roll out to all Green Line interlockings",
                "Extend the trial configuration line-wide.",
                0.88,
                1_150_000,
                "MEDIUM",
                False,
                False,
            ),
            (
                "Extend the trial for another year",
                "Gather a further year of data before committing.",
                0.42,
                210_000,
                "LOW",
                True,
                False,
            ),
            ("Stop at the trial", "Decommission the trial installation.", 0.15, 0, "MEDIUM", True, True),
        ],
        "recommendation": {
            "option": 0,
            "rationale": "The trial met every success criterion set for it in advance.",
            "reasoning_summary": (
                "Trial outcomes compared against the criteria agreed before it started: "
                "fault detection lead time, false positive rate and modelled benefit."
            ),
        },
        "outcome": {
            "kpi": "signalling.point_machine_failures",
            "target": 4.0,
            "actual": 2.0,
            "unit": "failures",
            "verdict": "ACHIEVED",
            "method": "Compared monthly failure counts on the fitted interlockings against the target.",
        },
        "lesson": (
            "Agreeing the success criteria before the trial started is what made the "
            "verification straightforward; the two earlier trials that lacked them "
            "were argued about for months and never formally closed."
        ),
    },
    {
        "reference": "DEC-2026-0044",
        "domain": "track-civils",
        "title": "Investigate drainage backflow at Jebel Ali underpass",
        "summary": "Standing water reported after two consecutive rainfall events.",
        "risk": "MEDIUM",
        "detected_by": "HUMAN",
        "detection_source": "reported by the permanent way inspector",
        "owner": "chief.engineer@rta.example",
        "path": ["ANALYSING"],
        # One evidence item, no scored options: this case exists so the console
        # genuinely renders "Confidence: Not Calculated" against real data
        # rather than only in a test.
        "evidence": [
            ("HUMAN", "inspection-note-2026-08-19", "Standing water observed to 150mm depth.", 0.6, 9),
        ],
        "options": [],
        # A recommendation with no computable confidence. This is the case that
        # matters most in a demo: an analyst has said what they think, and the
        # platform declines to put a percentage on it because the inputs do not
        # support one. Without this the "Not Calculated" state would exist only
        # in a test and never on a rendered page.
        "recommendation": {
            "option": None,
            "rationale": (
                "Survey the drainage run before committing to any remedial work. "
                "One inspection note is not enough to choose between causes."
            ),
            "reasoning_summary": (
                "A single field observation with no options costed. There is not "
                "enough evidence to compare alternatives, so none is proposed."
            ),
        },
    },
)


def _seeding_context(session: Session, tenant_id: str) -> ExecutionContext:
    """The identity the seed acts as.

    A real principal with every permission and MFA satisfied, because seeding is
    an administrative act performed by whoever runs it — not a way to slip past
    the checks. The lifecycle engine applies the same rules to it as to any
    request, which is the point: if a seeded path were illegal, the seed fails
    rather than quietly writing a state nobody could reach.
    """
    row = (
        session.execute(
            text("SELECT id, organization_id, email FROM users WHERE tenant_id = :t AND email = :e"),
            {"t": tenant_id, "e": "admin@rta.example"},
        )
        .mappings()
        .one()
    )
    return ExecutionContext(
        tenant_id=tenant_id,
        organization_id=str(row["organization_id"]),
        human=HumanIdentity(
            user_id=str(row["id"]),
            email=str(row["email"]),
            permissions=frozenset({"*"}),
            roles=frozenset({"platform_admin"}),
            mfa_satisfied=True,
        ),
        environment="development",
    )


def _seed_cases(session: Session, tenant_id: str, domains: dict[str, str], kpis: dict[str, str]) -> int:
    created = 0
    now = utcnow()
    seeding_ctx = _seeding_context(session, tenant_id)
    for spec in CASES:
        existing = session.execute(
            text("SELECT id FROM decisions WHERE tenant_id = :t AND reference = :r"),
            {"t": tenant_id, "r": spec["reference"]},
        ).scalar_one_or_none()
        if existing is not None:
            continue

        owner = _user(session, tenant_id, spec["owner"])
        decision_id = create_decision(
            session,
            seeding_ctx,
            domain_id=domains[spec["domain"]],
            reference=spec["reference"],
            title=spec["title"],
            summary=spec["summary"],
            detected_by=spec["detected_by"],
            detection_source=spec["detection_source"],
            risk=spec["risk"],
            owner_user_id=owner,
        )

        for kind, ref, summary, authority, age_days in spec["evidence"]:
            session.execute(
                text(
                    """
                    INSERT INTO decision_evidence
                        (tenant_id, decision_id, source_kind, source_ref, summary,
                         authority_weight, observed_at)
                    VALUES (:t, :d, :kind, :ref, :sum, :auth, :obs)
                    """
                ),
                {
                    "t": tenant_id,
                    "d": decision_id,
                    "kind": kind,
                    "ref": ref,
                    "sum": summary,
                    "auth": authority,
                    "obs": now - timedelta(days=age_days),
                },
            )

        option_ids: list[str] = []
        for label, description, score, cost, risk, reversible, status_quo in spec["options"]:
            option_ids.append(
                str(
                    session.execute(
                        text(
                            """
                            INSERT INTO decision_options
                                (tenant_id, decision_id, label, description, score,
                                 estimated_cost, risk, reversible, is_status_quo)
                            VALUES (:t, :d, :label, :desc, :score, :cost,
                                    CAST(:risk AS risk_class), :rev, :sq)
                            RETURNING id
                            """
                        ),
                        {
                            "t": tenant_id,
                            "d": decision_id,
                            "label": label,
                            "desc": description,
                            "score": score,
                            "cost": cost,
                            "risk": risk,
                            "rev": reversible,
                            "sq": status_quo,
                        },
                    ).scalar_one()
                )
            )

        recommendation = spec["recommendation"]
        if recommendation is not None:
            # Computed, never written down. A case with thin evidence will
            # honestly report "Not Calculated" here.
            confidence = calculate_confidence(session, tenant_id=tenant_id, decision_id=decision_id)
            session.execute(
                text(
                    """
                    INSERT INTO recommendations
                        (tenant_id, decision_id, option_id, rationale, reasoning_summary,
                         produced_by, confidence, confidence_calculation)
                    VALUES (:t, :d, :opt, :rationale, :summary, 'AGENT', :conf,
                            CAST(:calc AS jsonb))
                    """
                ),
                {
                    "t": tenant_id,
                    "d": decision_id,
                    "opt": (
                        option_ids[recommendation["option"]] if recommendation["option"] is not None else None
                    ),
                    "rationale": recommendation["rationale"],
                    "summary": recommendation["reasoning_summary"],
                    "conf": confidence.value,
                    "calc": json.dumps(confidence.calculation()),
                },
            )

        # The demo history is produced by the lifecycle engine, not written
        # around it. That keeps the single-writer rule true, and it means the
        # seeded cases carry genuine audit ledger entries rather than rows that
        # merely resemble them.
        for state in spec["path"]:
            transition(
                session, seeding_ctx, decision_id=decision_id, to_state=state, reason="seeded demo history"
            )

        outcome = spec.get("outcome")
        if outcome is not None:
            session.execute(
                text(
                    """
                    INSERT INTO decision_outcomes
                        (tenant_id, decision_id, kpi_definition_id, target_value, actual_value,
                         unit, verdict, verification_method, verified_by_user_id, verified_at)
                    VALUES (:t, :d, :kpi, :target, :actual, :unit, :verdict, :method, :by, now())
                    """
                ),
                {
                    "t": tenant_id,
                    "d": decision_id,
                    "kpi": kpis.get(outcome["kpi"]),
                    "target": outcome["target"],
                    "actual": outcome["actual"],
                    "unit": outcome["unit"],
                    "verdict": outcome["verdict"],
                    "method": outcome["method"],
                    "by": owner,
                },
            )
        lesson = spec.get("lesson")
        if lesson is not None:
            session.execute(
                text(
                    """
                    INSERT INTO lessons_learned
                        (tenant_id, decision_id, domain_id, lesson, category, recorded_by_user_id)
                    VALUES (:t, :d, :dom, :lesson, 'PROCESS', :by)
                    """
                ),
                {
                    "t": tenant_id,
                    "d": decision_id,
                    "dom": domains[spec["domain"]],
                    "lesson": lesson,
                    "by": owner,
                },
            )
        created += 1
    return created
