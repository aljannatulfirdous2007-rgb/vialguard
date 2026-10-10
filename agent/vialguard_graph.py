"""Ask VialGuard, LangGraph edition: a tool-calling agent over the live InfluxDB data.

    question ─▶ plan ─▶ tools ─▶ answer ─▶ check ─┬─▶ END              (every number grounded in a tool result)
                                                   └─▶ fallback ─▶ END  (LLM said something the tools did not)

  plan     a keyword router chooses tools. A local LLM can plan instead (--llm-plan), but on 8 held-out questions
           the router picked the right tools 7/8 times and qwen2.5-coder:1.5b 2/8 (agent/eval_planner.py)
  tools    query_telemetry_db, get_forecast, audit_hash_chain (Flux queries on InfluxDB), search_compliance_sop
  answer   a local LLM (Ollama, no API key, offline) writes a short answer from the tool results only
  check    guardrail: every number in the answer must appear in the tool results or the question
  fallback a template answer built directly from the tool results

Run:  python agent/vialguard_graph.py "Did fridge BLD26-001 ever go above 8 °C?"
      python agent/vialguard_graph.py --llm-plan "..."     LLM chooses the tools
      python agent/vialguard_graph.py --no-llm "..."      keyword router + template only
      python agent/vialguard_graph.py --mermaid            print the graph as a Mermaid diagram
Needs the pipeline running (pipeline/start_pipeline.bat) and data streamed (pipeline/producer.py --real).
"""
import argparse
import json
import os
import re
import sys
from typing import Any, TypedDict

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.isdir("D:/vialguard-agent"):        # LangGraph lives here; must win over any older langchain on C:
    sys.path.insert(0, "D:/vialguard-agent")
sys.path.insert(0, os.path.join(HERE, "..", "pipeline"))
import config  # noqa: E402  (adds D: package folders to sys.path)

from influxdb_client import InfluxDBClient  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402

MODEL = os.environ.get("VG_OLLAMA_MODEL", "qwen2.5-coder:1.5b")
DEVICES = {
    "BLD26-001": {"kind": "fridge", "range": (2.0, 8.0)},
    "MHT2025-001": {"kind": "fridge", "range": (2.0, 8.0)},
    "ULT26-001": {"kind": "ultra-low freezer", "range": None},
}
RANGE = "range(start: 2020-01-01T00:00:00Z)"

# Short, sourced rules. Kept deliberately small: the agent may only quote what is here.
SOP = [
    {"id": "storage", "keys": "store storage range limit fridge 2 8 temperature safe",
     "text": "Vaccines and insulin are stored at 2 to 8 °C.",
     "source": "WHO, Immunization in Practice, module 3 (cold chain)"},
    {"id": "freeze", "keys": "freeze frozen ice below 0 cold shake test damage",
     "text": "Freeze-sensitive vaccines (e.g. hepatitis B, DTP-containing, insulin) are damaged by freezing; "
             "suspected frozen vials must be quarantined and checked (shake test) before use.",
     "source": "WHO, Temperature-sensitive health products in the Expanded Programme on Immunization supply chain"},
    {"id": "excursion", "keys": "excursion above 8 breach warm heat alarm what do action report",
     "text": "On an excursion: keep the product cold, mark it 'do not use' until assessed, record start time, "
             "duration and peak temperature, then decide using the product's stability data.",
     "source": "WHO TRS 961 Annex 9, Model guidance for storage and transport of time- and temperature-sensitive products"},
    {"id": "mkt", "keys": "mkt mean kinetic temperature average stability arrhenius",
     "text": "Mean kinetic temperature (MKT) summarises a temperature history into one value that weights warm periods "
             "more heavily, using the Arrhenius equation.",
     "source": "USP <1079.2> Mean Kinetic Temperature in the Evaluation of Temperature Excursions"},
    {"id": "door", "keys": "door open opening lid access",
     "text": "Keep fridge doors closed; frequent or long openings cause warm spikes. Each opening should be explainable.",
     "source": "WHO, Immunization in Practice, module 3 (cold chain)"},
    {"id": "records", "keys": "record log audit tamper chain evidence inspection edit fake",
     "text": "Temperature records must be complete and attributable; gaps or edits must be detectable.",
     "source": "WHO TRS 961 Annex 9 (records); VialGuard seals each reading with SHA-256 linked to the previous one"},
]

