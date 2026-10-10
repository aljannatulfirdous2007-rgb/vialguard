// Run: node tests/agent.test.js — the assistant must answer only from tool results.
const assert = require("assert");
globalThis.VGChain = require("../dashboard/chain.js");
const E = require("../dashboard/engine.js");
const A = require("../dashboard/agent.js");

const dev = new E.Device(null);
for (let i = 0; i < 3; i++) dev.step(1);
const ctx = () => ({ state: JSON.parse(JSON.stringify(dev.state())), records: dev.readings[dev.ship] });

let r = A.ask("Is shipment A42 on track?", ctx());
assert.strictEqual(r.intent, "track");
assert.deepStrictEqual(r.steps.map((s) => s.tool), ["query_telemetry", "search_compliance_sop", "forecast_arrival"]);
assert.ok(/on track/.test(r.answer), r.answer);

dev.command({ env: "heat" });
for (let i = 0; i < 40; i++) dev.step(1);
r = A.ask("Where should we divert?", ctx());
assert.ok(/Divert to|No diversion/.test(r.answer), r.answer);
r = A.ask("Why is it at risk?", ctx());
assert.ok(/Root cause/.test(r.answer) && /faster than at 25/.test(r.answer), r.answer);

dev.command({ env: "freeze" });
let warnedLow = false;
for (let i = 0; i < 80 && !dev.tr.frozen; i++) {
  dev.step(1);
  if (dev.temp > 0 && dev.temp < 2 && !warnedLow) { assert.ok(/below 2 to 8 °C/.test(A.ask("Is it safe?", ctx()).answer)); warnedLow = true; }
}
assert.ok(warnedLow && dev.tr.frozen);
r = A.ask("Can I still use this vaccine?", ctx());
assert.ok(/not usable: it froze/.test(r.answer), r.answer);

// tampering is reported from the hash chain, not guessed
const recs = dev.readings[dev.ship]; recs[3].t = 400;
r = A.ask("Was the box tampered with?", ctx());
assert.ok(/TAMPERING DETECTED: reading #3/.test(r.answer), r.answer);

assert.strictEqual(A.ask("tell me a joke", ctx()).intent, null);
console.log("agent.js: all tests passed");
