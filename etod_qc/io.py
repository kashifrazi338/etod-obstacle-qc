"""Load obstacle datasets (GeoJSON or CSV) and QC rules."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml
from shapely import wkt
from shapely.geometry import Point, shape

DEFAULT_RULES = Path(__file__).resolve().parent.parent / "rules" / "default_rules.yaml"


def load_rules(path: str | Path | None = None) -> dict:
    with open(path or DEFAULT_RULES, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _clean(v):
    if isinstance(v, str):
        v = v.strip()
        return None if v == "" else v
    return v


def load_obstacles(path: str | Path) -> list[dict]:
    """Return a list of records: ``{"_row": n, "_geom": shapely|None, "_geom_error": str|None, **attrs}``.

    GeoJSON: geometry from the feature. CSV: a ``wkt`` column, or ``lon``/``lat`` columns.
    Coordinates are expected in WGS84 (EPSG:4326), as e-TOD datasets are delivered.
    """
    path = Path(path)
    records = []
    if path.suffix.lower() in {".geojson", ".json"}:
        data = json.loads(path.read_text(encoding="utf-8"))
        for i, feat in enumerate(data.get("features", []), start=1):
            rec = {k: _clean(v) for k, v in (feat.get("properties") or {}).items()}
            rec["_row"], rec["_geom"], rec["_geom_error"] = i, None, None
            try:
                rec["_geom"] = shape(feat["geometry"]) if feat.get("geometry") else None
            except Exception as e:  # malformed geometry
                rec["_geom_error"] = str(e)
            records.append(rec)
    elif path.suffix.lower() == ".csv":
        with open(path, newline="", encoding="utf-8-sig") as f:
            for i, row in enumerate(csv.DictReader(f), start=2):  # row 1 = header
                rec = {k.strip(): _clean(v) for k, v in row.items() if k}
                rec["_row"], rec["_geom"], rec["_geom_error"] = i, None, None
                try:
                    if rec.get("wkt"):
                        rec["_geom"] = wkt.loads(rec.pop("wkt"))
                    elif rec.get("lon") is not None and rec.get("lat") is not None:
                        rec["_geom"] = Point(float(rec["lon"]), float(rec["lat"]))
                except Exception as e:
                    rec["_geom_error"] = str(e)
                records.append(rec)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix} (use .geojson or .csv)")
    return records
