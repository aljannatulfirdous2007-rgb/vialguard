// VialGuard engine in the browser ("pocket mode").
// A faithful port of model/vialguard_model.py, model/behavior.py, model/forecast.py,
// simulator/field_trace.py and simulator/simulate.py, so the whole system runs on a
// judge's phone with no server. Python stays the reference implementation; tests in
// tests/engine.test.js check this port against the same numbers.
(function (root) {
  "use strict";

  // ------------------------------------------------------------------ viability model
  const T_REF_K = 298.15, MKT_EA_OVER_R = 10000.0, UPPER = 8.0, FREEZE = 0.0, FREEZE_CONFIRM = 2, WARN = 50, CRIT = 20;
  const PROFILES = {
    demo: { key: "demo", name: "Demo biologic", budget: 48, eaR: 10000, freeze: true },
    insulin: { key: "insulin", name: "Insulin (unopened)", budget: 28 * 24, eaR: 10000, freeze: true },
    vvm7: { key: "vvm7", name: "Vaccine, VVM7 heat class", budget: 45 * 24, eaR: 14340, freeze: true },
    vvm2: { key: "vvm2", name: "Vaccine, VVM2 heat class (freeze-tolerant)", budget: 309, eaR: 14340, freeze: false },
  };
  const rate = (tc, eaR) => Math.exp(-eaR * (1 / (tc + 273.15) - 1 / T_REF_K));
  const valid = (t) => t >= -55 && t <= 84;

  class Tracker {
    constructor(p) { this.p = p; this.used = 0; this.frozen = false; this.below = 0; this.mktSum = 0; this.mktN = 0; }
    get pct() { return Math.max(0, 100 * (1 - this.used / this.p.budget)); }
    update(t, dtH) {
      if (!valid(t)) return;
      this.mktSum += Math.exp(-MKT_EA_OVER_R / (t + 273.15)); this.mktN++;
      if (t > UPPER) this.used += rate(t, this.p.eaR) * dtH;
      if (t <= FREEZE) { this.below++; if (this.p.freeze && this.below >= FREEZE_CONFIRM) this.frozen = true; }
      else this.below = 0;
    }
    hoursLeft(t) {
      if (this.frozen || this.pct <= 0) return 0;
      if (t <= UPPER) return null;
      return (this.p.budget - this.used) / rate(t, this.p.eaR);
    }
    verdict() {
      if (this.frozen) return "DISCARD_FROZEN";
      const p = this.pct;
      if (p <= 0) return "DISCARD_HEAT";
      if (p < CRIT) return "USE_IMMEDIATELY";
      if (p < WARN) return "USE_SOON";
      return "OK";
    }
    mkt() { return this.mktN ? MKT_EA_OVER_R / -Math.log(this.mktSum / this.mktN) - 273.15 : null; }
  }

  // ------------------------------------------------------------------ behaviour detector
  const B = { JUMP_C: 0.9, WIN: 3.0, COOLDOWN: 6.0, SLOPE_WIN: 30.0, SLOPE: 0.04, ABOVE: 1.5 };
  class Detector {
    constructor() { this.hist = []; this.base = null; this.lastAccess = -1e9; this.access = 0; this.cooling = 0; this.unexplained = 0; this.armed = true; }
    update(m, t, lid) {
      const flags = [];
      if (t == null) return flags;
      this.hist.push([m, t]);
      const horizon = Math.max(B.WIN, B.SLOPE_WIN) + 1;
      while (this.hist.length && m - this.hist[0][0] > horizon) this.hist.shift();
      if (this.base === null) this.base = t; else if (t < this.base + 1) this.base += 0.02 * (t - this.base);
      const recent = this.hist.filter(([mm]) => m - mm <= B.WIN && mm < m).map(([, tt]) => tt);
      if (recent.length && t - Math.min(...recent) >= B.JUMP_C && m - this.lastAccess >= B.COOLDOWN) {
        this.lastAccess = m; this.access++;
        if (!lid) this.unexplained++;
        flags.push(lid ? "ACCESS_LOGGED" : "ACCESS_SUSPECTED");
      }
      if (!this.armed && t <= this.base + 0.5) this.armed = true;
      const win = this.hist.filter(([mm]) => m - mm <= B.SLOPE_WIN);
      if (win.length >= 4 && this.armed) {
        const n = win.length, mx = win.reduce((a, [x]) => a + x, 0) / n, my = win.reduce((a, [, y]) => a + y, 0) / n;
        const den = win.reduce((a, [x]) => a + (x - mx) ** 2, 0);
        const slope = den ? win.reduce((a, [x, y]) => a + (x - mx) * (y - my), 0) / den : 0;
        if (slope >= B.SLOPE && t - this.base >= B.ABOVE && win[n - 1][0] - win[0][0] >= B.SLOPE_WIN * 0.6) {
          this.armed = false; this.cooling++; flags.push("COOLING_FAILURE");
        }
      }
      return flags;
    }
    trust() { return Math.max(0, 100 - 12 * this.unexplained - 25 * this.cooling); }
    // early warning: line through the last 4 readings, minutes until it crosses 8 °C or 0 °C
    predictBreach(horizon = 90) {
      const pts = this.hist.slice(-4);
      if (pts.length < 3) return null;
      const tNow = pts[pts.length - 1][1];
      if (!(tNow > 0 && tNow <= 8)) return null;
      const n = pts.length, mx = pts.reduce((a, [x]) => a + x, 0) / n, my = pts.reduce((a, [, y]) => a + y, 0) / n;
      const den = pts.reduce((a, [x]) => a + (x - mx) ** 2, 0);
      if (!den) return null;
      const slope = pts.reduce((a, [x, y]) => a + (x - mx) * (y - my), 0) / den;
      if (Math.abs(slope) < 0.01) return null;
      const [target, kind] = slope > 0 ? [8, "heat"] : [0, "freeze"];
      const minutes = (target - tNow) / slope;
      return minutes > 0 && minutes <= horizon ? { kind, minutes: +minutes.toFixed(1), rate: +(slope * 60).toFixed(2) } : null;
    }
  }
  function rootCause(temp, lid, moving, recent, frozen, excursion) {
    const r = recent.slice(-3);
    if (frozen || temp <= 0) return { code: "OVERCOOLING", text: "Over-cooling: vials touching ice packs or a fridge thermostat set too cold" };
    if (lid && (excursion || r.includes("ACCESS_LOGGED"))) return { code: "DOOR_OPEN", text: "Box or door left open: warm air entering" };
    if (r.includes("ACCESS_SUSPECTED")) return { code: "UNLOGGED_OPENING", text: "Box opened without a logged reason (temperature jump, then recovery)" };
    if (excursion && !moving) return { code: "STATIONARY_HEAT", text: "Vehicle stationary in heat: cold box warming toward outside temperature" };
    if (r.includes("COOLING_FAILURE") || excursion) return { code: "COOLING_FAILURE", text: "Cooling failure: steady climb with no recovery (ice packs spent or unit off)" };
    return { code: null, text: null };
  }

  // ------------------------------------------------------------------ forecast + routing
  const TAU_BOX_H = 4.0, STEP_H = 0.1, ROAD = 1.3, SPEED = 40.0, USABLE = 50;
  const FACILITIES = [
    { id: "DVS", name: "District Vaccine Store, Egmore", lat: 13.0733, lon: 80.2610 },
    { id: "SPK", name: "PHC Sriperumbudur", lat: 12.9675, lon: 79.9419 },
    { id: "CGL", name: "GH Chengalpattu", lat: 12.6921, lon: 79.9707 },
    { id: "KPM", name: "GH Kanchipuram", lat: 12.8342, lon: 79.7036 },
    { id: "CYR", name: "GH Cheyyar", lat: 12.6620, lon: 79.5433 },
    { id: "VDV", name: "GH Vandavasi", lat: 12.5040, lon: 79.6193 },
    { id: "TVM", name: "GH Tiruvannamalai", lat: 12.2253, lon: 79.0747 },
  ];
  const ORIGIN = { name: "District Vaccine Store, Egmore", lat: 13.0733, lon: 80.2610 };
  const DEFAULT_DEST = "TVM";
  const fac = (id) => FACILITIES.find((f) => f.id === id);
  function km(a, b) {
    const r = 6371, rad = Math.PI / 180, p1 = a.lat * rad, p2 = b.lat * rad, dp = p2 - p1, dl = (b.lon - a.lon) * rad;
    const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2 * r * Math.asin(Math.sqrt(h)) * ROAD;
  }
  const etaH = (a, b) => km(a, b) / SPEED;
  function ambientSeries(weather, startEpoch, hours) {
    const n = Math.max(1, Math.ceil(hours / STEP_H));
    if (!weather) return new Array(n).fill(32);
    const off = weather.utc_offset_seconds ?? 19800;
    const epochs = weather.hourly.time.map((t) => Date.parse(t + ":00Z") / 1000 - off);
    const temps = weather.hourly.temperature_2m;
    return Array.from({ length: n }, (_, i) => {
      const t = startEpoch + i * STEP_H * 3600;
      const j = Math.max(0, Math.min(epochs.length - 1, Math.floor((t - epochs[0]) / 3600)));
      return +temps[j];
    });
  }
  function predictPath(t0, hours, amb, coolingOk) {
    const out = []; let t = t0;
    for (let i = 0; i < Math.max(1, Math.ceil(hours / STEP_H)); i++) {
      if (!coolingOk) { const a = amb[Math.min(i, amb.length - 1)]; t += (a - t) * (1 - Math.exp(-STEP_H / TAU_BOX_H)); }
      out.push(t);
    }
    return out;
  }
  function lifeAfter(p, usedH, frozen, path) {
    if (frozen) return 0;
    let used = usedH;
    for (const t of path) {
      if (t <= 0 && p.freeze) return 0;
      if (t > UPPER) used += rate(t, p.eaR) * STEP_H;
    }
    return Math.max(0, 100 * (1 - used / p.budget));
  }
  function plan(p, usedH, frozen, tNow, coolingOk, pos, destId, weather, surroundings) {
    const dest = fac(destId), remaining = etaH(pos, dest);
    let amb = ambientSeries(weather, Date.now() / 1000, Math.max(remaining, 0.25) + 6);
    if (surroundings != null) amb = amb.map((a) => Math.max(a, surroundings));
    const at = (f) => {
      const h = etaH(pos, f);
      return { id: f.id, name: f.name, eta_h: +h.toFixed(2), km: +km(pos, f).toFixed(1),
        life_at_arrival: +lifeAfter(p, usedH, frozen, predictPath(tNow, Math.max(h, STEP_H), amb, coolingOk)).toFixed(1) };
    };
    const target = at(dest), ok = target.life_at_arrival >= USABLE;
    const alts = FACILITIES.filter((f) => f.id !== destId).map(at).sort((a, b) => a.eta_h - b.eta_h);
    let reroute = null;
    if (!ok && !frozen) {
      const viable = alts.filter((a) => a.life_at_arrival >= USABLE);
      reroute = viable.length ? viable.reduce((a, b) => (b.life_at_arrival > a.life_at_arrival ? b : a)) : null;
    }
    let action;
    if (frozen) action = { kind: "discard", text: "Frozen: quarantine and discard. Do not re-route.", order: true };
    else if (ok) action = { kind: "continue", text: `Continue to ${dest.name}. Arrives with ${Math.round(target.life_at_arrival)}% life.`, order: false };
    else if (reroute) action = { kind: "reroute", text: `Divert to ${reroute.name} (${Math.round(reroute.eta_h * 60)} min). It arrives there with ${Math.round(reroute.life_at_arrival)}% life and can be used today.`, order: true };
    else action = { kind: "return", text: "No facility reachable in time. Get it into the nearest fridge now and flag for QA.", order: true };
    return { dest: target, will_survive: ok, action, reroute, alternatives: alts.slice(0, 3), cooling_ok: coolingOk, ambient_now: +amb[0].toFixed(1), assumed_tau_h: TAU_BOX_H };
  }

  // ------------------------------------------------------------------ 72 h reconstructed field trace (see simulator/field_trace.py)
  const FT = { STEP_H: 0.25, TOTAL_H: 72 };
  const SEGMENTS = [[0, 6, "PH_STORE", 4.5], [6, 9, "PH_POWERCUT", 13], [9, 25.5, "PH_STORE", 3.8], [25.5, 28, "PH_TRANSIT", 11],
    [28, 30.5, "PH_DELAY", 21], [30.5, 40, "PH_PHC", 6.5], [40, 44, "PH_PHC", 3], [44, 46.5, "PH_NIGHTFREEZE", -1.8], [46.5, 72, "PH_PHC", 4.2]];
  const TAU = { PH_POWERCUT: 1.2, PH_DELAY: 0.8, PH_TRANSIT: 0.7, PH_NIGHTFREEZE: 0.35 };
  const CHECKS = [1, 8, 25, 32, 49, 56], LIDS = [31, 33.5, 49.25];
  function mulberry32(a) { return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
  function buildFieldTrace() {
    const rnd = mulberry32(7), out = []; let temp = 4.5, prev = null;
    for (let i = 0; i <= FT.TOTAL_H / FT.STEP_H; i++) {
      const h = i * FT.STEP_H, seg = SEGMENTS.find((s) => s[0] <= h && h < s[1]) || SEGMENTS[SEGMENTS.length - 1];
      const tau = TAU[seg[2]] || 1;
      temp += (seg[3] - temp) * (1 - Math.exp(-FT.STEP_H / tau)) + (rnd() * 0.3 - 0.15);
      out.push({ h, t: +temp.toFixed(2), phase: seg[2], newPhase: seg[2] !== prev, check: CHECKS.includes(h), lid: LIDS.includes(h) });
      prev = seg[2];
    }
    return out;
  }

  // ------------------------------------------------------------------ the virtual device
  const ENV = { cold: 5, room: 30, heat: 45, freeze: -6 };
  const SCALE = 360, ESC = [15, 30];
  const now = () => Math.floor(Date.now() / 1000);

  class Device {
    constructor(realWindow) {
      this.realWindow = realWindow || null;
      this.temp = 5; this.env = 5; this.lid = false; this.envMode = "cold";
      this.weather = null; this.weatherSrc = "none";
      this.readings = {};          // ship -> array of sealed records
      this.newShipment("demo", "BOOT");
    }
    outdoor() { try { return +this.weather.current.temperature_2m; } catch { return 31; } }
    newShipment(drug, reason) {
      this.drug = drug; this.tr = new Tracker(PROFILES[drug]);
      this.ship = "s" + now(); this.seq = 0; this.prev = "GENESIS"; this.events = [reason];
      this.readings[this.ship] = [];
      this.excursion = this.w50 = this.w20 = this.discardSent = false;
      this.alertActive = false; this.acked = false; this.level = -1; this.severity = 0; this.reason = ""; this.levelSince = 0;
      this.replay = null; this.paused = false; this.det = new Detector(); this.modelMin = 0; this.lastFlags = [];
      this.prewarn = null; this.breach = null;
      this.trip = { dest: DEFAULT_DEST, done: 0, total: etaH(ORIGIN, fac(DEFAULT_DEST)), start: { ...ORIGIN }, delivered: false, rerouted: false };
      this.realscore = null;
    }
    position() {
      const d = fac(this.trip.dest), k = this.trip.total <= 0 ? 0 : Math.min(1, this.trip.done / this.trip.total), a = this.trip.start;
      return { lat: a.lat + (d.lat - a.lat) * k, lon: a.lon + (d.lon - a.lon) * k };
    }
    reroute(id) {
      if (!fac(id)) return;
      const pos = this.position();
      Object.assign(this.trip, { start: pos, dest: id, done: 0, total: etaH(pos, fac(id)), rerouted: true });
      this.events.push("REROUTED");
    }
    coolingOk() { return this.temp <= UPPER && this.det.cooling === 0; }
    plan() {
      if (this.replay) return null;
      const used = (1 - this.tr.pct / 100) * this.tr.p.budget;
      const pl = plan(this.tr.p, used, this.tr.frozen, this.temp, this.coolingOk(), this.position(), this.trip.dest, this.weather, this.temp > UPPER ? this.temp : null);
      if (this.trip.delivered && ["continue", "reroute", "return"].includes(pl.action.kind)) {
        pl.action = { kind: "delivered", order: !pl.will_survive, text: `Delivered to ${pl.dest.name} with ${Math.round(this.tr.pct)}% life. Verdict: ${this.tr.verdict().replace("_", " ").toLowerCase()}.` };
        pl.reroute = null;
      }
      return pl;
    }
    raise(sev, reason) {
      if (this.alertActive && !this.acked && sev <= this.severity) return;
      Object.assign(this, { alertActive: true, acked: false, severity: sev, reason, level: 0, levelSince: Date.now() / 1000 });
      this.events.push("ALERT_L0");
      if (this.onAlert) this.onAlert(0, reason);
    }
    escalate() {
      if (!this.alertActive || this.acked || this.level >= 2) return;
      const wait = this.level === 0 ? ESC[0] : ESC[1] - ESC[0];
      if (Date.now() / 1000 - this.levelSince < wait) return;
      this.level++; this.levelSince = Date.now() / 1000; this.events.push("ALERT_L" + this.level);
      if (this.onAlert) this.onAlert(this.level, this.reason);
    }
    ack() { if (this.alertActive && !this.acked) { this.acked = true; this.events.push("ACK"); } }

    step(dtReal) {
      if (this.paused) return;
      if (this.replay) return this.replay.kind === "real" ? this.realStep() : this.fieldStep();
      this.temp += (this.env - this.temp) * (1 - Math.exp(-dtReal / 12)) + (Math.random() * 0.08 - 0.04);
      this.evaluate(this.temp, (dtReal / 3600) * SCALE);
    }
    startField() {
      this.newShipment(this.drug, "REPLAY_START");
      const start = Math.floor(now() / 86400) * 86400 - 3 * 86400 + 2.5 * 3600;
      this.replay = { kind: "field", pts: buildFieldTrace(), i: 0, start };
      this.envMode = "replay"; this.lid = false;
    }
    fieldStep() {
      const r = this.replay;
      for (let k = 0; k < 4; k++) {
        if (r.i >= r.pts.length) { this.events.push("REPLAY_END"); this.seal(this.temp, r.start + FT.TOTAL_H * 3600 + 60); this.replay = null; this.paused = true; return; }
        const pt = r.pts[r.i++];
        if (pt.newPhase) this.events.push(pt.phase);
        if (pt.check) this.events.push("LOGBOOK_CHECK");
        if (pt.lid !== this.lid) { this.lid = pt.lid; this.events.push(this.lid ? "LID_OPEN" : "LID_CLOSE"); }
        this.temp = pt.t;
        this.evaluate(pt.t, FT.STEP_H, Math.round(r.start + pt.h * 3600), true, pt.h * 60);
      }
    }
    startReal() {
      if (!this.realWindow) return;
      this.newShipment(this.drug, "REAL_DATA_START");
      const pts = this.realWindow.map(([ts, t, o]) => ({ ts, t, open: !!o }));
      this.replay = { kind: "real", pts, i: 0, start: pts[0].ts };
      this.realscore = { labels: [], hits: [], matched: 0, unconfirmed: 0, missed: 0 };
      this.envMode = "real"; this.lid = false;
    }
    realStep() {
      const r = this.replay, sc = this.realscore;
      for (let k = 0; k < 20; k++) {
        if (r.i >= r.pts.length) { this.events.push("REPLAY_END"); this.seal(this.temp, r.pts[r.pts.length - 1].ts + 60); this.replay = null; this.paused = true; return; }
        const pt = r.pts[r.i++], minute = (pt.ts - r.start) / 60;
        if (pt.open) { sc.labels.push(minute); this.events.push("DOOR_LABEL"); }
        this.temp = pt.t;
        const flags = this.evaluate(pt.t, 1 / 60, pt.ts, r.i % 5 === 0 || pt.open, minute);
        if (flags.some((f) => f.startsWith("ACCESS"))) sc.hits.push(minute);
        const used = new Set(); let m = 0;
        for (const h of sc.hits) {
          const j = sc.labels.findIndex((t, idx) => !used.has(idx) && Math.abs(h - t) <= 4);
          if (j >= 0) { used.add(j); m++; }
        }
        sc.matched = m; sc.unconfirmed = sc.hits.length - m;
        sc.missed = sc.labels.filter((t, idx) => !used.has(idx) && minute - t > 4).length;
      }
    }
    evaluate(t, dtH, ts, record = true, minute = null) {
      const wasFrozen = this.tr.frozen;
      this.tr.update(t, dtH);
      this.modelMin = minute != null ? minute : this.modelMin + dtH * 60;
      const flags = this.det.update(this.modelMin, t, this.lid);
      for (const f of flags) {
        this.events.push(f); this.lastFlags = [...this.lastFlags, f].slice(-5);
        if (f === "ACCESS_SUSPECTED") this.raise(1, "Box opened without a logged reason (temperature signature)");
        else if (f === "COOLING_FAILURE") this.raise(2, "Cooling failure suspected: steady climb with no recovery");
      }
      this.breach = this.det.predictBreach();
      if (this.breach && this.prewarn !== this.breach.kind) {
        this.prewarn = this.breach.kind;
        const m = Math.round(this.breach.minutes);
        if (this.breach.kind === "freeze") { this.events.push("PREDICT_FREEZE"); this.raise(1, `Freeze predicted in ${m} min: move vials away from ice packs now`); }
        else { this.events.push("PREDICT_HEAT"); this.raise(1, `8 C breach predicted in ${m} min: check the cooling now`); }
      } else if (!this.breach && t >= 2 && t <= 8) this.prewarn = null;
      const moving = !["heat", "room"].includes(this.envMode);
      if (!this.replay && !this.trip.delivered && moving) {
        this.trip.done += dtH;
        if (this.trip.done >= this.trip.total) { this.trip.delivered = true; this.events.push("DELIVERED"); }
      }
      if (t > UPPER && !this.excursion) { this.excursion = true; this.events.push("EXCURSION_START"); this.raise(1, `Temperature excursion: ${t.toFixed(1)} C (limit 8 C)`); }
      else if (t <= UPPER && this.excursion) { this.excursion = false; this.events.push("EXCURSION_END"); }
      if (this.tr.frozen && !wasFrozen) { this.events.push("FROZEN"); this.raise(3, `FROZEN at ${t.toFixed(1)} C: irreversible damage. DISCARD.`); }
      const p = this.tr.pct;
      if (p < WARN && !this.w50) { this.w50 = true; this.events.push("WARN_50"); this.raise(1, "Stability budget below 50%. Use soon."); }
      if (p < CRIT && !this.w20) { this.w20 = true; this.events.push("CRIT_20"); this.raise(2, "Stability budget below 20%. Use immediately or discard."); }
      if (p <= 0 && !this.discardSent) { this.discardSent = true; this.events.push("DISCARD"); this.raise(3, "Stability budget exhausted by heat. DISCARD."); }
      if (record) this.seal(t, ts);
      return flags;
    }
    seal(t, ts) {
      const r = { seq: this.seq, ts: ts || now(), t: Math.round(t * 100), b: Math.round(this.tr.pct * 100), lid: this.lid ? 1 : 0, ev: this.events.join(","), prev: this.prev };
      r.h = root.VGChain.recordHash(r);
      this.readings[this.ship].push(r);
      this.prev = r.h; this.events = []; this.seq++;
    }
    command(cmd) {
      if (!cmd) return;
      if (cmd.ack) this.ack();
      if (cmd.replay) this.startField();
      if (cmd.real) this.startReal();
      if (cmd.reroute) this.reroute(cmd.reroute);
      if (cmd.env in ENV && (this.replay || this.paused)) { this.newShipment(this.drug, "NEW_SHIPMENT"); this.temp = 5; }
      if (cmd.env in ENV) { this.envMode = cmd.env; this.env = cmd.env === "room" ? this.outdoor() : ENV[cmd.env]; }
      if ("lid" in cmd && !!cmd.lid !== this.lid && !this.replay) { this.lid = !!cmd.lid; this.events.push(this.lid ? "LID_OPEN" : "LID_CLOSE"); }
      if (cmd.drug in PROFILES) this.newShipment(cmd.drug, "NEW_SHIPMENT");
      else if (cmd.reset) this.newShipment(this.drug, "NEW_SHIPMENT");
    }
    replayProgress() {
      const r = this.replay; if (!r) return null;
      const pt = r.pts[Math.min(r.i, r.pts.length - 1)];
      return r.kind === "real" ? { h: (pt.ts - r.start) / 3600, total: 24, phase: "REAL" } : { h: pt.h, total: FT.TOTAL_H, phase: pt.phase };
    }
    state() {
      const hl = this.tr.hoursLeft(this.temp), d = fac(this.trip.dest), rs = this.realscore;
      return {
        seq: this.seq, ts: now(), temp: +this.temp.toFixed(2), budget: +this.tr.pct.toFixed(2), verdict: this.tr.verdict(),
        drug: this.drug, drug_name: PROFILES[this.drug].name, mkt: this.tr.mkt(), hours_left: hl == null ? -1 : +hl.toFixed(2),
        lid: this.lid ? 1 : 0, excursion: this.excursion ? 1 : 0, frozen: this.tr.frozen ? 1 : 0,
        alert_level: this.alertActive ? this.level : -1, acked: this.acked ? 1 : 0, alert_reason: this.reason,
        scale: SCALE, ship: this.ship, rssi: -55, fw: "sim-browser", env_mode: this.envMode,
        replay: this.replayProgress(), replay_done: this.paused ? 1 : 0, replay_kind: this.replay ? this.replay.kind : null,
        trip: { origin: ORIGIN.name, dest: d.name, dest_id: d.id, done_h: this.trip.done, total_h: this.trip.total, delivered: this.trip.delivered, rerouted: this.trip.rerouted, pos: this.position() },
        forecast: this.plan(),
        behavior: { trust: this.det.trust(), access: this.det.access, unexplained: this.det.unexplained, cooling: this.det.cooling, recent: this.lastFlags },
        realscore: rs ? { labels: rs.labels.length, hits: rs.hits.length, matched: rs.matched, unconfirmed: rs.unconfirmed, missed: rs.missed } : null,
        breach: this.breach,
        cause: rootCause(this.temp, this.lid, !["heat", "room"].includes(this.envMode), this.lastFlags, this.tr.frozen, this.excursion),
        weather: { source: this.weatherSrc, outdoor_c: this.weather ? +this.outdoor().toFixed(1) : null },
      };
    }
  }

  // ------------------------------------------------------------------ in-browser stand-in for the Realtime Database REST API
  class BrowserDB {
    constructor(realWindow) {
      this.dev = new Device(realWindow);
      this.cmd = null;
      this.fetchWeather();
      setInterval(() => this.fetchWeather(), 15 * 60 * 1000);
      let last = performance.now();
      setInterval(() => {
        const t = performance.now(), dt = Math.min(3, (t - last) / 1000); last = t;
        this.dev.escalate();
        this.dev.step(dt);
      }, 1000);
    }
    async fetchWeather() {
      const url = "https://api.open-meteo.com/v1/forecast?latitude=13.08&longitude=80.27&hourly=temperature_2m&current=temperature_2m&forecast_days=2&timezone=Asia%2FKolkata";
      try {
        const r = await fetch(url);
        this.dev.weather = await r.json(); this.dev.weatherSrc = "live";
        if (this.dev.envMode === "room") this.dev.env = this.dev.outdoor();
      } catch { this.dev.weatherSrc = this.dev.weather ? "cache" : "none"; }
    }
    // mirrors: GET/PUT/PATCH /devices/<id>/<path>.json
    handle(method, path, body, query) {
      const parts = path.split("/").filter(Boolean);
      if (parts[0] === "state" && method === "GET") return this.dev.state();
      if (parts[0] === "cmd") {
        if (method === "PATCH" || method === "PUT") { this.dev.command(body); }
        return null;
      }
      if (parts[0] === "shipments" && parts[2] === "readings") {
        const list = this.dev.readings[parts[1]] || [];
        if (parts.length === 3 && method === "GET") {
          const m = /limitToLast=(\d+)/.exec(query || ""); const n = m ? +m[1] : list.length;
          const out = {}; list.slice(-n).forEach((r) => { out[String(r.seq).padStart(8, "0")] = { ...r }; });
          return out;
        }
        if (parts.length === 5 && method === "PUT") {          // tamper: edit one field of one sealed record
          const rec = list.find((r) => String(r.seq).padStart(8, "0") === parts[3]);
          if (rec) rec[parts[4]] = body;
          return body;
        }
      }
      return null;
    }
  }

  const api = { rootCause, PROFILES, Tracker, Detector, rate, plan, predictPath, lifeAfter, etaH, km, FACILITIES, ORIGIN, buildFieldTrace, Device, BrowserDB };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.VGEngine = api;
})(typeof window !== "undefined" ? window : globalThis);
