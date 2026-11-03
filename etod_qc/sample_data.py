"""Generate a synthetic obstacle dataset around a fictitious aerodrome, with known
errors injected, so the QC can be demonstrated and tested. No real data is used."""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np

# Fictitious aerodrome reference point in open desert (not a real airport)
ARP = (45.40, 23.60)

TYPES = [("BUILDING", 4, 40), ("TOWER", 20, 120), ("MAST", 15, 90), ("ANTENNA", 8, 60),
         ("POLE", 6, 18), ("TREE", 4, 15), ("LIGHT_STANDARD", 10, 30), ("TANK", 8, 25),
         ("CHIMNEY", 20, 80), ("WIND_TURBINE", 80, 160), ("CRANE", 30, 90)]

AREA_ACC = {"3": (0.3, 0.3), "2a": (2.5, 1.5), "2b": (3.0, 2.0), "2c": (3.5, 2.0), "1": (20.0, 10.0)}

ERROR_KINDS = ["ATT-001", "ATT-002", "ATT-003", "GEO-001", "GEO-002", "GEO-003", "GEO-004",
               "HGT-001", "HGT-003", "ACC-001", "ACC-002", "MRK-001", "DAT-001"]


def _offset(lon, lat, dx_m, dy_m):
    return (lon + dx_m / (111_320 * math.cos(math.radians(lat))), lat + dy_m / 110_540)


def _area_for(dist_m):
    if dist_m < 1_200:
        return "3"
    if dist_m < 4_000:
        return "2a"
    if dist_m < 10_000:
        return "2b"
    if dist_m < 20_000:
        return "2c"
    return "1"


