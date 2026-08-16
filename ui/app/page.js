"use client";

import { useEffect, useMemo, useState } from "react";
import {
  useTower, answerDecision, judgeDeployment, fetchSessionDetail, HARNESS_BADGE,
  elapsed, ago, money, tokens, activityText,
} from "@/lib/tower";

const STATUS_UI = {
  running: ["run", "RUNNING"],
  blocked: ["blk", "NEEDS YOU"],
  stale: ["idle", "STALE"],
  ended: ["end", "ENDED"],
  errored: ["blk", "ERRORED"],
};

function Hud({ sessions, needYou, spend, projects, wsLive, apiDown }) {
  const [now, setNow] = useState(null);
  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const running = sessions.filter((s) => s.status === "running").length;
  const blocked = sessions.filter((s) => s.status === "blocked").length;
  const stale = sessions.filter((s) => s.status === "stale").length;
  const today = spend.reduce((a, r) => a + Number(r.today || 0), 0);
  const week = spend.reduce((a, r) => a + Number(r.week || 0), 0);
  const tin = spend.reduce((a, r) => a + Number(r.tokens_in_today || 0), 0);
  const tout = spend.reduce((a, r) => a + Number(r.tokens_out_today || 0), 0);
  const unpriced = spend.reduce((a, r) => a + Number(r.unpriced_rows || 0), 0);

  const clock = now
    ? now.toLocaleString("en-US", {
        weekday: "short", day: "2-digit", month: "short",
        hour: "2-digit", minute: "2-digit", hour12: false,
      }).toUpperCase()
    : "";

  return (
    <header className="hud">
      <div className="brand">TOWER <small>VTV · AI ENGINEERING</small></div>
      <div className="res"><span className="lbl">Agents</span>
        <span className="val">{running} active · {blocked} blocked{stale ? ` · ${stale} stale` : ""}</span></div>
      <div className="res"><span className="lbl">Need you</span>
        <span className={"val" + (needYou ? " alert" : "")}>{needYou}</span></div>
      <div className="res"><span className="lbl">Tokens today</span>
        <span className="val">{tokens(tin)} in · {tokens(tout)} out</span></div>
      <div className="res"><span className="lbl">Spend</span>
        <span className="val">{money(today)} today · {money(week)} wk</span></div>
      {(() => {
        const cap = projects.reduce((a, p) => a + Number(p.budget_usd_daily || 0), 0);
        if (!cap) return null;
        const frac = today / cap;
        return (
          <div className="res"><span className="lbl">Daily cap</span>
            <span className={"val" + (frac >= 0.8 ? " alert" : "")}>{money(today)} / {money(cap)}</span>
            <div className="bar"><i className={frac >= 0.8 ? "hot" : ""}
              style={{ width: `${Math.min(100, frac * 100)}%` }} /></div>
          </div>
        );
      })()}
      {unpriced > 0 && (
        <div className="res"><span className="lbl">⚠ unpriced</span>
          <span className="val alert">{unpriced} usage rows</span></div>
      )}
      <a className="gear" href="/settings" title="pricing + budgets">⚙</a>
      <div className="keys"><kbd>1–6</kbd><kbd>a</kbd><kbd>u</kbd><kbd>i</kbd><kbd>/</kbd></div>
      <div className="clock">
        {clock} · {apiDown
          ? <span className="dead">API ○ down</span>
          : <span className={wsLive ? "live" : "dead"}>WS {wsLive ? "● live" : "○ reconnecting"}</span>}
      </div>
    </header>
  );
}

function Minimap({ projects, unassigned, sessions }) {
  return (
    <aside className="map">
      <h2>Minimap · projects</h2>
      <div className="terr">
        {projects.map((p) => {
          const active = Number(p.agents_running) + Number(p.agents_blocked);
          const cls = p.over_budget ? "bad"
            : Number(p.open_decisions) > 0 || Number(p.agents_blocked) > 0
            ? "warn" : active > 0 ? "ok" : "idle";
          const bits = [];
          if (active || p.agents_stale) {
            for (let i = 0; i < p.agents_running; i++) bits.push(<b key={"r" + i} />);
            for (let i = 0; i < p.agents_blocked; i++) bits.push(<b key={"b" + i} className="blk" />);
            for (let i = 0; i < p.agents_stale; i++) bits.push(<b key={"s" + i} className="stl" />);
          }
          const meta = [
            p.over_budget ? "OVER BUDGET" : null,
            active ? `${active} agent${active > 1 ? "s" : ""}` : "idle",
            Number(p.open_decisions) ? `${p.open_decisions} decision${p.open_decisions > 1 ? "s" : ""}` : null,
            Number(p.spend_today)
              ? money(p.spend_today) + (p.budget_usd_daily ? ` / ${money(p.budget_usd_daily)}` : "")
              : null,
          ].filter(Boolean).join(" · ");
          return (
            <div key={p.slug} className={`tile ${cls}`} title={p.objective || p.name}>
              <span className="dot" />
              <div className="n">{p.name}</div>
              <div className="units">{bits}</div>
              <div className="m">{meta}</div>
            </div>
          );
        })}
      </div>
      {unassigned > 0 && (
        <div className="fog">
          <div className="n">⚠ Unassigned</div>
          <div className="m">{unassigned} session{unassigned > 1 ? "s" : ""} with no project</div>
        </div>
      )}
      <div className="fog"><div className="n">Fog · untriaged</div><div className="m">intake lands in M4</div></div>
      <div className="plist">
        {projects.map((p, i) => (
          <div key={p.slug}><span>{i + 1}</span>{p.name} — {p.phase || "?"}</div>
        ))}
      </div>
    </aside>
  );
}

