"""Defensive redaction pass, applied to every free-text field before storage.

Adapters redact before sending (adapters/common); the API repeats it here so a
misbehaving or third-party adapter can't land PHI in the database.
"""

import re

FREE_TEXT_CAP = 500

_PATTERNS = [
    # SSN
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]"),
    # MRN-style identifiers (label followed by digits)
    (re.compile(r"\b(?:MRN|mrn|medical record(?: number)?)[\s:#]*\d{5,}\b"), "[REDACTED-MRN]"),
    # Dates of birth (label followed by a date)
    (
        re.compile(
            r"\b(?:DOB|dob|date of birth)[\s:]*\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b",
            re.IGNORECASE,
        ),
        "[REDACTED-DOB]",
    ),
    # Long digit runs (10+) — phone numbers, account numbers, bare MRNs
    (re.compile(r"\b\d{10,}\b"), "[REDACTED-NUM]"),
]


def redact_text(value: str) -> str:
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    if len(value) > FREE_TEXT_CAP:
        value = value[:FREE_TEXT_CAP] + "…"
    return value


def redact_payload(payload):
    """Recursively redact every string in a JSON-ish structure."""
    if isinstance(payload, str):
        return redact_text(payload)
    if isinstance(payload, dict):
        return {k: redact_payload(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [redact_payload(v) for v in payload]
    return payload
