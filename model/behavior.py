"""Behaviour detector: turns the temperature stream into "what did a person do?".

Two signatures, both explainable rules (no black box):

  ACCESS     the box/fridge was opened. Warm air rushes in: the probe jumps fast,
             then recovers within minutes once it is closed again.
             Rule: rise >= JUMP_C within JUMP_WINDOW_MIN minutes.
  COOLING    refrigeration stopped or ice packs ran out (e.g. a driver switching off
             the cooling to save fuel). No jump, but a steady climb that does not recover.
             Rule: least-squares slope over SLOPE_WINDOW_MIN >= SLOPE_C_PER_MIN while
             already SLOPE_ABOVE_C above the running baseline.

Thresholds are calibrated on REAL data: the Zenodo "Multi-Node IoT Dataset for Real-Time
Hospital Cold-Chain Monitoring" (DS18B20 probe, 1-minute readings, labelled door openings).
Run `python model/calibrate_behavior.py` to reproduce the numbers.
"""
from collections import deque

JUMP_C = 0.9               # °C rise that counts as a sudden jump (calibrated, see calibrate_behavior.py)
JUMP_WINDOW_MIN = 3.0      # ...within this many minutes
COOLDOWN_MIN = 6.0         # one opening produces one flag
SLOPE_WINDOW_MIN = 30.0
SLOPE_C_PER_MIN = 0.04     # 2.4 °C per hour sustained climb
SLOPE_ABOVE_C = 1.5
PREDICT_POINTS = 4            # readings used for the trend line
PREDICT_MIN_SLOPE = 0.01      # °C per minute (0.6 °C per hour); slower drifts are noise
PREDICT_HORIZON_MIN = 90.0    # only warn about breaches expected within 90 minutes


class BehaviorDetector:
    def __init__(self, jump_c=JUMP_C, window_min=JUMP_WINDOW_MIN, cooldown_min=COOLDOWN_MIN):
        self.jump_c, self.window_min, self.cooldown_min = jump_c, window_min, cooldown_min
        self.hist = deque()          # (minutes, temp) recent readings
        self.baseline = None         # slow running baseline (in-range behaviour)
        self.last_access = -1e9
        self.last_cooling = -1e9
        self.access_events = 0
        self.cooling_events = 0
        self.unexplained_access = 0  # openings without a logged lid/door event = suspicious
        self.cooling_armed = True    # one cooling-failure flag per episode

    def update(self, minutes, temp, lid_open=False):
        """Feed one reading. Returns a list of flags raised by this reading."""
        flags = []
        if temp is None:
            return flags
        self.hist.append((minutes, temp))
        horizon = max(self.window_min, SLOPE_WINDOW_MIN) + 1
        while self.hist and minutes - self.hist[0][0] > horizon:
            self.hist.popleft()
        if self.baseline is None:
            self.baseline = temp
        elif temp < self.baseline + 1.0:     # only learn from calm readings
            self.baseline += 0.02 * (temp - self.baseline)

        recent = [t for (m, t) in self.hist if minutes - m <= self.window_min and m < minutes]
        if recent and temp - min(recent) >= self.jump_c and minutes - self.last_access >= self.cooldown_min:
            self.last_access = minutes
            self.access_events += 1
            if not lid_open:
                self.unexplained_access += 1
            flags.append("ACCESS_LOGGED" if lid_open else "ACCESS_SUSPECTED")

        if not self.cooling_armed and temp <= self.baseline + 0.5:
            self.cooling_armed = True          # recovered: a new episode can be flagged
        win = [(m, t) for (m, t) in self.hist if minutes - m <= SLOPE_WINDOW_MIN]
        if len(win) >= 4 and self.cooling_armed:
            n = len(win)
            mx = sum(m for m, _ in win) / n
            my = sum(t for _, t in win) / n
            den = sum((m - mx) ** 2 for m, _ in win)
            slope = sum((m - mx) * (t - my) for m, t in win) / den if den else 0.0
            if slope >= SLOPE_C_PER_MIN and temp - self.baseline >= SLOPE_ABOVE_C and win[-1][0] - win[0][0] >= SLOPE_WINDOW_MIN * 0.6:
                self.last_cooling = minutes
                self.cooling_armed = False
                self.cooling_events += 1
                flags.append("COOLING_FAILURE")
        return flags

    def trust_score(self):
        """0-100 custody score: drops for unexplained openings and cooling failures."""
        return max(0, 100 - 12 * self.unexplained_access - 25 * self.cooling_events)

    def predict_breach(self, horizon_min=PREDICT_HORIZON_MIN):
        """Early warning: fit a line to the last few readings and say when it will cross 8 °C or 0 °C.

        Returns None, or {"kind": "heat"|"freeze", "minutes": m, "rate": °C per hour}.
        Only warns while the probe is still inside 0-8 °C, i.e. BEFORE the breach.
        """
        pts = list(self.hist)[-PREDICT_POINTS:]
        if len(pts) < 3:
            return None
        t_now = pts[-1][1]
        if not (0.0 < t_now <= 8.0):
            return None
        n = len(pts)
        mx = sum(m for m, _ in pts) / n
        my = sum(t for _, t in pts) / n
        den = sum((m - mx) ** 2 for m, _ in pts)
        if den == 0:
            return None
        slope = sum((m - mx) * (t - my) for m, t in pts) / den        # °C per minute
        if abs(slope) < PREDICT_MIN_SLOPE:
            return None
        target, kind = (8.0, "heat") if slope > 0 else (0.0, "freeze")
        minutes = (target - t_now) / slope
        if 0 < minutes <= horizon_min:
            return {"kind": kind, "minutes": round(minutes, 1), "rate": round(slope * 60, 2)}
        return None



def root_cause(temp, lid_open, moving, recent_flags, frozen, excursion):
    """Combine the signals into one plain-language cause. Returns (code, text) or (None, None)."""
    recent = recent_flags[-3:]
    if frozen or temp <= 0:
        return ("OVERCOOLING", "Over-cooling: vials touching ice packs or a fridge thermostat set too cold")
    if lid_open and (excursion or "ACCESS_LOGGED" in recent):
        return ("DOOR_OPEN", "Box or door left open: warm air entering")
    if "ACCESS_SUSPECTED" in recent:
        return ("UNLOGGED_OPENING", "Box opened without a logged reason (temperature jump, then recovery)")
    if excursion and not moving:
        return ("STATIONARY_HEAT", "Vehicle stationary in heat: cold box warming toward outside temperature")
    if "COOLING_FAILURE" in recent or excursion:
        return ("COOLING_FAILURE", "Cooling failure: steady climb with no recovery (ice packs spent or unit off)")
    return (None, None)
