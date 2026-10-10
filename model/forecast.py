"""Predictive + prescriptive layer: "will it survive the trip, and if not, what do we do?"

1. Real ambient data: live hourly forecast for the route from Open-Meteo (free, keyless,
   https://open-meteo.com). Cached to data/weather_cache.json so the demo still works offline.
2. Box thermal model (Newton's law of cooling, the standard first-order model for an
   insulated container):  dT/dt = (T_ambient - T) / TAU_BOX_H   once cooling is compromised.
   While the cold box is working the inside is held at the current temperature.
3. The predicted temperature path goes through the SAME Arrhenius model as the live verdict,
   giving "medicine life at arrival".
4. Prescriptive step: if it will not arrive usable, pick the closest facility it can still
   reach with useful life left, and draft a replacement order for the original destination.

Assumptions are stated, not hidden: TAU_BOX_H and the facility list are demo parameters.
"""
import json
import math
import os
import time
import urllib.request

from vialguard_model import UPPER_LIMIT_C, rate

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "..", "data", "weather_cache.json")
TAU_BOX_H = 4.0                 # assumed thermal time constant of a passive vaccine carrier (demo parameter)
STEP_H = 0.1
ROAD_FACTOR, SPEED_KMH = 1.3, 40.0    # straight-line km -> road km; average speed of a vaccine van on NH48 / state roads
USABLE_PCT = 50.0               # below this the drug is "use soon", so it must still be above it on arrival

# Demo routing data: approximate locations of government hospitals on the Chennai -> Tiruvannamalai
# vaccine route (illustrative coordinates, not an official cold-chain point list)
FACILITIES = [
    {"id": "DVS", "name": "District Vaccine Store, Egmore", "lat": 13.0733, "lon": 80.2610, "cold_store": True},
    {"id": "SPK", "name": "PHC Sriperumbudur", "lat": 12.9675, "lon": 79.9419, "cold_store": True},
    {"id": "CGL", "name": "GH Chengalpattu", "lat": 12.6921, "lon": 79.9707, "cold_store": True},
    {"id": "KPM", "name": "GH Kanchipuram", "lat": 12.8342, "lon": 79.7036, "cold_store": True},
    {"id": "CYR", "name": "GH Cheyyar", "lat": 12.6620, "lon": 79.5433, "cold_store": True},
    {"id": "VDV", "name": "GH Vandavasi", "lat": 12.5040, "lon": 79.6193, "cold_store": True},
    {"id": "TVM", "name": "GH Tiruvannamalai", "lat": 12.2253, "lon": 79.0747, "cold_store": True},
]
ORIGIN = {"name": "District Vaccine Store, Egmore", "lat": 13.0733, "lon": 80.2610}
DEFAULT_DEST = "TVM"


# ---------------------------------------------------------------- real weather
def fetch_weather(lat=13.08, lon=80.27, timeout=6):
    """Hourly temperature forecast. Returns (dict, source) where source is 'live' or 'cache'."""
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
           "&hourly=temperature_2m&current=temperature_2m&forecast_days=2&timezone=Asia%%2FKolkata") % (lat, lon)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read())
        data["_fetched"] = int(time.time())
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w") as f:
            json.dump(data, f)
        return data, "live"
    except Exception:
        if os.path.exists(CACHE):
            with open(CACHE) as f:
                return json.load(f), "cache"
        return None, "none"


