// VialGuard HUD. Polls the Realtime Database REST API (Firebase, or simulator/local_server.py).
"use strict";

const $ = (id) => document.getElementById(id);
const POLL_MS = 2000;
const MAX_POINTS = 600;
const SCALE_MIN = -10, SCALE_MAX = 45;
const LIQUID_FULL_Y = 180, LIQUID_EMPTY_Y = 478;   // vial SVG coordinates
const ARC_LEN = 1558.23;                            // 2 * pi * 248
const THERMO_SEGS = 32, BUDGET_SEGS = 40;
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

// Same profiles as model/vialguard_model.py, used only to explain the maths on screen.
const PROFILES = {
  demo: { budget: 48, eaR: 10000, freeze: true },
  insulin: { budget: 672, eaR: 10000, freeze: true },
  vvm7: { budget: 1080, eaR: 14340, freeze: true },
  vvm2: { budget: 309, eaR: 14340, freeze: false },
};

// ---------------------------------------------------------------- storage (never fatal)
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
function loadConfig() {
  const q = new URLSearchParams(location.search);
  const saved = store.get("vg-config", {});
  const servedByLaptop = location.protocol.startsWith("http") && location.port === "8787";
  return {
    db: (q.get("db") || saved.db || (servedByLaptop ? location.origin : "browser")).replace(/\/$/, ""),
    device: q.get("device") || saved.device || "vg-001",
    auth: q.get("auth") || saved.auth || "",
  };
}
let cfg = loadConfig();

let pocket = null;   // in-browser engine (engine.js) when cfg.db === "browser"
async function startPocket() {
  let win = null;
  try { win = await (await fetch("real_window.json")).json(); } catch { /* real-data replay unavailable */ }
  pocket = new VGEngine.BrowserDB(win);
}
async function api(method, path, body, query = "") {
  if (cfg.db === "browser") {
    if (!pocket) await startPocket();
    return JSON.parse(JSON.stringify(pocket.handle(method, path, body === undefined ? undefined : JSON.parse(JSON.stringify(body)), query) ?? null));
  }
  const params = [cfg.auth && "auth=" + encodeURIComponent(cfg.auth), query].filter(Boolean).join("&");
  const res = await fetch(`${cfg.db}/devices/${encodeURIComponent(cfg.device)}${path}.json${params ? "?" + params : ""}`, {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`DB ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------- helpers
const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const clock = (ts) => (ts ? new Date(ts * 1000).toLocaleTimeString("en-GB", { hour12: false }) : "--:--:--");
function duration(h) {
  if (h >= 48) return `${(h / 24).toFixed(1)} D`;
  if (h >= 1) return `${h.toFixed(1)} H`;
  return `${Math.max(1, Math.round(h * 60))} MIN`;
}
function withAlpha(color, a) {
  const c = document.createElement("canvas").getContext("2d");
  c.fillStyle = color;
  const v = c.fillStyle;
  if (v.startsWith("#")) { const n = parseInt(v.slice(1), 16); return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`; }
  return v.replace(/rgba?\(([^)]+)\)/, (_, x) => `rgba(${x.split(",").slice(0, 3).join(",")},${a})`);
}
const tweens = new WeakMap();
function tween(el, to, decimals) {
  const from = parseFloat(el.textContent) || 0;
  if (reduceMotion || Math.abs(to - from) < Math.pow(10, -decimals)) { el.textContent = to.toFixed(decimals); return; }
  cancelAnimationFrame(tweens.get(el));
  const t0 = performance.now(), dur = 800;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    el.textContent = (from + (to - from) * e).toFixed(decimals);
    if (k < 1) tweens.set(el, requestAnimationFrame(step));
  };
  tweens.set(el, requestAnimationFrame(step));
}

// segmented bars
$("thermo").innerHTML = "<i></i>".repeat(THERMO_SEGS);
$("budgetBar").innerHTML = "<i></i>".repeat(BUDGET_SEGS);
function segTempColor(t) { return t <= 0 ? css("--ice") : t <= 8 ? css("--ok") : t <= 25 ? css("--warn") : css("--bad"); }
function drawThermo(temp) {
  [...$("thermo").children].forEach((seg, i) => {
    const t = SCALE_MIN + ((i + 0.5) * (SCALE_MAX - SCALE_MIN)) / THERMO_SEGS;
    const lit = t <= temp;
    seg.className = lit ? "lit" : "";
    seg.style.color = lit ? segTempColor(t) : "";
  });
}
function drawBudget(pctLeft) {
  const n = Math.round((pctLeft / 100) * BUDGET_SEGS);
  [...$("budgetBar").children].forEach((seg, i) => { seg.className = i < n ? "lit" : ""; });
}

