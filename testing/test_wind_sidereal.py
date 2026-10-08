#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wind_sidereal.py -- the v0.8 wind-aware halo construction
(analysis/halo_wind.py, analysis spec Sec. 8) and the sidereal-modulation
fit (analysis/sidereal_modulation.py, Sec. 9) through
analysis/facility_report.py; validation items (v) and (vi) of Sec. 7.

    python3 testing/test_wind_sidereal.py [--out-dir DIR] [--full]
                                          [--skip-e2e]

  Tier A (seconds, no bundles; facility_report loaded with importlib, the
  test_report_bruker_refs.py pattern, halo_wind and sidereal_modulation
  through its own loaders) -- spec Sec. 7 (v):
    * alp_lineshape_angle(grid, nu, 90.0, SHM_VLAB_KMS) is BIT-identical
      (np.array_equal on lam, equal <v_perp^2/c^2>) to alp_lineshape(grid,
      nu, False) and theta 0.0 to alp_lineshape(grid, nu, True), at
      EXCL_MC_SAMPLES / EXCL_MC_SEED on EXCL_LINESHAPE_GRID_HZ, through
      halo_wind directly and through facility_report.alp_lineshape_angle;
    * the solar apex: RA 313 +- 1, Dec +48 +- 1 deg for the Sun alone;
      Dec +54 +- 0.5 on 5 October 2026 with the Earth's orbital velocity;
    * |v_lab| evaluated daily: 219.6 km/s on 1 December, 248.6 on 1 June
      (+- 0.2), the extremes of the year within 4 days of those dates;
    * <sin^2 theta> over one sidereal day at Oulu / Carbondale / Lausanne /
      Torino equals the closed form 1 - A^2 - B^2/2 (apex Dec of 5 Oct
      2026) within 0.002 and the Sec. 8.6 values 0.432 / 0.647 / 0.574 /
      0.586; the angle ranges 11-61 / 16-88 / 7-80 / 9-81 deg within 1;
    * wind_angle_deg folds into [0, 90]: a horizontal B0 at azimuth a and
      a + 180 gives the same angle, the vertical angle is acos|v . z|;
    * the row-time chain of Sec. 8.4 (c) on hand-made meta dicts
      (_wind_row_times): clock-audit block epochs -> block_spread at
      (i + 1/2) span / N; an acceptable row_started_offsets_ms list ->
      per_row at wall_start_ms + offsets[i]; an unacceptable list ->
      block_spread with a note naming it; started_local + tz, one row per
      experiment -> per_row; several rows with finished_local ->
      block_spread_local; without finished_local -> block_spread_estimated
      from the row seconds; no tz and no audit -> no times, and
      wind_session_model reports 'timing';
    * orientation defaults (_wind_orientation / wind_session_model):
      b0_orientation absent -> vertical / default_nmr for bruker, agilent,
      jeol and unknown for magritek (-> 'orientation'); horizontal without
      an azimuth -> unknown; horizontal with one -> horizontal; the
      'orientation' reason comes before 'coordinates' when both apply;
      city Nowhere -> 'coordinates', 'Carbondale, Illinois' -> (37.7,
      -89.2) registry_city, operator coordinates -> operator;
    * the fixed skip strings of Sec. 9.2 from sidereal_modulation_fit on
      hand-made exclusion dicts: 'exclusion_unavailable',
      'wind_model_unavailable: orientation | coordinates | timing';
    * sidereal_modulation unit pieces: fixed_shape_amplitude recovers an
      injected amplitude on a noise-free Lorentzian with its dispersive
      term and a flat offset (1e-9), clip_mask removes exactly one injected
      outlier at ROW_CLIP_NMAD, fit_curve equals per-column OLS to 1e-10,
      one_sided_bound clamps a negative P_a, _fit_summary's t90 is the
      t90_of callable at the fit's dof, t_quantile_one_sided gives 1.4398
      at dof 6 and the report hands it to the module.

  Tier B (end to end, synthetic bundles from testing/make_physics_bundle.py
  through analysis/facility_report.py; a 240-row report takes a few
  minutes, so the default QUICK mode runs ONE 240-row case and the 16-row
  default, --full runs them all) -- spec Sec. 7 (vi):
    (i)  recovery (quick): --site 65.0,25.5 --start-utc 2026-10-05T07:00Z
         --noise-rows 240 --row-cadence-s 180, bump a = 1.5 with
         --inject-modulation 6.5e5 (P_A / sigma(P_a) about 8): |fit.P_a -
         equivalent_P_a(template.offset_hz)| <= 1.5 sigma (the pull is
         printed), available, the Sec. 9.5 label verbatim, honesty[-1]
         'UNPUBLISHED ... SN1987A', bound.P90_mod == curve.P90_mod at
         bound.offset_at_best_hz, improvement.nominal == sqrt(P_90 /
         P90_mod) (1e-9), template.offset_hz == bound.offset_at_best_hz ==
         result.offset_at_best_wind_nominal_hz, len(curve) == 1100,
         fit.t90 == t_quantile_one_sided(0.90, fit.dof), every per_row
         carries line_power_fixed_shape_counts2, the HTML carries the
         Sec. 9.5 heading and every honesty entry;
    (ii) null (--full): the same bundle with --inject-modulation 0 ->
         |P_a| < 2.5 sigma, anti-phase |P_a| < 2.5 sigma;
    (iii) lab cycle (--full): --lab-cycle-pct 10 on the dip fixture with
         --lab-cycle-peak-utc 16:00Z (9 h into the 12 h window; the
         generator peaks at the session start by default): with
         SPINNOISE_SIDEREAL_REGRESSORS=none |P_a| >= 3 sigma and
         solar_degeneracy 'unbroken'; with the floor regressor |P_a| < 1.5
         sigma, 'regressed', VIF(f | regressors) < 20; the default peak at
         the session start -> solar_degeneracy 'inseparable'
         (fit.regressor_check.inseparable_from_template);
    (iv) skip paths: the default 16-row untimed bundle (city Nowhere) ->
         available false, skip_reason exactly 'wind_model_unavailable:
         coordinates' (the wind-model skip fires before n < 30),
         halo.wind_model_unavailable 'coordinates', no g90_wind_* key, the
         report-level honesty line printing the skip_reason (quick);
         --b0-orientation unknown -> 'wind_model_unavailable: orientation'
         (--full);
    (v)  option-B keys on the recovery report (quick): halo.wind_model
         carries every Sec. 8.5 key, halo.worst_construction is the fixed
         string, curve.g90_wind_conservative == curve.g90_wind_nominal x
         sqrt(D_cal) at every node and result.g90_wind_conservative_best ==
         result.g90_wind_nominal_best x sqrt(D_cal) when their offsets
         coincide (1e-9);
    (vi) site_exclusion over two synthetic v0.8 reports (--full: recovery
         + null) -> headline_curve g90_wind_conservative; recovery + the
         untimed 16-row report (quick) -> the g90_worst fallback, naming
         the session without a wind model.

  The two environment switches of the report (SPINNOISE_SIDEREAL_ESTIMATOR,
  SPINNOISE_SIDEREAL_REGRESSORS) are cleared for every report run and set
  per case, so the test does not depend on the caller's shell.

