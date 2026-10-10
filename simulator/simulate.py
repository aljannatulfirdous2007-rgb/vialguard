"""VialGuard device simulator: behaves like the ESP32 and writes the same data.

Use it to build and test the dashboard before the hardware arrives, or as a backup
demo. Same model (model/vialguard_model.py), same Firebase schema, same hash chain,
same alert ladder.

    python simulator/simulate.py --local                  # starts the local server too
    python simulator/simulate.py --db https://<project>-default-rtdb.<region>.firebasedatabase.app
    python simulator/simulate.py --local --scenario truck # scripted demo, no typing

Keys while running (type, then Enter):
    h heat (hair dryer, 45 °C)   c cold box (5 °C)   r room (30 °C)   f freeze (-6 °C)
    o open/close lid             a acknowledge alert  n new shipment
    d <drug> switch drug (demo, insulin, vvm7, vvm2)  q quit
"""
import argparse
import json
import math
import os
import random
import sys
import threading
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "model"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import field_trace
import real_data
import forecast
from behavior import BehaviorDetector
from vialguard_model import (CRIT_PCT, GENESIS, PROFILES, UPPER_LIMIT_C, WARN_PCT, MKT_EA_OVER_R, Tracker,
                             record_hash)

ROLES = ["Courier", "Pharmacist", "QA Head"]


class Backend:
    def __init__(self, url, auth=""):
        self.url = url.rstrip("/")
        self.auth = auth

    def _req(self, method, path, body=None):
        u = f"{self.url}{path}.json" + (f"?auth={self.auth}" if self.auth else "")
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(u, data=data, method=method, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read() or b"null")

    def get(self, path):
        return self._req("GET", path)

    def put(self, path, body):
        return self._req("PUT", path, body)

    def patch(self, path, body):
        return self._req("PATCH", path, body)


class Telegram:
    def __init__(self, token, chats):
        self.token, self.chats = token, chats

    def send(self, level, text):
        print(f"  [alert -> {ROLES[level]}] {text.splitlines()[0]}")
        if not self.token:
            return
        q = urllib.parse.urlencode({"chat_id": self.chats[level], "text": text})
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{self.token}/sendMessage?{q}", timeout=8).read()
        except Exception as e:
            print("  telegram error:", e)


