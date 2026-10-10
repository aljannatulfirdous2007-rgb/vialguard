"""Which planner picks the right tools more often: the local LLM or the keyword router?

Questions written by us with the tool set a correct answer needs. A plan counts as correct when it calls
exactly those tools (extra or missing tools = wrong). Results go to data/agent_eval.json.
CASES were used while fixing the keyword router (2 bugs), so its score there is optimistic.
HELDOUT was written afterwards and nothing was changed after scoring it: that is the fair number.
    python agent/eval_planner.py
"""
import json
import os
import time

import vialguard_graph as vg

CASES = [
    ("Did fridge BLD26-001 ever go above 8 °C?", {"query_telemetry_db"}),
    ("What is the temperature of MHT2025-001 right now?", {"query_telemetry_db"}),
    ("Give me a summary of the freezer ULT26-001", {"query_telemetry_db"}),
    ("What will BLD26-001 be in 10 minutes?", {"get_forecast"}),
    ("Forecast for MHT2025-001", {"get_forecast"}),
    ("Has anyone tampered with the freezer records?", {"audit_hash_chain"}),
    ("Can I trust the BLD26-001 log, was it edited?", {"audit_hash_chain"}),
    ("What does WHO say about freezing vaccines?", {"search_compliance_sop"}),
    ("What is mean kinetic temperature?", {"search_compliance_sop"}),
    ("BLD26-001 went above 8 °C, what should we do?", {"query_telemetry_db", "search_compliance_sop"}),
    ("Is MHT2025-001 safe right now and will it stay safe?", {"query_telemetry_db", "get_forecast"}),
    ("Check BLD26-001 for excursions and tampering", {"query_telemetry_db", "audit_hash_chain"}),
]

HELDOUT = [
    ("How cold is the ULT26-001 freezer at the moment?", {"query_telemetry_db"}),
    ("Were there any temperature breaches in MHT2025-001?", {"query_telemetry_db"}),
    ("Predict the next reading for BLD26-001", {"get_forecast"}),
    ("Is the hash chain for MHT2025-001 intact?", {"audit_hash_chain"}),
    ("What are the storage guidelines for insulin?", {"search_compliance_sop"}),
    ("How many door openings did BLD26-001 have?", {"query_telemetry_db"}),
    ("MHT2025-001 had an excursion, what is the procedure?", {"query_telemetry_db", "search_compliance_sop"}),
    ("Show the current reading of BLD26-001 and whether its log was manipulated", {"query_telemetry_db", "audit_hash_chain"}),
]


def score(planner, cases=CASES):
    ok, rows, t0 = 0, [], time.time()
    for q, want in cases:
        try:
            got = {c["name"] for c in planner(q)}
        except Exception as e:
            got = {f"error: {type(e).__name__}"}
        ok += got == want
        rows.append({"question": q, "expected": sorted(want), "got": sorted(got), "correct": got == want})
    return {"correct": ok, "total": len(cases), "seconds_per_question": round((time.time() - t0) / len(cases), 2), "cases": rows}


def main():
    res = {"keyword_router (tuning set)": score(vg.rule_plan), "keyword_router (held-out)": score(vg.rule_plan, HELDOUT)}
    if vg._ollama_up():
        res[f"llm {vg.MODEL} (tuning set)"] = score(vg.llm_plan)
        res[f"llm {vg.MODEL} (held-out)"] = score(vg.llm_plan, HELDOUT)
    for k, v in res.items():
        print(f"{k:<40} {v['correct']}/{v['total']} correct   {v['seconds_per_question']} s/question")
        for c in v["cases"]:
            if not c["correct"]:
                print(f"    miss: {c['question']!r} expected {c['expected']} got {c['got']}")
    json.dump(res, open(os.path.join(vg.HERE, "..", "data", "agent_eval.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