// ---------------------------------------------------------------- copy
const VERDICTS = {
  OK: { cls: "ok", word: "VIABLE", line: "Within stability budget. Cleared to dispense." },
  USE_SOON: { cls: "soon", word: "USE SOON", line: "Over half the stability budget is spent. Dispense first." },
  USE_IMMEDIATELY: { cls: "now", word: "CRITICAL", line: "Under 20% of the stability budget remains." },
  DISCARD_HEAT: { cls: "discard", word: "DISCARD", line: "Heat exhausted the stability budget. Do not administer." },
  DISCARD_FROZEN: { cls: "discard", word: "DISCARD", line: "Freeze event detected. Damage is irreversible. Do not administer." },
};
const EVENTS = {
  BOOT: ["DEVICE ONLINE", ""], NEW_SHIPMENT: ["NEW SHIPMENT", ""],
  EXCURSION_START: ["EXCURSION > 8C", "warn"], EXCURSION_END: ["BACK IN RANGE", "good"],
  FROZEN: ["FREEZE EVENT", "cold"], WARN_50: ["BUDGET 50% USED", "warn"], CRIT_20: ["BUDGET < 20%", "bad"], DISCARD: ["BUDGET EXHAUSTED", "bad"],
  LID_OPEN: ["LID OPENED", ""], LID_CLOSE: ["LID CLOSED", ""],
  ALERT_L0: ["PAGE COURIER", "warn"], ALERT_L1: ["ESCALATE PHARMACIST", "bad"], ALERT_L2: ["ESCALATE QA HEAD", "bad"],
  ACK: ["ACKNOWLEDGED", "good"], SENSOR_ERROR: ["PROBE FAULT", "bad"],
  REPLAY_START: ["FIELD TRACE REPLAY", ""], REPLAY_END: ["REPLAY COMPLETE", ""],
  ACCESS_SUSPECTED: ["UNLOGGED OPENING SUSPECTED", "bad"], ACCESS_LOGGED: ["OPENING (LOGGED)", ""],
  COOLING_FAILURE: ["COOLING FAILURE SIGNATURE", "bad"], REROUTED: ["RE-ROUTED", "good"], DELIVERED: ["DELIVERED", "good"],
  REAL_DATA_START: ["REAL FRIDGE DATA (ZENODO)", ""], PREDICT_FREEZE: ["FREEZE PREDICTED (EARLY WARNING)", "cold"], PREDICT_HEAT: ["BREACH PREDICTED (EARLY WARNING)", "warn"], PREDICT_HEAT_ML: ["XGBOOST: BREACH PREDICTED", "warn"], DOOR_LABEL: ["REAL DOOR OPENED (LABEL)", "cold"],
  PH_STORE: ["AT DISTRICT STORE", ""], PH_POWERCUT: ["POWER CUT AT STORE", "warn"], PH_TRANSIT: ["ON THE ROAD", ""],
  PH_DELAY: ["STUCK IN HEAT", "warn"], PH_PHC: ["IN PHC FRIDGE", ""], PH_NIGHTFREEZE: ["FRIDGE OVER-COOLING", "cold"],
};
const MARKER_EVENTS = new Set(["PREDICT_FREEZE", "PREDICT_HEAT", "ACCESS_SUSPECTED", "DOOR_LABEL", "COOLING_FAILURE", "REROUTED", "LOGBOOK_CHECK", "EXCURSION_START", "EXCURSION_END", "FROZEN", "ALERT_L0", "ALERT_L1", "ALERT_L2", "ACK", "LID_OPEN", "DISCARD"]);

