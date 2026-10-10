"""Tests for the LangGraph agent. Guardrail tests always run; tool tests need InfluxDB with the real data streamed.
    python -m unittest agent/test_graph.py
"""
import os
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vialguard_graph as vg  # noqa: E402


def influx_up():
    try:
        urllib.request.urlopen(vg.config.INFLUX_URL + "/health", timeout=2)
        return True
    except Exception:
        return False


class Guardrail(unittest.TestCase):
    results = [{"tool": "query_telemetry_db", "args": {}, "result": {"device": "BLD26-001", "peak_c": 9.9, "readings_outside": 9}}]

    def test_grounded_answer_passes(self):
        s = vg.check({"question": "above 8?", "results": self.results, "answer": "Yes: 9 readings above 8 °C, peak 9.9 °C.", "trace": []})
        self.assertTrue(s["grounded"])

    def test_invented_number_is_caught(self):
        s = vg.check({"question": "above 8?", "results": self.results, "answer": "Peak was 11.2 °C.", "trace": []})
        self.assertFalse(s["grounded"])

    def test_text_tool_call_is_parsed(self):
        calls = vg.parse_text_calls('{"name": "get_forecast", "arguments": {"device": "MHT2025-001"}}')
        self.assertEqual(calls, [{"name": "get_forecast", "args": {"device": "MHT2025-001"}}])

    def test_router_picks_tools(self):
        names = {c["name"] for c in vg.rule_plan("BLD26-001 went above 8 °C, what should we do?")}
        self.assertEqual(names, {"query_telemetry_db", "search_compliance_sop"})


@unittest.skipUnless(influx_up(), "InfluxDB not running")
class EndToEnd(unittest.TestCase):
    def test_real_excursion_found(self):
        s = vg.ask("Did fridge BLD26-001 ever go above 8 °C?", use_llm=False)
        self.assertTrue(s["grounded"])
        self.assertIn("9.9 °C", s["answer"])

    def test_chain_intact(self):
        s = vg.ask("Has anyone tampered with the freezer records?", use_llm=False)
        self.assertIn("0 broken hash links", s["answer"])


if __name__ == "__main__":
    unittest.main()
