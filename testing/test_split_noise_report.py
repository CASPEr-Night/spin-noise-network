#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_split_noise_report.py -- the report on a v0.8.3 'splitnoise' bundle.

    python3 testing/test_split_noise_report.py [--out-dir DIR]

Builds one timed synthetic physics bundle (make_physics_bundle: a bump, 120
rows every 360 s from 07:00 UTC at Oulu, so the wind-aware construction and
the sidereal fit both run), then re-packs its single noise block (expno 12)
as the script's 'splitnoise' layout: three consecutive pseudo-2D blocks at
expnos 12, 400 and 401 (40 rows each, the same bytes in the same order),
each with its own acqu2s, experiments[] entry and clock-audit block, the
wall span divided in proportion to the rows, so every row keeps its time --
plus a meta.noise_plan as the script writes it (a weekend phase, one block
with a recorded zg-return lag above 10 min, a fourth block the deadline
guard did not start).  Asserts:

  * the split report co-adds the three blocks as ONE noise block: 120 rows,
    n_experiments 3, noise_aggregation consistent, its note naming the
    consecutive pseudo-2D blocks;
  * the science is the unsplit bundle's: detection, headline, the
    wind-aware exclusion and the sidereal-modulation fit agree to 1e-6
    relative (the same rows, at the same times, in the same order);
  * science.noise_plan carries the plan with its summary and rows_acquired,
    and the QA has the INFO 'noise phase', the WARN 'noise phase: stopped
    early' and one WARN 'noise phase: zg returned late' (expno 400);
  * the unsplit bundle (no noise_plan, as every bundle before v0.8.3) gets
    no science.noise_plan key and no 'noise phase' QA row;
  * ticket 20261113 (Oulu 400, TopSpin 3.2, script 0.8.3): the unsplit
    bundle with expno 12's acqus carrying DATE one row after the start and
    NO DATE_START, and a recorded lag equal to the block's own length (what
    0.8.3 wrote) -- the report re-derives the lag from the clock audit
    (wall time less OCXO duration: near zero), keeps the recorded value,
    says so in an INFO row and raises no 'zg returned late' WARN; the same
    bundle WITH DATE_START keeps the recorded lag and its WARN.

