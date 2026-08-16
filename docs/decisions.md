# Decisions

Answers to the open decisions in `handoff.md`, plus anything decided since.

| # | date | decision | answer |
|---|---|---|---|
| 1 | 2026-08-16 | Repo name and location | `watchtower`, public, personal GitHub (Noah-Brown). Transfer to VTV org later if needed. |
| 2 | 2026-08-16 | UI auth | Local-only for v1. No auth; bind to localhost. Revisit before any VM deployment. |
| 3 | 2026-08-16 | Push channel for high-urgency alerts | None in v1. Alerts pane only. |
| 4 | 2026-08-16 | Budget circuit breaker | Implemented in M3 as proposed: warn at 80% (context nudge), over-budget at 100% (SessionStart injects a hard stop notice; adapters fail open if the Tower is unreachable). |
| 5 | 2026-08-16 | `tower ask` blocking | Blocks by default (as proposed); `--no-wait` returns immediately. |

Defaults 2–4 were taken as the proposed/conservative option to unblock M0; Noah can override any of them here.
