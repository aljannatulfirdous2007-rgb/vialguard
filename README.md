# VialGuard

**A ₹990 smart cold-chain package that tells you whether the medicine inside still works, not just what temperature it was.**

Team Lipgloss & Linux · BioByte 2K26 · TRK-01 Cold Chain Guardian

| Part | Folder | What it does |
|---|---|---|
| Viability model | `model/` | Arrhenius stability budget, freeze logic, MKT, hash chain. Reference implementation + 15 tests |
| Firmware | `firmware/vialguard/` | ESP32: reads the probe and lid sensor, runs the model, OLED, buzzer, Telegram alert ladder, Firebase sync, offline buffer |
| Dashboard | `dashboard/` | Live verdict, budget gauge, journey chart, alert ladder with Acknowledge, event log, tamper check |
| Simulator | `simulator/` | Acts exactly like the device (no hardware needed) + a local stand-in for Firebase (no internet needed) |
| Docs | `docs/` | `WIRING.md`, `DEMO_SCRIPT.md` |

## How it works

1. **Log:** the DS18B20 probe is read every 10 s (2 s in demo mode). An LDR detects the lid opening.
2. **Predict:** each drug has a *stability budget*: hours it may spend above 8 °C, measured at 25 °C.
   Above 8 °C the budget drains at `k(T) = exp(-(Ea/R)·(1/T − 1/298.15))`, so hotter means faster.
   For freeze-sensitive drugs, two readings at or below 0 °C mean **DISCARD**, because freezing is irreversible.
3. **Alert:** Telegram to the courier → pharmacist after 5 min → QA head after 15 min (30 s / 60 s in demo),
   until someone sends `/ack`, presses the BOOT button, or clicks Acknowledge on the dashboard.
4. **Prove:** every reading is SHA-256 chained to the previous one. Editing any record shows **TAMPERED**.

Drug profiles (`demo`, `insulin`, `vvm7`, `vvm2`) are illustrative. VVM classes follow WHO vaccine vial
monitor definitions; a real deployment calibrates them from manufacturer stability data.

---

## Expo demo (software only, one click)

Double-click **`start_demo.bat`**. It opens the dashboard and starts a virtual shipment. Drive it with the
**SIMULATION CONTROL** buttons: Cold box, Left out, Truck stall, Ice pack, Open lid, Fake a record.
Press **A** (or the red button) to acknowledge alerts. No internet, Firebase or hardware needed.

## Predict, detect, act (and the real data behind it)

| Layer | What it does | Data | Honest status |
|---|---|---|---|
| **Viability** | Arrhenius stability budget per drug, freeze = discard, MKT | WHO VVM classes, insulin label data | Physics, 15 unit tests |
| **Arrival forecast** | Predicts medicine life on arrival: probe trend + **live outdoor temperature** for the route, Newton cooling of the box | **Open-Meteo** live forecast (cached offline) | Box time constant (4 h) is a stated assumption |
| **Custody integrity** | Detects box openings and cooling failure from the temperature signature alone, custody score | Calibrated on **real hospital fridge data**, [Zenodo 10.5281/zenodo.22101912](https://doi.org/10.5281/zenodo.22101912) (CC BY 4.0) | Catches **7 of 9** labelled door openings in the replay window (10 of 17 over all 4 days); extra detections have the same physical signature but no label |
| **Recommended action** | If it will not arrive usable: divert to the closest hospital it can still reach with life left, auto-draft a replacement order | Route + facility list | Facility coordinates are illustrative demo data |
| **XGBoost forecaster** | 10-min-ahead fridge temperature, early warning above 8 °C | Trained on 8,316 real readings, tested on 2,773 unseen | MAE 0.14 °C vs 0.33 °C for "no change"; 0 false alarms; test period had no real excursion |
| **Ask VialGuard** | Tool-calling assistant with visible trace | Live engine state | Rule-based router, cannot invent answers |
| **Field trace replay** | 72 h last-mile journey: power cut, road heat, overnight PHC freeze missed by the manual logbook | Reconstructed from published Indian cold-chain findings | Not raw data; labelled as reconstructed |

Reproduce the detector numbers: `python model/calibrate_behavior.py`  
Reproduce the ML benchmark: `python model/ml_forecast.py` (needs xgboost, m2cgen)  
Architecture and production design (MQTT, Kafka, InfluxDB): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Quick start without hardware (5 minutes)

Needs Python 3 only.

```bash
python simulator/simulate.py --local
```

Open **http://localhost:8787/dashboard/**, then type in the terminal (each followed by Enter):

| Key | Does |
|---|---|
| `h` | heat (hair dryer) |
| `c` | back into the cold box |
| `f` | freeze (ice pack) |
| `o` | open/close lid |
| `a` | acknowledge alert |
| `d insulin` | switch drug (starts a new shipment) |

Scripted 2-minute demo: `python simulator/simulate.py --local --scenario truck`

## Setup with hardware

### 1. Firebase (free, 5 minutes)
1. https://console.firebase.google.com → **Add project** (Analytics not needed).
2. **Build → Realtime Database → Create database** → pick Singapore (asia-southeast1) → **Start in test mode**.
3. Copy the database URL, e.g. `https://vialguard-xxxx-default-rtdb.asia-southeast1.firebasedatabase.app`.

Test mode expires after 30 days and anyone with the URL can write. That's fine for the expo, but don't
put real patient data in it.

### 2. Telegram alerts (optional, 5 minutes)
1. In Telegram, message **@BotFather** → `/newbot` → copy the token.
2. Each person (courier / pharmacist / QA) opens your bot and presses **Start**.
3. Get each person's chat ID from **@userinfobot**. For a demo, all three can be the same phone.

### 3. Flash the ESP32
1. Install **Arduino IDE 2**. In Boards Manager, install **esp32 by Espressif**.
   Library Manager: install **OneWire**, **DallasTemperature**, **Adafruit SSD1306**, **Adafruit GFX Library**.
2. Copy `firmware/vialguard/config.example.h` to `config.h` and fill in Wi-Fi, Firebase URL and Telegram.
3. Wire it up: `docs/WIRING.md`.
4. Board: **ESP32 Dev Module**, choose the COM port, Upload. If no COM port appears, install the
   CP2102 or CH340 USB driver (look at the chip next to the USB port) and use a data USB cable.
5. Open Serial Monitor at 115200 baud: you should see `VialGuard 1.0 ready` and a status line every 2 s.

### 4. Dashboard
Open `dashboard/index.html` in Chrome, click ⚙, paste the Firebase URL, device ID `vg-001`.
To share a link, deploy the `dashboard/` folder to Vercel or GitHub Pages and add
`?db=<firebase-url>&device=vg-001` to the URL.

## No internet at the venue

Laptop and ESP32 on the same phone hotspot:

```bash
python simulator/local_server.py
```

In `config.h` set `FIREBASE_URL "http://<laptop-ip>:8787"` (find the IP with `ipconfig`) and flash.
Dashboard: `http://localhost:8787/dashboard/`. Telegram needs internet, so alerts then show only
on the dashboard, OLED and buzzer.

## Tests

```bash
npm test      # JS hash chain vs Python reference + 15 model tests
```

Real-world settings: in `config.h` set `TIME_SCALE 1`, `SAMPLE_MS 10000`,
`ESCALATE_PHARMACIST_SEC 300`, `ESCALATE_QA_SEC 900`.
