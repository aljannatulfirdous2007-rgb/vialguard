import unittest

import forecast as F
from behavior import BehaviorDetector
from vialguard_model import PROFILES


class BehaviorTests(unittest.TestCase):
    def test_sudden_jump_is_an_unlogged_opening(self):
        d = BehaviorDetector()
        flags = []
        for m, t in enumerate([5.0, 5.1, 5.0, 7.2, 6.5, 5.8, 5.2]):
            flags += d.update(m, t, lid_open=False)
        self.assertEqual(flags.count("ACCESS_SUSPECTED"), 1)
        self.assertEqual(d.unexplained_access, 1)

    def test_logged_opening_does_not_cost_trust(self):
        d = BehaviorDetector()
        for m, t in enumerate([5.0, 5.0, 7.2, 6.0]):
            d.update(m, t, lid_open=True)
        self.assertEqual(d.trust_score(), 100)

    def test_steady_climb_is_one_cooling_failure(self):
        d = BehaviorDetector()
        flags = []
        for m in range(0, 120):                       # +0.1 °C per minute for 2 h, no recovery
            flags += d.update(m, 5.0 + 0.1 * m)
        self.assertEqual(flags.count("COOLING_FAILURE"), 1)

    def test_normal_noise_raises_nothing(self):
        d = BehaviorDetector()
        flags = []
        for m in range(300):
            flags += d.update(m, 5.0 + 0.2 * ((m * 7) % 3 - 1))
        self.assertEqual(flags, [])


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.p = PROFILES["demo"]
        self.pos = F.ORIGIN

    def test_working_cold_box_arrives_intact(self):
        plan = F.plan(self.p, 0.0, False, 5.0, True, self.pos, F.DEFAULT_DEST, None)
        self.assertEqual(plan["action"]["kind"], "continue")
        self.assertAlmostEqual(plan["dest"]["life_at_arrival"], 100.0)

    def test_failing_box_on_long_route_is_rerouted_to_a_viable_stop(self):
        used = 0.45 * self.p.budget_hours_at_25c        # 55% left, warm box, 5 h still to go
        plan = F.plan(self.p, used, False, 40.0, False, self.pos, F.DEFAULT_DEST, None, surroundings=40.0)
        self.assertFalse(plan["will_survive"])
        self.assertEqual(plan["action"]["kind"], "reroute")
        self.assertGreaterEqual(plan["reroute"]["life_at_arrival"], F.USABLE_PCT)
        self.assertLess(plan["reroute"]["eta_h"], plan["dest"]["eta_h"])

    def test_frozen_is_discarded_not_rerouted(self):
        plan = F.plan(self.p, 0.0, True, 3.0, True, self.pos, F.DEFAULT_DEST, None)
        self.assertEqual(plan["action"]["kind"], "discard")
        self.assertIsNone(plan["reroute"])

    def test_newton_cooling_moves_toward_ambient(self):
        path = F.predict_path(5.0, 2.0, [35.0] * 50, cooling_ok=False)
        self.assertGreater(path[-1], 5.0)
        self.assertLess(path[-1], 35.0)


if __name__ == "__main__":
    unittest.main()
