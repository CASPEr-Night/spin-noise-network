#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dense_ladder_report.py -- the v0.8.2 dense receiver-gain ladder in
analysis/facility_report.py: the measured reference-to-noise gain bridge.

    python3 testing/test_dense_ladder_report.py [--out-dir DIR] [--skip-e2e]

The console script (spin_noise_run.py 0.8.2, design addendum) appends two
visits of every level at two per octave from RG 1 to max_rg, plus the
reference gain (max_rg / 4) and the noise gain (max_rg) exactly, to
calibration.rg_ladder (expnos 300.., "visit" 1|2) and records
calibration.rg_ladder_dense.  analyze_rg_ladder's multi-visit model
(amp = A rg c(rg) (1 + beta (t - t0))) then separates compression from
drift; the fitted per-level amplitudes at the two gains the transduction
bridges give rg_ladder.bridge_measured, transduction.rg_bridge_cross_check
states what kappa*M0 would be with it, and the headline keeps the declared
linear bridge.  Checks:

  1. testing/make_synthetic_bundle.py --dense-ladder (17 levels x 2 visits
     + the 4 legacy rungs, c(RG) = 1 - 0.04 (RG/max_rg)^1.5, drift +0.25
     %/min, seed 20261019) passes the uploader's validator; analyze_rg_ladder
     fits the drift_compression model over 38 visits of 17 levels in
     randomized time order, every level's compression within 3.5 sigma of
     the injected curve (RMS pull < 2), the drift within 3 sigma, and
     ladder_bridge_measured's ratio_of_ratios within 1% of the injected
     c(max_rg) / c(max_rg / 4) with a finite error -> WARN 'RG ladder
     bridge' (0.965 is outside 0.97-1.03);
  2. the same ladder with 1% compression -> OK row (ratio 0.991); the
     skipped form -> INFO; no block -> INFO; a single-visit legacy ladder
     -> bridge not measurable, INFO, never FAIL;
  3. a physics bundle (references RG 25, noise RG 101) with the dense ladder
     grafted on (max_rg 101, the reference gain 25 as a level, the four
     legacy rungs re-synthesized on the same law): report.json carries
     rg_ladder.bridge_measured, transduction.rg_bridge_cross_check with
     applied_to_headline False and kappa_M0_counts_headline equal to the
     transduction's linear-bridge kappa*M0, the WARN row, the honesty
     line, the 'ladder_dense' figure (alt='ladder_dense') and the HTML
     section;
  4. the ungrafted physics bundle (no block, as every bundle before 0.8.2):
     bridge_measured.available False with the single-visit reason, INFO
     row, no rg_bridge_cross_check, no honesty line, HTML states the
     absence -- the additions an old bundle gains and nothing else.

