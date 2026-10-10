"""Compare car, train and bus for one trip at a chosen time (+ the 17 slots around it)."""
from datetime import date as Date
from pathlib import Path
from typing import Optional

import joblib
import pandas as pd

from app.geo import KLCC, haversine_km, osrm_route
from app.rail import plan_train
from app.weather import get_weather, slot_rain

ROOT = Path(__file__).resolve().parent.parent
CONGESTION_FILE = ROOT / "model" / "congestion.joblib"
FEATURES = ["departure_hour", "weekday", "is_holiday", "rain_mm", "rain_prev3h_mm", "trip_km", "inbound"]
TRAFFIC_COLORS = [(1.25, "#1e9e4a"), (1.6, "#f29900"), (2.0, "#e8453c"), (99, "#a50e0e")]

CONGESTION_MODEL = None


def load_congestion_model():
    global CONGESTION_MODEL
    if CONGESTION_MODEL is None:
        try:
            CONGESTION_MODEL = joblib.load(CONGESTION_FILE)
            print("[planner] congestion model loaded")
        except Exception as e:
            print(f"[planner] congestion model missing ({e}). Run: python ml/train_congestion.py")
    return CONGESTION_MODEL


def is_holiday(day: Date) -> int:
    try:
        import holidays
        return int(day in holidays.country_holidays("MY", years=day.year))
    except Exception:
        return 0


def fmt_time(hour: float) -> str:
    return f"{int(hour):02d}:{int(round((hour % 1) * 60)):02d}"


def slot_hours(hour: float) -> list[float]:
    """Up to 17 slots: chosen time -2h .. +2h every 15 min, kept inside the day."""
    return [h for h in (hour + 0.25 * i for i in range(-8, 9)) if 0 <= h <= 23.75]


def predict_mults(rows: list[list]) -> tuple[list[float], str]:
    model = load_congestion_model()
    if model is None:
        return [1.3] * len(rows), "fallback"
    return [max(float(m), 1.0) for m in model.predict(pd.DataFrame(rows, columns=FEATURES))], "model"


def traffic_level(mult: float) -> str:
    if mult < 1.3:
        return "Light traffic"
    if mult < 1.7:
        return "Moderate traffic"
    return "Heavy traffic"