_client = None


def influx():
    global _client
    if _client is None:
        _client = InfluxDBClient(url=config.INFLUX_URL, token=config.INFLUX_TOKEN, org=config.INFLUX_ORG, timeout=30000)
    return _client


def flux(q):
    return [r.values for t in influx().query_api().query(q) for r in t.records]


def _device(d):
    d = (d or "").upper().strip()
    for k in DEVICES:
        if d and (d == k or d in k or k.split("-")[0] == d.split("-")[0]):
            return k
    return "BLD26-001"


# ---------------------------------------------------------------- tools

@tool
def query_telemetry_db(device: str = "BLD26-001", metric: str = "summary") -> dict:
    """Query the InfluxDB time-series database for one device.
    device: BLD26-001 (fridge), MHT2025-001 (fridge) or ULT26-001 (ultra-low freezer).
    metric: "latest" (last reading), "summary" (min, mean, max, count) or "excursions" (minutes outside 2-8 °C, peak, first/last time)."""
    dev = _device(device)
    base = (f'from(bucket:"{config.INFLUX_BUCKET}") |> {RANGE} '
            f'|> filter(fn:(r)=> r._measurement=="reading" and r._field=="temp" and r.device=="{dev}")')
    info = {"device": dev, "kind": DEVICES[dev]["kind"]}
    if metric == "latest":
        r = flux(base + " |> last()")[0]
        return {**info, "temp_c": round(r["_value"], 2), "time_utc": r["_time"].strftime("%Y-%m-%d %H:%M")}
    if metric == "excursions":
        lim = DEVICES[dev]["range"]
        if lim is None:
            return {**info, "note": "freezer: the 2-8 °C rule does not apply"}
        rows = flux(base + f" |> filter(fn:(r)=> r._value > {lim[1]} or r._value < {lim[0]})")
        if not rows:
            return {**info, "range_c": f"{lim[0]:g} to {lim[1]:g}", "readings_outside": 0}
        peak = max(rows, key=lambda r: abs(r["_value"] - 5))
        return {**info, "range_c": f"{lim[0]:g} to {lim[1]:g}", "readings_outside": len(rows),
                "peak_c": round(peak["_value"], 2), "peak_time_utc": peak["_time"].strftime("%Y-%m-%d %H:%M"),
                "first_utc": rows[0]["_time"].strftime("%Y-%m-%d %H:%M"), "last_utc": rows[-1]["_time"].strftime("%Y-%m-%d %H:%M")}
    out = dict(info)
    for fn in ("min", "mean", "max", "count"):
        out[f"{fn}_c" if fn != "count" else "readings"] = round(flux(base + f" |> {fn}()")[0]["_value"], 2)
    doors = flux(f'from(bucket:"{config.INFLUX_BUCKET}") |> {RANGE} |> filter(fn:(r)=> r._measurement=="event" '
                 f'and r.device=="{dev}") |> count()')
    out["door_openings_logged"] = int(doors[0]["_value"]) if doors else 0
    return out


@tool
def get_forecast(device: str = "BLD26-001") -> dict:
    """Latest 10-minute-ahead temperature forecasts (XGBoost and LSTM) stored in InfluxDB for a fridge."""
    dev = _device(device)
    rows = flux(f'from(bucket:"{config.INFLUX_BUCKET}") |> {RANGE} |> filter(fn:(r)=> r._measurement=="forecast" '
                f'and r._field=="temp_10m" and r.device=="{dev}") |> group(columns:["model"]) |> last()')
    if not rows:
        return {"device": dev, "note": "no forecasts (freezers are not forecast)"}
    return {"device": dev, "horizon_min": 10, "made_at_utc": rows[0]["_time"].strftime("%Y-%m-%d %H:%M"),
            **{f"{r['model']}_c": round(r["_value"], 2) for r in rows}}