Exit 0 iff every check passes.  Python 3.8 + numpy in this file (the
report needs numpy + matplotlib); nothing under scratch/ is read.
"""

from __future__ import print_function

import argparse
import datetime
import glob
import html as html_mod
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REPORT_PY = os.path.join(REPO, "analysis", "facility_report.py")
SITE_PY = os.path.join(REPO, "analysis", "site_exclusion.py")
PHYSICS = os.path.join(REPO, "testing", "make_physics_bundle.py")

UTC = datetime.timezone.utc
ENV_SWITCHES = ("SPINNOISE_SIDEREAL_ESTIMATOR", "SPINNOISE_SIDEREAL_REGRESSORS")

# spec Sec. 9.5, verbatim
LABEL_95 = ("sidereal-modulation fit, preliminary and internal --- one-sided "
            "90% CL bound on the modulated excess under the standard halo at "
            "this site's wind angles; not a detection claim")
HEADING_95 = ("Sidereal-modulation fit (preliminary, internal; not a "
              "detection claim)")
WORST_CONSTRUCTION_85 = ("wind parallel to B_0, theta = 0, V = 233 km/s, "
                         "D_cal applied")
WIND_MODEL_KEYS_85 = (
    "v_sun_gal_kms", "v_earth_orbital_kms", "apex_ra_deg", "apex_dec_deg",
    "site", "b0_orientation", "b0_orientation_basis", "b0_azimuth_deg",
    "rows_timed_basis", "tz_offset_min", "tz_basis", "n_rows_timed",
    "theta_min_deg", "theta_max_deg", "theta_mean_deg", "sin2_mean",
    "vlab_mean_kms", "vlab_basis", "theta_bin_deg", "vperp2_over_c2_actual")
# spec Sec. 8.6: full sidereal day, apex Dec +54 on 5 October
SITES_86 = (("Oulu", 65.0, 25.5, 11.0, 61.0, 0.432),
            ("Carbondale", 37.7, -89.2, 16.0, 88.0, 0.647),
            ("Lausanne", 46.5, 6.6, 7.0, 80.0, 0.574),
            ("Torino", 45.1, 7.7, 9.0, 81.0, 0.586))

# the Sec. 7 (vi) fixture (F3's settings): 240 rows every 180 s from 07:00
# UTC at Oulu = the 12 h fall of the template from its peak to its trough
TIMED = ["--noise-rows", "240", "--row-cadence-s", "180",
         "--start-utc", "2026-10-05T07:00:00Z", "--site", "65.0,25.5"]
BUMP = ["--feature", "bump", "--amp", "1.5"]
DIP = ["--feature", "dip", "--amp", "0.35"]
INJECT_P_A = 6.5e5          # counts^2; P_A / sigma(P_a) about 8 on this fixture

FAILED = []
TIMINGS = []


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    print("%s: %s%s" % (tag, name, ("" if ok or not detail else
                                    " -- " + str(detail))))
    if not ok:
        FAILED.append(name)
    return bool(ok)


def timed(label, t0):
    dt = time.time() - t0
    TIMINGS.append((label, dt))
    print("       [%s: %.1f s]" % (label, dt))


def load_report_module():
    spec = importlib.util.spec_from_file_location("facility_report", REPORT_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def rel(a, b):
    try:
        return abs(float(a) - float(b)) / max(abs(float(b)), 1e-300)
    except (TypeError, ValueError):
        return float("inf")


# ===========================================================================
# Tier A
# ===========================================================================

def lineshape_cases(fr, hw):
    grid = np.arange(*fr.EXCL_LINESHAPE_GRID_HZ)
    nu_a = 600.13e6 + 0.0
    t0 = time.time()
    lam_perp, vp_perp = fr.alp_lineshape(grid, nu_a, False)
    lam_par, vp_par = fr.alp_lineshape(grid, nu_a, True)
    lam_90, vp_90 = hw.alp_lineshape_angle(grid, nu_a, 90.0, hw.SHM_VLAB_KMS,
                                           fr.EXCL_MC_SAMPLES, fr.EXCL_MC_SEED)
    lam_0, vp_0 = hw.alp_lineshape_angle(grid, nu_a, 0.0, hw.SHM_VLAB_KMS,
                                         fr.EXCL_MC_SAMPLES, fr.EXCL_MC_SEED)
    timed("four full Monte-Carlo lineshapes", t0)
    check("7(v) lineshape: alp_lineshape_angle(grid, nu, 90.0, SHM_VLAB_KMS) "
          "== alp_lineshape(grid, nu, False) bit for bit, equal "
          "<v_perp^2/c^2>",
          np.array_equal(lam_90, lam_perp) and vp_90 == vp_perp,
          "max |diff| %.3g, vp %r vs %r" % (float(np.max(np.abs(lam_90 -
                                                              lam_perp))),
                                            vp_90, vp_perp))
    check("7(v) lineshape: alp_lineshape_angle(grid, nu, 0.0, SHM_VLAB_KMS) "
          "== alp_lineshape(grid, nu, True) bit for bit, equal "
          "<v_perp^2/c^2>",
          np.array_equal(lam_0, lam_par) and vp_0 == vp_par,
          "max |diff| %.3g, vp %r vs %r" % (float(np.max(np.abs(lam_0 -
                                                              lam_par))),
                                            vp_0, vp_par))
    check("7(v) lineshape: the constants are the spec's (EXCL_MC_SAMPLES "
          "2e6, EXCL_MC_SEED 20200529, grid 0..6000 Hz step 2, SHM_VLAB_KMS "
          "233 in both modules) and Int lam dnu = <v_perp^2/c^2>",
          fr.EXCL_MC_SAMPLES == 2000000 and fr.EXCL_MC_SEED == 20200529
          and tuple(fr.EXCL_LINESHAPE_GRID_HZ) == (0.0, 6000.0, 2.0)
          and fr.SHM_VLAB_KMS == hw.SHM_VLAB_KMS == 233.0
          and rel(np.sum(lam_90) * (grid[1] - grid[0]), vp_90) < 1e-3
          and vp_perp > vp_par,
          (fr.EXCL_MC_SAMPLES, fr.EXCL_MC_SEED, fr.EXCL_LINESHAPE_GRID_HZ))
    # the report's own wrapper (defaults) is the same call
    t0 = time.time()
    lam_w, vp_w = fr.alp_lineshape_angle(grid, nu_a, 90.0, fr.SHM_VLAB_KMS)
    timed("facility_report.alp_lineshape_angle wrapper", t0)
    check("7(v) lineshape: facility_report.alp_lineshape_angle at its "
          "defaults (EXCL_MC_SAMPLES, EXCL_MC_SEED) reproduces the "
          "perpendicular curve bit for bit",
          np.array_equal(lam_w, lam_perp) and vp_w == vp_perp)


def apex_vlab_cases(hw):
    ra_sun, dec_sun = hw.radec_deg(hw.GAL2EQ.dot(np.array(hw.V_SUN_GAL_KMS)))
    check("7(v) apex: the Sun alone (v_sun = (11.1, 232.24, 7.25) km/s) "
          "points at RA 313 +- 1, Dec +48 +- 1 deg",
          abs(ra_sun - 313.0) <= 1.0 and abs(dec_sun - 48.0) <= 1.0,
          "RA %.2f Dec %.2f" % (ra_sun, dec_sun))
    t_oct = datetime.datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    ra_oct, dec_oct = hw.apex_radec_deg(t_oct)
    check("7(v) apex: Dec +54 +- 0.5 deg on 5 October 2026 with the Earth's "
          "orbital velocity",
          abs(dec_oct - 54.0) <= 0.5, "RA %.2f Dec %.2f" % (ra_oct, dec_oct))
    decs = [hw.apex_radec_deg(datetime.datetime(2026, 1, 1, tzinfo=UTC)
                              + datetime.timedelta(days=d))[1]
            for d in range(366)]
    check("7(v) apex: the annual loop spans Dec +41 .. +54.5 deg (+- 0.5)",
          abs(min(decs) - 41.0) <= 0.5 and abs(max(decs) - 54.5) <= 0.5,
          "%.2f .. %.2f" % (min(decs), max(decs)))
    day0 = datetime.datetime(2026, 1, 1, tzinfo=UTC)
    days = [day0 + datetime.timedelta(days=d) for d in range(365)]
    speeds = np.array([float(np.linalg.norm(hw.v_lab_gal(t))) for t in days])
    v_dec1 = speeds[(datetime.datetime(2026, 12, 1, tzinfo=UTC) - day0).days]
    v_jun1 = speeds[(datetime.datetime(2026, 6, 1, tzinfo=UTC) - day0).days]
    check("7(v) |v_lab| daily: 219.6 km/s on 1 December and 248.6 km/s on "
          "1 June (+- 0.2)",
          abs(v_dec1 - 219.6) <= 0.2 and abs(v_jun1 - 248.6) <= 0.2,
          "1 Dec %.2f, 1 Jun %.2f" % (v_dec1, v_jun1))
    d_min, d_max = days[int(np.argmin(speeds))], days[int(np.argmax(speeds))]
    check("7(v) |v_lab| daily: the year's minimum falls within 4 days of "
          "1 December and the maximum within 4 days of 1 June",
          abs((d_min - datetime.datetime(2026, 12, 1, tzinfo=UTC)).days) <= 4
          and abs((d_max - datetime.datetime(2026, 6, 1, tzinfo=UTC)).days)
          <= 4,
          "min %s %.2f, max %s %.2f" % (d_min.date(), speeds.min(),
                                        d_max.date(), speeds.max()))
    v_oct = float(np.linalg.norm(hw.v_lab_gal(t_oct)))
    check("7(v) |v_lab| on 5 October 2026 is 226 km/s (+- 0.5; the Oulu "
          "sessions' vlab_mean_kms)", abs(v_oct - 226.0) <= 0.5,
          "%.2f" % v_oct)


def sidereal_day_cases(hw):
    t_oct = datetime.datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    dec_oct = hw.apex_radec_deg(t_oct)[1]
    for name, lat, lon, th_lo, th_hi, s2_spec in SITES_86:
        hours, th = hw.sidereal_angle_curve(lat, lon, t_oct, n=241)
        s2 = float(np.mean(np.sin(np.radians(th)) ** 2))
        cf = hw.sidereal_sin2_closed_form(lat, dec_oct)
        check("7(v) sidereal day at %s (%.1f N, %.1f E): sampled <sin^2> "
              "%.4f == closed form 1 - A^2 - B^2/2 = %.4f within 0.002 and "
              "the Sec. 8.6 value %.3f" % (name, lat, lon, s2, cf, s2_spec),
              abs(s2 - cf) <= 0.002 and abs(s2 - s2_spec) <= 0.002)
        check("7(v) sidereal day at %s: theta %.1f .. %.1f deg is the Sec. "
              "8.6 range %.0f-%.0f within 1 deg; the sampled day holds no "
              "repeated sidereal phase (endpoint excluded)"
              % (name, th.min(), th.max(), th_lo, th_hi),
              abs(th.min() - th_lo) <= 1.0 and abs(th.max() - th_hi) <= 1.0
              and hours[-1] < hw.SIDEREAL_DAY_H and hours[0] == 0.0
              and len(set(np.round(hours, 9))) == hours.size)


def wind_angle_cases(hw):
    lat, lon = 65.0, 25.5
    t0 = datetime.datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    ts = [t0 + datetime.timedelta(hours=h) for h in range(48)]
    th_v = np.array([hw.wind_angle_deg(lat, lon, t) for t in ts])
    th_a = np.array([hw.wind_angle_deg(lat, lon, t, 30.0) for t in ts])
    th_b = np.array([hw.wind_angle_deg(lat, lon, t, 210.0) for t in ts])
    explicit = []
    for t in ts:
        v = hw.v_lab_eq(t)
        v = v / np.linalg.norm(v)
        explicit.append(math.degrees(math.acos(
            min(1.0, abs(float(np.dot(v, hw.zenith_eq(lat, lon, t))))))))
    check("7(v) wind_angle_deg folds to [0, 90]: 48 hourly angles at Oulu "
          "lie in [0, 90] for a vertical and a horizontal B0, azimuth a "
          "and a + 180 give the same angle (1e-9), and the vertical angle "
          "is acos|v_lab . zenith|",
          np.all(th_v >= 0.0) and np.all(th_v <= 90.0)
          and np.all(th_a >= 0.0) and np.all(th_a <= 90.0)
          and np.max(np.abs(th_a - th_b)) < 1e-9
          and np.max(np.abs(th_v - np.array(explicit))) < 1e-9,
          "vertical %.1f..%.1f, az30 %.1f..%.1f, max|a - a+180| %.2g"
          % (th_v.min(), th_v.max(), th_a.min(), th_a.max(),
             float(np.max(np.abs(th_a - th_b)))))
    # a horizontal B0 and the vertical are orthogonal axes: the two angles
    # cannot both be small
    check("7(v) wind_angle_deg: a horizontal B0 is orthogonal to the "
          "vertical one (cos^2 th_vertical + cos^2 th_horizontal <= 1 + "
          "1e-9 at every hour)",
          np.all(np.cos(np.radians(th_v)) ** 2
                 + np.cos(np.radians(th_a)) ** 2 <= 1.0 + 1e-9))


def _rows(expno_counts):
    rows = []
    for ex, n in expno_counts:
        for k in range(n):
            rows.append({"expno": ex, "row_in_expno": k + 1, "fit": {},
                         "started_local": None})
    return rows


def row_time_cases(fr, hw):
    start_ms = int(datetime.datetime(2026, 10, 5, 7, 0, tzinfo=UTC)
                   .timestamp() * 1000.0)         # 2026-10-05T07:00:00Z
    n = 8
    span_ms = n * 20000.0
    meta_audit = {"clock_audit": {"available": True, "blocks": [
        {"expno": 20, "role": "noise", "wall_start_ms": start_ms,
         "wall_end_ms": start_ms + span_ms}]}}
    nres = {"per_row": _rows([(20, n)])}
    times, basis, tz, tz_basis, why, ext = fr._wind_row_times(meta_audit, nres)
    exp = [hw.utc_from_epoch_ms(start_ms + (i + 0.5) * span_ms / n)
           for i in range(n)]
    check("8.4 row times: clock_audit block epochs alone -> rows_timed_basis "
          "block_spread, row i at wall_start_ms + (i + 1/2) span / N, time "
          "source clock_audit, no note, tz basis 'unknown' when unrecorded",
          basis == "block_spread" and times is not None and why is None
          and all(abs((a - b).total_seconds()) < 1e-3
                  for a, b in zip(times, exp))
          and ext == {"time_source": "clock_audit", "note": None}
          and tz is None and tz_basis == "unknown",
          (basis, why, ext))
    offs = [i * 20000.0 for i in range(n)]
    meta_pr = json.loads(json.dumps(meta_audit))
    meta_pr["clock_audit"]["blocks"][0]["row_started_offsets_ms"] = offs
    meta_pr["local_timezone_offset_min"] = 180
    meta_pr["local_timezone_offset_basis"] = "answers"
    times, basis, tz, tz_basis, why, ext = fr._wind_row_times(meta_pr, nres)
    check("8.4 row times: an acceptable clock_audit.blocks[]."
          "row_started_offsets_ms -> per_row at wall_start_ms + offsets[i], "
          "no note; tz 180 / basis 'answers' carried",
          basis == "per_row" and ext["note"] is None
          and all(abs((t - hw.utc_from_epoch_ms(start_ms + o))
                      .total_seconds()) < 1e-3 for t, o in zip(times, offs))
          and tz == 180 and tz_basis == "answers", (basis, ext))
    for label, bad in (("%d entries for %d rows" % (n - 1, n), offs[:-1]),
                       ("not non-decreasing", list(reversed(offs))),
                       ("outside the wall span", [o + span_ms for o in offs]),
                       ("non-numeric", offs[:-1] + ["x"])):
        meta_bad = json.loads(json.dumps(meta_pr))
        meta_bad["clock_audit"]["blocks"][0]["row_started_offsets_ms"] = bad
        times, basis, tz, tz_basis, why, ext = fr._wind_row_times(meta_bad,
                                                                   nres)
        check("8.4 row times: an unacceptable row_started_offsets_ms list "
              "(%s) -> block_spread for the whole session with a note "
              "saying the list was ignored" % label,
              basis == "block_spread" and times is not None
              and ext["time_source"] == "clock_audit"
              and isinstance(ext["note"], str)
              and "row_started_offsets_ms ignored" in ext["note"]
              and "block_spread" in ext["note"]
              and all(abs((a - b).total_seconds()) < 1e-3
                      for a, b in zip(times, exp)),
              (basis, ext))
    # started_local chain (no audit)
    local = ["2026-10-05T10:%02d:00" % (3 * i) for i in range(n)]
    meta_sl = {"local_timezone_offset_min": 180,
               "local_timezone_offset_basis": "packing_machine",
               "experiments": [{"expno": 30 + i, "started_local": local[i]}
                               for i in range(n)]}
    times, basis, tz, tz_basis, why, ext = fr._wind_row_times(
        meta_sl, {"per_row": _rows([(30 + i, 1) for i in range(n)])})
    check("8.4 row times: no audit, started_local + "
          "local_timezone_offset_min with one row per experiment (the "
          "VnmrJ protocol) -> per_row from started_local, UTC = local - "
          "3 h, tz basis packing_machine",
          basis == "per_row" and ext["time_source"] == "started_local"
          and all(t == hw.utc_from_local(l, 180) for t, l in zip(times, local))
          and times[0] == datetime.datetime(2026, 10, 5, 7, 0, tzinfo=UTC)
          and tz_basis == "packing_machine", (basis, ext, times[:1]))
    meta_bl = {"local_timezone_offset_min": 180,
               "experiments": [{"expno": 40,
                                "started_local": "2026-10-05T10:00:00",
                                "finished_local": "2026-10-05T10:16:00"}]}
    times, basis, tz, tz_basis, why, ext = fr._wind_row_times(
        meta_bl, {"per_row": _rows([(40, n)])})
    t_s = hw.utc_from_local("2026-10-05T10:00:00", 180)
    check("8.4 row times: several rows per experiment with started_local and "
          "finished_local -> block_spread_local between the two",
          basis == "block_spread_local" and ext["time_source"] == "started_local"
          and all(abs((t - (t_s + datetime.timedelta(seconds=960.0 * (i + 0.5)
                                                     / n))).total_seconds())
                  < 1e-3 for i, t in enumerate(times)), (basis, ext))
    meta_est = {"local_timezone_offset_min": 180,
                "experiments": [{"expno": 40,
                                 "started_local": "2026-10-05T10:00:00"}]}
    times, basis, tz, tz_basis, why, ext = fr._wind_row_times(
        meta_est, {"per_row": _rows([(40, n)]), "row_seconds": 20.0})
    check("8.4 row times: without finished_local -> block_spread_estimated "
          "from started_local + N x the row seconds",
          basis == "block_spread_estimated"
          and all(abs((t - (t_s + datetime.timedelta(seconds=20.0 * (i + 0.5))))
                      .total_seconds()) < 1e-3 for i, t in enumerate(times)),
          (basis, ext))
    meta_none = {"experiments": [{"expno": 40,
                                  "started_local": "2026-10-05T10:00:00"}]}
    times, basis, tz, tz_basis, why, ext = fr._wind_row_times(
        meta_none, {"per_row": _rows([(40, n)])})
    check("8.4 row times: no clock audit and no local_timezone_offset_min -> "
          "no times, the reason naming the missing offset",
          times is None and basis is None and isinstance(why, str)
          and "local_timezone_offset_min" in why
          and ext["time_source"] is None, (basis, why))
    meta_none["facility"] = {"latitude_deg": 65.0, "longitude_east_deg": 25.5}
    model, sw, reason, times = fr.wind_session_model(
        meta_none, {"per_row": _rows([(40, n)])}, "bruker")
    check("8.4 wind_session_model: untimed rows at a known site, bruker "
          "default orientation -> wind_model_unavailable 'timing'",
          model is None and reason is not None and reason[0] == "timing"
          and "UTC" in reason[1], reason)
    # a full model from the audit case at an operator site
    meta_audit["facility"] = {"latitude_deg": 65.0, "longitude_east_deg": 25.5}
    model, sw, reason, times = fr.wind_session_model(meta_audit, nres, "bruker")
    check("8.4 wind_session_model: audit epochs + operator coordinates + "
          "bruker -> a model with every Sec. 8.5 key except the lineshape "
          "integral (filled by the caller), site basis operator, "
          "orientation vertical / default_nmr, rows_timed_basis "
          "block_spread, n_rows_timed %d, theta in [0, 90], vlab 226 km/s"
          % n,
          model is not None and reason is None
          and all(k in model for k in WIND_MODEL_KEYS_85)
          and model["vperp2_over_c2_actual"] is None
          and model["site"] == {"latitude_deg": 65.0,
                                "longitude_east_deg": 25.5,
                                "basis": "operator"}
          and model["b0_orientation"] == "vertical"
          and model["b0_orientation_basis"] == "default_nmr"
          and model["rows_timed_basis"] == "block_spread"
          and model["n_rows_timed"] == n
          and 0.0 <= model["theta_min_deg"] <= model["theta_max_deg"] <= 90.0
          and abs(model["vlab_mean_kms"] - 226.0) < 1.0
          and model["theta_bin_deg"] == 2.0
          and model["vlab_basis"] == "session_mean"
          and len(times) == n
          and times[0] == datetime.datetime(2026, 10, 5, 7, 0, 10,
                                            tzinfo=UTC),
          None if model is None else sorted(set(WIND_MODEL_KEYS_85)
                                            - set(model)))


def orientation_site_cases(fr):
    for vendor in ("bruker", "agilent", "jeol"):
        check("8.2 orientation: b0_orientation absent, vendor %s -> vertical, "
              "basis default_nmr, no azimuth" % vendor,
              fr._wind_orientation({}, vendor)
              == ("vertical", "default_nmr", None),
              fr._wind_orientation({}, vendor))
    for vendor in ("magritek", "nanalysis", "somebody"):
        o = fr._wind_orientation({}, vendor)
        check("8.2 orientation: b0_orientation absent, vendor %s -> unknown"
              % vendor, o[0] == "unknown" and o[2] is None, o)
    check("8.2 orientation: recorded 'vertical' -> vertical / recorded",
          fr._wind_orientation({"spectrometer": {"b0_orientation":
                                                 "vertical"}}, "magritek")
          == ("vertical", "recorded", None))
    o = fr._wind_orientation({"spectrometer": {"b0_orientation":
                                               "horizontal"}}, "bruker")
    check("8.2 orientation: 'horizontal' without b0_azimuth_deg -> unknown "
          "(an unavailable orientation), the basis saying why",
          o[0] == "unknown" and "azimuth" in o[1] and o[2] is None, o)
    o = fr._wind_orientation({"spectrometer": {"b0_orientation": "horizontal",
                                               "b0_azimuth_deg": 450.0}},
                             "bruker")
    check("8.2 orientation: 'horizontal' with an azimuth -> horizontal / "
          "recorded, azimuth folded to [0, 360)",
          o == ("horizontal", "recorded", 90.0), o)
    check("8.2 orientation: recorded 'unknown' -> unknown / recorded",
          fr._wind_orientation({"spectrometer": {"b0_orientation":
                                                 "unknown"}}, "bruker")
          == ("unknown", "recorded", None))
    # site resolution
    check("8.4 site: city Nowhere, no coordinates -> None",
          fr._wind_site({"facility": {"city": "Nowhere",
                                      "country": "Nowhere"}}) is None)
    check("8.4 site: 'Carbondale, Illinois' resolves through the registry "
          "gazetteer to (37.7, -89.2), basis registry_city",
          fr._wind_site({"facility": {"city": "Carbondale, Illinois",
                                      "country": "USA"}})
          == (37.7, -89.2, "registry_city"))
    check("8.4 site: operator facility.latitude_deg / longitude_east_deg win "
          "over the city, basis operator",
          fr._wind_site({"facility": {"city": "Carbondale", "latitude_deg": 65.0,
                                      "longitude_east_deg": 25.5}})
          == (65.0, 25.5, "operator"))
    check("8.4 site: an operator entry outside the schema bounds is not used "
          "(lon 200 -> gazetteer city, else None)",
          fr._wind_site({"facility": {"city": "Nowhere", "latitude_deg": 65.0,
                                      "longitude_east_deg": 200.0}}) is None)
    nres = {"per_row": _rows([(20, 4)])}
    meta = {"facility": {"city": "Nowhere"}}
    m, sw, reason, t = fr.wind_session_model(meta, nres, "bruker")
    check("8.4 wind_session_model: city Nowhere (bruker, vertical by default) "
          "-> 'coordinates', the detail naming the city",
          m is None and reason[0] == "coordinates" and "Nowhere" in reason[1],
          reason)
    m, sw, reason, t = fr.wind_session_model(meta, nres, "magritek")
    check("8.2 wind_session_model: magritek without a recorded orientation "
          "-> 'orientation' (checked before coordinates)",
          m is None and reason[0] == "orientation", reason)
    m, sw, reason, t = fr.wind_session_model(
        {"facility": {"city": "Nowhere"},
         "spectrometer": {"b0_orientation": "unknown"}}, nres, "bruker")
    check("8.2 wind_session_model: recorded 'unknown' on a bruker bundle -> "
          "'orientation', the detail quoting the recorded value",
          m is None and reason[0] == "orientation"
          and "'unknown' recorded" in reason[1], reason)


def skip_string_cases(fr):
    nres = {"per_row": []}
    obj, qa = fr.sidereal_modulation_fit({}, nres, {"available": False,
                                                    "reason": "no reference"},
                                         "bruker")
    check("9.2 skip strings: exclusion unavailable -> available false, "
          "skip_reason exactly 'exclusion_unavailable', the detail in "
          "honesty and the QA row, label the Sec. 9.5 string",
          obj["available"] is False
          and obj["skip_reason"] == "exclusion_unavailable"
          and obj["label"] == LABEL_95
          and any("no reference" in h for h in obj["honesty"])
          and qa and "no reference" in qa[0]["detail"], obj)
    for why in ("orientation", "coordinates", "timing"):
        excl = {"available": True, "halo": {"wind_model_unavailable": why},
                "result": {}}
        obj, qa = fr.sidereal_modulation_fit({}, nres, excl, "bruker")
        check("9.2 skip strings: halo.wind_model_unavailable '%s' -> "
              "skip_reason exactly 'wind_model_unavailable: %s', honesty "
              "entry ending in UNPUBLISHED" % (why, why),
              obj["available"] is False
              and obj["skip_reason"] == "wind_model_unavailable: %s" % why
              and obj["honesty"][-1].rstrip().endswith("UNPUBLISHED.")
              and obj["label"] == LABEL_95, obj)
    check("9.2 skip strings: the module's leverage skip reads \"no "
          "modulation leverage in this session's hours\" with MIN_ROWS 30 "
          "and MIN_TEMPLATE_STD 0.02",
          fr.sidereal_modulation_module().SKIP_NO_LEVERAGE
          == "no modulation leverage in this session's hours"
          and fr.sidereal_modulation_module().MIN_ROWS == 30
          and fr.sidereal_modulation_module().MIN_TEMPLATE_STD == 0.02)


def sidereal_unit_cases(fr, sm):
    # fixed-shape amplitude on a noise-free line with dispersive term + offset
    f0, w, r = -811.9, 11.6, 0.28
    f = np.arange(-1200.0, -400.0, 0.25)
    u = (f - f0) / (w / 2.0)
    L = 1.0 / (1.0 + u * u)
    a_true, c_true = 1.7, 0.37
    pnorm = c_true + a_true * L * (1.0 + r * u)
    a = sm.fixed_shape_amplitude(f, pnorm, f0, w, r, fr.FIT_HALF_HZ)
    check("9.2 fixed_shape_amplitude: recovers a = %.1f on a noise-free "
          "Lorentzian with dispersive fraction %.2f and a flat offset %.2f "
          "within 1e-9" % (a_true, r, c_true),
          a is not None and abs(a - a_true) < 1e-9, a)
    pnorm2 = c_true + a_true * L            # no dispersive term in the data
    a2 = sm.fixed_shape_amplitude(f, pnorm2, f0, w, 0.0, fr.FIT_HALF_HZ)
    check("9.2 fixed_shape_amplitude: a pure Lorentzian with r = 0 gives the "
          "same amplitude; a window with too few bins returns None",
          a2 is not None and abs(a2 - a_true) < 1e-9
          and sm.fixed_shape_amplitude(f[::400], pnorm[::400], f0, w, r,
                                       fr.FIT_HALF_HZ) is None, a2)
    # clip_mask: exactly one injected outlier
    rng = np.random.default_rng(20261008)
    resid = rng.normal(0.0, 60.0, 240)
    resid[100] += 60.0 * 25.0
    keep, mad_sigma, thr = sm.clip_mask(resid, fr.ROW_CLIP_NMAD)
    check("9.2 clip_mask: one injected 25-sigma outlier among 240 Gaussian "
          "residuals is the only row removed at ROW_CLIP_NMAD = %g, "
          "threshold = nmad x 1.4826 x MAD" % fr.ROW_CLIP_NMAD,
          fr.ROW_CLIP_NMAD == 5.0 and int(np.count_nonzero(~keep)) == 1
          and not keep[100]
          and abs(thr - fr.ROW_CLIP_NMAD * 1.4826
                  * float(np.median(np.abs(resid - np.median(resid))))) < 1e-9
          and abs(mad_sigma / 60.0 - 1.0) < 0.2,
          (int(np.count_nonzero(~keep)), mad_sigma, thr))
    # fit_curve == per-column OLS
    n, m = 240, 7
    tt = np.linspace(0.0, 12.0, n)
    Z = np.column_stack([np.ones(n), rng.normal(0.0, 1.0, n)])
    F = np.column_stack([1.0 + 0.2 * np.cos(2 * np.pi * (tt + k) / 24.0)
                         for k in range(m)])
    P = 1000.0 + 300.0 * F[:, 2] + 50.0 * Z[:, 1] + rng.normal(0.0, 40.0, n)
    b, s, dof = sm.fit_curve(P, F, Z)
    worst = 0.0
    for j in range(m):
        beta, err, s_row, resid_j, dof_j = sm.ols(P, np.column_stack(
            [Z, F[:, j]]))
        worst = max(worst, rel(b[j], beta[-1]), rel(s[j], err[-1]))
        if dof_j != dof:
            worst = float("inf")
    check("9.2 fit_curve: (P_a, sigma_Pa) for %d template columns equal the "
          "per-column OLS of P on [Z, F_j] within 1e-10 (relative), same "
          "dof n - p" % m, worst < 1e-10 and dof == n - Z.shape[1] - 1,
          worst)
    check("9.2 one_sided_bound: P90_mod = max(P_a, 0) + t90 sigma clamps a "
          "negative P_a",
          abs(sm.one_sided_bound(-5.0, 2.0, 1.3) - 2.6) < 1e-12
          and abs(sm.one_sided_bound(5.0, 2.0, 1.3) - 7.6) < 1e-12)
    # t90: _fit_summary takes it from the callable at the fit's dof
    fs = sm._fit_summary(P, F[:, 2], lambda d: 100.0 + d)
    check("9.2 _fit_summary: t90 is t90_of(dof) with dof = n - p, and "
          "P90_mod = one_sided_bound(P_a, sigma_Pa, t90)",
          fs["dof"] == n - 2 and fs["t90"] == 100.0 + (n - 2)
          and abs(fs["P90_mod_counts2"]
                  - sm.one_sided_bound(fs["P_a_counts2"],
                                       fs["sigma_Pa_counts2"], fs["t90"]))
          < 1e-9, (fs["dof"], fs["t90"]))
    t6 = fr.t_quantile_one_sided(0.90, 6)
    t_big = fr.t_quantile_one_sided(0.90, 1707)
    src = open(REPORT_PY, encoding="utf-8").read()
    check("9.2 t90: t_quantile_one_sided(0.90, 6) = 1.4398 (the pilot's), "
          "1.2816-1.2830 at dof 1707, and facility_report hands the module "
          "'t90_of': lambda dof: t_quantile_one_sided(0.90, dof)",
          abs(t6 - 1.4398) < 5e-4 and 1.2816 <= t_big <= 1.2830
          and '"t90_of": lambda dof: t_quantile_one_sided(0.90, dof)' in src,
          (t6, t_big))


def tier_a(fr):
    hw = fr.halo_wind_module()
    sm = fr.sidereal_modulation_module()
    print("--- A1. lineshape bit-identity at theta 90 / 0 (Sec. 7 (v)) ---")
    lineshape_cases(fr, hw)
    print("--- A2. solar apex and |v_lab| through the year ---")
    apex_vlab_cases(hw)
    print("--- A3. <sin^2> over a sidereal day vs the closed form ---")
    sidereal_day_cases(hw)
    print("--- A4. wind angle folding ---")
    wind_angle_cases(hw)
    print("--- A5. row-time chain (Sec. 8.4 (c)) ---")
    row_time_cases(fr, hw)
    print("--- A6. orientation defaults and site resolution ---")
    orientation_site_cases(fr)
    print("--- A7. fixed skip strings (Sec. 9.2) ---")
    skip_string_cases(fr)
    print("--- A8. sidereal_modulation unit pieces ---")
    sidereal_unit_cases(fr, sm)


# ===========================================================================
# Tier B
# ===========================================================================

def clean_env(**extra):
    env = dict(os.environ)
    for k in ENV_SWITCHES:
        env.pop(k, None)
    env.update(extra)
    return env


def gen_bundle(out_dir, extra, label):
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    t0 = time.time()
    p = subprocess.run([sys.executable, PHYSICS, "--out-dir", out_dir]
                       + list(extra), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, env=clean_env())
    timed("generate %s" % label, t0)
    if p.returncode != 0:
        check("generate %s" % label, False,
              p.stderr.decode("utf-8", "replace")[-600:])
        return None
    return p.stdout.decode().strip().splitlines()[-1]


def run_report(bundle, out_dir, label, **env_extra):
    t0 = time.time()
    p = subprocess.run([sys.executable, REPORT_PY, bundle, "--out", out_dir],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env=clean_env(**env_extra))
    timed("report %s%s" % (label, (" " + " ".join("%s=%s" % kv for kv in
                                                   sorted(env_extra.items())))
                           if env_extra else ""), t0)
    log = (p.stdout + p.stderr).decode("utf-8", "replace")
    rep = None
    jpath = os.path.join(out_dir, "report.json")
    if p.returncode == 0 and os.path.isfile(jpath):
        with open(jpath, encoding="utf-8") as fh:
            rep = json.load(fh)
    check("report %s: facility_report exit 0 and report.json written"
          % label, rep is not None, log[-800:])
    return rep, log


def bundle_meta(zip_path):
    import zipfile
    with zipfile.ZipFile(zip_path) as z:
        return json.loads(z.read("meta.json").decode("utf-8"))


def pull_of(sm_obj, meta):
    """(pull, fit.P_a, sigma, equivalent P_a at template.offset_hz)."""
    mod = meta["injection_truth"]["modulation"]
    fit, tpl = sm_obj["fit"], sm_obj["template"]
    key = "%g" % tpl["offset_hz"]
    eq = (mod.get("equivalent_P_a_by_offset") or {}).get(key)
    if eq is None:
        eq = mod["P_a_counts2"] if tpl["offset_hz"] == mod["offset_hz"] \
            else None
    if eq is None:
        return None, fit["P_a_counts2"], fit["sigma_Pa_counts2"], None
    return ((fit["P_a_counts2"] - eq) / fit["sigma_Pa_counts2"],
            fit["P_a_counts2"], fit["sigma_Pa_counts2"], eq)


def sidereal_object_checks(fr, rep, tag):
    """The structural Sec. 9.5 / 8.5 checks on an available fit."""
    sci = rep["science"]
    smo = sci["sidereal_modulation"]
    ex = sci["axion_exclusion"]
    res, cv = ex["result"], ex["curve"]
    ok = check("%s: science.sidereal_modulation.available is true" % tag,
               smo.get("available") is True,
               (smo.get("skip_reason"), (smo.get("honesty") or [""])[0][:200]))
    if not ok:
        return None
    check("%s: label is the Sec. 9.5 string verbatim" % tag,
          smo["label"] == LABEL_95, smo["label"])
    check("%s: honesty[-1] starts with UNPUBLISHED and names the SN1987A "
          "bound" % tag,
          smo["honesty"] and smo["honesty"][-1].startswith("UNPUBLISHED")
          and "SN1987A" in smo["honesty"][-1], smo["honesty"][-1][:160])
    b, t, fit, curve = smo["bound"], smo["template"], smo["fit"], smo["curve"]
    offs = np.array(curve["nu_a_minus_nu_L_hz"], dtype=float)
    j = int(np.argmin(np.abs(offs - b["offset_at_best_hz"])))
    check("%s: template.offset_hz == bound.offset_at_best_hz == "
          "result.offset_at_best_wind_nominal_hz (%+.0f Hz)"
          % (tag, b["offset_at_best_hz"]),
          t["offset_hz"] == b["offset_at_best_hz"]
          == res["offset_at_best_wind_nominal_hz"],
          (t["offset_hz"], b["offset_at_best_hz"],
           res["offset_at_best_wind_nominal_hz"]))
    check("%s: len(curve) == 1100 on every curve array (EXCL_SCAN_HZ -4000 "
          "..+396 Hz step 4) and the grid is the exclusion's" % tag,
          offs.size == 1100 and offs[0] == -4000.0 and offs[-1] == 396.0
          and all(len(curve[k]) == 1100 for k in
                  ("m_a_ev", "P_a_counts2", "sigma_Pa_counts2",
                   "P90_mod_counts2", "g90_nominal_mod", "g90_conservative_mod"))
          and len(cv["g90_wind_nominal"]) == 1100
          and np.array_equal(offs, np.array(cv["nu_a_minus_nu_L_hz"])),
          offs.size)
    check("%s: bound.P90_mod_counts2 == curve.P90_mod_counts2 at "
          "offset_at_best_hz (1e-9) and bound.t90 == fit.t90" % tag,
          rel(b["P90_mod_counts2"], curve["P90_mod_counts2"][j]) < 1e-9
          and b["t90"] == fit["t90"],
          (b["P90_mod_counts2"], curve["P90_mod_counts2"][j]))
    P90 = ex["signal_power"]["P_90_counts2"]
    check("%s: improvement_vs_line_power.nominal == sqrt(P_90 / P90_mod) == "
          "curve.g90_wind_nominal / g90_nominal_mod at that offset (1e-9); "
          ".conservative == result.g90_wind_conservative_best / "
          "bound.g90_conservative_mod; .vs_worst_parallel == "
          "result.g90_worst_best / bound.g90_conservative_mod" % tag,
          rel(b["improvement_vs_line_power"]["nominal"],
              math.sqrt(P90 / b["P90_mod_counts2"])) < 1e-9
          and rel(b["improvement_vs_line_power"]["nominal"],
                  cv["g90_wind_nominal"][j] / curve["g90_nominal_mod"][j])
          < 1e-9
          and rel(b["improvement_vs_line_power"]["conservative"],
                  res["g90_wind_conservative_best_gev_inv"]
                  / b["g90_conservative_mod_gev_inv"]) < 1e-9
          and rel(b["improvement_vs_line_power"]["vs_worst_parallel"],
                  res["g90_worst_best_gev_inv"]
                  / b["g90_conservative_mod_gev_inv"]) < 1e-9,
          b["improvement_vs_line_power"])
    check("%s: fit.t90 == t_quantile_one_sided(0.90, fit.dof) (1e-12) with "
          "dof = rows_used - n_params" % tag,
          abs(fit["t90"] - fr.t_quantile_one_sided(0.90, fit["dof"])) < 1e-12
          and fit["dof"] == smo["rows_used"] - fit["n_params"],
          (fit["t90"], fit["dof"], smo["rows_used"], fit["n_params"]))
    rows = sci["noise"]["per_row"]
    check("%s: per_row[].line_power_fixed_shape_counts2 present and numeric "
          "on every one of the %d rows; estimator fixed_shape on that field"
          % (tag, len(rows)),
          rows and all(num(pr.get("line_power_fixed_shape_counts2"))
                       for pr in rows)
          and smo["estimator"]["name"] == "fixed_shape"
          and smo["estimator"]["per_row_field"]
          == "line_power_fixed_shape_counts2",
          smo["estimator"])
    check("%s: rows_timed_basis block_spread (the generator writes the "
          "block epochs, no row_started_offsets_ms), n_rows == rows used + "
          "excluded, rows_excluded_frac consistent" % tag,
          smo["rows_timed_basis"] == "block_spread"
          and smo["rows_with_line_power"] == smo["rows_used"]
          + smo["rows_excluded"]
          and abs(smo["rows_excluded_frac"] - smo["rows_excluded"]
                  / float(smo["rows_with_line_power"])) < 1e-12,
          (smo["rows_timed_basis"], smo["n_rows"], smo["rows_used"],
           smo["rows_excluded"]))
    check("%s: template std(f) >= 0.05 on the 12 h Oulu window (spec: "
          "std(f) 0.16-0.20 for the fall from peak to trough), leverage = "
          "sqrt(n) std(f), theta range inside [0, 90]" % tag,
          t["template_std"] >= 0.05
          and rel(t["leverage"], math.sqrt(smo["rows_with_line_power"])
                  * t["template_std"]) < 1e-6
          and 0.0 <= t["theta_min_deg"] <= t["theta_max_deg"] <= 90.0,
          (t["template_std"], t["leverage"], t["theta_min_deg"],
           t["theta_max_deg"]))
    check("%s: one report-level science.honesty line for the fit, starting "
          "with the Sec. 9.5 heading" % tag,
          sum(1 for h in sci["honesty"] if h.startswith(HEADING_95)) == 1)
    return smo


def option_b_checks(rep, tag):
    ex = rep["science"]["axion_exclusion"]
    halo, res, cv = ex["halo"], ex["result"], ex["curve"]
    wm = halo.get("wind_model")
    check("%s 8.5: halo.wind_model present with every key of Sec. 8.5, "
          "v_sun [11.1, 232.24, 7.25], v_earth 29.79, site basis operator "
          "(65.0, 25.5), b0 vertical, theta_bin_deg 2, vperp2_over_c2_actual "
          "between the worst and the nominal bracket" % tag,
          isinstance(wm, dict)
          and all(k in wm for k in WIND_MODEL_KEYS_85)
          and wm["v_sun_gal_kms"] == [11.1, 232.24, 7.25]
          and wm["v_earth_orbital_kms"] == 29.79
          and wm["site"] == {"latitude_deg": 65.0, "longitude_east_deg": 25.5,
                             "basis": "operator"}
          and wm["b0_orientation"] == "vertical"
          and wm["theta_bin_deg"] == 2.0
          and halo["vperp2_over_c2_worst"] < wm["vperp2_over_c2_actual"]
          < halo["vperp2_over_c2_nominal"]
          and "wind_model_unavailable" not in halo,
          None if not isinstance(wm, dict)
          else sorted(set(WIND_MODEL_KEYS_85) - set(wm)))
    check("%s 8.5: halo.worst_construction is the fixed string" % tag,
          halo.get("worst_construction") == WORST_CONSTRUCTION_85,
          halo.get("worst_construction"))
    d_cal = ex["calibration_derating"]["D_cal"]
    wc = np.array(cv["g90_wind_conservative"], dtype=float)
    wn = np.array(cv["g90_wind_nominal"], dtype=float)
    dev = float(np.max(np.abs(wc / (wn * math.sqrt(d_cal)) - 1.0)))
    check("%s 8.5: curve.g90_wind_conservative == curve.g90_wind_nominal x "
          "sqrt(D_cal = %.3f) at every one of the %d nodes (1e-9), all "
          "finite and positive" % (tag, d_cal, wc.size),
          wc.size == wn.size == 1100 and np.all(np.isfinite(wc))
          and np.all(wc > 0) and dev < 1e-9, dev)
    same_offset = (res["offset_at_best_wind_conservative_hz"]
                   == res["offset_at_best_wind_nominal_hz"])
    if same_offset:
        check("%s 8.5: result.g90_wind_conservative_best_gev_inv == "
              "result.g90_wind_nominal_best_gev_inv x sqrt(D_cal) (1e-9; the "
              "two offsets coincide at %+.0f Hz)"
              % (tag, res["offset_at_best_wind_nominal_hz"]),
              rel(res["g90_wind_conservative_best_gev_inv"],
                  res["g90_wind_nominal_best_gev_inv"] * math.sqrt(d_cal))
              < 1e-9, res)
    else:
        print("       (offsets differ: conservative %+.0f, nominal %+.0f Hz; "
              "the scalar relation is checked through the curve)"
              % (res["offset_at_best_wind_conservative_hz"],
                 res["offset_at_best_wind_nominal_hz"]))
    offs = np.array(cv["nu_a_minus_nu_L_hz"], dtype=float)
    j_wc = int(np.argmin(np.abs(offs - res["offset_at_best_wind_conservative_hz"])))
    j_wn = int(np.argmin(np.abs(offs - res["offset_at_best_wind_nominal_hz"])))
    check("%s 8.5: the result scalars are the curve minima: "
          "g90_wind_conservative_best == min(curve.g90_wind_conservative) at "
          "its offset, g90_wind_nominal_best == min(curve.g90_wind_nominal)"
          % tag,
          rel(res["g90_wind_conservative_best_gev_inv"], wc[j_wc]) < 1e-9
          and rel(res["g90_wind_conservative_best_gev_inv"], wc.min()) < 1e-9
          and rel(res["g90_wind_nominal_best_gev_inv"], wn[j_wn]) < 1e-9
          and rel(res["g90_wind_nominal_best_gev_inv"], wn.min()) < 1e-9)
    check("%s 8.5: the wind-aware conservative headline sits between the "
          "worst-parallel and the perpendicular-nominal bests, and the v0.7 "
          "keys keep their names" % tag,
          res["g90_nominal_best_gev_inv"] < res["g90_wind_conservative_best_gev_inv"]
          <= res["g90_worst_best_gev_inv"] * (1.0 + 1e-12)
          and all(k in res for k in ("g90_worst_best_gev_inv", "offset_at_best_hz",
                                     "m_a_at_best_uev", "ratio_to_sn1987a_bound",
                                     "g90_nominal_best_gev_inv",
                                     "offset_at_best_nominal_hz"))
          and all(k in cv for k in ("g90_worst", "g90_nominal")),
          (res["g90_nominal_best_gev_inv"],
           res["g90_wind_conservative_best_gev_inv"],
           res["g90_worst_best_gev_inv"]))
    check("%s 8.5: honesty[0] of the exclusion names the actual wind "
          "direction and a later line keeps the worst-parallel robustness "
          "number ('wind along B_0')" % tag,
          "actual direction" in ex["honesty"][0]
          and any("along B_0" in h for h in ex["honesty"][1:]),
          [h[:100] for h in ex["honesty"][:3]])


def html_checks(out_dir, smo, tag):
    hpath = os.path.join(out_dir, "report.html")
    try:
        with open(hpath, encoding="utf-8") as fh:
            html = fh.read()
    except (IOError, OSError):
        check("%s HTML: report.html written" % tag, False)
        return
    missing = []
    for h in smo["honesty"]:
        probe = h[:60]
        if (probe not in html and html_mod.escape(probe) not in html
                and html_mod.escape(probe, quote=False) not in html):
            missing.append(probe)
    check("%s HTML: the Sec. 9.5 heading qualifier and every honesty entry "
          "of the fit are rendered" % tag,
          HEADING_95 in html and not missing, missing)


def skip_report_checks(rep, why, tag):
    sci = rep["science"]
    smo = sci["sidereal_modulation"]
    ex = sci["axion_exclusion"]
    check("%s 9.2: available false, skip_reason exactly "
          "'wind_model_unavailable: %s' (fires before the n < 30 rule), label "
          "the Sec. 9.5 string" % (tag, why),
          smo["available"] is False
          and smo["skip_reason"] == "wind_model_unavailable: %s" % why
          and smo["label"] == LABEL_95,
          (smo["available"], smo.get("skip_reason")))
    check("%s 8.5: halo.wind_model_unavailable == '%s', halo.wind_model "
          "absent, no g90_wind_* key in curve or result, the v0.7 keys "
          "present" % (tag, why),
          ex.get("available") is True
          and ex["halo"].get("wind_model_unavailable") == why
          and "wind_model" not in ex["halo"]
          and not any(k.startswith("g90_wind") for k in ex["curve"])
          and not any("wind" in k for k in ex["result"])
          and "g90_worst" in ex["curve"] and "g90_nominal" in ex["curve"]
          and "g90_worst_best_gev_inv" in ex["result"],
          (ex["halo"].get("wind_model_unavailable"),
           sorted(k for k in ex["curve"] if "wind" in k),
           sorted(k for k in ex["result"] if "wind" in k)))
    check("%s 9.5: the skipped session prints its skip_reason in the "
          "report-level honesty line and in its own honesty list" % tag,
          any(h.startswith("Sidereal-modulation fit not run: "
                           "wind_model_unavailable: %s" % why)
              for h in sci["honesty"])
          and any("wind_model_unavailable: %s" % why in h
                  for h in smo["honesty"]),
          [h for h in sci["honesty"] if "Sidereal" in h][:1])
    check("%s 8.5: the exclusion's construction / honesty[0] stay the v0.7 "
          "text (no 'actual direction') when the wind model is absent" % tag,
          "actual direction" not in ex["honesty"][0]
          and "actual direction" not in (ex.get("construction") or ""),
          ex["honesty"][0][:160])


def site_combiner_checks(reports, out_dir, expect_curve, tag):
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    t0 = time.time()
    p = subprocess.run([sys.executable, SITE_PY] + list(reports)
                       + ["--out", out_dir], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, env=clean_env())
    timed("site_exclusion %s" % tag, t0)
    hits = glob.glob(os.path.join(out_dir, "site_exclusion_*.json"))
    site = None
    if p.returncode == 0 and hits:
        with open(hits[0], encoding="utf-8") as fh:
            site = json.load(fh)
    ok = check("%s: site_exclusion.py exit 0 over %d reports and wrote its "
               "JSON" % (tag, len(reports)), site is not None,
               (p.stdout + p.stderr).decode("utf-8", "replace")[-500:])
    if not ok:
        return
    comb = site.get("combined") or {}
    check("%s: %d sessions used, none skipped, headline_curve '%s' at the "
          "site, the combined and the curve level, rule naming the basis"
          % (tag, len(reports), expect_curve),
          site["n_sessions"] == len(reports) and not site["skipped"]
          and site["headline_curve"] == expect_curve
          and comb.get("headline_curve") == expect_curve
          and (site.get("curve") or {}).get("headline_curve") == expect_curve
          and isinstance(site.get("rule"), str) and site["rule"],
          (site["n_sessions"], site["skipped"], site["headline_curve"],
           comb.get("headline_curve"), site["rule"][:200]))
    if expect_curve == "g90_wind_conservative":
        g = [s.get("g90_wind_conservative_best_gev_inv") for s in
             site["sessions"]]
        check("%s: every session carries g90_wind_conservative_best and the "
              "site headline equals the smallest of them up to the site "
              "grid's resampling (each node the weaker of its bracketing "
              "native points: never below, at most 1%% above), no session "
              "listed as missing the wind model" % tag,
              all(num(x) and x > 0 for x in g)
              and not site.get("wind_sessions_missing")
              and num(comb.get("g90_wind_conservative_best_gev_inv"))
              and min(g) * (1.0 - 1e-9)
              <= comb["g90_wind_conservative_best_gev_inv"]
              <= min(g) * 1.01,
              (g, comb.get("g90_wind_conservative_best_gev_inv"),
               site.get("wind_sessions_missing")))
    else:
        check("%s: the fallback names the session without a wind model in "
              "wind_sessions_missing and the rule says g90_worst" % tag,
              len(site.get("wind_sessions_missing") or []) == 1
              and "g90_worst" in site["rule"],
              (site.get("wind_sessions_missing"), site["rule"][:200]))


def tier_b(fr, work, full):
    reports = {}
    # ---- (i) recovery, (v) option B, HTML  [quick]
    print("--- B1. recovery: 240 rows, 12 h from 07:00 UTC at Oulu, bump "
          "a = 1.5, --inject-modulation %g (Sec. 7 (vi)) ---" % INJECT_P_A)
    bdir = os.path.join(work, "bundles")
    rec_zip = gen_bundle(bdir, BUMP + TIMED + ["--inject-modulation",
                                              "%g" % INJECT_P_A], "recovery")
    if rec_zip:
        meta = bundle_meta(rec_zip)
        mod = meta["injection_truth"]["modulation"]
        check("B1 generator: injection_truth.modulation records the MC "
              "response template, P_A %g, the offset, f_rows for 240 rows, "
              "equivalent_P_a_by_offset, and the report's MC constants "
              "(2e6 samples, seed 20200529, 2 deg bins)" % INJECT_P_A,
              mod["template"] == "mc_response"
              and mod["P_a_counts2"] == INJECT_P_A
              and len(mod["f_rows"]) == 240
              and isinstance(mod.get("equivalent_P_a_by_offset"), dict)
              and mod["nsamp"] == fr.EXCL_MC_SAMPLES
              and mod["seed"] == fr.EXCL_MC_SEED and mod["bin_deg"] == 2.0
              and meta["facility"]["latitude_deg"] == 65.0
              and meta["spectrometer"]["b0_orientation"] == "vertical"
              and len(meta["clock_audit"]["blocks"]) >= 1,
              {k: mod.get(k) for k in ("template", "P_a_counts2", "offset_hz",
                                       "nsamp", "seed", "bin_deg")})
        rdir = os.path.join(work, "report_recovery")
        rep, log = run_report(rec_zip, rdir, "recovery")
        if rep:
            reports["recovery"] = (rdir, rep)
            smo = sidereal_object_checks(fr, rep, "B1 recovery")
            if smo:
                pull, pa, sig, eq = pull_of(smo, meta)
                print("       recovery: fit.P_a = %+.6g +/- %.5g counts^2, "
                      "equivalent P_a at %+.0f Hz = %s, PULL = %s sigma; "
                      "P_A / sigma = %.1f; without regressors %+.6g +/- %.5g; "
                      "anti-phase %+.6g +/- %.5g; rows used %d/%d; chi2_dof %.3f"
                      % (pa, sig, smo["template"]["offset_hz"],
                         "%.6g" % eq if eq is not None else "n/a",
                         "%+.2f" % pull if pull is not None else "n/a",
                         INJECT_P_A / sig,
                         smo["fit"].get("P_a_without_regressors_counts2") or 0,
                         smo["fit"].get("sigma_Pa_without_regressors_counts2") or 0,
                         smo["antiphase_check"]["P_a_counts2"],
                         smo["antiphase_check"]["sigma_counts2"],
                         smo["rows_used"], smo["rows_with_line_power"],
                         smo["fit"]["chi2_dof"]))
                check("B1 recovery 7(vi): |fit.P_a - equivalent_P_a(template."
                      "offset_hz)| <= 1.5 sigma(P_a) (pull %s) with P_A / "
                      "sigma between 4 and 20"
                      % ("%+.2f" % pull if pull is not None else "n/a"),
                      pull is not None and abs(pull) <= 1.5
                      and 4.0 <= INJECT_P_A / sig <= 20.0,
                      (pull, pa, sig, eq))
                check("B1 recovery 7(vi): the injected excess is significant "
                      "(P_a > 4 sigma), the anti-phase template f(t + 12 h) "
                      "flips its sign on the 12 h window, and the one-sided "
                      "bound exceeds the injection",
                      pa > 4.0 * sig
                      and smo["antiphase_check"]["P_a_counts2"] < 0.0
                      and smo["bound"]["P90_mod_counts2"] > eq,
                      (pa / sig, smo["antiphase_check"]["P_a_counts2"],
                       smo["bound"]["P90_mod_counts2"]))
                html_checks(rdir, smo, "B1 recovery")
            option_b_checks(rep, "B1 recovery")
    # ---- (iv) the default 16-row untimed bundle  [quick]
    print("--- B2. skip path: the default 16-row untimed bundle (city "
          "Nowhere) ---")
    def_zip = gen_bundle(bdir, BUMP, "default-16")
    if def_zip:
        meta = bundle_meta(def_zip)
        check("B2 generator: the untimed default still writes city Nowhere, "
              "no coordinates, b0_orientation vertical, no clock_audit",
              meta["facility"].get("city") == "Nowhere"
              and "latitude_deg" not in meta["facility"]
              and meta["spectrometer"].get("b0_orientation") == "vertical"
              and not (meta.get("clock_audit") or {}).get("blocks"),
              (meta["facility"], meta["spectrometer"].get("b0_orientation")))
        rdir = os.path.join(work, "report_default16")
        rep, log = run_report(def_zip, rdir, "default-16")
        if rep:
            reports["default16"] = (rdir, rep)
            skip_report_checks(rep, "coordinates", "B2 default-16")
            check("B2 default-16: 16 noise rows (< 30) -- the coordinates "
                  "skip fires first, the n < 30 rule is never reached",
                  len(rep["science"]["noise"]["per_row"]) == 16
                  and rep["science"]["sidereal_modulation"]["skip_reason"]
                  == "wind_model_unavailable: coordinates")
    # ---- (vi) site combiner, mixed (fallback)  [quick]
    if "recovery" in reports and "default16" in reports:
        print("--- B3. site combiner: recovery (wind model) + default-16 (no "
              "wind model) -> g90_worst fallback ---")
        site_combiner_checks([os.path.join(reports["recovery"][0], "report.json"),
                              os.path.join(reports["default16"][0],
                                           "report.json")],
                             os.path.join(work, "site_mixed"), "g90_worst",
                             "B3 site mixed")
    if not full:
        print("--- (quick mode: null, lab cycle, orientation skip and the "
              "two-wind-session combiner need --full) ---")
        return
    # ---- (ii) null  [full]
    print("--- B4. null: the same bundle with --inject-modulation 0 ---")
    null_zip = gen_bundle(bdir, BUMP + TIMED + ["--inject-modulation", "0"],
                          "null")
    if null_zip:
        rdir = os.path.join(work, "report_null")
        rep, log = run_report(null_zip, rdir, "null")
        if rep:
            reports["null"] = (rdir, rep)
            smo = sidereal_object_checks(fr, rep, "B4 null")
            if smo:
                fit, anti = smo["fit"], smo["antiphase_check"]
                print("       null: P_a = %+.6g +/- %.5g (%+.2f sigma); "
                      "anti-phase %+.6g +/- %.5g (%+.2f sigma); P90_mod %.6g"
                      % (fit["P_a_counts2"], fit["sigma_Pa_counts2"],
                         fit["P_a_counts2"] / fit["sigma_Pa_counts2"],
                         anti["P_a_counts2"], anti["sigma_counts2"],
                         anti["P_a_counts2"] / anti["sigma_counts2"],
                         smo["bound"]["P90_mod_counts2"]))
                check("B4 null 7(vi): spin noise alone returns a null, |P_a| "
                      "< 2.5 sigma, and the anti-phase template too",
                      abs(fit["P_a_counts2"]) < 2.5 * fit["sigma_Pa_counts2"]
                      and abs(anti["P_a_counts2"]) < 2.5 * anti["sigma_counts2"],
                      (fit["P_a_counts2"] / fit["sigma_Pa_counts2"],
                       anti["P_a_counts2"] / anti["sigma_counts2"]))
                check("B4 null: the bound is the one-sided construction on "
                      "the null (P90_mod == max(P_a, 0) + t90 sigma within "
                      "1e-9)",
                      rel(smo["bound"]["P90_mod_counts2"],
                          max(fit["P_a_counts2"], 0.0)
                          + fit["t90"] * fit["sigma_Pa_counts2"]) < 1e-9)
            option_b_checks(rep, "B4 null")
    # ---- (vi) site combiner over two wind-aware sessions  [full]
    if "recovery" in reports and "null" in reports:
        print("--- B5. site combiner: two synthetic v0.8 reports -> headline "
              "g90_wind_conservative ---")
        site_combiner_checks([os.path.join(reports["recovery"][0], "report.json"),
                              os.path.join(reports["null"][0], "report.json")],
                             os.path.join(work, "site_wind"),
                             "g90_wind_conservative", "B5 site wind")
    # ---- (iii) lab cycle, separable phase  [full]
    print("--- B6. lab cycle: dip 0.35, --lab-cycle-pct 10 peaking at 16:00 "
          "UTC (9 h into the window) ---")
    lab_zip = gen_bundle(bdir, DIP + TIMED + [
        "--inject-modulation", "0", "--lab-cycle-pct", "10",
        "--lab-cycle-peak-utc", "2026-10-05T16:00:00Z"], "lab-cycle-16Z")
    if lab_zip:
        lc = bundle_meta(lab_zip)["injection_truth"].get("lab_cycle") or {}
        check("B6 generator: injection_truth.lab_cycle records 10% with the "
              "requested peak 2026-10-05T16:00:00Z on floor and dip depth",
              lc.get("pct") == 10.0
              and lc.get("peak_utc") == "2026-10-05T16:00:00Z"
              and sorted(lc.get("applied_to") or []) == ["dip_depth", "floor"],
              lc)
        rdir_n = os.path.join(work, "report_lab16_noreg")
        rep_n, log = run_report(lab_zip, rdir_n, "lab-cycle-16Z",
                                SPINNOISE_SIDEREAL_REGRESSORS="none")
        rdir_r = os.path.join(work, "report_lab16_reg")
        rep_r, log = run_report(lab_zip, rdir_r, "lab-cycle-16Z")
        sn = rep_n["science"]["sidereal_modulation"] if rep_n else None
        sr = rep_r["science"]["sidereal_modulation"] if rep_r else None
        if sn and sn.get("available"):
            fit = sn["fit"]
            z = fit["P_a_counts2"] / fit["sigma_Pa_counts2"]
            print("       lab 16Z, regressors none: P_a = %+.6g +/- %.5g "
                  "(%+.2f sigma), solar_degeneracy %s, regressors %s"
                  % (fit["P_a_counts2"], fit["sigma_Pa_counts2"], z,
                     sn["solar_degeneracy"], fit.get("regressors")))
            check("B6 lab cycle 7(vi), SPINNOISE_SIDEREAL_REGRESSORS=none: "
                  "the cycle biases P_a by >= 3 sigma and solar_degeneracy "
                  "reads 'unbroken' with no regressor in the design",
                  abs(z) >= 3.0 and sn["solar_degeneracy"] == "unbroken"
                  and not fit.get("regressors") and fit["n_params"] == 2
                  and (fit.get("regressor_check") or {}).get("env_switch_off")
                  is True,
                  (z, sn["solar_degeneracy"], fit.get("regressors"),
                   (fit.get("regressor_check") or {}).get("env_switch_off")))
        else:
            check("B6 lab cycle: regressor-free fit available", False,
                  (sn or {}).get("skip_reason"))
        if sr and sr.get("available"):
            fit = sr["fit"]
            rc = fit.get("regressor_check") or {}
            z = fit["P_a_counts2"] / fit["sigma_Pa_counts2"]
            print("       lab 16Z, floor regressor: P_a = %+.6g +/- %.5g "
                  "(%+.2f sigma), solar_degeneracy %s, VIF(f | regressors) "
                  "%.2f, inseparable %s, sigma ratio with/without %.2f"
                  % (fit["P_a_counts2"], fit["sigma_Pa_counts2"], z,
                     sr["solar_degeneracy"], rc.get("vif_f_regressor_set")
                     or float("nan"), rc.get("inseparable_from_template"),
                     rc.get("sigma_Pa_ratio_with_over_without")
                     or float("nan")))
            check("B6 lab cycle 7(vi), smoothed floor regressor: |P_a| < 1.5 "
                  "sigma, solar_degeneracy 'regressed', the regressor in the "
                  "design (p = 3), VIF(f | regressors) < 20 (separable phase, "
                  "peak 9 h from the session start)",
                  abs(z) < 1.5 and sr["solar_degeneracy"] == "regressed"
                  and fit["n_params"] == 3
                  and [r["name"] for r in fit["regressors"]]
                  == ["baseline_psd_at_line_smoothed"]
                  and num(rc.get("vif_f_regressor_set"))
                  and rc["vif_f_regressor_set"] < 20.0
                  and rc.get("inseparable_from_template") is False,
                  (z, sr["solar_degeneracy"], fit["n_params"],
                   rc.get("vif_f_regressor_set")))
            check("B6 lab cycle: P_a_without_regressors reproduces the "
                  "regressor-free run's P_a (1e-6) -- the same rows and "
                  "template either way",
                  sn is not None and sn.get("available")
                  and rel(fit["P_a_without_regressors_counts2"],
                          sn["fit"]["P_a_counts2"]) < 1e-6
                  and rel(fit["sigma_Pa_without_regressors_counts2"],
                          sn["fit"]["sigma_Pa_counts2"]) < 1e-6,
                  (fit.get("P_a_without_regressors_counts2"),
                   (sn or {}).get("fit", {}).get("P_a_counts2")))
        else:
            check("B6 lab cycle: regressed fit available", False,
                  (sr or {}).get("skip_reason"))
    # ---- (iii) lab cycle, inseparable phase (peak at the session start)
    print("--- B7. lab cycle peaking at the session start (collinear with "
          "the template over the half-day window) ---")
    ins_zip = gen_bundle(bdir, DIP + TIMED + ["--inject-modulation", "0",
                                              "--lab-cycle-pct", "10"],
                         "lab-cycle-07Z")
    if ins_zip:
        rdir = os.path.join(work, "report_lab07_reg")
        rep, log = run_report(ins_zip, rdir, "lab-cycle-07Z")
        si = rep["science"]["sidereal_modulation"] if rep else None
        if si and si.get("available"):
            fit = si["fit"]
            rc = fit.get("regressor_check") or {}
            print("       lab 07Z, floor regressor: P_a = %+.6g +/- %.5g; "
                  "without %+.6g +/- %.5g (%+.2f sigma); VIF %.1f; "
                  "solar_degeneracy %s"
                  % (fit["P_a_counts2"], fit["sigma_Pa_counts2"],
                     fit["P_a_without_regressors_counts2"],
                     fit["sigma_Pa_without_regressors_counts2"],
                     fit["P_a_without_regressors_counts2"]
                     / fit["sigma_Pa_without_regressors_counts2"],
                     rc.get("vif_f_regressor_set") or float("nan"),
                     si["solar_degeneracy"]))
            basis = si.get("solar_degeneracy_basis") or ""
            check("B7 lab cycle 7(vi): a cycle peaking at the session start "
                  "is flagged fit.regressor_check.inseparable_from_template "
                  "(VIF >= 20, also collinear_with_template) and the "
                  "regressor-free P_a is biased by >= 3 sigma; "
                  "solar_degeneracy_basis and the honesty list carry the "
                  "caveat that the degeneracy is NOT broken within this "
                  "session (solar_degeneracy reads 'inseparable', not "
                  "'regressed')",
                  si["solar_degeneracy"] == "inseparable"
                  and rc.get("inseparable_from_template") is True
                  and rc.get("collinear_with_template") is True
                  and rc.get("vif_f_regressor_set", 0) >= 20.0
                  and abs(fit["P_a_without_regressors_counts2"]) >= 3.0
                  * fit["sigma_Pa_without_regressors_counts2"]
                  and "not separable" in basis and "NOT broken" in basis
                  and any("NOT broken" in h for h in si.get("honesty") or []),
                  (rc.get("inseparable_from_template"),
                   rc.get("vif_f_regressor_set"), si["solar_degeneracy"],
                   basis[-200:]))
        else:
            check("B7 lab cycle (inseparable): fit available", False,
                  (si or {}).get("skip_reason"))
    # ---- (iv) orientation skip  [full]
    print("--- B8. skip path: --b0-orientation unknown on the 16-row default "
          "---")
    unk_zip = gen_bundle(bdir, BUMP + ["--b0-orientation", "unknown"],
                         "orientation-unknown")
    if unk_zip:
        rdir = os.path.join(work, "report_unknown16")
        rep, log = run_report(unk_zip, rdir, "orientation-unknown")
        if rep:
            skip_report_checks(rep, "orientation", "B8 orientation-unknown")


# ===========================================================================

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--full", action="store_true",
                    help="run every Tier B case (null, lab cycle separable "
                         "and inseparable, orientation skip, two-session "
                         "combiner); the default runs one 240-row case")
    ap.add_argument("--skip-e2e", action="store_true",
                    help="Tier A only (no bundles, seconds)")
    args = ap.parse_args(argv)
    work = args.out_dir or tempfile.mkdtemp(prefix="wind_sidereal_")
    if not os.path.isdir(work):
        os.makedirs(work)
    t_all = time.time()
    try:
        fr = load_report_module()
    except ImportError as exc:
        print("FAIL: cannot import analysis/facility_report.py (%s); the "
              "report needs numpy" % exc)
        return 1
    print("=== Tier A: unit checks (Sec. 7 (v), 8.2, 8.4, 9.2) ===")
    tier_a(fr)
    if not args.skip_e2e:
        print("=== Tier B: synthetic bundles through facility_report.py "
              "(Sec. 7 (vi)) %s ===" % ("[--full]" if args.full else
                                        "[quick: one 240-row case]"))
        tier_b(fr, work, args.full)
    print("")
    print("timings:")
    for label, dt in TIMINGS:
        print("  %7.1f s  %s" % (dt, label))
    print("  %7.1f s  total" % (time.time() - t_all))
    print("work dir: %s" % work)
    if FAILED:
        print("WIND + SIDEREAL: %d FAILED" % len(FAILED))
        for n in FAILED:
            print("  - %s" % n)
        return 1
    print("WIND + SIDEREAL: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
