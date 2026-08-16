"use client";

import { useEffect, useState } from "react";
import { getPricing, putPricing, getProjects, patchProject, money } from "@/lib/tower";

const PRICE_KEYS = ["input", "output", "cache_read", "cache_write"];

function PricingTable({ h, onSaved }) {
  const [pricing, setPricing] = useState(h.pricing_json || {});
  const [newModel, setNewModel] = useState("");
  const [state, setState] = useState("idle"); // idle | dirty | saving | saved

  const setPrice = (model, key, value) => {
    setState("dirty");
    setPricing((p) => ({
      ...p,
      [model]: { ...p[model], [key]: value === "" ? 0 : Number(value) },
    }));
  };

  const save = async () => {
    setState("saving");
    try { await putPricing(h.slug, pricing); setState("saved"); onSaved?.(); }
    catch { setState("dirty"); }
  };

  const models = Object.keys(pricing).sort();
  return (
    <section className="set-block">
      <h2>{h.display_name} <span className="dim mono">$/Mtok</span></h2>
      <table className="set-table mono">
        <thead>
          <tr><th>model</th>{PRICE_KEYS.map((k) => <th key={k}>{k}</th>)}<th /></tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m}>
              <td>{m}</td>
              {PRICE_KEYS.map((k) => (
                <td key={k}>
                  <input type="number" step="0.01" min="0"
                    value={pricing[m]?.[k] ?? ""}
                    onChange={(e) => setPrice(m, k, e.target.value)} />
                </td>
              ))}
              <td><button className="ghost" onClick={() => {
                setState("dirty");
                setPricing((p) => Object.fromEntries(Object.entries(p).filter(([key]) => key !== m)));
              }}>✕</button></td>
            </tr>
          ))}
          {models.length === 0 && <tr><td className="dim" colSpan={6}>no models priced — usage shows as unpriced on the HUD</td></tr>}
        </tbody>
      </table>
      <div className="set-actions">
        <input placeholder="add model id…" value={newModel}
          onChange={(e) => setNewModel(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && newModel.trim()) {
              setPrice(newModel.trim(), "input", 0);
              setNewModel("");
            }
          }} />
        <button disabled={state !== "dirty"} onClick={save}>
          {state === "saving" ? "saving…" : state === "saved" ? "saved ✓" : "save"}
        </button>
      </div>
    </section>
  );
}

function BudgetRow({ p, onSaved }) {
  const [daily, setDaily] = useState(p.budget_usd_daily ?? "");
  const [monthly, setMonthly] = useState(p.budget_usd_monthly ?? "");
  const [state, setState] = useState("idle");
  const save = async () => {
    setState("saving");
    try {
      await patchProject(p.slug, {
        budget_usd_daily: daily === "" ? null : Number(daily),
        budget_usd_monthly: monthly === "" ? null : Number(monthly),
      });
      setState("saved"); onSaved?.();
    } catch { setState("dirty"); }
  };
  return (
    <tr className={p.over_budget ? "over" : ""}>
      <td>{p.name}{p.over_budget && <span className="flag"> OVER BUDGET</span>}</td>
      <td>{money(p.spend_today)}</td>
      <td><input type="number" step="1" min="0" placeholder="—" value={daily}
        onChange={(e) => { setDaily(e.target.value); setState("dirty"); }} /></td>
      <td><input type="number" step="10" min="0" placeholder="—" value={monthly}
        onChange={(e) => { setMonthly(e.target.value); setState("dirty"); }} /></td>
      <td><button disabled={state !== "dirty"} onClick={save}>
        {state === "saving" ? "…" : state === "saved" ? "✓" : "save"}
      </button></td>
    </tr>
  );
}

export default function Settings() {
  const [harnesses, setHarnesses] = useState([]);
  const [projects, setProjects] = useState([]);
  const load = () => {
    getPricing().then((d) => setHarnesses(d.harnesses)).catch(() => {});
    getProjects().then((d) => setProjects(d.projects)).catch(() => {});
  };
  useEffect(load, []);

  return (
    <div className="settings">
      <header className="hud">
        <div className="brand">TOWER <small>SETTINGS · PRICING + BUDGETS</small></div>
        <a className="gear" href="/" title="back to HUD">← HUD</a>
      </header>
      <div className="set-cols">
        <div>
          <h2>Project budgets <span className="dim mono">USD</span></h2>
          <table className="set-table mono">
            <thead><tr><th>project</th><th>today</th><th>daily cap</th><th>monthly cap</th><th /></tr></thead>
            <tbody>
              {projects.map((p) => <BudgetRow key={p.slug} p={p} onSaved={load} />)}
            </tbody>
          </table>
          <p className="dim note">Breaker: warn at 80%, over-budget flag at 100%. Adapters check
            <span className="mono"> /v1/projects/&#123;slug&#125;/budget</span> before starting sessions.</p>
        </div>
        <div>
          {harnesses.map((h) => <PricingTable key={h.slug} h={h} onSaved={load} />)}
        </div>
      </div>
    </div>
  );
}