@tool
def audit_hash_chain(device: str = "BLD26-001") -> dict:
    """Check the tamper-evident SHA-256 hash chain for a device: how many readings failed verification."""
    dev = _device(device)
    base = (f'from(bucket:"{config.INFLUX_BUCKET}") |> {RANGE} |> filter(fn:(r)=> r._measurement=="reading" '
            f'and r._field=="chain_ok" and r.device=="{dev}")')
    total = flux(base + " |> count()")
    bad = flux(base + " |> filter(fn:(r)=> r._value==0) |> count()")
    return {"device": dev, "readings_checked": int(total[0]["_value"]) if total else 0,
            "broken_links": int(bad[0]["_value"]) if bad else 0}


@tool
def search_compliance_sop(query: str) -> dict:
    """Search cold-chain compliance rules (WHO, USP). Returns the best matching rules with their sources."""
    words = {w2 for w in re.findall(r"[a-z0-9]+", query.lower()) for w2 in (w, w.rstrip("s"), w.rstrip("ed"), w.rstrip("d"))}
    scored = sorted(SOP, key=lambda s: -len(words & set(s["keys"].split())))
    hits = [s for s in scored if words & set(s["keys"].split())][:2]
    return {"rules": [{"rule": s["text"], "source": s["source"]} for s in hits]}


TOOLS = {t.name: t for t in (query_telemetry_db, get_forecast, audit_hash_chain, search_compliance_sop)}


# ---------------------------------------------------------------- graph state and nodes

class State(TypedDict, total=False):
    question: str
    use_llm: bool
    llm_plan: bool
    planner: str                 # "llm" or "rules"
    calls: list[dict[str, Any]]  # [{"name", "args"}]
    results: list[dict[str, Any]]
    answer: str
    grounded: bool
    trace: list[str]


def _llm():
    from langchain_ollama import ChatOllama
    return ChatOllama(model=MODEL, temperature=0, num_predict=300)


def _find_device(q):
    for k in DEVICES:
        if k.split("-")[0].lower() in q.lower():
            return k
    if "freezer" in q.lower() or "ult" in q.lower():
        return "ULT26-001"
    return "BLD26-001"


def rule_plan(q):
    ql, dev = q.lower(), _find_device(q)
    calls = []
    if re.search(r"above|below|excursion|breach|exceed|outside|warm|went|ever|8 ?°?c", ql):
        calls.append({"name": "query_telemetry_db", "args": {"device": dev, "metric": "excursions"}})
    if re.search(r"now|current|latest|right now|status", ql):
        calls.append({"name": "query_telemetry_db", "args": {"device": dev, "metric": "latest"}})
    if re.search(r"forecast|predict|next|will|going to|future", ql):
        calls.append({"name": "get_forecast", "args": {"device": dev}})
    if re.search(r"tamper|hash|chain|audit|trust|edit|fake|manipulat", ql):
        calls.append({"name": "audit_hash_chain", "args": {"device": dev}})
    if re.search(r"rule|sop|should|what do|guideline|\bwho\b|usp|allowed|\bfreez(e|ing)\b|frozen|mkt|mean kinetic|stability", ql):
        calls.append({"name": "search_compliance_sop", "args": {"query": q}})
    if not calls:
        calls.append({"name": "query_telemetry_db", "args": {"device": dev, "metric": "summary"}})
    return calls


def parse_text_calls(text):
    """Small local models often write the tool call as JSON text instead of a structured call."""
    calls = []
    for m in re.finditer(r"\{[^{}]*\"name\"[^{}]*(\{[^{}]*\})?[^{}]*\}", text or ""):
        try:
            o = json.loads(m.group(0))
        except ValueError:
            continue
        if o.get("name") in TOOLS:
            calls.append({"name": o["name"], "args": o.get("arguments") or o.get("args") or o.get("parameters") or {}})
    return calls


