# Watchtower

A director's console ("Control Tower") for a one-person software org running a team of AI coding agents: live agent sessions, project state, production app health, intake, and spend — all on one RTS-style HUD.

- **[handoff.md](handoff.md)** — the full project brief: architecture, canonical event schema, data model, milestones, and working agreement. Read this first.
- **[wireframe.html](wireframe.html)** — visual direction for the HUD (open in a browser).

## Stack

Postgres + FastAPI + Next.js, Redis for pub/sub. Local-first, self-hostable. Harness-agnostic adapters (Claude Code, Codex CLI, Gemini CLI, OpenCode, and a universal pty-tail fallback) emit a canonical event stream; nothing above the adapter layer knows which harness it came from.

## Status

M0 (spine) not started. See the `STATE` section at the bottom of `handoff.md` for live status.
