"""Train routing over the Rapid KL rail network (data/rail.json, built by ml/build_rail.py)."""
import heapq
import json
from pathlib import Path
from typing import Optional

from app.geo import haversine_km

ROOT = Path(__file__).resolve().parent.parent
RAIL_FILE = ROOT / "data" / "rail.json"
MAX_ACCESS_KM = 6.0      # farther than this from any station -> no train option
WALK_MAX_KM = 1.2        # walk to the station if closer, otherwise take a Grab / drive
WALK_KMH, FEEDER_KMH = 4.5, 30.0

_RAIL: dict = {}
_ADJ: dict[str, list[dict]] = {}


def load_rail() -> dict:
    """Load rail.json once and build the adjacency list."""
    if _RAIL:
        return _RAIL
    try:
        _RAIL.update(json.loads(RAIL_FILE.read_text()))
    except Exception as e:
        print(f"[rail] could not read {RAIL_FILE} ({e}). Run: python ml/build_rail.py")
        return _RAIL
    for e in _RAIL["edges"]:
        _ADJ.setdefault(e["a"], []).append({"to": e["b"], **e})
        _ADJ.setdefault(e["b"], []).append({"to": e["a"], **e})
    return _RAIL


def headway(line_id: str, hour: float, weekend: bool) -> Optional[float]:
    """Minutes between trains at this hour, or None if the line is not running."""
    line = _RAIL["lines"][line_id]
    table = (line["headway_weekend"] if weekend else line["headway_weekday"]) or line["headway_weekday"]
    for start, end, mins in table:
        if start <= hour < end:
            return mins
    return None


def access_leg(place: tuple[float, float], station: dict, feeder_mult: float,
               rain_mm: float, to_station: bool) -> dict:
    """Walk (or Grab/drive if far) between a place and a station."""
    km = haversine_km(place[0], place[1], station["lat"], station["lon"])
    st = [station["lat"], station["lon"]]
    coords = [list(place), st] if to_station else [st, list(place)]
    if km <= WALK_MAX_KM:
        minutes = km * 1.3 / WALK_KMH * 60 * (1 + 0.02 * min(rain_mm, 20))
        return {"type": "walk", "minutes": round(max(minutes, 1), 1), "km": round(km * 1.3, 2),
                "coords": coords, "station": station["name"]}
    minutes = 3 + km * 1.35 / FEEDER_KMH * 60 * feeder_mult  # 3 min pickup
    return {"type": "feeder", "minutes": round(minutes, 1), "km": round(km * 1.35, 2),
            "coords": coords, "station": station["name"]}


def nearest_stations(place: tuple[float, float], n: int = 4) -> list[dict]:
    st = sorted(_RAIL["stations"].values(),
                key=lambda s: haversine_km(place[0], place[1], s["lat"], s["lon"]))
    return [s for s in st[:n] if haversine_km(place[0], place[1], s["lat"], s["lon"]) <= MAX_ACCESS_KM]


def slice_shape(line_id: str, a: dict, b: dict) -> list[list[float]]:
    """Part of the line's track between two stations, in travel order."""
    shape = _RAIL["lines"][line_id]["shape"]
    if not shape:
        return [[a["lat"], a["lon"]], [b["lat"], b["lon"]]]

    def idx(s: dict) -> int:
        return min(range(len(shape)), key=lambda i: (shape[i][0] - s["lat"]) ** 2 + (shape[i][1] - s["lon"]) ** 2)

    i, j = idx(a), idx(b)
    part = shape[min(i, j):max(i, j) + 1]
    if i > j:
        part = part[::-1]
    return [[a["lat"], a["lon"]]] + part + [[b["lat"], b["lon"]]]


