"""Decision Effectiveness Rate — the North Star, and the honest empty case.

    DER = decisions verified as achieving their intended outcome
          ---------------------------------------------------------
          decisions that reached verification at all

The denominator is deliberately *reached verification*, not *created*. A
decision still working its way through review has not failed; counting it as a
miss would punish having a queue. A decision closed as unmeasurable is likewise
excluded from the numerator but stays in the denominator, because "we could not
tell" is a real outcome of the process and hiding it would flatter the rate.

The empty case is the part worth being careful about. With no verified
decisions there is no rate, and both plausible defaults are lies: 0% reads as
total failure and 100% reads as total success, when the truth is that nothing
has been measured yet. So the rate is ``None`` and the surfaces render
"Not Calculated".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

#: States from which a decision has reached, or passed through, verification.
REACHED_VERIFICATION = ("VERIFICATION_PENDING", "VERIFIED", "CLOSED")

#: Verdicts that count as the decision having achieved what it set out to do.
#: PARTIAL is deliberately excluded: a decision that half worked did not work,
#: and a rate that counts it is a rate nobody can act on.
SUCCESSFUL_VERDICTS = ("ACHIEVED",)


@dataclass(slots=True)
class EffectivenessReport:
    rate: float | None
    verified: int
    achieved: int
    reached_verification: int
    unverifiable: int
    in_flight: int

    def display(self) -> str:
        if self.rate is None:
            return "Not Calculated"
        return f"{self.rate * 100:.0f}%"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rate": self.rate,
            "display": self.display(),
            "achieved": self.achieved,
            "verified": self.verified,
            "reached_verification": self.reached_verification,
            "unverifiable": self.unverifiable,
            "in_flight": self.in_flight,
            # Stated in the payload so a consumer cannot quietly reinterpret the
            # figure as something it is not.
            "definition": (
                "decisions verified as achieving their intended outcome, over "
                "decisions that reached verification"
            ),
        }


def decision_effectiveness_rate(
    session: Session, *, tenant_id: str, domain_ids: list[str] | None = None
) -> EffectivenessReport:
    """Compute the rate over the decisions the caller can see.

    Runs through the caller's session, so RLS and the domain filter both apply:
    an executive's figure covers their domains and no others. Two people can
    legitimately see different rates, and each figure is correct for its viewer.
    """
    scope = ""
    params: dict[str, Any] = {"t": tenant_id}
    if domain_ids is not None:
        if not domain_ids:
            # No domains means no visible decisions, which is a genuinely empty
            # set rather than a zero rate.
            return EffectivenessReport(None, 0, 0, 0, 0, 0)
        scope = " AND d.domain_id = ANY(CAST(:doms AS uuid[]))"
        params["doms"] = domain_ids

    row = (
        session.execute(
            text(
                f"""
                SELECT
                  count(*) FILTER (WHERE d.state = ANY(:reached)) AS reached,
                  count(*) FILTER (WHERE d.state = 'VERIFIED')    AS verified,
                  count(*) FILTER (
                      WHERE d.state = 'VERIFIED' AND o.verdict = ANY(:good)
                  ) AS achieved,
                  count(*) FILTER (WHERE o.verdict = 'UNVERIFIABLE') AS unverifiable,
                  count(*) FILTER (WHERE NOT (d.state = ANY(:reached))) AS in_flight
                FROM decisions d
                LEFT JOIN LATERAL (
                    SELECT verdict FROM decision_outcomes
                     WHERE decision_id = d.id AND tenant_id = d.tenant_id
                     ORDER BY created_at DESC LIMIT 1
                ) o ON true
                WHERE d.tenant_id = CAST(:t AS uuid){scope}
                """  # noqa: S608 - scope is a fixed literal, values stay bound
            ),
            {**params, "reached": list(REACHED_VERIFICATION), "good": list(SUCCESSFUL_VERDICTS)},
        )
        .mappings()
        .one()
    )

    reached = int(row["reached"])
    achieved = int(row["achieved"])
    return EffectivenessReport(
        rate=(achieved / reached) if reached else None,
        verified=int(row["verified"]),
        achieved=achieved,
        reached_verification=reached,
        unverifiable=int(row["unverifiable"]),
        in_flight=int(row["in_flight"]),
    )