class SimDevice:
    def __init__(self, backend, device_id, drug, scale, sample_s, escalate_s, telegram):
        self.db, self.id, self.scale, self.sample_s = backend, device_id, scale, sample_s
        self.escalate_s, self.tg = escalate_s, telegram
        self.temp, self.env, self.lid, self.env_mode = 5.0, 5.0, False, "cold"
        self.replay, self.paused = None, False
        self.weather, self.weather_src = None, "none"
        self.lock = threading.Lock()
        threading.Thread(target=self.weather_loop, daemon=True).start()
        self.new_shipment(drug, "BOOT")

    # ---------------------------------------------------------- state
    def new_shipment(self, drug, reason):
        self.drug = drug
        self.tr = Tracker(PROFILES[drug])
        self.ship = f"s{int(time.time())}"
        self.seq, self.prev, self.queue, self.events = 0, GENESIS, [], [reason]
        self.excursion = self.warned50 = self.warned20 = self.discard_sent = False
        self.alert_active, self.acked, self.level, self.severity, self.reason = False, False, -1, 0, ""
        self.level_since = 0.0
        self.replay, self.paused = None, False
        self.det = BehaviorDetector()
        self.model_min = 0.0
        self.last_flags = []
        self.trip = {"dest": forecast.DEFAULT_DEST, "done_h": 0.0,
                     "total_h": forecast.eta_h(forecast.ORIGIN, next(f for f in forecast.FACILITIES if f["id"] == forecast.DEFAULT_DEST)),
                     "start": dict(forecast.ORIGIN), "delivered": False, "rerouted": False}
        self.realscore = None
        print(f"New shipment {self.ship}: {PROFILES[drug].name}")

    def status_text(self):
        hl = self.tr.hours_left(self.temp)
        hls = "safe while 2-8 C" if hl is None else f"{hl:.1f} h at current temp"
        return f"{PROFILES[self.drug].name} | {self.temp:.1f} C | {self.tr.budget_pct:.0f}% budget left | {self.tr.verdict()} | {hls}"

    # ---------------------------------------------------------- real weather for the route (Open-Meteo)
    def weather_loop(self):
        while True:
            w, src = forecast.fetch_weather()
            if w:
                self.weather, self.weather_src = w, src
                if self.env_mode == "room":
                    self.env = self.outdoor_now()
            time.sleep(900)

    def outdoor_now(self):
        try:
            return float(self.weather["current"]["temperature_2m"])
        except Exception:
            return 31.0

    # ---------------------------------------------------------- trip along the route
    def position(self):
        tr = self.trip
        dest = next(f for f in forecast.FACILITIES if f["id"] == tr["dest"])
        k = 0 if tr["total_h"] <= 0 else min(1.0, tr["done_h"] / tr["total_h"])
        a = tr["start"]
        return {"lat": a["lat"] + (dest["lat"] - a["lat"]) * k, "lon": a["lon"] + (dest["lon"] - a["lon"]) * k}

    def reroute(self, fid):
        if not any(f["id"] == fid for f in forecast.FACILITIES):
            return
        pos = self.position()
        new = next(f for f in forecast.FACILITIES if f["id"] == fid)
        self.trip.update({"start": pos, "dest": fid, "done_h": 0.0, "total_h": forecast.eta_h(pos, new), "rerouted": True})
        self.events.append("REROUTED")
        print(f"  re-routed to {new['name']}")

    def cooling_ok(self):
        return self.temp <= UPPER_LIMIT_C and self.det.cooling_events == 0

    def plan(self):
        if self.replay:
            return None
        used_h = (1 - self.tr.budget_pct / 100) * self.tr.profile.budget_hours_at_25c
        surroundings = self.temp if self.temp > UPPER_LIMIT_C else None   # device-observable: assume it stays this warm
        pl = forecast.plan(self.tr.profile, used_h, self.tr.frozen, self.temp, self.cooling_ok(),
                           self.position(), self.trip["dest"], self.weather, surroundings=surroundings)
        if self.trip["delivered"] and pl["action"]["kind"] in ("continue", "reroute", "return"):
            pl["action"] = {"kind": "delivered", "order": not pl["will_survive"],
                            "text": f"Delivered to {pl['dest']['name']} with {self.tr.budget_pct:.0f}% life. Verdict: {self.tr.verdict().replace('_', ' ').lower()}."}
            pl["reroute"] = None
        return pl

    # ---------------------------------------------------------- alerts (same rules as firmware)
    def raise_alert(self, severity, reason):
        if self.alert_active and not self.acked and severity <= self.severity:
            return
        self.alert_active, self.acked, self.severity, self.reason = True, False, severity, reason
        self.level, self.level_since = 0, time.time()
        self.events.append("ALERT_L0")
        self.tg.send(0, f"VIALGUARD ALERT ({self.id})\n{reason}\n{self.status_text()}\nReply /ack to acknowledge.")

    def escalate(self):
        if not self.alert_active or self.acked or self.level >= 2:
            return
        wait = self.escalate_s[0] if self.level == 0 else self.escalate_s[1] - self.escalate_s[0]
        if time.time() - self.level_since < wait:
            return
        self.level += 1
        self.level_since = time.time()
        self.events.append(f"ALERT_L{self.level}")
        self.tg.send(self.level, f"ESCALATED to {ROLES[self.level]}: nobody acknowledged.\n{self.reason}\n{self.status_text()}")

    def acknowledge(self, by):
        if self.alert_active and not self.acked:
            self.acked = True
            self.events.append("ACK")
            print(f"  acknowledged via {by}")

    # ---------------------------------------------------------- one sample
    def step(self, dt_real):
        if self.paused:
            return
        if self.replay:
            return self.replay_step()
        tau = 12.0  # seconds for the probe to follow the surroundings
        self.temp += (self.env - self.temp) * (1 - math.exp(-dt_real / tau)) + random.uniform(-0.04, 0.04)
        self.evaluate(self.temp, dt_real / 3600.0 * self.scale)

    # ---------------------------------------------------------- 72 h field-trace replay
    PER_TICK = 4   # trace points per second: 72 h replays in about 72 s

    def start_real_replay(self):
        self.new_shipment(self.drug, "REAL_DATA_START")
        pts = real_data.load_window()
        self.replay = {"kind": "real", "pts": pts, "i": 0, "start": pts[0]["ts"]}
        self.realscore = {"labels": [], "hits": [], "matched": 0, "unconfirmed": 0, "missed": 0}
        self.env_mode = "real"
        self.lid = False
        print("  replaying real hospital fridge data")

    def real_step(self):
        r, sc = self.replay, self.realscore
        for _ in range(20):                      # 20 real minutes per tick: 24 h in ~72 s
            if r["i"] >= len(r["pts"]):
                self.events.append("REPLAY_END")
                self.append_record(self.temp, ts=r["pts"][-1]["ts"] + 60)
                self.replay, self.paused = None, True
                return
            pt = r["pts"][r["i"]]
            r["i"] += 1
            minute = (pt["ts"] - r["start"]) / 60.0
            if pt["open"]:
                sc["labels"].append(minute)
                self.events.append("DOOR_LABEL")
            self.temp = pt["t"]
            flags = self.evaluate(pt["t"], 1 / 60.0, ts=pt["ts"], record=(r["i"] % 5 == 0 or pt["open"]), minute=minute)
            if any(f.startswith("ACCESS") for f in flags):
                sc["hits"].append(minute)
            # score: a detection within 4 min of a real door label is a match
            m = 0
            used = set()
            for h in sc["hits"]:
                j = next((k for k, t in enumerate(sc["labels"]) if k not in used and abs(h - t) <= 4), None)
                if j is not None:
                    used.add(j); m += 1
            sc["matched"] = m
            sc["unconfirmed"] = len(sc["hits"]) - m
            sc["missed"] = sum(1 for k, t in enumerate(sc["labels"]) if k not in used and minute - t > 4)

    def start_replay(self):
        self.new_shipment(self.drug, "REPLAY_START")
        start = int(time.time()) // 86400 * 86400 - 3 * 86400 + int(2.5 * 3600)   # day 1 08:00 IST, three days ago
        self.replay = {"kind": "field", "pts": field_trace.build(), "i": 0, "start": start}
        self.env_mode = "replay"
        self.lid = False
        print("  replaying 72 h field trace")

    def replay_step(self):
        if self.replay.get("kind") == "real":
            return self.real_step()
        r = self.replay
        for _ in range(self.PER_TICK):
            if r["i"] >= len(r["pts"]):
                self.events.append("REPLAY_END")
                self.append_record(self.temp, ts=r["start"] + int(field_trace.TOTAL_H * 3600) + 60)
                self.replay, self.paused = None, True
                print("  replay finished")
                return
            pt = r["pts"][r["i"]]
            r["i"] += 1
            if pt["new_phase"]:
                self.events.append(pt["phase"])
            if pt["check"]:
                self.events.append("LOGBOOK_CHECK")
            if pt["lid"] != self.lid:
                self.lid = pt["lid"]
                self.events.append("LID_OPEN" if self.lid else "LID_CLOSE")
            self.temp = pt["t"]
            self.evaluate(pt["t"], field_trace.STEP_H, ts=r["start"] + int(pt["h"] * 3600), minute=pt["h"] * 60)

    def evaluate(self, t, dt_model_h, ts=None, record=True, minute=None):
        """Run one reading through the model and the behaviour detector, raise alerts, seal it into the log."""
        was_frozen = self.tr.frozen
        self.tr.update(t, dt_model_h)
        self.model_min = minute if minute is not None else self.model_min + dt_model_h * 60
        flags = self.det.update(self.model_min, t, lid_open=self.lid)
        for f in flags:
            self.events.append(f)
            self.last_flags = (self.last_flags + [f])[-5:]
            if f == "ACCESS_SUSPECTED":
                self.raise_alert(1, "Box opened without a logged reason (temperature signature)")
            elif f == "COOLING_FAILURE":
                self.raise_alert(2, "Cooling failure suspected: steady climb with no recovery")
        moving = self.env_mode not in ("heat", "room")   # truck stall / left out = the package is not moving
        if not self.replay and not self.trip["delivered"] and moving:
            self.trip["done_h"] += dt_model_h
            if self.trip["done_h"] >= self.trip["total_h"]:
                self.trip["delivered"] = True
                self.events.append("DELIVERED")
        if t > UPPER_LIMIT_C and not self.excursion:
            self.excursion = True
            self.events.append("EXCURSION_START")
            self.raise_alert(1, f"Temperature excursion: {t:.1f} C (limit 8 C)")
        elif t <= UPPER_LIMIT_C and self.excursion:
            self.excursion = False
            self.events.append("EXCURSION_END")
        if self.tr.frozen and not was_frozen:
            self.events.append("FROZEN")
            self.raise_alert(3, f"FROZEN at {t:.1f} C: irreversible damage. DISCARD.")
        p = self.tr.budget_pct
        if p < WARN_PCT and not self.warned50:
            self.warned50 = True; self.events.append("WARN_50"); self.raise_alert(1, "Stability budget below 50%. Use soon.")
        if p < CRIT_PCT and not self.warned20:
            self.warned20 = True; self.events.append("CRIT_20"); self.raise_alert(2, "Stability budget below 20%. Use immediately or discard.")
        if p <= 0 and not self.discard_sent:
            self.discard_sent = True; self.events.append("DISCARD"); self.raise_alert(3, "Stability budget exhausted by heat. DISCARD.")
        if record:
            self.append_record(t, ts)
        return flags

    def append_record(self, t, ts=None):
        ts, tc, bp, lid, ev = ts or int(time.time()), round(t * 100), round(self.tr.budget_pct * 100), int(self.lid), ",".join(self.events)
        h = record_hash(self.seq, ts, tc, bp, lid, ev, self.prev)
        self.queue.append({"seq": self.seq, "ts": ts, "t": tc, "b": bp, "lid": lid, "ev": ev, "prev": self.prev, "h": h})
        self.prev, self.events = h, []
        self.seq += 1

    def state(self):
        hl = self.tr.hours_left(self.temp)
        temps = self.tr.temps
        mkt = None
        if temps:
            s = sum(math.exp(-MKT_EA_OVER_R / (x + 273.15)) for x in temps) / len(temps)
            mkt = round(MKT_EA_OVER_R / -math.log(s) - 273.15, 2)
        return {"seq": self.seq, "ts": int(time.time()), "temp": round(self.temp, 2), "budget": round(self.tr.budget_pct, 2),
                "verdict": self.tr.verdict(), "drug": self.drug, "drug_name": PROFILES[self.drug].name, "mkt": mkt,
                "hours_left": -1 if hl is None else round(hl, 2), "lid": int(self.lid), "ldr": 2600 if self.lid else 400,
                "excursion": int(self.excursion), "frozen": int(self.tr.frozen),
                "alert_level": self.level if self.alert_active else -1, "acked": int(self.acked), "alert_reason": self.reason,
                "scale": self.scale, "ship": self.ship, "rssi": -55, "pending": len(self.queue), "fw": "sim-1.0", "env_mode": self.env_mode,
                "replay": self.replay_progress(),
                "replay_done": int(self.paused),
                "replay_kind": self.replay.get("kind") if self.replay else None,
                "trip": self.trip_state(), "forecast": self.plan(),
                "behavior": {"trust": self.det.trust_score(), "access": self.det.access_events, "unexplained": self.det.unexplained_access,
                             "cooling": self.det.cooling_events, "recent": self.last_flags},
                "realscore": None if not self.realscore else {k: (len(v) if isinstance(v, list) else v) for k, v in self.realscore.items()},
                "weather": {"source": self.weather_src, "outdoor_c": round(self.outdoor_now(), 1) if self.weather else None}}

    def replay_progress(self):
        r = self.replay
        if not r:
            return None
        pt = r["pts"][min(r["i"], len(r["pts"]) - 1)]
        if r.get("kind") == "real":
            return {"h": round((pt["ts"] - r["start"]) / 3600, 2), "total": real_data.WINDOW_H, "phase": "REAL"}
        return {"h": round(pt["h"], 2), "total": field_trace.TOTAL_H, "phase": pt["phase"]}

    def trip_state(self):
        dest = next(f for f in forecast.FACILITIES if f["id"] == self.trip["dest"])
        return {"origin": forecast.ORIGIN["name"], "dest": dest["name"], "dest_id": dest["id"],
                "done_h": round(self.trip["done_h"], 2), "total_h": round(self.trip["total_h"], 2),
                "delivered": self.trip["delivered"], "rerouted": self.trip["rerouted"], "pos": self.position()}

    def push(self):
        body = {"state": self.state()}
        for r in self.queue[:15]:
            body[f"shipments/{self.ship}/readings/{r['seq']:08d}"] = r
        try:
            self.db.patch(f"/devices/{self.id}", body)
            self.queue = self.queue[15:]
        except Exception as e:
            print("  push failed (will retry):", e)

    def poll_commands(self):
        try:
            cmd = self.db.get(f"/devices/{self.id}/cmd")
        except Exception:
            return
        if not cmd:
            return
        if cmd.get("ack"):
            self.acknowledge("dashboard")
        # control panel in the dashboard (software-only demo): same effects as the keyboard keys
        if cmd.get("replay"):
            self.start_replay()
        if cmd.get("real"):
            self.start_real_replay()
        if cmd.get("reroute"):
            self.reroute(cmd["reroute"])
        if cmd.get("env") in ENV_MODES and (self.replay or self.paused):
            self.new_shipment(self.drug, "NEW_SHIPMENT")
            self.temp = 5.0
        if cmd.get("env") in ENV_MODES:
            self.env_mode = cmd["env"]
            self.env = self.outdoor_now() if self.env_mode == "room" else ENV_MODES[self.env_mode]
            print(f"  dashboard: {self.env_mode}")
        if "lid" in cmd and bool(cmd["lid"]) != self.lid and not self.replay:
            self.lid = bool(cmd["lid"])
            self.events.append("LID_OPEN" if self.lid else "LID_CLOSE")
        if cmd.get("drug") in PROFILES:
            self.new_shipment(cmd["drug"], "NEW_SHIPMENT")
        elif cmd.get("reset"):
            self.new_shipment(self.drug, "NEW_SHIPMENT")
        self.db.put(f"/devices/{self.id}/cmd", None)

    # ---------------------------------------------------------- controls
    def command(self, line):
        parts = line.strip().split()
        if not parts:
            return True
        k = parts[0].lower()
        with self.lock:
            if k in ("h", "c", "r", "f"):
                self.env_mode = {"h": "heat", "c": "cold", "r": "room", "f": "freeze"}[k]
                self.env = ENV_MODES[self.env_mode]
                print(f"  {self.env_mode}")
            elif k == "o":
                self.lid = not self.lid
                self.events.append("LID_OPEN" if self.lid else "LID_CLOSE")
                print("  lid", "OPEN" if self.lid else "closed")
            elif k == "p": self.start_replay()
            elif k == "a": self.acknowledge("keyboard")
            elif k == "n": self.new_shipment(self.drug, "NEW_SHIPMENT")
            elif k == "d" and len(parts) > 1 and parts[1] in PROFILES: self.new_shipment(parts[1], "NEW_SHIPMENT")
            elif k == "q": return False
            else: print("  keys: h c r f o a n | d <demo|insulin|vvm7|vvm2> | q")
        return True

    def run(self, scenario=None, duration=None, loop=False):
        start = last_push = last_cmd = last = time.time()
        script = list(scenario or [])
        while True:
            now = time.time()
            if duration and now - start > duration:
                break
            while script and now - start >= script[0][0]:
                _, line = script.pop(0)
                print(f"[scenario t={now - start:.0f}s] {line}")
                self.command(line)
            if loop and scenario and not script and now - start >= LOOP_PERIOD:
                with self.lock:
                    self.env, self.temp, self.lid, self.env_mode = 5.0, 5.0, False, "cold"
                    self.new_shipment(self.drug, "NEW_SHIPMENT")
                start, script = now, list(scenario)
            with self.lock:
                self.step(now - last)
                last = now
                self.escalate()
                if now - last_cmd >= 3:
                    last_cmd = now
                    self.poll_commands()
                if now - last_push >= 2:
                    last_push = now
                    self.push()
            print(f"\r{self.status_text()}   ", end="", flush=True)
            time.sleep(self.sample_s)


