"""Publish readings to Kafka.

  python pipeline/producer.py --real            stream the real Zenodo hospital data (3 fridges, real timestamps)
  python pipeline/producer.py --real --speed 600   600x faster than real time (default)

Each message is one reading, keyed by device, so all readings of a device land in the same partition
and stay in order (the stream processor relies on that to verify the hash chain).
Every message is sealed exactly like the ESP32 does: SHA-256 of the reading plus the previous hash.
"""
import argparse
import csv
import json
import os
import time
from datetime import datetime

import config
from kafka import KafkaProducer
from vialguard_model import GENESIS, record_hash


def real_rows():
    path = os.path.join(config.ROOT, "data", "zenodo_hospital_coldchain.csv")
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for x in r:
            if not x[5]:
                continue
            rows.append({
                "device": x[8], "ts": int(datetime.fromisoformat(x[3].replace("Z", "+00:00")).timestamp()),
                "temp": float(x[5]), "door": 1 if x[4] == "open" else 0,
                "hum": float(x[6]) if x[6] else None, "room": float(x[10]) if len(x) > 10 and x[10] else None,
            })
    rows.sort(key=lambda p: p["ts"])
    return rows


def make_producer():
    return KafkaProducer(bootstrap_servers=config.KAFKA, key_serializer=str.encode,
                         value_serializer=lambda v: json.dumps(v).encode(), linger_ms=20, acks=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="stream the real Zenodo dataset")
    ap.add_argument("--speed", type=float, default=600, help="playback speed vs real time (0 = as fast as possible)")
    ap.add_argument("--limit", type=int, default=0, help="stop after N messages")
    a = ap.parse_args()
    if not a.real:
        ap.error("use --real")
    prod = make_producer()
    prev, seq = {}, {}
    rows = real_rows()
    t0_data, t0_wall = rows[0]["ts"], time.time()
    sent = 0
    for r in rows:
        if a.speed > 0:
            wait = (r["ts"] - t0_data) / a.speed - (time.time() - t0_wall)
            if wait > 0:
                time.sleep(wait)
        d = r["device"]
        s = seq.get(d, 0)
        msg = {"device": d, "source": "zenodo", "seq": s, "ts": r["ts"], "t": round(r["temp"] * 100), "b": 10000,
               "lid": r["door"], "ev": "DOOR_LABEL" if r["door"] else "", "prev": prev.get(d, GENESIS),
               "hum": r["hum"], "room": r["room"]}
        msg["h"] = record_hash(msg["seq"], msg["ts"], msg["t"], msg["b"], msg["lid"], msg["ev"], msg["prev"])
        prod.send(config.TOPIC, key=d, value=msg)
        prev[d], seq[d] = msg["h"], s + 1
        sent += 1
        if sent % 2000 == 0:
            print(f"sent {sent} readings (data time {datetime.utcfromtimestamp(r['ts']):%Y-%m-%d %H:%M} UTC)")
        if a.limit and sent >= a.limit:
            break
    prod.flush()
    print(f"done: {sent} readings published to Kafka topic {config.TOPIC}")


if __name__ == "__main__":
    main()
