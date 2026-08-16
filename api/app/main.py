import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis as redis_sync
import redis.asyncio as redis_async
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import events as ev
from .config import EVENTS_CHANNEL, HEARTBEAT_TIMEOUT_S, REDIS_URL, STALE_SWEEP_INTERVAL_S
from .db import engine, get_db
from .redaction import redact_text
from .schemas import (
    DecisionAnswer, DecisionCreate, DeploymentCreate, DeploymentVerdict,
    EventEnvelope, PricingUpdate, ProjectPatch,
)

publisher = redis_sync.Redis.from_url(REDIS_URL)


async def _stale_sweeper():
    while True:
        await asyncio.sleep(STALE_SWEEP_INTERVAL_S)
        try:
            with Session(engine) as db:
                n = ev.sweep_stale(db, HEARTBEAT_TIMEOUT_S)
                flipped = ev.sweep_budgets(db)
                db.commit()
            if n:
                publisher.publish(
                    EVENTS_CHANNEL,
                    json.dumps({"type": "sessions.stale", "count": n}),
                )
            for slug in flipped:
                publisher.publish(
                    EVENTS_CHANNEL,
                    json.dumps({"type": "budget.flip", "project_slug": slug}),
                )
        except Exception:
            pass  # sweeper must never die; next tick retries


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_stale_sweeper())
    yield
    task.cancel()


app = FastAPI(title="Control Tower API", lifespan=lifespan)

# Local-only v1 (docs/decisions.md #2): UI dev server origins only.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3600", "http://127.0.0.1:3600"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/v1/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"ok": True}


@app.post("/v1/events")
def post_events(body: EventEnvelope | list[EventEnvelope], db: Session = Depends(get_db)):
    envelopes = body if isinstance(body, list) else [body]
    results = []
    for envelope in envelopes:
        try:
            result = ev.ingest(db, envelope)
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=f"{envelope.id}: {exc}") from exc
        db.commit()
        results.append({k: result[k] for k in ("accepted", "duplicate", "session_id")})
        if result["event"]:
            publisher.publish(EVENTS_CHANNEL, json.dumps({"type": "event", "event": result["event"]}))
    return {"results": results}


@app.get("/v1/sessions")
def list_sessions(active: bool = False, db: Session = Depends(get_db)):
    where = "WHERE status IN ('running','blocked','stale')" if active else ""
    rows = db.execute(
        text(
            "SELECT id, harness, harness_session_id, project_slug, host, model, started_at,"
            " ended_at, status, last_heartbeat, cwd, branch,"
            " (SELECT COALESCE(SUM(cost_usd),0) FROM usage_ledger u WHERE u.session_id = agent_session.id) AS cost_usd,"
            " (SELECT COALESCE(SUM(input+output),0) FROM usage_ledger u WHERE u.session_id = agent_session.id) AS tokens,"
            " (SELECT jsonb_build_object('type', e.type, 'ts', e.ts, 'payload', e.payload)"
            "    FROM event e WHERE e.session_id = agent_session.id"
            "    AND e.type IN ('activity','tool.call','needs_input','decision.request','deploy.request','log')"
            "    ORDER BY e.seq DESC LIMIT 1) AS last_activity"
            f" FROM agent_session {where} ORDER BY started_at DESC LIMIT 200"
        )
    ).mappings()
    return {"sessions": [dict(r) for r in rows]}


@app.get("/v1/decisions")
def list_decisions(status: str = "open", db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT * FROM decision WHERE status = :status"
            " ORDER BY CASE urgency WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, created_at"
        ),
        {"status": status},
    ).mappings()
    return {"decisions": [dict(r) for r in rows]}


@app.post("/v1/decisions")
def create_decision(body: DecisionCreate, db: Session = Depends(get_db)):
    decision_id = str(uuid.uuid4())
    session_id = None
    if body.session_id:
        row = db.execute(
            text("SELECT id FROM agent_session WHERE id::text = :s OR harness_session_id = :s"),
            {"s": body.session_id},
        ).first()
        session_id = str(row.id) if row else None
    db.execute(
        text(
            "INSERT INTO decision (id, project_slug, session_id, kind, title, context, options,"
            " recommendation, urgency, status, created_at)"
            " VALUES (:id, :proj, :sid, :kind, :title, :ctx, :opts, :rec, :urg, 'open', now())"
        ),
        {
            "id": decision_id,
            "proj": body.project_slug,
            "sid": session_id,
            "kind": body.kind,
            "title": redact_text(body.title),
            "ctx": redact_text(body.context) if body.context else None,
            "opts": json.dumps(body.options),
            "rec": body.recommendation,
            "urg": body.urgency,
        },
    )
    db.commit()
    publisher.publish(
        EVENTS_CHANNEL,
        json.dumps({"type": "decision.open", "decision_id": decision_id, "title": body.title}),
    )
    return {"id": decision_id}


