"""Smoke test: python scripts/smoke_test.py (run from the repo root)."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import app

results: list[bool] = []


def check(name: str, ok: bool) -> None:
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}")


def default_minutes(body: dict) -> float:
    return body["default"]["minutes"]


PUCHONG = {"lat": 3.048101, "lon": 101.62095, "name": "IOI Puchong Jaya"}
KLCC = {"lat": 3.1579, "lon": 101.7116, "name": "KLCC"}


def next_day(weekday: int) -> str:
    """Next date with this weekday (0=Mon) that is not a Malaysian public holiday."""
    from app.planner import is_holiday
    d = date.today() + timedelta(days=1)
    while d.weekday() != weekday or is_holiday(d):
        d += timedelta(days=1)
    return d.isoformat()


def plan(client: TestClient, day: str, time: str, rain=None, frm=PUCHONG, to=KLCC) -> dict:
    body = {"from": frm, "to": to, "date": day, "time": time}
    if rain is not None:
        body["rain_override_mm"] = rain
    return client.post("/api/plan", json=body).json()


def car_minutes(p: dict) -> float:
    return next(o["minutes"] for o in p["options"] if o["mode"] == "car")


def v2_checks(client: TestClient) -> None:
    s = client.get("/api/search", params={"q": "Mid Valley"}).json()
    print(f"      search source: {s.get('source')}, first: {s['results'][0]['name'] if s.get('results') else None}")
    check("search 'Mid Valley' returns results", len(s.get("results", [])) >= 1)

    tue, sun = next_day(1), next_day(6)
    rush = plan(client, tue, "08:00", rain=0)
    modes = [o["mode"] for o in rush.get("options", [])]
    print(f"      {tue} 08:00 Puchong->KLCC: {[(o['mode'], o['minutes']) for o in rush.get('options', [])]}, "
          f"rec={rush.get('recommended')}, sources={rush.get('sources')}")
    check("plan has car, train and bus", modes == ["car", "train", "bus"])
    check("weekday 08:00 into KLCC: train recommended", rush.get("recommended") == "train")
    check("plan saved_minutes >= 0", rush.get("saved_minutes", -1) >= 0)
    check("plan has 17 slots", len(rush.get("slots", [])) == 17)
    check("time machine data: every slot has mult and rain",
          all("mult" in s and "rain" in s for s in rush.get("slots", [])))
    car = next(o for o in rush["options"] if o["mode"] == "car")
    check("time machine data: car segments have weight w",
          bool(car["segments"]) and all("w" in s for s in car["segments"]))
    mults = [s["mult"] for s in rush["slots"]]
    check("time machine: rush hour (08:00) busier than 06:00", mults[8] > mults[0])
    train = next(o for o in rush["options"] if o["mode"] == "train")
    check("train legs include a ride with a line colour",
          any(l["type"] == "ride" and l["color"].startswith("#") for l in train["legs"]))

    quiet = plan(client, sun, "11:00", rain=0)
    check("Sunday 11:00 car faster than weekday 08:00", car_minutes(quiet) < car_minutes(rush))
    wet = plan(client, tue, "08:00", rain=20)
    check("20 mm rain: car slower than 0 mm", car_minutes(wet) > car_minutes(rush))

    live = plan(client, tue, "08:00")
    check("no override: weather source is open-meteo or fallback",
          live.get("weather", {}).get("source") in ("open-meteo", "fallback"))
    bad = client.post("/api/plan", json={"from": PUCHONG, "to": KLCC, "time": "25:99"})
    check("bad time returns 400", bad.status_code == 400)


def main() -> None:
    client = TestClient(app)
    h = client.get("/health").json()
    check("health ok and model_loaded", h.get("status") == "ok" and h.get("model_loaded") is True)

    routes = client.get("/routes").json()
    check("5 routes", len(routes) == 5)

    for r in routes:
        rid = r["id"]
        dry = client.post("/predict", json={"route_id": rid, "rain_override_mm": 0}).json()
        wet = client.post("/predict", json={"route_id": rid, "rain_override_mm": 20}).json()
        check(f"{rid}: 17 slots", len(dry.get("slots", [])) == 17 and len(wet.get("slots", [])) == 17)
        check(f"{rid}: saved_minutes >= 0",
              dry.get("saved_minutes", -1) >= 0 and wet.get("saved_minutes", -1) >= 0)
        check(f"{rid}: 20 mm slower at 08:00 than 0 mm",
              default_minutes(wet) > default_minutes(dry))

    live = client.post("/predict", json={"route_id": routes[0]["id"]}).json()
    print(f"      no-override rain_source: {live.get('rain_source')}")
    check("no override: 17 slots and rain_source is open-meteo or fallback",
          len(live.get("slots", [])) == 17 and live.get("rain_source") in ("open-meteo", "fallback"))

    bt = client.get("/backtest").json()
    print(f"      backtest: {bt}")
    check("backtest avg_saved_min >= 0",
          bt.get("avg_saved_min") is not None and bt["avg_saved_min"] >= 0)

    v2_checks(client)

    print("ALL PASS" if all(results) else "SOME CHECKS FAILED")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