def traffic_segments(coords: list[list[float]], mult: float, n: int = 40) -> list[dict]:
    """Split the car line into chunks coloured like Google traffic (worse near the city centre)."""
    if len(coords) < 2:
        return []
    step = max(1, len(coords) // n)
    out = []
    for i in range(0, len(coords) - 1, step):
        chunk = coords[i:i + step + 1]
        mid = chunk[len(chunk) // 2]
        dc = haversine_km(mid[0], mid[1], *KLCC)
        local = 1 + (mult - 1) * min(max(1.5 - dc / 25, 0.3), 1.5)  # worse near the centre
        color = next(c for limit, c in TRAFFIC_COLORS if local < limit)
        out.append({"coords": chunk, "color": color})
    return out


def bus_estimate(car_minutes: float, km: float, hour: float, weekend: bool) -> Optional[dict]:
    if km < 1.0:
        return None
    peak = not weekend and (7 <= hour < 9.5 or 17 <= hour < 19.5)
    wait = 8 if peak else 12
    minutes = car_minutes * 1.3 + wait + 10  # 1.3 = stops; 10 = walking at both ends
    return {"minutes": round(minutes, 1), "wait_minutes": wait}


def evaluate(a, b, route, hour, weekday, hol, weekend, rain, prev3, mult) -> dict:
    car = round(route["minutes"] * mult, 1)
    train = plan_train(a, b, hour, weekend, feeder_mult=mult, rain_mm=rain)
    bus = bus_estimate(car, route["km"], hour, weekend)
    return {"car": car, "train": train, "bus": bus, "mult": mult, "rain": rain, "prev3": prev3}


def explain(rec: str, chosen: dict, car_opt: dict, train_opt: Optional[dict], time_str: str,
            saved: float, best_dep: dict) -> str:
    parts = []
    if rec == "train":
        parts.append(f"{car_opt['traffic']} at {time_str}: driving takes {car_opt['minutes']:.0f} min "
                     f"(+{car_opt['delay_minutes']:.0f} min delay).")
        parts.append(f"Take {' → '.join(train_opt['lines'])} to save {saved:.0f} min.")
    elif rec == "car":
        if train_opt:
            diff = train_opt["minutes"] - car_opt["minutes"]
            parts.append(f"Driving is fastest at {time_str} ({car_opt['minutes']:.0f} min, "
                         f"{diff:.0f} min faster than the train).")
        else:
            parts.append(f"No train line nearby. Driving takes {car_opt['minutes']:.0f} min at {time_str}.")
    else:
        parts.append(f"The bus is estimated fastest at {time_str} and saves {saved:.0f} min vs driving.")
    if chosen["rain"] >= 2:
        parts.append(f"Rain ({chosen['rain']:.1f} mm) slows the roads.")
    if best_dep["time"] != time_str and best_dep["saves"] >= 5:
        parts.append(f"Leaving at {best_dep['time']} by {best_dep['mode']} takes only "
                     f"{best_dep['minutes']:.0f} min ({best_dep['saves']:.0f} min less).")
    return " ".join(parts)


def plan_trip(a: tuple[float, float], b: tuple[float, float], day: Date, hour: float,
              rain_override: Optional[float] = None) -> dict:
    weather, wsource = get_weather(day, rain_override)
    route = osrm_route(a, b)
    weekday, hol = day.weekday(), is_holiday(day)
    weekend = weekday >= 5 or bool(hol)
    inbound = int(haversine_km(*b, *KLCC) < haversine_km(*a, *KLCC))

    hours = slot_hours(hour)
    rains = [slot_rain(weather["rain"], h) for h in hours]
    rows = [[h, weekday, hol, r, p, route["km"], inbound] for h, (r, p) in zip(hours, rains)]
    mults, msource = predict_mults(rows)
    evals = [evaluate(a, b, route, h, weekday, hol, weekend, r, p, m)
             for h, (r, p), m in zip(hours, rains, mults)]
    chosen = evals[hours.index(hour)]

    mult = chosen["mult"]
    car_opt = {"mode": "car", "minutes": chosen["car"], "km": route["km"],
               "freeflow_minutes": route["minutes"],
               "delay_minutes": round(max(chosen["car"] - route["minutes"] * 1.10, 0), 1),
               "traffic": traffic_level(mult), "multiplier": round(mult, 2),
               "segments": traffic_segments(route["coords"], mult), "coords": route["coords"],
               "source": route["source"]}
    train_opt = chosen["train"] and {"mode": "train", **chosen["train"]}
    bus_opt = chosen["bus"] and {"mode": "bus", "estimate": True, **chosen["bus"],
                                 "coords": route["coords"]}
    options = [o for o in (car_opt, train_opt, bus_opt) if o]
    rec = min(options, key=lambda o: o["minutes"])["mode"]
    rec_minutes = next(o["minutes"] for o in options if o["mode"] == rec)
    saved = round(max(car_opt["minutes"] - rec_minutes, 0), 1)

    slots = []
    for h, ev in zip(hours, evals):
        slots.append({"time": fmt_time(h), "car": ev["car"],
                      "train": ev["train"]["minutes"] if ev["train"] else None,
                      "bus": ev["bus"]["minutes"] if ev["bus"] else None})
    # Best time: fastest of car/train; ties within 2 min go to the slot closest to the chosen time
    def best_of(s: dict) -> tuple[float, str]:
        vals = [(s["car"], "car")] + ([(s["train"], "train")] if s["train"] else [])
        return min(vals)
    fastest = min(best_of(s)[0] for s in slots)
    near = [(abs(h - hour), s) for h, s in zip(hours, slots) if best_of(s)[0] <= fastest + 2]
    best_slot = min(near, key=lambda x: x[0])[1]
    bm, bmode = best_of(best_slot)
    chosen_best = min(car_opt["minutes"], train_opt["minutes"] if train_opt else 1e9)
    best_dep = {"time": best_slot["time"], "mode": bmode, "minutes": bm,
                "saves": round(max(chosen_best - bm, 0), 1)}

    time_str = fmt_time(hour)
    temp = weather["temp"][24 + int(hour)] if weather["temp"] else None
    return {
        "date": day.isoformat(), "time": time_str,
        "weather": {"rain_mm": round(chosen["rain"], 1), "temp_c": temp, "source": wsource},
        "options": options, "recommended": rec, "saved_minutes": saved,
        "explanation": explain(rec, chosen, car_opt, train_opt, time_str, saved, best_dep),
        "slots": slots, "best_departure": best_dep,
        "sources": {"route": route["source"], "rain": wsource, "traffic": msource},
    }
