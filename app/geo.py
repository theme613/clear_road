"""Place search (Photon) and car routing (OSRM). Both free, no key, with offline fallbacks."""
import json
import math
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PHOTON_URL = "https://photon.komoot.io/api/"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving/"
KL_BBOX = "101.2,2.75,102.0,3.45"  # minLon,minLat,maxLon,maxLat (Klang Valley)
HEADERS = {"User-Agent": "ClearRoad-hackathon/1.0"}
KLCC = (3.1579, 101.7116)

SEARCH_CACHE: dict[str, list[dict]] = {}
ROUTE_CACHE: dict[str, dict] = {}
_LOCAL_PLACES: list[dict] = []


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def local_places() -> list[dict]:
    """Landmarks from data/places.json + every rail station (loaded once)."""
    if _LOCAL_PLACES:
        return _LOCAL_PLACES
    try:
        _LOCAL_PLACES.extend(json.loads((ROOT / "data" / "places.json").read_text()))
    except Exception as e:
        print(f"[geo] could not read data/places.json: {e}")
    try:
        rail = json.loads((ROOT / "data" / "rail.json").read_text())
        seen = set()
        for s in rail["stations"].values():
            line = rail["lines"][s["line"]]
            key = (s["name"].lower(), line["kind"])
            if key in seen:
                continue
            seen.add(key)
            _LOCAL_PLACES.append({"name": f"{s['name']} {line['kind']} station",
                                  "address": line["name"], "lat": s["lat"], "lon": s["lon"]})
    except Exception as e:
        print(f"[geo] could not read data/rail.json for search: {e}. Run: python ml/build_rail.py")
    return _LOCAL_PLACES


def search_local(q: str, limit: int = 6) -> list[dict]:
    words = q.lower().split()
    hits = [p for p in local_places() if all(w in p["name"].lower() for w in words)]
    hits.sort(key=lambda p: (not p["name"].lower().startswith(words[0]), len(p["name"])))
    return hits[:limit]


def search_photon(q: str, limit: int = 6) -> list[dict]:
    params = {"q": q, "lat": KLCC[0], "lon": KLCC[1], "limit": limit, "bbox": KL_BBOX, "lang": "en"}
    resp = requests.get(PHOTON_URL, params=params, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    out = []
    for f in resp.json().get("features", []):
        p, (lon, lat) = f["properties"], f["geometry"]["coordinates"]
        name = p.get("name") or p.get("street")
        if not name or name.lower().startswith(("platform", "entrance", "exit")):
            continue
        parts = [p.get("street"), p.get("district"), p.get("city") or p.get("county"), p.get("state")]
        address = ", ".join(dict.fromkeys(x for x in parts if x and x != name))
        out.append({"name": name, "address": address, "lat": lat, "lon": lon})
    return out


def search_places(q: str) -> tuple[list[dict], str]:
    """Local matches first (stations, landmarks), then Photon. Returns (results, source)."""
    q = q.strip()
    if len(q) < 2:
        return [], "none"
    key = q.lower()
    if key in SEARCH_CACHE:
        return SEARCH_CACHE[key], "cache"
    local = search_local(q, limit=3)
    try:
        remote = search_photon(q)
        source = "photon"
    except Exception as e:
        print(f"[geo] Photon search failed ({e}); using local places only (fallback)")
        return search_local(q), "fallback"
    merged, seen = [], set()
    for p in local + remote:
        k = p["name"].lower().replace(" lrt station", "").replace(" mrt station", "")
        if k not in seen:
            seen.add(k)
            merged.append(p)
    SEARCH_CACHE[key] = merged[:7]
    return merged[:7], source


def downsample(coords: list[list[float]], max_points: int = 400) -> list[list[float]]:
    if len(coords) <= max_points:
        return coords
    step = math.ceil(len(coords) / max_points)
    return coords[::step] + [coords[-1]]


def straight_route(a: tuple[float, float], b: tuple[float, float]) -> dict:
    km = haversine_km(*a, *b) * 1.35  # roads are ~35% longer than a straight line
    return {"km": round(km, 2), "minutes": round(km / 45 * 60, 1),  # ~OSRM free-flow speed in KL
            "coords": [list(a), list(b)], "source": "estimate"}


def osrm_route(a: tuple[float, float], b: tuple[float, float]) -> dict:
    """Free-flow car route. {km, minutes, coords [[lat, lon]], source}."""
    key = f"{a[0]:.4f},{a[1]:.4f};{b[0]:.4f},{b[1]:.4f}"
    if key in ROUTE_CACHE:
        return ROUTE_CACHE[key]
    url = f"{OSRM_URL}{a[1]},{a[0]};{b[1]},{b[0]}"
    try:
        resp = requests.get(url, params={"overview": "full", "geometries": "geojson"},
                            headers=HEADERS, timeout=10)
        resp.raise_for_status()
        route = resp.json()["routes"][0]
        coords = [[round(lat, 5), round(lon, 5)] for lon, lat in route["geometry"]["coordinates"]]
        out = {"km": round(route["distance"] / 1000, 2), "minutes": round(route["duration"] / 60, 1),
               "coords": downsample(coords), "source": "osrm"}
    except Exception as e:
        print(f"[geo] OSRM failed ({e}); using straight-line estimate (fallback)")
        return straight_route(a, b)
    ROUTE_CACHE[key] = out
    return out
