"""Shared settings for the streaming pipeline (Kafka -> stream processor -> InfluxDB).

Kafka and InfluxDB run natively from D:/vialguard-infra (see pipeline/README.md).
Credentials live outside the repo in D:/vialguard-infra/influx.env; environment variables override them.
"""
import os
import sys

for extra in ("D:/vialguard-pylib", "D:/vialguard-torch"):
    if os.path.isdir(extra) and extra not in sys.path:
        sys.path.append(extra)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "simulator"))

ENV_FILE = os.environ.get("VG_INFLUX_ENV", "D:/vialguard-infra/influx.env")
_env = {}
if os.path.exists(ENV_FILE):
    for line in open(ENV_FILE, encoding="utf-8"):
        if "=" in line:
            k, v = line.strip().split("=", 1)
            _env[k] = v

KAFKA = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092")
TOPIC = os.environ.get("KAFKA_TOPIC", "vialguard.readings")
INFLUX_URL = os.environ.get("INFLUX_URL", _env.get("INFLUX_URL", "http://127.0.0.1:8086"))
INFLUX_ORG = os.environ.get("INFLUX_ORG", _env.get("INFLUX_ORG", "vialguard"))
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", _env.get("INFLUX_BUCKET", "vialguard"))
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", _env.get("INFLUX_TOKEN", ""))
