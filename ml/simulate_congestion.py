"""Simulate road congestion for ANY KL trip -> data/congestion.csv.

Label congestion_mult = real trip time / free-flow time (OSRM gives free-flow).
Simulated, not measured: morning peak ~08:00 hits trips INTO the city,
evening peak ~18:15 hits trips OUT of the city; rain and day type as in ml/simulate.py.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from simulate import load_holidays

SEED = 42
ROOT = Path(__file__).resolve().parent.parent
HOURS = [5.0 + 0.25 * i for i in range(73)]  # 05:00 .. 23:00
TRIPS_PER_SLOT = 2


def day_factor(weekday: int, is_holiday: int) -> float:
    """How much of the weekday rush exists on this day."""
    if weekday == 5:
        return 0.35
    if weekday == 6:
        return 0.25
    if is_holiday:
        return 0.30
    return 1.10 if weekday == 4 else 1.0


def congestion_mult(hour: float, weekday: int, is_holiday: int, rain_mm: float,
                    prev3: float, trip_km: float, inbound: int) -> float:
    # KL commute: the inbound morning rush is the worst (often 2-3x free-flow on Federal/LDP/NPE)
    am = np.exp(-((hour - 8.0) ** 2) / (2 * 0.8 ** 2)) * (1.25 if inbound else 0.6)
    pm = np.exp(-((hour - 18.25) ** 2) / (2 * 1.0 ** 2)) * (0.65 if inbound else 1.1)
    length = 0.75 + 0.25 * min(trip_km, 30) / 30
    daytime = 0.08 if 7 <= hour <= 21 else 0.0
    rush = ((am + pm) * length + daytime) * day_factor(weekday, is_holiday)
    rain = 1 + min(rain_mm, 30) * 0.025 + min(prev3, 30) * 0.01
    return 1.10 * (1 + rush) * rain  # 1.10 = traffic lights / turns OSRM ignores


def main() -> None:
    rain_file = ROOT / "data" / "rain_history.csv"
    if not rain_file.exists():
        print(f"[simulate_congestion] missing {rain_file}. Create it with: python ml/pull_rain.py")
        return
    rain = pd.read_csv(rain_file, parse_dates=["time"]).set_index("time")["precipitation_mm"]
    prev3 = (rain.shift(1) + rain.shift(2) + rain.shift(3)).fillna(0.0)
    days = sorted(rain.index.normalize().unique())[-365:]
    hol = load_holidays(sorted({d.year for d in days}))
    rng = np.random.default_rng(SEED)

    rows = []
    for day in days:
        wd, is_hol = day.weekday(), int(day.date() in hol)
        for hour in HOURS:
            t = day + pd.Timedelta(hours=int(hour))
            r_mm, p3 = float(rain.get(t, 0.0)), float(prev3.get(t, 0.0))
            for _ in range(TRIPS_PER_SLOT):
                km = float(rng.uniform(2, 45))
                inbound = int(rng.random() < 0.5)
                mean = congestion_mult(hour, wd, is_hol, r_mm, p3, km, inbound)
                mult = max(mean + rng.normal(0, 0.05 * mean), 1.0)
                rows.append({"date": day.date().isoformat(), "departure_hour": hour,
                             "weekday": wd, "is_holiday": is_hol, "rain_mm": round(r_mm, 2),
                             "rain_prev3h_mm": round(p3, 2), "trip_km": round(km, 2),
                             "inbound": inbound, "congestion_mult": round(mult, 4)})
    df = pd.DataFrame(rows)
    out = ROOT / "data" / "congestion.csv"
    df.to_csv(out, index=False)
    print(f"[simulate_congestion] saved {len(df)} rows to {out}")


if __name__ == "__main__":
    main()
