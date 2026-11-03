"""Command-line interface:  python -m etod_qc <command> ..."""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from . import checks, io, report, sample_data


def run(input_path, out_dir, rules_path=None, log=print) -> dict:
    rules = io.load_rules(rules_path)
    records = io.load_obstacles(input_path)
    issues = checks.run_checks(records, rules)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = Path(input_path).name
    rules_name = Path(rules_path).name if rules_path else "default_rules.yaml"
    outputs = {
        "Issue log (Excel)": str(report.write_excel(issues, records, out / "qc_issue_log.xlsx",
                                                    dataset_name=name, rules_name=rules_name)),
        "Flagged obstacles (GeoJSON)": str(report.write_geojson(issues, records,
                                                                out / "flagged_obstacles.geojson")),
    }
    html = report.write_html(issues, records, out / "qc_report.html", dataset_name=name, outputs=outputs)
    outputs["HTML report"] = str(html)
    sev = Counter(i.severity for i in issues)
    log(f"{len(records)} records · {len(issues)} issues "
        f"(critical {sev['CRITICAL']}, major {sev['MAJOR']}, minor {sev['MINOR']})")
    for k, v in outputs.items():
        log(f"  {k}: {v}")
    return {"records": records, "issues": issues, "outputs": outputs}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="etod_qc", description="QC of e-TOD obstacle datasets.")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="generate a sample dataset with known errors and check it")
    d.add_argument("--out", default="demo_output")
    c = sub.add_parser("check", help="check an obstacle dataset (.geojson or .csv)")
    c.add_argument("input")
    c.add_argument("--out", default="qc_output")
    c.add_argument("--rules", help="custom rules YAML (default: rules/default_rules.yaml)")
    c.add_argument("--fail-on", choices=["CRITICAL", "MAJOR", "MINOR"],
                   help="exit with code 1 if issues of this severity or worse are found")
    a = p.parse_args(argv)

    if a.cmd == "demo":
        s = sample_data.generate(Path(a.out) / "sample_obstacles.geojson")
        print(f"Sample dataset: {s['path']} ({len(s['injected'])} errors injected)")
        run(s["path"], Path(a.out) / "result")
        return 0

    res = run(a.input, a.out, a.rules)
    if a.fail_on:
        limit = checks.SEVERITY_ORDER[a.fail_on]
        if any(checks.SEVERITY_ORDER[i.severity] <= limit for i in res["issues"]):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
