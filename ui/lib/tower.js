"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export const API = process.env.NEXT_PUBLIC_TOWER_API || "http://localhost:8600";
export const WS_URL = API.replace(/^http/, "ws") + "/v1/stream";

async function getJSON(path) {
  const res = await fetch(API + path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

export function useTower() {
  const [data, setData] = useState({
    sessions: [], decisions: [], deployments: [], projects: [], apps: [], spend: [],
    unassigned: 0, loaded: false, apiDown: false,
  });
  const [wsLive, setWsLive] = useState(false);
  const refetchTimer = useRef(null);

  const refetch = useCallback(async () => {
    try {
      const [sessions, decisions, deployments, projects, apps, spend] = await Promise.all([
        getJSON("/v1/sessions?active=true"),
        getJSON("/v1/decisions?status=open"),
        getJSON("/v1/deployments?status=requested"),
        getJSON("/v1/projects"),
        getJSON("/v1/apps"),
        getJSON("/v1/spend"),
      ]);
      setData({
        sessions: sessions.sessions,
        decisions: decisions.decisions,
        deployments: deployments.deployments,
        projects: projects.projects,
        unassigned: projects.unassigned_sessions,
        apps: apps.apps,
        spend: spend.spend,
        loaded: true,
        apiDown: false,
      });
    } catch {
      setData((d) => ({ ...d, apiDown: true }));
    }
  }, []);

  // Coalesce bursts of WS events into one refetch.
  const scheduleRefetch = useCallback(() => {
    clearTimeout(refetchTimer.current);
    refetchTimer.current = setTimeout(refetch, 400);
  }, [refetch]);

  useEffect(() => {
    refetch();
    const poll = setInterval(refetch, 30000); // fallback if WS drops silently

    let ws;
    let retry = 1000;
    let closed = false;
    const connect = () => {
      ws = new WebSocket(WS_URL);
      ws.onopen = () => { setWsLive(true); retry = 1000; };
      ws.onmessage = scheduleRefetch;
      ws.onclose = () => {
        setWsLive(false);
        if (!closed) setTimeout(connect, (retry = Math.min(retry * 2, 15000)));
      };
      ws.onerror = () => ws.close();
    };
    connect();

    return () => { closed = true; clearInterval(poll); ws?.close(); };
  }, [refetch, scheduleRefetch]);

  return { ...data, wsLive, refetch };
}

export async function answerDecision(id, answer) {
  const res = await fetch(`${API}/v1/decisions/${id}/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
  if (!res.ok) throw new Error(`answer failed: ${res.status}`);
}

export async function judgeDeployment(id, verdict, notes) {
  const res = await fetch(`${API}/v1/deployments/${id}/${verdict}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notes: notes || null }),
  });
  if (!res.ok) throw new Error(`${verdict} failed: ${res.status}`);
}

export async function fetchSessionDetail(id) {
  const res = await fetch(`${API}/v1/sessions/${id}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`detail: ${res.status}`);
  return res.json();
}

export const HARNESS_BADGE = {
  "claude-code": ["h-cc", "claude code"],
  codex: ["h-cx", "codex"],
  antigravity: ["h-ag", "antigravity"],
  opencode: ["h-oc", "opencode"],
  gemini: ["h-gm", "gemini"],
  other: ["", "other"],
};

export function elapsed(startISO) {
  const s = Math.max(0, (Date.now() - new Date(startISO)) / 1000);
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

export function ago(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function money(v) {
  if (v === null || v === undefined) return "$—";
  return `$${Number(v).toFixed(2)}`;
}

export function tokens(n) {
  if (n === null || n === undefined) return "—";
  n = Number(n);
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}k`;
  return String(n);
}

export function activityText(la, session) {
  if (!la) {
    return session.status === "stale"
      ? [`No heartbeat since ${ago(session.last_heartbeat)} ago`, "adapter silent"]
      : ["Session active", "no activity events yet"];
  }
  const p = la.payload || {};
  const when = `${ago(la.ts)} ago`;
  switch (la.type) {
    case "tool.call": return [`Tool: ${p.tool}${p.ok === false ? " ✗" : ""}`, when];
    case "activity": return [`${p.phase}${p.label ? " — " + p.label : ""}`, when];
    case "needs_input": return [p.prompt || "Waiting on you", `${p.kind} · ${when}`];
    case "decision.request": return [`Waiting on decision: ${p.title}`, `tower ask · ${when}${p.recommendation ? " · rec: " + p.recommendation : ""}`];
    case "deploy.request": return [`Deploy request: ${p.app_slug} → ${p.env}`, `ref ${p.ref} · ${when}`];
    case "log": return [p.message, when];
    default: return [la.type, when];
  }
}
