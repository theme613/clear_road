"""Clear Road API: predict the best departure slot for a KL commute."""
import json
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import joblib
import pandas as pd
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.geo import haversine_km, search_places
from app.planner import load_congestion_model, plan_trip
from app.weather import get_weather, slot_rain

ROOT = Path(__file__).resolve().parent.parent
MODEL_FILE = ROOT / "model" / "model.joblib"
METRICS_FILE = ROOT / "model" / "metrics.json"
TRIPS_FILE = ROOT / "data" / "trips.csv"
FEATURES = ["route_id", "departure_hour", "weekday", "is_holiday", "rain_mm", "rain_prev3h_mm"]
SLOTS = [6.5 + 0.25 * i for i in range(17)]
DEFAULT_HOUR = 8.0
KL = timezone(timedelta(hours=8))  # Asia/Kuala_Lumpur has no DST
TRAIN_CMD = "python ml/train.py"

MODEL = None  # the one cached model
ROUTES: list[dict] = []
BACKTEST_CACHE: dict = {}


def load_assets() -> None:
    global MODEL, ROUTES
    try:
        ROUTES = json.loads((ROOT / "routes.json").read_text())
    except Exception as e:
        print(f"[main] could not read routes.json: {e}")
    try:
        MODEL = joblib.load(MODEL_FILE)
        print("[main] model loaded")
    except Exception as e:
        MODEL = None
        print(f"[main] model NOT loaded ({e}). Run: {TRAIN_CMD}")


def fmt_slot(hour: float) -> str:
    return f"{int(hour):02d}:{int(round((hour % 1) * 60)):02d}"


def model_error() -> JSONResponse:
    return JSONResponse({"error": f"Model not found. Run: {TRAIN_CMD}"}, status_code=503)


def get_route(route_id: str) -> Optional[dict]:
    return next((r for r in ROUTES if r["id"] == route_id), None)


# ---------- rain ----------
def get_rain(day: Date, override: Optional[float]) -> tuple[list[float], str]:
    """Return 48 hourly rain values (previous day + day) and the source name."""
    data, source = get_weather(day, override)
    return data["rain"], source


# ---------- prediction ----------
def make_frame(route_id: str, weekday: int, is_holiday: int, hours: list[float],
               vals: list[float]) -> pd.DataFrame:
    rows = []
    for h in hours:
        rain, prev3 = slot_rain(vals, h)
        rows.append([route_id, h, weekday, is_holiday, rain, prev3])
    return pd.DataFrame(rows, columns=FEATURES)


def pick_best(hours: list[float], minutes: list[float]) -> int:
    """Index of the EARLIEST slot within 5 min of the fastest slot."""
    limit = min(minutes) + 5
    return next(i for i, m in enumerate(minutes) if m <= limit)


def is_my_holiday(day: Date) -> int:
    try:
        import holidays
        return int(day in holidays.country_holidays("MY", years=day.year))
    except Exception:
        return 0


def contributions(route_id: str, weekday: int, is_hol: int, vals: list[float]) -> dict:
    row = make_frame(route_id, weekday, is_hol, [DEFAULT_HOUR], vals)
    no_rain = row.copy()
    no_rain[["rain_mm", "rain_prev3h_mm"]] = 0.0
    off_peak = row.copy()
    off_peak["departure_hour"] = 10.5
    p_all, p_dry, p_off = (float(MODEL.predict(f)[0]) for f in (row, no_rain, off_peak))
    return {"rain": round(p_all - p_dry, 1), "rush_hour": round(p_all - p_off, 1)}


def explain(contrib: dict, road: str, rain_mm: float, best_slot: str, saved: float) -> str:
    if contrib["rain"] >= 2:
        return (f"Rain of {rain_mm:.1f} mm adds about {contrib['rain']:.0f} min on {road} "
                f"at 08:00. Leave at {best_slot} to save {saved:.0f} min.")
    if contrib["rush_hour"] >= 2:
        return (f"Rush hour adds about {contrib['rush_hour']:.0f} min on {road} "
                f"at 08:00. Leave at {best_slot} to save {saved:.0f} min.")
    return f"Roads look fine. Leaving at {best_slot} saves only {saved:.0f} min."


def run_prediction(route: dict, day: Date, override: Optional[float]) -> dict:
    vals, source = get_rain(day, override)
    weekday, is_hol = day.weekday(), is_my_holiday(day)
    frame = make_frame(route["id"], weekday, is_hol, SLOTS, vals)
    minutes = [float(m) for m in MODEL.predict(frame)]
    bi = pick_best(SLOTS, minutes)
    di = SLOTS.index(DEFAULT_HOUR)
    saved = max(minutes[di] - minutes[bi], 0.0)
    contrib = contributions(route["id"], weekday, is_hol, vals)
    best_slot = fmt_slot(SLOTS[bi])
    return {
        "route_id": route["id"], "road": route["road"], "date": day.isoformat(),
        "rain_source": source,
        "slots": [{"slot": fmt_slot(h), "minutes": round(m, 1),
                   "rain_mm": round(float(frame.loc[i, "rain_mm"]), 1)}
                  for i, (h, m) in enumerate(zip(SLOTS, minutes))],
        "best": {"slot": best_slot, "minutes": round(minutes[bi], 1)},
        "default": {"slot": fmt_slot(DEFAULT_HOUR), "minutes": round(minutes[di], 1)},
        "saved_minutes": round(saved, 1),
        "contributions": contrib,
        "explanation": explain(contrib, route["road"], float(frame.loc[di, "rain_mm"]),
                               best_slot, saved),
    }


