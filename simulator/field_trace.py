"""A 72-hour last-mile vaccine journey, reconstructed for replay.

HONESTY NOTE: this is NOT raw logger data. The cited studies publish findings, not
their raw files. The trace is reconstructed to reproduce the deviation patterns they
report for India's cold chain:

  * Murhekar et al., Bull. WHO 2013 (10 states): exposure above 8 °C at every level,
    sub-zero exposure frequent at peripheral facilities and during transport.
  * Temperature integrity study, 3 states, TDTMV 2020: vaccines below 0 °C for 14.8% of
    storage time, deviations of 45+ minutes, "not captured by routine temperature
    monitoring practices" (manual logbook readings twice a day).

Journey (15-minute resolution, typical logger interval):
  day 1  district vaccine store, afternoon power cut warms the fridge for ~3 h
  day 2  carrier box on a two-wheeler, stuck in afternoon heat, then PHC fridge
  day 3  PHC fridge thermostat over-cools after midnight: ~2.5 h below 0 °C
         the 09:00 manual logbook check reads a normal 4 °C
"""
import math
import random

STEP_H = 0.25          # 15-minute logger interval
TOTAL_H = 72.0

# (start hour, end hour, phase code, target temperature °C); hour 0 = day 1, 08:00
SEGMENTS = [
    (0.0, 6.0, "PH_STORE", 4.5),       # district store walk-in / ILR fridge
    (6.0, 9.0, "PH_POWERCUT", 13.0),   # 14:00-17:00 power cut, fridge warms
    (9.0, 25.5, "PH_STORE", 3.8),      # power back, recovers overnight
    (25.5, 28.0, "PH_TRANSIT", 11.0),  # packed in carrier, road transport
    (28.0, 30.5, "PH_DELAY", 21.0),    # stuck in afternoon traffic / heat
    (30.5, 40.0, "PH_PHC", 6.5),       # PHC domestic fridge, warm side of range
    (40.0, 44.0, "PH_PHC", 3.0),       # night, thermostat drifting colder
    (44.0, 46.5, "PH_NIGHTFREEZE", -1.8),  # 00:00-02:30 day 3: over-cooling below 0 °C
    (46.5, 72.0, "PH_PHC", 4.2),       # recovers, looks normal by morning
]
TAU_H = {"PH_POWERCUT": 1.2, "PH_DELAY": 0.8, "PH_TRANSIT": 0.7, "PH_NIGHTFREEZE": 0.35}   # thermal lag of box/fridge
CHECK_HOURS = {9 - 8 + 24 * d for d in range(3)} | {16 - 8 + 24 * d for d in range(3)}       # 09:00 and 16:00 logbook checks
LID_HOURS = {31.0, 33.5, 49.25}                                                                # PHC staff open the box


def build(seed=7):
    """List of dicts: h (hours since start), t (°C), phase, check (bool), lid (bool)."""
    rnd = random.Random(seed)
    out, temp, prev_phase = [], 4.5, None
    n = int(TOTAL_H / STEP_H) + 1
    for i in range(n):
        h = i * STEP_H
        seg = next((s for s in SEGMENTS if s[0] <= h < s[1]), SEGMENTS[-1])
        phase, target = seg[2], seg[3]
        tau = TAU_H.get(phase, 1.0)
        temp += (target - temp) * (1 - math.exp(-STEP_H / tau)) + rnd.uniform(-0.15, 0.15)
        out.append({
            "h": h, "t": round(temp, 2), "phase": phase,
            "new_phase": phase != prev_phase,
            "check": any(abs(h - c) < 1e-6 for c in CHECK_HOURS),
            "lid": any(abs(h - l) < 1e-6 for l in LID_HOURS),
        })
        prev_phase = phase
    return out


if __name__ == "__main__":
    tr = build()
    below0 = sum(1 for p in tr if p["t"] <= 0)
    above8 = sum(1 for p in tr if p["t"] > 8)
    print(f"{len(tr)} points over {TOTAL_H:.0f} h | below 0 °C: {below0 * STEP_H:.2f} h | above 8 °C: {above8 * STEP_H:.2f} h")
    for p in tr:
        if p["check"]:
            print(f"  manual logbook check at h{p['h']:5.2f}: {p['t']:.1f} °C")