@app.get("/v1/decisions/{decision_id}")
def get_decision(decision_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT * FROM decision WHERE id::text = :id"), {"id": decision_id}
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="decision not found")
    return dict(row)


@app.post("/v1/decisions/{decision_id}/answer")
def answer_decision(decision_id: str, body: DecisionAnswer, db: Session = Depends(get_db)):
    result = db.execute(
        text(
            "UPDATE decision SET status = 'answered', answer = :answer, answered_at = now()"
            " WHERE id::text = :id AND status = 'open' RETURNING id"
        ),
        {"id": decision_id, "answer": body.answer},
    ).first()
    if not result:
        raise HTTPException(status_code=409, detail="decision not open (or not found)")
    db.commit()
    publisher.publish(
        EVENTS_CHANNEL, json.dumps({"type": "decision.answered", "decision_id": decision_id})
    )
    return {"ok": True}


@app.get("/v1/sessions/{session_id}")
def session_detail(session_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text(
            "SELECT *,"
            " (SELECT COALESCE(SUM(cost_usd),0) FROM usage_ledger u WHERE u.session_id = agent_session.id) AS cost_usd,"
            " (SELECT COALESCE(SUM(input),0) FROM usage_ledger u WHERE u.session_id = agent_session.id) AS tokens_in,"
            " (SELECT COALESCE(SUM(output),0) FROM usage_ledger u WHERE u.session_id = agent_session.id) AS tokens_out"
            " FROM agent_session WHERE id::text = :id OR harness_session_id = :id"
        ),
        {"id": session_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="session not found")
    sid = str(row["id"])
    timeline = db.execute(
        text(
            "SELECT ts, seq, type, payload FROM event WHERE session_id = :sid"
            " AND type != 'session.heartbeat' ORDER BY seq DESC LIMIT 200"
        ),
        {"sid": sid},
    ).mappings()
    artifacts = db.execute(
        text(
            "SELECT ts, payload FROM event WHERE session_id = :sid AND type = 'artifact'"
            " ORDER BY seq DESC LIMIT 50"
        ),
        {"sid": sid},
    ).mappings()
    return {
        "session": dict(row),
        "timeline": [dict(r) for r in timeline],
        "artifacts": [dict(r) for r in artifacts],
    }


@app.get("/v1/deployments")
def list_deployments(status: str = "requested", db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT d.*, a.project_slug, a.env FROM deployment d"
            " JOIN app a ON a.slug = d.app_slug"
            " WHERE d.status = :status ORDER BY d.requested_at"
        ),
        {"status": status},
    ).mappings()
    return {"deployments": [dict(r) for r in rows]}


@app.post("/v1/deployments")
def create_deployment_endpoint(body: DeploymentCreate, db: Session = Depends(get_db)):
    session_id = None
    if body.session_id:
        row = db.execute(
            text("SELECT id FROM agent_session WHERE id::text = :s OR harness_session_id = :s"),
            {"s": body.session_id},
        ).first()
        session_id = str(row.id) if row else None
    deployment_id = ev.create_deployment(
        db,
        app_slug=body.app_slug,
        env=body.env,
        ref=body.ref,
        summary=redact_text(body.summary) if body.summary else None,
        checks=body.checks,
        session_id=session_id,
        project_slug=body.project_slug,
    )
    db.commit()
    publisher.publish(
        EVENTS_CHANNEL,
        json.dumps({"type": "deploy.requested", "deployment_id": deployment_id}),
    )
    return {"id": deployment_id}


@app.get("/v1/deployments/{deployment_id}")
def get_deployment(deployment_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT * FROM deployment WHERE id::text = :id"), {"id": deployment_id}
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="deployment not found")
    return dict(row)


@app.post("/v1/deployments/{deployment_id}/{verdict}")
def judge_deployment(deployment_id: str, verdict: str, body: DeploymentVerdict,
                     db: Session = Depends(get_db)):
    if verdict not in ("approve", "reject"):
        raise HTTPException(status_code=404, detail="unknown action")
    status = "approved" if verdict == "approve" else "rejected"
    result = db.execute(
        text(
            "UPDATE deployment SET status = :st, approved_at = now(), approved_by = 'noah',"
            " notes = COALESCE(notes || ' · ', '') || COALESCE(:notes, '')"
            " WHERE id::text = :id AND status = 'requested' RETURNING id"
        ),
        {"st": status, "id": deployment_id, "notes": body.notes},
    ).first()
    if not result:
        raise HTTPException(status_code=409, detail="deployment not in requested state")
    db.commit()
    publisher.publish(
        EVENTS_CHANNEL,
        json.dumps({"type": f"deploy.{status}", "deployment_id": deployment_id}),
    )
    return {"ok": True, "status": status}