// ---------------------------------------------------------------- telemetry chart
let chart = null, markers = [];
const glowPlugin = {
  id: "glow",
  beforeDatasetDraw(c, args) { const ctx = c.ctx; ctx.save(); ctx.shadowColor = c.data.datasets[args.index].borderColor; ctx.shadowBlur = 12; },
  afterDatasetDraw(c) { c.ctx.restore(); },
};
const safeBand = {
  id: "safeBand",
  beforeDatasetsDraw(c) {
    const { ctx, chartArea: a, scales } = c;
    if (!a || !scales.yT) return;
    const top = scales.yT.getPixelForValue(8), bottom = scales.yT.getPixelForValue(2);
    ctx.save();
    ctx.fillStyle = withAlpha(css("--ok"), 0.1); ctx.fillRect(a.left, top, a.right - a.left, bottom - top);
    ctx.strokeStyle = withAlpha(css("--ok"), 0.45); ctx.setLineDash([4, 4]);
    [top, bottom].forEach((y) => { ctx.beginPath(); ctx.moveTo(a.left, y); ctx.lineTo(a.right, y); ctx.stroke(); });
    ctx.restore();
  },
};
const markerPlugin = {
  id: "eventMarkers",
  afterDatasetsDraw(c) {
    const { ctx, chartArea: a, scales } = c;
    if (!a) return;
    ctx.save();
    for (const m of markers) {
      const x = scales.x.getPixelForValue(m.index);
      if (x < a.left || x > a.right) continue;
      const color = { bad: css("--bad"), warn: css("--warn"), good: css("--ok"), cold: css("--ice") }[m.kind] || css("--hud");
      ctx.strokeStyle = withAlpha(color, 0.55); ctx.setLineDash([2, 4]);
      ctx.beginPath(); ctx.moveTo(x, a.top + 12); ctx.lineTo(x, a.bottom); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = color; ctx.shadowColor = color; ctx.shadowBlur = 10;
      ctx.beginPath(); ctx.moveTo(x, a.top + 1); ctx.lineTo(x + 6, a.top + 7); ctx.lineTo(x, a.top + 13); ctx.lineTo(x - 6, a.top + 7); ctx.closePath(); ctx.fill();
      ctx.shadowBlur = 0;
    }
    ctx.restore();
  },
};
function buildChart() {
  const tick = css("--muted"), grid = withAlpha(css("--hud"), 0.08);
  chart = new Chart($("chart"), {
    type: "line",
    data: { labels: [], datasets: [
      { label: "TEMP", data: [], yAxisID: "yT", borderColor: css("--hud"), borderWidth: 2, pointRadius: 0, tension: 0.35 },
      { label: "LIFE", data: [], yAxisID: "yL", borderColor: css("--v"), borderWidth: 2.5, pointRadius: 0, tension: 0.2,
        fill: "origin", backgroundColor: (ctx) => {
          const a = ctx.chart.chartArea; if (!a) return "transparent";
          const g = ctx.chart.ctx.createLinearGradient(0, a.top, 0, a.bottom);
          g.addColorStop(0, withAlpha(css("--v"), 0.09)); g.addColorStop(1, withAlpha(css("--v"), 0)); return g;
        } },
    ] },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
      layout: { padding: { top: 6 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: css("--solid"), borderColor: css("--hud"), borderWidth: 1, titleColor: css("--hud"), bodyColor: css("--text"),
          padding: 10, cornerRadius: 0, displayColors: false, titleFont: { family: "JetBrains Mono" }, bodyFont: { family: "JetBrains Mono" },
          callbacks: { label: (i) => (i.datasetIndex === 0 ? `TEMP ${i.parsed.y.toFixed(1)} C` : `LIFE ${i.parsed.y.toFixed(1)} %`) },
        },
      },
      scales: {
        x: { grid: { color: grid }, border: { display: false }, ticks: { color: tick, maxTicksLimit: innerWidth < 600 ? 3 : 9, maxRotation: 0, font: { family: "JetBrains Mono", size: 10 } } },
        yT: { position: "left", suggestedMin: -5, suggestedMax: 40, grid: { color: grid }, border: { display: false },
              ticks: { color: tick, font: { family: "JetBrains Mono", size: 10 }, callback: (v) => `${v}°` } },
        yL: { position: "right", min: 0, max: 100, grid: { display: false }, border: { display: false },
              ticks: { color: tick, font: { family: "JetBrains Mono", size: 10 }, stepSize: 25, callback: (v) => `${v}%` } },
      },
    },
    plugins: [safeBand, glowPlugin, markerPlugin],
  });
}

function drawSpark(records) {
  const canvas = $("spark"), box = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1, w = box.clientWidth, h = box.clientHeight;
  if (!w || !h) return;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d"); ctx.scale(dpr, dpr);
  const pts = records.filter((r) => r.t > -12000).slice(-90).map((r) => r.t / 100);
  if (pts.length < 2) return;
  const lo = Math.min(...pts, 0) - 2, hi = Math.max(...pts, 10) + 2;
  const y = (v) => h - 3 - ((v - lo) / (hi - lo)) * (h - 6), x = (i) => (i / (pts.length - 1)) * w;
  const hud = css("--hud");
  ctx.fillStyle = withAlpha(css("--ok"), 0.12); ctx.fillRect(0, y(8), w, y(2) - y(8));
  ctx.strokeStyle = withAlpha(hud, 0.12); ctx.lineWidth = 1;
  for (let gx = 0; gx < w; gx += 16) { ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, h); ctx.stroke(); }
  ctx.beginPath(); pts.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.strokeStyle = hud; ctx.lineWidth = 1.8; ctx.shadowColor = hud; ctx.shadowBlur = 8; ctx.stroke(); ctx.shadowBlur = 0;
  ctx.fillStyle = hud; ctx.beginPath(); ctx.arc(x(pts.length - 1), y(pts[pts.length - 1]), 3, 0, Math.PI * 2); ctx.fill();
}

