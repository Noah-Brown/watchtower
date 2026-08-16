"""Seed harnesses, projects, apps, and a few fake live sessions for UI work.

Run: uv run python seed.py  (idempotent — upserts reference data, recreates fake sessions)
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import engine

# Pricing is data, editable later in the UI. USD per million tokens.
HARNESSES = [
    ("claude-code", "Claude Code", {
        "claude-fable-5": {"input": 25, "output": 100, "cache_read": 2.5, "cache_write": 31.25},
        "claude-opus-5": {"input": 10, "output": 40, "cache_read": 1, "cache_write": 12.5},
        "claude-sonnet-5": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75},
    }),
    ("codex", "Codex CLI", {}),
    ("antigravity", "Antigravity", {}),
    ("opencode", "OpenCode", {}),
    ("gemini", "Gemini CLI", {}),
    ("other", "Other", {}),
]

PROJECTS = [
    ("tower", "Control Tower", "The director's console itself", "M0",
     ["/home/nbrown/projects/watchtower"], "#7c5cff"),
    ("cce", "CCE", "Clinical claims engine", "maintain", [], "#3ddc84"),
    ("corrections", "Corrections", "Corrections intake pipeline", "build", [], "#ffb020"),
    ("screener", "Screener", "Eligibility screener", "maintain", [], "#29b6f6"),
]

APPS = [
    ("cce-prod", "CCE", "cce", "prod", "https://example.invalid/cce/health"),
    ("screener-prod", "Screener", "screener", "prod", "https://example.invalid/screener/health"),
]


def seed():
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        for slug, name, pricing in HARNESSES:
            db.execute(text(
                "INSERT INTO harness (slug, display_name, pricing_json) VALUES (:s, :n, :p)"
                " ON CONFLICT (slug) DO UPDATE SET display_name = :n, pricing_json = :p"
            ), {"s": slug, "n": name, "p": json.dumps(pricing)})

        for slug, name, objective, phase, paths, color in PROJECTS:
            db.execute(text(
                "INSERT INTO project (slug, name, objective, phase, repo_paths, color, owner)"
                " VALUES (:s, :n, :o, :ph, :paths, :c, 'noah')"
                " ON CONFLICT (slug) DO UPDATE SET name = :n, objective = :o, phase = :ph,"
                " repo_paths = :paths, color = :c"
            ), {"s": slug, "n": name, "o": objective, "ph": phase, "paths": paths, "c": color})

        for slug, name, project, env, url in APPS:
            db.execute(text(
                "INSERT INTO app (slug, name, project_slug, env, url, probe_config)"
                " VALUES (:s, :n, :p, :e, :u, '{\"type\": \"http\", \"interval_s\": 60}')"
                " ON CONFLICT (slug) DO NOTHING"
            ), {"s": slug, "n": name, "p": project, "e": env, "u": url})

        # Fake sessions (replace on each run).
        db.execute(text("DELETE FROM usage_ledger WHERE session_id IN"
                        " (SELECT id FROM agent_session WHERE harness_session_id LIKE 'seed-%')"))
        db.execute(text("DELETE FROM decision WHERE session_id IN"
                        " (SELECT id FROM agent_session WHERE harness_session_id LIKE 'seed-%')"))
        db.execute(text("DELETE FROM event WHERE session_id IN"
                        " (SELECT id FROM agent_session WHERE harness_session_id LIKE 'seed-%')"))
        db.execute(text("DELETE FROM agent_session WHERE harness_session_id LIKE 'seed-%'"))

        fakes = [
            ("seed-1", "claude-code", "cce", "claude-fable-5", "running", 0),
            ("seed-2", "claude-code", "corrections", "claude-sonnet-5", "blocked", 0),
            ("seed-3", "gemini", "screener", None, "stale", 120),
        ]
        for hsid, harness, project, model, status, hb_age in fakes:
            sid = str(uuid.uuid4())
            db.execute(text(
                "INSERT INTO agent_session (id, harness, harness_session_id, project_slug, host,"
                " model, started_at, status, last_heartbeat, cwd)"
                " VALUES (:id, :h, :hsid, :p, 'noah-mbp', :m, :start, :st, :hb, '/fake')"
            ), {"id": sid, "h": harness, "hsid": hsid, "p": project, "m": model,
                "start": now - timedelta(minutes=42), "st": status,
                "hb": now - timedelta(seconds=hb_age)})
            if model:
                db.execute(text(
                    "INSERT INTO usage_ledger (id, session_id, project_slug, harness, model, ts,"
                    " input, output, cache_read, cache_write, cost_usd)"
                    " VALUES (:id, :sid, :p, :h, :m, :ts, 180000, 22000, 900000, 40000, :cost)"
                ), {"id": str(uuid.uuid4()), "sid": sid, "p": project, "h": harness,
                    "m": model, "ts": now - timedelta(minutes=5),
                    "cost": 9.55 if model == "claude-fable-5" else 1.12})

        # One open decision so the Alerts pane has something to show.
        db.execute(text(
            "INSERT INTO decision (id, project_slug, kind, title, context, options,"
            " recommendation, urgency, status)"
            " SELECT :id, 'corrections', 'decision', 'Schema for intake dedupe',"
            " 'Two viable unique keys; pick one before migration lands.',"
            " '[\"(source, external_id)\", \"content hash\"]', '(source, external_id)',"
            " 'normal', 'open'"
            " WHERE NOT EXISTS (SELECT 1 FROM decision WHERE title = 'Schema for intake dedupe')"
        ), {"id": str(uuid.uuid4())})

        db.commit()
    print("seeded.")


if __name__ == "__main__":
    seed()
