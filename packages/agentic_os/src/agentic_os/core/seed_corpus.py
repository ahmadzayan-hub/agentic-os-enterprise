"""Demo corpus seeder.

Ingests a small rail-maintenance knowledge base and two structured datasets
through the *real* pipelines — the same ingestion, chunking, embedding, ACL and
dataset code paths a production upload takes. Nothing here is inserted directly
into a retrieval table, so a green end-to-end demo is evidence that the pipeline
works, not evidence that the fixtures were written carefully.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from sqlalchemy import text

from agentic_os.core.context import ExecutionContext, HumanIdentity
from agentic_os.core.crypto import sha256_hex
from agentic_os.core.db import bind_tenant, provisioning_session_scope
from agentic_os.core.ids import utcnow

DOCUMENTS: tuple[dict[str, Any], ...] = (
    {
        "filename": "escalator-maintenance-procedure.md",
        "title": "Escalator Maintenance Procedure (DOC-221)",
        "classification": "INTERNAL",
        "owner_team": "electromechanical",
        "acl": [{"principal_type": "ROLE", "principal_id": "operator"},
                {"principal_type": "ROLE", "principal_id": "analyst"},
                {"principal_type": "ROLE", "principal_id": "executive"},
                {"principal_type": "ROLE", "principal_id": "auditor"},
                {"principal_type": "AGENT", "principal_id": "operations"},
                {"principal_type": "AGENT", "principal_id": "knowledge"},
                {"principal_type": "AGENT", "principal_id": "engineering"}],
        "body": """# Escalator Maintenance Procedure (DOC-221)

## Scope

This procedure covers preventive and corrective maintenance for passenger
escalators across all stations, referenced as asset class AST-4000 series.

## Inspection intervals

Step chain tension must be inspected every 90 days. Handrail speed deviation is
checked monthly. Comb plate impact detection is verified at every quarterly
inspection.

## Failure modes

Step chain elongation is the dominant failure mode observed across the fleet and
accounts for the majority of unplanned escalator downtime. The step chain
tensioner is the component most frequently replaced under work order WO-4471 and
similar corrective orders.

Handrail drive slippage is the second most common failure and is usually caused
by contamination of the drive surface rather than by wear.

## Corrective action

When step chain elongation exceeds 2 percent of nominal pitch length, the chain
assembly must be replaced. Partial link replacement is not permitted because it
produces uneven load distribution across the remaining links.

## Dependencies

Escalator availability depends on AST-4012 drive units. A failure of AST-4012
affects station throughput during peak periods.
""",
    },
    {
        "filename": "rolling-stock-reliability-standard.md",
        "title": "Rolling Stock Reliability Standard (DOC-334)",
        "classification": "INTERNAL",
        "owner_team": "rolling_stock",
        "acl": [{"principal_type": "ROLE", "principal_id": "operator"},
                {"principal_type": "ROLE", "principal_id": "analyst"},
                {"principal_type": "ROLE", "principal_id": "executive"},
                {"principal_type": "ROLE", "principal_id": "auditor"},
                {"principal_type": "AGENT", "principal_id": "operations"},
                {"principal_type": "AGENT", "principal_id": "knowledge"},
                {"principal_type": "AGENT", "principal_id": "analytics"}],
        "body": """# Rolling Stock Reliability Standard (DOC-334)

## Availability target

Fleet availability must not fall below 97.0 percent measured over any rolling
28-day period. Availability is calculated as scheduled service hours delivered
divided by scheduled service hours planned.

## Mean distance between failures

The contractual target for mean distance between service-affecting failures is
40,000 kilometres. Performance below 35,000 kilometres in any month triggers a
formal reliability review.

## Brake system

Brake pad wear must be measured at every A-check. Pads below 4 millimetres
residual thickness are replaced. Brake performance depends on AST-5100 friction
assemblies.

## Door systems

Door faults are the largest single contributor to service-affecting delays.
Door obstruction detection sensitivity is verified at every B-check.

## Escalation

A reliability trend below target for two consecutive periods requires a written
report to the department director within ten working days.
""",
    },
    {
        "filename": "asset-criticality-policy.md",
        "title": "Asset Criticality and Obsolescence Policy (DOC-410)",
        "classification": "CONFIDENTIAL",
        "owner_team": "engineering",
        "acl": [{"principal_type": "ROLE", "principal_id": "operator"},
                {"principal_type": "ROLE", "principal_id": "executive"},
                {"principal_type": "ROLE", "principal_id": "auditor"},
                {"principal_type": "AGENT", "principal_id": "engineering"},
                {"principal_type": "AGENT", "principal_id": "operations"}],
        "body": """# Asset Criticality and Obsolescence Policy (DOC-410)

## Criticality classification

Assets are classified as safety-critical, service-critical or supporting.
Safety-critical assets require dual authorisation for any configuration change.

## Obsolescence

An asset enters obsolescence review when the manufacturer announces end of
support or when spares lead time exceeds 26 weeks. Project PRJ-118 covers the
signalling obsolescence programme and depends on contract CTR-905.

## Decommissioning

