"""VialGuard viability model: the reference implementation.

The ESP32 firmware (firmware/vialguard/vialguard.ino) and the simulator implement
exactly this logic. Change it here first, run the tests, then mirror it.

Idea: every drug has a "stability budget", the number of hours it may spend above
its storage limit (8 °C) before it is no longer guaranteed to work, measured at a
reference temperature of 25 °C. Hotter temperatures use the budget up faster,
following the Arrhenius equation. Freezing is different: for freeze-sensitive
drugs, a single freeze is irreversible, so the verdict becomes DISCARD at once.
"""
import hashlib
import math
from dataclasses import dataclass, field

T_REF_K = 298.15            # 25 °C reference temperature
MKT_EA_OVER_R = 10000.0     # ΔH/R = 83.144 kJ/mol ÷ R, the USP default for MKT
UPPER_LIMIT_C = 8.0         # storage range is 2–8 °C
FREEZE_LIMIT_C = 0.0        # at or below this, freeze-sensitive drugs are damaged
FREEZE_CONFIRM_READINGS = 2 # two consecutive readings avoid single-sample glitches
WARN_PCT = 50.0
CRIT_PCT = 20.0
GENESIS = "GENESIS"


@dataclass(frozen=True)
class DrugProfile:
    key: str
    name: str
    budget_hours_at_25c: float   # excursion budget expressed at 25 °C
    ea_over_r: float             # activation energy / gas constant, in kelvin
    freeze_sensitive: bool


# Illustrative profiles. A real deployment calibrates them from manufacturer
# stability data. VVM numbers follow WHO vaccine vial monitor categories:
# VVM7 reaches its end point after 7 days at 37 °C or 45 days at 25 °C, which
# implies Ea/R ≈ 14,340 K.
PROFILES = {
    "demo": DrugProfile("demo", "Demo biologic", 48.0, 10000.0, True),
    "insulin": DrugProfile("insulin", "Insulin (unopened)", 28 * 24.0, 10000.0, True),
    "vvm7": DrugProfile("vvm7", "Vaccine, VVM7 heat class", 45 * 24.0, 14340.0, True),
    "vvm2": DrugProfile("vvm2", "Vaccine, VVM2 heat class (freeze-tolerant)", 309.0, 14340.0, False),
}


def rate(temp_c: float, ea_over_r: float) -> float:
    """Degradation rate relative to 25 °C (1.0 at 25 °C, about 7.5 at 38 °C for Ea/R = 14,340 K)."""
    return math.exp(-ea_over_r * (1.0 / (temp_c + 273.15) - 1.0 / T_REF_K))


def mkt_c(temps_c) -> float:
    """Mean Kinetic Temperature (USP <1079.2>) of equally spaced readings, in °C."""
    temps_c = list(temps_c)
    if not temps_c:
        return float("nan")
    s = sum(math.exp(-MKT_EA_OVER_R / (t + 273.15)) for t in temps_c) / len(temps_c)
    return MKT_EA_OVER_R / -math.log(s) - 273.15


def valid_reading(temp_c: float) -> bool:
    """DS18B20 returns -127 when disconnected and 85 at power-on reset."""
    return -55.0 <= temp_c <= 84.0


@dataclass
class Tracker:
    profile: DrugProfile
    budget_used_h: float = 0.0
    frozen: bool = False
    below_freeze_count: int = 0
    temps: list = field(default_factory=list)

    @property
    def budget_pct(self) -> float:
        return max(0.0, 100.0 * (1.0 - self.budget_used_h / self.profile.budget_hours_at_25c))

    def update(self, temp_c: float, dt_hours: float) -> None:
        """Account for one reading held for dt_hours (already multiplied by any demo time scale)."""
        if not valid_reading(temp_c):
            return
        self.temps.append(temp_c)
        if temp_c > UPPER_LIMIT_C:
            self.budget_used_h += rate(temp_c, self.profile.ea_over_r) * dt_hours
        if temp_c <= FREEZE_LIMIT_C:
            self.below_freeze_count += 1
            if self.profile.freeze_sensitive and self.below_freeze_count >= FREEZE_CONFIRM_READINGS:
                self.frozen = True
        else:
            self.below_freeze_count = 0

    def hours_left(self, temp_c: float):
        """Hours of use remaining if the current temperature continues. None means safe indefinitely (in range)."""
        if self.frozen or self.budget_pct <= 0:
            return 0.0
        if temp_c <= UPPER_LIMIT_C:
            return None
        remaining = self.profile.budget_hours_at_25c - self.budget_used_h
        return remaining / rate(temp_c, self.profile.ea_over_r)

    def verdict(self) -> str:
        if self.frozen:
            return "DISCARD_FROZEN"
        p = self.budget_pct
        if p <= 0:
            return "DISCARD_HEAT"
        if p < CRIT_PCT:
            return "USE_IMMEDIATELY"
        if p < WARN_PCT:
            return "USE_SOON"
        return "OK"


def record_hash(seq: int, ts: int, temp_centi: int, budget_bp: int, lid: int, event: str, prev: str) -> str:
    """SHA-256 link of the tamper-evident log. Integers only, so C and JS produce byte-identical input."""
    msg = f"{seq}|{ts}|{temp_centi}|{budget_bp}|{lid}|{event}|{prev}"
    return hashlib.sha256(msg.encode()).hexdigest()


def verify_chain(records) -> int:
    """Return the index of the first broken record, or -1 if the whole chain is intact."""
    prev = GENESIS
    for i, r in enumerate(records):
        if r["prev"] != prev or record_hash(r["seq"], r["ts"], r["t"], r["b"], r["lid"], r["ev"], r["prev"]) != r["h"]:
            return i
        prev = r["h"]
    return -1