def generate(out_path: str | Path, n: int = 300, seed: int = 11, today: date | None = None) -> dict:
    """Write a GeoJSON with ``n`` clean obstacles plus injected errors.

    Returns ``{"path": Path, "injected": {obstacle_id: code}}`` for verification.
    """
    rng = np.random.default_rng(seed)
    today = today or date.today()
    feats, used = [], []

    def far_enough(lon, lat):
        return all(abs(lon - u[0]) > 1e-4 or abs(lat - u[1]) > 1e-4 for u in used)

    i = 0
    while len(feats) < n:
        dist = float(rng.choice([rng.uniform(200, 1_200), rng.uniform(1_200, 4_000),
                                 rng.uniform(4_000, 10_000), rng.uniform(10_000, 30_000)]))
        ang = rng.uniform(0, 2 * math.pi)
        lon, lat = _offset(*ARP, dist * math.cos(ang), dist * math.sin(ang))
        if not far_enough(lon, lat):
            continue
        used.append((lon, lat))
        i += 1
        area = _area_for(dist)
        if len(feats) % 25 == 24:   # some power lines
            t, h = "POWER_LINE", round(float(rng.uniform(18, 45)), 1)
            lon2, lat2 = _offset(lon, lat, rng.uniform(-600, 600), rng.uniform(-600, 600))
            geom = {"type": "LineString", "coordinates": [[round(lon, 7), round(lat, 7)],
                                                          [round(lon2, 7), round(lat2, 7)]]}
        else:
            t, lo, hi = TYPES[int(rng.integers(len(TYPES)))]
            h = round(float(rng.uniform(lo, hi)), 1)
            geom = {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]}
        ground = round(float(560 + rng.normal(0, 6)), 2)
        ha, va = AREA_ACC[area]
        tall = h >= 150
        props = {
            "obstacle_id": f"OBS-{i:05d}", "obstacle_type": t,
            "ground_elev_m": ground, "height_m": h, "elevation_m": round(ground + h, 2),
            "horizontal_accuracy_m": round(ha * float(rng.uniform(0.5, 1.0)), 2),
            "vertical_accuracy_m": round(va * float(rng.uniform(0.5, 1.0)), 2),
            "area": area,
            "lighting": "YES" if tall or rng.random() < 0.3 else "NO",
            "marking": "YES" if tall or rng.random() < 0.2 else "NO",
            "capture_date": str(today - timedelta(days=int(rng.integers(30, 900)))),
            "source": "SYNTHETIC",
        }
        feats.append({"type": "Feature", "properties": props, "geometry": geom})

    # ---- inject one known error per kind (twice for most kinds) -------------------
    injected: dict[str, str] = {}
    candidates = [f for f in feats if f["geometry"]["type"] == "Point"]
    picks = iter(rng.permutation(len(candidates)).tolist())

    def take():
        return candidates[next(picks)]

    for _ in range(2):
        f = take(); f["properties"]["vertical_accuracy_m"] = None; injected[f["properties"]["obstacle_id"]] = "ATT-001"
        f = take(); f["properties"]["obstacle_type"] = "BLDG"; injected[f["properties"]["obstacle_id"]] = "ATT-002"
        f = take(); p = f["properties"]; p["height_m"] = round(p["height_m"] + 12.0, 1)  # elevation not updated
        injected[p["obstacle_id"]] = "HGT-003"
        f = take(); p = f["properties"]
        lim = {"3": 0.5, "2a": 5, "2b": 5, "2c": 5, "1": 50}[p["area"]]
        p["horizontal_accuracy_m"] = round(lim * 2.4, 2); injected[p["obstacle_id"]] = "ACC-001"
        f = take(); p = f["properties"]
        lim = {"3": 0.5, "2a": 3, "2b": 3, "2c": 3, "1": 30}[p["area"]]
        p["vertical_accuracy_m"] = round(lim * 1.8, 2); injected[p["obstacle_id"]] = "ACC-002"
        f = take(); p = f["properties"]; p["capture_date"] = str(today + timedelta(days=40))
        injected[p["obstacle_id"]] = "DAT-001"

    # duplicate identifier: give a record another record's id
    a, b = take(), take()
    b["properties"]["obstacle_id"] = a["properties"]["obstacle_id"]
    injected[a["properties"]["obstacle_id"]] = "ATT-003"

    # empty geometry
    f = take(); f["geometry"] = None; injected[f["properties"]["obstacle_id"]] = "GEO-001"
    # swapped lat/lon outside range
    f = take(); x, y = f["geometry"]["coordinates"]; f["geometry"]["coordinates"] = [x, y + 80]
    injected[f["properties"]["obstacle_id"]] = "GEO-002"
    # tower captured as a line
    f = take(); x, y = f["geometry"]["coordinates"]
    f["geometry"] = {"type": "LineString", "coordinates": [[x, y], [x + 0.0003, y]]}
    f["properties"]["obstacle_type"] = "TOWER"; injected[f["properties"]["obstacle_id"]] = "GEO-004"
    # negative height + elevation fixed accordingly
    f = take(); p = f["properties"]; p["height_m"] = -3.0; p["elevation_m"] = round(p["ground_elev_m"] - 3.0, 2)
    injected[p["obstacle_id"]] = "HGT-001"
    # tall unmarked unlit mast (consistent elevation)
    f = take(); p = f["properties"]
    p.update(obstacle_type="MAST", height_m=185.0, elevation_m=round(p["ground_elev_m"] + 185.0, 2),
             lighting="NO", marking="NO")
    injected[p["obstacle_id"]] = "MRK-001"
    # double capture: copy of an existing point 0.8 m away with new id
    src = take()
    dup = json.loads(json.dumps(src))
    x, y = src["geometry"]["coordinates"]
    dup["geometry"]["coordinates"] = list(_offset(x, y, 0.6, 0.5))
    dup["properties"]["obstacle_id"] = f"OBS-{i + 1:05d}"
    feats.append(dup)
    injected[dup["properties"]["obstacle_id"]] = "GEO-003"

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"type": "FeatureCollection", "name": "synthetic_obstacles",
                                    "features": feats}, indent=1))
    return {"path": out_path, "injected": injected}