Decommissioning a safety-critical asset is irreversible and requires written
authorisation from the chief engineer and the safety assurance manager. The
decision must record the compensating control that maintains the safety
argument.

## Spares

Spares holding for service-critical assets targets 95 percent line-item
availability. Below 85 percent the asset is added to the departmental risk
register as RSK-220.
""",
    },
    {
        "filename": "restricted-workforce-note.md",
        "title": "Workforce Case Note (RESTRICTED)",
        "classification": "RESTRICTED",
        "owner_team": "hr",
        # Deliberately narrow: only the director holds RESTRICTED clearance and
        # an explicit grant. Retrieval tests assert that nobody else can see it.
        "acl": [{"principal_type": "ROLE", "principal_id": "executive"}],
        "body": """# Workforce Case Note

This note concerns an individual employment matter and is restricted to the
department director. It records a grievance escalation relating to shift
allocation on the escalator maintenance team and references an ongoing review.

The matter is unrelated to asset performance and must not appear in any
operational report.
""",
    },
)


WORK_ORDERS_CSV = """work_order_id,asset_id,section,type,status,opened_date,closed_date,downtime_hours,cost_aed,failure_mode
WO-4471,AST-4012,electromechanical,CORRECTIVE,CLOSED,2026-03-02,2026-03-04,14.5,8200,step_chain_elongation
WO-4472,AST-4013,electromechanical,PREVENTIVE,CLOSED,2026-03-05,2026-03-05,2.0,1100,scheduled_inspection
WO-4473,AST-4012,electromechanical,CORRECTIVE,CLOSED,2026-03-19,2026-03-21,17.0,9400,step_chain_elongation
WO-4474,AST-5100,rolling_stock,CORRECTIVE,CLOSED,2026-03-08,2026-03-09,6.5,15200,brake_pad_wear
WO-4475,AST-5104,rolling_stock,PREVENTIVE,CLOSED,2026-03-11,2026-03-11,4.0,2400,a_check
WO-4476,AST-4020,electromechanical,CORRECTIVE,OPEN,2026-03-22,,0,0,handrail_slippage
WO-4477,AST-6001,infrastructure,PREVENTIVE,CLOSED,2026-03-14,2026-03-14,3.0,1800,track_inspection
WO-4478,AST-5100,rolling_stock,CORRECTIVE,CLOSED,2026-03-25,2026-03-26,7.5,16100,brake_pad_wear
WO-4479,AST-7001,systems,CORRECTIVE,CLOSED,2026-03-17,2026-03-18,9.0,22000,atc_comms_fault
WO-4480,AST-4012,electromechanical,CORRECTIVE,OPEN,2026-03-28,,0,0,step_chain_elongation
WO-4481,AST-7002,systems,PREVENTIVE,CLOSED,2026-03-20,2026-03-20,2.5,1500,scheduled_inspection
WO-4482,AST-6003,infrastructure,CORRECTIVE,CLOSED,2026-03-23,2026-03-24,11.0,13400,switch_actuator_fault
WO-4483,AST-5110,rolling_stock,PREVENTIVE,CLOSED,2026-03-26,2026-03-26,4.0,2400,b_check
WO-4484,AST-4013,electromechanical,CORRECTIVE,CLOSED,2026-03-29,2026-03-30,8.0,6100,handrail_slippage
WO-4485,AST-7001,systems,CORRECTIVE,OPEN,2026-03-30,,0,0,atc_comms_fault
"""

ASSET_REGISTER_CSV = """asset_id,name,section,criticality,install_year,manufacturer,support_status,spares_availability_pct
AST-4012,Escalator Drive Unit - Union North,electromechanical,SERVICE_CRITICAL,2014,Otis,SUPPORTED,91
AST-4013,Escalator Drive Unit - Union South,electromechanical,SERVICE_CRITICAL,2014,Otis,SUPPORTED,91
AST-4020,Escalator Handrail Drive - Central,electromechanical,SUPPORTING,2016,Schindler,SUPPORTED,88
AST-5100,Brake Friction Assembly - Fleet A,rolling_stock,SAFETY_CRITICAL,2012,Knorr-Bremse,END_OF_SUPPORT_ANNOUNCED,72
AST-5104,Traction Motor - Fleet A,rolling_stock,SERVICE_CRITICAL,2012,Alstom,SUPPORTED,84
AST-5110,Door Control Unit - Fleet B,rolling_stock,SAFETY_CRITICAL,2018,Alstom,SUPPORTED,96
AST-6001,Track Section 12 - Mainline,infrastructure,SAFETY_CRITICAL,2010,Vossloh,SUPPORTED,79
AST-6003,Point Machine PM-14,infrastructure,SAFETY_CRITICAL,2011,Siemens,END_OF_SUPPORT_ANNOUNCED,68
AST-7001,ATC Trackside Radio - Zone 3,systems,SAFETY_CRITICAL,2013,Thales,END_OF_SUPPORT_ANNOUNCED,61
AST-7002,SCADA Gateway - OCC,systems,SERVICE_CRITICAL,2017,Siemens,SUPPORTED,93
"""


def _system_ctx(tenant_id: str, organization_id: str, user_id: str) -> ExecutionContext:
    return ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        human=HumanIdentity(
            user_id=user_id,
            email="admin@rta.example",
            roles=frozenset({"platform_admin"}),
            permissions=frozenset({"*"}),
            clearance="RESTRICTED",
            mfa_satisfied=True,
        ),
        service_principal="seed",
    )


def _ingest_dataset(
    session, ctx: ExecutionContext, *, dataset_key: str, name: str, description: str,
    csv_text: str, primary_key: str, owner_team: str, classification: str = "INTERNAL",
) -> int:
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    fields = reader.fieldnames or []

    dataset = session.execute(
        text(
            """
            INSERT INTO datasets (tenant_id, dataset_key, name, description, source_system,
                                  owner_team, classification, schema_fields, primary_key_field,
                                  row_count, freshness_at)
            VALUES (:t, :k, :n, :d, 'maximo-export', :o, CAST(:c AS data_classification),
                    CAST(:fields AS jsonb), :pk, :rc, now())
            ON CONFLICT (tenant_id, dataset_key) DO UPDATE
              SET name = EXCLUDED.name, description = EXCLUDED.description,
                  schema_fields = EXCLUDED.schema_fields, row_count = EXCLUDED.row_count,
                  freshness_at = now(), updated_at = now()
            RETURNING id
            """
        ),
        {
            "t": ctx.tenant_id, "k": dataset_key, "n": name, "d": description, "o": owner_team,
            "c": classification, "fields": json.dumps(fields), "pk": primary_key,
            "rc": len(rows),
        },
    ).one()

    existing = session.execute(
        text("SELECT count(*) FROM dataset_rows WHERE tenant_id = :t AND dataset_id = :d"),
        {"t": ctx.tenant_id, "d": dataset.id},
    ).scalar_one()
    if existing:
        return 0

    batch = session.execute(
        text(
            """
            INSERT INTO dataset_batches (tenant_id, dataset_id, source_file, source_hash,
                                         row_count, ingested_by)
            VALUES (:t, :d, :f, :h, :rc, :by)
            RETURNING id
            """
        ),
        {
            "t": ctx.tenant_id, "d": dataset.id, "f": f"{dataset_key}.csv",
            "h": sha256_hex(csv_text), "rc": len(rows),
            "by": ctx.human.user_id if ctx.human else None,
        },
    ).one()

    for number, row in enumerate(rows, start=1):
        typed = {
            k: (float(v) if _is_number(v) else (None if v == "" else v)) for k, v in row.items()
        }
        flags = [k for k, v in row.items() if v == ""]
        session.execute(
            text(
                """
                INSERT INTO dataset_rows (tenant_id, dataset_id, batch_id, row_key,
                                          source_row_no, data, quality_flags)
                VALUES (:t, :d, :b, :rk, :n, CAST(:data AS jsonb), :flags)
                """
            ),
            {
                "t": ctx.tenant_id, "d": dataset.id, "b": batch.id,
                "rk": str(row.get(primary_key, "")), "n": number,
                "data": json.dumps(typed, default=str),
                "flags": [f"missing:{f}" for f in flags],
            },
        )
    return len(rows)


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def seed_corpus(tenant_id: str, organization_id: str) -> dict[str, int]:
    from agentic_os.knowledge.ingestion import ingest

    summary = {"documents": 0, "chunks": 0, "datasets": 0, "dataset_rows": 0, "graph_nodes": 0}

    with provisioning_session_scope() as session:
        bind_tenant(session, tenant_id)
        admin = session.execute(
            text("SELECT id FROM users WHERE tenant_id = :t AND email = 'admin@rta.example'"),
            {"t": tenant_id},
        ).first()
        if admin is None:
            return summary
        ctx = _system_ctx(tenant_id, organization_id, str(admin.id))

        for spec in DOCUMENTS:
            result = ingest(
                session,
                ctx,
                data=spec["body"].encode("utf-8"),
                filename=spec["filename"],
                mime_type="text/markdown",
                title=spec["title"],
                source_system="seed",
                declared_classification=spec["classification"],
                owner_team=spec["owner_team"],
                acl=list(spec["acl"]),
            )
            if result.published:
                summary["documents"] += 1
                summary["chunks"] += result.chunk_count

        summary["dataset_rows"] += _ingest_dataset(
            session, ctx,
            dataset_key="maintenance.work_orders",
            name="Work Orders (Maximo export)",
            description="Corrective and preventive work orders with downtime and cost.",
            csv_text=WORK_ORDERS_CSV,
            primary_key="work_order_id",
            owner_team="rail_maintenance",
        )
        summary["dataset_rows"] += _ingest_dataset(
            session, ctx,
            dataset_key="assets.register",
            name="Asset Register (Maximo export)",
            description="Asset register with criticality, support status and spares cover.",
            csv_text=ASSET_REGISTER_CSV,
            primary_key="asset_id",
            owner_team="rail_maintenance",
        )
        summary["datasets"] = 2

        summary["graph_nodes"] = int(
            session.execute(
                text("SELECT count(*) FROM knowledge_nodes WHERE tenant_id = :t"),
                {"t": tenant_id},
            ).scalar_one()
        )

    return summary
