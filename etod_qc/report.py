"""Outputs: vendor issue log (Excel), HTML summary and GeoJSON of flagged obstacles."""
from __future__ import annotations

import base64
import html
import io
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402
from shapely.geometry import mapping  # noqa: E402

from .checks import CHECK_NAMES, SEVERITY_ORDER, Issue  # noqa: E402

SEV_FILL = {"CRITICAL": "F4C7C3", "MAJOR": "FCE8B2", "MINOR": "D9EAD3"}
SEV_COLOR = {"CRITICAL": "#c0392b", "MAJOR": "#e08e0b", "MINOR": "#5b8c3a"}
HEAD_FILL = PatternFill("solid", fgColor="1D2621")
THIN = Border(*[Side(style="thin", color="D0CCC2")] * 4)


def _verdict(issues: list[Issue]) -> str:
    sev = Counter(i.severity for i in issues)
    if sev["CRITICAL"]:
        return "REJECTED — critical issues must be corrected and the dataset resubmitted"
    if sev["MAJOR"]:
        return "CONDITIONALLY ACCEPTED — major issues to be corrected"
    return "ACCEPTED" if not issues else "ACCEPTED WITH MINOR COMMENTS"


def write_excel(issues: list[Issue], records: list[dict], out_path: str | Path, *,
                dataset_name: str, rules_name: str) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    sev = Counter(i.severity for i in issues)
    flagged = len({i.row for i in issues})
    ws["A1"] = "e-TOD Obstacle Data — QC Issue Report"
    ws["A1"].font = Font(size=16, bold=True)
    meta = [("Dataset", dataset_name), ("Rules", rules_name),
            ("Checked on", datetime.now().strftime("%Y-%m-%d %H:%M")),
            ("Records checked", len(records)), ("Records with issues", flagged),
            ("Total issues", len(issues)), ("Verdict", _verdict(issues))]
    for r, (k, v) in enumerate(meta, start=3):
        ws.cell(r, 1, k).font = Font(bold=True)
        ws.cell(r, 2, v)
    ws.cell(9, 2).font = Font(bold=True, color="C0392B" if sev["CRITICAL"] else "1D2621")

    r0 = 12
    for c, h in enumerate(["Code", "Check", "Severity", "Issues"], start=1):
        cell = ws.cell(r0, c, h)
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), HEAD_FILL
    by_code = Counter(i.code for i in issues)
    codes = sorted(CHECK_NAMES, key=lambda c: c)
    for r, code in enumerate(codes, start=r0 + 1):
        s = next((i.severity for i in issues if i.code == code), "")
        vals = [code, CHECK_NAMES[code], s, by_code.get(code, 0)]
        for c, v in enumerate(vals, start=1):
            cell = ws.cell(r, c, v)
            cell.border = THIN
            if s and by_code.get(code):
                cell.fill = PatternFill("solid", fgColor=SEV_FILL[s])
    for col, w in zip("ABCD", (18, 52, 12, 10)):
        ws.column_dimensions[col].width = w
    ws.column_dimensions["B"].width = 70

    # Issue log — the sheet sent to the vendor
    log = wb.create_sheet("Issue Log")
    headers = ["Issue #", "Severity", "Code", "Check", "Obstacle ID", "Source row", "Field",
               "Value found", "Expected", "Description", "Longitude", "Latitude",
               "Status", "Vendor response", "Reviewer remarks"]
    widths = [8, 11, 9, 34, 14, 10, 20, 22, 24, 52, 12, 12, 12, 30, 30]
    for c, (h, w) in enumerate(zip(headers, widths), start=1):
        cell = log.cell(1, c, h)
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), HEAD_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        log.column_dimensions[get_column_letter(c)].width = w
    for n, i in enumerate(issues, start=1):
        row = [n, i.severity, i.code, i.check, i.obstacle_id, i.row, i.field, i.value,
               i.expected, i.description, i.lon, i.lat, "Open", "", ""]
        for c, v in enumerate(row, start=1):
            cell = log.cell(n + 1, c, v)
            cell.border = THIN
            cell.alignment = Alignment(vertical="top", wrap_text=c in (4, 8, 9, 10))
        log.cell(n + 1, 2).fill = PatternFill("solid", fgColor=SEV_FILL.get(i.severity, "FFFFFF"))
    log.freeze_panes = "A2"
    log.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(len(issues) + 1, 2)}"
    dv = DataValidation(type="list", formula1='"Open,Fixed,Rejected,Accepted as is"', allow_blank=True)
    log.add_data_validation(dv)
    dv.add(f"M2:M{max(len(issues) + 1, 2)}")

    # Per-obstacle summary
    ob = wb.create_sheet("Obstacles")
    per = {}
    for i in issues:
        p = per.setdefault(i.row, {"id": i.obstacle_id, "n": 0, "worst": "MINOR", "codes": set()})
        p["n"] += 1
        p["codes"].add(i.code)
        if SEVERITY_ORDER[i.severity] < SEVERITY_ORDER[p["worst"]]:
            p["worst"] = i.severity
    for c, h in enumerate(["Source row", "Obstacle ID", "Type", "Height (m)", "Issues",
                           "Worst severity", "Codes"], start=1):
        cell = ob.cell(1, c, h)
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), HEAD_FILL
    for r, rec in enumerate(records, start=2):
        p = per.get(rec["_row"], {"n": 0, "worst": "", "codes": set()})
        vals = [rec["_row"], rec.get("obstacle_id"), rec.get("obstacle_type"), rec.get("height_m"),
                p["n"], p["worst"], ", ".join(sorted(p["codes"]))]
        for c, v in enumerate(vals, start=1):
            ob.cell(r, c, v)
        if p["worst"]:
            ob.cell(r, 6).fill = PatternFill("solid", fgColor=SEV_FILL[p["worst"]])
    for col, w in zip("ABCDEFG", (10, 14, 16, 11, 8, 14, 40)):
        ob.column_dimensions[col].width = w
    ob.freeze_panes = "A2"
    ob.auto_filter.ref = f"A1:G{len(records) + 1}"

    for sheet in wb.worksheets:   # print-ready: landscape, fit all columns on one page width
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
    log.print_title_rows = "1:1"

    out_path = Path(out_path)
    wb.save(out_path)
    return out_path


