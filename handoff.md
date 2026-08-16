# HANDOFF — Control Tower

Read this whole file before writing code. Then read `STATE` at the bottom (it is the live status; keep it current).

## What this is

A director's console for a one-person software org that runs a team of AI coding agents. Noah is the director. He does not write most of the code; he sets vision, makes decisions the agents escalate, signs off on deployments, talks to the rest of the company, and watches spend. The Tower is the thing he looks at all day. It should feel like the HUD of an RTS game: everything that matters at a glance, alerts that demand attention, resources across the top, a minimap of the territory, and click-into-a-unit detail on demand.

Four things it must show, always:

1. **Agents** — every agent session running under any harness: which project, what it's doing, whether it's blocked on Noah, context/tokens used, cost so far.
2. **Projects** — the state of each software project (5–6 today): objective, phase, last deploy, open decisions, health.
3. **Apps** — health of software already in production (uptime, error rate, last deploy, last incident).
4. **Intake** — incoming project requests and tickets, untriaged → triaged → assigned.

Plus **spend**: tokens and dollars per project, per harness, today / week / month, with budgets and a circuit breaker.

## What this is NOT

- Not an orchestrator. It does not decide what agents do or route work between them. Noah decides. (Munder Difflin has "Michael"; we do not.) A future v2 may add suggestion-only routing; do not build toward it now.
- Not a harness. It never runs the model. It observes harnesses and talks to them through thin adapters.
- Not a terminal replacement. Terminals stay wherever they are (nodeterm, tmux, VS Code, whatever). The Tower may deep-link to a session; it does not host the pty in v1.
- Not a place PHI goes. Ever. This is a healthcare org. Adapters must not forward tool outputs, file contents, or transcript bodies — only metadata and Noah-facing summaries. See "PHI boundary".

## Hard requirements

**Harness-agnostic.** Primary harnesses today: Claude Code, Codex CLI, Antigravity (Gemini), OpenCode, Gemini CLI/agents. Others will appear. Every harness talks to the Tower through an adapter that emits the canonical event schema below. Nothing above the adapter layer may know which harness it is dealing with beyond a `harness` string used for display and pricing.

**Local-first, self-hostable.** Runs on Noah's machine or a small GCP VM. Postgres + FastAPI + Next.js. Redis for pub/sub. No SaaS dependencies for core function.

**Live.** Agent state changes should be visible within ~1s (WebSocket or SSE from FastAPI). Health probes on their own cadence.

**Boring reliability.** If an adapter dies, the Tower marks the session `stale` after a heartbeat timeout; it never shows a dead agent as running.

## Architecture

```
┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐
│ Claude Code│  │  Codex CLI │  │Antigravity │  │  OpenCode  │  │ Gemini CLI │
└─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
      │ hooks         │ ?             │ ?             │ plugin        │ hooks/OTel
      ▼               ▼               ▼               ▼               ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  ADAPTERS  (tower-adapter-<harness>)  →  canonical events over HTTP/JSON   │
│  fallback for any harness: pty/log-tail adapter                            │
└──────────────────────────────────┬─────────────────────────────────────────┘
                                   ▼  POST /v1/events   (batch ok)
┌────────────────────────────────────────────────────────────────────────────┐
│  TOWER API (FastAPI)  · validate · persist (Postgres) · fan-out (Redis)    │
│  probes (app health) · pricing/cost ledger · budgets/breaker · decisions   │
└──────────────────────────────────┬─────────────────────────────────────────┘
                                   ▼  WS/SSE + REST
┌────────────────────────────────────────────────────────────────────────────┐
│  TOWER UI (Next.js)  · RTS HUD layout · minimap · units · alerts · intake  │
└────────────────────────────────────────────────────────────────────────────┘
```

Repo layout (monorepo):

```
tower/
  api/            FastAPI app, Alembic migrations, probes, pricing
  ui/             Next.js app
  adapters/
    common/       shared client: event envelope, batching, retry, redaction
    claude-code/  hook scripts + settings.json snippet
    codex/
    antigravity/
    opencode/
    gemini/
    pty-tail/     universal fallback
  cli/            `tower` CLI: `tower ask`, `tower deploy-request`, `tower state`
  docs/
  handoff.md      this file
```

