// Run: node tests/chain.test.js  — checks the dashboard hash code against the Python reference vector.
const assert = require("assert");
const { sha256Hex, recordHash, verifyChain } = require("../dashboard/chain.js");

assert.strictEqual(sha256Hex("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
assert.strictEqual(sha256Hex(""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
const shared = recordHash({ seq: 0, ts: 1700000000, t: 512, b: 10000, lid: 0, ev: "BOOT", prev: "GENESIS" });
assert.strictEqual(shared, "54df1b83e6721d49f00f3a3eeac730e2c7b76bd105ddba0d05f80863682016b5", "must match model/test_model.py");

let prev = "GENESIS"; const recs = [];
for (let i = 0; i < 5; i++) {
  const r = { seq: i, ts: 1700000000 + i, t: 500 + i, b: 10000, lid: 0, ev: "", prev };
  r.h = recordHash(r); recs.push(r); prev = r.h;
}
assert.ok(verifyChain(recs).ok);
recs[3].t = 900;
assert.strictEqual(verifyChain(recs).brokenSeq, 3);
console.log("chain.js: all tests passed");
