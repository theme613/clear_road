"""Build data/rail.json from the free Rapid KL rail GTFS (data.gov.my, no key).

Run once; the output is committed so the app works offline.
"""
import csv
import io
import json
import math
import re
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "rail.json"
URL = "https://api.data.gov.my/gtfs-static/prasarana?category=rapid-rail-kl"
TRANSFER_NEAR_M = 250  # stations closer than this on different lines are an interchange


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


UPPER_WORDS = {"kl", "klcc", "pwtc", "usj", "uob", "ioi", "upm", "uitm", "trx", "ss", "ttdi",
               "ukm", "mrt", "lrt", "brt", "rtm", "ppr", "lrt3"}


def clean_name(raw: str) -> str:
    """'KL SENTRAL - REDONE' -> 'KL Sentral - Redone', 'USJ7' -> 'USJ 7'."""
    text = re.sub(r"(?i)\b(usj|ss)(\d)", r"\1 \2", raw.strip().title())
    parts = re.split(r"([ \-])", text)
    return "".join(p.upper() if p.lower() in UPPER_WORDS else p for p in parts)


def to_minutes(t: str) -> float:
    h, m, s = (int(x) for x in t.split(":"))
    return h * 60 + m + s / 60


def read_csv(z: zipfile.ZipFile, name: str) -> list[dict]:
    with z.open(name) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))


def download() -> zipfile.ZipFile:
    resp = requests.get(URL, timeout=10)  # follows the redirect to the S3 zip
    resp.raise_for_status()
    return zipfile.ZipFile(io.BytesIO(resp.content))


def headways(freqs: list[dict], trip_id: str) -> list[list[float]]:
    """[[start_hour, end_hour, headway_min], ...] for one trip."""
    out = []
    for f in freqs:
        if f["trip_id"] == trip_id:
            out.append([round(to_minutes(f["start_time"]) / 60, 2),
                        round(to_minutes(f["end_time"]) / 60, 2),
                        round(int(f["headway_secs"]) / 60, 1)])
    return out


def build(z: zipfile.ZipFile) -> dict:
    routes = {r["route_id"]: r for r in read_csv(z, "routes.txt")}
    stops = {s["stop_id"]: s for s in read_csv(z, "stops.txt")}
    trips = read_csv(z, "trips.txt")
    stop_times = read_csv(z, "stop_times.txt")
    freqs = read_csv(z, "frequencies.txt")
    shapes: dict[str, list] = {}
    for p in read_csv(z, "shapes.txt"):
        shapes.setdefault(p["shape_id"], []).append(
            (int(p["shape_pt_sequence"]), round(float(p["shape_pt_lat"]), 5),
             round(float(p["shape_pt_lon"]), 5)))

    lines, stations, edges = {}, {}, []
    for rid, r in routes.items():
        # Weekday trip in direction 0 gives the full stop order for the line
        wk = next((t for t in trips if t["route_id"] == rid and t["direction_id"] == "0"
                   and t["service_id"] in ("MonFri", "weekday")), None)
        if wk is None:
            print(f"[build_rail] no weekday trip for {rid}, skipping")
            continue
        we = next((t for t in trips if t["route_id"] == rid and t["direction_id"] == "0"
                   and t["service_id"] in ("Sat", "SatSun", "weekend")), None)
        seq = sorted((st for st in stop_times if st["trip_id"] == wk["trip_id"]),
                     key=lambda st: int(st["stop_sequence"]))
        shape = [[lat, lon] for _, lat, lon in sorted(shapes.get(wk["shape_id"], []))]
        lines[rid] = {
            "id": rid, "short": r["route_short_name"], "name": r["route_long_name"],
            "color": "#" + r["route_color"].lstrip("#"), "kind": r["category"],
            "headway_weekday": headways(freqs, wk["trip_id"]),
            "headway_weekend": headways(freqs, we["trip_id"]) if we else [],
            "shape": shape, "stops": [st["stop_id"] for st in seq],
        }
        for st in seq:
            s = stops.get(st["stop_id"])
            if s is None:
                continue
            stations[st["stop_id"]] = {
                "id": st["stop_id"], "name": clean_name(s["stop_name"]), "line": rid,
                "lat": float(s["stop_lat"]), "lon": float(s["stop_lon"]),
            }
        for a, b in zip(seq, seq[1:]):
            mins = to_minutes(b["arrival_time"]) - to_minutes(a["departure_time"])
            if not 0 < mins < 15:
                mins = 2.5  # fallback when the timetable looks wrong
            edges.append({"a": a["stop_id"], "b": b["stop_id"], "minutes": round(mins, 2),
                          "line": rid})

    # Interchanges: same name or very close, on different lines
    ids = list(stations)
    transfers = 0
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            sa, sb = stations[a], stations[b]
            if sa["line"] == sb["line"]:
                continue
            d = haversine_m(sa["lat"], sa["lon"], sb["lat"], sb["lon"])
            if sa["name"].lower() == sb["name"].lower() or d <= TRANSFER_NEAR_M:
                edges.append({"a": a, "b": b, "minutes": 3.0 if d < 60 else 5.0,
                              "line": "transfer"})
                transfers += 1
    print(f"[build_rail] {len(lines)} lines, {len(stations)} stations, "
          f"{len(edges) - transfers} ride edges, {transfers} interchanges")
    return {"lines": lines, "stations": stations, "edges": edges}


def main() -> None:
    try:
        z = download()
    except Exception as e:
        print(f"[build_rail] download failed: {e}")
        if OUT.exists():
            print(f"[build_rail] keeping existing {OUT}")
        else:
            print("[build_rail] no rail data yet; connect to the internet and run: python ml/build_rail.py")
        return
    data = build(z)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, separators=(",", ":")))
    print(f"[build_rail] saved {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
