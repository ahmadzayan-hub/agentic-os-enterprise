"""PII detection and DLP classification.

Detection is pattern- and checksum-based, so it is deterministic, explainable
and free of a model dependency. Detectors report a confidence; anything a
detector cannot validate structurally (for example a name) is deliberately not
claimed, because a false PII label is as damaging operationally as a missed one.

Coverage is reported honestly: :func:`scan` returns which detectors ran, and
``UNSUPPORTED_TYPES`` names categories this implementation does not attempt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Categories deliberately not attempted here. Named so that a compliance
#: reviewer sees the gap rather than inferring full coverage.
UNSUPPORTED_TYPES = (
    "PERSON_NAME",
    "PHYSICAL_ADDRESS",
    "BIOMETRIC",
    "HEALTH_CONDITION",
    "RELIGIOUS_AFFILIATION",
)


@dataclass(frozen=True, slots=True)
class PiiFinding:
    pii_type: str
    detector: str
    confidence: float
    start: int
    end: int
    sample: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pii_type": self.pii_type,
            "detector": self.detector,
            "confidence": self.confidence,
            "span": [self.start, self.end],
            "sample": self.sample,
        }


def _luhn_valid(digits: str) -> bool:
    numbers = [int(d) for d in digits if d.isdigit()]
    if len(numbers) < 13:
        return False
    checksum = 0
    parity = len(numbers) % 2
    for index, digit in enumerate(numbers):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


_PATTERNS: tuple[tuple[str, str, re.Pattern[str], float], ...] = (
    (
        "EMAIL",
        "regex_email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        0.95,
    ),
    (
        "IP_ADDRESS",
        "regex_ipv4",
        re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
        0.85,
    ),
    (
        "IBAN",
        "regex_iban",
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
        0.8,
    ),
    (
        "EMIRATES_ID",
        "regex_emirates_id",
        re.compile(r"\b784-?\d{4}-?\d{7}-?\d\b"),
        0.9,
    ),
    (
        "PASSPORT",
        "regex_passport",
        re.compile(r"\b[A-Z]{1,2}\d{6,9}\b"),
        0.5,
    ),
    (
        "DATE_OF_BIRTH",
        "regex_dob_labelled",
        re.compile(r"\b(?:date of birth|dob|born on)\b\s*[:=-]?\s*\d{1,4}[-/]\d{1,2}[-/]\d{1,4}", re.I),
        0.85,
    ),
    (
        "CREDENTIAL",
        "regex_credential",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|"
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----)"
        ),
        0.97,
    ),
)

_CARD_PATTERN = re.compile(r"\b(?:\d[ -]?){13,19}\b")

#: Candidate telephone runs. Grouping conventions vary far too much for a
#: single fixed regex (``+971 4 284 4444`` and ``+1 (415) 555-0132`` share no
#: shape), so candidates are found loosely and validated by digit count.
_PHONE_CANDIDATE = re.compile(r"(?<![\w.-])\+?\d[\d\s().-]{5,18}\d(?![\w-])")

#: Dates share the digit-and-separator shape of phone numbers. Excluding them
#: keeps ordinary operational text (``closed on 2026-03-12``) from being
#: mislabelled as personal data, which would wrongly raise the record's
#: classification and restrict access to it.
_DATE_SHAPED = re.compile(r"^\s*(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\s*$")

DETECTORS = tuple(name for _, name, _, _ in _PATTERNS) + (
    "luhn_payment_card",
    "digit_count_phone",
)


def _mask(value: str) -> str:
    """Show enough to locate the finding, never enough to reuse it."""
    stripped = value.strip()
    if len(stripped) <= 4:
        return "*" * len(stripped)
    return f"{stripped[:2]}{'*' * max(3, len(stripped) - 4)}{stripped[-2:]}"


def scan(text: str, *, max_findings: int = 500) -> list[PiiFinding]:
    """Return every PII finding in ``text``."""
    findings: list[PiiFinding] = []
    for pii_type, detector, pattern, confidence in _PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                PiiFinding(
                    pii_type=pii_type,
                    detector=detector,
                    confidence=confidence,
                    start=match.start(),
                    end=match.end(),
                    sample=_mask(match.group(0)),
                )
            )
            if len(findings) >= max_findings:
                return findings

    for match in _PHONE_CANDIDATE.finditer(text):
        candidate = match.group(0)
        if _DATE_SHAPED.match(candidate):
            continue
        digit_count = sum(c.isdigit() for c in candidate)
        # E.164 allows 7-15 digits. Anything longer is an identifier or a card,
        # which other detectors own; anything shorter is not a phone number.
        if 7 <= digit_count <= 15:
            findings.append(
                PiiFinding(
                    pii_type="PHONE",
                    detector="digit_count_phone",
                    confidence=0.75 if candidate.strip().startswith("+") else 0.6,
                    start=match.start(),
                    end=match.end(),
                    sample=_mask(candidate),
                )
            )
            if len(findings) >= max_findings:
                return findings

    for match in _CARD_PATTERN.finditer(text):
        candidate = match.group(0)
        if _luhn_valid(candidate):
            findings.append(
                PiiFinding(
                    pii_type="PAYMENT_CARD",
                    detector="luhn_payment_card",
                    confidence=0.92,
                    start=match.start(),
                    end=match.end(),
                    sample=_mask(candidate),
                )
            )
            if len(findings) >= max_findings:
                break
    return findings


def resolve_overlaps(findings: list[PiiFinding]) -> list[PiiFinding]:
    """Reduce overlapping detections to one finding per region.

    Several detectors legitimately match the same characters — a payment card
    also looks like a phone number, an IBAN also looks like a passport. Redacting
    overlapping spans independently corrupts the text and can leave fragments of
    the original value behind, so overlaps are resolved before any replacement.

    The winner is the longest span, breaking ties on confidence: the longest
    match is the one that covers the whole sensitive value.
    """
    if not findings:
        return []
    ordered = sorted(findings, key=lambda f: (-(f.end - f.start), -f.confidence, f.start))
    kept: list[PiiFinding] = []
    for finding in ordered:
        if any(finding.start < k.end and k.start < finding.end for k in kept):
            continue
        kept.append(finding)
    return sorted(kept, key=lambda f: f.start)


def redact(text: str, findings: list[PiiFinding] | None = None) -> str:
    """Replace every finding with a typed placeholder, right to left."""
    findings = findings if findings is not None else scan(text)
    result = text
    for finding in sorted(resolve_overlaps(findings), key=lambda f: f.start, reverse=True):
        result = f"{result[: finding.start]}[{finding.pii_type}_REDACTED]{result[finding.end :]}"
    return result


#: PII type -> minimum classification the containing record must carry.
_CLASSIFICATION_FLOOR = {
    "CREDENTIAL": "RESTRICTED",
    "PAYMENT_CARD": "RESTRICTED",
    "EMIRATES_ID": "RESTRICTED",
    "PASSPORT": "RESTRICTED",
    "IBAN": "RESTRICTED",
    "DATE_OF_BIRTH": "CONFIDENTIAL",
    "EMAIL": "CONFIDENTIAL",
    "PHONE": "CONFIDENTIAL",
    "IP_ADDRESS": "INTERNAL",
}

_ORDER = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED")


def classify(text: str, *, declared_classification: str = "INTERNAL") -> dict[str, Any]:
    """DLP classification: raise the declared level to at least the PII floor."""
    findings = resolve_overlaps(scan(text))
    floor = declared_classification
    for finding in findings:
        candidate = _CLASSIFICATION_FLOOR.get(finding.pii_type, "INTERNAL")
        if _ORDER.index(candidate) > _ORDER.index(floor):
            floor = candidate

    labels = sorted({f.pii_type for f in findings})
    return {
        "classification": floor,
        "raised": floor != declared_classification,
        "declared": declared_classification,
        "labels": labels,
        "findings": [f.to_dict() for f in findings],
        "finding_count": len(findings),
        "detectors_run": list(DETECTORS),
        "unsupported_types": list(UNSUPPORTED_TYPES),
    }
