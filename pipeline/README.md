# Streaming pipeline: Kafka → stream processor → InfluxDB

```
producer.py (real Zenodo data, 3 devices) ──▶ Kafka topic vialguard.readings (3 partitions, keyed by device)
                                                        │
                                                        ▼
                                          stream_processor.py
                                            ├─ verifies each device's SHA-256 hash chain in order
                                            ├─ XGBoost + LSTM 10-minute forecasts (models trained on real data)
                                            └─ writes InfluxDB measurements: reading · forecast · event
                                                        │
                                                        ▼
                                          InfluxDB bucket "vialguard"  →  query.py / InfluxDB UI (localhost:8086)
```

## Run it

1. Double-click `pipeline\start_pipeline.bat` (starts Kafka 4.3 in KRaft mode, InfluxDB 2.7, the stream processor).
2. `python pipeline\producer.py --real` streams the real hospital data (600× real time; `--speed 0` = as fast as possible).
3. `python pipeline\query.py` reads the results back out of InfluxDB.

Everything is installed on `D:\vialguard-infra` (Kafka, InfluxDB, data) and `D:\vialguard-pylib`, `D:\vialguard-torch`
(Python packages), because drive C: is small. InfluxDB credentials are in `D:\vialguard-infra\influx.env` and never
committed.

## First-time setup on another machine

- Java 17+ for Kafka. Download Kafka 4.x, set `log.dirs` in `config/server.properties`, then
  `kafka-storage format --standalone -t <uuid> -c config/server.properties`.
- InfluxDB 2.x, then `POST /api/v2/setup` with org `vialguard`, bucket `vialguard`, and a token.
- `pip install kafka-python influxdb-client xgboost torch`.
- Create the topic: `kafka-topics --create --topic vialguard.readings --partitions 3 --bootstrap-server localhost:9092`.
