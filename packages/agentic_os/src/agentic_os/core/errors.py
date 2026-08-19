"""Platform error taxonomy.

Errors are classified so that the reliability layer can decide retry, DLQ and
compensation behaviour without string matching.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


# Kept as (str, Enum) rather than StrEnum: the members are serialised
# explicitly through ``.value`` everywhere, and StrEnum would additionally
# change ``str()`` and f-string rendering of a member, which is a wire-format
# change for anything that interpolates one by accident.
class ErrorClass(str, Enum):  # noqa: UP042
    VALIDATION = "VALIDATION"
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    POLICY_DENIED = "POLICY_DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    RISK_BLOCKED = "RISK_BLOCKED"
    KILL_SWITCH = "KILL_SWITCH"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL = "INTERNAL"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


#: Error classes for which a retry may succeed without operator action.
RETRYABLE = frozenset(
    {
        ErrorClass.UPSTREAM_UNAVAILABLE,
        ErrorClass.UPSTREAM_TIMEOUT,
        ErrorClass.RATE_LIMITED,
    }
)

_HTTP_STATUS: dict[ErrorClass, int] = {
    ErrorClass.VALIDATION: 422,
    ErrorClass.AUTHENTICATION: 401,
    ErrorClass.AUTHORIZATION: 403,
    ErrorClass.POLICY_DENIED: 403,
    ErrorClass.APPROVAL_REQUIRED: 202,
    ErrorClass.RISK_BLOCKED: 403,
    ErrorClass.KILL_SWITCH: 503,
    ErrorClass.BUDGET_EXCEEDED: 429,
    ErrorClass.CONTRACT_VIOLATION: 403,
    ErrorClass.NOT_FOUND: 404,
    ErrorClass.CONFLICT: 409,
    ErrorClass.UPSTREAM_UNAVAILABLE: 502,
    ErrorClass.UPSTREAM_TIMEOUT: 504,
    ErrorClass.RATE_LIMITED: 429,
    ErrorClass.INTERNAL: 500,
    ErrorClass.NOT_IMPLEMENTED: 501,
}


class AgenticError(Exception):
    """Base error carrying a machine-readable classification."""

    error_class: ErrorClass = ErrorClass.INTERNAL

    def __init__(
        self,
        message: str,
        *,
        error_class: ErrorClass | None = None,
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if error_class is not None:
            self.error_class = error_class
        self.details = details or {}
        self.correlation_id = correlation_id

    @property
    def retryable(self) -> bool:
        return self.error_class in RETRYABLE

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS.get(self.error_class, 500)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": self.error_class.value,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = self.details
        if self.correlation_id:
            payload["correlation_id"] = self.correlation_id
        return payload


class ValidationError(AgenticError):
    error_class = ErrorClass.VALIDATION


class AuthenticationError(AgenticError):
    error_class = ErrorClass.AUTHENTICATION


class AuthorizationError(AgenticError):
    error_class = ErrorClass.AUTHORIZATION


class PolicyDenied(AgenticError):
    error_class = ErrorClass.POLICY_DENIED


class ApprovalRequired(AgenticError):
    error_class = ErrorClass.APPROVAL_REQUIRED


class RiskBlocked(AgenticError):
    error_class = ErrorClass.RISK_BLOCKED


class KillSwitchEngaged(AgenticError):
    error_class = ErrorClass.KILL_SWITCH


class BudgetExceeded(AgenticError):
    error_class = ErrorClass.BUDGET_EXCEEDED


class ContractViolation(AgenticError):
    error_class = ErrorClass.CONTRACT_VIOLATION


class NotFound(AgenticError):
    error_class = ErrorClass.NOT_FOUND


class Conflict(AgenticError):
    error_class = ErrorClass.CONFLICT


class UpstreamUnavailable(AgenticError):
    error_class = ErrorClass.UPSTREAM_UNAVAILABLE


class UpstreamTimeout(AgenticError):
    error_class = ErrorClass.UPSTREAM_TIMEOUT


class RateLimited(AgenticError):
    error_class = ErrorClass.RATE_LIMITED


class NotImplementedCapability(AgenticError):
    """Raised by capabilities that are declared but deliberately not implemented."""

    error_class = ErrorClass.NOT_IMPLEMENTED