Exit 0 iff all assertions hold.
"""

from __future__ import print_function

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PHYSICS = os.path.join(REPO, "testing", "make_physics_bundle.py")
REPORT_PY = os.path.join(REPO, "analysis", "facility_report.py")
TIMED = ["--feature", "bump", "--amp", "1.5", "--noise-rows", "120",
         "--row-cadence-s", "360", "--start-utc", "2026-10-05T07:00:00Z",
         "--site", "65.0,25.5"]
SPLIT = ((12, 40), (400, 40), (401, 40))
LATE_EXPNO, LATE_LAG_S = 400, 1250.0
REL_TOL = 1e-6

FAILURES = []


def check(name, ok, detail=""):
    print("%s : %s" % ("PASS" if ok else "FAIL", name))
    if not ok:
        if detail:
            print("       %s" % detail)
        FAILURES.append(name)


def _local(text, seconds):
    t = datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
    return (t + datetime.timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%S")


def split_bundle(src, dst):
    """Re-pack src's expno 12 as the SPLIT blocks (see the module doc)."""
    zin = zipfile.ZipFile(src)
    meta = json.loads(zin.read("meta.json"))
    noise = [e for e in meta["experiments"] if e["role"] == "noise"][0]
    n = int(noise["td1_rows"])
    assert sum(r for _e, r in SPLIT) == n, "SPLIT must cover the block"
    ser = zin.read("data/12/ser")
    row_bytes = len(ser) // n
    blocks = meta["clock_audit"]["blocks"]
    nb = [b for b in blocks if b.get("expno") == 12][0]
    ws, we = nb["wall_start_ms"], nb["wall_end_ms"]
    span_ms = we - ws
    t_start = noise["started_local"]
    span_s = span_ms / 1000.0
    new_files, new_exps, new_blocks = {}, [], []
    done = 0
    for expno, rows in SPLIT:
        pre = "data/%d/" % expno
        new_files[pre + "ser"] = ser[done * row_bytes:(done + rows) * row_bytes]
        new_files[pre + "acqus"] = zin.read("data/12/acqus")
        new_files[pre + "pulseprogram"] = zin.read("data/12/pulseprogram")
        new_files[pre + "acqu2s"] = ("##TITLE= Parameter file F1, synthetic\n"
                                     "##$TD= %d\n##END=\n" % rows).encode()
        e = dict(noise)
        e["expno"] = expno
        e["td1_rows"] = rows
        e["started_local"] = _local(t_start, span_s * done / n)
        e["finished_local"] = _local(t_start, span_s * (done + rows) / n)
        new_exps.append(e)
        b = dict(nb)
        b["expno"] = expno
        b["wall_start_ms"] = ws + int(round(span_ms * done / float(n)))
        b["wall_end_ms"] = ws + int(round(span_ms * (done + rows) / float(n)))
        b["ocxo_expected_s"] = nb["ocxo_expected_s"] * rows / float(n)
        new_blocks.append(b)
        done += rows
    exps = []
    for e in meta["experiments"]:
        if e["role"] == "noise":
            exps.extend(new_exps)
        else:
            exps.append(e)
    meta["experiments"] = exps
    cab = []
    for b in blocks:
        if b.get("expno") == 12:
            cab.extend(new_blocks)
        else:
            cab.append(b)
    meta["clock_audit"]["blocks"] = cab
    meta["noise_plan"] = {
        "mode": "weekend", "choice": "Weekend (until Monday 07:00)",
        "split": 1, "requested_s": 50000.0,
        "weekend_end_local": "2026-10-06T07:00:00",
        "weekend_refused": [], "deadline_local": "2026-10-06T06:30:00",
        "row_s": 360.0, "bytes_per_point": 8,
        "bytes_per_point_basis": "acqus DTYPA 2 (float64)",
        "row_bytes": row_bytes, "block_max_bytes": 40 * row_bytes,
        "session_max_bytes": 9 * (1 << 30), "disk_free_bytes": None,
        "disk_free_basis": "unknown", "rows_by_time": 160,
        "rows_by_size": 4608, "rows_by_disk": None, "rows_planned": 160,
        "capped_by": None, "disk_short": 0, "blocks_planned": 4,
        "blocks": [{"expno": expno, "rows_planned": rows, "rows": rows,
                    "zg_return_lag_s": (LATE_LAG_S if expno == LATE_EXPNO
                                        else None)}
                   for expno, rows in SPLIT],
        "stopped_early": ("block 4 of 4 not started: 0 s left before the "
                          "planned end of the noise phase "
                          "(2026-10-06T06:30:00)"),
        "late_returns": 1}
    ck = meta["checksums"]
    prefixed = str(ck.get("data/12/ser", "")).startswith("sha256:")
    for k in [k for k in ck if k.startswith("data/12/")]:
        del ck[k]
    for arc, data in new_files.items():
        h = hashlib.sha256(data).hexdigest()
        ck[arc] = ("sha256:" + h) if prefixed else h
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in zin.namelist():
            if name == "meta.json" or name.startswith("data/12/"):
                continue
            zout.writestr(name, zin.read(name))
        for arc in sorted(new_files):
            zout.writestr(arc, new_files[arc])
        zout.writestr("meta.json", json.dumps(meta, indent=1) + "\n")
    return dst


