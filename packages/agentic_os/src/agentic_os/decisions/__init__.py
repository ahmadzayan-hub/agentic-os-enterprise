"""Decision intelligence: the organisation's decisions as first-class records.

The platform could already run an agent safely. What it could not do was say
what the organisation chose, on what evidence, and whether the choice worked —
because approvals attached to *runs*, and a run is a record of what the machine
did. This package adds the missing object and the loop around it:

    DETECT -> ANALYSE -> RECOMMEND -> REVIEW -> APPROVE -> EXECUTE -> VERIFY -> LEARN

Three rules hold everywhere in here, and each is enforced rather than intended:

* :func:`~agentic_os.decisions.lifecycle.transition` is the only writer of
  ``decisions.state``. Every other module reads it.
* A confidence figure is computed from stored inputs or it is ``None``. There
  is no default and no fallback constant; ``None`` renders as
  "Confidence: Not Calculated".
* Domain membership is part of the SQL predicate, not a filter applied to
  results. A caller outside a domain retrieves nothing rather than retrieving
  and discarding.
"""

from agentic_os.decisions.confidence import (
    ConfidenceResult,
    calculate_confidence,
)
from agentic_os.decisions.effectiveness import (
    EffectivenessReport,
    decision_effectiveness_rate,
)
from agentic_os.decisions.lifecycle import (
    LEGAL_TRANSITIONS,
    STATES,
    DecisionState,
    IllegalTransition,
    create_decision,
    transition,
)
from agentic_os.decisions.repository import (
    get_decision,
    list_decisions,
    user_domain_ids,
)

__all__ = [
    "LEGAL_TRANSITIONS",
    "STATES",
    "ConfidenceResult",
    "DecisionState",
    "EffectivenessReport",
    "IllegalTransition",
    "calculate_confidence",
    "create_decision",
    "decision_effectiveness_rate",
    "get_decision",
    "list_decisions",
    "transition",
    "user_domain_ids",
]