def write_geojson(issues: list[Issue], records: list[dict], out_path: str | Path) -> Path:
    per = {}
    for i in issues:
        per.setdefault(i.row, []).append(i)
    feats = []
    for rec in records:
        if rec["_row"] not in per or rec.get("_geom") is None or rec["_geom"].is_empty:
            continue
        iss = per[rec["_row"]]
        worst = min((i.severity for i in iss), key=lambda s: SEVERITY_ORDER[s])
        props = {k: v for k, v in rec.items() if not k.startswith("_")}
        props.update(qc_row=rec["_row"], qc_issues=len(iss), qc_worst=worst,
                     qc_codes=", ".join(sorted({i.code for i in iss})))
        feats.append({"type": "Feature", "properties": props, "geometry": mapping(rec["_geom"])})
    out_path = Path(out_path)
    out_path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, indent=1))
    return out_path


def _map_png(issues: list[Issue], records: list[dict]) -> str:
    worst = {}
    for i in issues:
        if i.row not in worst or SEVERITY_ORDER[i.severity] < SEVERITY_ORDER[worst[i.row]]:
            worst[i.row] = i.severity
    off_map = {i.row for i in issues if i.code == "GEO-002"}
    fig, ax = plt.subplots(figsize=(7, 6))
    groups = {"OK": [], "MINOR": [], "MAJOR": [], "CRITICAL": []}
    lats = []
    for rec in records:
        g = rec.get("_geom")
        if g is None or g.is_empty or rec["_row"] in off_map:
            continue
        lats.append(g.representative_point().y)
        if g.geom_type in ("LineString", "MultiLineString"):
            xs, ys = g.xy if g.geom_type == "LineString" else (None, None)
            if xs is not None:
                ax.plot(xs, ys, lw=1, color=SEV_COLOR.get(worst.get(rec["_row"]), "#9aa59e"), zorder=1)
        p = g.representative_point()
        groups[worst.get(rec["_row"], "OK")].append((p.x, p.y))
    styles = {"OK": ("#9aa59e", 10), "MINOR": (SEV_COLOR["MINOR"], 22),
              "MAJOR": (SEV_COLOR["MAJOR"], 30), "CRITICAL": (SEV_COLOR["CRITICAL"], 38)}
    for k, pts in groups.items():
        if pts:
            xs, ys = zip(*pts)
            ax.scatter(xs, ys, s=styles[k][1], c=styles[k][0], label=f"{k} ({len(pts)})",
                       edgecolors="white", linewidths=0.4, zorder=2 + SEVERITY_ORDER.get(k, -1) * -1)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    if lats:  # metric-looking aspect at this latitude
        import math
        ax.set_aspect(1 / max(math.cos(math.radians(sum(lats) / len(lats))), 0.1))
    n_missing = sum(1 for r in records if r.get("_geom") is None or r["_geom"].is_empty) + len(off_map)
    if n_missing:
        ax.text(0.01, 0.01, f"{n_missing} record(s) not plotted (no/invalid location)",
                transform=ax.transAxes, fontsize=7, color="#c0392b")
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title("Obstacles by worst QC severity")
    ax.tick_params(labelsize=7)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def write_html(issues: list[Issue], records: list[dict], out_path: str | Path, *,
               dataset_name: str, outputs: dict[str, str]) -> Path:
    esc = html.escape
    sev = Counter(i.severity for i in issues)
    by_code = Counter(i.code for i in issues)
    verdict = _verdict(issues)
    vcol = "#c0392b" if sev["CRITICAL"] else ("#e08e0b" if sev["MAJOR"] else "#2f6b5a")
    code_rows = "".join(
        f"<tr><td>{c}</td><td>{esc(CHECK_NAMES[c])}</td><td>{by_code.get(c, 0)}</td></tr>"
        for c in sorted(CHECK_NAMES))
    top = "".join(
        f"<tr><td><span class='s {i.severity}'>{i.severity}</span></td><td>{i.code}</td>"
        f"<td>{esc(i.obstacle_id)}</td><td>{esc(i.field)}</td><td>{esc(i.value)}</td>"
        f"<td>{esc(i.description)}</td></tr>" for i in issues[:60])
    more = f"<p>… {len(issues) - 60} more in the Excel issue log.</p>" if len(issues) > 60 else ""
    outs = "".join(f"<li><b>{esc(k)}</b>: <code>{esc(v)}</code></li>" for k, v in outputs.items())
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>e-TOD QC Report</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 16px;color:#1d2621;background:#fbf9f4}}
h1{{margin:0}}h2{{margin-top:34px;border-bottom:1px solid #ddd;padding-bottom:4px}}
.kpis{{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0}}.kpi{{background:#fff;border:1px solid #ddd;border-radius:10px;padding:12px 16px;min-width:130px}}
.kpi b{{display:block;font-size:24px}}table{{border-collapse:collapse;width:100%;font-size:13px;background:#fff}}
th,td{{border:1px solid #e3e0d8;padding:5px 8px;text-align:left;vertical-align:top}}th{{background:#efece4}}
.s{{font-size:11px;font-weight:600;padding:1px 6px;border-radius:4px;color:#fff}}.CRITICAL{{background:#c0392b}}.MAJOR{{background:#e08e0b}}.MINOR{{background:#5b8c3a}}
.verdict{{padding:12px 16px;border-radius:10px;color:#fff;font-weight:600;background:{vcol}}}
.grid{{display:grid;grid-template-columns:1.1fr 1fr;gap:24px;align-items:start}}img{{max-width:100%;border:1px solid #ddd}}
@media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}</style></head><body>
<h1>e-TOD Obstacle Data — QC Report</h1>
<div>{esc(dataset_name)} · checked {datetime.now():%Y-%m-%d %H:%M} · etod-obstacle-qc</div>
<div class="kpis"><div class="kpi"><b>{len(records)}</b>records</div>
<div class="kpi"><b>{len({i.row for i in issues})}</b>records with issues</div>
<div class="kpi" style="color:#c0392b"><b>{sev['CRITICAL']}</b>critical issues</div>
<div class="kpi" style="color:#e08e0b"><b>{sev['MAJOR']}</b>major issues</div>
<div class="kpi" style="color:#5b8c3a"><b>{sev['MINOR']}</b>minor issues</div></div>
<div class="verdict">{esc(verdict)}</div>
<div class="grid"><div><h2>Issues by check</h2><table><thead><tr><th>Code</th><th>Check</th><th>Issues</th></tr></thead>
<tbody>{code_rows}</tbody></table></div>
<div><h2>Map</h2><img src="data:image/png;base64,{_map_png(issues, records)}"></div></div>
<h2>Issues (most severe first)</h2><table><thead><tr><th>Severity</th><th>Code</th><th>Obstacle</th><th>Field</th>
<th>Value</th><th>Description</th></tr></thead><tbody>{top}</tbody></table>{more}
<h2>Outputs</h2><ul>{outs}</ul></body></html>"""
    out_path = Path(out_path)
    out_path.write_text(doc, encoding="utf-8")
    return out_path
