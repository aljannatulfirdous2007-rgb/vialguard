// "Ask VialGuard": a tool-calling assistant over the live shipment.
// Pattern: question -> intent router -> tool calls -> answer built ONLY from tool results.
// Deliberately rule-based: runs offline on a phone and cannot invent a medical answer.
// Every tool call and its raw result is returned so the UI can show the full trace.
(function (root) {
  "use strict";

  // compliance rules the agent can cite (same numbers the model uses)
  const SOP = {
    demo: { range: "2 to 8 °C", freeze: true, budget_h: 48, source: "Demo profile (illustrative biologic)" },
    insulin: { range: "2 to 8 °C unopened", freeze: true, budget_h: 672, source: "Insulin label: up to 28 days out of the fridge below 30 °C; never freeze" },
    vvm7: { range: "2 to 8 °C", freeze: true, budget_h: 1080, source: "WHO VVM7 class: end point after 7 days at 37 °C or 45 days at 25 °C; freeze-sensitive" },
    vvm2: { range: "2 to 8 °C", freeze: false, budget_h: 309, source: "WHO VVM2 class: heat-sensitive, tolerates freezing" },
  };

  const TOOLS = {
    query_telemetry: (ctx) => {
      const s = ctx.state;
      return { shipment: s.ship, drug: s.drug_name, temp_c: s.temp, life_pct: Math.round(s.budget), verdict: s.verdict,
        frozen: !!s.frozen, excursion: !!s.excursion, lid_open: !!s.lid,
        location: s.trip ? (s.trip.delivered ? `delivered to ${s.trip.dest}` : `${Math.round((s.trip.total_h - s.trip.done_h) * 60)} min from ${s.trip.dest}`) : "storage" };
    },
    search_compliance_sop: (ctx) => {
      const r = SOP[ctx.state.drug] || SOP.demo;
      return { drug: ctx.state.drug_name, storage: r.range, freeze_sensitive: r.freeze, heat_budget_h_at_25c: r.budget_h, source: r.source };
    },
    forecast_arrival: (ctx) => {
      const f = ctx.state.forecast;
      if (!f) return { available: false, reason: "no live trip (replay mode)" };
      return { destination: f.dest.name, eta_min: Math.round(f.dest.eta_h * 60), life_on_arrival_pct: Math.round(f.dest.life_at_arrival),
        will_arrive_usable: f.will_survive, cooling_ok: f.cooling_ok, outside_c: f.ambient_now };
    },
    find_alternatives: (ctx) => {
      const f = ctx.state.forecast;
      if (!f) return { available: false };
      return { recommended: f.reroute ? { name: f.reroute.name, eta_min: Math.round(f.reroute.eta_h * 60), life_on_arrival_pct: Math.round(f.reroute.life_at_arrival) } : null,
        nearest: (f.alternatives || []).map((a) => ({ name: a.name, eta_min: Math.round(a.eta_h * 60), life_on_arrival_pct: Math.round(a.life_at_arrival) })) };
    },
    diagnose_cause: (ctx) => {
      const s = ctx.state, c = s.cause || {};
      return { root_cause: c.code || "none", explanation: c.text || "No abnormal pattern right now.",
        early_warning: s.breach ? `${s.breach.kind} predicted in ${Math.round(s.breach.minutes)} min` : (s.ml && s.ml.temp > 8 ? `XGBoost: ${s.ml.temp} °C in 10 min` : "none"),
        ageing_rate_vs_25c: +Math.exp(-(SOP[s.drug] && s.drug.startsWith("vvm") ? 14340 : 10000) * (1 / (s.temp + 273.15) - 1 / 298.15)).toFixed(2) };
    },
    audit_log: (ctx) => {
      const recs = ctx.records || [];
      const v = root.VGChain ? root.VGChain.verifyChain(recs) : { ok: true, checked: recs.length };
      const ev = recs.flatMap((r) => (r.ev ? r.ev.split(",") : []));
      const count = (k) => ev.filter((e) => e === k).length;
      const b = ctx.state.behavior || {};
      return { readings_sealed: recs.length, chain_intact: v.ok, tampered_reading: v.ok ? null : v.brokenSeq,
        lid_openings: count("LID_OPEN"), unlogged_openings: b.unexplained || 0, cooling_failures: b.cooling || 0, custody_score: b.trust ?? 100 };
    },
  };

  const INTENTS = [
    { id: "tamper", re: /tamper|fake|edit|audit|log|open(ed)?|who|custody|trust|chain/i, tools: ["audit_log", "diagnose_cause"] },
    { id: "divert", re: /divert|re-?route|where|nearest|alternative|hospital|phc|go to/i, tools: ["forecast_arrival", "find_alternatives"] },
    { id: "why", re: /why|cause|reason|explain|what happened|risk/i, tools: ["query_telemetry", "diagnose_cause"] },
    { id: "usable", re: /use|usable|safe|good|potency|still work|discard|give|administer/i, tools: ["query_telemetry", "search_compliance_sop"] },
    { id: "track", re: /track|status|how is|arrive|eta|when|on time|ok\b|okay/i, tools: ["query_telemetry", "search_compliance_sop", "forecast_arrival"] },
    { id: "rules", re: /rule|sop|limit|range|store|storage|freeze|frozen|guideline|who\b/i, tools: ["search_compliance_sop", "query_telemetry"] },
  ];

  const VERDICT_TEXT = { OK: "safe to use", USE_SOON: "usable but should be used soon", USE_IMMEDIATELY: "usable only if used immediately",
    DISCARD_HEAT: "not usable: heat has used up its stability budget", DISCARD_FROZEN: "not usable: it froze, which is irreversible" };

  function compose(intent, r) {
    const t = r.query_telemetry, sop = r.search_compliance_sop, fc = r.forecast_arrival, alt = r.find_alternatives, dx = r.diagnose_cause, au = r.audit_log;
    switch (intent) {
      case "track": {
        let a = `Shipment ${t.shipment.toUpperCase()} (${t.drug}) is at ${t.temp_c.toFixed(1)} °C with ${t.life_pct}% medicine life left, ${t.location}. The required range is ${sop.storage}.`;
        if (fc.available !== false) a += fc.will_arrive_usable ? ` It is on track: forecast ${fc.life_on_arrival_pct}% life on arrival at ${fc.destination}.`
          : ` It is NOT on track: forecast only ${fc.life_on_arrival_pct}% life on arrival at ${fc.destination}. Ask me where to divert.`;
        return a;
      }
      case "usable": {
        let a = `Right now it is ${VERDICT_TEXT[t.verdict] || t.verdict} (${t.life_pct}% life, ${t.temp_c.toFixed(1)} °C).`;
        if (t.frozen) a += ` Rule: ${sop.source}.`;
        else if (t.temp_c > 8) a += ` It is above ${sop.storage}, so its budget is draining. ${sop.source}.`;
        else if (t.temp_c < 2) a += ` Warning: it is below ${sop.storage} and ${sop.freeze_sensitive ? "close to freezing, which would destroy it. Move it away from the ice packs now" : "cold, but this vaccine type tolerates freezing"}.`;
        else a += ` It is inside ${sop.storage}.`;
        return a;
      }
      case "why": {
        let a = dx.root_cause === "none" ? `Nothing abnormal: ${t.temp_c.toFixed(1)} °C, ${t.life_pct}% life.` : `Root cause: ${dx.explanation}.`;
        if (t.temp_c > 8) a += ` At ${t.temp_c.toFixed(1)} °C the drug ages ${dx.ageing_rate_vs_25c}x faster than at 25 °C (Arrhenius).`;
        if (dx.early_warning !== "none") a += ` Early warning: ${dx.early_warning}.`;
        return a;
      }
      case "divert": {
        if (fc.available === false) return "Diversion applies to live shipments; this is a replay of recorded data.";
        if (fc.will_arrive_usable) return `No diversion needed: ${fc.destination} in ${fc.eta_min} min, arriving with ${fc.life_on_arrival_pct}% life.`;
        if (alt.recommended) return `Divert to ${alt.recommended.name}: ${alt.recommended.eta_min} min away, it arrives there with ${alt.recommended.life_on_arrival_pct}% life. Going on to ${fc.destination} would leave only ${fc.life_on_arrival_pct}%.`;
        return `No hospital can be reached in time with usable life. Get it into the nearest fridge now and flag it for QA.`;
      }
      case "tamper": {
        let a = au.chain_intact ? `The log is intact: ${au.readings_sealed} readings, each sealed with the hash of the one before.` : `TAMPERING DETECTED: reading #${au.tampered_reading} was changed after it was sealed. Everything after it is untrusted.`;
        a += ` Lid openings logged: ${au.lid_openings}. Openings with no logged reason: ${au.unlogged_openings}. Cooling failures: ${au.cooling_failures}. Custody score ${au.custody_score}/100.`;
        return a;
      }
      case "rules":
        return `${sop.drug}: store at ${sop.storage}, ${sop.freeze_sensitive ? "freezing destroys it" : "it tolerates freezing"}, heat budget ${sop.heat_budget_h_at_25c} h at 25 °C. Source: ${sop.source}. It is now at ${t.temp_c.toFixed(1)} °C.`;
      default:
        return "";
    }
  }

  function ask(question, ctx) {
    const intent = INTENTS.find((i) => i.re.test(question));
    if (!intent) {
      return { intent: null, steps: [], answer: "I can answer: Is the shipment on track? Can I still use it? Why is it at risk? Where should we divert? Was the box tampered with? What are the storage rules?" };
    }
    const results = {}, steps = [];
    for (const name of intent.tools) {
      const out = TOOLS[name](ctx);
      results[name] = out;
      steps.push({ tool: name, args: name === "query_telemetry" ? { shipment: ctx.state.ship } : name === "search_compliance_sop" ? { drug: ctx.state.drug } : {}, result: out });
    }
    return { intent: intent.id, steps, answer: compose(intent.id, results) };
  }

  const api = { ask, TOOLS, INTENTS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.VGAgent = api;
})(typeof window !== "undefined" ? window : globalThis);
