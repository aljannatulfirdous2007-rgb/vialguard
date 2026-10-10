"""LSTM temperature forecaster on the SAME task, data and split as model/ml_forecast.py.

Input: the last 30 one-minute readings of a fridge, each [temp, humidity, room temp, door, sin(time), cos(time)].
Output: temperature change over the next 10 minutes.
Split: first 75% of each fridge for training (last tenth of that for early stopping), last 25% unseen test.
The result is added to data/ml_report.json next to persistence, trend line, ridge and XGBoost.

Run:  python model/lstm_forecast.py     (PyTorch is installed in D:/vialguard-torch)
"""
import json
import math
import os
import sys

import numpy as np

for extra in ("D:/vialguard-torch", "D:/vialguard-pylib"):
    if os.path.isdir(extra) and extra not in sys.path:
        sys.path.append(extra)
import torch
from torch import nn

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
WINDOW, HORIZON = 30, 10
CHANNELS = ["temp", "hum", "room", "door", "tod_sin", "tod_cos"]


class Net(nn.Module):
    def __init__(self, n_in=len(CHANNELS), hidden=32):
        super().__init__()
        self.lstm = nn.LSTM(n_in, hidden, batch_first=True)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.head(out[:, -1]).squeeze(-1)


def frame(g):
    """Per-minute channels for one fridge (same resampling as ml_forecast.load)."""
    minutes = g.index.hour * 60 + g.index.minute
    m = np.stack([g.temp.values, g.hum.values, g.room.values, g.door.values,
                  np.sin(2 * math.pi * minutes / 1440), np.cos(2 * math.pi * minutes / 1440)], axis=1).astype(np.float32)
    return m


def windows(m, start, end):
    """Sequences ending at index i in [start, end), target = temp[i + H] - temp[i]."""
    X, y, now = [], [], []
    for i in range(max(start, WINDOW - 1), min(end, len(m) - HORIZON)):
        X.append(m[i - WINDOW + 1:i + 1]); y.append(m[i + HORIZON, 0] - m[i, 0]); now.append(m[i, 0])
    return np.array(X), np.array(y, dtype=np.float32), np.array(now, dtype=np.float32)


class Loaded:
    """Inference wrapper used by pipeline/stream_processor.py."""
    window = WINDOW

    def __init__(self, net, mu, sd):
        self.net, self.mu, self.sd = net.eval(), mu, sd

    def predict_delta(self, hist):
        import time
        rows = []
        for p in hist[-WINDOW:]:
            g = time.gmtime(p["ts"])
            mins = g.tm_hour * 60 + g.tm_min
            rows.append([p["temp"], p["hum"], p["room"], p["door"], math.sin(2 * math.pi * mins / 1440), math.cos(2 * math.pi * mins / 1440)])
        x = (np.array([rows], dtype=np.float32) - self.mu) / self.sd
        with torch.no_grad():
            return float(self.net(torch.from_numpy(x))[0])


def load(path):
    ck = torch.load(path, weights_only=False)
    net = Net(); net.load_state_dict(ck["state"])
    return Loaded(net, ck["mu"], ck["sd"])


def main():
    import ml_forecast
    torch.manual_seed(7); np.random.seed(7)
    trX, trY, teX, teY, teNow = [], [], [], [], []
    for g in ml_forecast.load():
        m = frame(g)
        f = ml_forecast.featurize(g)                  # identical test rows to the XGBoost benchmark
        cut_time = f.index[int(len(f) * 0.75)]
        cut = int(np.searchsorted(g.index.values, cut_time.to_datetime64()))
        a, b, _ = windows(m, 0, cut)
        c, d, now = windows(m, cut, len(m))
        trX.append(a); trY.append(b); teX.append(c); teY.append(d); teNow.append(now)
    trX, trY = np.concatenate(trX), np.concatenate(trY)
    teX, teY, teNow = np.concatenate(teX), np.concatenate(teY), np.concatenate(teNow)
    mu = trX.reshape(-1, len(CHANNELS)).mean(0); sd = trX.reshape(-1, len(CHANNELS)).std(0) + 1e-6
    norm = lambda x: torch.from_numpy(((x - mu) / sd).astype(np.float32))
    n_val = len(trX) // 10
    Xt, yt, Xv, yv = norm(trX[:-n_val]), torch.from_numpy(trY[:-n_val]), norm(trX[-n_val:]), torch.from_numpy(trY[-n_val:])
    net = Net()
    opt = torch.optim.Adam(net.parameters(), lr=3e-3)
    loss_fn = nn.SmoothL1Loss(beta=0.5)
    best, best_state, patience = 1e9, None, 0
    for epoch in range(40):
        net.train()
        perm = torch.randperm(len(Xt))
        for i in range(0, len(Xt), 256):
            idx = perm[i:i + 256]
            opt.zero_grad(); loss = loss_fn(net(Xt[idx]), yt[idx]); loss.backward(); opt.step()
        net.eval()
        with torch.no_grad():
            v = float((net(Xv) - yv).abs().mean())
        if v < best - 1e-4:
            best, best_state, patience = v, {k: t.clone() for k, t in net.state_dict().items()}, 0
        else:
            patience += 1
            if patience >= 6:
                break
    net.load_state_dict(best_state)
    net.eval()
    with torch.no_grad():
        pred = teNow + net(norm(teX)).numpy()
    actual = teNow + teY
    err = np.abs(actual - pred)
    res = {"mae": round(float(err.mean()), 3), "p90_error": round(float(np.quantile(err, .9)), 3),
           "excursions": int((actual > 8).sum()), "excursions_caught": int(((pred > 8) & (actual > 8)).sum()),
           "false_alarms": int(((pred > 8) & (actual <= 8)).sum()), "epochs": epoch + 1, "test_rows": int(len(actual))}
    torch.save({"state": net.state_dict(), "mu": mu, "sd": sd}, os.path.join(ROOT, "data", "ml_lstm.pt"))
    rp = os.path.join(ROOT, "data", "ml_report.json")
    report = json.load(open(rp))
    report["models"]["lstm"] = res
    json.dump(report, open(rp, "w"), indent=2)
    print(f"LSTM: {len(trX)} training sequences, {len(actual)} test, stopped after {epoch + 1} epochs")
    for k, mres in report["models"].items():
        print(f"  {k:<12} MAE {mres['mae']:.3f} °C   false alarms {mres['false_alarms']}")


if __name__ == "__main__":
    main()
