#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_nutation_report.py -- the v0.8.2 nutation block in
analysis/facility_report.py: the pulse calibration kept as data.

    python3 testing/test_nutation_report.py [--out-dir DIR] [--skip-e2e]

The console script (spin_noise_run.py 0.8.2) acquires quick 1Ds at the
calibrated 90-degree power with P1 = 0.25 ... 3 x the declared P90
(calibration.nutation, expnos 80..) so a sceptic can re-derive P90 and
the small-flip tip from raw files.  The report fits A(P1) = A90 |sin(pi
P1 / (2 P90))| over the unclipped steps, cross-checks the references'
amplitude against A90 x sin(tip_declared) x RG_ref / RG_nut, and keeps
the headline kappa*M0 at the DECLARED tip.  Checks:

  1. testing/make_synthetic_bundle.py --nutation passes the uploader's
     own validator; restamped synthetic-injection (so the science path
     runs), the report recovers the generator's true P90 within 1%
     (declared 3% long), lists no clipped step, writes the 'nutation'
     figure (alt='nutation') and the 'Pulse calibration kept as data'
     section, and reports the small-flip check as not possible (that
     bundle's references hold no data);
  2. a physics bundle (make_physics_bundle, real references at RG 25 with
     A0 2.0e6 at the declared 1-degree tip) with a CLEAN nutation block
     grafted on (A90 chosen so the small-flip prediction is exact, true
     P90 = 10 / 1.03 us against the declared 10 us): P90 within 1%,
     measured / predicted small-flip ratio within 2%, both QA rows OK,
     transduction.tip_cross_check present with the headline kappa*M0
     untouched (equal to the ungrafted bundle's), the honesty line;
  3. the same with the P1 = P90 step driven into int32 saturation: that
     step is listed under `clipped`, excluded from the fit, and P90 is
     still recovered within 1% from the remaining steps;
  4. the ungrafted physics bundle (no block, as every bundle before
     0.8.2): science.nutation.available False with the 'no calibration.
     nutation block' reason, an INFO row, no nutation honesty line, and
     its report.json identical to the grafted bundle's except for added
     keys and the paths the block itself owns (headline, kappa*M0,
     exclusion numbers byte-identical);
  5. unit cases on the report module: the skipped form ({"skipped":
     true, "why": ...}) -> available False, skipped True; an Agilent
     bundle without the block -> 'not acquired by the Agilent driver';
     one nutation fid missing -> listed under `unreadable`, the fit
     runs on the other nine; a declared P90 8% off -> WARN 'nutation
     P90'; a step whose multiplier/p1 tables disagree in length ->
     available False with a reason; never a FAIL row from the block.

Exit 0 iff every check passes.  Python 3 standard library here; the
report module needs numpy + matplotlib.
"""

from __future__ import print_function

import argparse
import copy
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
    """Copy a bundle zip, editing meta.json (meta_edit(meta) in place),
    adding (arcname, bytes) files and dropping arcnames; checksums are
    updated for the files touched."""
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


def nutation_fail_rows(rep):
    return [q for q in rep["science"]["qa_flags"]
            if q["level"] == "FAIL" and ("nutation" in q["check"]
                                         or "small-flip" in q["check"])]


# ---------------------------------------------------------------------------
# structural diff: B must differ from A by additions only, except at the
# paths the nutation block owns
# ---------------------------------------------------------------------------

def walk(a, b, path, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            if k not in b:
                out.append(("removed", path + "." + k))
            else:
                walk(a[k], b[k], path + "." + k, out)
        for k in b:
            if k not in a:
                out.append(("added", path + "." + k))
        return
    if isinstance(a, list) and isinstance(b, list):
        for i in range(min(len(a), len(b))):
            walk(a[i], b[i], "%s[%d]" % (path, i), out)
        for i in range(len(a), len(b)):
            out.append(("added", "%s[%d]" % (path, i)))
        for i in range(len(b), len(a)):
            out.append(("removed", "%s[%d]" % (path, i)))
        return
    if a != b:
        out.append(("changed", path))


ALLOWED_CHANGED_PREFIXES = (
    "$.generated_utc", "$.bundle", "$.bundle_sha256",
    "$.validation.messages",          # file counts differ
    "$.science.nutation",             # the block itself (why -> numbers)
    "$.science.qa_flags",             # INFO row -> OK rows (checked apart)
    "$.science.raw_data_read",        # the nutation expnos read
)


# ---------------------------------------------------------------------------
# graft helpers
# ---------------------------------------------------------------------------

def graft_nutation(synth, src, dst, p90_declared_us, p90_true_us, a90,
                   clip_mults=(), drop_expno=None, rg=1.0):
    """dst: a directory (the grafted bundle gets a conventional name
    there) or a zip path.  The nutation steps are stamped after the
    base bundle's last experiment, as the console records them in
    acquisition order."""
    with zipfile.ZipFile(src) as zf:
        base_meta = json.loads(zf.read("meta.json").decode("utf-8"))
    last = max(e.get("finished_local") or e.get("started_local") or ""
               for e in base_meta["experiments"])
    started = last[:11] + "%02d:%02d:%02d" % (
        min(23, int(last[11:13]) + 1), int(last[14:16]), int(last[17:19])) \
        if len(last) >= 19 else "2026-10-19T10:30:00"
    block = synth.nutation_block(p90_declared_us=p90_declared_us,
                                 p90_true_us=p90_true_us, a90=a90, rg=rg,
                                 clip_mults=clip_mults, seed=20261019,
                                 started=started)
    if os.path.isdir(dst):
        dst = os.path.join(dst, "spinnoise_injection-test_20261019_%06dZ_%04x.zip"
                           % (abs(hash((p90_true_us, clip_mults, drop_expno)))
                              % 1000000, abs(hash(dst)) % 0xFFFF))
    files = block["files"]
    if drop_expno is not None:
        files = [(a, p) for a, p in files
                 if not a.startswith("data/%d/fid" % drop_expno)]

    def edit(meta):
        meta["calibration"]["p90_us"] = p90_declared_us
        meta["calibration"]["nutation"] = block["calibration"]
        meta["experiments"] = meta["experiments"] + block["experiments"]
        meta["nutation_truth"] = block["truth"]
    rewrite_bundle(src, dst, meta_edit=edit, add_files=files)
    block["path"] = dst
    return block


# ---------------------------------------------------------------------------
# 1. the synthetic bundle with --nutation
# ---------------------------------------------------------------------------

def synthetic_cases(fr, synth, work):
    out = subprocess.check_output(
        [sys.executable, SYNTH_PY, "--nutation", "--out-dir", work],
        stderr=subprocess.DEVNULL).decode().strip().splitlines()[-1]
    sys.path.insert(0, os.path.join(REPO, "uploader"))
    import upload_bundle
    ok, msgs = upload_bundle.verify_bundle(out, SCHEMA)
    check("1. make_synthetic_bundle --nutation passes the uploader's "
          "validator", ok, msgs[-3:])
    with zipfile.ZipFile(out) as zf:
        meta = json.loads(zf.read("meta.json").decode("utf-8"))
        names = set(zf.namelist())
    nut = meta["calibration"]["nutation"]
    check("1. calibration.nutation in the contract shape (expnos 80.., p1_us, "
          "multipliers, p90_us_assumed, power_db, power_parname, rg, d1_s, "
          "td, skipped False) with fids and acqus for every step",
          nut["expnos"] == list(range(80, 90)) and len(nut["p1_us"]) == 10
          and len(nut["multipliers"]) == 10 and nut["skipped"] is False
          and all(k in nut for k in ("p90_us_assumed", "power_db",
                                     "power_parname", "rg", "d1_s", "td"))
          and all("data/%d/fid" % e in names and "data/%d/acqus" % e in names
                  for e in nut["expnos"]),
          sorted(nut))
    roles = set(e["role"] for e in meta["experiments"])
    check("1. experiments[] role 'nutation' written iff the schema accepts it",
          ("nutation" in roles) == synth.schema_role_accepted("nutation"),
          roles)
    truth = meta["nutation_truth"]
    sci = os.path.join(work, "synth_science.zip")

    def restamp(m):
        m["software"]["run_mode"] = "synthetic-injection"
    rewrite_bundle(out, sci, meta_edit=restamp)
    rep, html = run_report(sci, os.path.join(work, "synth_report"))
    n = rep["science"]["nutation"]
    check("1. science.nutation available on the restamped synthetic bundle",
          n.get("available") is True, n.get("why"))
    if n.get("available"):
        dev = abs(n["p90_fit_us"] / truth["p90_true_us"] - 1.0)
        check("1. fitted P90 within 1%% of the true %.4g us (declared %.4g)"
              % (truth["p90_true_us"], nut["p90_us_assumed"]),
              dev < 0.01, (n["p90_fit_us"], dev))
        check("1. ratio_fit_over_declared = p90_fit / p90_declared",
              abs(n["ratio_fit_over_declared"]
                  - n["p90_fit_us"] / nut["p90_us_assumed"]) < 1e-9)
        check("1. no clipped step, 10 steps fitted, chi2/dof finite",
              n["clipped"] == [] and n["n_steps"] == 10
              and isinstance(n["chi2_dof"], float)
              and math.isfinite(n["chi2_dof"]),
              (n["clipped"], n["n_steps"], n["chi2_dof"]))
        check("1. step table carries A0, spectral amplitude, saturation "
              "numbers and residuals for every step",
              all(isinstance(s.get("A0_counts"), float)
                  and isinstance(s.get("spectral_amplitude_counts"), float)
                  and "fullscale_fraction" in s and "flat_top_run" in s
                  and "residual_counts" in s for s in n["steps"]))
        p = qa_row(rep, "nutation P90")
        check("1. QA 'nutation P90' OK (3% off is inside the 5% band)",
              p is not None and p["level"] == "OK", p)
        s = qa_row(rep, "small-flip linearity")
        check("1. QA 'small-flip linearity' INFO (no readable reference)",
              s is not None and s["level"] == "INFO", s)
    check("1. no FAIL row from the nutation block", not nutation_fail_rows(rep))
    check("1. HTML: the 'nutation' figure (alt='nutation') and the section",
          "alt='nutation'" in html
          and "Pulse calibration kept as data" in html)
    check("1. honesty line names the pulse calibration kept as data",
          any(h.startswith("Pulse calibration kept as data")
              for h in rep["science"]["honesty"]))


# ---------------------------------------------------------------------------
# 2.-4. physics bundle: clean graft, clipped graft, ungrafted
# ---------------------------------------------------------------------------

def physics_cases(fr, synth, work):
    base = subprocess.check_output(
        [sys.executable, PHYS_PY, "--feature", "bump", "--amp", "1.5",
         "--out-dir", work], stderr=subprocess.DEVNULL
    ).decode().strip().splitlines()[-1]
    phys = load_module("make_physics_bundle", PHYS_PY)
    p90_decl = 10.0                       # the physics bundle's calibration
    p90_true = p90_decl / 1.03
    rg_nut = 1.0
    tip_decl = math.radians(1.0)          # the references' declared tip
    # A90 such that predicted A1 = A90 sin(tip) RG_ref / RG_nut = REF_A0
    a90 = phys.REF_A0 * rg_nut / (phys.RG_REF * math.sin(tip_decl))

    os.makedirs(os.path.join(work, "clean"))
    clean = graft_nutation(synth, base, os.path.join(work, "clean"),
                           p90_decl, p90_true, a90, rg=rg_nut)["path"]
    rep_c, html_c = run_report(clean, os.path.join(work, "phys_clean_report"))
    n = rep_c["science"]["nutation"]
    check("2. clean graft: science.nutation available", n.get("available"),
          n.get("why"))
    if n.get("available"):
        check("2. P90 recovered within 1%% (true %.4g, declared %.4g)"
              % (p90_true, p90_decl),
              abs(n["p90_fit_us"] / p90_true - 1.0) < 0.01, n["p90_fit_us"])
        sfc = n["small_flip_check"]
        r = sfc.get("ratio_measured_over_predicted")
        check("2. small-flip ratio measured/predicted within 2% of 1",
              r is not None and abs(r - 1.0) < 0.02,
              (r, sfc.get("predicted_A1_counts_at_ref_rg"),
               sfc.get("measured_A1_counts")))
        check("2. small-flip check names the references, the RG bridge "
              "(RG_ref / RG_nut = 25) and the tip formula",
              sfc.get("references_used") == [11, 13]
              and all(abs(e["rg_bridge_amplitude"] - 25.0) < 1e-9
                      for e in sfc["per_reference"])
              and sfc.get("tip_formula") and "P90_fit" in sfc["tip_formula"],
              (sfc.get("references_used"), sfc.get("tip_formula")))
        check("2. tip from the fitted P90 = 90 x P1/P90_fit x 10^(-39.08/20) "
              "(power levels not comparable on this fixture)",
              abs(sfc["tip_from_nutation_deg"]
                  - 90.0 * 10.0 / n["p90_fit_us"] * 10 ** (-39.08 / 20.0))
              < 1e-6, sfc["tip_from_nutation_deg"])
        p = qa_row(rep_c, "nutation P90")
        s = qa_row(rep_c, "small-flip linearity")
        check("2. QA rows 'nutation P90' and 'small-flip linearity' both OK",
              p and s and p["level"] == "OK" and s["level"] == "OK", (p, s))
    tr = rep_c["science"]["axion_exclusion"].get("transduction") or {}
    tcc = tr.get("tip_cross_check")
    check("2. transduction.tip_cross_check present with the stated keys",
          tcc is not None and all(k in tcc for k in (
              "p90_declared_us", "p90_fit_us", "ratio_fit_over_declared",
              "tip_declared_deg", "tip_from_nutation_deg", "tip_formula",
              "small_flip_ratio_measured_over_predicted",
              "kappa_M0_counts_headline", "kappa_M0_counts_at_nutation_tip",
              "applied_to_headline")) and tcc["applied_to_headline"] is False,
          tcc)
    if tcc:
        check("2. kappa_M0_counts_headline is the transduction's kappa*M0 and "
              "the nutation-tip value differs from it by sin(tip_decl)/"
              "sin(tip_nut)",
              tcc["kappa_M0_counts_headline"] == tr["kappa_M0_counts"]
              and abs(tcc["kappa_M0_counts_at_nutation_tip"]
                      / tr["kappa_M0_counts"]
                      - math.sin(math.radians(tcc["tip_declared_deg"]))
                      / math.sin(math.radians(tcc["tip_from_nutation_deg"])))
              < 1e-9)
    check("2. honesty line present and HTML figure + section present",
          any(h.startswith("Pulse calibration kept as data")
              for h in rep_c["science"]["honesty"])
          and "alt='nutation'" in html_c
          and "Pulse calibration kept as data" in html_c)
    check("2. no FAIL row from the nutation block", not nutation_fail_rows(rep_c))

    # ---- 3. clipped step
    os.makedirs(os.path.join(work, "clipped"))
    blk = graft_nutation(synth, base, os.path.join(work, "clipped"), p90_decl,
                         p90_true, a90, clip_mults=(1.0,), rg=rg_nut)
    rep_k, _ = run_report(blk["path"], os.path.join(work, "phys_clipped_report"))
    n = rep_k["science"]["nutation"]
    exp_clip = blk["truth"]["clipped_expnos"]
    check("3. the saturated P1 = P90 step (expno %s) is listed under clipped "
          "and excluded from the fit" % exp_clip,
          n.get("clipped") == exp_clip
          and all(s["used_in_fit"] is False and s["clipped"] is True
                  for s in n["steps"] if s["expno"] in exp_clip)
          and n.get("n_steps") == 9, (n.get("clipped"), n.get("n_steps")))
    check("3. P90 still recovered within 1% from the nine unclipped steps",
          n.get("available") and abs(n["p90_fit_us"] / p90_true - 1.0) < 0.01,
          n.get("p90_fit_us"))
    p = qa_row(rep_k, "nutation P90")
    check("3. QA 'nutation P90' stays OK and names the clipped step",
          p and p["level"] == "OK" and "clipped" in p["detail"], p)
    check("3. the clipped step's own ADC/saturation numbers say why",
          any(s.get("clipped_why") and "full scale" in s["clipped_why"]
              for s in n["steps"] if s["expno"] in exp_clip))
    check("3. no FAIL row from the nutation block", not nutation_fail_rows(rep_k))

    # ---- 4. the ungrafted bundle: an old bundle without the block
    rep_o, html_o = run_report(base, os.path.join(work, "phys_old_report"))
    n = rep_o["science"]["nutation"]
    check("4. old bundle: available False, 'no calibration.nutation block'",
          n.get("available") is False and "no calibration.nutation" in
          n.get("why", "") and not n.get("present"), n)
    p = qa_row(rep_o, "nutation P90")
    check("4. old bundle: INFO 'nutation P90', no 'small-flip linearity' row, "
          "no FAIL", p and p["level"] == "INFO"
          and qa_row(rep_o, "small-flip linearity") is None
          and not nutation_fail_rows(rep_o), p)
    check("4. old bundle: no nutation honesty line; HTML section states the "
          "absence",
          not any("Pulse calibration" in h for h in rep_o["science"]["honesty"])
          and "Pulse calibration kept as data" in html_o
          and "alt='nutation'" not in html_o)
    diffs = []
    walk(rep_o, rep_c, "$", diffs)
    bad = [(k, p_) for k, p_ in diffs
           if k != "added" and not any(p_.startswith(pre)
                                       for pre in ALLOWED_CHANGED_PREFIXES)]
    check("4. grafted vs old report.json: additions only outside the block's "
          "own paths (headline, kappa*M0, exclusion byte-identical)",
          not bad, bad[:10])
    hd_o, hd_c = rep_o["science"]["headline"], rep_c["science"]["headline"]
    ex_o = rep_o["science"]["axion_exclusion"]
    ex_c = copy.deepcopy(rep_c["science"]["axion_exclusion"])
    ex_c["transduction"].pop("tip_cross_check", None)
    check("4. headline and axion_exclusion (minus tip_cross_check) equal",
          hd_o == hd_c and ex_o == ex_c)
    other_o = [q for q in rep_o["science"]["qa_flags"]
               if q["check"] not in ("nutation P90", "small-flip linearity")]
    other_c = [q for q in rep_c["science"]["qa_flags"]
               if q["check"] not in ("nutation P90", "small-flip linearity")
               and not any(q["check"] == "ADC check expno %d" % e
                           for e in range(80, 96))]
    check("4. every QA row other than the block's own is identical",
          other_o == other_c)
    check("4. honesty lists identical apart from the appended nutation line",
          rep_c["science"]["honesty"][:len(rep_o["science"]["honesty"])]
          == rep_o["science"]["honesty"]
          and len(rep_c["science"]["honesty"])
          == len(rep_o["science"]["honesty"]) + 1)
    return base, a90, p90_decl, p90_true


# ---------------------------------------------------------------------------
# 5. unit cases on the module
# ---------------------------------------------------------------------------

def unit_cases(fr, synth, work, base, a90, p90_decl, p90_true):
    fake = types.SimpleNamespace(vendor="bruker", meta={}, read_errors={})
    meta = {"calibration": {"p90_us": 8.5, "p90_power_db_or_w": "-14 dB",
                            "rg_ladder": [], "topshim_ok": False,
                            "nutation": {"skipped": True,
                                         "why": "p90 power unknown"}},
            "experiments": []}
    n = fr.analyze_nutation(fake, meta, 6900.0)
    check("5. skipped form -> available False, skipped True, the script's "
          "reason, INFO row, honesty line says NOT kept as data",
          n["available"] is False and n.get("skipped") is True
          and "p90 power unknown" in n["why"]
          and fr.nutation_qa_rows(n)[0]["level"] == "INFO"
          and fr.nutation_honesty_line(n).startswith(
              "Pulse calibration NOT kept as data"), n)
    ag = types.SimpleNamespace(vendor="agilent", meta={}, read_errors={})
    n = fr.analyze_nutation(ag, {"calibration": {"p90_us": 10.0},
                                 "experiments": []}, 6900.0)
    check("5. Agilent bundle without the block -> 'not acquired by the "
          "Agilent driver', no honesty line",
          n["available"] is False and n["why"]
          == "not acquired by the Agilent driver"
          and fr.nutation_honesty_line(n) is None, n)
    n = fr.analyze_nutation(fake, {"calibration": {"p90_us": 10.0},
                                   "experiments": []}, 6900.0)
    check("5. no block -> available False, no honesty line, INFO row only",
          n["available"] is False and fr.nutation_honesty_line(n) is None
          and [r["level"] for r in fr.nutation_qa_rows(n)] == ["INFO"])
    meta_bad = {"calibration": {"p90_us": 10.0,
                                "nutation": {"expnos": [80, 81, 82],
                                             "p1_us": [2.5, 5.0],
                                             "skipped": False}},
                "experiments": []}
    n = fr.analyze_nutation(fake, meta_bad, 6900.0)
    check("5. inconsistent step table -> available False with a reason, "
          "present True (honesty line), never FAIL",
          n["available"] is False and "unusable" in n["why"]
          and n.get("present") and fr.nutation_honesty_line(n)
          and all(r["level"] != "FAIL" for r in fr.nutation_qa_rows(n)), n)
    # one fid missing: unreadable, fit on nine
    miss = os.path.join(work, "phys_nutation_missing.zip")
    graft_nutation(synth, base, miss, p90_decl, p90_true, a90,
                   drop_expno=82)
    b = fr.Bundle(miss)
    n = fr.analyze_nutation(b, b.meta, 6900.0)
    check("5. a missing nutation fid -> listed under unreadable, nine steps "
          "fitted, P90 within 1%",
          [u["expno"] for u in n.get("unreadable", [])] == [82]
          and n.get("n_steps") == 9 and n.get("available")
          and abs(n["p90_fit_us"] / p90_true - 1.0) < 0.01,
          (n.get("unreadable"), n.get("n_steps")))
    check("5. ... and its QA row stays OK while naming the unreadable step",
          fr.nutation_qa_rows(n)[0]["level"] == "OK"
          and "unreadable" in fr.nutation_qa_rows(n)[0]["detail"])
    # declared P90 8% long -> WARN
    warn = os.path.join(work, "phys_nutation_warn.zip")
    graft_nutation(synth, base, warn, p90_decl, p90_decl / 1.08, a90)
    b = fr.Bundle(warn)
    n = fr.analyze_nutation(b, b.meta, 6900.0)
    rows = fr.nutation_qa_rows(n)
    check("5. declared P90 8%% off -> WARN 'nutation P90' (ratio %.3f), fit "
          "still within 1%% of the truth"
          % (n.get("ratio_fit_over_declared") or 0.0),
          n.get("available") and rows[0]["level"] == "WARN"
          and abs(n["p90_fit_us"] / (p90_decl / 1.08) - 1.0) < 0.01, rows)
    # saturation check on hand-built rows
    import numpy as np
    x = np.zeros(2048, dtype=complex)
    x[100:108] = 2.0e9
    sat = fr.nutation_saturation_check(x, 2.0e9 / 2147483647.0)
    check("5. saturation check: 93% of full scale with an 8-sample flat top "
          "-> clipped, both reasons", sat["clipped"] and sat["flat_top_run"]
          == 8 and "full scale" in sat["why"] and "flat top" in sat["why"], sat)
    y = np.exp(-np.arange(2048) / 300.0) * 1.0e6 * np.exp(1j * np.arange(2048))
    sat = fr.nutation_saturation_check(y, 1.0e6 / 2147483647.0)
    check("5. saturation check: an ordinary FID is not clipped",
          not sat["clipped"] and sat["flat_top_run"] == 1, sat)
    # the fit on an exact curve
    p1 = [m * 8.76 for m in synth.NUTATION_ORDER]
    amps = [3.0e6 * abs(math.sin(math.pi * p / (2 * 8.5))) for p in p1]
    fit = fr.fit_nutation_curve(p1, amps, [1.0e3] * len(p1), 8.76)
    check("5. fit_nutation_curve on an exact curve: P90 8.5 and A90 3e6 to "
          "1e-6", abs(fit["p90_us"] - 8.5) < 1e-5
          and abs(fit["a90_counts"] / 3.0e6 - 1.0) < 1e-6, fit)
    check("5. fit_nutation_curve refuses fewer than 3 points",
          fr.fit_nutation_curve(p1[:2], amps[:2], [1.0, 1.0], 8.76) is None)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--skip-e2e", action="store_true",
                    help="unit cases only (no full report runs)")
    args = ap.parse_args(argv)
    work = args.out_dir or tempfile.mkdtemp(prefix="nutation_report_")
    if not os.path.isdir(work):
        os.makedirs(work)
    try:
        fr = load_module("facility_report", REPORT_PY)
    except ImportError as exc:
        print("FAIL: cannot import analysis/facility_report.py (%s)" % exc)
        return 1
    synth = load_module("make_synthetic_bundle", SYNTH_PY)
    if not args.skip_e2e:
        print("--- 1. synthetic bundle with --nutation ---")
        synthetic_cases(fr, synth, work)
        print("--- 2.-4. physics bundle: clean graft, clipped step, old bundle ---")
        base, a90, p90_decl, p90_true = physics_cases(fr, synth, work)
    else:
        base = subprocess.check_output(
            [sys.executable, PHYS_PY, "--feature", "bump", "--amp", "1.5",
             "--out-dir", work], stderr=subprocess.DEVNULL
        ).decode().strip().splitlines()[-1]
        phys = load_module("make_physics_bundle", PHYS_PY)
        p90_decl, p90_true = 10.0, 10.0 / 1.03
        a90 = phys.REF_A0 / (phys.RG_REF * math.sin(math.radians(1.0)))
    print("--- 5. unit cases ---")
    unit_cases(fr, synth, work, base, a90, p90_decl, p90_true)
    print("")
    if FAILED:
        print("NUTATION REPORT: %d FAILED" % len(FAILED))
        for n in FAILED:
            print("  - %s" % n)
        return 1
    print("NUTATION REPORT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
