"""Machine-learning temperature forecaster, trained and tested on REAL hospital fridge data.

Task: from the last 10 minutes of a fridge's readings, predict its temperature 10 minutes ahead.
Data: Zenodo 10.5281/zenodo.22101912 (two 2-8 °C fridges, 1-minute DS18B20 readings, door + humidity
+ room temperature), ~10,900 readings over 4 days.
Split: by time. Each fridge's first 75% is training, the last 25% (about a day) is the unseen test.

Models compared on the test set:
  persistence      the temperature 10 min from now = the temperature now
  trend line       our rule-based early-warning extrapolation (behavior.py)
  ridge            linear regression on the same features
  XGBoost          gradient-boosted trees on the same features

Run:  python model/ml_forecast.py      (writes data/ml_report.json and dashboard/ml_model.js)
XGBoost and m2cgen are installed in D:/vialguard-pylib because drive C: is nearly full.
"""
import json
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/vialguard-pylib")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
CSV = os.path.join(ROOT, "data", "zenodo_hospital_coldchain.csv")
HORIZON = 10            # minutes ahead
LAGS = [0, 1, 2, 3, 5, 10]
FEATURES = [f"t_lag{l}" for l in LAGS] + ["d1", "d3", "d10", "roll_mean10", "roll_std10", "roll_max10",
                                         "hum", "hum_d3", "room", "door_recent", "tod_sin", "tod_cos"]


def load():
    cols = ["_id", "__v", "createdAt", "datetime", "doorStatus", "dsTemperature", "humidity", "time_slot", "username", "x1", "x2", "x3"]
    df = pd.read_csv(CSV, names=cols, skiprows=1)
    df = df[df.username.isin(["BLD26-001", "MHT2025-001"])].copy()
    df["ts"] = pd.to_datetime(df.datetime, utc=True)
    df["temp"] = pd.to_numeric(df.dsTemperature, errors="coerce")
    df["hum"] = pd.to_numeric(df.humidity, errors="coerce")
    df["room"] = pd.to_numeric(df.x2, errors="coerce")
    df["door"] = (df.doorStatus == "open").astype(int)
    out = []
    for node, g in df.groupby("username"):
        # one row per minute, gaps filled forward (sensor drops a reading now and then)
        g = g.set_index("ts").sort_index()[["temp", "hum", "room", "door"]]
        g = g.resample("1min").agg({"temp": "mean", "hum": "mean", "room": "mean", "door": "max"})
        g[["temp", "hum", "room"]] = g[["temp", "hum", "room"]].ffill()
        g["door"] = g["door"].fillna(0)
        g["room"] = g["room"].fillna(g["room"].median() if g["room"].notna().any() else 22.0)
        g["node"] = node
        out.append(g.dropna(subset=["temp", "hum"]))
    return out


def featurize(g):
    f = pd.DataFrame(index=g.index)
    for l in LAGS:
        f[f"t_lag{l}"] = g.temp.shift(l)
    f["d1"] = g.temp - g.temp.shift(1)
    f["d3"] = g.temp - g.temp.shift(3)
    f["d10"] = g.temp - g.temp.shift(10)
    r = g.temp.rolling(10)
    f["roll_mean10"], f["roll_std10"], f["roll_max10"] = r.mean(), r.std(), r.max()
    f["hum"] = g.hum
    f["hum_d3"] = g.hum - g.hum.shift(3)
    f["room"] = g.room
    f["door_recent"] = g.door.rolling(3).max()
    minutes = g.index.hour * 60 + g.index.minute
    f["tod_sin"], f["tod_cos"] = np.sin(2 * math.pi * minutes / 1440), np.cos(2 * math.pi * minutes / 1440)
    f["target"] = g.temp.shift(-HORIZON)
    return f.dropna()


def trend_forecast(row):
    """Same idea as BehaviorDetector.predict_breach: line through the last 4 readings, extrapolated."""
    xs = np.array([-3, -2, -1, 0], dtype=float)
    ys = np.array([row["t_lag3"], row["t_lag2"], row["t_lag1"], row["t_lag0"]])
    slope = np.polyfit(xs, ys, 1)[0]
    return row["t_lag0"] + slope * HORIZON