def llm_plan(q):
    llm = _llm().bind_tools(list(TOOLS.values()))
    msg = llm.invoke([
        ("system", "You are VialGuard, a cold-chain monitoring assistant. Call every tool needed to answer. "
                   "Devices: BLD26-001 and MHT2025-001 are fridges, ULT26-001 is a freezer. "
                   "Temperature history or 'did it go above/below' needs query_telemetry_db with metric excursions. "
                   "Current temperature needs metric latest. Rules or 'what should we do' need search_compliance_sop. "
                   "Tampering needs audit_hash_chain. Future temperature needs get_forecast."),
        ("human", q)])
    calls = [{"name": c["name"], "args": c.get("args") or {}} for c in msg.tool_calls if c["name"] in TOOLS]
    return calls or parse_text_calls(msg.content)


def plan(state: State) -> State:
    q = state["question"]
    trace = [f"QUESTION  {q}"]
    if state.get("use_llm") and state.get("llm_plan"):
        try:
            calls = llm_plan(q)
            if calls:
                trace.append(f"PLAN      LLM ({MODEL}) chose {', '.join(c['name'] for c in calls)}")
                return {"planner": "llm", "calls": calls, "trace": trace}
            trace.append("PLAN      LLM chose no valid tool, using keyword router")
        except Exception as e:
            trace.append(f"PLAN      LLM unavailable ({type(e).__name__}), using keyword router")
    calls = rule_plan(q)
    trace.append(f"PLAN      keyword router chose {', '.join(c['name'] for c in calls)}")
    return {"planner": "rules", "calls": calls, "trace": trace}


def run_tools(state: State) -> State:
    trace, results = list(state["trace"]), []
    for c in state["calls"]:
        try:
            out = TOOLS[c["name"]].invoke(c["args"])
        except Exception as e:
            out = {"error": f"{type(e).__name__}: {e}"}
        results.append({"tool": c["name"], "args": c["args"], "result": out})
        trace.append(f"TOOL      {c['name']}({json.dumps(c['args'], ensure_ascii=False)}) -> {json.dumps(out, ensure_ascii=False, default=str)}")
    return {"results": results, "trace": trace}


def template_answer(results):
    parts = []
    for r in results:
        o, t = r["result"], r["tool"]
        if "error" in o:
            parts.append(f"{t} failed: {o['error']}.")
        elif t == "query_telemetry_db" and "readings_outside" in o:
            if o["readings_outside"] == 0:
                parts.append(f"{o['device']} stayed inside {o['range_c']} °C for every reading.")
            else:
                parts.append(f"{o['device']} had {o['readings_outside']} readings outside {o['range_c']} °C, "
                             f"between {o['first_utc']} and {o['last_utc']} UTC, peaking at {o['peak_c']} °C ({o['peak_time_utc']} UTC).")
        elif t == "query_telemetry_db" and "temp_c" in o:
            parts.append(f"{o['device']} ({o['kind']}) last read {o['temp_c']} °C at {o['time_utc']} UTC.")
        elif t == "query_telemetry_db" and "mean_c" in o:
            parts.append(f"{o['device']} ({o['kind']}): {int(o['readings'])} readings, {o['min_c']} to {o['max_c']} °C, "
                         f"average {o['mean_c']} °C, {o['door_openings_logged']} door openings logged.")
        elif t == "query_telemetry_db" and "note" in o:
            parts.append(f"{o['device']}: {o['note']}.")
        elif t == "get_forecast" and "note" not in o:
            models = ", ".join(f"{k[:-2].upper()} {v} °C" for k, v in o.items() if k.endswith("_c"))
            parts.append(f"Forecast for {o['device']} {o['horizon_min']} min after {o['made_at_utc']} UTC: {models}.")
        elif t == "get_forecast":
            parts.append(f"{o['device']}: {o['note']}.")
        elif t == "audit_hash_chain":
            parts.append(f"{o['device']}: {o['readings_checked']} readings checked, {o['broken_links']} broken hash links"
                         + (" (no tampering detected)." if o["broken_links"] == 0 else " (records were altered or lost)."))
        elif t == "search_compliance_sop":
            parts += [f"Rule: {x['rule']} (Source: {x['source']})" for x in o["rules"]]
    return " ".join(parts)


