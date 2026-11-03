import csv
from datetime import date

import pytest
from openpyxl import load_workbook

from etod_qc import checks, io, sample_data
from etod_qc.__main__ import main, run

TODAY = date(2026, 11, 1)


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    d = tmp_path_factory.mktemp("s")
    s = sample_data.generate(d / "obs.geojson", n=250, seed=5, today=TODAY)
    records = io.load_obstacles(s["path"])
    issues = checks.run_checks(records, io.load_rules(), today=TODAY)
    return s, records, issues


def test_every_injected_error_is_detected(sample):
    s, _, issues = sample
    found = {(i.obstacle_id, i.code) for i in issues}
    missed = [(oid, code) for oid, code in s["injected"].items() if (oid, code) not in found]
    assert not missed, f"missed: {missed}"


def test_no_false_positives_on_clean_records(sample):
    s, _, issues = sample
    injected_ids = set(s["injected"])
    # records involved in injected errors (incl. the original of the double capture / dup id)
    involved = injected_ids | {i.obstacle_id for i in issues if i.code in ("GEO-003", "ATT-003")}
    false_pos = [i for i in issues if i.obstacle_id not in involved]
    assert not false_pos, [(i.obstacle_id, i.code, i.description) for i in false_pos[:5]]


def test_severity_assigned_and_sorted(sample):
    _, _, issues = sample
    order = [checks.SEVERITY_ORDER[i.severity] for i in issues]
    assert order == sorted(order)
    assert {i.severity for i in issues} == {"CRITICAL", "MAJOR", "MINOR"}


def test_accuracy_thresholds_per_area():
    rules = io.load_rules()
    rec = lambda area, h, v: {"_row": 1, "_geom": None, "obstacle_id": "X", "area": area,
                              "horizontal_accuracy_m": h, "vertical_accuracy_m": v}
    codes = lambda r: {i.code for i in checks.check_accuracy([r], rules)}
    assert codes(rec("1", 50, 30)) == set()           # exactly at limit passes
    assert codes(rec("1", 50.1, 30)) == {"ACC-001"}
    assert codes(rec("2a", 5, 3.2)) == {"ACC-002"}
    assert codes(rec("3", 0.6, 0.6)) == {"ACC-001", "ACC-002"}


def test_csv_input_with_lon_lat(tmp_path):
    p = tmp_path / "o.csv"
    fields = ["obstacle_id", "obstacle_type", "lon", "lat", "ground_elev_m", "height_m", "elevation_m",
              "horizontal_accuracy_m", "vertical_accuracy_m", "area", "lighting", "marking", "capture_date"]
    rows = [["A1", "TOWER", 46.7, 24.7, 600, 50, 650, 2, 1, "2a", "YES", "YES", "2026-01-10"],
            ["A2", "TOWER", 46.71, 24.71, 600, 50, 655, 2, 1, "2a", "YES", "YES", "2026-01-10"]]
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        w.writerows(rows)
    issues = checks.run_checks(io.load_obstacles(p), io.load_rules(), today=TODAY)
    assert [(i.obstacle_id, i.code, i.row) for i in issues] == [("A2", "HGT-003", 3)]


def test_outputs_and_excel_issue_log(sample, tmp_path):
    s, _, _ = sample
    res = run(s["path"], tmp_path, log=lambda *_: None)
    wb = load_workbook(tmp_path / "qc_issue_log.xlsx")
    assert wb.sheetnames == ["Summary", "Issue Log", "Obstacles"]
    log = wb["Issue Log"]
    assert log.max_row - 1 == len(res["issues"])
    assert log["M2"].value == "Open"
    assert "REJECTED" in wb["Summary"]["B9"].value
    assert (tmp_path / "qc_report.html").read_text().count("<tr>") > 10
    assert (tmp_path / "flagged_obstacles.geojson").exists()


def test_cli_fail_on(sample, tmp_path):
    s, _, _ = sample
    assert main(["check", str(s["path"]), "--out", str(tmp_path), "--fail-on", "CRITICAL"]) == 1
