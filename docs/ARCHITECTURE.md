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

## Streaming pipeline (runs locally: `pipeline/`)

```
 producer.py (real data) ──▶ Kafka 4.3 (KRaft) topic vialguard.readings, 3 partitions keyed by device
                                   │
                                   ▼
                       stream_processor.py: verify hash chain · XGBoost + LSTM forecasts
                                   │
                                   ▼
                       InfluxDB 2.7 bucket vialguard: reading · forecast · event  ──▶ query.py / InfluxDB UI
```

Measured run (all 16,311 real readings from 3 devices, Zenodo dataset):

| Check | Result |
|---|---|
| Readings through Kafka into InfluxDB | 16,311 (BLD26-001 5,392 · MHT2025-001 5,448 · ULT26-001 5,471) |
| Hash-chain breaks detected | 0 |
| Door openings logged as events | 17 |
| Forecasts written (fridges only; the −30 °C freezer is outside what the models were trained on) | 17,398 |

Still production-only: an MQTT broker in front of Kafka for thousands of boxes on 2G links, more engine workers,
and an append-only audit store. The public website keeps running fully in the browser so it works without servers.

## Machine learning: what we tested and what we found

`python model/ml_forecast.py`, 10-minute-ahead fridge temperature, trained on 8,316 real readings,
tested on 2,773 unseen ones (last 25% of each fridge, time-based split):

| Model | Mean abs. error | False alarms |
|---|---|---|
| Persistence (no change) | 0.33 °C | 0 |
| Our trend-line rule | 1.88 °C | 261 |
| Ridge regression | 0.16 °C | 0 |
| **XGBoost** | **0.14 °C** | **0** |
| LSTM (PyTorch) | 0.15 °C | 0 |

The trend-line rule works for a cold box warming smoothly in a truck, but it overshoots on real fridge
compressor cycles and door spikes. So the dashboard uses XGBoost for fridge data and the trend line only
for the transit box. The test period contained no real excursion above 8 °C, so we report accuracy and false
alarms, not a breach catch rate.

**LSTM** (`model/lstm_forecast.py`, PyTorch): same task, same unseen test rows, last 30 minutes as a sequence.
Result: MAE **0.153 °C**, 0 false alarms. Good, but XGBoost (0.144 °C) is still slightly better and far smaller,
which is expected with only four days of data. Both models run side by side in the stream processor; the dashboard
uses XGBoost.

## Ask VialGuard

Rule-based intent router plus six tools (`query_telemetry`, `search_compliance_sop`, `forecast_arrival`,
`find_alternatives`, `diagnose_cause`, `audit_log`). Answers are built only from tool results, and every tool call
is shown. No language model is involved, so it runs offline and cannot invent a medical answer. A language model
could be added as the router later without changing the tools.