# ---------- backtest ----------
def compute_backtest() -> dict:
    empty = {"mornings": 0, "avg_saved_min": None, "source": "unavailable"}
    try:
        test_start = json.loads(METRICS_FILE.read_text())["test_start_date"]
        df = pd.read_csv(TRIPS_FILE)
    except Exception as e:
        print(f"[main] backtest files missing ({e}). Run: python ml/simulate.py && {TRAIN_CMD}")
        return empty
    df = df[df["date"] >= test_start]
    combos = df[["route_id", "date"]].drop_duplicates()
    if combos.empty or MODEL is None:
        return empty
    combos = combos.sample(n=min(30, len(combos)), random_state=42)
    saved_list = []
    for route_id, day in combos.itertuples(index=False):
        m = df[(df["route_id"] == route_id) & (df["date"] == day)].sort_values("departure_hour")
        pred = [float(p) for p in MODEL.predict(m[FEATURES])]
        bi = pick_best(list(m["departure_hour"]), pred)
        actual = m["trip_minutes"].tolist()
        di = list(m["departure_hour"]).index(DEFAULT_HOUR)
        saved_list.append(actual[di] - actual[bi])
    return {"mornings": len(saved_list),
            "avg_saved_min": round(sum(saved_list) / len(saved_list), 1),
            "source": "simulated test days"}


# ---------- API ----------
load_assets()
load_congestion_model()
app = FastAPI(title="Clear Road")


class PredictBody(BaseModel):
    route_id: str
    date: Optional[str] = None
    rain_override_mm: Optional[float] = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model_loaded": MODEL is not None}


@app.get("/routes")
def routes() -> list[dict]:
    return [{"id": r["id"], "name": r["name"], "road": r["road"]} for r in ROUTES]


@app.post("/predict")
def predict(body: PredictBody):
    if MODEL is None:
        return model_error()
    route = get_route(body.route_id)
    if route is None:
        return JSONResponse({"error": f"Unknown route_id '{body.route_id}'. See GET /routes."},
                            status_code=404)
    try:
        day = Date.fromisoformat(body.date) if body.date else datetime.now(KL).date()
    except ValueError:
        return JSONResponse({"error": "date must be YYYY-MM-DD"}, status_code=400)
    return run_prediction(route, day, body.rain_override_mm)


@app.get("/backtest")
def backtest():
    if MODEL is None:
        return model_error()
    if not BACKTEST_CACHE:
        BACKTEST_CACHE.update(compute_backtest())
    return BACKTEST_CACHE


# ---------- v2: any From -> To, car vs train vs bus ----------
class Place(BaseModel):
    lat: float
    lon: float
    name: Optional[str] = None


class PlanBody(BaseModel):
    from_: Place = Field(alias="from")
    to: Place
    date: Optional[str] = None
    time: Optional[str] = None  # "HH:MM", default 08:00
    rain_override_mm: Optional[float] = None


def parse_time(text: Optional[str]) -> Optional[float]:
    """'07:40' -> 7.75 (rounded to the 15-min slot). None if invalid."""
    if not text:
        return DEFAULT_HOUR
    try:
        h, m = (int(x) for x in text.split(":")[:2])
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h + round(m / 15) * 0.25 if h + round(m / 15) * 0.25 <= 23.75 else 23.75


@app.get("/api/search")
def api_search(q: str = ""):
    results, source = search_places(q)
    return {"results": results, "source": source}


@app.post("/api/plan")
def api_plan(body: PlanBody):
    try:
        day = Date.fromisoformat(body.date) if body.date else datetime.now(KL).date()
    except ValueError:
        return JSONResponse({"error": "date must be YYYY-MM-DD"}, status_code=400)
    hour = parse_time(body.time)
    if hour is None:
        return JSONResponse({"error": "time must be HH:MM"}, status_code=400)
    a, b = (body.from_.lat, body.from_.lon), (body.to.lat, body.to.lon)
    if haversine_km(*a, *b) < 0.3:
        return JSONResponse({"error": "From and To are the same place."}, status_code=400)
    try:
        return plan_trip(a, b, day, hour, body.rain_override_mm)
    except Exception as e:
        print(f"[main] plan failed: {type(e).__name__}: {e}")
        return JSONResponse({"error": f"Could not plan this trip: {e}"}, status_code=500)


app.mount("/", StaticFiles(directory=ROOT / "app" / "static", html=True), name="static")