def ts32_bundle(src, dst, with_date_start):
    """src with expno 12's acqus stamped as TopSpin 3.2 does (DATE one row
    after the start, no DATE_START) -- or, with_date_start, as 3.7/4.x do
    (DATE_START at the start, DATE at that same early time) -- and a
    one-block meta.noise_plan whose lag is what script 0.8.3 recorded:
    now - DATE, i.e. the block's own wall span less one row."""
    zin = zipfile.ZipFile(src)
    meta = json.loads(zin.read("meta.json"))
    nb = [b for b in meta["clock_audit"]["blocks"] if b.get("expno") == 12][0]
    ws, we = nb["wall_start_ms"] / 1000.0, nb["wall_end_ms"] / 1000.0
    noise = [e for e in meta["experiments"] if e["role"] == "noise"][0]
    row_s = (we - ws) / float(noise["td1_rows"])
    date = int(ws + row_s)
    acqus = zin.read("data/12/acqus").decode("utf-8", "replace")
    lines = [ln for ln in acqus.split("\n")
             if ln and not ln.startswith("##$DATE")
             and not ln.startswith("##END=")]
    if with_date_start:
        lines.append("##$DATE_START= %d" % int(ws))
    lines += ["##$DATE= %d" % date, "##END="]
    new_acqus = ("\n".join(lines) + "\n").encode()
    lag = round(we - date, 1)
    meta["noise_plan"] = {
        "mode": "fixed", "choice": "30 min (default)", "split": 0,
        "rows_planned": noise["td1_rows"], "blocks_planned": 1,
        "blocks": [{"expno": 12, "rows_planned": noise["td1_rows"],
                    "rows": noise["td1_rows"], "zg_return_lag_s": lag}],
        "stopped_early": None, "late_returns": 1}
    ck = meta["checksums"]
    h = hashlib.sha256(new_acqus).hexdigest()
    ck["data/12/acqus"] = (("sha256:" + h)
                           if str(ck.get("data/12/acqus", "")).startswith(
                               "sha256:") else h)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in zin.namelist():
            if name in ("meta.json", "data/12/acqus"):
                continue
            zout.writestr(name, zin.read(name))
        zout.writestr("data/12/acqus", new_acqus)
        zout.writestr("meta.json", json.dumps(meta, indent=1) + "\n")
    return dst, lag, (we - ws) - nb["ocxo_expected_s"]


