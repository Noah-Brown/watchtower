#!/usr/bin/env python3
"""Claude Code → Tower adapter (reference adapter).

Wired via lifecycle hooks in settings.json (see settings.snippet.json). Claude
Code passes hook input as JSON on stdin; we map each hook to a canonical event
and POST it. Never sends tool args, results, or transcript text — names,
outcomes, and lifecycle only (PHI boundary).

Exit code is always 0: an adapter failure must never block the harness.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
from tower_client import envelope, send  # noqa: E402

SEQ_DIR = os.path.expanduser("~/.tower/seq")


def next_seq(session_id):
    os.makedirs(SEQ_DIR, exist_ok=True)
    path = os.path.join(SEQ_DIR, session_id)
    try:
        with open(path) as f:
            seq = int(f.read().strip()) + 1
    except (FileNotFoundError, ValueError):
        seq = 1
    with open(path, "w") as f:
        f.write(str(seq))
    return seq


def main():
    try:
        hook = json.load(sys.stdin)
    except Exception:
        return
    session_id = hook.get("session_id") or "unknown"
    name = hook.get("hook_event_name", "")

    if name == "SessionStart":
        etype, payload = "session.start", {
            "cwd": hook.get("cwd"),
            "model": (hook.get("model") or {}).get("id") if isinstance(hook.get("model"), dict)
            else hook.get("model"),
        }
    elif name == "SessionEnd":
        etype, payload = "session.end", {"reason": "completed"}
    elif name == "PostToolUse":
        etype, payload = "tool.call", {
            "tool": hook.get("tool_name", "unknown"),
            "ok": not (hook.get("tool_response") or {}).get("is_error", False)
            if isinstance(hook.get("tool_response"), dict) else True,
        }
    elif name == "Notification":
        etype, payload = "needs_input", {
            "kind": "permission",
            "prompt": hook.get("message", "Claude Code is waiting on you"),
        }
    elif name == "Stop":
        etype, payload = "session.heartbeat", {"status": "idle"}
    elif name == "SubagentStop":
        etype, payload = "activity", {"phase": "reviewing", "label": "subagent finished"}
    else:
        return

    event = envelope(
        harness="claude-code",
        session_id=session_id,
        event_type=etype,
        payload=payload,
        seq=next_seq(session_id),
        project_slug=os.environ.get("TOWER_PROJECT"),
    )
    if etype == "session.start":
        event["payload"]["cwd"] = hook.get("cwd")  # cwd is a path, exempt from free-text redaction
    send([event])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
