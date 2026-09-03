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

    # ------------------------------------------------------------------
    # Scenario B (v0.6): carrier-follow sweep + persistent-line catalog.
    # Three sweep blocks at carrier shifts -1000/0/+1000 Hz, each with
    # freshly synthesized rows carrying (i) the spin line at its LOCAL
    # baseline position (offset by a +12 Hz field deviation on the
    # middle step), (ii) a WINDOW-FIXED spur tone at -300 Hz in every
    # block (receiver-chain spur), and (iii) an ABSOLUTE-FIXED tone at
    # +900 Hz from the baseline carrier, whose window position marches
    # through the blocks as +1900/+900/-100 Hz. The catalog must
    # classify all three.
    # ------------------------------------------------------------------
    sys.path.insert(0, os.path.join(REPO, "testing"))
    import numpy as np
    import make_physics_bundle as mpb

    rng = np.random.default_rng(20260903)
    fs = mpb.SW_HZ
    n_row = mpb.TD_ROW // 2
    t = np.arange(n_row) / fs
    a_tone = float(np.sqrt(40.0 * mpb.FLOOR_C2HZ))
    cf_rows = 4
    plan_b = (  # (expno, carrier shift = target, field deviation)
        (50, -1000.0, 0.0), (51, 0.0, 12.0), (52, 1000.0, 0.0))

    dst_b = os.path.join(work, "sweep_cf_bundle.zip")
    zin = zipfile.ZipFile(base)
    meta = json.loads(zin.read("meta.json"))
    exps = [e for e in meta["experiments"] if e["role"] != "noise"]
    noise = [e for e in meta["experiments"] if e["role"] == "noise"][0]
    new_files = {}
    for expno, shift, dev in plan_b:
        rows = []
        for _ in range(cf_rows):
            row = mpb.synth_noise_row(rng, n_row, fs, mpb.FLOOR_C2HZ,
                                      0.9, 0.3, F0_INJECTED + dev, 12.0)
            row = row + a_tone * np.exp(2j * np.pi * (-300.0) * t)
            row = row + a_tone * np.exp(2j * np.pi * (900.0 - shift) * t)
            rows.append(row)
        pre = "data/%d/" % expno
        new_files[pre + "acqus"] = mpb.acqus_text(
            mpb.TD_ROW, fs, mpb.RG_NOISE, o1=shift, d1_s=mpb.D1_NOISE_S)
        new_files[pre + "pulseprogram"] = mpb.PP_TEXTS["zgnoise2d"]
        new_files[pre + "acqu2s"] = mpb.acqu2s_text(cf_rows)
        new_files[pre + "ser"] = mpb.to_bruker_int32(rows)
        e = dict(noise)
        e["expno"] = expno
        e["role"] = "noise_sweep"
        e["td1_rows"] = cf_rows
        e["o1_hz"] = shift
        exps.append(e)
    meta["experiments"] = exps
    meta["field_sweep"] = {
        "enabled": True, "requested_half_span_hz": 1000.0,
        "carrier_follow": True, "span_cap_hz": 15000.0,
        "per_step_secs": 600.0,
        "baseline_line_offset_hz": F0_INJECTED,
        "restored_offset_hz": 0.5, "field_restored": True,
        "ended_early": False, "sign_convention_flip": 1,
        "sign_convention_basis": "carrier_displacement_calibration",
        "note": "fabricated v0.6 carrier-follow sweep (test)",
        "steps": [
            {"index": i, "target_offset_hz": shift,
             "measured_offset_hz": shift + dev,
             "measured_offset_hz_raw": dev,
             "measured_offset_local_hz": F0_INJECTED + dev,
             "local_deviation_hz": dev,
             "carrier_o1_hz": shift,
             "lock_shift_target_ppm": shift / 600.13,
             "verify_expno": 31 + i, "noise_expno": expno,
             "rows": cf_rows, "skipped": 0}
            for i, (expno, shift, dev) in enumerate(plan_b)]}
    with zipfile.ZipFile(dst_b, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in zin.namelist():
            if n != "meta.json":
                zout.writestr(n, zin.read(n))
        for arc in sorted(new_files):
            zout.writestr(arc, new_files[arc])
            meta["checksums"][arc] = "sha256:" + __import__(
                "hashlib").sha256(new_files[arc] if isinstance(
                    new_files[arc], bytes) else new_files[arc].encode(
                        "utf-8")).hexdigest()
        zout.writestr("meta.json", json.dumps(meta, indent=1) + "\n")

    out_b = os.path.join(work, "report_cf")
    subprocess.check_call(
        [sys.executable, os.path.join(REPO, "analysis", "facility_report.py"),
         dst_b, "--out", out_b], stdout=subprocess.DEVNULL)
    with open(os.path.join(out_b, "report.json")) as fh:
        rep_b = json.load(fh)
    sci = rep_b.get("science") or {}

    steps_b = ((sci.get("field_sweep") or {}).get("steps")) or []
    check("CF: three carrier-follow steps analyzed",
          len(steps_b) == 3 and all(s.get("offset_basis") == "measured"
                                    for s in steps_b), str(steps_b)[:200])
    ok_pos = True
    for s, (expno, shift, dev) in zip(steps_b, plan_b):
        lc = s.get("line_center_hz")
        if lc is None or abs(lc - (F0_INJECTED + dev)) > 5.0:
            ok_pos = False
    check("CF: each step's line found at its LOCAL position "
          "(baseline %+.0f Hz, deviation-shifted)" % F0_INJECTED, ok_pos,
          str([(s.get("expno"), s.get("line_center_hz"))
               for s in steps_b]))
    check("CF: steps carry carrier_o1_hz",
          all(s.get("carrier_o1_hz") is not None for s in steps_b))

    cat = sci.get("line_catalog") or {}
    classes = [ln.get("class") for ln in cat.get("lines") or []]
    check("CF: catalog has carrier diversity",
          cat.get("carrier_diversity") is True)
    check("CF: catalog sees the spin line", "spin_line" in classes,
          str(classes))
    wf = [ln for ln in cat.get("lines") or []
          if ln.get("class") == "window_fixed"
          and abs((ln.get("center_hz") or 0) + 300.0) < 5.0]
    check("CF: window-fixed spur classified at -300 Hz", len(wf) >= 1,
          str(cat.get("lines"))[:300])
    af = [ln for ln in cat.get("lines") or []
          if ln.get("class") == "absolute_fixed"
          and abs((ln.get("center_hz") or 0) - 900.0) < 5.0]
    check("CF: absolute-fixed line classified at +900 Hz", len(af) >= 1,
          str(cat.get("lines"))[:300])

    mb = sci.get("axion_mass_bookkeeping") or {}
    check("CF: axion mass coordinate ~2.482 ueV",
          abs((mb.get("axion_mass_coordinate_uev") or 0) - 2.4823) < 0.01,
          str(mb))
    check("CF: axial-vector conversion factor = m_a * v",
          abs((mb.get("axial_vector_conversion_gev") or 0)
              - (mb.get("axion_mass_coordinate_uev") or 0) * 1e-18)
          < 1e-22, str(mb))

    sub = sci.get("subvirial_pass") or {}
    check("CF: sub-virial pass ran with the chirp-template caveat",
          "chirp" in (sub.get("note") or ""), str(sub)[:200])

    with open(os.path.join(out_b, "report.html")) as fh:
        html_b = fh.read()
    check("CF: HTML renders the persistent-line catalog section",
          "Persistent-line catalog" in html_b)

    if failures:
        print("SWEEP REPORT TEST: FAIL (%d)" % len(failures))
        return 1
    print("SWEEP REPORT TEST: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