def report(bundle, out):
    p = subprocess.run([sys.executable, REPORT_PY, bundle, "--out", out],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    jpath = os.path.join(out, "report.json")
    check("facility_report exit 0 on %s" % os.path.basename(bundle),
          p.returncode == 0 and os.path.isfile(jpath),
          (p.stdout + p.stderr).decode("utf-8", "replace")[-800:])
    if p.returncode != 0 or not os.path.isfile(jpath):
        return None
    with open(jpath) as fh:
        return json.load(fh)


def get(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def close(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= REL_TOL * max(abs(a), abs(b), 1e-300)
    return a == b


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)
    work = args.out_dir or tempfile.mkdtemp(prefix="split_noise_test_")
    if not os.path.isdir(work):
        os.makedirs(work)
    base = subprocess.check_output(
        [sys.executable, PHYSICS, "--out-dir", work] + TIMED,
        stderr=subprocess.DEVNULL).decode().strip().splitlines()[-1]
    split = split_bundle(base, os.path.join(work, "split_bundle.zip"))
    one = report(base, os.path.join(work, "report_one"))
    three = report(split, os.path.join(work, "report_split"))
    if one is None or three is None:
        return 1
    s1, s3 = one["science"], three["science"]

    agg = s3.get("noise_aggregation") or {}
    check("split: ONE noise block of 120 rows from 3 experiments, consistent, "
          "the note naming the consecutive pseudo-2D blocks",
          get(s3, "noise", "n_rows") == 120
          and get(s3, "noise", "n_experiments") == 3
          and agg.get("consistent") is True
          and "consecutive pseudo-2D noise blocks (expnos 12, 400, 401)"
          in (agg.get("note") or ""), json.dumps(agg)[:400])
    keys = (("detection", "amp_significance"), ("detection", "fit_amp_norm"),
            ("detection", "fit_fwhm_hz"), ("detection", "line_center_hz"),
            ("headline", "spin_coupled_floor_fraction"),
            ("axion_exclusion", "result", "g90_wind_conservative_best_gev_inv"),
            ("axion_exclusion", "result", "g90_worst_best_gev_inv"),
            ("axion_exclusion", "result", "g90_nominal_best_gev_inv"),
            ("sidereal_modulation", "rows_used"),
            ("sidereal_modulation", "rows_timed_basis"),
            ("sidereal_modulation", "fit", "P_a_counts2"),
            ("sidereal_modulation", "fit", "sigma_Pa_counts2"),
            ("sidereal_modulation", "bound", "g90_conservative_mod_gev_inv"))
    diffs = []
    for k in keys:
        a, b = get(s1, *k), get(s3, *k)
        if a is None or not close(a, b):
            diffs.append("%s: %r vs %r" % (".".join(k), a, b))
    check("split: detection, headline, wind-aware exclusion and sidereal fit "
          "equal the unsplit bundle's (rel %g)" % REL_TOL, not diffs,
          "; ".join(diffs)[:900])

    plan = s3.get("noise_plan") or {}
    qa = s3.get("qa_flags") or []
    rows = [(q["level"], q["check"]) for q in qa
            if q["check"].startswith("noise phase")]
    late = [q for q in qa if q["check"] == "noise phase: zg returned late"]
    check("split: science.noise_plan with summary and rows_acquired 120; QA "
          "INFO 'noise phase', WARN 'stopped early', one WARN 'zg returned "
          "late' naming expno %d" % LATE_EXPNO,
          plan.get("rows_acquired") == 120
          and "splitnoise" in (plan.get("summary") or "")
          and ("INFO", "noise phase") in rows
          and ("WARN", "noise phase: stopped early") in rows
          and len(late) == 1
          and ("expno %d:" % LATE_EXPNO) in late[0]["detail"]
          and ("WARN", "noise phase: disk") not in rows,
          json.dumps([rows, plan.get("summary")])[:600])
    with open(os.path.join(work, "report_split", "report.html")) as fh:
        html = fh.read()
    check("split: the HTML names the noise phase", "Noise phase:" in html)
    check("unsplit (no noise_plan): no science.noise_plan key, no 'noise "
          "phase' QA row -- reports of earlier bundles are unchanged",
          "noise_plan" not in s1
          and not [q for q in (s1.get("qa_flags") or [])
                   if q["check"].startswith("noise phase")])

    # ticket 20261113: TopSpin 3.2's acqus stamps under script 0.8.3
    b32, lag32, expect32 = ts32_bundle(
        base, os.path.join(work, "ts32_bundle.zip"), False)
    r32 = report(b32, os.path.join(work, "report_ts32"))
    b4x, lag4x, _e = ts32_bundle(
        base, os.path.join(work, "ts4x_bundle.zip"), True)
    r4x = report(b4x, os.path.join(work, "report_ts4x"))
    if r32 is not None and r4x is not None:
        p32 = get(r32, "science", "noise_plan") or {}
        bk = (p32.get("blocks") or [{}])[0]
        qa32 = r32["science"].get("qa_flags") or []
        check("ts32: DATE without DATE_START -> the recorded lag %.0f s is "
              "re-derived from the clock audit (%.1f s expected), kept as "
              "zg_return_lag_s_recorded, late_returns 0 (recorded 1), an "
              "INFO row and no 'zg returned late' WARN" % (lag32, expect32),
              lag32 > 600.0
              and abs((bk.get("zg_return_lag_s") or -1e9)
                      - max(0.0, round(expect32, 1))) < 0.2
              and bk.get("zg_return_lag_s_recorded") == lag32
              and bk.get("zg_return_lag_basis") == "wall_less_ocxo (report)"
              and p32.get("late_returns") == 0
              and p32.get("late_returns_recorded") == 1
              and [q for q in qa32 if q["check"]
                   == "noise phase: zg-return lag re-derived"]
              and not [q for q in qa32 if q["check"]
                       == "noise phase: zg returned late"],
              json.dumps([bk, [q for q in qa32
                               if q["check"].startswith("noise phase")]])[:900])
        p4x = get(r4x, "science", "noise_plan") or {}
        qa4x = r4x["science"].get("qa_flags") or []
        check("ts4x control: with DATE_START the recorded lag is kept as "
              "recorded, with its WARN and no re-derivation",
              (p4x.get("blocks") or [{}])[0].get("zg_return_lag_s") == lag4x
              and "late_returns_recorded" not in p4x
              and [q for q in qa4x if q["check"]
                   == "noise phase: zg returned late"]
              and not [q for q in qa4x if q["check"]
                       == "noise phase: zg-return lag re-derived"],
              json.dumps(p4x)[:600])

    print("")
    if FAILURES:
        print("SPLIT-NOISE REPORT: FAIL (%d)" % len(FAILURES))
        return 1
    print("SPLIT-NOISE REPORT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
