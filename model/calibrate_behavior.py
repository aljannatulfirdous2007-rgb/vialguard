"""Calibrate and score the ACCESS detector on real labelled data.

Dataset: Zenodo record 10.5281/zenodo.22101912, "Multi-Node IoT Dataset for Real-Time
Hospital Cold-Chain Monitoring" (CC BY 4.0), file data/zenodo_hospital_coldchain.csv.
Ground truth = rows where doorStatus == "open" (17 openings on the 2-8 °C fridges).

A detection counts as correct if it fires within TOL_MIN minutes of a labelled opening.
Usage:  python model/calibrate_behavior.py
"""
import csv
import json
import os
from datetime import datetime

from behavior import BehaviorDetector

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "..", "data", "zenodo_hospital_coldchain.csv")
TOL_MIN = 4.0
FRIDGES = ("BLD26-001", "MHT2025-001")   # the 2-8 °C units (ULT26-001 is a -30 °C freezer)


def load():
    series = {}
    with open(CSV, newline="", encoding="utf-8") as f:
        rows = csv.reader(f)
        next(rows)
        for r in rows:
            node = r[8]
            if node not in FRIDGES:
                continue
            ts = datetime.fromisoformat(r[3].replace("Z", "+00:00")).timestamp()
            temp = float(r[5]) if r[5] else None
            series.setdefault(node, []).append((ts, temp, r[4] == "open"))
    for node in series:
        series[node].sort()
    return series


def score(series, jump_c, window_min):
    tp = fp = fn = 0
    for node, rows in series.items():
        t0 = rows[0][0]
        det = BehaviorDetector(jump_c=jump_c, window_min=window_min)
        hits, truth = [], []
        for ts, temp, is_open in rows:
            m = (ts - t0) / 60.0
            if is_open:
                truth.append(m)
            # lid state is NOT given to the detector: it must find openings from temperature alone
            if any(f.startswith("ACCESS") for f in det.update(m, temp, lid_open=False)):
                hits.append(m)
        matched = set()
        for h in hits:
            j = next((i for i, t in enumerate(truth) if i not in matched and abs(h - t) <= TOL_MIN), None)
            if j is None:
                fp += 1
            else:
                matched.add(j); tp += 1
        fn += len(truth) - len(matched)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"jump_c": jump_c, "window_min": window_min, "tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}


if __name__ == "__main__":
    series = load()
    n_open = sum(sum(1 for r in rows if r[2]) for rows in series.values())
    n_rows = sum(len(rows) for rows in series.values())
    print(f"real data: {n_rows} readings from {len(series)} fridges, {n_open} labelled door openings")
    grid = [score(series, j / 10, w) for j in range(5, 21) for w in (2.0, 3.0, 4.0)]
    best = max(grid, key=lambda r: (r["f1"], r["precision"]))
    for r in sorted(grid, key=lambda r: -r["f1"])[:6]:
        print(r)
    print("BEST:", best)
    with open(os.path.join(HERE, "..", "data", "behavior_calibration.json"), "w") as f:
        json.dump({"dataset": "zenodo.22101912", "readings": n_rows, "openings": n_open, "tolerance_min": TOL_MIN, "best": best}, f, indent=2)
