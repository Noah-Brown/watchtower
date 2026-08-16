# Watchtower

A director's console ("Control Tower") for a one-person software org running a team of AI coding agents: live agent sessions, project state, production app health, intake, and spend — all on one RTS-style HUD.

- **[handoff.md](handoff.md)** — the full project brief: architecture, canonical event schema, data model, milestones, and working agreement. Read this first.
- **[wireframe.html](wireframe.html)** — visual direction for the HUD (open in a browser).

## Stack

Postgres + FastAPI + Next.js, Redis for pub/sub. Local-first, self-hostable. Harness-agnostic adapters (Claude Code, Codex CLI, Gemini CLI, OpenCode, and a universal pty-tail fallback) emit a canonical event stream; nothing above the adapter layer knows which harness it came from.

## Quickstart

```bash
docker compose up -d                 # Postgres :5433, Redis :6380
cd api && uv sync
uv run alembic upgrade head
uv run python seed.py                # harnesses + pricing, projects, fake sessions
uv run uvicorn app.main:app --port 8600
```

Then: `curl localhost:8600/v1/health`, `cli/tower state --active`, live events on `ws://localhost:8600/v1/stream`. Wire up a real Claude Code session with `adapters/claude-code/settings.snippet.json`.

HUD (Next.js):

```bash
cd ui && npm install && npm run build && npm run start   # http://localhost:3600
```

## Status

M0 (spine) not started. See the `STATE` section at the bottom of `handoff.md` for live status.
