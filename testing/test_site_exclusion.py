#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_site_exclusion.py -- validate analysis/site_exclusion.py (the per-site
combiner of worst-case axion-coupling exclusions) and its --prior-reports
hook in analysis/facility_report.py.

    python3 testing/test_site_exclusion.py [--out-dir DIR] [--skip-e2e]

  1. synthetic curves on the site's 4 Hz node grid: two hand-built report
     dicts of one facility whose mass bands overlap partially -> the
     combined grid is the union of the two node sets, the combined curve
     is the pointwise minimum, a session's bound is None (+inf) outside
     its own band, the combined best is the smaller session best, the
     within-10x band around the best follows the minimum, and a node no
     session covers under every placement is dropped; a third session
     at a distant carrier adds its own nodes only (no dense fill of the
     gap), a second within-10x segment and a second coverage segment; a
     lone sign-unverified session enters as the pointwise MAXIMUM over its
     two mass placements (the two sign hypotheses of one session); the
     coverage note names the fluctuation regime when a session's
     statistical term reaches 30% of P_90;
  1b. shared axis sign: two sign-unverified sessions of ONE vendor at
     different carriers -> the robust curve is the max over the two
     joint-hypothesis curves, each hypothesis curve a plain min over the
     sessions placed by that sign, and the robust headline is <= (here
     strictly below) what the old independent-sign rule (per-session max,
     then min) gives; all sessions sign-verified -> one hypothesis, the
     plain minimum, no if_sign entries, no sign_note; one verified + one
     unverified session -> the verified curve is identical under both
     hypotheses; two unverified vendors -> 4 labelled hypotheses; a
     report predating line.vendor takes its vendor from
     science.frequency_axis_sign; more than 3 unverified vendors -> the
     independent-sign fallback with a note, and the 4th vendor's arrival
     can LOOSEN the headline (1.0 -> 3.0 here) as the docstring, rule and
     note say; the input order changes nothing in combined on the
     unverified path (sign groups sorted); sign_premise names the shared
     sign as a premise, what breaks it, and quotes the independent-sign
     headline (combined.independent_sign); a lone unverified session
     whose two placements share no node -> combined None, CLI exit 1; no
     note quotes a duration for the tof-shift test; the construction
     string names the D_cal values the sessions actually used;
  2. skipped-report listing: a software-test report (science null), a
     report whose exclusion is unavailable, a report predating the
     exclusion, curves carrying NaN, +inf or zero couplings and an exact
     duplicate bundle are skipped with a reason and never counted;
  3. slug mismatch: reports from two facility_slugs raise SiteMismatch in
     the module (a skipped software-test report from another site
     included) and exit 2 with an ERROR line from the CLI; a report whose
     JSON top level is not an object is refused with exit 2;
  4. idempotence: the CLI run twice on the same inputs writes
     byte-identical .json and .txt, the input order does not change the
     combined curve, and session labels come from the bundle names;
  5. end to end (unless --skip-e2e): two Bruker synthetic physics bundles
     (make_physics_bundle --feature bump --amp 1.5 and --feature none)
     -> facility_report on each -> site_exclusion CLI over both report.json
     files, and facility_report on the second bundle with --prior-reports
     over the first report: the combined best g is <= each session's best,
     site_combined carries n_sessions 2 without per-session columns; a
     re-run with the session's OWN earlier report among the priors keeps
     the new exclusion and skips the prior as the duplicate; a prior from
     another facility_slug and a prior whose JSON is a list are refused
     with exit 2.
  6. real reports (--real-report PATH ...): the shared-sign rule on real
     sessions --- finite JSON, robust >= best conditional, the factor
     field; for siu-carbondale the known numbers (if nominal/mirror
     ~3.17e-4 GeV^-1 at 1.6527016 / 1.6526971 ueV, robust ~1.83e-3, note
     quoting 5.8x); the CLI .txt carries one column per hypothesis.
     --real-bundle ZIP ZIP ...: facility_report on each, the last with
     --prior-reports over the others, and the card shows the robust
     headline, the conditional headlines and the sign note.

