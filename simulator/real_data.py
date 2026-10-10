"""Real hospital fridge data for replay (Zenodo 10.5281/zenodo.22101912, CC BY 4.0).

Picks the 24-hour window of the BLD26-001 fridge with the most labelled door openings,
at its native 1-minute resolution. Door labels are used ONLY to score the detector on screen;
the detector itself never sees them.
"""
import csv
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "..", "data", "zenodo_hospital_coldchain.csv")
NODE = "BLD26-001"
WINDOW_H = 24


def load_window():
    rows = []
    with open(CSV, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for x in r:
            if x[8] != NODE or not x[5]:
                continue
            ts = int(datetime.fromisoformat(x[3].replace("Z", "+00:00")).timestamp())
            ambient = float(x[10]) if len(x) > 10 and x[10] else None
            rows.append({"ts": ts, "t": float(x[5]), "open": x[4] == "open", "hum": float(x[6]) if x[6] else None, "amb": ambient})
    rows.sort(key=lambda p: p["ts"])
    opens = [p["ts"] for p in rows if p["open"]]
    best_start, best_n = rows[0]["ts"], -1
    for s in opens:                      # windows that start shortly before an opening
        start = s - 1800
        n = sum(1 for o in opens if start <= o < start + WINDOW_H * 3600)
        if n > best_n:
            best_start, best_n = start, n
    win = [p for p in rows if best_start <= p["ts"] < best_start + WINDOW_H * 3600]
    return win


if __name__ == "__main__":
    w = load_window()
    print(len(w), "readings", sum(p["open"] for p in w), "labelled openings",
          datetime.utcfromtimestamp(w[0]["ts"]), "->", datetime.utcfromtimestamp(w[-1]["ts"]),
          "temp", min(p["t"] for p in w), max(p["t"] for p in w))
