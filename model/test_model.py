import math
import unittest

from vialguard_model import (GENESIS, PROFILES, Tracker, mkt_c, rate, record_hash, verify_chain)


def truck_stall(profile_key, stall_temp=38.0, step_h=0.5):
    """5 °C, then a 1 h ramp up, 2 h at stall_temp, 1 h ramp down, back to 5 °C (24 h total)."""
    tr = Tracker(PROFILES[profile_key])
    for i in range(48):
        h = i * step_h
        if 8 <= h < 9:
            t = 5 + (h - 8) * (stall_temp - 5)
        elif 9 <= h < 11:
            t = stall_temp
        elif 11 <= h < 12:
            t = stall_temp - (h - 11) * (stall_temp - 5)
        else:
            t = 5
        tr.update(t, step_h)
    return tr


class ModelTests(unittest.TestCase):
    def test_rate_is_one_at_reference(self):
        self.assertAlmostEqual(rate(25.0, 10000.0), 1.0)

    def test_hotter_is_faster(self):
        self.assertGreater(rate(38.0, 10000.0), rate(30.0, 10000.0))

    def test_vvm7_class_matches_who_definition(self):
        # 7 days at 37 °C should use the same budget as 45 days at 25 °C
        self.assertAlmostEqual(rate(37.0, 14340.0) * 7, 45, delta=1.0)

    def test_in_range_storage_uses_no_budget(self):
        tr = Tracker(PROFILES["demo"])
        for _ in range(100):
            tr.update(5.0, 1.0)
        self.assertEqual(tr.budget_pct, 100.0)
        self.assertEqual(tr.verdict(), "OK")
        self.assertIsNone(tr.hours_left(5.0))

    def test_deck_scenario_loses_about_23_percent(self):
        # The slide says "3 h at 38 °C = 23% of life lost" for the demo profile
        tr = truck_stall("demo")
        self.assertAlmostEqual(100 - tr.budget_pct, 23, delta=2)
        self.assertEqual(tr.verdict(), "OK")

    def test_same_excursion_different_verdict_per_drug(self):
        demo, insulin = truck_stall("demo"), truck_stall("insulin")
        self.assertLess(demo.budget_pct, insulin.budget_pct)

    def test_freeze_is_discard_only_for_freeze_sensitive(self):
        sensitive, tolerant = Tracker(PROFILES["vvm7"]), Tracker(PROFILES["vvm2"])
        for tr in (sensitive, tolerant):
            for t in (5, -1, -1, 5):
                tr.update(t, 0.1)
        self.assertEqual(sensitive.verdict(), "DISCARD_FROZEN")
        self.assertEqual(tolerant.verdict(), "OK")

    def test_single_glitch_below_zero_is_not_a_freeze(self):
        tr = Tracker(PROFILES["vvm7"])
        for t in (5, -1, 5, -1, 5):
            tr.update(t, 0.1)
        self.assertFalse(tr.frozen)

    def test_sensor_error_values_are_ignored(self):
        tr = Tracker(PROFILES["demo"])
        tr.update(-127.0, 1.0)
        tr.update(85.0, 1.0)
        self.assertEqual(tr.temps, [])
        self.assertEqual(tr.budget_pct, 100.0)

    def test_heat_exhaustion_leads_to_discard(self):
        tr = Tracker(PROFILES["demo"])
        for _ in range(60):
            tr.update(30.0, 1.0)
        self.assertEqual(tr.verdict(), "DISCARD_HEAT")
        self.assertEqual(tr.hours_left(30.0), 0.0)

    def test_hours_left_at_25c_equals_remaining_budget(self):
        tr = Tracker(PROFILES["demo"])
        tr.update(25.0, 12.0)
        self.assertAlmostEqual(tr.hours_left(25.0), 36.0)

    def test_mkt_of_constant_temperature_is_that_temperature(self):
        self.assertAlmostEqual(mkt_c([5.0] * 10), 5.0, places=6)

    def test_mkt_is_pulled_up_by_excursions(self):
        temps = [5.0] * 9 + [40.0]
        self.assertGreater(mkt_c(temps), sum(temps) / len(temps))

    def test_hash_chain_detects_tampering(self):
        prev, recs = GENESIS, []
        for seq, t in enumerate([500, 520, 3800]):
            h = record_hash(seq, 1700000000 + seq * 10, t, 10000, 0, "", prev)
            recs.append({"seq": seq, "ts": 1700000000 + seq * 10, "t": t, "b": 10000, "lid": 0, "ev": "", "prev": prev, "h": h})
            prev = h
        self.assertEqual(verify_chain(recs), -1)
        recs[2]["t"] = 500  # someone hides the excursion
        self.assertEqual(verify_chain(recs), 2)

    def test_known_hash_vector(self):
        # Shared with firmware and dashboard tests: all three must produce this exact value
        self.assertEqual(record_hash(0, 1700000000, 512, 10000, 0, "BOOT", GENESIS),
                         "54df1b83e6721d49f00f3a3eeac730e2c7b76bd105ddba0d05f80863682016b5")


if __name__ == "__main__":
    unittest.main()