// ---------------------------------------------------------------- render: state
let currentVerdictCls = "idle";
function renderState(s) {
  const v = VERDICTS[s.verdict] || { cls: "idle", word: s.verdict, line: "" };
  const body = document.body;
  body.dataset.verdict = v.cls;
  body.dataset.frozen = s.frozen ? "1" : "0";
  body.classList.toggle("hot", s.temp > 8 && !s.frozen);
  if (v.cls !== currentVerdictCls) { currentVerdictCls = v.cls; chart.data.datasets[0].borderColor = css("--hud"); }

  const demo = s.scale && s.scale !== 1;
  $("demo").hidden = !demo;
  $("demo").querySelector("span").textContent = `${s.scale}X TIME`;
  $("demo").title = demo ? `Demo mode: 1 real second counts as ${Math.round(s.scale / 60)} minutes of transit.` : "";
  document.querySelectorAll("#drugSeg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.drug === s.drug)));

  // a frozen or exhausted drug has no usable life left, whatever heat budget remains
  const life = s.verdict.startsWith("DISCARD") ? 0 : clamp(s.budget, 0, 100);
  tween($("budget"), life, life > 0 && life < 10 ? 1 : 0);
  $("arc").style.strokeDashoffset = ARC_LEN * (1 - life / 100);
  const level = s.frozen ? clamp(s.budget, 0, 100) : life;   // frozen: liquid stays but turns to ice
  const y = LIQUID_EMPTY_Y - (level / 100) * (LIQUID_EMPTY_Y - LIQUID_FULL_Y);
  $("liquidMove").style.transform = `translateY(${y + (level === 0 ? 8 : 0)}px)`;
  $("levelTag").textContent = s.frozen ? "▸ STATE FROZEN" : `▸ LIFE ${life.toFixed(life < 10 && life > 0 ? 1 : 0)}%`;
  $("drugName").textContent = (s.drug_name || s.drug).toUpperCase();

  $("verdict").textContent = v.word;
  $("verdictSub").textContent = v.line;
  $("stampText").textContent = s.frozen ? "FROZEN" : "DISCARD";
  $("alarmText").textContent = s.frozen ? "FREEZE EVENT // DISCARD // DO NOT ADMINISTER" : "STABILITY BUDGET EXHAUSTED // DISCARD";

  tween($("temp"), s.temp, 1);
  drawThermo(s.temp);
  const ts = $("tempState");
  if (s.temp <= 0) { ts.className = "mono cold"; ts.textContent = "FREEZING"; }
  else if (s.frozen) { ts.className = "mono cold"; ts.textContent = "FROZE EARLIER"; }
  else if (s.excursion) { ts.className = "mono hot"; ts.textContent = "EXCURSION > 8 °C"; }
  else { ts.className = "mono ok"; ts.textContent = "IN RANGE"; }

  if (s.verdict.startsWith("DISCARD")) $("hoursLeft").textContent = "NONE";
  else if (s.hours_left < 0) $("hoursLeft").textContent = "UNLIMITED @ 2-8 °C";
  else $("hoursLeft").textContent = `${duration(s.hours_left)} @ ${s.temp.toFixed(0)} °C`;
  $("mkt").textContent = s.mkt == null ? "--" : `${Number(s.mkt).toFixed(1)} °C`;
  $("lid").innerHTML = s.lid ? '<i class="ph ph-lock-simple-open"></i>OPEN' : '<i class="ph ph-lock-simple"></i>SEALED';
  $("ship").textContent = (s.ship || "--").toUpperCase();
  $("devLabel").textContent = cfg.device.toUpperCase();
  $("seq").textContent = s.seq ?? "--";
  $("rssi").textContent = s.rssi ? `${s.rssi} DBM` : "--";

  const firing = s.alert_level >= 0 && !s.acked;
  $("alertState").textContent = s.alert_level < 0 ? "IDLE" : s.acked ? "ACKNOWLEDGED" : ["PAGING COURIER", "ESCALATED L2", "ESCALATED L3"][s.alert_level];
  $("alertReason").textContent = s.alert_level < 0 ? "Escalates courier to pharmacist to QA head when nobody responds." : s.alert_reason;
  document.querySelectorAll("#ladder li").forEach((li) => {
    const l = Number(li.dataset.l);
    li.className = s.alert_level >= l ? (s.acked ? "done" : l === s.alert_level ? "on current" : "on") : "";
  });
  $("ackBtn").disabled = !firing;

  renderCore(s, life);
  renderControls(s);
  renderPredict(s);
  renderWarnings(s);
  agentState = s;
  initMap();
  renderMap(s);
}

function renderCore(s, life) {
  const p = PROFILES[s.drug] || PROFILES.demo;
  const TK = s.temp + 273.15;
  const k = Math.exp(-p.eaR * (1 / TK - 1 / 298.15));
  $("eqSub").textContent = `> e^(-${p.eaR} × (1/${TK.toFixed(1)} - 1/298.2)) = ${k.toFixed(k < 1 ? 3 : 2)}`;
  $("kNow").textContent = `${k < 10 ? k.toFixed(2) : k.toFixed(1)}×`;
  $("kNote").textContent = s.temp <= 8 ? "PAUSED: INSIDE 2-8 °C" : `1 H = ${k.toFixed(1)} H OF BUDGET`;
  const used = (1 - clamp(s.budget, 0, 100) / 100) * p.budget;
  $("usedH").textContent = `${used < 10 ? used.toFixed(1) : used.toFixed(0)} H`;
  $("usedNote").textContent = `OF ${p.budget} H @ 25 °C`;
  $("freezeRule").textContent = !p.freeze ? "TOLERANT" : s.frozen ? "TRIPPED" : "ARMED";
  $("freezeNote").textContent = p.freeze ? "2 READINGS ≤ 0 °C = DISCARD" : "SURVIVES FREEZING";
  drawBudget(life);
}

// ---------------------------------------------------------------- render: readings
let seen = new Set(), lastShip = null;
function renderReadings(records, ship) {
  if (ship !== lastShip) { seen = new Set(); lastShip = ship; }
  const pts = records.filter((r) => r.t > -12000).slice(-MAX_POINTS);
  const index = new Map(pts.map((r, i) => [r.seq, i]));
  const multiDay = pts.length > 1 && pts[pts.length - 1].ts - pts[0].ts > 20 * 3600;
  chart.data.labels = pts.map((r) => (multiDay ? new Date(r.ts * 1000).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }) : clock(r.ts)));
  chart.data.datasets[0].data = pts.map((r) => r.t / 100);
  chart.data.datasets[1].data = pts.map((r) => r.b / 100);
  chart.data.datasets[0].borderColor = css("--hud");
  chart.data.datasets[1].borderColor = css("--v");
  markers = [];
  const lines = [], alertTimes = {};
  for (const r of records) {
    if (!r.ev) continue;
    for (const e of r.ev.split(",")) {
      let [label, kind] = EVENTS[e] || [e, ""];
      if (e === "LOGBOOK_CHECK") {
        const tc = r.t / 100, inRange = tc >= 2 && tc <= 8;
        label = `MANUAL LOGBOOK ${tc.toFixed(1)}C: ${inRange ? "LOOKS OK" : "OUT OF RANGE"}`;
        kind = inRange ? "logok" : "warn";
      }
      if (MARKER_EVENTS.has(e) && index.has(r.seq)) markers.push({ index: index.get(r.seq), kind });
      if (e === "ALERT_L0") for (const key in alertTimes) delete alertTimes[key];   // a new alert restarts the ladder
      if (e.startsWith("ALERT_L")) alertTimes[e.slice(-1)] = r.ts;
      lines.push({ key: `${r.seq}:${e}`, label, kind, r });
    }
  }
  chart.update();
  drawSpark(records);

  document.querySelectorAll("#ladder li").forEach((li) => {
    const t = alertTimes[li.dataset.l];
    li.querySelector(".when").textContent = t ? clock(t) : ["T+0", "IF NO ACK", "FINAL"][li.dataset.l];
  });

  $("feedEmpty").hidden = lines.length > 0;
  $("feed").innerHTML = lines.reverse().map((l) => {
    const fresh = seen.size && !seen.has(l.key) ? ' class="fresh"' : "";
    return `<li${fresh}><span class="t">${clock(l.r.ts)}</span><span class="e ${l.kind}">${l.label}</span>` +
      `<span class="v">${(l.r.t / 100).toFixed(1)}C ${Math.round(l.r.b / 100)}%</span>` +
      `<span class="h">#${String(l.r.seq).padStart(4, "0")} 0x${l.r.h.slice(0, 16)}</span></li>`;
  }).join("");
  lines.forEach((l) => seen.add(l.key));

  lastRecords = records;
  agentRecords = records;
  if (!records.length) return;
  const v = VGChain.verifyChain(records);
  $("receipt").className = `panel ledger ${v.ok ? "ok" : "bad"}`;
  const cs = $("chainStatus");
  cs.className = `mono ${v.ok ? "ok" : "bad"}`;
  cs.textContent = v.ok ? `INTEGRITY OK // ${v.checked} VERIFIED` : `BREACH @ #${v.brokenSeq}`;
  $("chainCount").textContent = records.length;
  const h = records[records.length - 1].h;
  $("lastHash").textContent = `0x${h.slice(0, 12)}…${h.slice(-6)}`;
  $("packedAt").textContent = records[0].seq === 0 ? clock(records[0].ts) : "EARLIER";
}

