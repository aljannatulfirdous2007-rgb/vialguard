"""Kafka -> stream processor -> InfluxDB.

For every reading on the topic:
  1. verify the hash chain for that device (prev must equal the last hash seen, hash must recompute)
  2. update per-device 1-minute history and run the ML forecasters (XGBoost, and LSTM if trained)
  3. write to InfluxDB:
       reading   tags: device, source     fields: temp, life_pct, lid, seq, chain_ok
       forecast  tags: device, model      fields: temp_10m, horizon_min
       event     tags: device, kind       fields: value (1)
Run:  python pipeline/stream_processor.py            (Ctrl+C to stop)
      python pipeline/stream_processor.py --idle-exit 20   exit after 20 s without messages (tests)
"""
import argparse
import json
import math
import os
import time
from collections import defaultdict, deque

import config
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import WriteOptions
from kafka import KafkaConsumer
from vialguard_model import GENESIS, record_hash

FEATURES = ["t_lag0", "t_lag1", "t_lag2", "t_lag3", "t_lag5", "t_lag10", "d1", "d3", "d10", "roll_mean10", "roll_std10",
            "roll_max10", "hum", "hum_d3", "room", "door_recent", "tod_sin", "tod_cos"]


def load_models():
    models = {}
    xgb_path = os.path.join(config.ROOT, "data", "ml_xgb.json")
    if os.path.exists(xgb_path):
        import xgboost as xgb
        m = xgb.XGBRegressor()
        m.load_model(xgb_path)
        models["xgboost"] = m
    lstm_path = os.path.join(config.ROOT, "data", "ml_lstm.pt")
    if os.path.exists(lstm_path):
        try:
            import lstm_forecast
            models["lstm"] = lstm_forecast.load(lstm_path)
        except Exception as e:
            print("LSTM not loaded:", e)
    return models


def features(h):
    """Same features as model/ml_forecast.py, from the last >= 11 one-minute readings."""
    t = [p["temp"] for p in h]
    T = lambda l: t[-1 - l]
    last10 = t[-10:]
    mean = sum(last10) / 10
    std = math.sqrt(sum((x - mean) ** 2 for x in last10) / 9)
    ts = time.gmtime(h[-1]["ts"])
    mins = ts.tm_hour * 60 + ts.tm_min
    return [T(0), T(1), T(2), T(3), T(5), T(10), T(0) - T(1), T(0) - T(3), T(0) - T(10), mean, std, max(last10),
            h[-1]["hum"], h[-1]["hum"] - h[-4]["hum"], h[-1]["room"], max(p["door"] for p in h[-3:]),
            math.sin(2 * math.pi * mins / 1440), math.cos(2 * math.pi * mins / 1440)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-beginning", action="store_true")
    ap.add_argument("--idle-exit", type=float, default=0, help="exit after this many seconds without a message")
    ap.add_argument("--group", default="vialguard-processor", help="Kafka consumer group (a new name re-reads the topic)")
    a = ap.parse_args()
    models = load_models()
    print("models loaded:", ", ".join(models) or "none (run model/ml_forecast.py)")
    consumer = KafkaConsumer(config.TOPIC, bootstrap_servers=config.KAFKA, group_id=a.group,
                             auto_offset_reset="earliest" if a.from_beginning else "latest",
                             value_deserializer=lambda b: json.loads(b), consumer_timeout_ms=1000)
    client = InfluxDBClient(url=config.INFLUX_URL, token=config.INFLUX_TOKEN, org=config.INFLUX_ORG)
    write = client.write_api(write_options=WriteOptions(batch_size=500, flush_interval=1000))
    last_hash = defaultdict(lambda: GENESIS)
    hist = defaultdict(lambda: deque(maxlen=30))
    n = broken = forecasts = 0
    last_msg = time.time()
    try:
        while True:
            got = False
            for m in consumer:
                got, last_msg = True, time.time()
                r, dev = m.value, m.value["device"]
                ok = r["prev"] == last_hash[dev] and record_hash(r["seq"], r["ts"], r["t"], r["b"], r["lid"], r["ev"], r["prev"]) == r["h"]
                if not ok:
                    broken += 1
                last_hash[dev] = r["h"]
                temp = r["t"] / 100
                p = (Point("reading").tag("device", dev).tag("source", r.get("source", "device"))
                     .field("temp", temp).field("life_pct", r["b"] / 100).field("lid", int(r["lid"]))
                     .field("seq", int(r["seq"])).field("chain_ok", int(ok)).time(r["ts"], WritePrecision.S))
                write.write(bucket=config.INFLUX_BUCKET, record=p)
                for ev in filter(None, r.get("ev", "").split(",")):
                    write.write(bucket=config.INFLUX_BUCKET, record=Point("event").tag("device", dev).tag("kind", ev)
                                .field("value", 1).time(r["ts"], WritePrecision.S))
                if r.get("hum") is not None and r.get("room") is not None:
                    hist[dev].append({"ts": r["ts"], "temp": temp, "hum": r["hum"], "room": r["room"], "door": int(r["lid"])})
                    h = list(hist[dev])
                    # models were trained on 2-8 °C fridges only: do not forecast a -30 °C freezer (out of distribution)
                    if len(h) >= 11 and models and temp > -5:
                        x = features(h)
                        if "xgboost" in models:
                            f = temp + float(models["xgboost"].predict([x])[0])
                            write.write(bucket=config.INFLUX_BUCKET, record=Point("forecast").tag("device", dev).tag("model", "xgboost")
                                        .field("temp_10m", round(f, 3)).field("horizon_min", 10).time(r["ts"], WritePrecision.S))
                            forecasts += 1
                        if "lstm" in models and len(h) >= models["lstm"].window:
                            f = temp + models["lstm"].predict_delta(h)
                            write.write(bucket=config.INFLUX_BUCKET, record=Point("forecast").tag("device", dev).tag("model", "lstm")
                                        .field("temp_10m", round(f, 3)).field("horizon_min", 10).time(r["ts"], WritePrecision.S))
                            forecasts += 1
                n += 1
                if n % 2000 == 0:
                    print(f"processed {n} readings, chain breaks {broken}, forecasts {forecasts}")
            if not got and a.idle_exit and time.time() - last_msg > a.idle_exit:
                break
    except KeyboardInterrupt:
        pass
    finally:
        write.close()
        client.close()
        consumer.close()
        print(f"stopped: {n} readings -> InfluxDB, {forecasts} forecasts, {broken} hash-chain breaks")


if __name__ == "__main__":
    main()