Exit 0 iff every check passes.  Python 3 standard library here; the
report module needs numpy + matplotlib.
"""

from __future__ import print_function

import argparse
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import types
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REPORT_PY = os.path.join(REPO, "analysis", "facility_report.py")
SYNTH_PY = os.path.join(HERE, "make_synthetic_bundle.py")
PHYS_PY = os.path.join(HERE, "make_physics_bundle.py")
SCHEMA = os.path.join(REPO, "schema", "meta.schema.json")

FAILED = []


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    print("%s: %s%s" % (tag, name, ("" if ok or not detail else
                                    " -- " + str(detail))))
    if not ok:
        FAILED.append(name)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_report(bundle, out_dir):
    subprocess.check_call([sys.executable, REPORT_PY, bundle, "--out", out_dir],
                          stdout=subprocess.DEVNULL)
    with open(os.path.join(out_dir, "report.json")) as fh:
        rep = json.load(fh)
    with open(os.path.join(out_dir, "report.html")) as fh:
        html = fh.read()
    return rep, html


def rewrite_bundle(src, dst, meta_edit=None, add_files=(), drop_files=()):
    with zipfile.ZipFile(src, "r") as zin:
        meta = json.loads(zin.read("meta.json").decode("utf-8"))
        members = [(n, zin.read(n)) for n in zin.namelist()
                   if n != "meta.json" and n not in drop_files]
    for arc in drop_files:
        meta["checksums"].pop(arc, None)
    for arc, payload in add_files:
        meta["checksums"][arc] = "sha256:" + hashlib.sha256(payload).hexdigest()
        members.append((arc, payload))
    if meta_edit:
        meta_edit(meta)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        zout.writestr("meta.json", json.dumps(meta, indent=1) + "\n")
        for arc, payload in members:
            zout.writestr(arc, payload)
    return meta


def qa_row(rep, name):
    rows = [q for q in rep["science"]["qa_flags"] if q["check"] == name]
    return rows[0] if rows else None


def ladder_checks(fr, lad, truth, tag, pull_max=3.5, rms_max=2.0):
    check("%s model drift_compression over %d visits of %d levels, "
          "randomized time order" % (tag, truth["n_rungs"], truth["n_levels"]),
          lad.get("model") == "drift_compression"
          and lad.get("n_levels") == truth["n_levels"]
          and lad.get("n_visits") == truth["n_rungs"]
          and lad.get("time_order") == "randomized",
          (lad.get("model"), lad.get("n_levels"), lad.get("n_visits"),
           lad.get("time_order")))
    if lad.get("model") != "drift_compression":
        return
    pulls = []
    for lv, ct in zip(lad["levels"], truth["compression_by_level"]):
        if lv["compression_err"]:
            pulls.append((lv["compression"] - ct) / lv["compression_err"])
    rms = math.sqrt(sum(p * p for p in pulls) / len(pulls))
    check("%s per-level compression recovered within its stated error "
          "(max |pull| %.2f < %.1f, RMS %.2f < %.1f over %d levels)"
          % (tag, max(abs(p) for p in pulls), pull_max, rms, rms_max,
             len(pulls)),
          max(abs(p) for p in pulls) < pull_max and rms < rms_max,
          [round(p, 2) for p in pulls])
    d = lad["drift"]
    pull_b = (d["beta_pct_per_min"] - truth["drift_pct_per_min"]) \
        / d["beta_err_pct_per_min"]
    check("%s drift %+.4f +/- %.4f %%/min recovers the injected %+.3f "
          "(pull %.2f)" % (tag, d["beta_pct_per_min"],
                           d["beta_err_pct_per_min"],
                           truth["drift_pct_per_min"], pull_b),
          abs(pull_b) < 3.0)
    env_true = 1.0 - min(truth["compression_by_level"])
    check("%s compression envelope %.4f near the injected %.4f (within "
          "0.5%% absolute)" % (tag, lad["max_abs_fractional_deviation"],
                                env_true),
          abs(lad["max_abs_fractional_deviation"] - env_true) < 0.005)


# ---------------------------------------------------------------------------
# 1.-2. the synthetic bundle with --dense-ladder, unit level
# ---------------------------------------------------------------------------

def synthetic_cases(fr, synth, work):
    out = subprocess.check_output(
        [sys.executable, SYNTH_PY, "--dense-ladder", "--out-dir", work],
        stderr=subprocess.DEVNULL).decode().strip().splitlines()[-1]
    sys.path.insert(0, os.path.join(REPO, "uploader"))
    import upload_bundle
    ok, msgs = upload_bundle.verify_bundle(out, SCHEMA)
    check("1. make_synthetic_bundle --dense-ladder passes the uploader's "
          "validator", ok, msgs[-3:])
    b = fr.Bundle(out)
    truth = b.meta["dense_ladder_truth"]
    dense = b.meta["calibration"]["rg_ladder_dense"]
    check("1. calibration.rg_ladder_dense in the contract shape (expnos "
          "300.., levels, seed, visits 2, skipped False) and every dense "
          "rung in calibration.rg_ladder with visit 1|2",
          dense["expnos"] == list(range(300, 334)) and len(dense["levels"]) == 17
          and dense["visits"] == 2 and dense["skipped"] is False
          and sorted(e["expno"] for e in b.meta["calibration"]["rg_ladder"]
                     if e.get("visit") in (1, 2)) == dense["expnos"],
          dense)
    check("1. levels: two per octave 1..128 plus the reference gain 37.5 and "
          "max_rg 150 exactly, visit 2 the reverse of visit 1",
          abs(truth["levels"][-1] - 150.0) < 1e-9 and 37.5 in truth["levels"]
          and truth["order"][:17] == truth["order"][17:][::-1],
          truth["levels"])
    lad = fr.analyze_rg_ladder(b, b.meta, synth.NUTATION_F0_HZ, 6900.0)
    ladder_checks(fr, lad, truth, "1.")
    br = fr.ladder_bridge_measured(lad, truth["rg_ref"], truth["rg_noise"],
                                   dense)
    check("1. bridge_measured available with rg_ref 37.5 -> rg_noise 150, "
          "linear x4, fitted x%.4g" % (br.get("amplitude_ratio_fitted") or 0),
          br.get("available") and abs(br["amplitude_ratio_linear"] - 4.0) < 1e-9
          and br["level_ref"]["rg"] == 37.5 and br["level_noise"]["rg"] == 150.0,
          br)
    if br.get("available"):
        check("1. ratio_of_ratios %.5f within 1%% of the injected %.5f, err "
              "finite" % (br["ratio_of_ratios"], truth["ratio_of_ratios_true"]),
              abs(br["ratio_of_ratios"] / truth["ratio_of_ratios_true"] - 1.0)
              < 0.01 and br["err"] is not None and math.isfinite(br["err"]),
              (br["ratio_of_ratios"], br["err"]))
        check("1. the measured ratio sits within 3 sigma of the truth, too",
              abs(br["ratio_of_ratios"] - truth["ratio_of_ratios_true"])
              < 3.0 * br["err"] + 1e-4,
              (br["ratio_of_ratios"] - truth["ratio_of_ratios_true"], br["err"]))
        row = fr.dense_ladder_qa_row(br)
        check("1. QA 'RG ladder bridge' WARN (0.965 outside 0.97-1.03), naming "
              "the dense ladder", row["level"] == "WARN"
              and row["check"] == "RG ladder bridge"
              and "34 rungs" in row["detail"], row)
        line = fr.dense_ladder_honesty_line(br, lad)
        check("1. honesty line names the dense ladder, the drift and the bridge",
              line.startswith("Dense receiver-gain ladder:")
              and "DECLARED linear bridge" in line, line)
        fig = fr.make_dense_ladder_figure(lad, br)
        check("1. the dense-ladder figure renders (base64 PNG)",
              isinstance(fig, str) and len(fig) > 1000)

    # ---- 2. mild compression -> OK; skipped / absent -> INFO
    out2 = subprocess.check_output(
        [sys.executable, SYNTH_PY, "--dense-ladder", "--dense-compression",
         "0.01", "--out-dir", os.path.join(work, "mild")],
        stderr=subprocess.DEVNULL).decode().strip().splitlines()[-1]
    b2 = fr.Bundle(out2)
    t2 = b2.meta["dense_ladder_truth"]
    lad2 = fr.analyze_rg_ladder(b2, b2.meta, synth.NUTATION_F0_HZ, 6900.0)
    ladder_checks(fr, lad2, t2, "2. (1% compression)")
    br2 = fr.ladder_bridge_measured(lad2, t2["rg_ref"], t2["rg_noise"],
                                    b2.meta["calibration"]["rg_ladder_dense"])
    check("2. 1%% compression: ratio_of_ratios %.4f within 1%% of %.4f and "
          "inside the band -> OK row"
          % (br2.get("ratio_of_ratios") or 0, t2["ratio_of_ratios_true"]),
          br2.get("available")
          and abs(br2["ratio_of_ratios"] / t2["ratio_of_ratios_true"] - 1.0) < 0.01
          and fr.dense_ladder_qa_row(br2)["level"] == "OK",
          fr.dense_ladder_qa_row(br2))
    br_skip = fr.ladder_bridge_measured(lad2, t2["rg_ref"], t2["rg_noise"],
                                        {"skipped": True,
                                         "why": "sparseladder flag"})
    row = fr.dense_ladder_qa_row(br_skip)
    check("2. skipped form -> INFO row with the script's reason; honesty line "
          "says NOT acquired",
          row["level"] == "INFO" and "sparseladder flag" in row["detail"]
          and fr.dense_ladder_honesty_line(br_skip, lad2).startswith(
              "Dense receiver-gain ladder NOT acquired"), row)
    br_abs = fr.ladder_bridge_measured(lad2, t2["rg_ref"], t2["rg_noise"], None)
    row = fr.dense_ladder_qa_row(br_abs)
    check("2. no block -> INFO row (the legacy ladder's repeated levels still "
          "quoted), no honesty line",
          row["level"] == "INFO" and "no calibration.rg_ladder_dense" in
          row["detail"] and fr.dense_ladder_honesty_line(br_abs, lad2) is None,
          row)
    single = {"available": True, "model": "degenerate_single_visit",
              "note": "x", "levels": None}
    br_s = fr.ladder_bridge_measured(single, 25.0, 101.0, None)
    row = fr.dense_ladder_qa_row(br_s)
    check("2. single-visit legacy ladder -> bridge not measurable, INFO, "
          "never FAIL",
          br_s["available"] is False and "no fitted per-level compression"
          in br_s["why"] and row["level"] == "INFO", (br_s, row))
    br_u = fr.ladder_bridge_measured(lad2, None, 150.0, None)
    check("2. unknown reference gain -> available False with the reason",
          br_u["available"] is False and "unknown" in br_u["why"], br_u)
    br_n = fr.ladder_bridge_measured(lad2, 1.2, 150.0, None)
    check("2. a gain with no level within 10% (rg 1.2 between 1 and 1.41) -> "
          "available False naming the nearest level", br_n["available"] is False
          and "no ladder level within" in br_n["why"], br_n)
    br_near = fr.ladder_bridge_measured(lad2, 36.0, 150.0, None)
    check("2. a gain 4% off a level -> the nearest level stands in, noted",
          br_near.get("available") and br_near.get("level_notes")
          and "stands in" in br_near["level_notes"][0], br_near.get("level_notes"))
    check("2. rg_bridge_cross_check is None without an available exclusion",
          fr.rg_bridge_cross_check(br2, {"available": False}) is None)


# ---------------------------------------------------------------------------
# 3.-4. physics bundle: grafted dense ladder, ungrafted
# ---------------------------------------------------------------------------

def graft_dense(synth, phys, src, dst_dir):
    """The dense ladder (max_rg = the physics bundle's noise gain, the
    reference gain as a level) grafted on: the four legacy rungs are
    re-synthesized on the same amplitude law (their original fids and
    acqus dropped), the dense rungs added, calibration.rg_ladder
    replaced, experiments[] stamped after the bundle's last experiment."""
    with zipfile.ZipFile(src) as zf:
        meta = json.loads(zf.read("meta.json").decode("utf-8"))
        names = zf.namelist()
    last = max(e.get("finished_local") or "" for e in meta["experiments"])
    start = synth._iso_plus(last, 60) if len(last) >= 19 else "2026-10-19T10:00:00"
    block = synth.dense_ladder_block(max_rg=phys.RG_NOISE, rg_ref=phys.RG_REF,
                                     start=start, f0_hz=phys.F0_HZ,
                                     o1_hz=0.0, sfo1_mhz=phys.H1_FREQ_MHZ,
                                     grpdly=phys.GRPDLY, sw_hz=phys.SW_HZ,
                                     td=phys.TD_LADDER, p90_us=phys.P1_US)
    legacy = [e for e, _r in synth.DENSE_LEGACY] + [16]
    drop = [n for n in names
            if any(n.startswith("data/%d/" % e) for e in legacy)
            and (n.endswith("/fid") or n.endswith("/acqus"))]

    def edit(m):
        m["calibration"]["rg_ladder"] = block["rg_ladder"]
        m["calibration"]["rg_ladder_dense"] = block["rg_ladder_dense"]
        m["experiments"] = [e for e in m["experiments"]
                            if e["expno"] not in legacy] + block["experiments"]
        m["dense_ladder_truth"] = block["truth"]
    dst = os.path.join(dst_dir, "spinnoise_injection-test_20261019_%06dZ_%04x.zip"
                       % (abs(hash(dst_dir)) % 1000000, abs(hash(src)) % 0xFFFF))
    rewrite_bundle(src, dst, meta_edit=edit, add_files=block["files"],
                   drop_files=drop)
    block["path"] = dst
    return block