Exit 0 iff every check passes.
"""

from __future__ import print_function

import argparse
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

TESTING = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(TESTING, ".."))
SITE = os.path.join(REPO, "analysis", "site_exclusion.py")
REPORT = os.path.join(REPO, "analysis", "facility_report.py")
PHYSICS = os.path.join(REPO, "testing", "make_physics_bundle.py")

EV_PER_HZ = 4.135667696e-15
STEP_HZ = 4.0
K0 = 100000000            # node index of nu_a = 4e8 Hz (m_a ~ 1.654 ueV)

FAILURES = []


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    line = "%s : %s" % (tag, name)
    if detail and not ok:
        line += "\n       %s" % str(detail)[:500]
    print(line)
    if not ok:
        FAILURES.append(name)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def m_ev(k):
    """Mass (eV) of the site-grid node k (nu_a = k x 4 Hz)."""
    return k * STEP_HZ * EV_PER_HZ


def close(a, b, rel=1e-9):
    return a is not None and b is not None and abs(a - b) <= rel * abs(b)


def fake_report(slug, bundle, ks, g, carrier_mhz=400.0, noise_s=1800.0,
                available=True, mirror_shift=None, stat_frac=0.05,
                vendor=None, vendor_in_line=True, d_cal=None):
    """A minimal report.json dict with one worst-case curve at the grid
    nodes ks; mirror_shift (in nodes) makes it sign-unverified with the
    mirror mass axis shifted by that many nodes; vendor is recorded on
    the exclusion's line (or, vendor_in_line False, only under
    science.frequency_axis_sign as reports predating line.vendor have
    it); d_cal = (D_cal, D_cal_pilot) fills calibration_derating."""
    ms = [m_ev(k) for k in ks]
    best = min(range(len(g)), key=lambda i: g[i])
    verified = mirror_shift is None
    band = [ms[0] * 1e6, ms[-1] * 1e6]
    ex = {"available": available,
          "line": {"carrier_mhz": carrier_mhz, "sign_verified": verified},
          "signal_power": {"noise_seconds": noise_s, "n_rows": 60,
                           "statistical_fraction_of_P90": stat_frac},
          "result": {"g90_worst_best_gev_inv": g[best],
                     "m_a_at_best_uev": ms[best] * 1e6,
                     "offset_at_best_hz": 0.0,
                     "band_10x_uev": {"nominal": band}},
          "curve": {"m_a_ev": ms, "g90_worst": list(g),
                    "g90_nominal": [v / 3.0 for v in g]}}
    if not verified:
        mm = [m_ev(k + mirror_shift) for k in ks]
        ex["curve"]["m_a_ev_mirror"] = mm
        ex["result"]["band_10x_uev"].update(
            {"mirror": [mm[0] * 1e6, mm[-1] * 1e6],
             "intersection": [max(band[0], mm[0] * 1e6),
                              min(band[1], mm[-1] * 1e6)]})
    if vendor is not None and vendor_in_line:
        ex["line"]["vendor"] = vendor
    if d_cal is not None:
        ex["calibration_derating"] = {"D_cal": d_cal[0],
                                      "D_cal_pilot": d_cal[1]}
    if not available:
        ex = {"available": False, "reason": "synthetic unavailable"}
    rep = {"report_version": "test", "generated_utc": "2026-09-15T00:00:00Z",
           "bundle": bundle, "bundle_sha256": "sha_" + bundle,
           "facility_slug": slug, "report_type": "science",
           "science": {"axion_exclusion": ex}}
    if vendor is not None and not vendor_in_line:
        rep["science"]["frequency_axis_sign"] = {"vendor": vendor,
                                                 "verified": verified}
    return rep


def placed(ks, g, shift=0):
    """{node: g} of a curve with its nodes shifted by shift (a mirror
    placement)."""
    return {k + shift: v for k, v in zip(ks, g)}


def min_over(dicts):
    """The min rule: nodes ANY dict covers."""
    out = {}
    for d in dicts:
        for k, v in d.items():
            out[k] = min(out.get(k, float("inf")), v)
    return out


def max_over(dicts):
    """The robust rule: nodes EVERY dict covers."""
    keys = set.intersection(*[set(d) for d in dicts])
    return {k: max(d[k] for d in dicts) for k in keys}


def node_of(m_ev_value):
    return int(round(m_ev_value / EV_PER_HZ / STEP_HZ))


def matches(nodes, values, want):
    """values (None = no statement) over nodes equal the dict want."""
    return (sorted(k for k, v in zip(nodes, values) if v is not None)
            == sorted(want)
            and all(v is None or close(v, want[k])
                    for k, v in zip(nodes, values)))


def write_json(path, obj):
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=1)
    return path


def run_cli(args):
    p = subprocess.run([sys.executable, SITE] + list(args),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode(), p.stderr.decode()


def run_report(bundle, out_dir, extra=()):
    p = subprocess.run([sys.executable, REPORT, bundle, "--out", out_dir]
                       + list(extra), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    report = None
    rp = os.path.join(out_dir, "report.json")
    if p.returncode == 0 and os.path.isfile(rp):
        with open(rp) as fh:
            report = json.load(fh)
    return p.returncode, report, p.stdout.decode() + p.stderr.decode()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--skip-e2e", action="store_true")
    ap.add_argument("--real-report", nargs="+", default=None, metavar="PATH",
                    help="report.json files of REAL sessions of one "
                         "facility: combine them and check the shared-sign "
                         "rule (siu-carbondale: the known numbers)")
    ap.add_argument("--real-bundle", nargs="+", default=None, metavar="ZIP",
                    help="two or more real bundles of one facility: "
                         "facility_report on each, the last with "
                         "--prior-reports over the others, card checked")
    args = ap.parse_args(argv)
    work = args.out_dir or tempfile.mkdtemp(prefix="site_exclusion_test_")
    if not os.path.isdir(work):
        os.makedirs(work)
    se = load_module("snn_site_exclusion_test", SITE)

    # ------------------------------------------------------------------
    # 1. synthetic curves: union of node sets, min rule, None outside
    # ------------------------------------------------------------------
    ks_a = [K0 + i for i in range(6)]            # nodes 0..5
    g_a = [8.0, 6.0, 4.0, 3.0, 4.0, 6.0]
    ks_b = [K0 + 3 + i for i in range(7)]        # nodes 3..9
    g_b = [5.0, 2.0, 1.0, 2.0, 5.0, 20.0, 50.0]
    rep_a = fake_report("site-x", "a.zip", ks_a, g_a, noise_s=1800.0)
    rep_b = fake_report("site-x", "b.zip", ks_b, g_b, noise_s=3600.0)
    site = se.combine_reports([("/w/rep_a/report.json", rep_a),
                               ("/w/rep_b/report.json", rep_b)])
    grid = site["curve"]["m_a_ev"]
    check("union node grid of the two sessions (10 nodes 0..9, sorted)",
          len(grid) == 10 and all(close(v, m_ev(K0 + i))
                                  for i, v in enumerate(grid)), str(grid))
    want_min = [8.0, 6.0, 4.0, 3.0, 2.0, 1.0, 2.0, 5.0, 20.0, 50.0]
    got = site["curve"]["g_site"]
    check("combined curve is the pointwise minimum over sessions",
          len(got) == 10 and all(close(x, y) for x, y in zip(got, want_min)),
          str(got))
    col_a, col_b = site["curve"]["per_session"]
    check("a session's column is None (+inf) outside its own band and its "
          "own curve inside",
          col_a[6:] == [None] * 4
          and all(close(x, y) for x, y in zip(col_a[:6], g_a))
          and col_b[:3] == [None] * 3
          and all(close(x, y) for x, y in zip(col_b[3:], g_b)),
          "a=%s b=%s" % (col_a, col_b))
    comb = site["combined"]
    check("combined best g = 1.0 at node 5, set by session 'b' (label from "
          "the bundle name), n_sessions 2, total noise 5400 s",
          site["n_sessions"] == 2 and comb["g90_worst_best_gev_inv"] == 1.0
          and close(comb["m_a_at_best_uev"], m_ev(K0 + 5) * 1e6)
          and comb["session_setting_best"] == "b"
          and [s["label"] for s in site["sessions"]] == ["a", "b"]
          and abs(comb["total_noise_seconds"] - 5400.0) < 1e-9,
          json.dumps(comb)[:400])
    check("combined within-10x band around the best spans nodes 0..7 "
          "(g < 10), one segment, one coverage segment",
          close(comb["band_10x_uev"][0], m_ev(K0) * 1e6)
          and close(comb["band_10x_uev"][1], m_ev(K0 + 7) * 1e6)
          and len(comb["band_10x_segments_uev"]) == 1
          and len(comb["mass_coverage_segments_uev"]) == 1,
          json.dumps({k: comb[k] for k in ("band_10x_uev",
                                           "band_10x_segments_uev",
                                           "mass_coverage_segments_uev")}))
    check("combined best <= every session's best (min rule)",
          all(comb["g90_worst_best_gev_inv"] <= s["g90_worst_best_gev_inv"]
              for s in site["sessions"]))
    check("all sessions sign-verified -> one sign hypothesis: g_site is the "
          "plain minimum (above), if_sign empty, no sign_note, no "
          "per-hypothesis curve, robust block = headline, rule says so",
          comb["if_sign"] == {} and comb["sign_hypotheses"] == []
          and "sign_note" not in comb
          and comb["sign_unverified_sessions"] == []
          and comb["robust"]["g90_worst_best_gev_inv"]
          == comb["g90_worst_best_gev_inv"]
          and comb["robust"]["hypothesis_setting_best"] is None
          and site["curve"]["if_sign"] is None
          and "plain minimum" in site["rule"]
          and "plain min" in comb["sign_rule"],
          json.dumps({k: comb.get(k) for k in ("if_sign", "sign_hypotheses",
                                               "sign_rule", "robust")}))
    check("mass coverage is the union's extent, slug carried, grid step 4 Hz",
          close(comb["mass_coverage_ev"][0], m_ev(K0))
          and close(comb["mass_coverage_ev"][1], m_ev(K0 + 9))
          and site["facility_slug"] == "site-x"
          and site["curve"]["grid_step_hz"] == 4.0)
    check("coverage note: both sessions line-power dominated (5%) -> "
          "'joint worst-case bound', max fraction recorded",
          "joint worst-case bound" in comb["coverage_note"]
          and close(comb["statistical_fraction_of_P90_max"], 0.05),
          comb["coverage_note"])
    check("docstring states the min rule, the coverage caveat (0.9^N) and "
          "the update mechanism, and calls the numbers unpublished",
          "min" in se.__doc__ and "0.9^N" in se.__doc__
          and "--prior-reports" in se.__doc__ and "unpublished" in se.__doc__
          and "publication" not in se.__doc__)

    # a single session combines to itself (monotone: adding b only tightens)
    solo = se.combine_reports([("/w/rep_a/report.json", rep_a)])
    check("one session alone reproduces its own curve and best",
          all(close(x, y) for x, y in zip(solo["curve"]["g_site"], g_a))
          and solo["combined"]["g90_worst_best_gev_inv"] == 3.0
          and all(a >= b for a, b in zip(solo["curve"]["g_site"],
                                         site["curve"]["g_site"][:6])),
          str(solo["curve"]["g_site"]))

    # a third session at a distant carrier: its own nodes only, a second
    # within-10x segment, band_10x_uev stays the segment around the best
    ks_c = [K0 + 20 + i for i in range(6)]
    g_c = [3.0, 2.0, 1.5, 2.0, 3.0, 4.0]
    rep_c = fake_report("site-x", "c.zip", ks_c, g_c, carrier_mhz=400.0001)
    site3 = se.combine_reports([("/w/rep_a/report.json", rep_a),
                                ("/w/rep_b/report.json", rep_b),
                                ("/w/rep_c/report.json", rep_c)])
    c3 = site3["combined"]
    check("distant third session: 16 nodes (no fill of the 10-node gap), "
          "two within-10x segments, two coverage segments, band_10x_uev "
          "the segment around the best only",
          len(site3["curve"]["m_a_ev"]) == 16
          and len(c3["band_10x_segments_uev"]) == 2
          and len(c3["mass_coverage_segments_uev"]) == 2
          and close(c3["band_10x_uev"][0], m_ev(K0) * 1e6)
          and close(c3["band_10x_uev"][1], m_ev(K0 + 7) * 1e6)
          and close(c3["band_10x_segments_uev"][1][0], m_ev(K0 + 20) * 1e6)
          and close(c3["band_10x_segments_uev"][1][1], m_ev(K0 + 25) * 1e6)
          and c3["g90_worst_best_gev_inv"] == 1.0
          and c3["session_setting_best"] == "b",
          json.dumps(c3)[:500])

    # a sign-unverified session: max over its two placements
    ks_u = [K0 + i for i in range(6)]
    g_u = [8.0, 6.0, 4.0, 3.0, 4.0, 6.0]
    rep_u = fake_report("site-x", "u.zip", ks_u, g_u, mirror_shift=-2)
    siteu = se.combine_reports([("/w/rep_u/report.json", rep_u)])
    gu = siteu["curve"]["g_site"]
    # nominal covers nodes 0..5, mirror -2..3: only 0..3 carry a statement
    want_u = [max(g_u[0], g_u[2]), max(g_u[1], g_u[3]),
              max(g_u[2], g_u[4]), max(g_u[3], g_u[5])]
    cu = siteu["combined"]
    check("a lone sign-unverified session enters as the pointwise MAXIMUM "
          "over its two placements (= max over its two sign hypotheses), "
          "nodes covered by one placement only dropped",
          len(gu) == 4 and all(close(x, y) for x, y in zip(gu, want_u))
          and all(close(v, m_ev(K0 + i))
                  for i, v in enumerate(siteu["curve"]["m_a_ev"]))
          and cu["g90_worst_best_gev_inv"] == 4.0
          and cu["sign_unverified_sessions"] == ["u"]
          and siteu["sessions"][0]["band_10x_basis"].startswith(
              "intersection"),
          "%s vs %s" % (gu, want_u))
    check("lone unverified session: if_sign nominal best 3.0 at node 3, "
          "mirror best 3.0 at node 1, robust 4.0 -> note says '1.3x "
          "weaker than the tighter', names the tof-shift test and checklist "
          "item 2 without a duration; an unrecorded vendor forms its own "
          "sign group whose key stays out of the prose ('vendor "
          "unrecorded, session u', 'that session's console')",
          cu["sign_hypotheses"] == ["nominal", "mirror"]
          and cu["if_sign"]["nominal"]["g90_worst_best_gev_inv"] == 3.0
          and close(cu["if_sign"]["nominal"]["m_a_at_best_uev"],
                    m_ev(K0 + 3) * 1e6)
          and cu["if_sign"]["mirror"]["g90_worst_best_gev_inv"] == 3.0
          and close(cu["if_sign"]["mirror"]["m_a_at_best_uev"],
                    m_ev(K0 + 1) * 1e6)
          and close(cu["robust_over_conditional_factor"], 4.0 / 3.0)
          and "1.3x weaker than the tighter" in cu["sign_note"]
          and "tof-shift" in cu["sign_note"]
          and "vendor checklist item 2" in cu["sign_note"]
          and "one-minute" not in cu["sign_note"]
          and list(cu["sign_groups"]) == ["unrecorded-vendor:u"]
          and "unrecorded-vendor" not in cu["sign_note"]
          and "vendor unrecorded, session u" in cu["sign_note"]
          and "that session's console" in cu["sign_note"]
          and cu["robust"]["hypothesis_setting_best"] in ("nominal",
                                                          "mirror"),
          json.dumps({k: cu.get(k) for k in ("if_sign", "sign_note",
                                             "sign_groups")})[:600])
    check("lone unverified session: sign_premise names the premise and "
          "what breaks it; independent_sign = its own max over placements "
          "(4.0, the same as the robust headline, and the premise says so)",
          "premise" in cu["sign_premise"]
          and "one console" in cu["sign_premise"]
          and "breaks it" in cu["sign_premise"]
          and cu["independent_sign"]["g90_worst_best_gev_inv"] == 4.0
          and cu["independent_sign"]["session_setting_best"] == "u"
          and "the same as the sign-robust headline" in cu["sign_premise"]
          and "band_10x_segments_uev" not in cu["independent_sign"],
          cu.get("sign_premise"))

    # a lone unverified session whose two placements share no node
    rep_w = fake_report("site-x", "w.zip", ks_u, g_u, mirror_shift=-50)
    sitew = se.combine_reports([("/w/rep_w/report.json", rep_w)])
    check("lone unverified session with placements 50 nodes apart -> no "
          "node under every sign hypothesis: combined None, curve None, "
          "skipped 'all' says 'under every sign hypothesis', rule kept",
          sitew["combined"] is None and sitew["curve"] is None
          and sitew["n_sessions"] == 1
          and sitew["skipped"][-1]["report"] == "all"
          and "under every sign hypothesis" in sitew["skipped"][-1]["why"]
          and "ONE shared unknown" in sitew["rule"],
          json.dumps(sitew["skipped"]))
    d_w = os.path.join(work, "rep_w")
    if not os.path.isdir(d_w):
        os.makedirs(d_w)
    write_json(os.path.join(d_w, "report.json"), rep_w)
    rc_w, o_w, e_w = run_cli([d_w, "--out", os.path.join(work, "w_out")])
    check("CLI on that session exits 1 ('no usable exclusion'), writes the "
          "JSON, no traceback",
          rc_w == 1 and "no usable exclusion" in o_w
          and "Traceback" not in e_w
          and os.path.isfile(os.path.join(work, "w_out",
                                          "site_exclusion_site-x.json")),
          "rc=%s out=%s err=%s" % (rc_w, o_w[:200], e_w[:200]))
    check("no Infinity or NaN reaches the JSON (None outside a session's "
          "band)",
          "Infinity" not in json.dumps(siteu)
          and "NaN" not in json.dumps(siteu)
          and "Infinity" not in json.dumps(site3))

    # ------------------------------------------------------------------
    # 1b. the axis sign is ONE shared unknown per vendor
    # ------------------------------------------------------------------
    # two agilent sessions at different carriers, built so that at node 2
    # each is strong under one sign and weak under the other: the shared
    # sign rule (max over hypotheses of the min) beats the independent
    # rule (min over sessions of each one's max) there
    ks_p = [K0 + i for i in range(6)]
    g_p = [8.0, 6.0, 9.0, 3.0, 1.0, 6.0]
    ks_q = [K0 + 2 + i for i in range(6)]
    g_q = [1.0, 2.0, 9.0, 3.0, 5.0, 20.0]
    rep_p = fake_report("site-x", "p.zip", ks_p, g_p, mirror_shift=-2,
                        vendor="agilent")
    rep_q = fake_report("site-x", "q.zip", ks_q, g_q, mirror_shift=-2,
                        vendor="agilent", carrier_mhz=400.0001)
    site_pq = se.combine_reports([("/w/rep_p/report.json", rep_p),
                                  ("/w/rep_q/report.json", rep_q)])
    cpq = site_pq["combined"]
    nom = min_over([placed(ks_p, g_p), placed(ks_q, g_q)])
    mir = min_over([placed(ks_p, g_p, -2), placed(ks_q, g_q, -2)])
    robust = max_over([nom, mir])
    old_rule = min_over([max_over([placed(ks_p, g_p), placed(ks_p, g_p, -2)]),
                         max_over([placed(ks_q, g_q), placed(ks_q, g_q, -2)])])
    nodes_pq = [node_of(m) for m in site_pq["curve"]["m_a_ev"]]
    check("shared sign, two unverified agilent sessions at different "
          "carriers: robust curve = max over the two hypothesis curves on "
          "the nodes both cover (0..5)",
          nodes_pq == list(range(K0, K0 + 6))
          and matches(nodes_pq, site_pq["curve"]["g_site"], robust),
          "%s vs %s" % (site_pq["curve"]["g_site"],
                        [robust[k] for k in sorted(robust)]))
    ifc = site_pq["curve"]["if_sign"]
    if_nodes = [node_of(m) for m in ifc["m_a_ev"]]
    check("each hypothesis curve is the plain min over the sessions placed "
          "by that sign, on every node some hypothesis covers (-2..7, None "
          "where that hypothesis makes no statement)",
          if_nodes == list(range(K0 - 2, K0 + 8))
          and matches(if_nodes, ifc["g"]["nominal"], nom)
          and matches(if_nodes, ifc["g"]["mirror"], mir),
          json.dumps(ifc["g"]))
    check("robust headline 1.0 at node 2 <= the old independent-sign rule's "
          "%.1f (computed inline; strictly tighter here), conditional "
          "headlines 1.0 under both signs, factor 1 -> note says "
          "'matches', groups {agilent: [p, q]}, rule 'shared'"
          % min(old_rule.values()),
          cpq["g90_worst_best_gev_inv"] == 1.0
          and close(cpq["m_a_at_best_uev"], m_ev(K0 + 2) * 1e6)
          and min(old_rule.values()) == 3.0
          and cpq["g90_worst_best_gev_inv"] <= min(old_rule.values())
          and cpq["sign_hypotheses"] == ["nominal", "mirror"]
          and cpq["if_sign"]["nominal"]["g90_worst_best_gev_inv"] == 1.0
          and cpq["if_sign"]["mirror"]["g90_worst_best_gev_inv"] == 1.0
          and cpq["if_sign"]["nominal"]["assignment"] == {"agilent": "nominal"}
          and close(cpq["robust_over_conditional_factor"], 1.0)
          and "matches" in cpq["sign_note"]
          and cpq["sign_groups"] == {"agilent": ["p", "q"]}
          and cpq["sign_unverified_sessions"] == ["p", "q"]
          and "shared" in cpq["sign_rule"]
          and "ONE shared unknown per vendor" in site_pq["rule"]
          and "min over sessions" in site_pq["rule"],
          json.dumps(cpq)[:600])
    check("the shared sign is named as a premise: rule says 'premise' and "
          "'under that premise it holds whichever sign is true'; "
          "sign_premise says what breaks it and quotes the independent-sign "
          "headline 3.0 (= the old rule, '3.0x weaker than the sign-robust "
          "headline'); no note quotes a duration for the tof-shift test",
          "premise" in site_pq["rule"]
          and "under that premise it holds whichever sign is true"
          in site_pq["rule"]
          and "a premise the data do not check" in cpq["sign_premise"]
          and "second console" in cpq["sign_premise"]
          and "tighter than the truth" in cpq["sign_premise"]
          and cpq["independent_sign"]["g90_worst_best_gev_inv"]
          == min(old_rule.values()) == 3.0
          and close(cpq["independent_sign"]["m_a_at_best_uev"],
                    m_ev(K0 + 3) * 1e6)
          and "3.0x weaker than the sign-robust headline"
          in cpq["sign_premise"]
          and "one-minute" not in cpq["sign_note"]
          and "one-minute" not in cpq["sign_premise"]
          and "one-minute" not in se.__doc__,
          json.dumps({"rule": site_pq["rule"],
                      "premise": cpq["sign_premise"]})[:900])
    site_qp = se.combine_reports([("/w/rep_q/report.json", rep_q),
                                  ("/w/rep_p/report.json", rep_p)])
    check("unverified path: the input order changes nothing in combined "
          "(sign groups and unverified list sorted) nor in the curves",
          site_qp["combined"] == cpq
          and site_qp["curve"]["g_site"] == site_pq["curve"]["g_site"]
          and site_qp["curve"]["if_sign"] == site_pq["curve"]["if_sign"]
          and site_qp["curve"]["per_session"]
          == site_pq["curve"]["per_session"][::-1],
          json.dumps([k for k in cpq if site_qp["combined"].get(k) != cpq[k]]))
    own_p = max_over([placed(ks_p, g_p), placed(ks_p, g_p, -2)])
    own_q = max_over([placed(ks_q, g_q), placed(ks_q, g_q, -2)])
    check("per-session columns stay each session's own sign-robust column "
          "(max over its placements) on the robust grid",
          matches(nodes_pq, site_pq["curve"]["per_session"][0], own_p)
          and matches(nodes_pq, site_pq["curve"]["per_session"][1], own_q),
          json.dumps(site_pq["curve"]["per_session"]))
    check("adding the second session never loosened the first alone "
          "(monotone under the shared-sign rule)",
          all(site_pq["curve"]["g_site"][nodes_pq.index(k)] <= v + 1e-12
              for k, v in own_p.items() if k in nodes_pq))

    # one verified + one unverified session
    ks_v = [K0 + 20 + i for i in range(6)]
    g_v = [3.0, 2.0, 1.5, 2.0, 3.0, 4.0]
    rep_v = fake_report("site-x", "v.zip", ks_v, g_v, mirror_shift=-2,
                        vendor="agilent", carrier_mhz=400.0001)
    site_av = se.combine_reports([("/w/rep_a/report.json", rep_a),
                                  ("/w/rep_v/report.json", rep_v)])
    cav = site_av["combined"]
    ifa = site_av["curve"]["if_sign"]
    nodes_av = [node_of(m) for m in ifa["m_a_ev"]]
    on_a = [j for j, k in enumerate(nodes_av) if K0 <= k <= K0 + 5]
    on_v = [j for j, k in enumerate(nodes_av) if k >= K0 + 18]
    check("one verified + one unverified session: the verified session's "
          "curve is identical under both hypotheses (and equals the robust "
          "curve there); the hypotheses differ only on the unverified "
          "session's nodes",
          len(on_a) == 6
          and all(close(ifa["g"]["nominal"][j], g_a[nodes_av[j] - K0])
                  and close(ifa["g"]["mirror"][j], g_a[nodes_av[j] - K0])
                  for j in on_a)
          and any(ifa["g"]["nominal"][j] != ifa["g"]["mirror"][j]
                  for j in on_v)
          and all(close(site_av["curve"]["g_site"][i], g_a[i])
                  for i in range(6))
          and cav["sign_groups"] == {"agilent": ["v"]}
          and cav["sign_unverified_sessions"] == ["v"]
          and cav["g90_worst_best_gev_inv"] == 2.0
          and cav["session_setting_best"] == "v"
          and cav["if_sign"]["nominal"]["g90_worst_best_gev_inv"] == 1.5
          and cav["if_sign"]["mirror"]["g90_worst_best_gev_inv"] == 1.5
          and "1.3x weaker than the tighter sign-conditional headline"
          in cav["sign_note"]
          and "1 session(s) are taken to share ONE" in cav["sign_note"]
          and "(vendor agilent)" in cav["sign_note"],
          json.dumps(ifa["g"])[:400] + json.dumps(cav)[:400])

    # two unverified vendors: 4 labelled joint hypotheses
    rep_j = fake_report("site-x", "j.zip", ks_q, g_q, mirror_shift=-2,
                        vendor="jeol")
    site_pj = se.combine_reports([("/w/rep_p/report.json", rep_p),
                                  ("/w/rep_j/report.json", rep_j)])
    cpj = site_pj["combined"]
    hyp4 = {}
    for sp in ("nominal", "mirror"):
        for sj in ("nominal", "mirror"):
            hyp4["agilent %s, jeol %s" % (sp, sj)] = min_over(
                [placed(ks_p, g_p, 0 if sp == "nominal" else -2),
                 placed(ks_q, g_q, 0 if sj == "nominal" else -2)])
    robust4 = max_over(list(hyp4.values()))
    nodes_pj = [node_of(m) for m in site_pj["curve"]["m_a_ev"]]
    if_nodes4 = [node_of(m) for m in site_pj["curve"]["if_sign"]["m_a_ev"]]
    check("two sign-unverified vendors -> 4 joint hypotheses labelled "
          "'agilent <sign>, jeol <sign>', each the plain min under its "
          "assignment, robust = max over the four, note says 'per vendor'",
          cpj["sign_hypotheses"] == ["agilent nominal, jeol nominal",
                                     "agilent nominal, jeol mirror",
                                     "agilent mirror, jeol nominal",
                                     "agilent mirror, jeol mirror"]
          and matches(nodes_pj, site_pj["curve"]["g_site"], robust4)
          and all(matches(if_nodes4, site_pj["curve"]["if_sign"]["g"][l],
                          hyp4[l]) for l in hyp4)
          and all(cpj["if_sign"][l]["g90_worst_best_gev_inv"]
                  == min(hyp4[l].values()) for l in hyp4)
          and set(cpj["sign_groups"]) == {"agilent", "jeol"}
          and "per vendor" in cpj["sign_note"]
          and "each such vendor" in cpj["sign_note"]
          and "4 joint hypotheses" in cpj["sign_rule"],
          json.dumps(cpj)[:600])

    # a report predating line.vendor: vendor from science.frequency_axis_sign
    rep_o = fake_report("site-x", "o.zip", ks_q, g_q, mirror_shift=-2,
                        vendor="agilent", vendor_in_line=False)
    site_po = se.combine_reports([("/w/rep_p/report.json", rep_p),
                                  ("/w/rep_o/report.json", rep_o)])
    check("a report predating line.vendor takes its vendor from "
          "science.frequency_axis_sign and shares the agilent sign (2 "
          "hypotheses, not 4)",
          site_po["sessions"][1]["vendor"] == "agilent"
          and site_po["combined"]["sign_hypotheses"] == ["nominal", "mirror"]
          and site_po["combined"]["sign_groups"] == {"agilent": ["o", "p"]}
          and site_po["curve"]["g_site"] == site_pq["curve"]["g_site"],
          json.dumps(site_po["combined"]["sign_groups"]))

    # more than 3 unverified vendors: the independent-sign fallback
    reps4 = [("/w/f%d/report.json" % i,
              fake_report("site-x", "f%d.zip" % i, ks_p, g_p, mirror_shift=-2,
                          vendor="v%d" % i)) for i in range(4)]
    site4 = se.combine_reports(reps4)
    c4 = site4["combined"]
    check("4 sign-unverified vendors -> independent-sign fallback: g_site = "
          "min over sessions of each one's max over placements, if_sign "
          "empty, rule and note say 'independent' / 'MAXIMUM' / 'fallback'",
          matches([node_of(m) for m in site4["curve"]["m_a_ev"]],
                  site4["curve"]["g_site"], own_p)
          and c4["if_sign"] == {} and c4["sign_hypotheses"] == []
          and site4["curve"]["if_sign"] is None
          and "independent" in c4["sign_rule"]
          and "MAXIMUM" in c4["sign_note"]
          and "one-minute" not in c4["sign_note"]
          and "sign_premise" not in c4
          and "fallback" in site4["rule"]
          and len(c4["sign_groups"]) == 4,
          json.dumps(c4)[:500])

    # the fallback boundary: the 4th sign-unverified vendor can LOOSEN the
    # site curve (shared rule 1.0 at node 2 -> independent rule 9.0 there,
    # headline 1.0 -> 3.0), the one exception to monotone tightening
    far = [("/w/far%d/report.json" % i,
            fake_report("site-x", "far%d.zip" % i,
                        [K0 + 100 * (i + 1) + j for j in range(6)], [70.0] * 6,
                        mirror_shift=-2, vendor="far%d" % i,
                        carrier_mhz=400.001 * (i + 1)))
           for i in range(3)]
    site_k3 = se.combine_reports([("/w/rep_p/report.json", rep_p),
                                  ("/w/rep_q/report.json", rep_q)] + far[:2])
    site_k4 = se.combine_reports([("/w/rep_p/report.json", rep_p),
                                  ("/w/rep_q/report.json", rep_q)] + far)
    g_at = lambda s, k: s["curve"]["g_site"][
        [node_of(m) for m in s["curve"]["m_a_ev"]].index(k)]
    check("fallback boundary: with 3 sign-unverified vendors the shared rule "
          "holds (headline 1.0, node 2 = 1.0); a 4th far-away vendor "
          "switches to the independent-sign fallback and LOOSENS the curve "
          "(headline 3.0, node 2 = 9.0) --- docstring qualifies its "
          "monotonicity claim with MAX_SIGN_VENDORS, rule and note say the "
          "4th vendor 'may have loosened' the curve",
          len(site_k3["combined"]["sign_hypotheses"]) == 8
          and site_k3["combined"]["g90_worst_best_gev_inv"] == 1.0
          and g_at(site_k3, K0 + 2) == 1.0
          and site_k4["combined"]["sign_hypotheses"] == []
          and site_k4["combined"]["g90_worst_best_gev_inv"] == 3.0
          and g_at(site_k4, K0 + 2) == 9.0
          and "MAX_SIGN_VENDORS" in se.__doc__
          and "can loosen" in se.__doc__
          and "may have loosened" in site_k4["rule"]
          and "may have loosened" in site_k4["combined"]["sign_note"]
          and "4th vendor" in site_k4["rule"],
          json.dumps({"k3": site_k3["combined"]["g90_worst_best_gev_inv"],
                      "k4": site_k4["combined"]["g90_worst_best_gev_inv"],
                      "rule4": site_k4["rule"]})[:600])

    # the construction string names the D_cal the sessions actually used
    rep_d1 = fake_report("site-x", "d1.zip", ks_a, g_a, d_cal=(4.6, 4.6))
    rep_d2 = fake_report("site-x", "d2.zip", ks_b, g_b, d_cal=(4.8, 4.6))
    site_d = se.combine_reports([("/w/d1/report.json", rep_d1),
                                 ("/w/d2/report.json", rep_d2)])
    site_d1 = se.combine_reports([("/w/d1/report.json", rep_d1)])
    check("construction string: 'D_cal 4.60 to 4.80 by session' naming the "
          "pilot's 4.6 envelope and the ladder inflation; 'D_cal 4.60 in "
          "every session' for one value; 'unrecorded' without the field",
          "D_cal 4.60 to 4.80 by session" in site_d["construction"]
          and "pilot's 4.6 envelope" in site_d["construction"]
          and "inflated" in site_d["construction"]
          and "D_cal 4.60 in every session" in site_d1["construction"]
          and "inflated" not in site_d1["construction"]
          and "D_cal unrecorded" in site["construction"]
          and [s["D_cal"] for s in site_d["sessions"]] == [4.6, 4.8],
          site_d["construction"][:300])
    check("no Infinity or NaN in any shared-sign JSON",
          all("Infinity" not in json.dumps(x) and "NaN" not in json.dumps(x)
              for x in (site_pq, site_av, site_pj, site_po, site4)))

    # a fluctuation-driven session flips the coverage note
    rep_f = fake_report("site-x", "f.zip", ks_b, g_b, stat_frac=0.36)
    sitef = se.combine_reports([("/w/rep_a/report.json", rep_a),
                                ("/w/rep_f/report.json", rep_f)])
    check("statistical term 36% in the session setting the best -> coverage "
          "note names the luckiest-fluctuation regime and 0.9^N",
          "luckiest" in sitef["combined"]["coverage_note"]
          and "0.9^N" in sitef["combined"]["coverage_note"]
          and close(sitef["combined"]["statistical_fraction_of_P90_max"], 0.36)
          and close(sitef["combined"]["statistical_fraction_of_P90_at_best"],
                    0.36),
          sitef["combined"]["coverage_note"])
    check("rule and construction strings state the min rule and its "
          "coverage limit, never 'published'",
          "min over sessions" in sitef["rule"]
          and "0.9^N" in sitef["construction"]
          and "unpublished" in sitef["construction"]
          and "publication" not in sitef["construction"])

    # ------------------------------------------------------------------
    # 2. skipped reports are listed, never counted
    # ------------------------------------------------------------------
    sw_test = {"report_version": "test", "bundle": "desk.zip",
               "bundle_sha256": "sha_desk", "facility_slug": "site-x",
               "report_type": "software-test", "science": None}
    unavail = fake_report("site-x", "un.zip", ks_a, g_a, available=False)
    old = copy.deepcopy(rep_a)
    old["bundle"], old["bundle_sha256"] = "old.zip", "sha_old"
    del old["science"]["axion_exclusion"]
    dup = copy.deepcopy(rep_b)
    nan_rep = fake_report("site-x", "nan.zip", ks_a,
                          [4.0, float("nan"), 2.0, 3.0, 4.0, 5.0])
    inf_rep = fake_report("site-x", "inf.zip", ks_a, [float("inf")] * 6)
    zero_rep = fake_report("site-x", "zero.zip", ks_a, [0.0] * 6)
    site2 = se.combine_reports([("/w/a/report.json", rep_a),
                                ("/w/desk/report.json", sw_test),
                                ("/w/un/report.json", unavail),
                                ("/w/old/report.json", old),
                                ("/w/nan/report.json", nan_rep),
                                ("/w/inf/report.json", inf_rep),
                                ("/w/zero/report.json", zero_rep),
                                ("/w/b/report.json", rep_b),
                                ("/w/dup/report.json", dup)])
    whys = {os.path.basename(os.path.dirname(s["report"])): s["why"]
            for s in site2["skipped"]}
    check("software-test, unavailable, pre-exclusion, NaN, +inf, zero and "
          "duplicate reports skipped with reasons; 2 sessions remain and "
          "the curve is unchanged",
          set(whys) == {"desk", "un", "old", "nan", "inf", "zero", "dup"}
          and "software-test" in whys["desk"]
          and "unavailable" in whys["un"]
          and "no science.axion_exclusion" in whys["old"]
          and all("non-finite or non-positive" in whys[k]
                  for k in ("nan", "inf", "zero"))
          and "1 of 6" in whys["nan"]
          and "duplicate" in whys["dup"]
          and site2["n_sessions"] == 2
          and site2["curve"]["g_site"] == site["curve"]["g_site"],
          json.dumps(whys))
    empty = se.combine_reports([("/w/desk/report.json", sw_test)])
    check("no usable report -> combined None, curve None, one skipped, and "
          "the rule says 'no usable session' rather than claiming every "
          "session's sign is verified",
          empty["combined"] is None and empty["curve"] is None
          and empty["n_sessions"] == 0 and len(empty["skipped"]) == 1
          and "no usable session" in empty["rule"]
          and "verified" not in empty["rule"], empty["rule"])
    only_bad = se.combine_reports([("/w/inf/report.json", inf_rep),
                                   ("/w/zero/report.json", zero_rep)])
    check("only unusable curves -> combined None without an exception",
          only_bad["combined"] is None and only_bad["n_sessions"] == 0
          and len(only_bad["skipped"]) == 2)

    # ------------------------------------------------------------------
    # 3. slug mismatch, non-object JSON
    # ------------------------------------------------------------------
    rep_y = fake_report("site-y", "y.zip", ks_b, g_b)
    try:
        se.combine_reports([("/w/a/report.json", rep_a),
                            ("/w/y/report.json", rep_y)])
        check("two facility_slugs raise SiteMismatch", False, "no exception")
    except se.SiteMismatch as exc:
        check("two facility_slugs raise SiteMismatch naming both",
              "site-x" in str(exc) and "site-y" in str(exc), str(exc))
    sw_other = dict(sw_test, facility_slug="site-z")
    try:
        se.combine_reports([("/w/a/report.json", rep_a),
                            ("/w/deskz/report.json", sw_other)])
        check("a skipped report from another facility still raises "
              "SiteMismatch", False, "no exception")
    except se.SiteMismatch as exc:
        check("a skipped (software-test) report from another facility still "
              "raises SiteMismatch", "site-z" in str(exc), str(exc))
    d_a = os.path.join(work, "rep_a")
    d_b = os.path.join(work, "rep_b")
    d_y = os.path.join(work, "rep_y")
    for d in (d_a, d_b, d_y):
        if not os.path.isdir(d):
            os.makedirs(d)
    write_json(os.path.join(d_a, "report.json"), rep_a)
    write_json(os.path.join(d_b, "report.json"), rep_b)
    write_json(os.path.join(d_y, "report.json"), rep_y)
    rc, out, err = run_cli([d_a, d_y, "--out", os.path.join(work, "mm")])
    check("CLI: slug mismatch exits 2 with an ERROR line and writes nothing",
          rc == 2 and "ERROR" in out
          and not os.path.exists(os.path.join(work, "mm",
                                              "site_exclusion_site-x.json")),
          "rc=%s out=%s err=%s" % (rc, out[:200], err[:200]))
    lst = write_json(os.path.join(work, "list.json"), [1, 2])
    rc, out, err = run_cli([d_a, lst, "--out", os.path.join(work, "lst")])
    check("CLI: a report whose JSON top level is a list is refused (exit 2, "
          "ERROR, no traceback)",
          rc == 2 and "ERROR" in out and "not an object" in out
          and "Traceback" not in err,
          "rc=%s out=%s err=%s" % (rc, out[:200], err[:200]))
    try:
        se.load_report(lst)
        check("load_report raises ValueError on a non-object top level",
              False, "no exception")
    except ValueError as exc:
        check("load_report raises ValueError on a non-object top level",
              "not an object" in str(exc), str(exc))

    # ------------------------------------------------------------------
    # 4. idempotence and order independence (directories accepted)
    # ------------------------------------------------------------------
    out1 = os.path.join(work, "cli1")
    out2 = os.path.join(work, "cli2")
    out3 = os.path.join(work, "cli3")
    rc1, o1, e1 = run_cli([d_a, d_b, "--out", out1])
    rc2, o2, e2 = run_cli([d_a, d_b, "--out", out2])
    rc3, o3, e3 = run_cli([os.path.join(d_b, "report.json"),
                           os.path.join(d_a, "report.json"), "--out", out3])
    check("CLI exits 0 on two sessions (directories and files accepted)",
          rc1 == 0 and rc2 == 0 and rc3 == 0,
          "%s %s %s | %s %s %s" % (rc1, rc2, rc3, e1[:150], e2[:150],
                                   e3[:150]))
    files = ("site_exclusion_site-x.json", "site_exclusion_site-x.txt")
    same = all(os.path.isfile(os.path.join(out1, f)) and open(
        os.path.join(out1, f), "rb").read() == open(
        os.path.join(out2, f), "rb").read() for f in files)
    check("CLI run twice writes byte-identical .json and .txt", same)
    with open(os.path.join(out1, files[0])) as fh:
        j1 = json.load(fh)
    with open(os.path.join(out3, files[0])) as fh:
        j3 = json.load(fh)
    check("input order changes neither the combined curve nor the best",
          j1["curve"]["g_site"] == j3["curve"]["g_site"]
          and j1["combined"] == j3["combined"])
    with open(os.path.join(out1, files[1])) as fh:
        txt = fh.read()
    check(".txt carries m_a, g_site and one column per session labelled by "
          "bundle name, inf outside a session's band, a coverage line",
          "g_site" in txt and "g_a [GeV^-1]" in txt and "g_b [GeV^-1]" in txt
          and "inf" in txt and "# coverage:" in txt
          and len([l for l in txt.splitlines()
                   if not l.startswith("#")]) == 10,
          txt[:300])
    check("CLI stdout quotes the combined worst-case number, the coverage "
          "line and 'worst-case'",
          "combined worst-case" in o1 and "1 GeV^-1" in o1
          and "coverage:" in o1, o1[:300])

    # ------------------------------------------------------------------
    # 5. end to end on two Bruker synthetic physics bundles
    # ------------------------------------------------------------------
    if not args.skip_e2e:
        pdir = os.path.join(work, "phys")
        if not os.path.isdir(pdir):
            os.makedirs(pdir)
        bundles = {}
        for tag, feat in (("bump", ["--feature", "bump", "--amp", "1.5"]),
                          ("none", ["--feature", "none"])):
            bundles[tag] = subprocess.check_output(
                [sys.executable, PHYSICS, "--out-dir", pdir] + feat,
                stderr=subprocess.DEVNULL).decode().strip().splitlines()[-1]
        reps = {}
        for tag in ("bump", "none"):
            rc, rep, log = run_report(bundles[tag],
                                      os.path.join(pdir, "report_" + tag))
            reps[tag] = rep
            ex = ((rep or {}).get("science") or {}).get("axion_exclusion") or {}
            check("e2e %s: facility_report ran and science.axion_exclusion "
                  "is available with finite numbers" % tag,
                  rc == 0 and ex.get("available") is True
                  and ex["result"]["g90_worst_best_gev_inv"] > 0
                  and ex["result"]["g90_nominal_best_gev_inv"] > 0
                  and len(ex["curve"]["m_a_ev"]) == len(ex["curve"]["g90_worst"])
                  and all(v > 0 and v < float("inf")
                          for v in ex["curve"]["g90_worst"]),
                  log[-400:] if rc else json.dumps(ex.get("result"))[:300])
        exb = reps["bump"]["science"]["axion_exclusion"]
        exn = reps["none"]["science"]["axion_exclusion"]
        check("e2e: Bruker tip angle resolved for both bundles (ladder "
              "tip_deg 1 deg, P1 = P90 path) and no reference left out",
              all(abs(p["tip_deg"] - 1.0) < 1e-9
                  for e in (exb, exn) for p in e["transduction"]["references"])
              and not exb["transduction"]["unresolved"]
              and "tip_deg" in exb["transduction"]["references"][0]["tip_basis"]
              and "P1 = P90" in exb["transduction"]["references"][0]
              ["tip_basis"],
              json.dumps(exb["transduction"])[:400])
        check("e2e: references at RG 25 bridged to the noise RG 101 with "
              "D_cal inflated by the ladder envelope",
              exb["transduction"]["rg_bridged"] is True
              and abs(exb["transduction"]["references"][0]
                      ["rg_bridge_amplitude"] - 101.0 / 25.0) < 1e-6
              and exb["calibration_derating"]["D_cal"] > 4.6
              and "inflated" in exb["calibration_derating"]["basis"],
              json.dumps(exb["calibration_derating"]))
        check("e2e: the bump session's Gamma is the broader POWER width and "
              "both widths are recorded",
              exb["damping"]["fwhm_used_hz"] >= max(
                  exb["damping"]["noise_line_power_fwhm_hz"] or 0,
                  exb["damping"]["reference_power_equivalent_fwhm_hz"] or 0)
              - 1e-9 and abs(exb["damping"]["reference_power_equivalent_fwhm_hz"]
                             * 3 ** 0.5
                             - exb["damping"]["reference_magnitude_fwhm_hz"])
              < 1e-9, json.dumps(exb["damping"]))
        spn, spb = exn["signal_power"], exb["signal_power"]
        check("e2e: the null session's P_90 is fluctuation-level (|excess| "
              "< 5 per-row sigma, statistical term > 20%) and says "
              "'statistics-limited'; the bump session's is line-power "
              "dominated (statistical term < 20%, |excess| > 5 sigma) and "
              "its line power is |PSD - baseline| over the window fraction",
              spn["feature"] == "null"
              and spn["P_absolute_excess_counts2"]
              < 5 * spn["sigma_stat_counts2"]
              and spn["statistical_fraction_of_P90"] > 0.2
              and any("statistics-limited" in h for h in exn["honesty"])
              and spb["feature"] == "bump"
              and spb["P_absolute_excess_counts2"]
              > 5 * spb["sigma_stat_counts2"]
              and spb["statistical_fraction_of_P90"] < 0.2
              and spb["P_absolute_excess_counts2"]
              >= spb["P_positive_part_counts2"] - 1e-12
              and abs(spb["P_line_counts2"]
                      - spb["P_absolute_excess_counts2"]
                      / spb["lorentzian_window_fraction"]) < 1e-9,
              "null %s bump %s" % (spn, spb))
        check("e2e: Bruker sign verified -> one mass axis, no mirror band",
              exb["line"]["sign_verified"] is True
              and "mirror" not in exb["result"]["band_10x_uev"]
              and abs(exb["result"]["m_a_at_best_uev"]
                      - (600.13e6 - 812.0 + exb["result"]["offset_at_best_hz"])
                      * 4.135667696e-15 * 1e6) < 1e-9,
              json.dumps(exb["result"])[:300])
        for e in (exb, exn):
            check("e2e: honesty carries the time-independence line and never "
                  "the word 'published'",
                  any("does NOT improve with more measurement time" in h
                      for h in e["honesty"])
                  and not any("published" in h.replace("unpublished", "")
                              .replace("UNPUBLISHED", "") for h in e["honesty"]))
        for tag in ("bump", "none"):
            with open(os.path.join(pdir, "report_" + tag, "report.html")) as fh:
                html = fh.read()
            check("e2e %s: HTML card present with inputs and honesty" % tag,
                  "Axion-coupling exclusion (worst-case, this session)" in html
                  and "does NOT improve with more measurement time" in html
                  and "D<sub>cal</sub>" in html and "P<sub>90</sub>" in html)

        # CLI combination over the two report.json files
        out_e = os.path.join(work, "site_e2e")
        rc, out, err = run_cli([os.path.join(pdir, "report_bump", "report.json"),
                                os.path.join(pdir, "report_none", "report.json"),
                                "--out", out_e])
        jp = os.path.join(out_e, "site_exclusion_injection-test.json")
        check("e2e: site_exclusion CLI exits 0 and writes the slug-named "
              "outputs", rc == 0 and os.path.isfile(jp)
              and os.path.isfile(jp[:-5] + ".txt"), err[:300] + out[:300])
        site_e = None
        if os.path.isfile(jp):
            with open(jp) as fh:
                site_e = json.load(fh)
            bests = [s["g90_worst_best_gev_inv"] for s in site_e["sessions"]]
            cb = site_e["combined"]["g90_worst_best_gev_inv"]
            # the node grid takes the weaker bracketing native point, so the
            # combined best sits at or a hair above the smaller session best
            check("e2e: combined best g %.6g = the smaller session best "
                  "(%s) to within +1%%, never below it"
                  % (cb, ", ".join("%.6g" % b for b in bests)),
                  site_e["n_sessions"] == 2
                  and min(bests) * (1 - 1e-12) <= cb <= 1.01 * min(bests),
                  "combined %s" % cb)
            gs = site_e["curve"]["g_site"]
            check("e2e: combined curve <= each session's curve pointwise, "
                  "one carrier -> ~1100 shared nodes, not 2 x 1100",
                  all(all(c is None or g is None or g <= c + 1e-12 * c
                          for g, c in zip(gs, col))
                      for col in site_e["curve"]["per_session"])
                  and 1099 <= len(gs) <= 1102, "nodes %d" % len(gs))
            check("e2e: total noise seconds = 2 x 16 rows x row length",
                  abs(site_e["combined"]["total_noise_seconds"]
                      - 2 * exb["signal_power"]["noise_seconds"]) < 1e-6)
            check("e2e: session labels are the bundle stems, not directory "
                  "names",
                  all(s["label"] not in ("report_bump", "report_none")
                      and s["label"] in os.path.basename(s["bundle"])
                      for s in site_e["sessions"]),
                  json.dumps([(s["label"], s["bundle"])
                              for s in site_e["sessions"]]))

        # --prior-reports on the second bundle
        rc, rep_p, log = run_report(
            bundles["none"], os.path.join(pdir, "report_none_prior"),
            ["--prior-reports", os.path.join(pdir, "report_bump", "report.json")])
        sc = (((rep_p or {}).get("science") or {}).get("axion_exclusion")
              or {}).get("site_combined") or {}
        check("e2e: --prior-reports -> science.axion_exclusion.site_combined "
              "with n_sessions 2, combined best <= each session's, no "
              "per-session columns in report.json",
              rc == 0 and sc.get("n_sessions") == 2
              and min(s["g90_worst_best_gev_inv"] for s in sc["sessions"])
              * (1 - 1e-12) <= sc["combined"]["g90_worst_best_gev_inv"]
              <= 1.01 * min(s["g90_worst_best_gev_inv"]
                            for s in sc["sessions"])
              and sc["combined"]["total_noise_seconds"] > 0
              and sc["curve"]["per_session"] is None
              and sc["curve"]["if_sign"] is None,
              log[-400:] if rc else json.dumps(sc)[:400])
        if sc and site_e:
            check("e2e: --prior-reports combination equals the CLI's",
                  abs(sc["combined"]["g90_worst_best_gev_inv"]
                      - site_e["combined"]["g90_worst_best_gev_inv"])
                  < 1e-12 * sc["combined"]["g90_worst_best_gev_inv"]
                  and sc["curve"]["g_site"] == site_e["curve"]["g_site"])
            with open(os.path.join(pdir, "report_none_prior",
                                   "report.html")) as fh:
                html_p = fh.read()
            check("e2e: HTML card shows the site-combined summary and the "
                  "coverage line",
                  "Site-combined (2 sessions" in html_p
                  and "Coverage:" in html_p)
            check("e2e: sign-verified site -> the card prints the headline "
                  "only (no conditional headlines, no sign note), sessions "
                  "marked 'verified (bruker)'",
                  "Conditional on the axis sign" not in html_p
                  and "sign-robust" not in html_p
                  and "verified (bruker)" in html_p
                  and sc["combined"]["if_sign"] == {}
                  and "sign_note" not in sc["combined"])

        # a re-run with the session's own earlier report among the priors:
        # the new exclusion is kept, the prior is the duplicate
        own_prior = os.path.join(pdir, "report_none", "report.json")
        rc, rep_s, log = run_report(
            bundles["none"], os.path.join(pdir, "report_none_selfprior"),
            ["--prior-reports", os.path.join(pdir, "report_bump", "report.json"),
             own_prior])
        scs = (((rep_s or {}).get("science") or {}).get("axion_exclusion")
               or {}).get("site_combined") or {}
        check("e2e: own earlier report among --prior-reports -> the NEW "
              "session is kept, the prior is skipped as the duplicate",
              rc == 0 and scs.get("n_sessions") == 2
              and len(scs.get("skipped") or []) == 1
              and "duplicate" in scs["skipped"][0]["why"]
              and os.path.abspath(scs["skipped"][0]["report"])
              == os.path.abspath(own_prior)
              and any(os.path.abspath(s["report"]) == os.path.abspath(
                  os.path.join(pdir, "report_none_selfprior", "report.json"))
                      for s in scs["sessions"]),
              log[-400:] if rc else json.dumps(scs.get("skipped"))[:400])

        # a prior report from another facility is refused
        other = copy.deepcopy(reps["bump"])
        other["facility_slug"] = "other-site"
        op = write_json(os.path.join(pdir, "other_report.json"), other)
        rc, rep_o, log = run_report(bundles["none"],
                                    os.path.join(pdir, "report_refused"),
                                    ["--prior-reports", op])
        check("e2e: --prior-reports from another facility_slug is refused "
              "(exit 2, ERROR, no report.json)",
              rc == 2 and "ERROR" in log and "other-site" in log
              and not os.path.isfile(os.path.join(pdir, "report_refused",
                                                  "report.json")),
              "rc=%s %s" % (rc, log[-300:]))
        rc, rep_l, log = run_report(bundles["none"],
                                    os.path.join(pdir, "report_refused_list"),
                                    ["--prior-reports", lst])
        check("e2e: --prior-reports whose JSON is a list is refused (exit 2, "
              "ERROR, no traceback, no report.json)",
              rc == 2 and "ERROR" in log and "Traceback" not in log
              and not os.path.isfile(os.path.join(pdir, "report_refused_list",
                                                  "report.json")),
              "rc=%s %s" % (rc, log[-300:]))

    # ------------------------------------------------------------------
    # 6. real sessions (opt-in): --real-report, --real-bundle
    # ------------------------------------------------------------------
    if args.real_report:
        reals = [se.load_report(p) for p in args.real_report]
        site_r = se.combine_reports(reals)
        cr = site_r["combined"] or {}
        order_r = cr.get("sign_hypotheses") or []
        cond = [cr["if_sign"][l]["g90_worst_best_gev_inv"] for l in order_r]
        check("real reports (%s, %d session(s)): combined, finite JSON, "
              "robust headline >= the best conditional headline and the "
              "factor field is their ratio"
              % (site_r["facility_slug"], site_r["n_sessions"]),
              cr and "Infinity" not in json.dumps(site_r)
              and "NaN" not in json.dumps(site_r)
              and (not cond or (cr["g90_worst_best_gev_inv"]
                                >= min(cond) * (1 - 1e-12)
                                and close(cr["robust_over_conditional_factor"],
                                          cr["g90_worst_best_gev_inv"]
                                          / min(cond)))),
              json.dumps(cr)[:500])
        if cond:
            check("real reports: sign_premise present, independent-sign "
                  "headline >= the sign-robust one (max-min <= min-max), no "
                  "'one-minute' in the notes",
                  "premise" in cr.get("sign_premise", "")
                  and cr["independent_sign"]["g90_worst_best_gev_inv"]
                  >= cr["g90_worst_best_gev_inv"] * (1 - 1e-12)
                  and "one-minute" not in cr["sign_note"]
                  and "one-minute" not in cr["sign_premise"],
                  cr.get("sign_premise"))
        if (site_r.get("facility_slug") == "siu-carbondale"
                and order_r == ["nominal", "mirror"]):
            hn, hm = cr["if_sign"]["nominal"], cr["if_sign"]["mirror"]
            check("SIU real sessions: if nominal g ~3.17e-4 GeV^-1 at "
                  "1.6527016 ueV, if mirror ~3.17e-4 at 1.6526971 ueV, "
                  "robust ~1.83e-3 (5.8x weaker), one agilent sign group, "
                  "note quotes the factor and the tof-shift test",
                  abs(hn["g90_worst_best_gev_inv"] / 3.17e-4 - 1) < 0.01
                  and abs(hm["g90_worst_best_gev_inv"] / 3.17e-4 - 1) < 0.01
                  and abs(hn["m_a_at_best_uev"] - 1.6527016) < 5e-7
                  and abs(hm["m_a_at_best_uev"] - 1.6526971) < 5e-7
                  and abs(cr["g90_worst_best_gev_inv"] / 1.83e-3 - 1) < 0.01
                  and abs(cr["robust_over_conditional_factor"] - 5.8) < 0.1
                  and "5.8x weaker" in cr["sign_note"]
                  and "tof-shift" in cr["sign_note"]
                  and list(cr["sign_groups"]) == ["agilent"]
                  and len(cr["sign_groups"]["agilent"]) == site_r["n_sessions"]
                  and all(s["vendor"] == "agilent" for s in site_r["sessions"]),
                  json.dumps({"robust": cr.get("robust"),
                              "if_sign": cr.get("if_sign"),
                              "note": cr.get("sign_note")})[:900])
        out_r = os.path.join(work, "real_cli")
        rc, o_r, e_r = run_cli(list(args.real_report) + ["--out", out_r])
        tp = os.path.join(out_r, "site_exclusion_%s.txt" % site_r["facility_slug"])
        txt_r = open(tp).read() if os.path.isfile(tp) else ""
        head = [l for l in txt_r.splitlines() if l.startswith("# m_a")]
        check("real reports CLI: exit 0, .txt carries one g_if_<hypothesis> "
              "column per hypothesis, the sign line, and one row per node "
              "some hypothesis covers",
              rc == 0 and head
              and all("g_if_%s [GeV^-1]" % l.replace(", ", "+").replace(" ", "-")
                      in head[0] for l in order_r)
              and (not order_r or ("# sign:" in txt_r
                                   and "# sign premise:" in txt_r))
              and len([l for l in txt_r.splitlines() if not l.startswith("#")])
              == len(((site_r["curve"] or {}).get("if_sign") or
                      {"m_a_ev": site_r["curve"]["m_a_ev"]})["m_a_ev"]),
              "rc=%s %s %s" % (rc, e_r[:200], head[:1]))
    if args.real_bundle and len(args.real_bundle) >= 2:
        rdir = os.path.join(work, "real_bundles")
        priors = []
        for i, b in enumerate(args.real_bundle[:-1]):
            d = os.path.join(rdir, "report_%d" % i)
            rc, rep_i, log = run_report(b, d)
            check("real bundle %d: facility_report exits 0" % i, rc == 0,
                  log[-300:])
            priors.append(os.path.join(d, "report.json"))
        d_last = os.path.join(rdir, "report_last")
        rc, rep_l, log = run_report(args.real_bundle[-1], d_last,
                                    ["--prior-reports"] + priors)
        scl = (((rep_l or {}).get("science") or {}).get("axion_exclusion")
               or {}).get("site_combined") or {}
        cl = scl.get("combined") or {}
        html_l = ""
        if os.path.isfile(os.path.join(d_last, "report.html")):
            with open(os.path.join(d_last, "report.html")) as fh:
                html_l = fh.read()
        unv = bool(cl.get("sign_hypotheses"))
        check("real bundles with --prior-reports: site_combined over %d "
              "sessions, finite JSON, card shows the headline%s"
              % (len(args.real_bundle),
                 ", the conditional headlines and the sign note" if unv
                 else " only"),
              rc == 0 and scl.get("n_sessions") == len(args.real_bundle)
              and "Infinity" not in json.dumps(scl)
              and "NaN" not in json.dumps(scl)
              and scl["curve"]["if_sign"] is None
              and "Site-combined (%d sessions" % len(args.real_bundle) in html_l
              and ((unv and "Conditional on the axis sign" in html_l
                    and "if the axis sign is <b>nominal</b>" in html_l
                    and "if the axis sign is <b>mirror</b>" in html_l
                    and "sign-robust headline" in html_l
                    and "Sign premise:" in html_l
                    and "a premise the axis-sign lines below state" in html_l
                    and "one-minute" not in html_l
                    and ("weaker than" in html_l or "matches" in html_l)
                    and all("%.3g" % cl["if_sign"][l]["g90_worst_best_gev_inv"]
                            in html_l for l in cl["sign_hypotheses"]))
                   or (not unv and "Conditional on the axis sign"
                       not in html_l)),
              log[-400:] if rc else json.dumps(cl)[:600])

    print("")
    if FAILURES:
        print("SITE EXCLUSION TEST: FAIL (%d)" % len(FAILURES))
        return 1
    print("SITE EXCLUSION TEST: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
