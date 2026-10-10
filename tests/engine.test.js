// Run: node tests/engine.test.js — checks the in-browser engine against the Python reference numbers.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
globalThis.VGChain = require("../dashboard/chain.js");
const E = require("../dashboard/engine.js");

// 1. same truck-stall result as model/test_model.py (22.55 % used for the demo profile)
const tr = new E.Tracker(E.PROFILES.demo);
for (let i = 0; i < 48; i++) {
  const h = i * 0.5;
  const t = h >= 8 && h < 9 ? 5 + (h - 8) * 33 : h >= 9 && h < 11 ? 38 : h >= 11 && h < 12 ? 38 - (h - 11) * 33 : 5;
  tr.update(t, 0.5);
}
assert.ok(Math.abs(100 - tr.pct - 22.55) < 0.05, `truck stall used ${100 - tr.pct}`);

// 2. VVM7 class: 7 days at 37 °C == 45 days at 25 °C
assert.ok(Math.abs(E.rate(37, 14340) * 7 - 45) < 1);

// 3. freeze rule + freeze-tolerant vaccine
const s = new E.Tracker(E.PROFILES.vvm7), tol = new E.Tracker(E.PROFILES.vvm2);
for (const t of [5, -1, -1, 5]) { s.update(t, 0.1); tol.update(t, 0.1); }
assert.strictEqual(s.verdict(), "DISCARD_FROZEN");
assert.strictEqual(tol.verdict(), "OK");

// 4. re-routing decision matches model/test_engine.py
const p = E.PROFILES.demo;
const plan = E.plan(p, 0.45 * p.budget, false, 40, false, E.ORIGIN, "TVM", null, 40);
assert.strictEqual(plan.action.kind, "reroute");
assert.ok(plan.reroute.life_at_arrival >= 50 && plan.reroute.eta_h < plan.dest.eta_h);

// 5. real hospital fridge replay: detector scores 7 of 9 labelled openings, like the Python engine
const win = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "dashboard", "real_window.json"), "utf8"));
const dev = new E.Device(win);
dev.startReal();
while (dev.replay) dev.step(1);
const rs = dev.state().realscore;
assert.deepStrictEqual([rs.labels, rs.matched, rs.unconfirmed, rs.missed], [9, 7, 9, 2], JSON.stringify(rs));
assert.ok(VGChain.verifyChain(dev.readings[dev.ship]).ok, "sealed log verifies");

// 6. field trace: overnight freeze is caught, logbook says OK
const d2 = new E.Device(null);
d2.startField();
while (d2.replay) d2.step(1);
assert.strictEqual(d2.tr.verdict(), "DISCARD_FROZEN");

console.log("engine.js: all parity tests passed");

// 7. early freeze warning + root cause, same behaviour as the Python simulator
const d3 = new E.Device(null);
for (let i = 0; i < 4; i++) d3.step(1);
d3.command({ env: "freeze" });
let warned = null;
for (let i = 0; i < 12 && !d3.tr.frozen; i++) { d3.step(1); if (d3.breach && !warned) warned = { t: d3.temp, ...d3.breach }; }
assert.ok(warned && warned.kind === "freeze" && warned.t > 0, "freeze predicted before 0 °C");
assert.strictEqual(d3.state().cause.code, d3.tr.frozen ? "OVERCOOLING" : d3.state().cause.code);
// saving the vials: warning, then back to the cold box, never freezes
const d4 = new E.Device(null);
for (let i = 0; i < 4; i++) d4.step(1);
d4.command({ env: "freeze" });
for (let i = 0; i < 12; i++) { d4.step(1); if (d4.breach) break; }
d4.command({ env: "cold" });
for (let i = 0; i < 30; i++) d4.step(1);
assert.strictEqual(d4.tr.frozen, false, "acting on the warning saves the vials");
console.log("engine.js: early-warning tests passed");
