# Data

| File | Source | Licence |
|---|---|---|
| `zenodo_hospital_coldchain.csv` | Perez, I. B. K. L. K. (2026). *Multi-Node IoT Dataset for Real-Time Hospital Cold-Chain Monitoring*. Zenodo. https://doi.org/10.5281/zenodo.22101912 | CC BY 4.0 |
| `behavior_calibration.json` | Output of `python model/calibrate_behavior.py` on the file above | |
| `weather_cache.json` | Last Open-Meteo forecast (https://open-meteo.com), used when offline | CC BY 4.0 |

`dashboard/real_window.json` is the 24-hour window of fridge `BLD26-001` that the dashboard replays,
exported from the Zenodo file (timestamp, °C, door-open label).

The 72-hour field trace (`simulator/field_trace.py`) is **reconstructed**, not measured. It reproduces
deviation patterns reported in Murhekar et al., Bull. WHO 2013, and the 3-state Indian cold-chain study
(TDTMV 2020).