def plan_train(a: tuple[float, float], b: tuple[float, float], hour: float, weekend: bool,
               feeder_mult: float, rain_mm: float) -> Optional[dict]:
    """Fastest train journey from place a to place b, or None if no sensible option."""
    if not load_rail():
        return None
    stations = _RAIL["stations"]
    starts, ends = nearest_stations(a), nearest_stations(b)
    if not starts or not ends:
        return None

    def wait(line_id: str) -> Optional[float]:
        h = headway(line_id, hour, weekend)
        return None if h is None else h / 2

    dist: dict[str, float] = {}
    prev: dict[str, tuple] = {}
    heap: list = []
    for s in starts:
        w = wait(s["line"])
        if w is None:
            continue
        leg = access_leg(a, s, feeder_mult, rain_mm, to_station=True)
        cost = leg["minutes"] + w
        if cost < dist.get(s["id"], 1e9):
            dist[s["id"]] = cost
            prev[s["id"]] = (None, {"line": "board", "minutes": w}, leg)
            heapq.heappush(heap, (cost, s["id"]))

    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, 1e9):
            continue
        for e in _ADJ.get(u, []):
            v = e["to"]
            if e["line"] == "transfer":
                w = wait(stations[v]["line"])
                if w is None:
                    continue
                cost = e["minutes"] + w
            else:
                if headway(e["line"], hour, weekend) is None:
                    continue
                cost = e["minutes"]
            if d + cost < dist.get(v, 1e9):
                dist[v] = d + cost
                prev[v] = (u, {**e, "wait": w if e["line"] == "transfer" else 0}, None)
                heapq.heappush(heap, (d + cost, v))

    best, best_total, best_egress = None, 1e9, None
    for s in ends:
        if s["id"] not in dist:
            continue
        leg = access_leg(b, s, feeder_mult, rain_mm, to_station=False)
        total = dist[s["id"]] + leg["minutes"]
        if total < best_total:
            best, best_total, best_egress = s["id"], total, leg
    if best is None:
        return None
    return build_legs(best, prev, best_egress, round(best_total, 1))


def build_legs(end_id: str, prev: dict, egress: dict, total: float) -> dict:
    """Turn the Dijkstra path into walk / ride / transfer legs."""
    stations, lines = _RAIL["stations"], _RAIL["lines"]
    path, node = [], end_id
    while node is not None:
        p, edge, access = prev[node]
        path.append((node, edge, access))
        node = p
    path.reverse()

    legs = [path[0][2]]
    first_wait = path[0][1]["minutes"]
    ride = None
    for i in range(1, len(path)):
        node, edge, _ = path[i]
        frm = path[i - 1][0]
        if edge["line"] == "transfer":
            ride = None
            legs.append({"type": "transfer", "minutes": round(edge["minutes"], 1),
                         "station": stations[node]["name"], "wait": round(edge["wait"], 1),
                         "coords": [[stations[frm]["lat"], stations[frm]["lon"]],
                                    [stations[node]["lat"], stations[node]["lon"]]]})
            continue
        if ride is None or ride["line"] != edge["line"]:
            line = lines[edge["line"]]
            ride = {"type": "ride", "line": edge["line"], "line_name": line["name"],
                    "short": line["short"], "color": line["color"], "kind": line["kind"],
                    "from": stations[frm]["name"], "from_id": frm, "to": stations[node]["name"],
                    "to_id": node, "stops": 0, "minutes": 0.0,
                    "wait": round(first_wait if len(legs) == 1 else legs[-1].get("wait", 0), 1)}
            legs.append(ride)
        ride["stops"] += 1
        ride["minutes"] = round(ride["minutes"] + edge["minutes"], 1)
        ride["to"], ride["to_id"] = stations[node]["name"], node
    for leg in legs:
        if leg["type"] == "ride":
            leg["coords"] = slice_shape(leg["line"], stations[leg.pop("from_id")], stations[leg.pop("to_id")])
    legs.append(egress)
    rides = [l for l in legs if l["type"] == "ride"]
    if not rides:
        return None  # nearest stations are the same one; not a train trip
    return {
        "minutes": total,
        "legs": legs,
        "lines": [l["short"] for l in rides],
        "line_names": [l["line_name"] for l in rides],
        "walk_minutes": round(sum(l["minutes"] for l in legs if l["type"] == "walk"), 1),
        "wait_minutes": round(sum(l.get("wait", 0) for l in rides), 1),
        "feeder": any(l["type"] == "feeder" for l in legs),
    }
