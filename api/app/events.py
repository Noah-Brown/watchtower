"""Event ingestion: dedup, persistence, session lifecycle, cost ledger, decisions."""

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from .redaction import redact_payload
from .schemas import EventEnvelope


def _infer_project(db: Session, envelope: EventEnvelope, cwd: str | None) -> str | None:
    if envelope.project_slug:
        exists = db.execute(
            text("SELECT 1 FROM project WHERE slug = :slug"), {"slug": envelope.project_slug}
        ).first()
        if exists:
            return envelope.project_slug
    if cwd:
        row = db.execute(
            text(
                "SELECT slug FROM project, unnest(repo_paths) AS rp"
                " WHERE :cwd LIKE rp || '%' ORDER BY length(rp) DESC LIMIT 1"
            ),
            {"cwd": cwd},
        ).first()
        if row:
            return row.slug
    return None  # session lands in the `unassigned` bucket (project_slug NULL)


def _cost_usd(db: Session, harness: str, model: str | None, u: dict) -> float | None:
    if not model:
        return None
    row = db.execute(
        text("SELECT pricing_json FROM harness WHERE slug = :slug"), {"slug": harness}
    ).first()
    pricing = (row.pricing_json or {}).get(model) if row else None
    if not pricing:
        return None  # unknown model → cost null + HUD alert, never a guess
    per_mtok = lambda key: pricing.get(key, 0) / 1_000_000
    return round(
        u.get("input", 0) * per_mtok("input")
        + u.get("output", 0) * per_mtok("output")
        + u.get("cache_read", 0) * per_mtok("cache_read")
        + u.get("cache_write", 0) * per_mtok("cache_write"),
        6,
    )


