"""Shared adapter client: canonical envelope, redaction-before-send, retry.

Stdlib-only on purpose — hook scripts must run in whatever Python the harness
machine has, with no venv. Redaction here is the FIRST line of the PHI boundary;
the API redacts again defensively.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

TOWER_URL = os.environ.get("TOWER_URL", "http://localhost:8600")
FREE_TEXT_CAP = 500

_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]"),
    (re.compile(r"\b(?:MRN|mrn|medical record(?: number)?)[\s:#]*\d{5,}\b"), "[REDACTED-MRN]"),
    (re.compile(r"\b(?:DOB|dob|date of birth)[\s:]*\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b", re.I),
     "[REDACTED-DOB]"),
    (re.compile(r"\b\d{10,}\b"), "[REDACTED-NUM]"),
]


def _redact(value):
    if isinstance(value, str):
        for pattern, repl in _PATTERNS:
            value = pattern.sub(repl, value)
        return value[:FREE_TEXT_CAP] + "…" if len(value) > FREE_TEXT_CAP else value
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def envelope(harness, session_id, event_type, payload, seq, project_slug=None, host=None):
    return {
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        "harness": harness,
        "session_id": session_id,
        "project_slug": project_slug,
        "host": host or os.uname().nodename,
        "type": event_type,
        "seq": seq,
        "payload": _redact(payload),
    }


def send(events, retries=3):
    """POST a list of envelopes. Best-effort: adapters must never break the
    harness, so failures are swallowed after retries."""
    body = json.dumps(events).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                TOWER_URL + "/v1/events", data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status < 300
        except (urllib.error.URLError, OSError):
            time.sleep(0.5 * (attempt + 1))
    return False