TRUCK = [(10, "h"), (40, "c"), (75, "o"), (82, "o"), (95, "f"), (125, "c")]
ENV_MODES = {"cold": 5.0, "room": 30.0, "heat": 45.0, "freeze": -6.0}   # surroundings the probe drifts towards
LOOP_PERIOD = 150  # seconds before the scripted story restarts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="Firebase RTDB URL or http://host:8787 for the local server")
    ap.add_argument("--local", action="store_true", help="start the local server on :8787 and use it")
    ap.add_argument("--auth", default=os.environ.get("FIREBASE_AUTH", ""))
    ap.add_argument("--device", default="vg-001")
    ap.add_argument("--drug", default="demo", choices=list(PROFILES))
    ap.add_argument("--scale", type=int, default=360, help="demo time scale (1 = real time)")
    ap.add_argument("--sample", type=float, default=1.0, help="seconds between samples")
    ap.add_argument("--escalate", type=int, nargs=2, default=[30, 60], metavar=("PHARMACIST_S", "QA_S"))
    ap.add_argument("--scenario", choices=["truck"], help="run a scripted demo instead of reading the keyboard")
    ap.add_argument("--duration", type=float, help="stop after N seconds")
    ap.add_argument("--loop", action="store_true", help="replay the scenario forever (attract mode for the expo table)")
    ap.add_argument("--panel", action="store_true", help="no keyboard: drive everything from the dashboard control panel")
    a = ap.parse_args()

    if a.local:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import local_server
        local_server.serve(8787)
        a.db = a.db or "http://localhost:8787"
        print("Local server: http://localhost:8787/dashboard/")
    if not a.db:
        ap.error("give --db <url> or --local")

    tg = Telegram(os.environ.get("TELEGRAM_TOKEN", ""),
                  [os.environ.get(k, "") for k in ("CHAT_COURIER", "CHAT_PHARMACIST", "CHAT_QA")])
    dev = SimDevice(Backend(a.db, a.auth), a.device, a.drug, a.scale, a.sample, a.escalate, tg)

    if a.panel:
        print("Control it from the dashboard panel. Ctrl+C to stop.")
        try:
            dev.run(duration=a.duration)
        except KeyboardInterrupt:
            pass
        return
    if a.scenario:
        dev.run(TRUCK, a.duration or (None if a.loop else 150), loop=a.loop)
        return
    threading.Thread(target=dev.run, kwargs={"duration": a.duration}, daemon=True).start()
    print(__doc__.split("Keys while running")[1])
    try:
        for line in sys.stdin:
            if not dev.command(line):
                break
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