// ---------------------------------------------------------------- polling
let busy = false;
async function poll() {
  if (busy || !cfg.db) return;
  busy = true;
  const conn = $("conn"), label = conn.querySelector("b");
  try {
    const s = await api("GET", "/state");
    if (!s) throw new Error(`WAITING ${cfg.device.toUpperCase()}`);
    renderState(s);
    const data = await api("GET", `/shipments/${s.ship}/readings`, undefined, 'orderBy="$key"&limitToLast=' + MAX_POINTS);
    renderReadings(data ? Object.keys(data).sort().map((k) => data[k]) : [], s.ship);
    const age = s.ts ? Date.now() / 1000 - s.ts : 0;
    conn.className = age > 15 ? "rec err" : "rec live";
    label.textContent = age > 15 ? `SILENT ${Math.round(age)}S` : "LIVE";
  } catch (e) {
    conn.className = "rec err";
    label.textContent = e.message === "Failed to fetch" ? "NO LINK" : e.message;
  } finally {
    busy = false;
  }
}

// ---------------------------------------------------------------- actions
async function sendCmd(cmd) {
  try { await api("PATCH", "/cmd", cmd); setTimeout(poll, 600); }
  catch { $("conn").className = "rec err"; $("conn").querySelector("b").textContent = "CMD FAILED"; }
}
function acknowledge() { if (!$("ackBtn").disabled) { $("ackBtn").disabled = true; sendCmd({ ack: true }); } }
$("ackBtn").addEventListener("click", acknowledge);
document.addEventListener("keydown", (e) => {
  if ((e.key === "a" || e.key === "A") && !e.metaKey && !e.ctrlKey && !(e.target instanceof HTMLInputElement)) acknowledge();
});
$("drugSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-drug]");
  if (b && b.getAttribute("aria-pressed") !== "true") sendCmd({ drug: b.dataset.drug });
});
$("newShip").addEventListener("click", () => sendCmd({ reset: true }));