const FILTERS = ["all", "needs you", "running", "stale"];

function Drawer({ sessionId, onClose }) {
  const [detail, setDetail] = useState(null);
  useEffect(() => {
    let dead = false;
    fetchSessionDetail(sessionId).then((d) => !dead && setDetail(d)).catch(() => {});
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => { dead = true; window.removeEventListener("keydown", onKey); };
  }, [sessionId, onClose]);

  const s = detail?.session;
  return (
    <div className="drawer-veil" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        {!s ? <div className="empty">loading…</div> : (
          <>
            <div className="dr-head">
              <span className={`badge ${(HARNESS_BADGE[s.harness] || [""])[0]}`}>{s.harness}</span>
              <span className="proj">{s.project_slug || "unassigned"}</span>
              <span className="mono dim">{s.status}</span>
              <button className="dr-close" onClick={onClose}>esc</button>
            </div>
            <div className="dr-meta mono">
              <div><span>session</span>{s.harness_session_id}</div>
              <div><span>host</span>{s.host || "—"} · {s.cwd || "—"}{s.branch ? ` @ ${s.branch}` : ""}</div>
              <div><span>model</span>{s.model || "unknown"} · started {ago(s.started_at)} ago</div>
              <div><span>usage</span>{tokens(s.tokens_in)} in · {tokens(s.tokens_out)} out · {money(s.cost_usd)}</div>
            </div>
            {detail.artifacts.length > 0 && (
              <>
                <h2>Artifacts</h2>
                <div className="dr-artifacts">
                  {detail.artifacts.map((a, i) => (
                    <a key={i} className="mono" href={a.payload.ref} target="_blank" rel="noreferrer">
                      [{a.payload.kind}] {a.payload.title || a.payload.ref}
                    </a>
                  ))}
                </div>
              </>
            )}
            <h2>Timeline</h2>
            <div className="dr-timeline mono">
              {detail.timeline.map((e) => (
                <div key={e.seq}>
                  <span className="dim">{ago(e.ts)}</span>
                  <span className="et">{e.type}</span>
                  <span className="ep">{summarizeEvent(e)}</span>
                </div>
              ))}
              {detail.timeline.length === 0 && <div className="dim">no events</div>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function summarizeEvent(e) {
  const p = e.payload || {};
  switch (e.type) {
    case "tool.call": return `${p.tool}${p.ok === false ? " ✗" : ""}${p.duration_ms ? ` · ${p.duration_ms}ms` : ""}`;
    case "activity": return `${p.phase}${p.label ? " — " + p.label : ""}`;
    case "usage": return `${tokens(p.input)} in · ${tokens(p.output)} out`;
    case "session.start": return p.cwd || "";
    case "session.end": return p.reason || "";
    case "needs_input": return p.prompt || p.kind;
    case "decision.request": return p.title;
    case "deploy.request": return `${p.app_slug} → ${p.env} @ ${p.ref}`;
    case "log": return p.message;
    default: return "";
  }
}

function Units({ sessions, onOpen }) {
  const [filter, setFilter] = useState("all");
  const shown = useMemo(() => {
    const rank = { blocked: 0, running: 1, stale: 2, errored: 3, ended: 4 };
    return sessions
      .filter((s) =>
        filter === "all" ? true
        : filter === "needs you" ? s.status === "blocked"
        : s.status === filter)
      .sort((a, b) => (rank[a.status] ?? 9) - (rank[b.status] ?? 9)
        || new Date(b.started_at) - new Date(a.started_at));
  }, [sessions, filter]);

  return (
    <main className="units-pane">
      <div className="hdr">
        <h2>Units · agent sessions</h2>
        <div className="filters">
          {FILTERS.map((f) => (
            <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>{f}</button>
          ))}
        </div>
      </div>
      <div className="grid">
        {shown.length === 0 && <div className="empty">no sessions — waiting for adapters…</div>}
        {shown.map((s) => {
          const [badgeCls, badgeLabel] = HARNESS_BADGE[s.harness] || ["", s.harness];
          const [statusCls, statusLabel] = STATUS_UI[s.status] || ["idle", s.status.toUpperCase()];
          const [act, actSub] = activityText(s.last_activity, s);
          const tok = Number(s.tokens || 0);
          const pct = Math.min(100, Math.round((tok / 500000) * 100));
          return (
            <div key={s.id} onClick={() => onOpen(s.id)}
              className={"card" + (s.status === "blocked" ? " blocked" : "") + (s.status === "stale" ? " stale" : "")}>
              <div className="top">
                <span className={`badge ${badgeCls}`}>{badgeLabel}</span>
                <span className="proj">{s.project_slug || "unassigned"}</span>
                <span className={`status ${statusCls}`}><i />{statusLabel}</span>
              </div>
              <div className="act">{act}<small>{actSub}</small></div>
              <div className="meters">
                <div className="ctx"><i className={pct > 75 ? "hot" : ""} style={{ width: `${pct}%` }} /></div>
                <span>{tokens(tok)} tok</span>
              </div>
              <div className="foot">
                <span>{s.model || "unknown"} · <b>{elapsed(s.started_at)}</b></span>
                <span>{s.cost_usd !== null ? money(s.cost_usd) : "$—"}</span>
              </div>
            </div>
          );
        })}
      </div>
    </main>
  );
}

function Alert({ d, onAnswered }) {
  const [busy, setBusy] = useState(false);
  const [free, setFree] = useState("");
  const options = Array.isArray(d.options) ? d.options : [];
  const answer = async (value) => {
    if (!value || busy) return;
    setBusy(true);
    try { await answerDecision(d.id, value); onAnswered(); }
    catch { setBusy(false); }
  };
  return (
    <div className={"al" + (d.kind === "deploy" ? " deploy" : "")}>
      <div className="k">
        <span>{d.kind} · {d.project_slug || "unassigned"}</span>
        <span>{ago(d.created_at)} · {d.urgency}</span>
      </div>
      <div className="t">{d.title}</div>
      {d.context && <div className="c">{d.context}</div>}
      <div className="opts">
        {options.map((o) => (
          <button key={o} disabled={busy}
            className={o === d.recommendation ? "rec" : ""}
            onClick={() => answer(o)}>
            {o}{o === d.recommendation ? " ★" : ""}
          </button>
        ))}
        <input className="free" placeholder="answer…" value={free} disabled={busy}
          onChange={(e) => setFree(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && answer(free.trim())} />
      </div>
    </div>
  );
}

function DeployAlert({ d, onDone }) {
  const [busy, setBusy] = useState(false);
  const judge = async (verdict) => {
    if (busy) return;
    setBusy(true);
    try { await judgeDeployment(d.id, verdict); onDone(); }
    catch { setBusy(false); }
  };
  return (
    <div className="al deploy">
      <div className="k">
        <span>deploy · {d.project_slug || d.app_slug}</span>
        <span>{ago(d.requested_at)} · → {d.env}</span>
      </div>
      <div className="t">Approve {d.ref} to {d.env}?</div>
      <div className="c">{d.app_slug}{d.notes ? ` — ${d.notes}` : ""}</div>
      <div className="opts">
        <button className="rec" disabled={busy} onClick={() => judge("approve")}>Approve</button>
        <button disabled={busy} onClick={() => judge("reject")}>Reject</button>
      </div>
    </div>
  );
}

function Alerts({ decisions, deployments, refetch }) {
  return (
    <aside className="alerts">
      <h2>Alerts · needs you</h2>
      <div className="stack">
        {decisions.length === 0 && deployments.length === 0 &&
          <div className="empty">nothing needs you</div>}
        {deployments.map((d) => <DeployAlert key={d.id} d={d} onDone={refetch} />)}
        {decisions.map((d) => <Alert key={d.id} d={d} onAnswered={refetch} />)}
      </div>
      <div className="intake">
        <h2>Intake · fog of war</h2>
        <div className="in">
          <div><div className="t">Intake board lands in M4</div>
            <div className="m">manual + form ingestion</div></div>
          <span className="src">soon</span>
        </div>
      </div>
    </aside>
  );
}

function Base({ apps }) {
  return (
    <footer className="base">
      <h2>Base · app health</h2>
      {apps.map((a) => {
        const s = a.last_sample;
        const cls = !s ? "na" : s.ok ? "ok" : "bad";
        const meta = !s ? "no probes yet (M4)" : s.ok ? `ok · ${s.latency_ms}ms` : "failing";
        return (
          <div key={a.slug} className={`app ${cls}`}>
            <span className="dot" />
            <div><div className="n">{a.name} <small className="m">{a.env}</small></div>
              <div className="m">{meta}</div></div>
          </div>
        );
      })}
    </footer>
  );
}

export default function Tower() {
  const t = useTower();
  const [open, setOpen] = useState(null);
  const needYou = t.decisions.length + t.deployments.length;
  return (
    <div className="shell">
      <Hud sessions={t.sessions} needYou={needYou} spend={t.spend} projects={t.projects} wsLive={t.wsLive} apiDown={t.apiDown} />
      <Minimap projects={t.projects} unassigned={t.unassigned} sessions={t.sessions} />
      <Units sessions={t.sessions} onOpen={setOpen} />
      <Alerts decisions={t.decisions} deployments={t.deployments} refetch={t.refetch} />
      <Base apps={t.apps} />
      {open && <Drawer sessionId={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
