# Clear Road

KL commute planner in the style of Google Maps / Grab. Search **From → To** anywhere in the Klang
Valley, pick a date and time (for example tomorrow 08:00), and Clear Road compares:

- 🚗 **Car**: real road route, with predicted traffic (rush hour, rain, weekday/holiday) drawn as
  green / orange / red segments.
- 🚆 **Train**: LRT, MRT, Monorail and BRT routes on the official Rapid KL network, drawn in each
  line's official colour, with walking, waiting and changes.
- 🚌 **Bus**: a rough estimate (labelled as one).

What makes it different from a normal map app: everything is a **prediction for the time you
will travel**, not the traffic right now.

- **Prediction card**: "🚆 Take the train · Leave 08:00 → arrive 08:51 · saves 12 min vs driving",
  plus a tip when leaving at another time is much faster.
- **Car vs train vs bus rows** with predicted minutes and arrival times; tap one to show it on the map.
- **Time machine (leave-time forecast)**: car and train minutes for every 15 min, 2 hours before
  and after your time. Press ▶ Play and the map animates the predicted traffic on the car route
  (green → red as rush hour builds) while a badge shows which mode wins at each time.
- **Back to my route** button appears when you pan or zoom away from the route.
- **Demo chips** for presentations: 🌅 Rush hour, 🌧 Rainy evening, ☀️ Sunday.

100% free: no API keys, no sign-up, no database. Needs Python 3.10 or newer.

## Quickest way

```bash
./run.command
```

Sets up the venv, builds anything missing, starts the server and opens the browser.
(On macOS you can also double-click `run.command` in Finder.)

## Setup

```bash
python3 -m venv .venv          # use python3.11 if your python3 is older than 3.10
source .venv/bin/activate
pip install -r requirements.txt
```

## Data and models (already committed, only needed to rebuild)

```bash
python ml/pull_rain.py             # data/rain_history.csv (falls back to synthetic rain if offline)
python ml/build_rail.py            # data/rail.json: 8 lines, 187 stations from data.gov.my GTFS
python ml/simulate_congestion.py   # data/congestion.csv: simulated road traffic for any trip
python ml/train_congestion.py      # model/congestion.joblib + model/congestion_metrics.json
python ml/simulate.py              # (v1) data/trips.csv for the 5 fixed routes
python ml/train.py                 # (v1) model/model.joblib + model/metrics.json
```

## Run

```bash
uvicorn app.main:app --port 8000
```

Open http://localhost:8000

## Test

```bash
python scripts/smoke_test.py
```

## API

| Endpoint | What it does |
|---|---|
| `GET /api/search?q=Mid Valley` | Place search (stations and landmarks first, then Photon) |
| `POST /api/plan` | `{"from": {lat, lon, name}, "to": {...}, "date": "YYYY-MM-DD", "time": "HH:MM", "rain_override_mm": 20}` → car / train / bus, AI pick, 17 time slots |
| `GET /health`, `GET /routes`, `POST /predict`, `GET /backtest` | v1 endpoints (5 fixed routes), kept working |

## Free services used (no key) and what happens if they fail

| Service | Used for | If it fails |
|---|---|---|
| Open-Meteo | Rain and temperature forecast | 0 mm rain, marked "forecast unavailable" |
| OSRM (router.project-osrm.org) | Car route shape, distance, free-flow time | Straight line × 1.35, marked "estimated" |
| Photon (photon.komoot.io) | Place search | Built-in list of stations and landmarks |
| OpenFreeMap | Map background | Plain OpenStreetMap tiles; if no map at all, the panel still works |
| data.gov.my GTFS | Rail network (build time only) | Committed `data/rail.json` is used |

## Deploy on Render (one web service)

| Setting | Value |
|---|---|
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Env var | `PYTHON_VERSION=3.11.9` |

`data/rail.json`, `data/places.json`, `model/*.joblib`, `model/*metrics.json` and `data/trips.csv` are
committed, so no training happens on Render.

The free Render service sleeps when idle. Open the site 1 minute before the demo.

## Honest notes

- **Car traffic is simulated.** `ml/simulate_congestion.py` generates a traffic multiplier from a
  formula (morning rush into the city, evening rush out of it, rain, weekday/holiday, noise),
  and the model learns that formula. Real rain comes from Open-Meteo and real road shapes and
  free-flow times come from OSRM, but the traffic delay is not measured.
- **Train times are real timetable data**: stop-to-stop minutes and headways from the official
  Rapid KL GTFS. Crowding and delays are not modelled. KTM Komuter and ERL are not in this data.
- **Bus is an estimate**: car road time × 1.3 + waiting + walking. Real bus routes are not used yet.
- Real-data upgrade path: log real trip times (for example with `ml/check_tomtom.py` and a TomTom
  key, or your own trips), replace `data/congestion.csv` with real rows using the same columns, then
  run `python ml/train_congestion.py`. The API and page need no changes.