// ---------------------------------------------------------------- predict & act
function renderPredict(s) {
  const tr = s.trip, fc = s.forecast, bh = s.behavior || {}, wx = s.weather || {};
  $("wxSrc").textContent = wx.outdoor_c != null ? `OUTSIDE ${wx.outdoor_c} °C ${wx.source === "live" ? "LIVE" : "CACHED"}` : "WEATHER OFFLINE";
  $("wxSrc").title = "Real outdoor temperature for the route, from Open-Meteo";
  $("leftOutSub").textContent = wx.outdoor_c != null ? `${wx.outdoor_c} °C outdoors now (live)` : "outdoor temperature";
  const real = s.replay_kind === "real" || (s.realscore && !s.replay_kind && s.replay_done);
  if (tr && fc && !s.replay_kind && !s.replay_done) {
    $("fcUnit").textContent = "%"; $("fcLabel").innerHTML = "predicted medicine life<br>on arrival";
    const k = tr.total_h ? Math.min(1, tr.done_h / tr.total_h) : 1;
    $("rtFrom").textContent = tr.rerouted ? "DIVERTED" : tr.origin.toUpperCase();
    $("rtTo").textContent = tr.dest.toUpperCase();
    $("rtBar").style.width = `${k * 100}%`;
    $("rtDot").style.left = `${k * 100}%`;
    const left = Math.max(0, tr.total_h - tr.done_h);
    $("rtEta").textContent = tr.delivered ? "DELIVERED" : (s.env_mode === "heat" || s.env_mode === "room") ? `STOPPED // ${Math.round(left * 60)} MIN LEFT` : `ETA ${Math.round(left * 60)} MIN`;
    $("rtKm").textContent = `${fc.dest.km} KM`;
    const life = fc.dest.life_at_arrival;
    $("fcLife").textContent = tr.delivered ? Math.round(s.budget) : Math.round(life);
    document.querySelector(".fc").style.setProperty("--fcv", life >= 50 ? css("--ok") : life > 0 ? css("--warn") : css("--bad"));
    $("fcNote").textContent = fc.cooling_ok
      ? "Cold box holding. Forecast assumes it keeps holding until arrival."
      : `Cooling compromised. Box modelled warming toward ${fc.ambient_now} °C (probe trend and live outdoor forecast, Newton cooling, tau ${fc.assumed_tau_h} h).`;
    const a = fc.action, act = document.querySelector(".act");
    act.className = `panel act kind-${a.kind}`;
    $("actKind").textContent = a.kind.toUpperCase();
    $("actText").textContent = a.text;
    const rb = $("rerouteBtn");
    rb.hidden = !fc.reroute;
    if (fc.reroute) { rb.dataset.id = fc.reroute.id; $("rerouteLabel").textContent = `DIVERT TO ${fc.reroute.name.toUpperCase()}`; }
    $("alts").innerHTML = (fc.alternatives || []).map((f) =>
      `<div><b>${f.name}</b><span>${Math.round(f.eta_h * 60)} min</span><span class="${f.life_at_arrival >= 50 ? "ok" : "no"}">${Math.round(f.life_at_arrival)}%</span></div>`).join("");
    const ord = $("order");
    ord.hidden = !a.order;
    if (a.order) ord.textContent = `AUTO-DRAFTED REPLACEMENT ORDER\nTo: ${tr.dest}\nItem: ${s.drug_name} x 1 shipment\nReason: ${a.kind === "discard" ? "frozen in transit" : `predicted arrival life ${Math.round(fc.dest.life_at_arrival)}% (below 50%)`}, shipment ${s.ship.toUpperCase()}\nStatus: awaiting pharmacist approval`;
  } else {
    $("rtFrom").textContent = s.realscore ? "HOSPITAL FRIDGE" : "FIELD TRACE"; $("rtTo").textContent = s.replay_done ? "REPLAY DONE" : "REPLAY";
    $("rtBar").style.width = "0"; $("rtEta").textContent = "--"; $("rtKm").textContent = "--";
    if (s.ml) {
      $("fcLife").textContent = s.ml.temp.toFixed(1); $("fcUnit").textContent = "°C";
      $("fcLabel").innerHTML = "XGBoost forecast<br>10 min ahead";
      document.querySelector(".fc").style.setProperty("--fcv", s.ml.temp > 8 ? css("--bad") : css("--ok"));
      $("fcNote").textContent = "Trained on 8,316 real fridge readings. On 2,773 unseen ones: 0.14 °C average error, 56% better than assuming no change, zero false alarms.";
    } else {
      $("fcLife").textContent = "--";
      $("fcNote").textContent = "Arrival forecast applies to live shipments.";
    }
    document.querySelector(".act").className = "panel act";
    $("actKind").textContent = "REPLAY"; $("actText").textContent = s.replay_done ? "Replay finished. Press a SIMULATION CONTROL button or NEW SHIPMENT to start a live shipment." : "Replaying recorded data. Watch the custody panel and the seal log.";
    $("rerouteBtn").hidden = true; $("alts").innerHTML = ""; $("order").hidden = true;
  }
  const trust = bh.trust ?? 100;
  $("trust").textContent = trust;
  document.querySelector(".integ").style.setProperty("--tv", trust >= 75 ? css("--ok") : trust >= 40 ? css("--warn") : css("--bad"));
  $("inLogged").textContent = Math.max(0, (bh.access || 0) - (bh.unexplained || 0));
  $("inUnlogged").textContent = bh.unexplained || 0;
  $("inCooling").textContent = bh.cooling || 0;
  const rs = s.realscore;
  $("realScore").hidden = !rs;
  $("integMode").textContent = rs ? "REAL DATA TEST" : "LIVE SHIPMENT";
  document.querySelector(".trust").style.display = rs ? "none" : "";
  $("integKv").style.display = rs ? "none" : "";
  if (rs) { $("rsLabels").textContent = rs.labels; $("rsMatched").textContent = rs.matched; $("rsUnconf").textContent = rs.unconfirmed; $("rsMissed").textContent = rs.missed; }
  $("realBtn").setAttribute("aria-pressed", String(s.replay_kind === "real"));
}

