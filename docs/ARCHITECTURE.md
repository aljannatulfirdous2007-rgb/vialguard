# Architecture

## What runs today (prototype)

```
 ESP32 box / simulator / real-data replay
        │  JSON readings, every reading SHA-256 chained
        ▼
 Firebase RTDB REST  ─or─  simulator/local_server.py  ─or─  in-browser store (pocket mode)
        │
        ▼
 Engine (Python reference, JS port with parity tests)
   ├─ Arrhenius viability + MKT            model/vialguard_model.py
   ├─ Behaviour detector + root cause       model/behavior.py
   ├─ Breach early warning (trend line)     model/behavior.py   (transit box)
   ├─ XGBoost 10-min forecaster             model/ml_forecast.py (fridges, trained on real data)
   └─ Arrival forecast + re-routing         model/forecast.py   (live Open-Meteo weather)
        │
        ▼
 HUD dashboard · Ask VialGuard (tool-calling assistant) · Telegram alert ladder · route map
```

## Production design (not run in the demo)

```
 Boxes ──MQTT──▶ Mosquitto / HiveMQ ──▶ Kafka topic "readings" ──┬─▶ InfluxDB (time series, 1 row per reading)
                                                                  ├─▶ Engine workers (same code as today, one per partition)
                                                                  └─▶ Audit store (append-only hash chain, Postgres)
 Engine workers ──▶ Kafka topic "verdicts" ──▶ alerts (Telegram / SMS / WhatsApp Business), dashboards, agent tools
```

| Production piece | Why it is needed at scale | Prototype stand-in |
|---|---|---|
| MQTT broker | thousands of boxes on flaky 2G links | HTTP REST push with on-device buffer |
| Kafka | replay, back-pressure, many consumers per reading | single process loop |
| InfluxDB | months of minute-level data per box, fast range queries | Firebase RTDB / in-memory |
| Engine workers | horizontal scaling | one engine instance |

### Why Kafka and InfluxDB are not running in the expo build

1. **Nothing visible changes.** Judges see the same verdicts whether readings pass through Kafka or a function call.
2. **Risk.** The demo laptop has under 0.5 GB free on C:. Kafka + Zookeeper/KRaft + InfluxDB in Docker need several GB.
3. **The engine is already message-shaped.** Every reading is a self-contained JSON record with its own hash, so moving to Kafka is a transport change, not a rewrite.

## Machine learning: what we tested and what we found

`python model/ml_forecast.py`, 10-minute-ahead fridge temperature, trained on 8,316 real readings,
tested on 2,773 unseen ones (last 25% of each fridge, time-based split):

| Model | Mean abs. error | False alarms |
|---|---|---|
| Persistence (no change) | 0.33 °C | 0 |
| Our trend-line rule | 1.88 °C | 261 |
| Ridge regression | 0.16 °C | 0 |
| **XGBoost** | **0.14 °C** | **0** |

The trend-line rule works for a cold box warming smoothly in a truck, but it overshoots on real fridge
compressor cycles and door spikes. So the dashboard uses XGBoost for fridge data and the trend line only
for the transit box. The test period contained no real excursion above 8 °C, so we report accuracy and false
alarms, not a breach catch rate.

Why not an LSTM: four days of data from two fridges is far too little for a recurrent network; gradient-boosted
trees are the standard choice for small tabular data and beat a linear baseline here.

## Ask VialGuard

Rule-based intent router plus six tools (`query_telemetry`, `search_compliance_sop`, `forecast_arrival`,
`find_alternatives`, `diagnose_cause`, `audit_log`). Answers are built only from tool results, and every tool call
is shown. No language model is involved, so it runs offline and cannot invent a medical answer. A language model
could be added as the router later without changing the tools.