## Canonical event schema

One envelope. Adapters emit these; nothing else enters the system.

```json
{
  "id": "uuid",
  "ts": "2026-08-16T14:03:22Z",
  "harness": "claude-code | codex | antigravity | opencode | gemini | other",
  "harness_version": "1.2.3",
  "session_id": "harness-native session id, stable for the life of the session",
  "project_slug": "cce",
  "host": "noah-mbp",
  "type": "<see enum>",
  "seq": 42,
  "payload": { }
}
```

`type` enum (v1 — keep small, add deliberately):

| type | meaning | payload |
|---|---|---|
| `session.start` | agent session began | `{cwd, branch, model, parent_session_id?}` |
| `session.end` | ended (any reason) | `{reason: completed\|error\|killed\|timeout, summary?}` |
| `session.heartbeat` | alive ping (every 15s) | `{status: working\|idle\|blocked}` |
| `activity` | coarse "what I'm doing now" | `{phase: planning\|editing\|running\|testing\|reviewing\|waiting, label}` |
| `tool.call` | a tool was invoked | `{tool, ok, duration_ms}` — **name and outcome only, never args/results** |
| `usage` | tokens consumed since last usage event | `{input, output, cache_read, cache_write, model}` |
| `needs_input` | agent is blocked on a human | `{kind: permission\|question\|approval, prompt, options?, decision_id?}` |
| `decision.request` | explicit escalation via `tower ask` | `{title, context, options[], recommendation?, urgency}` |
| `deploy.request` | agent wants sign-off to deploy | `{app_slug, env, ref, summary, checks[]}` |
| `artifact` | something Noah might want to open | `{kind: pr\|commit\|doc\|url, ref, title}` |
| `log` | Noah-facing note (redacted, short) | `{level, message}` |

Rules:
- Adapters redact before send (see PHI boundary). The API also redacts defensively.
- Missing `project_slug` → API infers from `cwd` via `projects.repo_paths`; if still unknown, session goes to an `unassigned` bucket that shows on the HUD as an alert.
- `seq` is per-session monotonic; API dedups on `(session_id, seq)`.

## Data model (Postgres)

```
harness            (slug PK, display_name, pricing_json, adapter_version)
project            (slug PK, name, objective, phase, repo_paths[], owner, budget_usd_daily, budget_usd_monthly, color)
agent_session      (id PK, harness, harness_session_id, project_slug, host, model, started_at, ended_at, status, last_heartbeat, parent_id, cwd, branch)
event              (id PK, session_id, ts, seq, type, payload jsonb)     -- append-only; partition by month later
usage_ledger       (id, session_id, project_slug, harness, model, ts, input, output, cache_read, cache_write, cost_usd)
decision           (id, project_slug, session_id?, kind, title, context, options jsonb, recommendation, urgency, status: open|answered|expired, answer, answered_at)
app                (slug PK, name, project_slug, env, url, probe_config jsonb, owner)
app_health_sample  (id, app_slug, ts, ok, latency_ms, status_code, error_rate?, detail jsonb)
deployment         (id, app_slug, ref, requested_by_session, requested_at, approved_at, approved_by, deployed_at, status, notes)
intake_item        (id, source: manual|email|form|ticket, submitted_by, title, body, status: new|triaged|assigned|declined|done, project_slug?, priority, created_at)
project_state      (project_slug, updated_at, updated_by_session, summary_md, in_flight[], blockers[], next_up[])   -- one row per project, overwritten
```

Cost = `usage_ledger` × `harness.pricing_json[model]`. Pricing is data, editable in the UI, versioned by date. Unknown model → cost `null` and an alert, never a guess.

## Harness adapters

Design principle: **use native hooks where they exist, fall back to pty/log-tail everywhere else.** Verify every claim below against current docs before building — these harnesses change monthly.