// ---------------------------------------------------------------- live route map (Leaflet + OpenStreetMap tiles)
const FAC = [
  ["DVS", "District Vaccine Store, Egmore", 13.0733, 80.2610], ["SPK", "PHC Sriperumbudur", 12.9675, 79.9419],
  ["CGL", "GH Chengalpattu", 12.6921, 79.9707], ["KPM", "GH Kanchipuram", 12.8342, 79.7036], ["CYR", "GH Cheyyar", 12.6620, 79.5433],
  ["VDV", "GH Vandavasi", 12.5040, 79.6193], ["TVM", "GH Tiruvannamalai", 12.2253, 79.0747],
];
let map = null, mapLayers = null, truck = null;
function initMap() {
  if (map || typeof L === "undefined") { if (typeof L === "undefined") $("mapPanel").hidden = true; return; }
  map = L.map("map", { zoomControl: true, attributionControl: true, scrollWheelZoom: false }).setView([12.7, 79.7], 9);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 14, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  FAC.forEach(([id, name, lat, lon]) => L.circleMarker([lat, lon], { radius: 6, color: css("--hud"), weight: 2, fillColor: "#fff", fillOpacity: 1 })
    .bindTooltip(name, { direction: "top" }).addTo(map));
  mapLayers = L.layerGroup().addTo(map);
  truck = L.marker([13.0733, 80.2610], { icon: L.divIcon({ className: "", html: '<div class="vg-truck"><i class="ph-fill ph-truck"></i></div>', iconSize: [34, 34], iconAnchor: [17, 17] }) }).addTo(map);
  map.fitBounds(FAC.map((f) => [f[2], f[3]]), { padding: [24, 24] });
}
function renderMap(s) {
  if (!map || !s.trip || !s.trip.pos) return;
  if (!map._vgFitted) { map.invalidateSize(); map.fitBounds(FAC.map((f) => [f[2], f[3]]), { padding: [30, 30] }); map._vgFitted = true; }
  const pos = [s.trip.pos.lat, s.trip.pos.lon];
  const dest = FAC.find((f) => f[0] === s.trip.dest_id);
  mapLayers.clearLayers();
  if (dest) L.polyline([pos, [dest[2], dest[3]]], { color: css("--hud"), weight: 4, opacity: .85 }).addTo(mapLayers);
  const rr = s.forecast && s.forecast.reroute && FAC.find((f) => f[0] === s.forecast.reroute.id);
  if (rr) L.polyline([pos, [rr[2], rr[3]]], { color: css("--warn"), weight: 4, dashArray: "8 8" }).addTo(mapLayers)
    .bindTooltip(`Divert: ${rr[1]}`, { permanent: true, direction: "center", className: "" });
  truck.setLatLng(pos);
  truck.getElement()?.querySelector(".vg-truck")?.classList.toggle("warn", !!(s.forecast && !s.forecast.will_survive));
}
function renderWarnings(s) {
  const b = s.breach, w = $("breachWarn");
  w.hidden = !b;
  if (b) {
    w.className = `breach-warn ${b.kind}`;
    $("breachTitle").textContent = b.kind === "freeze" ? `FREEZE PREDICTED IN ${Math.round(b.minutes)} MIN` : `8 °C BREACH IN ${Math.round(b.minutes)} MIN`;
    $("breachSub").textContent = b.kind === "freeze" ? "Act now: move vials off the ice packs (try COLD BOX)" : `Warming ${b.rate} °C per hour. Check the cooling now.`;
  }
  const c = s.cause || {};
  $("cause").hidden = !c.code;
  if (c.code) $("causeText").textContent = c.text;
}


// ---------------------------------------------------------------- Ask VialGuard
let agentState = null, agentRecords = [];
function askAgent(q) {
  q = q.trim();
  if (!q || !agentState) return;
  const out = VGAgent.ask(q, { state: agentState, records: agentRecords });
  const add = (cls, text) => { const d = document.createElement("div"); d.className = `msg ${cls}`; d.textContent = text; $("msgs").appendChild(d); };
  add("user", q);
  add("bot", out.answer);
  $("msgs").scrollTop = $("msgs").scrollHeight;
  const esc = (x) => String(x).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const items = [`<li><span class="who">OPERATOR</span><pre>"${esc(q)}"</pre></li>`,
    `<li><span class="who">AGENT</span><pre>intent: ${out.intent || "unknown"}</pre></li>`];
  out.steps.forEach((s) => items.push(`<li><span class="who">TOOL ${esc(s.tool)}(${esc(JSON.stringify(s.args))})</span><pre>${esc(JSON.stringify(s.result, null, 1))}</pre></li>`));
  items.push(`<li><span class="who">ANSWER</span><pre>${esc(out.answer)}</pre></li>`);
  $("trace").innerHTML = items.join("");
}
$("chips").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) askAgent(b.textContent); });
$("askForm").addEventListener("submit", (e) => { e.preventDefault(); askAgent($("askInput").value); $("askInput").value = ""; });