def metrics(y, p):
    err = np.abs(y - p)
    hot = y > 8.0
    hits = int(((p > 8.0) & hot).sum())
    false_alarms = int(((p > 8.0) & ~hot).sum())
    return {"mae": round(float(err.mean()), 3), "p90_error": round(float(np.quantile(err, .9)), 3),
            "excursions": int(hot.sum()), "excursions_caught": hits, "false_alarms": false_alarms}


def main():
    import xgboost as xgb
    from sklearn.linear_model import Ridge

    train, test = [], []
    for g in load():
        f = featurize(g)
        cut = int(len(f) * 0.75)
        train.append(f.iloc[:cut]); test.append(f.iloc[cut:])
    tr, te = pd.concat(train), pd.concat(test)
    Xtr, ytr, Xte, yte = tr[FEATURES], tr.target.values, te[FEATURES], te.target.values

    preds = {
        "persistence": te.t_lag0.values,
        "trend_line": te.apply(trend_forecast, axis=1).values,
    }
    ridge = Ridge(alpha=1.0).fit(Xtr, ytr)
    preds["ridge"] = ridge.predict(Xte)
    # predict the CHANGE over 10 min, then add the current temperature: easier for trees to learn
    model = xgb.XGBRegressor(n_estimators=120, max_depth=3, learning_rate=0.08, subsample=0.9,
                             colsample_bytree=0.9, min_child_weight=5, random_state=7,
                             base_score=0.0)   # explicit: m2cgen cannot read XGBoost 3 default intercept
    model.fit(Xtr, ytr - Xtr.t_lag0.values)
    preds["xgboost"] = Xte.t_lag0.values + model.predict(Xte)

    report = {"dataset": "zenodo.22101912", "horizon_min": HORIZON, "train_rows": len(tr), "test_rows": len(te),
              "split": "time-based, last 25% of each fridge held out", "features": FEATURES,
              "models": {k: metrics(yte, v) for k, v in preds.items()}}
    imp = sorted(zip(FEATURES, model.feature_importances_.tolist()), key=lambda x: -x[1])[:6]
    report["xgboost_top_features"] = [[k, round(v, 3)] for k, v in imp]
    with open(os.path.join(ROOT, "data", "ml_report.json"), "w") as f:
        json.dump(report, f, indent=2)

    # export the trained trees to plain JavaScript so the phone version runs the same model
    import m2cgen as m2c
    js = m2c.export_to_javascript(model, function_name="vgXgbDelta")
    with open(os.path.join(ROOT, "dashboard", "ml_model.js"), "w") as f:
        f.write("// Generated by model/ml_forecast.py from XGBoost trained on real hospital fridge data (Zenodo 22101912).\n")
        f.write("// Input order: " + ", ".join(FEATURES) + "\n")
        f.write("// Output: predicted temperature CHANGE over the next %d minutes (°C).\n" % HORIZON)
        f.write(js)
        f.write("\nif (typeof module !== 'undefined') module.exports = { vgXgbDelta, FEATURES: %s };\n" % json.dumps(FEATURES))
    # sample rows to check the JS export gives the same numbers
    sample = Xte.iloc[::max(1, len(Xte) // 25)].head(25)
    json.dump({"X": sample.values.round(6).tolist(), "y": model.predict(sample).round(6).tolist()},
              open(os.path.join(ROOT, "tests", "ml_parity.json"), "w"))

    print(f"train {len(tr)} rows, test {len(te)} rows (unseen last 25% of each fridge), horizon {HORIZON} min")
    print(f"{'model':<12} {'MAE °C':>7} {'p90 err':>8} {'>8°C caught':>12} {'false alarms':>13}")
    for k, m in report["models"].items():
        print(f"{k:<12} {m['mae']:>7} {m['p90_error']:>8} {str(m['excursions_caught']) + '/' + str(m['excursions']):>12} {m['false_alarms']:>13}")
    print("top XGBoost features:", report["xgboost_top_features"])


if __name__ == "__main__":
    main()
