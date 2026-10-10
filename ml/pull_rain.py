"""Step 2: download hourly rain for KL from Open-Meteo (no key) -> data/rain_history.csv."""
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

SEED = 42
LAT, LON = 3.139, 101.687
URL = "https://archive-api.open-meteo.com/v1/archive"
OUT = Path(__file__).resolve().parent.parent / "data" / "rain_history.csv"


def date_range() -> tuple[date, date]:
    today = date.today()
    return today - timedelta(days=372), today - timedelta(days=7)


def fetch_rain(start: date, end: date) -> pd.DataFrame:
    params = {
        "latitude": LAT, "longitude": LON,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": "precipitation", "timezone": "Asia/Kuala_Lumpur",
    }
    resp = requests.get(URL, params=params, timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"status {resp.status_code}: {resp.text[:200]}")
    h = resp.json()["hourly"]
    return pd.DataFrame({"time": h["time"], "precipitation_mm": h["precipitation"]})


def synthetic_rain(start: date, end: date) -> pd.DataFrame:
    """Seeded fake rain: ~12% of mornings rainy, 0.5-25 mm spread over 06:00-10:00."""
    rng = np.random.default_rng(SEED)
    times = pd.date_range(start, end + timedelta(days=1), freq="h", inclusive="left")
    rain = np.zeros(len(times))
    for i in range(0, len(times), 24):
        if rng.random() < 0.12:
            total = rng.uniform(0.5, 25.0)
            weights = rng.dirichlet(np.ones(4))
            rain[i + 6:i + 10] = total * weights  # hours 06..09
    return pd.DataFrame({"time": times.strftime("%Y-%m-%dT%H:%M"),
                         "precipitation_mm": np.round(rain, 2)})


def main() -> None:
    start, end = date_range()
    print(f"[pull_rain] requesting {start} to {end}")
    try:
        df = fetch_rain(start, end)
        df["precipitation_mm"] = df["precipitation_mm"].fillna(0.0)
        print("[pull_rain] using real Open-Meteo data")
    except Exception as e:
        print(f"[pull_rain] FAILED: {e}")
        df = synthetic_rain(start, end)
        print("[pull_rain] USING SYNTHETIC RAIN")
    OUT.parent.mkdir(exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"[pull_rain] saved {len(df)} rows to {OUT}")


if __name__ == "__main__":
    main()