$("rerouteBtn").addEventListener("click", () => { const id = $("rerouteBtn").dataset.id; if (id) sendCmd({ reroute: id }); });
$("realBtn").addEventListener("click", () => sendCmd({ real: true }));


// ---------------------------------------------------------------- simulation control panel
let lidOpen = false, lastShipId = null, lastRecords = [];
document.querySelectorAll(".ctl[data-env]").forEach((b) => b.addEventListener("click", () => sendCmd({ env: b.dataset.env })));
$("lidBtn").addEventListener("click", () => sendCmd({ lid: !lidOpen }));
$("replayBtn").addEventListener("click", () => sendCmd({ replay: true }));
// Edits a sealed past reading straight in the database, the way a dishonest courier would.
// The hash chain no longer matches, so the seal log raises CHAIN BREACH.
$("tamperBtn").addEventListener("click", async () => {
  const target = [...lastRecords].reverse().find((r) => r.t > 800) || lastRecords[Math.floor(lastRecords.length / 2)];
  if (!target || !lastShipId) return;
  try {
    await api("PUT", `/shipments/${lastShipId}/readings/${String(target.seq).padStart(8, "0")}/t`, 500);
    setTimeout(poll, 300);
  } catch { $("conn").querySelector("b").textContent = "TAMPER FAILED"; }
});
function renderControls(s) {
  lidOpen = !!s.lid; lastShipId = s.ship;
  const sim = typeof s.fw === "string" && s.fw.startsWith("sim");
  document.querySelectorAll(".ctl[data-env]").forEach((b) => { b.setAttribute("aria-pressed", String(b.dataset.env === s.env_mode)); b.disabled = !sim; });
  $("lidBtn").disabled = !sim || !!s.replay;
  const rb = $("replayBtn");
  rb.disabled = !sim;
  $("realBtn").disabled = !sim;
  rb.setAttribute("aria-pressed", String(!!s.replay));
  const PH = { PH_STORE: "district store", PH_POWERCUT: "power cut", PH_TRANSIT: "on the road", PH_DELAY: "stuck in heat", PH_PHC: "PHC fridge", PH_NIGHTFREEZE: "fridge over-cooling" };
  if (s.replay) {
    $("replaySub").textContent = `hour ${Math.floor(s.replay.h)} of ${s.replay.total}: ${PH[s.replay.phase] || ""}`;
    let bar = rb.querySelector(".bar-fill"); if (!bar) { bar = document.createElement("i"); bar.className = "bar-fill"; rb.appendChild(bar); }
    bar.style.width = `${(100 * s.replay.h) / s.replay.total}%`;
  } else {
    $("replaySub").textContent = s.replay_done ? "done: compare the logbook lines" : "store, power cut, road, PHC fridge";
    rb.querySelector(".bar-fill")?.remove();
  }
  $("lidBtn").querySelector("b").textContent = lidOpen ? "CLOSE LID" : "OPEN LID";
  $("ctlHint").textContent = !sim ? "REAL DEVICE CONNECTED: CONTROLS OFF" : s.replay ? "REPLAYING A RECONSTRUCTED 72 H LAST-MILE TRACE (15 MIN LOGGER INTERVAL)" : "CLICK TO CHANGE WHAT THE PACKAGE EXPERIENCES";
}
$("settingsBtn").addEventListener("click", () => {
  $("dbUrl").value = cfg.db; $("devId").value = cfg.device; $("dbAuth").value = cfg.auth;
  $("settings").showModal();
});
$("saveSettings").addEventListener("click", () => {
  cfg = { db: $("dbUrl").value.trim().replace(/\/$/, ""), device: $("devId").value.trim() || "vg-001", auth: $("dbAuth").value.trim() };
  store.set("vg-config", cfg);
  poll();
});

function setThemeIcon() {
  $("themeBtn").innerHTML = `<i class="ph ${document.documentElement.dataset.theme === "dark" ? "ph-sun" : "ph-moon"}"></i>`;
}
$("themeBtn").addEventListener("click", () => {
  const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("vg-hud-theme", t); } catch { /* ignore */ }
  setThemeIcon();
  chart.destroy(); buildChart(); poll();
});
setThemeIcon();

// ---------------------------------------------------------------- boot
const tickClock = () => { $("clock").textContent = new Date().toLocaleTimeString("en-GB", { hour12: false }); };
tickClock(); setInterval(tickClock, 1000);
buildChart();
if (!cfg.db) $("settings").showModal();
// deep links from the landing page: ?start=heat | freeze | real | field
(function autoStart() {
  const st = new URLSearchParams(location.search).get("start");
  const cmd = { heat: { env: "heat" }, freeze: { env: "freeze" }, real: { real: true }, field: { replay: true } }[st];
  if (cmd) setTimeout(() => { sendCmd(cmd); document.querySelector(".control")?.scrollIntoView({ behavior: "smooth", block: "start" }); }, 1500);
})();
if (cfg.db === "browser") document.body.classList.add("pocket");
poll();
setInterval(poll, POLL_MS);
