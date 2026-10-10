"""Optional step 8: compare live TomTom speeds with free-flow for each route.
Not used by the app. Needs TOMTOM_API_KEY in the environment."""
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/relative0/10/json"


def check_route(route: dict, key: str) -> None:
    params = {"point": f"{route['check_lat']},{route['check_lon']}", "key": key}
    try:
        resp = requests.get(URL, params=params, timeout=10)
        if resp.status_code != 200:
            print(f"[check_tomtom] {route['id']}: status {resp.status_code}: {resp.text[:200]}")
            return
        flow = resp.json()["flowSegmentData"]
        cur, free = flow["currentSpeed"], flow["freeFlowSpeed"]
        ratio = cur / free if free else float("nan")
        print(f"[check_tomtom] {route['id']}: currentSpeed={cur} freeFlowSpeed={free} ratio={ratio:.2f}")
    except Exception as e:
        print(f"[check_tomtom] {route['id']}: request failed: {e}")


def main() -> int:
    key = os.environ.get("TOMTOM_API_KEY", "").strip()
    if not key:
        print("TOMTOM_API_KEY not set - skipping")
        return 0
    try:
        routes = json.loads((ROOT / "routes.json").read_text())
    except Exception as e:
        print(f"[check_tomtom] could not read routes.json: {e}")
        return 0
    for route in routes:
        check_route(route, key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
