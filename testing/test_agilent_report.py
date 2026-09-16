#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agilent_report.py -- validate facility_report's Agilent/Varian read
path and Tier-1 noise aggregation on a synthetic VnmrJ session.

    python3 testing/test_agilent_report.py [--out-dir DIR]
                                           [--real-bundle ZIP ...]

Steps:

  1. build a synthetic Agilent session with
     vendors/agilent/make_synthetic_agilent_data.py (white int32 noise,
     sigma 1000 counts per re/im sample; 4 ladder + 2 reference + 3
     single-row noise expnos) and pack it with packer/pack_bundle.py
     --vendor agilent;
  2. derive a science-path copy: the generator stamps run_mode 'desktest'
     so its bundle can never pass as data, and facility_report refuses
     science numbers for desktest bundles by design. The copy is
     restamped 'synthetic-injection' (the schema's run_mode for
     numerically generated validation data); nothing else changes;
  3. run facility_report on it and assert that every noise / reference /
     ladder experiment is read as Agilent int32 with 1 row and the
     generator's point count, that the block headers were verified (scale
     0, recorded), that the per-row DC is recorded but NOT subtracted,
     that the three noise experiments are aggregated into ONE block of 3
     rows in meta order carrying their source expno and start time, that
     per-row stats are finite and match the generator's white-noise PSD
     (2*sigma^2/sw), that no Bruker default was applied (group delay 0,
     format 'agilent', nothing refused), that S_32 samples get no assumed
     full scale, that the references are reported as a matched pair, that
     the detection's fit basis is stated and consistent with the
     alignment cross-check, that the frequency-sign caveat is stated, and
     that science.axion_exclusion is present with finite worst-case
     numbers (references gain-bridged to the noise rg, D_cal inflated by
     the ladder envelope) and the 'does NOT improve with more measurement
     time' honesty line;
  4. fail-loudly copies: one noise expno stripped of its procpar must be
     REFUSED with an explicit reason (QA FAIL, block shrinks to 2 rows)
     and must NOT also receive an 'ADC check ... OK' row; with every
     noise expno stripped the spin-noise analysis must be marked
     UNAVAILABLE instead of printing a null result; a closing reference
     re-written with a different pw must surface as an unmatched
     reference pair in report.json, the QA flags and the HTML; a noise
     expno at a different rg plus swapped start times must yield two
     parameter groups AND the headline block's time-order warning;
  5. direct Bundle checks on hand-built fids: float32 with a DC offset
     (dtype float32, full-scale fraction None, DC recorded and kept),
     int16 (full scale 32767), int32 (full scale unknown), a block header
     with non-zero scale (refused), a block header whose element-type
     bits disagree with the file header (refused), a header/procpar np
     mismatch (refused), a missing procpar (refused, also by the ADC
     check), an unparseable procpar and a procpar lacking sw (refused
     with the specific reason);
  6. the clock-audit expectation refinement on an Agilent expno degrades
     to a refine_note without a traceback.

With --real-bundle, each given bundle (the SIU Carbondale DD2 sessions)
is also run through the report and checked for: every expno read as
Agilent float32 with a zero-scale block header, nothing refused, the
noise expnos aggregated into one block, the detection's fit basis
consistent with the alignment cross-check and the headline fraction
derived from that same fit, the reference pair recorded (with a QA
WARN when it is not matched), and science.axion_exclusion present: on
the matched-reference session (SIU session 2) the worst-case best g_ap
must lie within 15% of the maintainer's 3.05e-4 GeV^-1 scratch
cross-check AND each input must agree on its own --- kappa*M0 within 12%
of 7.77e5 counts (the scratch takes A0 from the first four FID samples,
the report from its fit), the damping FWHM within 10% of 19.3 Hz, the
positive-part line power within 20% of 0.270 counts^2 and P_90 between
0.9 and 1.5 times the scratch's 0.2776 counts^2 (the report integrates
|PSD - baseline| over the Lorentzian window fraction, the scratch the
positive part only) --- so a cancellation between inputs cannot pass;
on the unmatched session (SIU session 1) the exclusion must be marked
unmatched with each reference at its own tip and the SMALLER kappa*M0
used; both carry the mirror-sign mass and the intersection band.