| harness | expected surface (verify) | adapter approach |
|---|---|---|
| Claude Code | lifecycle hooks (SessionStart, PreToolUse, PostToolUse, Notification, Stop, SubagentStop) via `settings.json`; OTel metrics/logs export | hook scripts POST events; usage from OTel or from Stop-hook cost fields. This is the reference adapter — build it first and best. |
| Gemini CLI | hooks and/or OTel telemetry (had OTel early) | same shape as CC if hooks exist; else OTel receiver + pty-tail |
| OpenCode | plugin/event API in TS | thin plugin that emits events |
| Codex CLI | notify/hook config uncertain; has JSONL session logs | log-tail adapter first; hooks if present |
| Antigravity | least documented; IDE-ish | pty-tail + log-tail; treat as best-effort in v1 |

`pty-tail` adapter: wraps any command in a pty, watches for prompt/permission patterns and idle, tails the harness's session log dir if known, emits `session.*`, `activity`, `needs_input` (heuristic), `heartbeat`. Never emits tool args or transcript text. This is what makes "harness-agnostic" true — anything runnable gets at least presence, blocked-state, and duration.

`tower ask` CLI (works from any harness because it's just a shell command the agent runs): `tower ask --project cce --title "..." --option A --option B --recommend A --urgency normal` → creates a `decision`, prints the decision id, and by default **blocks** polling until Noah answers (with `--no-wait` to return immediately). This is the harness-agnostic escalation path and the heart of the director model. Instruct agents in each project's CLAUDE.md/AGENTS.md to use it.

`tower deploy-request` similarly creates a `deployment` awaiting Noah's approval; agents must not deploy to prod without an approved deployment id.

## PHI boundary (non-negotiable)

- Adapters send: event types, tool names, token counts, phases, short agent-authored summaries, decision text the agent chose to write, URLs/refs.
- Adapters never send: tool arguments, tool results, file diffs, transcript bodies, environment variables, anything read from a database.
- `common` client applies a redaction pass (regex for MRN/SSN/DOB/name patterns + length cap 500 chars on any free-text field) before send. API repeats it.
- `event.payload` is jsonb but schema-validated per type; unknown keys are dropped, not stored.

## Decisions loop (v1)

1. Agent hits `needs_input` (detected) or runs `tower ask` (explicit).
2. Tower shows it in the Alerts pane with a badge count in the HUD; optional push (ntfy/Slack) if urgency high.
3. Noah answers in the UI (pick option or free text).
4. `tower ask` polling client returns the answer to the agent's stdout. For hook-detected `needs_input`, v1 just deep-links Noah to the session; v2 can inject.
5. Decision and answer are logged against the project and visible in project history.

## App health (v1)

Probe types: `http` (GET url, expect 2xx, latency), `gcp_cloud_run` (revision, request count, 5xx rate via Monitoring API), `manual`. Cadence per app. Health status = green / amber / red from thresholds in `probe_config`. Deploy events annotate the timeline.

## Intake (v1)

Manual create + a small public form (behind Google auth) that people at VTV can submit to. Email/ticket ingestion later. Triage is Noah dragging cards: new → triaged → assigned(project) → done/declined.

## UI — RTS HUD

Desktop-first, one screen, no scrolling for the main view. Panels:

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ HUD  ⬢ 3 agents active   ⚠ 2 need you   ▮ $14.20 today  ▮ $61 wk / $400 cap │
├──────────────┬────────────────────────────────────────┬───────────────────────┤
│ MINIMAP      │ UNITS                                  │ ALERTS (needs you)    │
│ 6 project    │ agent cards: harness badge · project   │ decision / deploy /   │
│ territories  │ · phase · elapsed · ctx% · $ · status  │ permission — answer   │
│ colored by   │ click → detail drawer (timeline,       │ inline                │
│ state; fog   │ artifacts, usage, deep-link)           ├───────────────────────┤
│ = untriaged  │                                        │ INTAKE (fog of war)   │
│ intake       │                                        │ new requests/tickets  │
├──────────────┴────────────────────────────────────────┴───────────────────────┤
│ BASE HEALTH   CCE ● 99.9%   Corrections ● building   Screener ● 200ms  …      │
└───────────────────────────────────────────────────────────────────────────────┘
```

- Minimap: projects as tiles; tile color = worst of (health, open decisions, budget). Click → project view (state summary, in-flight, decisions, sessions, spend, apps).
- Units: sort by "needs you" first, then active, then idle. Stale sessions dim.
- Alerts: answerable in place. Nothing leaves this pane without an answer or a snooze.
- Keyboard: `1–6` select project, `a` alerts, `u` units, `i` intake, `/` search, `esc` back.
- Sound: optional single ping on new high-urgency alert. Off by default.
- See `wireframe.html` for the visual direction (palette, type, density).

## Milestones

- **M0 — spine.** Postgres schema + migrations, `POST /v1/events`, dedup, session lifecycle + heartbeat/stale, WS fan-out. Claude Code adapter (hooks) end to end. `tower ask` CLI with blocking answer. Seed script with fake sessions for UI work.
- **M1 — read-only HUD.** Next.js: HUD bar, Units, Alerts (read), Minimap. Live over WS.
- **M2 — decisions.** Answer in UI → returned to `tower ask`. Deploy requests + approval.
- **M3 — spend.** usage_ledger, pricing table UI, per-project budgets, circuit breaker (mark project `over_budget`; adapters check a flag and refuse to start new sessions).
- **M4 — health + intake.** http + Cloud Run probes, base health strip, intake board + form.
- **M5 — more adapters.** pty-tail universal, then OpenCode plugin, Gemini, Codex log-tail, Antigravity best-effort. Update `project_state` from agents at session end.

## Working agreement for agents on this repo

- Before starting: read `STATE` below and `docs/decisions.md`.
- Anything that would lock us to one harness above the adapter layer → stop and `tower ask` (or, until the CLI exists, write it in `STATE › Decisions for Noah`).
- Anything touching the PHI boundary → same.
- No new external service dependencies without a decision.
- End every session by updating `STATE`: what changed, what's in flight, decisions for Noah, next up. Keep it under 40 lines.
- Prefer boring: Alembic, pydantic models, plain SQL where it's clearer, no ORM cleverness.

## Open decisions for Noah (answer before or during M0)

1. Repo name and where it lives (personal GitHub vs VTV org).
2. Auth for the UI: Google Workspace SSO (VTV) or local-only for v1?
3. Push channel for high-urgency alerts: ntfy, Slack, Google Chat, none?
4. Budget circuit-breaker behavior: hard-stop new sessions, or warn-only?
5. Should `tower ask` block by default (agent waits) or return immediately? Default proposed: block.

---

## STATE

_Updated: 2026-08-16 by Claude (M0 session)_

**Phase:** M0 built and smoke-tested end to end.
**What changed:**
- Monorepo scaffolded: `api/` (FastAPI + Alembic), `adapters/common` + `adapters/claude-code`, `cli/tower`, `docker-compose.yml` (Postgres :5433, Redis :6380).
- Full Postgres schema in migration `0001`; seed script (`api/seed.py`) with harness pricing, 4 projects, fake sessions, one open decision.
- `POST /v1/events`: envelope + per-type payload validation (unknown keys dropped), dedup on (session_id, seq), session lifecycle, project inference from cwd, usage → cost ledger (unknown model → null cost), defensive PHI redaction. Redis fan-out → `WS /v1/stream`.
- Stale sweeper: no heartbeat for 45s → `stale`.
- `tower ask` blocks by default and returns Noah's answer on stdout — verified round trip. `tower deploy-request`, `tower state` also in.
- Claude Code hook adapter (`adapters/claude-code/tower_hook.py` + settings snippet): SessionStart/End, PostToolUse (name+outcome only), Notification → needs_input, Stop/SubagentStop.
**In flight:** nothing.
**Blockers:** none. Open decisions 1–5 answered with defaults in `docs/decisions.md` — Noah should review 2–4.
**Next up (M1):** Next.js read-only HUD (HUD bar, Units, Alerts, Minimap) over `WS /v1/stream` + REST. Then M2 answer-in-UI (API side already done).
**Decisions for Noah:** review `docs/decisions.md` defaults (auth, push channel, circuit breaker).
