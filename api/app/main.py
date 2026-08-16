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
from .schemas import DecisionAnswer, DecisionCreate, EventEnvelope

publisher = redis_sync.Redis.from_url(REDIS_URL)


async def _stale_sweeper():
    while True:
        await asyncio.sleep(STALE_SWEEP_INTERVAL_S)
        try:
            with Session(engine) as db:
                n = ev.sweep_stale(db, HEARTBEAT_TIMEOUT_S)
                db.commit()
            if n:
                publisher.publish(
                    EVENTS_CHANNEL,
                    json.dumps({"type": "sessions.stale", "count": n}),
                )
        except Exception:
            pass  # sweeper must never die; next tick retries


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_stale_sweeper())
    yield
    task.cancel()


app = FastAPI(title="Control Tower API", lifespan=lifespan)


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
            " (SELECT COALESCE(SUM(input+output),0) FROM usage_ledger u WHERE u.session_id = agent_session.id) AS tokens"
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


@app.get("/v1/spend")
def spend(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT project_slug, harness,"
            " SUM(cost_usd) FILTER (WHERE ts >= date_trunc('day', now())) AS today,"
            " SUM(cost_usd) FILTER (WHERE ts >= date_trunc('week', now())) AS week,"
            " SUM(cost_usd) FILTER (WHERE ts >= date_trunc('month', now())) AS month,"
            " COUNT(*) FILTER (WHERE cost_usd IS NULL) AS unpriced_rows"
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