Exit 0 iff every check passes.
"""

from __future__ import print_function

import argparse
import hashlib
import importlib.util
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import zipfile

TESTING = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(TESTING, ".."))
GENERATOR = os.path.join(REPO, "vendors", "agilent",
                         "make_synthetic_agilent_data.py")
PACKER = os.path.join(REPO, "packer", "pack_bundle.py")
REPORT = os.path.join(REPO, "analysis", "facility_report.py")

FAILURES = []


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    line = "%s : %s" % (tag, name)
    if detail and not ok:
        line += "\n       %s" % str(detail)[:400]
    print(line)
    if not ok:
        FAILURES.append(name)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def derive_bundle(src, dst, run_mode=None, drop_members=(),
                  replace_members=None, edit_meta=None):
    """Copy a bundle, optionally restamping software.run_mode, dropping
    zip members (with their checksum entries), replacing members (with
    their checksum entries recomputed) and editing meta in place."""
    replace_members = replace_members or {}
    zin = zipfile.ZipFile(src)
    meta = json.loads(zin.read("meta.json"))
    if run_mode:
        meta["software"]["run_mode"] = run_mode
    for m in drop_members:
        meta["checksums"].pop(m, None)
    for m, data in replace_members.items():
        meta["checksums"][m] = "sha256:" + hashlib.sha256(data).hexdigest()
    if edit_meta:
        edit_meta(meta)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in zin.namelist():
            if n == "meta.json" or n in drop_members:
                continue
            zout.writestr(n, replace_members.get(n, zin.read(n)))
        zout.writestr("meta.json", json.dumps(meta, indent=1) + "\n")
    return meta


def procpar_edit(pp_bytes, name, value=None):
    """The generator's procpar with one real-valued record set to `value`
    (or removed when value is None). Records are three lines: header,
    '1 <value>', enumerable."""
    lines = pp_bytes.decode("ascii").split("\n")
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith(name + " "):
            if value is not None:
                out.extend([lines[i], "1 %.12g" % value, lines[i + 2]])
            i += 3
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).encode("ascii")


def run_report(bundle, out_dir):
    subprocess.check_call([sys.executable, REPORT, bundle, "--out", out_dir],
                          stdout=subprocess.DEVNULL)
    with open(os.path.join(out_dir, "report.json")) as fh:
        report = json.load(fh)
    with open(os.path.join(out_dir, "report.html")) as fh:
        html = fh.read()
    return report, html


def finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def fit_basis_consistent(sci):
    """detection.fit_basis names the fit the headline numbers came from,
    and it is the unaligned one exactly when the alignment cross-check
    is SUSPECT. Returns (ok, detail)."""
    noise = sci.get("noise") or {}
    det = sci.get("detection") or {}
    hd = sci.get("headline") or {}
    basis = det.get("fit_basis")
    suspect = bool((noise.get("alignment_check") or {}).get("suspect"))
    want = "unaligned_fit" if suspect else "coadd_fit"
    fit = noise.get(basis) or {}
    ok = (basis == want and noise.get("headline_fit") == want
          and fit.get("amp_norm") == det.get("fit_amp_norm")
          and fit.get("amp_err") == det.get("fit_amp_err"))
    if ok and det.get("detected"):
        a = fit["amp_norm"]
        frac = a / (1.0 + a) if a > 0 else abs(a)
        ok = abs((hd.get("spin_coupled_floor_fraction") or 0) - frac) < 1e-9
        if suspect:
            ok = ok and "UNALIGNED" in det.get("fit_basis_note", "") \
                and (sci.get("floor_calibration") or {}).get(
                    "spin_line_fit_basis", "unaligned_fit") == "unaligned_fit"
    detail = "basis=%s want=%s suspect=%s headline=%s det=%s" % (
        basis, want, suspect, hd.get("spin_coupled_floor_fraction"),
        {k: det.get(k) for k in ("detected", "fit_amp_norm", "fit_amp_err")})
    return ok, detail


def check_real_bundle(path, work):
    """Invariants of the read path and headline plumbing on a real
    Agilent bundle."""
    tag = os.path.basename(path)
    out_dir = os.path.join(work, "report_real_" + tag[:-4])
    report, html = run_report(path, out_dir)
    sci = report.get("science") or {}
    exps = json.loads(zipfile.ZipFile(path).read("meta.json"))["experiments"]
    noise_expnos = [e["expno"] for e in exps if e["role"] == "noise"]
    rr = sci.get("raw_data_read") or {}
    by = rr.get("by_expno") or {}
    check("%s: science report, every expno read (%d), none refused"
          % (tag, len(exps)),
          report.get("report_type") == "science" and len(by) == len(exps)
          and not rr.get("refused"), json.dumps(rr.get("refused"))[:300])
    check("%s: every expno Agilent float32, 1 row, block header scale 0 "
          "recorded, DC recorded not subtracted" % tag,
          all(v.get("format") == "agilent" and v.get("dtype") == "float32"
              and v.get("n_rows") == 1
              and (v.get("fid_block_header") or {}).get("scale") == 0
              and v.get("dc_offset_subtracted") is False
              and finite(v.get("dc_offset_max_abs")) for v in by.values()),
          json.dumps(list(by.values())[:1])[:300])
    noise = sci.get("noise") or {}
    check("%s: %d noise expnos aggregated into one block" % (tag,
                                                             len(noise_expnos)),
          noise.get("n_rows") == len(noise_expnos)
          and noise.get("expnos") == noise_expnos
          and (sci.get("noise_aggregation") or {}).get("consistent") is True,
          "n_rows=%s" % noise.get("n_rows"))
    ok, detail = fit_basis_consistent(sci)
    check("%s: detection fit basis consistent with the alignment "
          "cross-check and the headline fraction" % tag, ok, detail)
    if (noise.get("alignment_check") or {}).get("suspect"):
        check("%s: SUSPECT alignment -> HTML shows the UNALIGNED basis and "
              "the headline quotes the unaligned amplitude" % tag,
              "UNALIGNED stack" in html
              and "%.3f" % noise["unaligned_fit"]["amp_norm"] in html)
    qa = sci.get("qa_flags") or []
    adc = [q for q in qa if q["check"].startswith("ADC check")]
    check("%s: ADC checks: %d rows, all float32 'not applicable', no FAIL"
          % (tag, len(adc)),
          len(adc) == len(exps) and all(
              q["level"] == "OK" and "float32" in q["detail"]
              and "not applicable" in q["detail"] for q in adc))
    pair = sci.get("reference_pair") or {}
    warned = any(q["check"] == "reference pair" and q["level"] == "WARN"
                 for q in qa)
    check("%s: reference pair recorded (matched=%s) with QA WARN iff "
          "unmatched" % (tag, pair.get("matched")),
          isinstance(pair.get("matched"), bool) and len(pair.get(
              "pulses") or []) == 2 and warned == (not pair["matched"])
          and ((not pair["matched"]) == ("NOT a matched pair" in html)),
          json.dumps(pair)[:300])
    if not pair.get("matched"):
        check("%s: unmatched pair caveat sits next to the A0 ratio" % tag,
              "a0_ratio_caveat" in (sci.get("floor_calibration") or {}))
    ex = sci.get("axion_exclusion") or {}
    res = ex.get("result") or {}
    curve = ex.get("curve") or {}
    check("%s: science.axion_exclusion available, finite worst-case and "
          "nominal best g, curve arrays of one length" % tag,
          ex.get("available") is True
          and finite(res.get("g90_worst_best_gev_inv"))
          and res["g90_worst_best_gev_inv"] > 0
          and finite(res.get("g90_nominal_best_gev_inv"))
          and res["g90_nominal_best_gev_inv"] < res["g90_worst_best_gev_inv"]
          and len(curve.get("m_a_ev") or []) == len(curve.get("g90_worst")
                                                    or []) > 0
          and len(curve["m_a_ev"]) == len(curve.get("g90_nominal") or []),
          json.dumps(res or ex)[:300])
    if not ex.get("available"):
        return
    tr = ex["transduction"]
    sp = ex["signal_power"]
    g = res["g90_worst_best_gev_inv"]
    if pair.get("matched"):
        check("%s: matched references -> worst-case best g %.3g GeV^-1 "
              "within 15%% of the 3.05e-4 scratch cross-check, kappa*M0 "
              "the mean of both references at the noise rg" % (tag, g),
              abs(g / 3.05e-4 - 1.0) < 0.15
              and tr["matched_references"] is True
              and tr["rg_bridged"] is False and len(tr["references"]) == 2
              and abs(tr["kappa_M0_counts"] - sum(
                  p["kappa_M0_counts"] for p in tr["references"]) / 2.0)
              < 1e-6 * tr["kappa_M0_counts"],
              json.dumps(tr)[:400])
        dmw = ex["damping"]["fwhm_used_hz"]
        check("%s: each exclusion input agrees with the scratch on its own "
              "--- kappa*M0 %.4g (7.77e5 +/-12%%), damping FWHM %.2f Hz "
              "(19.3 +/-10%%), positive-part line power %.4g (0.270 "
              "+/-20%%), P_90 %.4g (0.9..1.5 x 0.2776), tip 1 deg from "
              "pw 0.05 / p90 4.5 us at tpwr 56 dB"
              % (tag, tr["kappa_M0_counts"], dmw,
                 sp["P_positive_part_counts2"], sp["P_90_counts2"]),
              abs(tr["kappa_M0_counts"] / 7.77e5 - 1.0) < 0.12
              and abs(dmw / 19.3 - 1.0) < 0.10
              and abs(sp["P_positive_part_counts2"] / 0.270 - 1.0) < 0.20
              and 0.9 < sp["P_90_counts2"] / 0.2776 < 1.5
              and sp["P_absolute_excess_counts2"]
              >= sp["P_positive_part_counts2"]
              and abs(sp["P_line_counts2"] - sp["P_absolute_excess_counts2"]
                      / sp["lorentzian_window_fraction"]) < 1e-9
              and all(abs(p["tip_deg"] - 1.0) < 1e-9
                      and p["pulse_us"] == 0.05 and p["power_db"] == 56.0
                      for p in tr["references"]),
              json.dumps({"km0": tr["kappa_M0_counts"], "fwhm": dmw,
                          "sp": sp})[:500])
    else:
        kms = [p["kappa_M0_counts"] for p in tr["references"]]
        check("%s: unmatched references -> marked, each reference at its "
              "own tip (%s deg), the SMALLER kappa*M0 (%.4g of %s) used, "
              "said in JSON, honesty and HTML"
              % (tag, [round(p["tip_deg"], 3) for p in tr["references"]],
                 tr["kappa_M0_counts"], ["%.4g" % k for k in kms]),
              tr["matched_references"] is False and len(kms) == 2
              and abs(tr["kappa_M0_counts"] - min(kms)) < 1e-9 * min(kms)
              and len(set(round(p["tip_deg"], 6)
                          for p in tr["references"])) == 2
              and "NOT a matched pair" in tr["basis"]
              and any(h.startswith("Unmatched references")
                      for h in ex.get("honesty") or [])
              and "SMALLER" in html, json.dumps(tr)[:400])
    check("%s: sign unverified -> mirror mass, intersection and union "
          "bands, mirror mass axis on the curve, sign honesty line" % tag,
          ex["line"]["sign_verified"] is False
          and finite(ex["line"].get("m_a_uev_mirror"))
          and all(k in res["band_10x_uev"] for k in
                  ("nominal", "mirror", "intersection", "union"))
          and "m_a_ev_mirror" in curve
          and any("INTERSECTION" in h for h in ex.get("honesty") or []),
          json.dumps(res.get("band_10x_uev")))
    dm = ex["damping"]
    check("%s: Gamma = pi x max(noise POWER FWHM %.2f, reference MAGNITUDE "
          "FWHM %.2f / sqrt3) Hz, both widths recorded"
          % (tag, dm["noise_line_power_fwhm_hz"] or 0,
             dm["reference_magnitude_fwhm_hz"] or 0),
          abs(dm["fwhm_used_hz"] - max(dm["noise_line_power_fwhm_hz"],
                                       dm["reference_magnitude_fwhm_hz"]
                                       / 3 ** 0.5)) < 1e-9
          and abs(dm["gamma_per_s"] - math.pi * dm["fwhm_used_hz"]) < 1e-9,
          json.dumps(dm)[:300])
    check("%s: D_cal 4.6 recorded (no gain bridge on this session)" % tag,
          ex["calibration_derating"]["D_cal"] == 4.6
          and ex["calibration_derating"]["D_cal_pilot"] == 4.6)
    check("%s: HTML card present with the inputs, the time-independence "
          "line, and no 'published' anywhere" % tag,
          "Axion-coupling exclusion (worst-case, this session)" in html
          and "does NOT improve with more measurement time" in html
          and "%.3g" % g in html
          and "publication" not in html.lower()
          and html.lower().count("published")
          == html.lower().count("unpublished"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--real-bundle", action="append", default=[],
                    help="a real Agilent bundle zip to run the invariant "
                         "checks on (repeatable; read-only)")
    args = ap.parse_args(argv)
    work = args.out_dir or tempfile.mkdtemp(prefix="agilent_report_test_")
    if not os.path.isdir(work):
        os.makedirs(work)

    gen = load_module("snn_agilent_gen", GENERATOR)
    sys.path.insert(0, os.path.join(REPO, "analysis"))
    import facility_report as fr

    # ------------------------------------------------------------------
    # 1. synthetic session -> packed bundle
    # ------------------------------------------------------------------
    session = subprocess.check_output(
        [sys.executable, GENERATOR, "--out-dir", work],
        stderr=subprocess.DEVNULL).decode().strip().splitlines()[-1]
    bundle = subprocess.check_output(
        [sys.executable, PACKER, session, "--answers",
         os.path.join(session, "answers_packer.json"), "--vendor", "agilent",
         "--out-dir", work],
        stderr=subprocess.DEVNULL).decode().strip().splitlines()[-1]
    check("synthetic Agilent session packed", os.path.isfile(bundle), bundle)
    meta0 = json.loads(zipfile.ZipFile(bundle).read("meta.json"))
    check("packed bundle is stamped desktest (never data)",
          meta0["software"].get("run_mode") == "desktest")

    # ------------------------------------------------------------------
    # 2./3. science-path copy
    # ------------------------------------------------------------------
    sci_bundle = os.path.join(work, os.path.basename(bundle)[:-9]
                              + "_beef.zip")
    meta = derive_bundle(bundle, sci_bundle, run_mode="synthetic-injection")
    report, html = run_report(sci_bundle, os.path.join(work, "report_sci"))
    sci = report.get("science") or {}
    check("science path ran (report_type synthetic-validation)",
          report.get("report_type") == "synthetic-validation"
          and bool(sci), report.get("report_type"))

    exps = meta["experiments"]
    by_role = {}
    for e in exps:
        by_role.setdefault(e["role"], []).append(e)
    noise_expnos = [e["expno"] for e in by_role["noise"]]
    n_complex = {e["expno"]: e["td"] // 2 for e in exps}

    rr = sci.get("raw_data_read") or {}
    by = rr.get("by_expno") or {}
    check("every experiment read (%d of %d), none refused"
          % (len(by), len(exps)),
          len(by) == len(exps) and not rr.get("refused"),
          "refused=%s" % rr.get("refused"))
    check("every experiment read as format 'agilent', dtype int32 "
          "(from the fid status bits), 1 row, generator point count",
          all(by.get(str(e["expno"]), {}).get("format") == "agilent"
              and by[str(e["expno"])].get("dtype") == "int32"
              and by[str(e["expno"])].get("n_rows") == 1
              and by[str(e["expno"])].get("n_points_complex")
              == n_complex[e["expno"]]
              for e in exps),
          json.dumps(by)[:400])
    check("point counts: noise %d complex, reference/ladder %d complex"
          % (gen.NOISE_NP // 2, gen.REF_NP // 2),
          all(by[str(e)]["n_points_complex"] == gen.NOISE_NP // 2
              for e in noise_expnos)
          and all(by[str(e["expno"])]["n_points_complex"] == gen.REF_NP // 2
                  for e in by_role["reference_open"] + by_role["rg_ladder"]))
    check("block header verified and recorded on every row (scale 0, "
          "status agrees with the file header); DC recorded, NOT subtracted",
          all((v.get("fid_block_header") or {}).get("scale") == 0
              and (v["fid_block_header"].get("status") & 0xC)
              == (v.get("fid_status") & 0xC)
              and v.get("dc_offset_subtracted") is False
              and finite(v.get("dc_offset_max_abs")) for v in by.values()),
          json.dumps(list(by.values())[:1])[:300])
    check("read-path note states the block-header check and that the DC "
          "is not subtracted",
          "scale must be 0" in rr.get("agilent_note", "")
          and "NOT subtracted" in rr.get("agilent_note", ""))

    noise = sci.get("noise") or {}
    agg = sci.get("noise_aggregation") or {}
    check("noise aggregation: %d noise expnos -> ONE consistent block"
          % len(noise_expnos),
          agg.get("n_parameter_groups") == 1 and agg.get("consistent") is True
          and agg.get("headline_expnos") == noise_expnos, json.dumps(agg)[:300])
    check("headline noise block has n_rows == number of noise expnos",
          noise.get("n_rows") == len(noise_expnos)
          and noise.get("n_experiments") == len(noise_expnos)
          and noise.get("expnos") == noise_expnos,
          "n_rows=%s expnos=%s" % (noise.get("n_rows"), noise.get("expnos")))
    per_row = noise.get("per_row") or []
    check("per-row records carry source expno (meta order) and started_local",
          [pr.get("expno") for pr in per_row] == noise_expnos
          and all(pr.get("started_local") for pr in per_row),
          str([(pr.get("expno"), pr.get("started_local"))
               for pr in per_row]))
    check("noise block is Agilent-format, rg 10^(60/20)",
          noise.get("data_format") == "agilent"
          and abs((noise.get("rg") or 0) - 10.0 ** (gen.NOISE_GAIN_DB / 20.0))
          < 1e-6 and noise.get("axis_sign_unverified") is True)

    # white complex noise, independent re/im of variance sigma^2, sampled
    # at sw: two-sided PSD = 2*sigma^2/sw (counts^2/Hz)
    expect_psd = 2.0 * 1000.0 ** 2 / gen.SW_HZ
    meds = [pr.get("psd_median_in_band") for pr in per_row]
    check("per-row PSD medians finite and within 15%% of the generator's "
          "white-noise floor %.0f counts^2/Hz" % expect_psd,
          meds and all(finite(m) and abs(m / expect_psd - 1.0) < 0.15
                       for m in meds), str(meds))
    stats_ok = True
    for pr in per_row:
        for k in ("nseg", "n_spikes", "resolution_hz", "nperseg"):
            if not finite(pr.get(k)):
                stats_ok = False
        ft = pr.get("fit")
        if ft and not all(finite(ft.get(k)) for k in
                          ("amp_norm", "amp_err", "center_hz", "fwhm_hz")):
            stats_ok = False
    check("per-row statistics all finite", stats_ok,
          json.dumps(per_row)[:400])
    check("row length is the noise expno's aq (np/(2 sw)) in seconds",
          abs((noise.get("row_seconds") or 0)
              - gen.NOISE_NP / (2.0 * gen.SW_HZ)) < 1e-9,
          noise.get("row_seconds"))

    refs = sci.get("references") or []
    check("both references readable, 1 row x %d complex points, Agilent "
          "format, group delay 0 (no Bruker GRPDLY default)"
          % (gen.REF_NP // 2),
          len(refs) == 2 and all(
              r.get("readable") and r.get("n_rows") == 1
              and r.get("n_points_complex") == gen.REF_NP // 2
              and r.get("data_format") == "agilent"
              and r.get("group_delay_points_used") == 0 for r in refs),
          json.dumps([{k: r.get(k) for k in (
              "expno", "readable", "n_rows", "n_points_complex",
              "data_format", "group_delay_points_used")} for r in refs]))
    check("reference rg = 10^(%.0f/20) from procpar gain" % gen.REF_GAIN_DB,
          all(abs(r.get("rg", 0) - 10.0 ** (gen.REF_GAIN_DB / 20.0)) < 1e-6
              for r in refs))
    pair = sci.get("reference_pair") or {}
    check("references recorded as a MATCHED pair (same pw/tpwr), no QA WARN",
          pair.get("matched") is True and len(pair.get("pulses") or []) == 2
          and not any(q["check"] == "reference pair"
                      for q in sci.get("qa_flags") or [])
          and "matched pair" in html and "NOT a matched pair" not in html,
          json.dumps(pair)[:300])

    lad = sci.get("rg_ladder") or {}
    rungs = lad.get("rungs") or []
    check("RG ladder: 4 rungs read, rg %s, %d complex points each, "
          "finite amplitudes, group delay 0"
          % ([10.0 ** (g / 20.0) for _e, _t, g in gen.LADDER], gen.REF_NP // 2),
          lad.get("available") is True and len(rungs) == 4
          and [r["expno"] for r in rungs] == [e for e, _t, _g in gen.LADDER]
          and all(abs(r["rg"] - 10.0 ** (g / 20.0)) < 1e-6
                  for r, (_e, _t, g) in zip(rungs, gen.LADDER))
          and all(r.get("n_points_complex") == gen.REF_NP // 2
                  and finite(r.get("amplitude_counts"))
                  and r.get("group_delay_points_used") == 0 for r in rungs)
          and not lad.get("grpdly_note"),
          json.dumps(lad)[:400])

    det = sci.get("detection") or {}
    check("detection ran on the aggregated block (not 'unavailable')",
          not det.get("unavailable") and finite(det.get("amp_significance")),
          json.dumps(det)[:300])
    ok, detail = fit_basis_consistent(sci)
    check("detection states its fit basis, consistent with the alignment "
          "cross-check and the headline", ok, detail)
    check("frequency-axis sign marked UNVERIFIED for the Agilent bundle",
          (sci.get("frequency_axis_sign") or {}).get("verified") is False
          and "UNVERIFIED" in ((sci.get("frequency_axis_sign") or {})
                               .get("note") or ""))
    check("axion-mass bookkeeping carries the offset-sign caveat",
          "SIGN" in ((sci.get("axion_mass_bookkeeping") or {})
                     .get("offset_sign_caveat") or ""))
    qa = sci.get("qa_flags") or []
    fails = [q for q in qa if q.get("level") == "FAIL"]
    check("no QA FAIL on the clean bundle", not fails, json.dumps(fails)[:300])
    adc = [q for q in qa if q["check"].startswith("ADC check")]
    check("ADC checks are dtype-aware: %d int32 rows, S_32 full scale NOT "
          "assumed ('not applicable'), none 'float64'" % len(adc),
          len(adc) == len(exps) and all(
              "int32" in q["detail"] and "not applicable" in q["detail"]
              and "full scale" not in q["detail"].split("not applicable")[1]
              for q in adc)
          and not any("float64" in q["detail"] for q in adc),
          json.dumps(adc)[:300])
    check("HTML states aggregation, read path and the sign caveat",
          "aggregated as ONE noise block" in html
          and "Raw-data read path" in html and "UNVERIFIED" in html)
    ex = sci.get("axion_exclusion") or {}
    res = ex.get("result") or {}
    check("axion exclusion present with finite numbers (worst-case and "
          "nominal best g, mass, band, 1100-point curve)",
          ex.get("available") is True
          and finite(res.get("g90_worst_best_gev_inv"))
          and res["g90_worst_best_gev_inv"] > 0
          and finite(res.get("g90_nominal_best_gev_inv"))
          and finite(res.get("m_a_at_best_uev"))
          and all(finite(v) for v in res.get("band_10x_offset_hz") or [None])
          and len((ex.get("curve") or {}).get("g90_worst") or []) == 1100
          and all(finite(v) and v > 0 for v in ex["curve"]["g90_worst"]),
          json.dumps(res or ex)[:400])
    if ex.get("available"):
        tr = ex["transduction"]
        check("references at 20 dB bridged to the 60 dB noise rg (x100 in "
              "amplitude), tip 90 x 1.0/10.0 = 9 deg, D_cal inflated by "
              "the ladder's power envelope",
              tr["rg_bridged"] is True
              and all(abs(p["rg_bridge_amplitude"] - 100.0) < 1e-6
                      and abs(p["tip_deg"] - 9.0) < 1e-9
                      for p in tr["references"])
              and ex["calibration_derating"]["D_cal"] > 4.6
              and "inflated" in ex["calibration_derating"]["basis"]
              and any(h.startswith("Receiver-gain bridge")
                      for h in ex["honesty"]), json.dumps(tr)[:400])
        hon = ex.get("honesty") or []
        check("axion exclusion honesty: 'does NOT improve with more "
              "measurement time', D_cal reuse, sign caveat, unpublished",
              any("does NOT improve with more measurement time" in h
                  for h in hon)
              and any("D_cal" in h and "pilot" in h for h in hon)
              and any("UNVERIFIED" in h for h in hon)
              and any("UNPUBLISHED" in h for h in hon), json.dumps(hon)[:400])
        if ex["signal_power"]["feature"] == "null":
            sp = ex["signal_power"]
            check("null block -> P_90 fluctuation-level (positive part < 5 "
                  "per-row sigma, statistical term > 20%) and said "
                  "'statistics-limited'",
                  sp["P_positive_part_counts2"] < 5 * sp["sigma_stat_counts2"]
                  and sp["statistical_fraction_of_P90"] > 0.2
                  and any("statistics-limited" in h for h in hon),
                  json.dumps(sp)[:300])
        check("HTML card 'Axion-coupling exclusion (worst-case, this "
              "session)' with the time-independence line; global honesty "
              "points at it",
              "Axion-coupling exclusion (worst-case, this session)" in html
              and "does NOT improve with more measurement time" in html
              and any("Axion-coupling exclusion" in h
                      for h in sci.get("honesty") or []))

    # the packer stamps real vendor-software acquisitions
    # 'external-acquisition': that must be a science report, not a
    # synthetic validation
    ext_bundle = os.path.join(work, os.path.basename(bundle)[:-9]
                              + "_ea01.zip")
    derive_bundle(bundle, ext_bundle, run_mode="external-acquisition")
    rep_ext, html_ext = run_report(ext_bundle, os.path.join(work, "report_ext"))
    check("run_mode external-acquisition -> report_type 'science' with the "
          "same aggregated block",
          rep_ext.get("report_type") == "science"
          and ((rep_ext.get("science") or {}).get("noise") or {}).get(
              "n_rows") == len(noise_expnos)
          and "SYNTHETIC-INJECTION" not in html_ext,
          rep_ext.get("report_type"))

    # ------------------------------------------------------------------
    # 4. fail loudly
    # ------------------------------------------------------------------
    one_bad = os.path.join(work, os.path.basename(bundle)[:-9] + "_bad1.zip")
    dropped = noise_expnos[-1]
    derive_bundle(bundle, one_bad, run_mode="synthetic-injection",
                  drop_members=("data/%d/procpar" % dropped,))
    rep1, html1 = run_report(one_bad, os.path.join(work, "report_bad1"))
    s1 = rep1.get("science") or {}
    refused = (s1.get("raw_data_read") or {}).get("refused") or {}
    check("expno %d without procpar is REFUSED with an explicit reason"
          % dropped,
          str(dropped) in refused
          and "no procpar" in refused[str(dropped)]
          and "NOT" in refused[str(dropped)], json.dumps(refused)[:300])
    n1 = s1.get("noise") or {}
    check("headline block shrinks to the readable %d rows and lists the "
          "skipped expno" % (len(noise_expnos) - 1),
          n1.get("n_rows") == len(noise_expnos) - 1
          and n1.get("expnos") == noise_expnos[:-1]
          and [s["expno"] for s in n1.get("skipped_experiments") or []]
          == [dropped], json.dumps({k: n1.get(k) for k in (
              "n_rows", "expnos", "skipped_experiments")}))
    qa1 = s1.get("qa_flags") or []
    check("QA carries a FAIL row for the refused expno",
          any(q["level"] == "FAIL" and "expno %d" % dropped in q["check"]
              for q in qa1), json.dumps([q for q in qa1
                                         if q["level"] == "FAIL"])[:300])
    adc1 = [q for q in qa1 if q["check"] == "ADC check expno %d" % dropped]
    check("the refused expno gets NO 'ADC check OK' row (its ADC row is the "
          "refusal)",
          len(adc1) == 1 and adc1[0]["level"] == "FAIL"
          and "procpar" in adc1[0]["detail"], json.dumps(adc1)[:300])
    check("HTML marks the refusal", "Refused" in html1 and "EXCLUDED" in html1)

    all_bad = os.path.join(work, os.path.basename(bundle)[:-9] + "_bad3.zip")
    derive_bundle(bundle, all_bad, run_mode="synthetic-injection",
                  drop_members=tuple("data/%d/procpar" % e
                                     for e in noise_expnos))
    rep3, html3 = run_report(all_bad, os.path.join(work, "report_bad3"))
    s3 = rep3.get("science") or {}
    det3 = s3.get("detection") or {}
    check("with no readable noise expno the analysis is UNAVAILABLE, not a "
          "null result",
          det3.get("unavailable") is True and det3.get("detected") is False
          and "upper_limit_95_amp" not in det3
          and "UNAVAILABLE" in (s3.get("headline") or {}).get("status", ""),
          json.dumps(det3)[:300])
    check("HTML says UNAVAILABLE and never 'No significant spin-noise "
          "feature'",
          "UNAVAILABLE" in html3
          and "No significant spin-noise feature was detected" not in html3)
    check("all three refusals listed",
          sorted((s3.get("raw_data_read") or {}).get("refused") or {})
          == sorted(str(e) for e in noise_expnos))
    ex3 = s3.get("axion_exclusion") or {}
    check("with no readable noise block the axion exclusion is unavailable "
          "with a reason, never a number; honesty says NOT determined",
          ex3.get("available") is False
          and "no readable noise block" in ex3.get("reason", "")
          and "result" not in ex3
          and any(h.startswith("NOT determined: the axion-coupling")
                  for h in s3.get("honesty") or [])
          and "Not computed" in html3, json.dumps(ex3)[:300])

    # closing reference acquired at a different flip angle (SIU session
    # 1's Defect 1): must be machine-readable, not HTML-only
    zsrc = zipfile.ZipFile(bundle)
    pp13 = zsrc.read("data/13/procpar")
    unmatched = os.path.join(work, os.path.basename(bundle)[:-9]
                             + "_pw13.zip")
    derive_bundle(bundle, unmatched, run_mode="synthetic-injection",
                  replace_members={"data/13/procpar":
                                   procpar_edit(pp13, "pw", 2.25)})
    rep_u, html_u = run_report(unmatched, os.path.join(work, "report_pw13"))
    s_u = rep_u.get("science") or {}
    pair_u = s_u.get("reference_pair") or {}
    check("unmatched reference pair (pw 1.0 vs 2.25 us) recorded in "
          "report.json with both pulses",
          pair_u.get("matched") is False
          and sorted(p["pw_us"] for p in pair_u.get("pulses") or [])
          == [1.0, 2.25] and "NOT a matched pair" in pair_u.get("note", ""),
          json.dumps(pair_u)[:300])
    check("unmatched pair -> QA WARN 'reference pair', honesty line, HTML "
          "warning, caveat next to the A0 ratio",
          any(q["check"] == "reference pair" and q["level"] == "WARN"
              for q in s_u.get("qa_flags") or [])
          and any("Reference pair" in h for h in s_u.get("honesty") or [])
          and "NOT a matched pair" in html_u
          and "a0_ratio_caveat" in (s_u.get("floor_calibration") or {}),
          json.dumps([q for q in s_u.get("qa_flags") or []
                      if q["level"] == "WARN"])[:300])

    # two parameter groups AND swapped start times in the headline group:
    # the time-order warning must fire regardless of the group count
    def _two_groups(m):
        by_no = {e["expno"]: e for e in m["experiments"]}
        by_no[noise_expnos[-1]]["rg"] = 100.0
        a, b = by_no[noise_expnos[0]], by_no[noise_expnos[1]]
        for k in ("started_local", "finished_local"):
            a[k], b[k] = b[k], a[k]

    multi = os.path.join(work, os.path.basename(bundle)[:-9] + "_grp2.zip")
    derive_bundle(bundle, multi, run_mode="synthetic-injection",
                  edit_meta=_two_groups)
    rep_m, _html_m = run_report(multi, os.path.join(work, "report_grp2"))
    s_m = rep_m.get("science") or {}
    agg_m = s_m.get("noise_aggregation") or {}
    check("noise expno at another rg -> 2 parameter groups, headline group "
          "= the %d same-rg expnos, flagged inconsistent" % (
              len(noise_expnos) - 1),
          agg_m.get("n_parameter_groups") == 2
          and agg_m.get("consistent") is False
          and agg_m.get("headline_expnos") == noise_expnos[:-1],
          json.dumps({k: agg_m.get(k) for k in (
              "n_parameter_groups", "consistent", "headline_expnos")}))
    check("swapped start times inside the headline group -> time_order_note "
          "and QA WARN 'noise row time order' even with 2 groups",
          bool(agg_m.get("time_order_note"))
          and any(q["check"] == "noise row time order"
                  for q in s_m.get("qa_flags") or []),
          json.dumps(agg_m.get("time_order_note")))

    # ------------------------------------------------------------------
    # 5. direct Bundle checks on hand-built fids
    # ------------------------------------------------------------------
    def varian_fid(values, status, block_status=None, scale=0):
        """One block, one trace; values interleaved re/im."""
        if status & fr.VARIAN_S_FLT:
            fmt, ebytes = "f", 4
        elif status & fr.VARIAN_S_32:
            fmt, ebytes = "i", 4
        else:
            fmt, ebytes = "h", 2
        npts = len(values)
        tbytes = npts * ebytes
        bbytes = fr.VARIAN_BLOCK_HEADER_BYTES + tbytes
        head = struct.pack(fr.VARIAN_FILE_HEADER, 1, 1, npts, ebytes, tbytes,
                           bbytes, 0, status, 1)
        bhead = struct.pack(fr.VARIAN_BLOCK_HEADER, scale,
                            status if block_status is None else block_status,
                            1, 0, 1, 0.0, 0.0, 0.0, 0.0)
        return head + bhead + struct.pack(">%d%s" % (npts, fmt), *values)

    # the procpar of the packed synthetic noise expno is reused verbatim
    # (np = NOISE_NP, gain = NOISE_GAIN_DB), so this part depends only on
    # the generator's public constants
    mini = os.path.join(work, "mini_agilent.zip")
    npts = gen.NOISE_NP
    pp_bytes = zsrc.read("data/%d/procpar" % noise_expnos[0])
    import random
    rnd = random.Random(7)
    f32 = []
    for _ in range(npts // 2):
        f32 += [rnd.gauss(5.0, 1.0), rnd.gauss(-3.0, 1.0)]   # DC 5-3j
    i16 = [int(rnd.gauss(0, 2000)) for _ in range(npts)]
    i16[10] = 30000
    i32 = [int(rnd.gauss(0, 20000)) for _ in range(npts)]
    i32[20] = 32767                                          # railed 16-bit ADC
    with zipfile.ZipFile(mini, "w") as zf:
        zf.writestr("meta.json", json.dumps({
            "vendor": "agilent", "schema_version": "2.0",
            "experiments": [{"expno": n, "role": "noise"}
                            for n in range(1, 10)]}))
        for n in (1, 2, 3, 5, 6, 7):
            zf.writestr("data/%d/procpar" % n, pp_bytes)
        zf.writestr("data/1/fid", varian_fid(f32, 0x1 | fr.VARIAN_S_FLT))
        zf.writestr("data/2/fid", varian_fid(i16, 0x1))
        zf.writestr("data/3/fid", varian_fid(i16[:256], 0x1 | fr.VARIAN_S_32))
        zf.writestr("data/4/fid", varian_fid(i16, 0x1))
        zf.writestr("data/5/fid", varian_fid(i16, 0x1, scale=3))
        zf.writestr("data/6/fid", varian_fid(f32, 0x1 | fr.VARIAN_S_FLT,
                                             block_status=0x1))
        zf.writestr("data/7/fid", varian_fid(i32, 0x1 | fr.VARIAN_S_32))
        zf.writestr("data/8/procpar", bytes(range(256)) * 8)
        zf.writestr("data/8/fid", varian_fid(i16, 0x1))
        zf.writestr("data/9/procpar", procpar_edit(pp_bytes, "sw"))
        zf.writestr("data/9/fid", varian_fid(i16, 0x1))
    b = fr.Bundle(mini)
    rows, acq = b.read_rows(1, {"expno": 1, "td": npts, "td1_rows": 1})
    check("float32 fid: dtype float32, TD/SW_h/RG/NS/DS/DE/GRPDLY/PULPROG "
          "synthesized from procpar",
          rows is not None and acq.get("_dtype") == "float32"
          and acq.get("TD") == npts and abs(acq.get("SW_h", 0) - gen.SW_HZ)
          < 1e-9 and abs(acq.get("RG", 0)
                         - 10.0 ** (gen.NOISE_GAIN_DB / 20.0)) < 1e-9
          and acq.get("NS") == 1 and acq.get("DS") == 0
          and acq.get("DE") == 0.0 and acq.get("GRPDLY") == 0.0
          and acq.get("PULPROG") == "s2pul", str(acq)[:300])
    check("float32 fid: per-row complex mean (DC 5-3j) recorded in read_log "
          "and KEPT in the rows",
          rows is not None and abs(complex(rows[0].mean()) - (5 - 3j)) < 0.2
          and b.read_log[1]["dc_offset_subtracted"] is False
          and abs(b.read_log[1]["dc_offset_max_abs"] - abs(5 - 3j)) < 0.2
          and b.read_log[1]["fid_block_header"]["scale"] == 0,
          "mean=%s log=%s" % (rows[0].mean() if rows is not None else None,
                              b.read_log.get(1)))
    st = b.raw_int_stats(1)
    check("float32 fid: fullscale_fraction None",
          st and st["dtype"] == "float32" and st["fullscale_fraction"] is None,
          str(st))
    rows2, acq2 = b.read_rows(2, {"expno": 2, "td": npts, "td1_rows": 1})
    st2 = b.raw_int_stats(2)
    check("int16 fid: dtype int16, full scale 32767, %d complex points"
          % (npts // 2),
          rows2 is not None and rows2.shape == (1, npts // 2)
          and acq2.get("_dtype") == "int16" and st2["dtype"] == "int16"
          and abs(st2["fullscale_fraction"] - 30000.0 / 32767.0) < 1e-9,
          str(st2))
    rows7, acq7 = b.read_rows(7, {"expno": 7, "td": npts, "td1_rows": 1})
    st7 = b.raw_int_stats(7)
    check("int32 (S_32) fid: read as int32, but NO full scale assumed "
          "(a railed 16-bit ADC word must not pass as 0.0015%)",
          rows7 is not None and acq7.get("_dtype") == "int32"
          and st7["dtype"] == "int32" and st7["fullscale_fraction"] is None
          and st7["max_abs"] >= 32767.0, str(st7))
    rows3, _acq3 = b.read_rows(3, {"expno": 3, "td": npts, "td1_rows": 1})
    check("fid np != procpar np is refused, not read",
          rows3 is None and 3 in b.read_errors
          and "disagrees" in b.read_errors[3], str(b.read_errors.get(3)))
    rows5, _acq5 = b.read_rows(5, {"expno": 5, "td": npts, "td1_rows": 1})
    check("block header scale 3 is refused (samples 8x low), not rescaled",
          rows5 is None and 5 in b.read_errors
          and "scale 3" in b.read_errors[5]
          and "8-fold" in b.read_errors[5], str(b.read_errors.get(5)))
    rows6, _acq6 = b.read_rows(6, {"expno": 6, "td": npts, "td1_rows": 1})
    check("block header element-type bits disagreeing with the file header "
          "are refused",
          rows6 is None and 6 in b.read_errors
          and "disagrees with the file header" in b.read_errors[6],
          str(b.read_errors.get(6)))
    rows4, _acq4 = b.read_rows(4, {"expno": 4, "td": npts, "td1_rows": 1})
    check("Agilent expno without procpar is refused, not read",
          rows4 is None and 4 in b.read_errors
          and "no procpar" in b.read_errors[4]
          and "files present: fid" in b.read_errors[4],
          str(b.read_errors.get(4)))
    b_adc = fr.Bundle(mini)
    st4 = b_adc.raw_int_stats(4)
    check("ADC check on an expno without procpar refuses too (None + "
          "read_errors), never an 'OK' on unread data",
          st4 is None and 4 in b_adc.read_errors
          and "no procpar" in b_adc.read_errors[4],
          "%s | %s" % (st4, b_adc.read_errors.get(4)))
    rows8, _acq8 = b.read_rows(8, {"expno": 8, "td": npts, "td1_rows": 1})
    check("binary garbage procpar: refused with the parse diagnosis, not "
          "'no procpar'",
          rows8 is None and 8 in b.read_errors
          and "unusable" in b.read_errors[8]
          and "recognised" in b.read_errors[8]
          and "no procpar" not in b.read_errors[8],
          str(b.read_errors.get(8)))
    rows9, _acq9 = b.read_rows(9, {"expno": 9, "td": npts, "td1_rows": 1})
    check("procpar without an sw record: refused naming sw=None",
          rows9 is None and 9 in b.read_errors
          and "sw=None" in b.read_errors[9]
          and "np=%s" % float(npts) in b.read_errors[9],
          str(b.read_errors.get(9)))
    check("group_delay_points: Agilent 0, Bruker GRPDLY, Bruker default 68",
          fr.group_delay_points({"_vendor": "agilent"}) == 0
          and fr.group_delay_points({"GRPDLY": 67.98}) == 68
          and fr.group_delay_points({"GRPDLY": 76.0}) == 76
          and fr.group_delay_points({}) == fr.GRPDLY_DEFAULT == 68
          and fr.group_delay_points({"GRPDLY": -1}) == 68)

    # headline_fit: the aligned co-add unless the cross-check said SUSPECT
    aligned = {"amp_norm": 0.48, "amp_err": 0.02}
    unaligned = {"amp_norm": 0.27, "amp_err": 0.01}
    check("headline_fit picks coadd_fit normally, unaligned_fit when "
          "SUSPECT, coadd_fit when no unaligned fit exists, None when empty",
          fr.headline_fit({"coadd_fit": aligned, "unaligned_fit": unaligned,
                           "headline_fit": "coadd_fit"})
          == (aligned, "coadd_fit")
          and fr.headline_fit({"coadd_fit": aligned,
                               "unaligned_fit": unaligned,
                               "headline_fit": "unaligned_fit"})
          == (unaligned, "unaligned_fit")
          and fr.headline_fit({"coadd_fit": aligned,
                               "headline_fit": "unaligned_fit"})
          == (aligned, "coadd_fit")
          and fr.headline_fit({"coadd_fit": aligned}) == (aligned, "coadd_fit")
          and fr.headline_fit(None) == (None, None)
          and fr.headline_fit({}) == (None, None))
    # the exclusion's tip angle: calibration.p90_power_db_or_w is read only
    # with an explicit unit (the schema allows dB or watts), a Bruker
    # reference whose power does not resolve takes the ladder tip scaled
    # by its own P1/P90
    cal_ladder = {"p90_us": 10.0, "p90_power_db_or_w": "n/a",
                  "rg_ladder": [{"tip_deg": 1.0, "rg": 1.0}]}
    tip_2x, basis_2x = fr.reference_tip_deg(
        {"data_format": "bruker", "p1_us": 20.0}, cal_ladder)
    tip_1x, basis_1x = fr.reference_tip_deg(
        {"data_format": "bruker", "p1_us": 10.0}, cal_ladder)
    tip_w, basis_w = fr.reference_tip_deg(
        {"data_format": "bruker", "p1_us": 10.0, "pl1_db": 29.08},
        {"p90_us": 10.0, "p90_power_db_or_w": "0.1 W"})
    tip_bare, basis_bare = fr.reference_tip_deg(
        {"data_format": "bruker", "p1_us": 10.0, "pl1_db": 29.08},
        {"p90_us": 10.0, "p90_power_db_or_w": 20.0,
         "rg_ladder": [{"tip_deg": 1.0, "rg": 1.0}]})
    tip_ag, basis_ag = fr.reference_tip_deg(
        {"data_format": "agilent", "pw_us": 0.05, "tpwr_db": 56.0},
        {"p90_us": 4.5, "p90_power_db_or_w": "10 W"})
    check("p90_power_db: explicit dB read, watts converted on the Bruker "
          "PL scale (0.1 W -> +10 dB), refused for Agilent tpwr, a bare "
          "number refused, a unitless string refused",
          fr.p90_power_db("56 dB (tpwr)", "agilent")[0] == 56.0
          and abs(fr.p90_power_db("0.1 W", "bruker")[0] - 10.0) < 1e-9
          and fr.p90_power_db("10 W", "agilent")[0] is None
          and fr.p90_power_db(20.0, "bruker")[0] is None
          and "bare number" in fr.p90_power_db(20.0, "bruker")[1]
          and fr.p90_power_db("unknown", "bruker")[0] is None
          and fr.p90_power_db(None, "bruker")[0] is None)
    check("reference_tip_deg: Bruker power unresolved -> ladder tip x "
          "P1/P90 (2 deg at P1 = 2 x P90, 1 deg at P1 = P90, said in the "
          "basis); '0.1 W' with PL1 29.08 dB -> 90 x 10^(-(29.08-10)/20) "
          "= 10 deg; a bare 20 falls back to the ladder, never read as "
          "dB; Agilent with a watts p90 power keeps pw/p90 and says ASSUMED",
          abs(tip_2x - 2.0) < 1e-9 and "x P1 20 us / P90 10 us" in basis_2x
          and abs(tip_1x - 1.0) < 1e-9 and "P1 = P90 = 10 us" in basis_1x
          and abs(tip_w - 90.0 * 10 ** (-(29.08 - 10.0) / 20.0)) < 1e-9
          and abs(tip_bare - 1.0) < 1e-9 and "bare number" in basis_bare
          and "ASSUMED" in basis_bare
          and abs(tip_ag - 1.0) < 1e-9 and "ASSUMED" in basis_ag
          and "watts" in basis_ag,
          "%s %s | %s %s | %s %s | %s %s | %s %s" % (
              tip_2x, basis_2x, tip_1x, basis_1x, tip_w, basis_w,
              tip_bare, basis_bare, tip_ag, basis_ag))
    check("reference_pair_check: matched / unmatched / too few",
          fr.reference_pair_check([
              {"expno": 11, "readable": True, "pw_us": 0.05, "tpwr_db": 56},
              {"expno": 13, "readable": True, "pw_us": 0.05, "tpwr_db": 56}]
          )["matched"] is True
          and fr.reference_pair_check([
              {"expno": 11, "readable": True, "pw_us": 0.05, "tpwr_db": 56},
              {"expno": 13, "readable": True, "pw_us": 0.1125,
               "tpwr_db": 56}])["matched"] is False
          and fr.reference_pair_check([
              {"expno": 11, "readable": True, "pw_us": 0.05, "tpwr_db": 56},
              {"expno": 13, "readable": False}]) is None
          and fr.reference_pair_check([
              {"expno": 11, "readable": True},
              {"expno": 13, "readable": True}]) is None)

    # ------------------------------------------------------------------
    # 6. clock-audit refinement degrades gracefully on an Agilent expno
    # ------------------------------------------------------------------
    sb = fr.Bundle(sci_bundle)
    fake_meta = dict(sb.meta)
    fake_meta["clock_audit"] = {
        "workstation_time_source": "test", "ntp_status_raw": "",
        "blocks": [{"expno": noise_expnos[0], "role": "noise",
                    "wall_start_ms": 0, "wall_end_ms": 1000,
                    "ocxo_expected_s": 0.8192},
                   {"expno": 11, "role": "reference_open",
                    "wall_start_ms": 2000, "wall_end_ms": 2300,
                    "ocxo_expected_s": 0.2048}]}
    try:
        clk = fr.analyze_clock_audit(fake_meta, sb)
        blocks = clk.get("blocks") or []
        check("clock-audit refine on Agilent expnos: recorded expectation "
              "kept with a Varian refine_note, no traceback",
              len(blocks) == 2 and all(
                  b_["expected_source"] == "script-recorded"
                  and "Varian" in b_.get("refine_note", "") for b_ in blocks),
              json.dumps(blocks)[:300])
    except Exception as exc:
        check("clock-audit refine on Agilent expnos: no traceback", False,
              repr(exc))

    # ------------------------------------------------------------------
    # 7. real bundles, when given
    # ------------------------------------------------------------------
    for path in args.real_bundle:
        check_real_bundle(os.path.abspath(path), work)

    print("")
    if FAILURES:
        print("AGILENT REPORT TEST: FAIL (%d)" % len(FAILURES))
        return 1
    print("AGILENT REPORT TEST: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
