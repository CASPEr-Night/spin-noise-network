#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_sweep_report.py -- validate facility_report's field-sweep science
path on a fabricated sweep bundle.

    python3 testing/test_sweep_report.py [--out-dir DIR]

Builds a synthetic physics bundle (make_physics_bundle, injected bump at
F0 = -812 Hz), then clones its noise block into three noise_sweep blocks
with a field_sweep meta object:

  expno 50: measured offset  -20 Hz   (fit must find the line)
  expno 51: measured offset  +25 Hz   (fit must find the line)
  expno 52: offset NOT measured       (must be listed, NOT fitted)

Because the cloned data all carry the line at the baseline position,
the small measured offsets keep the true line inside fit_line's search
window around either sign candidate -- so the per-step fits must land
on the real line, not on noise. Asserts:

  * three steps in science.field_sweep, in expno order;
  * measured steps carry offset_basis 'measured' and a line center
    within 5 Hz of the injected position; the unverified step carries
    offset_basis 'unverified' and NO line fit (an unverified target is
    not a position -- review finding, 2026-08-31);
  * headline_expno is the measured step nearest baseline (50);
  * the HTML renders the sweep section and flags the unverified step.

Exit 0 iff all assertions hold.
"""

from __future__ import print_function

import argparse
import json
import os
import subprocess
import sys
import tempfile
import zipfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
F0_INJECTED = -812.0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)
    work = args.out_dir or tempfile.mkdtemp(prefix="sweep_report_test_")
    if not os.path.isdir(work):
        os.makedirs(work)

    base = subprocess.check_output(
        [sys.executable, os.path.join(REPO, "testing", "make_physics_bundle.py"),
         "--feature", "bump", "--clock-offset", "0", "--out-dir", work],
        stderr=subprocess.DEVNULL).decode().strip().splitlines()[-1]

    dst = os.path.join(work, "sweep_bundle.zip")
    zin = zipfile.ZipFile(base)
    meta = json.loads(zin.read("meta.json"))
    exps = [e for e in meta["experiments"] if e["role"] != "noise"]
    noise = [e for e in meta["experiments"] if e["role"] == "noise"][0]
    plan = ((50, -20.0), (51, 25.0), (52, None))
    for expno, _off in plan:
        e = dict(noise)
        e["expno"] = expno
        e["role"] = "noise_sweep"
        exps.append(e)
    meta["experiments"] = exps
    meta["field_sweep"] = {
        "enabled": True, "requested_half_span_hz": 800.0,
        "per_step_secs": 600.0, "baseline_line_offset_hz": F0_INJECTED,
        "restored_offset_hz": 1.0, "field_restored": True,
        "ended_early": False, "sign_convention_flip": 1,
        "note": "fabricated sweep for analysis-path testing",
        "steps": [
            {"index": i, "target_offset_hz": (off if off is not None
                                              else 800.0),
             "measured_offset_hz": off,
             "measured_offset_hz_raw": off,
             "verify_expno": 31 + i, "noise_expno": expno,
             "rows": noise["td1_rows"], "skipped": 0}
            for i, (expno, off) in enumerate(plan)]}
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in zin.namelist():
            if n == "meta.json":
                continue
            data = zin.read(n)
            zout.writestr(n, data)
            if n.startswith("data/12/"):
                for expno, _off in plan:
                    arc = n.replace("data/12/", "data/%d/" % expno)
                    zout.writestr(arc, data)
                    meta["checksums"][arc] = meta["checksums"][n]
        zout.writestr("meta.json", json.dumps(meta, indent=1) + "\n")

    out = os.path.join(work, "report")
    subprocess.check_call(
        [sys.executable, os.path.join(REPO, "analysis", "facility_report.py"),
         dst, "--out", out], stdout=subprocess.DEVNULL)
    with open(os.path.join(out, "report.json")) as fh:
        report = json.load(fh)

    failures = []

    def check(name, ok, detail=""):
        print("%s : %s" % ("PASS" if ok else "FAIL", name))
        if not ok:
            if detail:
                print("       %s" % detail)
            failures.append(name)

    fs = (report.get("science") or {}).get("field_sweep")
    check("science.field_sweep present", isinstance(fs, dict))
    steps = (fs or {}).get("steps") or []
    check("three sweep steps in expno order",
          [s.get("expno") for s in steps] == [50, 51, 52], str(steps)[:200])
    for i in (0, 1):
        s = steps[i] if len(steps) > 2 else {}
        lc = s.get("line_center_hz")
        want = F0_INJECTED
        check("step %d (measured %+d Hz): basis 'measured', line found "
              "within 5 Hz of the injected position"
              % (i, (-20, 25)[i]),
              s.get("offset_basis") == "measured" and lc is not None
              and abs(lc - want) < 5.0,
              "basis=%s line=%s want~%s" % (s.get("offset_basis"), lc, want))
    s2 = steps[2] if len(steps) > 2 else {}
    check("unverified step: basis 'unverified', NO line fit",
          s2.get("offset_basis") == "unverified"
          and s2.get("line_center_hz") is None
          and "not a position" in (s2.get("note") or ""), str(s2)[:200])
    check("headline is the measured step nearest baseline (expno 50)",
          (fs or {}).get("headline_expno") == 50,
          "headline=%s" % (fs or {}).get("headline_expno"))
    with open(os.path.join(out, "report.html")) as fh:
        html = fh.read()
    check("HTML renders the sweep section with the unverified flag",
          "Field-stepped sweep" in html and "unverified" in html)

    if failures:
        print("SWEEP REPORT TEST: FAIL (%d)" % len(failures))
        return 1
    print("SWEEP REPORT TEST: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
