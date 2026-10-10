"""Step 3: simulate trip times -> data/trips.csv (route x day x 15-min slot)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
ROOT = Path(__file__).resolve().parent.parent
SLOTS = [6.5 + 0.25 * i for i in range(17)]  # 06:30 .. 10:30


def load_holidays(years: list[int]) -> set:
    try:
        import holidays
        return set(holidays.country_holidays("MY", years=years).keys())
    except Exception as e:
        print(f"[simulate] holidays failed ({e}); using is_holiday=0")
        return set()


def day_mult(weekday: int, is_holiday: int) -> float:
    if weekday == 5:
        return 0.80
    if weekday == 6:
        return 0.65
    if is_holiday:
        return 0.70
    return 1.08 if weekday == 4 else 1.0


def main() -> None:
    rain_file = ROOT / "data" / "rain_history.csv"
    if not rain_file.exists():
        print(f"[simulate] missing {rain_file}. Create it with: python ml/pull_rain.py")
        return
    routes = json.loads((ROOT / "routes.json").read_text())
    rain = pd.read_csv(rain_file, parse_dates=["time"]).set_index("time")["precipitation_mm"]
    prev3 = rain.shift(1) + rain.shift(2) + rain.shift(3)

    days = sorted(rain.index.normalize().unique())[-365:]
    print(f"[simulate] rain file has {len(rain)} hours; using last {len(days)} days")
    hol = load_holidays(sorted({d.year for d in days}))
    rng = np.random.default_rng(SEED)

    rows = []
    for day in days:
        weekday = day.weekday()
        is_hol = int(day.date() in hol)
        for r in routes:
            for hour in SLOTS:
                t = day + pd.Timedelta(hours=int(hour))  # hour of departure
                rain_mm = float(rain.get(t, 0.0))
                p3 = prev3.get(t, 0.0)
                p3 = 0.0 if pd.isna(p3) else float(p3)
                tod = 1 + (r["peak_mult"] - 1) * np.exp(-((hour - r["peak_hour"]) ** 2) / (2 * 0.8 ** 2))
                rm = 1 + min(rain_mm, 30) * 0.025 + min(p3, 30) * 0.01
                mean = r["base_minutes"] * tod * rm * day_mult(weekday, is_hol)
                trip = max(mean + rng.normal(0, 0.05 * mean), r["base_minutes"] * 0.8)
                rows.append({
                    "date": day.date().isoformat(), "route_id": r["id"],
                    "departure_hour": hour, "weekday": weekday, "is_holiday": is_hol,
                    "rain_mm": round(rain_mm, 2), "rain_prev3h_mm": round(p3, 2),
                    "trip_minutes": round(trip, 2),
                })
    df = pd.DataFrame(rows)
    out = ROOT / "data" / "trips.csv"
    df.to_csv(out, index=False)
    print(f"[simulate] saved {len(df)} rows to {out}")


if __name__ == "__main__":
    main()