@app.get("/v1/projects")
def list_projects(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT p.slug, p.name, p.objective, p.phase, p.color,"
            " p.budget_usd_daily, p.budget_usd_monthly, p.over_budget,"
            " COUNT(s.id) FILTER (WHERE s.status IN ('running')) AS agents_running,"
            " COUNT(s.id) FILTER (WHERE s.status = 'blocked') AS agents_blocked,"
            " COUNT(s.id) FILTER (WHERE s.status = 'stale') AS agents_stale,"
            " (SELECT COUNT(*) FROM decision d WHERE d.project_slug = p.slug AND d.status = 'open') AS open_decisions,"
            " (SELECT COALESCE(SUM(cost_usd), 0) FROM usage_ledger u"
            "   WHERE u.project_slug = p.slug AND u.ts >= date_trunc('day', now())) AS spend_today"
            " FROM project p LEFT JOIN agent_session s ON s.project_slug = p.slug"
            " GROUP BY p.slug ORDER BY p.slug"
        )
    ).mappings()
    unassigned = db.execute(
        text(
            "SELECT COUNT(*) AS n FROM agent_session"
            " WHERE project_slug IS NULL AND status IN ('running','blocked','stale')"
        )
    ).scalar()
    return {"projects": [dict(r) for r in rows], "unassigned_sessions": unassigned}


@app.get("/v1/projects/{slug}/budget")
def project_budget(slug: str, db: Session = Depends(get_db)):
    """The circuit-breaker check. Adapters call this before starting a session;
    over_budget=true means do not start (docs/decisions.md #4)."""
    status = ev.budget_status(db, slug)
    if status is None:
        raise HTTPException(status_code=404, detail="project not found")
    return status


@app.patch("/v1/projects/{slug}")
def patch_project(slug: str, body: ProjectPatch, db: Session = Depends(get_db)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        return {"ok": True}
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    result = db.execute(
        text(f"UPDATE project SET {sets} WHERE slug = :slug RETURNING slug"),
        {**fields, "slug": slug},
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="project not found")
    flipped = ev.sweep_budgets(db)
    db.commit()
    for s in flipped:
        publisher.publish(EVENTS_CHANNEL, json.dumps({"type": "budget.flip", "project_slug": s}))
    publisher.publish(EVENTS_CHANNEL, json.dumps({"type": "project.updated", "project_slug": slug}))
    return {"ok": True}


@app.get("/v1/pricing")
def get_pricing(db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT slug, display_name, pricing_json FROM harness ORDER BY slug")
    ).mappings()
    return {"harnesses": [dict(r) for r in rows]}


@app.put("/v1/pricing/{harness_slug}")
def put_pricing(harness_slug: str, body: PricingUpdate, db: Session = Depends(get_db)):
    result = db.execute(
        text("UPDATE harness SET pricing_json = :p WHERE slug = :s RETURNING slug"),
        {"p": json.dumps(body.pricing_json), "s": harness_slug},
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="harness not found")
    db.commit()
    publisher.publish(EVENTS_CHANNEL, json.dumps({"type": "pricing.updated", "harness": harness_slug}))
    return {"ok": True}


@app.get("/v1/apps")
def list_apps(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT a.slug, a.name, a.project_slug, a.env, a.url,"
            " (SELECT jsonb_build_object('ok', h.ok, 'latency_ms', h.latency_ms, 'ts', h.ts)"
            "    FROM app_health_sample h WHERE h.app_slug = a.slug"
            "    ORDER BY h.ts DESC LIMIT 1) AS last_sample"
            " FROM app a ORDER BY a.slug"
        )
    ).mappings()
    return {"apps": [dict(r) for r in rows]}


@app.get("/v1/spend")
def spend(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT project_slug, harness,"
            " SUM(cost_usd) FILTER (WHERE ts >= date_trunc('day', now())) AS today,"
            " SUM(cost_usd) FILTER (WHERE ts >= date_trunc('week', now())) AS week,"
            " SUM(cost_usd) FILTER (WHERE ts >= date_trunc('month', now())) AS month,"
            " COUNT(*) FILTER (WHERE cost_usd IS NULL) AS unpriced_rows,"
            " SUM(input) FILTER (WHERE ts >= date_trunc('day', now())) AS tokens_in_today,"
            " SUM(output) FILTER (WHERE ts >= date_trunc('day', now())) AS tokens_out_today"
            " FROM usage_ledger GROUP BY project_slug, harness"
        )
    ).mappings()
    return {"spend": [dict(r) for r in rows]}


@app.websocket("/v1/stream")
async def stream(ws: WebSocket):
    await ws.accept()
    r = redis_async.Redis.from_url(REDIS_URL)
    pubsub = r.pubsub()
    await pubsub.subscribe(EVENTS_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await ws.send_text(message["data"].decode())
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.close()
        await r.aclose()