def answer(state: State) -> State:
    trace = list(state["trace"])
    if not state.get("use_llm"):
        a = template_answer(state["results"])
        trace.append("ANSWER    template from tool results")
        return {"answer": a, "trace": trace}
    try:
        facts = json.dumps([{"tool": r["tool"], "result": r["result"]} for r in state["results"]], ensure_ascii=False, default=str)
        msg = _llm().invoke([
            ("system", "Answer in at most 3 short sentences for a pharmacist. Use ONLY the facts in the tool results. "
                       "Copy numbers exactly. Always say °C. If a rule is used, name its source."),
            ("human", f"Question: {state['question']}\nTool results: {facts}")])
        trace.append(f"ANSWER    written by {MODEL}")
        return {"answer": msg.content.strip(), "trace": trace}
    except Exception as e:
        trace.append(f"ANSWER    LLM failed ({type(e).__name__}), template used")
        return {"answer": template_answer(state["results"]), "trace": trace}


NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def check(state: State) -> State:
    """Guardrail: every number in the answer must come from a tool result or the question."""
    allowed = set()
    for n in NUM.findall(json.dumps(state["results"], default=str) + " " + state["question"]):
        allowed |= {n, n.lstrip("-")}
        try:
            allowed.add(f"{float(n):g}")
        except ValueError:
            pass
    bad = []
    for n in NUM.findall(state["answer"]):
        if n in allowed or n.lstrip("-") in allowed or f"{float(n):g}" in allowed:
            continue
        bad.append(n)
    trace = list(state["trace"])
    trace.append("CHECK     all numbers grounded in tool results" if not bad else f"CHECK     ungrounded numbers {bad}")
    return {"grounded": not bad, "trace": trace}


def fallback(state: State) -> State:
    trace = list(state["trace"]) + ["FALLBACK  replaced with template answer built from tool results"]
    return {"answer": template_answer(state["results"]), "grounded": True, "trace": trace}


def _ollama_up():
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2)
        return True
    except Exception:
        return False


def build():
    g = StateGraph(State)
    g.add_node("plan", plan)
    g.add_node("tools", run_tools)
    g.add_node("answer", answer)
    g.add_node("check", check)
    g.add_node("fallback", fallback)
    g.add_edge(START, "plan")
    g.add_edge("plan", "tools")
    g.add_edge("tools", "answer")
    g.add_edge("answer", "check")
    g.add_conditional_edges("check", lambda s: END if s["grounded"] else "fallback", {END: END, "fallback": "fallback"})
    g.add_edge("fallback", END)
    return g.compile()


GRAPH = build()


def ask(question, use_llm=True, llm_plan=False):
    return GRAPH.invoke({"question": question, "use_llm": use_llm and _ollama_up(), "llm_plan": llm_plan})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*")
    ap.add_argument("--no-llm", action="store_true", help="template answers, no Ollama")
    ap.add_argument("--llm-plan", action="store_true", help="let the LLM choose tools (less accurate with small models)")
    ap.add_argument("--mermaid", action="store_true")
    a = ap.parse_args()
    if a.mermaid:
        print(GRAPH.get_graph().draw_mermaid())
        return
    s = ask(" ".join(a.question) or "Did fridge BLD26-001 ever go above 8 °C?", use_llm=not a.no_llm, llm_plan=a.llm_plan)
    print("\n".join(s["trace"]))
    print("\nANSWER:", s["answer"])


if __name__ == "__main__":
    main()