def ambient_series(weather, start_epoch, hours):
    """Ambient °C for each STEP_H step from start_epoch. Falls back to 32 °C (Chennai typical) without data."""
    n = max(1, int(math.ceil(hours / STEP_H)))
    if not weather:
        return [32.0] * n
    times = weather["hourly"]["time"]
    temps = weather["hourly"]["temperature_2m"]
    off = weather.get("utc_offset_seconds", 19800)
    epochs = [time.mktime(time.strptime(t, "%Y-%m-%dT%H:%M")) - time.timezone - off for t in times]
    out = []
    for i in range(n):
        t = start_epoch + i * STEP_H * 3600
        j = max(0, min(len(epochs) - 1, int((t - epochs[0]) // 3600)))
        out.append(float(temps[j]))
    return out


# ---------------------------------------------------------------- geography
def km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h)) * ROAD_FACTOR


def eta_h(a, b):
    return km(a, b) / SPEED_KMH


# ---------------------------------------------------------------- prediction
def predict_path(temp_now, hours, ambient, cooling_ok):
    """Predicted inside temperatures for each step over `hours`."""
    path, t = [], temp_now
    for i in range(max(1, int(math.ceil(hours / STEP_H)))):
        if not cooling_ok:
            amb = ambient[min(i, len(ambient) - 1)]
            t += (amb - t) * (1 - math.exp(-STEP_H / TAU_BOX_H))
        path.append(t)
    return path


def life_after(profile, used_h, frozen, path):
    """Medicine life (%) after following `path` (one temp per STEP_H), starting from used_h of budget."""
    if frozen:
        return 0.0
    used = used_h
    for t in path:
        if t <= 0 and profile.freeze_sensitive:
            return 0.0
        if t > UPPER_LIMIT_C:
            used += rate(t, profile.ea_over_r) * STEP_H
    return max(0.0, 100.0 * (1 - used / profile.budget_hours_at_25c))


def plan(profile, used_h, frozen, temp_now, cooling_ok, position, dest_id, weather, now_epoch=None, surroundings=None):
    """Forecast for the current destination plus the best alternative. Returns a JSON-friendly dict.

    surroundings: temperature the box is exposed to right now (e.g. a stalled truck). The prediction
    assumes the worse of that and the real outdoor forecast for the rest of the trip.
    """
    now_epoch = now_epoch or time.time()
    dest = next(f for f in FACILITIES if f["id"] == dest_id)
    remaining = eta_h(position, dest)
    amb = ambient_series(weather, now_epoch, max(remaining, 0.25) + 6)
    if surroundings is not None:
        amb = [max(a, surroundings) for a in amb]

    def at(f):
        h = eta_h(position, f)
        life = life_after(profile, used_h, frozen, predict_path(temp_now, max(h, STEP_H), amb, cooling_ok))
        return {"id": f["id"], "name": f["name"], "eta_h": round(h, 2), "km": round(km(position, f), 1), "life_at_arrival": round(life, 1)}

    target = at(dest)
    ok = target["life_at_arrival"] >= USABLE_PCT
    alternatives = sorted((at(f) for f in FACILITIES if f["id"] != dest_id and f["cold_store"]), key=lambda x: x["eta_h"])
    reroute = None
    if not ok and not frozen:
        viable = [a for a in alternatives if a["life_at_arrival"] >= USABLE_PCT]
        reroute = max(viable, key=lambda a: a["life_at_arrival"]) if viable else None
    if frozen:
        action = {"kind": "discard", "text": "Frozen: quarantine and discard. Do not re-route.", "order": True}
    elif ok:
        action = {"kind": "continue", "text": f"Continue to {dest['name']}. Arrives with {target['life_at_arrival']:.0f}% life.", "order": False}
    elif reroute:
        action = {"kind": "reroute", "text": f"Divert to {reroute['name']} ({reroute['eta_h'] * 60:.0f} min). It arrives there with {reroute['life_at_arrival']:.0f}% life and can be used today.", "order": True}
    else:
        action = {"kind": "return", "text": "No facility reachable in time. Get it into the nearest fridge now and flag for QA.", "order": True}
    return {
        "dest": target, "will_survive": ok, "action": action, "reroute": reroute,
        "alternatives": alternatives[:3], "cooling_ok": cooling_ok,
        "ambient_now": round(amb[0], 1), "assumed_tau_h": TAU_BOX_H,
    }