def physics_cases(fr, synth, work):
    base = subprocess.check_output(
        [sys.executable, PHYS_PY, "--feature", "bump", "--amp", "1.5",
         "--out-dir", work], stderr=subprocess.DEVNULL
    ).decode().strip().splitlines()[-1]
    phys = load_module("make_physics_bundle", PHYS_PY)
    os.makedirs(os.path.join(work, "dense"))
    blk = graft_dense(synth, phys, base, os.path.join(work, "dense"))
    truth = blk["truth"]
    rep, html = run_report(blk["path"], os.path.join(work, "dense_report"))
    lad = rep["science"]["rg_ladder"]
    ladder_checks(fr, lad, truth, "3. (grafted)")
    br = lad.get("bridge_measured") or {}
    check("3. rg_ladder.bridge_measured available: rg_ref 25 -> rg_noise 101, "
          "ratio_of_ratios %.4f within 1%% of the injected %.4f"
          % (br.get("ratio_of_ratios") or 0, truth["ratio_of_ratios_true"]),
          br.get("available") and br["rg_ref"] == 25.0 and br["rg_noise"] == 101.0
          and abs(br["ratio_of_ratios"] / truth["ratio_of_ratios_true"] - 1.0) < 0.01
          and all(k in br for k in ("amplitude_ratio_fitted",
                                    "amplitude_ratio_linear", "err")),
          {k: br.get(k) for k in ("available", "rg_ref", "rg_noise",
                                  "ratio_of_ratios", "err", "why")})
    tr = rep["science"]["axion_exclusion"].get("transduction") or {}
    cc = tr.get("rg_bridge_cross_check")
    check("3. transduction.rg_bridge_cross_check present, applied_to_headline "
          "False, kappa_M0_counts_headline == the transduction's kappa*M0, "
          "with_measured_bridge = headline x ratio_of_ratios",
          cc is not None and cc["applied_to_headline"] is False
          and cc["kappa_M0_counts_headline"] == tr["kappa_M0_counts"]
          and abs(cc["kappa_M0_counts_with_measured_bridge"]
                  / (tr["kappa_M0_counts"] * br["ratio_of_ratios"]) - 1.0) < 1e-12
          and all(k in cc for k in ("amplitude_ratio_linear",
                                    "amplitude_ratio_fitted",
                                    "ratio_of_ratios", "err", "band")), cc)
    # the headline kappa*M0 is still the LINEAR bridge of the references
    per = tr.get("references") or []
    lin_ok = all(abs(p["rg_bridge_amplitude"] - 101.0 / 25.0) < 1e-9
                 and abs(p["kappa_M0_counts"]
                         - p["A0_counts"] / math.sin(math.radians(p["tip_deg"]))
                         * p["rg_bridge_amplitude"]) < 1e-6 * p["kappa_M0_counts"]
                 for p in per)
    check("3. headline kappa*M0 keeps the declared linear bridge x4.04 for "
          "every reference (the measured 0.96 not applied)",
          per and lin_ok and tr.get("rg_bridged") is True, per[:1])
    row = qa_row(rep, "RG ladder bridge")
    check("3. QA 'RG ladder bridge' WARN naming the measured ratio",
          row and row["level"] == "WARN" and "OUTSIDE" in row["detail"], row)
    check("3. honesty line present; HTML section and figure (alt='ladder_dense')",
          any(h.startswith("Dense receiver-gain ladder:")
              for h in rep["science"]["honesty"])
          and "Dense receiver-gain ladder and the gain bridge" in html
          and "alt='ladder_dense'" in html)
    check("3. no FAIL row from the dense ladder",
          not [q for q in rep["science"]["qa_flags"]
               if q["level"] == "FAIL" and "bridge" in q["check"]])
    check("3. every rung in report.json carries its visit (legacy rungs none)",
          all(("visit" in r) == (r["expno"] >= 300) for r in lad["rungs"]))

    # ---- 4. the ungrafted bundle
    rep_o, html_o = run_report(base, os.path.join(work, "old_report"))
    lad_o = rep_o["science"]["rg_ladder"]
    br_o = lad_o.get("bridge_measured") or {}
    check("4. old bundle: bridge_measured.available False with the single-"
          "visit reason, dense_ladder.present False",
          br_o.get("available") is False and "no fitted per-level compression"
          in br_o.get("why", "") and br_o["dense_ladder"]["present"] is False,
          br_o)
    row = qa_row(rep_o, "RG ladder bridge")
    check("4. old bundle: INFO 'RG ladder bridge', no rg_bridge_cross_check, "
          "no honesty line, HTML states the absence, no figure",
          row and row["level"] == "INFO"
          and "rg_bridge_cross_check" not in
          (rep_o["science"]["axion_exclusion"].get("transduction") or {})
          and not any("Dense receiver-gain ladder" in h
                      for h in rep_o["science"]["honesty"])
          and "Dense receiver-gain ladder and the gain bridge" in html_o
          and "alt='ladder_dense'" not in html_o, row)
    check("4. old bundle: no 'visit' key on any rung",
          not any("visit" in r for r in lad_o["rungs"]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--skip-e2e", action="store_true",
                    help="unit cases only (no full report runs)")
    args = ap.parse_args(argv)
    work = args.out_dir or tempfile.mkdtemp(prefix="dense_ladder_report_")
    if not os.path.isdir(work):
        os.makedirs(work)
    try:
        fr = load_module("facility_report", REPORT_PY)
    except ImportError as exc:
        print("FAIL: cannot import analysis/facility_report.py (%s)" % exc)
        return 1
    synth = load_module("make_synthetic_bundle", SYNTH_PY)
    print("--- 1.-2. synthetic bundle with --dense-ladder ---")
    synthetic_cases(fr, synth, work)
    if not args.skip_e2e:
        print("--- 3.-4. physics bundle: grafted dense ladder, old bundle ---")
        physics_cases(fr, synth, work)
    print("")
    if FAILED:
        print("DENSE LADDER REPORT: %d FAILED" % len(FAILED))
        for n in FAILED:
            print("  - %s" % n)
        return 1
    print("DENSE LADDER REPORT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