def ingest(db: Session, envelope: EventEnvelope) -> dict:
    """Process one event. Returns {accepted, duplicate, session_id} plus the
    stored event dict for fan-out (None when duplicate)."""
    payload = redact_payload(envelope.validated_payload())

    # Resolve or create the session row.
    session_row = db.execute(
        text(
            "SELECT id, project_slug FROM agent_session"
            " WHERE harness = :h AND harness_session_id = :sid"
        ),
        {"h": envelope.harness, "sid": envelope.session_id},
    ).first()

    if session_row is None:
        cwd = payload.get("cwd") if envelope.type == "session.start" else None
        project_slug = _infer_project(db, envelope, cwd)
        session_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO agent_session"
                " (id, harness, harness_session_id, project_slug, host, model,"
                "  started_at, status, last_heartbeat, cwd, branch)"
                " VALUES (:id, :h, :sid, :proj, :host, :model, :ts, 'running', :ts, :cwd, :branch)"
            ),
            {
                "id": session_id,
                "h": envelope.harness,
                "sid": envelope.session_id,
                "proj": project_slug,
                "host": envelope.host,
                "model": payload.get("model"),
                "ts": envelope.ts,
                "cwd": cwd,
                "branch": payload.get("branch"),
            },
        )
    else:
        session_id = str(session_row.id)
        project_slug = session_row.project_slug

    # Dedup on (session_id, seq).
    inserted = db.execute(
        text(
            "INSERT INTO event (id, session_id, ts, seq, type, payload)"
            " VALUES (:id, :sid, :ts, :seq, :type, :payload)"
            " ON CONFLICT (session_id, seq) DO NOTHING RETURNING id"
        ),
        {
            "id": str(envelope.id),
            "sid": session_id,
            "ts": envelope.ts,
            "seq": envelope.seq,
            "type": envelope.type,
            "payload": json.dumps(payload),
        },
    ).first()
    if inserted is None:
        return {"accepted": False, "duplicate": True, "session_id": session_id, "event": None}

    # Lifecycle side effects.
    now = datetime.now(timezone.utc)
    if envelope.type == "session.heartbeat":
        db.execute(
            text(
                "UPDATE agent_session SET last_heartbeat = :ts,"
                " status = CASE WHEN status IN ('running','stale','blocked') THEN"
                "   CASE :st WHEN 'blocked' THEN 'blocked' ELSE 'running' END"
                " ELSE status END WHERE id = :id"
            ),
            {"ts": envelope.ts, "st": payload.get("status", "working"), "id": session_id},
        )
    elif envelope.type == "session.end":
        db.execute(
            text(
                "UPDATE agent_session SET ended_at = :ts,"
                " status = CASE :reason WHEN 'completed' THEN 'ended' ELSE 'errored' END"
                " WHERE id = :id"
            ),
            {"ts": envelope.ts, "reason": payload.get("reason", "completed"), "id": session_id},
        )
    elif envelope.type in ("needs_input",):
        db.execute(
            text("UPDATE agent_session SET status = 'blocked', last_heartbeat = :ts WHERE id = :id"),
            {"ts": envelope.ts, "id": session_id},
        )
    else:
        db.execute(
            text(
                "UPDATE agent_session SET last_heartbeat = :ts"
                " WHERE id = :id AND status IN ('running', 'stale')"
            ),
            {"ts": envelope.ts, "id": session_id},
        )

    if envelope.type == "usage":
        db.execute(
            text(
                "INSERT INTO usage_ledger"
                " (id, session_id, project_slug, harness, model, ts,"
                "  input, output, cache_read, cache_write, cost_usd)"
                " VALUES (:id, :sid, :proj, :h, :model, :ts, :i, :o, :cr, :cw, :cost)"
            ),
            {
                "id": str(uuid.uuid4()),
                "sid": session_id,
                "proj": project_slug,
                "h": envelope.harness,
                "model": payload.get("model"),
                "ts": envelope.ts,
                "i": payload.get("input", 0),
                "o": payload.get("output", 0),
                "cr": payload.get("cache_read", 0),
                "cw": payload.get("cache_write", 0),
                "cost": _cost_usd(db, envelope.harness, payload.get("model"), payload),
            },
        )

    if envelope.type == "deploy.request":
        create_deployment(
            db,
            app_slug=payload["app_slug"],
            env=payload.get("env", "prod"),
            ref=payload["ref"],
            summary=payload.get("summary"),
            checks=payload.get("checks") or [],
            session_id=session_id,
            project_slug=project_slug,
        )

    if envelope.type in ("decision.request", "needs_input"):
        kind = {
            "decision.request": "decision",
            "needs_input": payload.get("kind", "question"),
        }[envelope.type]
        title = payload.get("title") or payload.get("prompt") or "(untitled)"
        db.execute(
            text(
                "INSERT INTO decision"
                " (id, project_slug, session_id, kind, title, context, options,"
                "  recommendation, urgency, status, created_at)"
                " VALUES (:id, :proj, :sid, :kind, :title, :ctx, :opts, :rec, :urg, 'open', :ts)"
            ),
            {
                "id": str(envelope.id) if envelope.type != "needs_input" else str(uuid.uuid4()),
                "proj": project_slug,
                "sid": session_id,
                "kind": kind,
                "title": title,
                "ctx": payload.get("context") or payload.get("summary"),
                "opts": json.dumps(payload.get("options") or []),
                "rec": payload.get("recommendation"),
                "urg": payload.get("urgency", "normal"),
                "ts": envelope.ts,
            },
        )

    stored = {
        "id": str(envelope.id),
        "ts": envelope.ts.isoformat(),
        "harness": envelope.harness,
        "session_id": session_id,
        "harness_session_id": envelope.session_id,
        "project_slug": project_slug,
        "type": envelope.type,
        "seq": envelope.seq,
        "payload": payload,
    }
    return {"accepted": True, "duplicate": False, "session_id": session_id, "event": stored}


def create_deployment(db: Session, *, app_slug: str, env: str, ref: str, summary: str | None,
                      checks: list, session_id: str | None, project_slug: str | None) -> str:
    """Create a deployment awaiting approval. Unknown apps are auto-registered
    (minimal row) so a request for a not-yet-registered app still reaches Noah
    instead of bouncing."""
    db.execute(
        text(
            "INSERT INTO app (slug, name, project_slug, env) VALUES (:slug, :slug, :proj, :env)"
            " ON CONFLICT (slug) DO NOTHING"
        ),
        {"slug": app_slug, "proj": project_slug, "env": env},
    )
    deployment_id = str(uuid.uuid4())
    notes = "; ".join(filter(None, [summary, "checks: " + ", ".join(checks) if checks else None]))
    db.execute(
        text(
            "INSERT INTO deployment (id, app_slug, ref, requested_by_session, status, notes)"
            " VALUES (:id, :app, :ref, :sid, 'requested', :notes)"
        ),
        {"id": deployment_id, "app": app_slug, "ref": ref, "sid": session_id, "notes": notes or None},
    )
    return deployment_id


def sweep_stale(db: Session, timeout_s: int) -> int:
    result = db.execute(
        text(
            "UPDATE agent_session SET status = 'stale'"
            " WHERE status IN ('running', 'blocked') AND ended_at IS NULL"
            " AND last_heartbeat < now() - make_interval(secs => :t)"
        ),
        {"t": timeout_s},
    )
    return result.rowcount
