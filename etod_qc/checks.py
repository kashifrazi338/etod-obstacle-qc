"""QC checks for e-TOD obstacle datasets. Every check yields :class:`Issue` objects."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree

CHECK_NAMES = {
    "ATT-001": "Missing mandatory attribute",
    "ATT-002": "Value outside allowed domain",
    "ATT-003": "Duplicate obstacle identifier",
    "GEO-001": "Invalid or empty geometry",
    "GEO-002": "Outside area of interest / invalid coordinates",
    "GEO-003": "Probable duplicate capture",
    "GEO-004": "Geometry type not allowed for obstacle type",
    "HGT-001": "Height out of plausible range",
    "HGT-002": "Elevation out of plausible range",
    "HGT-003": "Elevation inconsistent with ground + height",
    "ACC-001": "Horizontal accuracy does not meet area requirement",
    "ACC-002": "Vertical accuracy does not meet area requirement",
    "MRK-001": "Tall obstacle without marking or lighting",
    "DAT-001": "Capture date invalid, in the future or too old",
}
SEVERITY_ORDER = {"CRITICAL": 0, "MAJOR": 1, "MINOR": 2}


@dataclass
class Issue:
    code: str
    obstacle_id: str
    row: int
    field: str
    value: str
    expected: str
    description: str
    lon: float | None = None
    lat: float | None = None
    severity: str = field(default="MAJOR")

    @property
    def check(self) -> str:
        return CHECK_NAMES.get(self.code, self.code)


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _xy(rec):
    g = rec.get("_geom")
    if g is None or g.is_empty:
        return None, None
    p = g.representative_point()
    return round(p.x, 7), round(p.y, 7)


def _issue(rec, code, fld, value, expected, desc):
    lon, lat = _xy(rec)
    return Issue(code, str(rec.get("obstacle_id") or f"row {rec['_row']}"), rec["_row"], fld,
                 "" if value is None else str(value), expected, desc, lon, lat)


# --- attribute checks -------------------------------------------------------------

def check_mandatory(records, rules):
    for rec in records:
        for f in rules["mandatory_fields"]:
            if rec.get(f) is None:
                yield _issue(rec, "ATT-001", f, None, "value required", f"'{f}' is empty or missing")


def check_domains(records, rules):
    for rec in records:
        for f, allowed in rules["domains"].items():
            v = rec.get(f)
            if v is not None and str(v) not in {str(a) for a in allowed}:
                exp = ", ".join(map(str, allowed))
                if len(allowed) > 6:
                    exp = ", ".join(map(str, allowed[:4])) + f" … ({len(allowed)} allowed values)"
                yield _issue(rec, "ATT-002", f, v, exp,
                             f"'{v}' is not an allowed value for '{f}'")


def check_duplicate_ids(records, rules):
    counts = Counter(r.get("obstacle_id") for r in records if r.get("obstacle_id") is not None)
    for rec in records:
        oid = rec.get("obstacle_id")
        if oid is not None and counts[oid] > 1:
            yield _issue(rec, "ATT-003", "obstacle_id", oid, "unique",
                         f"identifier used by {counts[oid]} records")


# --- geometry checks --------------------------------------------------------------

def check_geometry(records, rules):
    bbox = rules.get("aoi_bbox")
    for rec in records:
        g = rec.get("_geom")
        if rec.get("_geom_error") or g is None or g.is_empty:
            yield _issue(rec, "GEO-001", "geometry", rec.get("_geom_error") or "empty", "valid geometry",
                         "geometry missing or could not be parsed")
            continue
        if not g.is_valid:
            from shapely.validation import explain_validity
            yield _issue(rec, "GEO-001", "geometry", explain_validity(g), "valid geometry",
                         "geometry is invalid")
        minx, miny, maxx, maxy = g.bounds
        if minx < -180 or maxx > 180 or miny < -90 or maxy > 90:
            yield _issue(rec, "GEO-002", "geometry", f"{minx:.5f},{miny:.5f}", "WGS84 lon/lat",
                         "coordinates outside valid longitude/latitude range")
        elif bbox and not (bbox[0] <= minx and maxx <= bbox[2] and bbox[1] <= miny and maxy <= bbox[3]):
            yield _issue(rec, "GEO-002", "geometry", f"{minx:.5f},{miny:.5f}", f"inside {bbox}",
                         "obstacle lies outside the area of interest")


def check_geometry_type(records, rules):
    gr = rules.get("geometry_rules", {})
    line_only, no_lines = set(gr.get("line_only", [])), set(gr.get("no_lines", []))
    for rec in records:
        g, t = rec.get("_geom"), rec.get("obstacle_type")
        if g is None or g.is_empty or t is None:
            continue
        is_line = g.geom_type in ("LineString", "MultiLineString")
        if t in line_only and not is_line:
            yield _issue(rec, "GEO-004", "geometry", g.geom_type, "LineString",
                         f"{t} must be captured as a line")
        elif t in no_lines and is_line:
            yield _issue(rec, "GEO-004", "geometry", g.geom_type, "Point or Polygon",
                         f"{t} should not be captured as a line")


def _projector(records):
    pts = [_xy(r) for r in records]
    lons = [p[0] for p in pts if p[0] is not None and -180 <= p[0] <= 180]
    lats = [p[1] for p in pts if p[1] is not None and -90 <= p[1] <= 90]
    if not lons:
        return None
    lon0, lat0 = float(np.median(lons)), float(np.median(lats))
    zone = int((lon0 + 180) // 6) + 1
    epsg = (32600 if lat0 >= 0 else 32700) + zone
    return Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)


def check_near_duplicates(records, rules):
    cfg = rules["near_duplicate"]
    tr = _projector(records)
    if tr is None:
        return
    idx, xy = [], []
    for i, r in enumerate(records):
        lon, lat = _xy(r)
        if lon is not None and -180 <= lon <= 180 and -90 <= lat <= 90:
            idx.append(i)
            xy.append(tr.transform(lon, lat))
    if len(xy) < 2:
        return
    tree = cKDTree(np.array(xy))
    for a, b in sorted(tree.query_pairs(cfg["distance_m"])):
        ra, rb = records[idx[a]], records[idx[b]]
        ha, hb = _num(ra.get("height_m")), _num(rb.get("height_m"))
        if ha is None or hb is None or abs(ha - hb) <= cfg["height_diff_m"]:
            d = float(np.hypot(*(np.array(xy[a]) - np.array(xy[b]))))
            for r, other in ((ra, rb), (rb, ra)):
                yield _issue(r, "GEO-003", "geometry", f"{d:.2f} m from {other.get('obstacle_id')}",
                             f"> {cfg['distance_m']} m apart",
                             f"near-coincident with {other.get('obstacle_id')} — probable double capture")


# --- height / elevation checks ----------------------------------------------------

def check_heights(records, rules):
    hmin, hmax = rules["height_m"]["min"], rules["height_m"]["max"]
    emin, emax = rules["elevation_m"]["min"], rules["elevation_m"]["max"]
    tol = rules["elevation_consistency_tolerance_m"]
    for rec in records:
        h, e = _num(rec.get("height_m")), _num(rec.get("elevation_m"))
        if rec.get("height_m") is not None and (h is None or not hmin <= h <= hmax):
            yield _issue(rec, "HGT-001", "height_m", rec.get("height_m"), f"{hmin}–{hmax} m",
                         "height is not numeric or outside plausible range")
        if rec.get("elevation_m") is not None and (e is None or not emin <= e <= emax):
            yield _issue(rec, "HGT-002", "elevation_m", rec.get("elevation_m"), f"{emin}–{emax} m",
                         "elevation is not numeric or outside plausible range")
        g = _num(rec.get("ground_elev_m"))
        if None not in (g, h, e) and abs(e - (g + h)) > tol:
            yield _issue(rec, "HGT-003", "elevation_m", f"{e:.2f}", f"{g + h:.2f} ± {tol}",
                         f"top elevation differs from ground ({g:.2f}) + height ({h:.2f}) "
                         f"by {e - (g + h):+.2f} m")


def check_accuracy(records, rules):
    req = rules["accuracy_requirements_m"]
    for rec in records:
        area = rec.get("area")
        if area is None or str(area) not in req:
            continue
        r = req[str(area)]
        for f, code, key in (("horizontal_accuracy_m", "ACC-001", "horizontal"),
                             ("vertical_accuracy_m", "ACC-002", "vertical")):
            v = _num(rec.get(f))
            if rec.get(f) is not None and (v is None or v > r[key]):
                yield _issue(rec, code, f, rec.get(f), f"≤ {r[key]} m (Area {area})",
                             f"{key} accuracy {rec.get(f)} m does not meet Area {area} requirement")


def check_marking_lighting(records, rules):
    limit = rules["marking_lighting_height_m"]
    for rec in records:
        h = _num(rec.get("height_m"))
        if h is not None and h >= limit and rec.get("lighting") == "NO" and rec.get("marking") == "NO":
            yield _issue(rec, "MRK-001", "lighting/marking", "NO/NO", f"marked or lit at ≥ {limit} m",
                         f"{h:.0f} m obstacle recorded with no marking and no lighting — verify")


def check_dates(records, rules, today: date | None = None):
    today = today or date.today()
    max_age = rules["capture_date"]["max_age_years"]
    for rec in records:
        v = rec.get("capture_date")
        if v is None:
            continue
        try:
            d = datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
        except ValueError:
            yield _issue(rec, "DAT-001", "capture_date", v, "YYYY-MM-DD", "date cannot be parsed")
            continue
        if d > today:
            yield _issue(rec, "DAT-001", "capture_date", v, f"≤ {today}", "capture date is in the future")
        elif (today - d).days > max_age * 365.25:
            yield _issue(rec, "DAT-001", "capture_date", v, f"within {max_age} years",
                         "data older than the currency requirement")


ALL_CHECKS = [check_mandatory, check_domains, check_duplicate_ids, check_geometry,
              check_geometry_type, check_near_duplicates, check_heights, check_accuracy,
              check_marking_lighting, check_dates]


def run_checks(records: list[dict], rules: dict, today: date | None = None) -> list[Issue]:
    issues = []
    for chk in ALL_CHECKS:
        found = chk(records, rules, today) if chk is check_dates else chk(records, rules)
        issues.extend(found)
    for i in issues:
        i.severity = rules.get("severity", {}).get(i.code, "MAJOR")
    issues.sort(key=lambda i: (SEVERITY_ORDER.get(i.severity, 9), i.code, i.row))
    return issues
