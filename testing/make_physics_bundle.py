#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_physics_bundle.py -- synthesize a physically sensible spin-noise bundle
with a KNOWN injected feature, for injection-recovery validation of
analysis/facility_report.py.

    python3 testing/make_physics_bundle.py --feature bump --amp 1.5 --fwhm 12
    python3 testing/make_physics_bundle.py --feature dip  --amp 0.35
    python3 testing/make_physics_bundle.py --feature none            # null case
    python3 testing/make_physics_bundle.py --clock-offset 3e-7       # + clock audit

    # v0.8, a TIMED session for the Sec. 7 (vi) sidereal-modulation tests:
    # 240 rows every 180 s from 07:00 UTC at Oulu (the 12 h fall of the
    # wind template from its peak to its trough), a bump, plus a modulated
    # excess of P_A = 6.5e5 counts^2 x f(t_k; -420 Hz) per row, f the
    # report's own Monte-Carlo response template (8.4 sigma(P_a) on this
    # fixture; see the Sec. 7 (vi) record at the end of this docstring)
    python3 testing/make_physics_bundle.py --feature bump --amp 1.5 \\
        --noise-rows 240 --row-cadence-s 180 --start-utc 2026-10-05T07:00:00Z \\
        --site 65.0,25.5 --inject-modulation 6.5e5@-420
    # the same with --inject-modulation 0 is the spin-noise-only null; a
    # dip with --lab-cycle-pct 10 is the solar-day degeneracy case

Prints exactly one line on stdout: the bundle path (progress on stderr).

What it builds (network expno tree per topspin/INSTALL.md):
  1            setup (acqus only)
  10,14,15,16  RG ladder: small-flip 1Ds at RG 1/8/64/101, amplitude
               exactly linear in RG (plus noise)
  11           reference_open : 8-row small-flip pseudo-2D
  12           noise          : pseudo-2D, pure noise rows with the injected
                                absorptive+dispersive Lorentzian feature
  13           reference_close: as 11

The noise rows are synthesized EXACTLY in the frequency domain: each row's
two-sided PSD is floor * (1 + (a + b*u)/(1+u^2)), u = (f-f0)/(FWHM/2), so
the injected amplitude/width/asymmetry are known to numerical precision.
Data are written as little-endian int32 Bruker ser/fid files with real
acqus/acqu2s parameter files (GRPDLY transient included in the references).

meta.json declares run_mode 'synthetic-injection' (schema enum, v1.2):
the report generator runs its full science path on such bundles but
watermarks the report as validation, never as a measurement.

With --clock-offset F the bundle also carries a schema-1.2 clock_audit
object built on the PHYSICAL timing model: each block's recorded
ocxo_expected_s uses the acquisition-side formula rows*(AQ + n_d1*D1)
(mirroring spin_noise_run.py), while its wall-clock duration follows
the pulse-program texts written into each expno (see PP_TEXTS: the
30m/p1/DE terms the acquisition-side formula omits) times (1 + F), plus
a constant per-block overhead and ms-scale jitter. The report's
pulse-program-derived fit must recover F within its stated uncertainty,
and --expect-refined catches a dead refinement. --de-us sets DE (default
6.5 us, the stock value; crank it, e.g. 20000, to make the per-row DE
shortfall itself many sigma). Until v0.7.6 the references were Bruker's
zg2d, whose d20-computed pacing delay failed to compile on hardware
(Torino, 2026-09-25); the fixture's stand-in for it spent a second d1
per row that the recorded expectations omitted. The references are the
project's zgref2d and, since v0.7.7, both project sequences write their
row during the data-transfer delay d11 (1 s; Oulu's receiver unit refused
1 MB rows written in 30-50 ms, 2026-09-30): one d1 and one d11 per row,
both in the recorded expectation and in acqus (D[1], D[11]), so the only
shortfalls left are p1 + DE per scan, the 30 ms before zgref2d's row
loop, and zg's two 30m lines per pass.
The default session is short (~10 min of audited time), so the report
correctly flags the audit 'inconclusive (short session)' while
still reporting the fitted offset; that flag is part of what this bundle
validates.

Validation record (rerun 2026-08-25 at the committed DEFAULT SEED 20260825;
these supersede the numbers quoted in the message of commit 350907b, which
came from a different seed / pre-commit code state):
  bump, a=1.5, FWHM 12 Hz : recovered 1.591 +/- 0.021 (+6.1%),
                            FWHM 11.71 +/- 0.22 Hz (-2.4%)
  dip,  a=0.35, FWHM 12 Hz: recovered 0.357 +/- 0.021 (+2.1%),
                            FWHM 10.47 +/- 0.83 Hz (-12.8%)
  none (null)             : no detection; UL95 = 0.052 x floor
Acceptance criterion is amplitude recovery within 10% -- both features
pass. Known bias: dip WIDTH recovery runs ~13% low at this SNR while the
amplitude stays within ~2%; quote widths from dip fits with that caveat.

--------------------------------------------------------------------------
Timed sessions (v0.8; analysis_note_broadband_regime.md Sec. 7 (vi), for
the Sec. 8 wind model and the Sec. 9 sidereal-modulation fit).  Every
option below is additive: with none of them the bundle is byte-identical
to the v0.7.9 fixture (same seed, same random stream, same data files),
except that spectrometer.b0_orientation ('vertical') and a few extra
injection_truth keys are now written.

  --noise-rows N        rows of the noise block (default 16, unchanged).
                        The 240- and 960-row blocks of the v0.7.4
                        regression were made by editing N_NOISE_ROWS.
  --start-utc ISO       session start, e.g. 2026-10-05T07:00:00Z (an
                        offset such as +03:00 is accepted; a naive time
                        is UTC).  This engages the TIMED mode: every
                        block of the session is laid on a wall-clock
                        timeline from this start -- setup 120 s, the four
                        rungs, reference_open, noise, reference_close,
                        each block N x its per-row true duration (AQ + d1
                        [+ p1] + d11 + DE, from the same PP_TEXTS model as
                        the clock audit) x (1 + clock offset) + 0.18 s +
                        jitter, 2.5 s between blocks -- and meta.json
                        carries clock_audit.blocks[].wall_start_ms /
                        wall_end_ms for EVERY block whether or not
                        --clock-offset is given (the offset defaults to
                        0; given, it scales the durations exactly as
                        before), with experiments[].started_local /
                        finished_local and local_timezone_offset_min
                        consistent with them (local = UTC + --tz-offset-min,
                        default 0) and created_utc = the session's end.
                        Without --start-utc the behaviour is the old one:
                        started_local = finished_local = now, offset 0, a
                        clock audit only with --clock-offset, on its
                        arbitrary 2026-08 epoch.
  --tz-offset-min M     local_timezone_offset_min and the zone of the
                        started_local/finished_local strings (timed mode;
                        default 0 = UTC; Oulu in October is 180).
  --row-cadence-s C     wall seconds from one noise row to the next
                        (default the acquisition-derived AQ + d1 + d11 +
                        DE = 20.05 s at the stock geometry).  A longer
                        cadence is realised PHYSICALLY as a longer
                        zgnoise2d relaxation delay d1 = C - (AQ + d11 +
                        DE), written to the noise expno's acqus D[1] and
                        counted in the recorded ocxo_expected_s, so the
                        clock audit stays self-consistent (the report's
                        pulse-program fit still recovers the injected
                        offset).  C below the stock cadence is refused.
  --site LAT,LON        facility.latitude_deg / facility.longitude_east_deg
                        (degrees, longitude EAST-positive) with
                        facility.coordinates_basis 'operator' (Sec. 8.4 a).
  --city NAME           facility.city = NAME, which must resolve in the
                        registry's gazetteer (analysis/registry_report.py,
                        resolve_coords, loaded by file path) unless --site
                        gives the coordinates; the default city stays
                        'Nowhere', which resolves nowhere, so a default
                        synthetic keeps skipping the wind model on
                        'coordinates' (Sec. 7 (vi)).
  --b0-orientation      spectrometer.b0_orientation: vertical (default,
                        standard-bore), horizontal (MRI nodes; writes
                        spectrometer.b0_azimuth_deg from --b0-azimuth-deg,
                        default 0 = north, east 90) or unknown.
  --inject-modulation P_A[@OFFSET_HZ]
                        a modulated excess: row k gets EXTRA line power
                        P_A x f(t_k; OFFSET) counts^2 (the report's units,
                        per_row[].line_power_fixed_shape_counts2 = a pi w/2
                        x floor), with the injected line's shape (same f0,
                        FWHM and dispersive fraction b/a, i.e. the shape
                        the Sec. 9.2 fixed-shape estimator uses), where f
                        is the Sec. 9.1 SIGNAL-MODEL template at the row's
                        mid-time, the session's site and B0 orientation:
                        f(t_k; Delta) = xi^2(theta_bin(t_k), V; Delta) /
                        <xi^2>_rows, the exclusion's response integral
                        Int lam(nu; theta, V) / (Gamma^2 + (2 pi (Delta +
                        nu))^2) dnu at the scan offset Delta = OFFSET from
                        the Monte-Carlo lineshape at the row's 2 degree
                        wind-angle bin, every bin at the session-mean
                        |v_lab| V (Sec. 8.3), normalised to its row mean --
                        the template analysis/sidereal_modulation.py's
                        TemplateBank fits, built the same way: the
                        lineshape is analysis/halo_wind.alp_lineshape_angle
                        loaded by FILE PATH (the registry_report pattern
                        below; Sec. 7 (v) pins it bit-for-bit to the
                        report's alp_lineshape at 0 and 90 degrees, so the
                        fixture shares the one lineshape routine with the
                        report and nothing else) on the exclusion's random
                        stream (EXCL_MC_SAMPLES 2000000, EXCL_MC_SEED
                        20200529), its offset grid (0..6000 Hz by 2 Hz),
                        the fixture's line nu_a = h1_freq_mhz x 1e6 + F0_HZ
                        and Gamma = pi x --fwhm (the report fits Gamma from
                        the measured FWHM, 39.7 against 37.7 s^-1 on the
                        stock fixture, a sub-percent effect on f).  The
                        wind angle theta(t) = angle between -v_lab(t) and
                        B0 is the generator's own port of the 2026-10-06
                        prototype (superseded in the repository by
                        analysis/halo_wind.py; kept as an independent copy
                        here so the fixture shares only the lineshape routine
                        with the report: Sun + Earth orbital velocity,
                        galactic -> J2000, IAU 1982 GMST, the horizontal-B0
                        axis of Sec. 8.2), which agrees with
                        halo_wind.wind_angle_deg to 0.0 degrees over a day
                        at Oulu.
                        OFFSET (Hz, nu_a - nu_L; default: chosen) is the
                        scan offset the injected template is evaluated at.
                        The default is the WIND-AWARE BEST OFFSET for the
                        fixture's line: the node of the exclusion's scan
                        grid (EXCL_SCAN_HZ, -4000..+396 Hz by 4 Hz) where
                        the session-mean response <xi^2>_rows(Delta) is
                        largest, which is where the report's wind-aware
                        nominal curve g90_wind_nominal(Delta) = sqrt(P_90 /
                        (kappa M0^2 <xi^2>(Delta))) has its minimum (P_90
                        is one number for all Delta), i.e. the report's
                        result.offset_at_best_wind_nominal_hz up to the
                        report's fitted Gamma and nu_L: -420 Hz for the
                        stock fixture (12 Hz line at 600.13 MHz, Oulu, the
                        07:00 UTC 12 h window), the offset the report
                        selected on every such bundle so far.  The report
                        quotes template.offset_hz / fit.P_a_counts2 at ITS
                        best offset, which is data-chosen, so the truth
                        also carries equivalent_P_a_by_offset (below) for
                        a test to compare like with like wherever the
                        report lands.
                        Why the MC template and not the closed form: the
                        closed form (v0^2 + V^2 sin^2 theta)/mean is the
                        total transverse power, which the narrow spin line
                        never samples whole; Sec. 9.1 prints it as the
                        intuition only.  On this 12 h Oulu window a
                        closed-form injection fits with the report's MC
                        template as 1.14 x P_A at -420 Hz, 1.36 x at -324
                        Hz and 2.5 x at 0 Hz (corr 0.9994 -- a pure
                        amplitude mismatch, std 0.194 against 0.170), so
                        the Sec. 7 (vi) 'recovered within one sigma'
                        criterion would be unmeetable above P_A/sigma(P_a)
                        of about 7 for a reason unrelated to the fit.
                        --inject-modulation-template closed_form keeps the
                        closed-form injection for that intuition case; the
                        truth then records closed_form_slope and the
                        equivalent P_a table so the mismatch is pinned, not
                        hidden.  Requires --start-utc, a site and a known
                        B0 orientation.
                        Row k's mid-time is wall_start_ms of the noise
                        block + (k + 1/2) x the row's wall duration, which
                        is what the report's 'block_spread' placement
                        (Sec. 8.4 c) reconstructs from the audit.
  --inject-modulation-template {mc_response,closed_form}
                        which f the excess follows (default mc_response,
                        above).  closed_form: f = (v0^2 + V^2 sin^2
                        theta(t_k)) / <.>_rows of the prototype shm_wind.py;
                        the MC fields of the truth are still written, so a
                        test can divide the known mismatch out.
  --lab-cycle-pct P     a 24 h laboratory cycle: the floor AND the dip
                        depth of row k are both multiplied by
                        1 + (P/100) cos(2 pi (t_k - t_peak) / 24 h), so the
                        line power in counts^2 moves by about 2P percent.
                        t_peak defaults to the session start (worst case
                        for a run that starts at a template extremum,
                        Sec. 9.3 c); --lab-cycle-peak-utc ISO moves it.
                        The modulated excess above is NOT scaled by the
                        cycle (it is an absolute P_A f(t_k)).
  injection_truth records all of it: noise_rows, session {start_utc,
  end_utc, tz_offset_min, row_cadence_s, noise_d1_s, clock offset},
  site, b0_orientation, lab_cycle {pct, peak_utc, per-row factor} and
  modulation {P_a_counts2, template ('mc_response' | 'closed_form'),
  offset_hz, offset_basis, nu_a_hz, gamma_per_s, nsamp, seed, bin_deg,
  grid_hz, scan_hz, vlab_mean_kms, v0_kms, row_mid_utc_ms, theta_deg_rows,
  f_rows (the template INJECTED, whichever it is) with f_min/f_max/f_std,
  f_mc_rows and f_mc_std (the MC template at offset_hz), f_closed_rows and
  f_closed_std (the closed form), closed_form_slope = OLS slope of
  f_closed on f_mc at offset_hz (what a closed-form injection is recovered
  as by the report's fit there; 1.14 on the stock fixture, the intuition
  only), extra_power_counts2_rows, and equivalent_P_a_by_offset: for every
  4 Hz node from offset_hz - 200 to offset_hz + 200 the value P_A x
  cov(f_injected, f_mc(Delta)) / var(f_mc(Delta)) -- the P_a the report's
  two-parameter fit at Delta recovers from this bundle (equal to P_A at
  offset_hz for an mc_response injection) -- keyed by the offset as a
  string ('-420'), with equivalent_P_a_slope_by_offset the slopes
  themselves.  A test reads the report's template.offset_hz, looks the
  equivalent P_a up and asserts fit.P_a_counts2 within one sigma of it
  (or curve.P_a_counts2 at nu_a_minus_nu_L_hz == offset_hz against
  P_a_counts2 directly).

Sec. 7 (vi) record (2026-10-08, this generator through
analysis/facility_report.py with the default fixed-shape, floor-normalised
estimator and its default regressor set; 240 rows every 180 s from
2026-10-05T07:00:00Z at --site 65.0,25.5, seed 20260825, the report's
wind-aware best offset -420 Hz = the injected offset on every bundle;
reproduced by testing/test_wind_sidereal.py, quick tier for the recovery,
--full for the rest).
  recovery  --feature bump --amp 1.5 --inject-modulation 6.5e5 (MC
            template, default offset -420 Hz; a_k stays 1.70-1.83):
            fit.P_a = +641646 +/- 77603 counts^2, pull -0.11 sigma against
            equivalent_P_a_by_offset['-420'] = P_A (P_A/sigma = 8.4);
            without the regressor +639535 +/- 77383 (-0.14); the
            estimator-level OLS of per_row[].line_power_fixed_shape_counts2
            (floor-normalised) on the truth f_rows gives +639402 +/- 77432
            (-0.14); chi2_dof 0.97; the curve at -420 Hz is the fit.
  null      the same bump with --inject-modulation 0:
            fit.P_a = -2736 +/- 55837 (-0.05 sigma), P_90,mod = 71758,
            anti-phase template -6011 +/- 60132 (-0.10 sigma).
  lab cycle --feature dip --amp 0.35 --inject-modulation 0 --lab-cycle-pct
            10 (peak at the session start, the worst case):
            SPINNOISE_SIDEREAL_REGRESSORS=none -> P_a = -101127 +/- 17408
            (-5.81 sigma), solar_degeneracy 'unbroken';
            regressors on (the 1.5 h-smoothed floor, corr(regressor, f) =
            0.996, VIF(f | regressors) 133.7) -> P_a = -44071 +/- 201640
            (-0.22 sigma, sigma(P_a) 11.6x the regressor-free value),
            solar_degeneracy 'inseparable': the regressor records the
            degeneracy, it does not break it.
            The separable case, --lab-cycle-peak-utc 2026-10-05T16:00:00Z
            (9 h into the window): regressors none -> +88063 +/- 18358
            (+4.80 sigma), 'unbroken'; floor regressor -> -584 +/- 37930
            (-0.02 sigma), 'regressed', VIF 4.38.
  A closed-form injection (--inject-modulation-template closed_form) is
  recovered as closed_form_slope x P_A = 1.14 P_A at -420 Hz by
  construction; the truth table makes that comparison explicit.

Python 3 + numpy only (3.8-compatible); analysis/halo_wind.py and
analysis/registry_report.py are loaded by file path when the options that
need them are given (never for the untimed default bundle).
"""

from __future__ import print_function

import argparse
import datetime
import hashlib
import importlib.util
import io
import json
import math
import os
import random
import sys
import time
import zipfile

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

FACILITY_SLUG = "injection-test"

# Network acquisition geometry (mirrors topspin/spin_noise_run.py)
SW_HZ = 6900.0
TD_ROW = 262144            # real+imag ints per row -> 131072 complex points
TD_LADDER = 16384
N_NOISE_ROWS = 16
REF_ROWS = 8
GRPDLY = 67.984
F0_HZ = -812.0             # injected line offset from carrier ("water offset")
H1_FREQ_MHZ = 600.13       # spectrometer.h1_freq_mhz / acqus SFO1; the line
                           # sits at nu_a = H1_FREQ_MHZ x 1e6 + F0_HZ
RG_NOISE = 101.0
RG_REF = 25.0
RG_LADDER = [(10, 1.0), (14, 8.0), (15, 64.0), (16, 101.0)]
FLOOR_C2HZ = 40000.0       # flat noise floor, counts^2/Hz (comfortably int32)
REF_A0 = 2.0e6             # injected reference amplitude, counts
REF_DECAY_S = 3.0          # reference decay rate 1/s (line ~1 Hz + inhomog.)
REF_FWHM_HZ = 6.0          # reference line FWHM via extra Lorentzian decay
D1_REF_S = 2.0             # relaxation delay, references/ladder (run script)
D1_NOISE_S = 0.05          # zgnoise2d loop delay (once per row, before go)
D11_TRANSFER_S = 1.0       # the data-transfer delay of the 'd11 wr' line in
                           # zgnoise2d AND zgref2d (the run script's
                           # D11_TRANSFER_S; written to acqus D[11] and part
                           # of the RECORDED model)
REF_PRE_S = 0.030          # zgref2d's 30m before its row loop: once per
                           # block, not in the recorded model
DE_US_DEFAULT = 6.5        # stock pre-acquisition delay DE, microseconds
P1_US = 10.0               # small-flip pulse length in the pulsed sequences

# Clock-audit / timeline model (session_timeline, build_clock_audit)
AUDIT_EPOCH_MS = 1787000000000   # the untimed audit's arbitrary 2026-ish epoch
SETUP_WALL_MS = 120000           # setup block: tune/shim/dialogs, wall only
BLOCK_OVERHEAD_MS = 180.0        # constant per-block overhead (disk writes)
BLOCK_GAP_MS = 2500              # inter-block gap (dataset switching)

# Standard-halo-model wind (Sec. 8.2; ported from the prototype shm_wind.py
# so the generator's template is the prototype's closed form exactly)
SHM_V0_KMS = 220.0                                   # 2 sigma^2 = v0^2
V_SUN_GAL = np.array([11.1, 220.0 + 12.24, 7.25])    # km/s, galactic (U, V, W)
V_EARTH_KMS = 29.79
EPS1_GAL = np.array([0.9931, 0.1170, -0.01032])      # Earth velocity direction at the March equinox
EPS2_GAL = np.array([-0.0670, 0.4927, -0.8676])      # ... at the June solstice
GAL2EQ = np.array([[-0.054876, 0.494109, -0.867666],  # galactic -> equatorial J2000
                   [-0.873437, -0.444830, -0.198076],
                   [-0.483835, 0.746982, 0.455984]])
UTC = datetime.timezone.utc
J2000 = datetime.datetime(2000, 1, 1, 12, tzinfo=UTC)
MARCH_EQUINOX_DOY = 79.5          # ~March 21 00:00 UT (fraction of year since Jan 1)
TROPICAL_YEAR_D = 365.2422
B0_ORIENTATIONS = ("vertical", "horizontal", "unknown")

# The exclusion's Monte-Carlo stream, grids and binning, mirrored from
# analysis/facility_report.py (EXCL_MC_SAMPLES, EXCL_MC_SEED,
# EXCL_LINESHAPE_GRID_HZ, EXCL_SCAN_HZ, WIND_THETA_BIN_DEG) so the injected
# Sec. 9.1 template is the one analysis/sidereal_modulation.TemplateBank
# fits; every value is written into injection_truth.modulation so a test
# can assert it against the report's.  Importing the report for them would
# pull matplotlib into the fixture; the copies are deliberate.
EXCL_MC_SAMPLES = 2000000
EXCL_MC_SEED = 20200529
EXCL_LINESHAPE_GRID_HZ = (0.0, 6000.0, 2.0)      # offsets above nu_a
EXCL_SCAN_HZ = (-4000.0, 400.0, 4.0)             # nu_a - nu_L: start, stop, step
WIND_THETA_BIN_DEG = 2.0                         # Sec. 8.3 angle bins
EQUIV_HALF_SPAN_HZ = 200.0                       # equivalent_P_a_by_offset span
EQUIV_STEP_HZ = 4.0                              # ... and step (the scan step)
MODULATION_TEMPLATES = ("mc_response", "closed_form")


def _project_pp(name):
    """The shipped pulse program topspin/pp/<name>, as bytes.  Read from
    the repository so the fixture can never drift from what the run
    script installs (static_check pins the script's embedded copies to
    the same files)."""
    with open(os.path.join(REPO, "topspin", "pp", name), "rb") as fh:
        return fh.read()


# Pulse-program texts written into each expno, mirroring what real
# TopSpin stores in data/<expno>/pulseprogram. The report's clock-audit
# refinement parses these texts (NOT a name-keyed table) to derive each
# block's true OCXO duration, so the fixture's wall clocks below are
# built from the same structures. zgnoise2d and zgref2d are the shipped
# sequences themselves (d1 + d11 per row; d1 + p1 + d11 per row plus one
# 30m before the loop -- and zgref2d's "acqt0=..." definition, which the
# report's parser must take as zero duration; the parser resolves d11
# from acqus D[11] like any dN); the zg stand-in carries the library
# sequence's 30m loop/write delays and the same acqt0 line.
PP_TEXT_ZGNOISE2D = _project_pp("zgnoise2d")
PP_TEXT_ZGREF2D = _project_pp("zgref2d")
PP_TEXT_ZG = (
    ";zg (fixture stand-in for the library 1D sequence, with its 30m\n"
    ";loop and write delays and its acqt0 definition)\n"
    "#include <Avance.incl>\n"
    "\"acqt0=-p1*2/3.1416\"\n"
    "1 ze\n"
    "2 30m\n"
    "  d1\n"
    "  p1 ph1\n"
    "  go=2 ph31\n"
    "30m mc #0 to 2 F0(zd)\n"
    "exit\n"
    "ph1=0\n"
    "ph31=0\n").encode("ascii")
PP_TEXTS = {"zg": PP_TEXT_ZG, "zgref2d": PP_TEXT_ZGREF2D,
            "zgnoise2d": PP_TEXT_ZGNOISE2D}


def info(msg):
    print(msg, file=sys.stderr)


def acqus_text(td, sw, rg, o1=0.0, pulprog="zgnoise2d", grpdly=GRPDLY,
               d1_s=D1_NOISE_S, de_us=DE_US_DEFAULT, p1_us=P1_US,
               d11_s=D11_TRANSFER_S):
    # D and P arrays formatted like real Bruker acqus: "(0..63)" header,
    # values on continuation lines (element 1 carries D1/P1, element 11
    # the data-transfer delay D11 the run script writes; rest zero).
    # FRQLO3 is a DBL_MAX sentinel: real consoles write these for unset
    # doubles, and parse_jcamp must survive them (regression coverage
    # for the OverflowError found in review against the 2020 dataset).
    d_vals = ["0"] * 64
    d_vals[1] = "%.6g" % d1_s
    d_vals[11] = "%.6g" % d11_s
    d_line = " ".join(d_vals)
    p_line = "0 %.6g " % p1_us + " ".join(["0"] * 62)
    return (
        "##TITLE= Parameter file, synthetic injection bundle\n"
        "##$PULPROG= <%s>\n"
        "##$TD= %d\n"
        "##$SW_h= %.10g\n"
        "##$SFO1= 600.13\n"
        "##$FRQLO3= 1.79769313486232e+308\n"
        "##$O1= %.6g\n"
        "##$RG= %.6g\n"
        "##$NS= 1\n"
        "##$DS= 0\n"
        "##$DE= %.6g\n"
        "##$D= (0..63)\n"
        "%s\n"
        "##$P= (0..63)\n"
        "%s\n"
        "##$BYTORDA= 0\n"
        "##$DTYPA= 0\n"
        "##$DECIM= 1664\n"
        "##$DSPFVS= 20\n"
        "##$GRPDLY= %.6g\n"
        "##END=\n" % (pulprog, td, sw, o1, rg, de_us, d_line, p_line,
                      grpdly)
    ).encode("ascii")


def acqu2s_text(rows):
    return ("##TITLE= Parameter file F1, synthetic\n"
            "##$TD= %d\n##END=\n" % rows).encode("ascii")


def to_bruker_int32(rows_complex):
    """Interleave re/im as little-endian int32, pad rows to 1024 bytes."""
    out = io.BytesIO()
    for row in rows_complex:
        v = np.empty(row.size * 2, dtype="<i4")
        v[0::2] = np.clip(np.round(row.real), -2**31 + 1, 2**31 - 1)
        v[1::2] = np.clip(np.round(row.imag), -2**31 + 1, 2**31 - 1)
        b = v.tobytes()
        pad = (-len(b)) % 1024
        out.write(b + b"\x00" * pad)
    return out.getvalue()


def synth_noise_row(rng, n, fs, floor_c2hz, a, b, f0, fwhm):
    """Exact frequency-domain synthesis: complex time series whose two-sided
    PSD is floor*(1 + (a + b*u)/(1+u^2)), u=(f-f0)/(fwhm/2)."""
    f = np.fft.fftfreq(n, d=1.0 / fs)
    shape = np.ones(n)
    if a != 0.0 or b != 0.0:
        u = (f - f0) / (fwhm / 2.0)
        shape = shape + (a + b * u) / (1.0 + u ** 2)
    shape = np.clip(shape, 0.05, None)     # keep PSD positive for deep dips
    psd = floor_c2hz * shape
    # X_k with <|X_k|^2> = psd_k * fs * n  ->  ifft gives the series
    amp = np.sqrt(psd * fs * n / 2.0)
    X = amp * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return np.fft.ifft(X)


def synth_reference_row(rng, n, fs, a0, f0, fwhm, decay_s, floor_c2hz):
    """Small-flip FID: group-delay transient (zeros) + decaying complex
    exponential at f0 with Lorentzian width, over the same noise floor."""
    g = int(round(GRPDLY))
    t = np.arange(n - g) / fs
    lam = decay_s + math.pi * fwhm          # total amplitude decay rate
    sig = a0 * np.exp((2j * math.pi * f0 - lam) * t)
    noise = synth_noise_row(rng, n, fs, floor_c2hz, 0.0, 0.0, 0.0, 1.0)
    x = noise.copy()
    x[g:] += sig
    x[:g] *= 0.1                            # crude filter-transient stand-in
    return x


# --------------------------------------------------------------------------
# Session timeline / clock audit
# --------------------------------------------------------------------------

def noise_row_true_s(d1_noise_s=D1_NOISE_S, de_us=DE_US_DEFAULT):
    """OCXO seconds one zgnoise2d row really takes: AQ + d1 + d11 (the
    recorded model) + DE (the per-row shortfall the pulse-program text
    carries).  Times (1 + clock offset) it is the row's wall cadence."""
    return TD_ROW / 2 / SW_HZ + d1_noise_s + D11_TRANSFER_S + de_us * 1e-6


def session_timeline(offset, rng, de_us=DE_US_DEFAULT,
                     start_ms=AUDIT_EPOCH_MS, n_noise_rows=N_NOISE_ROWS,
                     d1_noise_s=D1_NOISE_S):
    """The session's blocks on a wall-clock timeline from start_ms, as
    schema-1.2 clock_audit block entries with a KNOWN injected fractional
    clock offset: each acquisition block's wall duration is its OCXO-implied
    duration times (1 + offset), plus a constant 0.18 s per-block overhead
    (disk writes; the fit's intercept must absorb it) and +/-4 ms jitter
    (NTP timestamp granularity). A setup block with no OCXO prediction is
    included to exercise the fit's exclusion path.

    PHYSICAL timing model (mirrors the fixture PP_TEXTS, which the
    report's refinement parses): the RECORDED ocxo_expected_s is the
    acquisition-side formula rows*(AQ + D1 + fixed), exactly like
    spin_noise_run.py's ocxo_expected_s through acquire_block (ladder:
    one d1, fixed 0; zgref2d references and zgnoise2d noise: one d1 and
    the transfer delay d11 as the fixed term) -- while the TRUE (wall)
    duration follows the pulse-program text: the zg ladder additionally
    spends 2x30m + p1 + DE per pass, the zgref2d references p1 + DE per
    row plus one 30m before the row loop, and zgnoise2d DE per row. With
    the stock DE these shortfalls are tiny; --de-us 20000 makes them many
    sigma, which is what the harness's discrimination case exercises. The
    report's pulse-program-derived fit removes the shortfalls; its
    recorded-model comparison fit keeps them.  d1_noise_s is the noise
    block's relaxation delay (--row-cadence-s realises a longer cadence
    through it; it is in acqus D[1] and in the recorded model alike)."""
    de_s = de_us * 1e-6
    p1_s = P1_US * 1e-6
    aq_lad = TD_LADDER / 2 / SW_HZ
    aq_row = TD_ROW / 2 / SW_HZ
    lad_rec = aq_lad + D1_REF_S
    lad_true = aq_lad + D1_REF_S + p1_s + de_s + 0.060   # two 30m lines
    ref_rec = aq_row + D1_REF_S + D11_TRANSFER_S
    ref_true = aq_row + D1_REF_S + p1_s + de_s + D11_TRANSFER_S
    noi_rec = aq_row + d1_noise_s + D11_TRANSFER_S
    noi_true = noi_rec + de_s
    # (expno, role, rows, rec_per_row, true_per_row, true_pre_block);
    # rows None = setup
    plan = [(1, "setup", None, None, None, 0.0)]
    plan += [(e, "rg_ladder", 1, lad_rec, lad_true, 0.0)
             for e, _rg in RG_LADDER]
    plan += [(11, "reference_open", REF_ROWS, ref_rec, ref_true, REF_PRE_S),
             (12, "noise", n_noise_rows, noi_rec, noi_true, 0.0),
             (13, "reference_close", REF_ROWS, ref_rec, ref_true, REF_PRE_S)]
    t_ms = start_ms
    blocks = []
    for expno, role, rows, rec_row, true_row, pre_s in plan:
        if rows is None:
            ocxo_s = None
            dur_ms = SETUP_WALL_MS  # setup: tune/shim/dialogs, wall only
        else:
            ocxo_s = rows * rec_row
            true_s = rows * true_row + pre_s
            dur_ms = int(round(true_s * 1000.0 * (1.0 + offset)
                               + BLOCK_OVERHEAD_MS + rng.integers(-4, 5)))
        blocks.append({"expno": expno, "role": role,
                       "wall_start_ms": t_ms,
                       "wall_end_ms": t_ms + dur_ms,
                       "ocxo_expected_s": ocxo_s})
        t_ms += dur_ms + BLOCK_GAP_MS
    return blocks


def build_clock_audit(offset, rng, de_us=DE_US_DEFAULT, blocks=None,
                      n_noise_rows=N_NOISE_ROWS, d1_noise_s=D1_NOISE_S):
    """Schema-1.2 clock_audit object (see session_timeline for the model).
    Without a prepared timeline the blocks start at the fixture's
    arbitrary 2026-08 epoch, as before v0.8."""
    if blocks is None:
        blocks = session_timeline(offset, rng, de_us, AUDIT_EPOCH_MS,
                                  n_noise_rows, d1_noise_s)
    return {"blocks": blocks,
            "ntp_status_raw": "synthetic clock-audit fixture (no NTP "
                              "daemon was queried)",
            "workstation_time_source": "synthetic"}


def parse_utc(text):
    """ISO 8601 -> aware UTC datetime ('Z' or an offset; naive = UTC)."""
    s = text.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def utc_from_ms(ms):
    return datetime.datetime.fromtimestamp(ms / 1000.0, UTC)


def local_iso(ms, tz_offset_min):
    """started_local-style string (second resolution, no zone) of a UTC
    epoch in the zone UTC + tz_offset_min, as the run script's now_local."""
    dt = utc_from_ms(int(ms) // 1000 * 1000) \
        + datetime.timedelta(minutes=tz_offset_min)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------------
# Standard-halo wind angle (port of the prototype shm_wind.py; Sec. 8.2)
# --------------------------------------------------------------------------

def _days_since_j2000(t_utc):
    return (t_utc - J2000).total_seconds() / 86400.0


def gmst_deg(t_utc):
    d = _days_since_j2000(t_utc)
    T = d / 36525.0
    return (280.46061837 + 360.98564736629 * d + 0.000387933 * T * T) % 360.0


def v_lab_gal(t_utc):
    """Lab velocity through the halo, galactic cartesian, km/s."""
    year_start = datetime.datetime(t_utc.year, 1, 1, tzinfo=UTC)
    doy = (t_utc - year_start).total_seconds() / 86400.0
    phase = 2.0 * math.pi * (doy - MARCH_EQUINOX_DOY) / TROPICAL_YEAR_D
    v_e = V_EARTH_KMS * (EPS1_GAL * math.cos(phase) + EPS2_GAL * math.sin(phase))
    return V_SUN_GAL + v_e


def b0_axis_eq(lat_deg, lon_east_deg, t_utc, orientation="vertical",
               azimuth_deg=0.0):
    """Unit vector along B0 in equatorial coordinates: the zenith for a
    vertical bore, or the horizontal unit vector at the bore azimuth
    (north 0, east 90) built in the horizon frame and rotated to
    equatorial (Sec. 8.2)."""
    lst = math.radians((gmst_deg(t_utc) + lon_east_deg) % 360.0)
    lat = math.radians(lat_deg)
    if orientation == "vertical":
        return np.array([math.cos(lat) * math.cos(lst),
                         math.cos(lat) * math.sin(lst), math.sin(lat)])
    north = np.array([-math.sin(lat) * math.cos(lst),
                      -math.sin(lat) * math.sin(lst), math.cos(lat)])
    east = np.array([-math.sin(lst), math.cos(lst), 0.0])
    az = math.radians(azimuth_deg)
    return math.cos(az) * north + math.sin(az) * east


def wind_angle_deg(lat_deg, lon_east_deg, t_utc, orientation="vertical",
                   azimuth_deg=0.0):
    """Angle between the DM wind (-v_lab) and B0; the sign of B0 is
    irrelevant (v_perp^2 even), so the angle is folded to [0, 90]."""
    v = GAL2EQ @ v_lab_gal(t_utc)
    v = v / np.linalg.norm(v)
    c = abs(float(np.dot(v, b0_axis_eq(lat_deg, lon_east_deg, t_utc,
                                       orientation, azimuth_deg))))
    return math.degrees(math.acos(min(1.0, c)))


def closed_form_template(lat_deg, lon_east_deg, times_utc,
                         orientation="vertical", azimuth_deg=0.0):
    """The Sec. 9.1 closed form f(t_k) = (v0^2 + V^2 sin^2 theta_k) / mean,
    V the session-mean |v_lab| (Sec. 8.3).  Returns (f, theta_deg, V)."""
    th = np.array([wind_angle_deg(lat_deg, lon_east_deg, t, orientation,
                                  azimuth_deg) for t in times_utc])
    vl = np.array([float(np.linalg.norm(v_lab_gal(t))) for t in times_utc])
    V = float(vl.mean())
    vp = SHM_V0_KMS ** 2 + V ** 2 * np.sin(np.radians(th)) ** 2
    return vp / vp.mean(), th, V


def _load_by_path(name):
    path = os.path.join(REPO, "analysis", name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def registry_module():
    """analysis/registry_report.py loaded by file path (its gazetteer and
    resolve_coords are what the report uses for a city, Sec. 8.4 a)."""
    return _load_by_path("registry_report")


def halo_wind_module():
    """analysis/halo_wind.py loaded by file path: its alp_lineshape_angle
    is the one lineshape routine the Monte-Carlo template shares with the
    report (Sec. 7 (v) pins it bit-for-bit to the report's alp_lineshape
    at 0 and 90 degrees).  The wind angle itself stays the port above."""
    return _load_by_path("halo_wind")


def theta_bin_index(theta_deg, bin_deg=WIND_THETA_BIN_DEG):
    """(bin index per row, bin centres) on the Sec. 8.3 grid, exactly as
    analysis/sidereal_modulation.theta_bin_index assigns them."""
    edges = np.arange(0.0, 90.0 + bin_deg, bin_deg)
    idx = np.clip(np.digitize(np.asarray(theta_deg, dtype=float), edges) - 1,
                  0, len(edges) - 2)
    centres = 0.5 * (edges[:-1] + edges[1:])
    return idx, centres


def mc_response_rows(theta_deg, vlab_kms, nu_a_hz, gamma_per_s, offsets_hz,
                     hw=None, grid_hz=None, nsamp=EXCL_MC_SAMPLES,
                     seed=EXCL_MC_SEED, bin_deg=WIND_THETA_BIN_DEG):
    """xi^2[k, j] (up to the constant c_omega2, which the row-mean
    normalisation removes) for rows at wind angle theta_deg[k] and scan
    offsets offsets_hz[j]: the Monte-Carlo lineshape at the row's 2 degree
    bin centre and the session-mean |v_lab|, times the Lorentzian response
    1 / (Gamma^2 + (2 pi (Delta + nu))^2), integrated over the exclusion's
    grid -- the TemplateBank construction of analysis/sidereal_modulation.py
    (one lineshape per bin, the response vectorised over the offsets).
    Returns (xi2 rows x offsets, number of bins evaluated)."""
    hw = hw or halo_wind_module()
    grid = np.arange(*(grid_hz or EXCL_LINESHAPE_GRID_HZ))
    offsets = np.asarray(offsets_hz, dtype=float)
    resp = 1.0 / (gamma_per_s ** 2 + (
        2.0 * math.pi * (offsets[:, None] + grid[None, :])) ** 2)
    trapz = getattr(np, "trapezoid", None) or np.trapz
    idx, centres = theta_bin_index(theta_deg, bin_deg)
    xi2_bin = {}
    for b in np.unique(idx):
        lam, _vp = hw.alp_lineshape_angle(grid, float(nu_a_hz),
                                          float(centres[b]), float(vlab_kms),
                                          int(nsamp), int(seed))
        xi2_bin[int(b)] = trapz(lam[None, :] * resp, grid, axis=1)
    xi2 = np.vstack([xi2_bin[int(b)] for b in idx])
    return xi2, len(xi2_bin)


def ols_slope(y, x):
    """OLS slope of y on x (with an intercept): cov(y, x) / var(x)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    xc = x - x.mean()
    return float(np.dot(xc, y - y.mean()) / np.dot(xc, xc))


def modulation_templates(theta_deg, vlab_kms, nu_a_hz, gamma_per_s,
                         offset_hz=None):
    """The Sec. 9.1 Monte-Carlo template for the rows: picks the offset
    when none is given (the scan node maximising the session-mean response
    <xi^2>_rows(Delta), i.e. the minimum of the report's wind-aware
    nominal curve, see the docstring), and returns a dict with offset_hz,
    offset_basis, f_mc (rows, normalised to the row mean at offset_hz),
    the equivalence table offsets/F (rows x table offsets, each column
    normalised to its row mean) and bins_evaluated."""
    scan = np.arange(*EXCL_SCAN_HZ)
    if offset_hz is None:
        xi2_scan, _nb = mc_response_rows(theta_deg, vlab_kms, nu_a_hz,
                                         gamma_per_s, scan)
        offset_hz = float(scan[int(np.argmax(xi2_scan.mean(axis=0)))])
        basis = ("default: the EXCL_SCAN_HZ node (%+.0f..%+.0f Hz by %.0f) "
                 "maximising the session-mean response <xi^2>_rows(Delta) "
                 "for the fixture's line at Gamma = pi x FWHM -- the "
                 "minimum of the report's g90_wind_nominal(Delta), i.e. "
                 "result.offset_at_best_wind_nominal_hz up to the report's "
                 "fitted Gamma and nu_L"
                 % (EXCL_SCAN_HZ[0], scan[-1], EXCL_SCAN_HZ[2]))
    else:
        offset_hz = float(offset_hz)
        basis = "given on the command line (--inject-modulation P_A@OFFSET)"
    table = offset_hz + np.arange(-EQUIV_HALF_SPAN_HZ,
                                  EQUIV_HALF_SPAN_HZ + EQUIV_STEP_HZ / 2.0,
                                  EQUIV_STEP_HZ)
    xi2, nbins = mc_response_rows(theta_deg, vlab_kms, nu_a_hz, gamma_per_s,
                                  table)
    F = xi2 / xi2.mean(axis=0)[None, :]
    j = int(np.argmin(np.abs(table - offset_hz)))
    return {"offset_hz": offset_hz, "offset_basis": basis,
            "f_mc": F[:, j], "table_offsets_hz": table, "table_F": F,
            "bins_evaluated": nbins}


def resolve_city(name):
    """(lat, lon) of a gazetteer city, or None."""
    try:
        return registry_module().resolve_coords({"city": name, "country": ""})
    except Exception as exc:         # pragma: no cover - registry broken
        info("registry_report unavailable (%s); --city not resolved" % exc)
        return None


def pp_file(expno, text, args):
    """The (arcname, bytes) pair carrying expno's pulse-program text in
    the layout of the chosen console generation.  TopSpin 2.x/3.x store
    the source as data/<expno>/pulseprogram; the TopSpin 4.x Neo consoles
    store only the preprocessed data/<expno>/pulseprogram.precomp (Torino,
    2026-09-25: no 'pulseprogram' in any expno) -- cpp output, i.e. the
    same statements with '# <line> "<file>"' markers in place of the
    #include lines.  The report must model both."""
    if args.pp_layout == "topspin3":
        return ("data/%d/pulseprogram" % expno, text)
    src = "/root/.topspin-BladeEpu/local_acqu/FIXTURE/%d/lists/pp/x" % expno
    out = ['# 1 "%s"' % src, '# 1 "<built-in>"', '# 1 "<command-line>"',
           '# 1 "%s"' % src]
    n = 0
    for ln in text.decode("ascii").splitlines():
        n += 1
        if ln.startswith("#include"):
            out.append('# 1 "%s.incl" 1' % src)
            out.append('# %d "%s" 2' % (n + 1, src))
            continue
        out.append(ln)
    return ("data/%d/pulseprogram.precomp" % expno,
            ("\n".join(out) + "\n").encode("ascii"))


def build_bundle(args):
    rng = np.random.default_rng(args.seed)
    sign = {"bump": 1.0, "dip": -1.0, "none": 0.0}[args.feature]
    a_inj = sign * abs(args.amp) if sign else 0.0
    b_inj = a_inj * args.b_over_a
    info("injected: a=%.4g b=%.4g f0=%.4g Hz fwhm=%.4g Hz"
         % (a_inj, b_inj, F0_HZ, args.fwhm))

    n_row = TD_ROW // 2
    n_noise = args.noise_rows
    files = []          # (arcname, bytes)

    # ---- timed session (v0.8): the timeline first, so the noise rows
    # know their mid-times; its jitter comes from a child stream so the
    # untimed fixture's random stream (rows first, audit after) is intact
    offset = args.clock_offset if args.clock_offset is not None else 0.0
    d1_noise = args.noise_d1_s
    row_true_s = noise_row_true_s(d1_noise, args.de_us)
    timed = args.start_utc is not None
    timeline = None
    row_mid_ms = None
    start_dt = None
    noise_block = None
    if timed:
        start_dt = parse_utc(args.start_utc)
        start_ms = int(round(start_dt.timestamp() * 1000.0))
        timeline = session_timeline(offset, np.random.default_rng([args.seed, 1]),
                                    args.de_us, start_ms, n_noise, d1_noise)
        noise_block = [b for b in timeline if b["role"] == "noise"][0]
        row_mid_ms = (noise_block["wall_start_ms"]
                      + (np.arange(n_noise) + 0.5) * row_true_s
                      * (1.0 + offset) * 1000.0)
        info("timed session: %s .. %s UTC, noise block %s .. %s (%d rows "
             "every %.3f s, d1 %.4g s)"
             % (utc_from_ms(timeline[0]["wall_start_ms"]).strftime("%H:%M:%S"),
                utc_from_ms(timeline[-1]["wall_end_ms"]).strftime("%H:%M:%S"),
                utc_from_ms(noise_block["wall_start_ms"]).strftime("%H:%M:%S"),
                utc_from_ms(noise_block["wall_end_ms"]).strftime("%H:%M:%S"),
                n_noise, row_true_s * (1.0 + offset), d1_noise))

    # per-row floor / line parameters (constants unless a lab cycle or a
    # modulated excess is injected; the arithmetic below leaves the
    # untimed values bit-identical: x*(1+0.0) == x, x + 0.0 == x)
    lab_factor = np.zeros(n_noise)
    modulation = None
    lab_cycle = None
    if args.lab_cycle_pct:
        peak = parse_utc(args.lab_cycle_peak_utc) if args.lab_cycle_peak_utc \
            else start_dt
        t_rel = (row_mid_ms / 1000.0 - peak.timestamp())
        lab_factor = (args.lab_cycle_pct / 100.0) * np.cos(
            2.0 * math.pi * t_rel / 86400.0)
        lab_cycle = {"pct": args.lab_cycle_pct,
                     "peak_utc": peak.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "applied_to": ["floor", "dip_depth"],
                     "factor_rows": [float(1.0 + x) for x in lab_factor]}
    floors = FLOOR_C2HZ * (1.0 + lab_factor)
    a_rows = a_inj * (1.0 + lab_factor)
    if args.inject_modulation is not None:
        P_A, offset_req = args.inject_modulation
        times = [utc_from_ms(ms) for ms in row_mid_ms]
        f_closed, theta, vlab = closed_form_template(
            args.site[0], args.site[1], times, args.b0_orientation,
            args.b0_azimuth_deg)
        # the Sec. 9.1 Monte-Carlo template (the report's) at the offset
        nu_a_hz = H1_FREQ_MHZ * 1e6 + F0_HZ
        gamma = math.pi * args.fwhm
        t_mc = time.time()
        mc = modulation_templates(theta, vlab, nu_a_hz, gamma, offset_req)
        f_mc = mc["f_mc"]
        offset_hz = mc["offset_hz"]
        f_rows = f_mc if args.inject_modulation_template == "mc_response" \
            else f_closed
        # extra ABSOLUTE line power P_A f_k counts^2 = da_k pi w/2 floor_k
        a_rows = a_rows + P_A * f_rows / (floors * math.pi * args.fwhm / 2.0)
        # what the report's two-parameter fit recovers from THIS bundle at
        # every table offset: P_A x cov(f_injected, f_mc(Delta)) /
        # var(f_mc(Delta)) (equal to P_A at offset_hz for an MC injection)
        slopes = [ols_slope(f_rows, mc["table_F"][:, j])
                  for j in range(mc["table_offsets_hz"].size)]
        keys = ["%g" % x for x in mc["table_offsets_hz"]]
        modulation = {
            "P_a_counts2": P_A,
            "template": args.inject_modulation_template,
            "template_basis": (
                "f(t_k; Delta) = xi^2(theta_bin(t_k), V; Delta) / "
                "<xi^2>_rows, the exclusion's response integral at the scan "
                "offset Delta = offset_hz from the Monte-Carlo lineshape "
                "(analysis/halo_wind.alp_lineshape_angle by file path) at "
                "the row's %g degree wind-angle bin, every bin at the "
                "session-mean |v_lab|, normalised to the row mean: the "
                "template analysis/sidereal_modulation.TemplateBank fits "
                "(Sec. 9.1)" % WIND_THETA_BIN_DEG
                if args.inject_modulation_template == "mc_response" else
                "closed form (v0^2 + V^2 sin^2 theta)/<.>_rows of the "
                "prototype shm_wind.py: the Sec. 9.1 intuition, NOT the "
                "report's Monte-Carlo response; the report's fit at "
                "offset_hz recovers closed_form_slope x P_a_counts2"),
            "offset_hz": offset_hz,
            "offset_basis": mc["offset_basis"],
            "nu_a_hz": nu_a_hz, "gamma_per_s": gamma,
            "gamma_basis": "pi x the injected FWHM (the report fits Gamma "
                           "from the measured line width)",
            "nsamp": EXCL_MC_SAMPLES, "seed": EXCL_MC_SEED,
            "bin_deg": WIND_THETA_BIN_DEG,
            "grid_hz": list(EXCL_LINESHAPE_GRID_HZ),
            "scan_hz": list(EXCL_SCAN_HZ),
            "bins_evaluated": mc["bins_evaluated"],
            "shape": "the injected line's (f0, FWHM, b/a)",
            "v0_kms": SHM_V0_KMS, "vlab_mean_kms": vlab,
            "f_min": float(f_rows.min()), "f_max": float(f_rows.max()),
            "f_std": float(f_rows.std()),
            "f_mc_std": float(f_mc.std()),
            "f_closed_std": float(f_closed.std()),
            "closed_form_slope": ols_slope(f_closed, f_mc),
            "closed_form_slope_basis": (
                "OLS slope of the closed form on the MC template at "
                "offset_hz: a closed-form injection of P fits as "
                "closed_form_slope x P with the report's template there "
                "(the intuition only; offset-dependent)"),
            "theta_min_deg": float(theta.min()),
            "theta_max_deg": float(theta.max()),
            "row_mid_utc_ms": [int(round(x)) for x in row_mid_ms],
            "theta_deg_rows": [float(x) for x in theta],
            "f_rows": [float(x) for x in f_rows],
            "f_mc_rows": [float(x) for x in f_mc],
            "f_closed_rows": [float(x) for x in f_closed],
            "extra_power_counts2_rows": [float(P_A * x) for x in f_rows],
            "equivalent_P_a_by_offset": dict(
                zip(keys, [float(P_A * s) for s in slopes])),
            "equivalent_P_a_slope_by_offset": dict(
                zip(keys, [float(s) for s in slopes])),
            "equivalent_P_a_basis": (
                "for a report fit at scan offset Delta (its "
                "template.offset_hz / curve.nu_a_minus_nu_L_hz), the P_a its "
                "two-parameter OLS recovers from this bundle: P_a_counts2 x "
                "cov(f_rows, f_mc(Delta)) / var(f_mc(Delta)), every %g Hz "
                "from offset_hz - %g to offset_hz + %g, keyed by Delta as "
                "'%%g'; compare fit.P_a_counts2 (or curve.P_a_counts2 at "
                "Delta) with it within sigma_Pa_counts2"
                % (EQUIV_STEP_HZ, EQUIV_HALF_SPAN_HZ, EQUIV_HALF_SPAN_HZ))}
        info("modulated excess (%s template at %+.0f Hz, %s; %d bins, %.1f "
             "s): P_a %.4g counts^2 x f, f %.3f..%.3f (std %.4f; closed form "
             "std %.4f, slope on MC %.3f), theta %.1f..%.1f deg, V %.1f km/s"
             % (args.inject_modulation_template, offset_hz,
                "default offset" if offset_req is None else "given offset",
                mc["bins_evaluated"], time.time() - t_mc, P_A,
                f_rows.min(), f_rows.max(), f_rows.std(), f_closed.std(),
                modulation["closed_form_slope"], theta.min(), theta.max(),
                vlab))
    b_rows = a_rows * args.b_over_a

    # setup expno 1: acqus only
    files.append(("data/1/acqus",
                  acqus_text(TD_LADDER, SW_HZ, 1.0, pulprog="zg",
                             d1_s=D1_REF_S, de_us=args.de_us)))
    files.append(pp_file(1, PP_TEXTS["zg"], args))

    # RG ladder: amplitude exactly linear in RG
    ladder_meta = []
    n_lad = TD_LADDER // 2
    for expno, rg in RG_LADDER:
        # amplitude exactly linear in RG; floor scales as RG^2 (a constant
        # input-referred floor seen through the gain), so every rung has the
        # same signal-to-noise and the linearity check is noise-limited at
        # well below the percent level
        row = synth_reference_row(rng, n_lad, SW_HZ, 3000.0 * rg, F0_HZ,
                                  REF_FWHM_HZ, REF_DECAY_S,
                                  FLOOR_C2HZ * (rg / RG_NOISE) ** 2)
        files.append(("data/%d/acqus" % expno,
                      acqus_text(TD_LADDER, SW_HZ, rg, pulprog="zg",
                                 d1_s=D1_REF_S, de_us=args.de_us)))
        files.append(pp_file(expno, PP_TEXTS["zg"], args))
        files.append(("data/%d/fid" % expno, to_bruker_int32([row])))
        ladder_meta.append({"expno": expno, "rg": rg, "tip_deg": 1.0})

    # references (open=11, close=13)
    ref_rows = {}
    for expno in (11, 13):
        rows = [synth_reference_row(rng, n_row, SW_HZ, REF_A0, F0_HZ,
                                    REF_FWHM_HZ, REF_DECAY_S,
                                    FLOOR_C2HZ * (RG_REF / RG_NOISE) ** 2)
                for _ in range(REF_ROWS)]
        ref_rows[expno] = rows
        files.append(("data/%d/acqus" % expno,
                      acqus_text(TD_ROW, SW_HZ, RG_REF, pulprog="zgref2d",
                                 d1_s=D1_REF_S, de_us=args.de_us)))
        files.append(pp_file(expno, PP_TEXTS["zgref2d"], args))
        files.append(("data/%d/acqu2s" % expno, acqu2s_text(REF_ROWS)))
        files.append(("data/%d/ser" % expno, to_bruker_int32(rows)))

    # noise block (12)
    noise_rows = [synth_noise_row(rng, n_row, SW_HZ, float(floors[k]),
                                  float(a_rows[k]), float(b_rows[k]),
                                  F0_HZ, args.fwhm)
                  for k in range(n_noise)]
    files.append(("data/12/acqus",
                  acqus_text(TD_ROW, SW_HZ, RG_NOISE,
                             d1_s=d1_noise, de_us=args.de_us)))
    files.append(pp_file(12, PP_TEXTS["zgnoise2d"], args))
    files.append(("data/12/acqu2s", acqu2s_text(n_noise)))
    files.append(("data/12/ser", to_bruker_int32(noise_rows)))

    # ---- meta.json
    with open(os.path.join(REPO, "VERSION")) as fh:
        version = fh.read().strip()
    utc = time.gmtime()
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", utc)
    t0 = time.strftime("%Y-%m-%dT%H:%M:%S", utc)
    aq_row = n_row / SW_HZ
    tz_min = args.tz_offset_min if timed else 0
    block_times = {}
    if timed:
        for b in timeline:
            block_times[b["expno"]] = (local_iso(b["wall_start_ms"], tz_min),
                                       local_iso(b["wall_end_ms"], tz_min))
        created = utc_from_ms(timeline[-1]["wall_end_ms"]).strftime(
            "%Y-%m-%dT%H:%M:%SZ")

    def expmeta(expno, role, pulprog, td, rows, rg, aq):
        started, finished = block_times.get(expno, (t0, t0))
        return {"expno": expno, "role": role, "pulprog": pulprog,
                "td": td, "td1_rows": rows, "sw_hz": SW_HZ, "o1_hz": 0.0,
                "rg": rg, "ns": 1, "aq_s_per_row": aq,
                "started_local": started, "finished_local": finished}

    notes = ("SYNTHETIC injection bundle; injected a=%.4g b/a=%.3g f0=%.4g "
             "Hz fwhm=%.4g Hz; never a measurement"
             % (a_inj, args.b_over_a, F0_HZ, args.fwhm))
    if timed:
        notes += ("; timed session from %s UTC, %d noise rows every %.4g s"
                  % (start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), n_noise,
                     row_true_s))
    if modulation is not None:
        notes += ("; modulated excess P_a=%.4g counts^2 x f(t; %+.0f Hz) "
                  "(Sec. 9.1 %s template)"
                  % (modulation["P_a_counts2"], modulation["offset_hz"],
                     modulation["template"]))
    if lab_cycle is not None:
        notes += "; 24 h laboratory cycle +/-%.3g%%" % args.lab_cycle_pct

    facility = {"institution": "Injection-recovery validation (synthetic)",
                "city": args.city or "Nowhere", "country": "n/a",
                "facility_slug": FACILITY_SLUG,
                "contact_email": "jwbquantum@gmail.com",
                "contact_consent": True}
    if args.site_given:
        facility["latitude_deg"] = args.site[0]
        facility["longitude_east_deg"] = args.site[1]
        facility["coordinates_basis"] = "operator"
    spectrometer = {"topspin_version": "n/a (synthetic)",
                    "h1_freq_mhz": H1_FREQ_MHZ, "field_tesla": 14.095,
                    "console": "synthetic",
                    "probe_string": "synthetic 5 mm probe",
                    "probe_type": ("N2-cryo" if a_inj > 0 else "RT"),
                    "coil_temp_k": None, "preamp_temp_k": None,
                    "b0_orientation": args.b0_orientation}
    if args.b0_orientation == "horizontal":
        spectrometer["b0_azimuth_deg"] = args.b0_azimuth_deg

    meta = {
        "schema_version": "1.2",
        "program_version": version,
        "software": {"script_version": version, "schema_version": "1.2",
                     "script_sha256": "unavailable",
                     "run_mode": "synthetic-injection"},
        "created_utc": created,
        "local_timezone_offset_min": tz_min,
        "facility": facility,
        "spectrometer": spectrometer,
        "sample": {"description": "synthetic water (numerical)",
                   "h2o_fraction_pct": 100.0, "d2o_pct": 0.0,
                   "additives": "none", "tube_od_mm": 5.0,
                   "sample_volume_ul": 550.0, "vt_setpoint_k": 298.0},
        "environment": {"locked": False, "lock_sweep_confirmed_off": True,
                        "operator_notes": notes},
        "calibration": {"p90_us": 10.0, "p90_power_db_or_w": "n/a",
                        "rg_ladder": ladder_meta, "topshim_ok": False},
        "experiments": [
            expmeta(1, "setup", "zg", TD_LADDER, 1, 1.0, TD_LADDER / 2 / SW_HZ)]
        + [expmeta(e, "rg_ladder", "zg", TD_LADDER, 1, rg,
                   TD_LADDER / 2 / SW_HZ) for e, rg in RG_LADDER]
        + [expmeta(11, "reference_open", "zgref2d", TD_ROW, REF_ROWS, RG_REF, aq_row),
           expmeta(12, "noise", "zgnoise2d", TD_ROW, n_noise, RG_NOISE, aq_row),
           expmeta(13, "reference_close", "zgref2d", TD_ROW, REF_ROWS, RG_REF, aq_row)],
        "checksums": {},
        "injection_truth": {   # extra key (schema allows additional props)
            "feature": args.feature, "amp_norm": a_inj,
            "b_over_a": args.b_over_a, "f0_hz": F0_HZ,
            "fwhm_hz": args.fwhm, "floor_counts2perhz": FLOOR_C2HZ,
            "ref_a0_counts": REF_A0, "seed": args.seed,
            "clock_fractional_offset": args.clock_offset,
            "clock_de_us": args.de_us,
            # v0.8 additions
            "noise_rows": n_noise,
            "line_power_counts2": a_inj * math.pi * args.fwhm / 2.0 * FLOOR_C2HZ,
            "b0_orientation": args.b0_orientation,
            "site": ({"latitude_deg": args.site[0],
                      "longitude_east_deg": args.site[1],
                      "basis": "operator" if args.site_given else "registry_city"}
                     if args.site else None),
            "session": ({"start_utc": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "end_utc": created, "tz_offset_min": tz_min,
                         "row_cadence_s": row_true_s * (1.0 + offset),
                         "row_cadence_ocxo_s": row_true_s,
                         "noise_d1_s": d1_noise,
                         "clock_fractional_offset_applied": offset,
                         "noise_wall_start_ms": noise_block["wall_start_ms"],
                         "noise_wall_end_ms": noise_block["wall_end_ms"]}
                        if timed else None),
            "modulation": modulation,
            "lab_cycle": lab_cycle},
    }

    # ---- clock audit: always in a timed session (offset 0 unless given),
    # otherwise only with a known injected fractional offset, as before
    if timed:
        meta["clock_audit"] = build_clock_audit(offset, rng, args.de_us,
                                                blocks=timeline)
        info("clock audit: %d blocks on the session timeline (fractional "
             "offset %.3e, DE = %.6g us)"
             % (len(timeline), offset, args.de_us))
    elif args.clock_offset is not None:
        meta["clock_audit"] = build_clock_audit(args.clock_offset, rng,
                                                args.de_us,
                                                n_noise_rows=n_noise,
                                                d1_noise_s=d1_noise)
        info("clock audit injected: fractional offset %.3e over %d blocks "
             "(DE = %.6g us)"
             % (args.clock_offset, len(meta["clock_audit"]["blocks"]),
                args.de_us))
    for arc, payload in files:
        meta["checksums"][arc] = "sha256:" + hashlib.sha256(payload).hexdigest()

    out_dir = args.out_dir or os.path.join(REPO, "testing", "synthetic_bundles")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    stamp = time.strftime("%Y%m%d_%H%M%SZ", time.gmtime())
    name = "spinnoise_%s_%s_%04x.zip" % (FACILITY_SLUG, stamp,
                                         random.randint(0, 0xFFFF))
    path = os.path.join(out_dir, name)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta, indent=1) + "\n")
        for arc, payload in files:
            zf.writestr(arc, payload)
    info("bundle: %s (%.1f MiB)" % (path, os.path.getsize(path) / 1048576.0))
    print(path)
    return 0


def _parse_site(text):
    try:
        lat_s, lon_s = text.split(",")
        lat, lon = float(lat_s), float(lon_s)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "--site wants LAT,LON in degrees (longitude east-positive), "
            "e.g. 65.0,25.5")
    # the schema's bounds (uploader/meta.schema.json: |lat| <= 90, lon in
    # [-180, 180]), so a --site the uploader would refuse fails here
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        raise argparse.ArgumentTypeError(
            "--site %s out of range (latitude in [-90, 90], longitude "
            "east-positive in [-180, 180], the schema's bounds)" % text)
    return (lat, lon)


def _parse_modulation(text):
    """'P_A' or 'P_A@OFFSET_HZ' -> (P_A counts^2, offset Hz or None)."""
    s = str(text).strip()
    off = None
    if "@" in s:
        s, off_s = s.split("@", 1)
        try:
            off = float(off_s)
        except ValueError:
            raise argparse.ArgumentTypeError(
                "--inject-modulation wants P_A[@OFFSET_HZ], e.g. 5e5@-420; "
                "%r is not an offset" % off_s)
        if not math.isfinite(off):
            raise argparse.ArgumentTypeError("--inject-modulation offset "
                                             "must be finite")
    try:
        p_a = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "--inject-modulation wants P_A[@OFFSET_HZ] with P_A in "
            "counts^2, e.g. 5e5@-420; %r is not a number" % s)
    if not math.isfinite(p_a):
        raise argparse.ArgumentTypeError("--inject-modulation P_A must be "
                                         "finite")
    return (p_a, off)


def main(argv=None):
    ap = argparse.ArgumentParser(description="synthesize an injection bundle")
    ap.add_argument("--feature", choices=("bump", "dip", "none"),
                    default="bump")
    ap.add_argument("--amp", type=float, default=1.5,
                    help="|feature amplitude| relative to floor "
                         "(bump: e.g. 1.5; dip: must be < 1, e.g. 0.35)")
    ap.add_argument("--fwhm", type=float, default=12.0)
    ap.add_argument("--b-over-a", type=float, default=0.3,
                    help="dispersive fraction of the injected line")
    ap.add_argument("--seed", type=int, default=20260825)
    ap.add_argument("--clock-offset", type=float, default=None,
                    help="include a schema-1.2 clock_audit whose blocks "
                         "carry this KNOWN fractional console-clock offset "
                         "(e.g. 3e-7); omit for no clock_audit (pre-1.2 "
                         "behavior; a timed session always carries one, "
                         "at offset 0 when this is omitted)")
    ap.add_argument("--de-us", type=float, default=DE_US_DEFAULT,
                    help="pre-acquisition delay DE in microseconds, written "
                         "to acqus AND spent per scan in the clock-audit "
                         "wall durations but ABSENT from the recorded "
                         "expectations (the physical shortfall the report's "
                         "acqus-refined fit must remove); default %.3g"
                         % DE_US_DEFAULT)
    ap.add_argument("--pp-layout", choices=("topspin3", "topspin4"),
                    default="topspin3",
                    help="where each expno's pulse-program text lives: "
                         "data/<expno>/pulseprogram (TopSpin 2.x/3.x, "
                         "default) or the preprocessed "
                         "data/<expno>/pulseprogram.precomp only (TopSpin "
                         "4.x Neo consoles, as Torino's 2026-09-25 bundle)")
    ap.add_argument("--out-dir", default=None)
    # ---- v0.8 timed-session options (Sec. 7 (vi))
    ap.add_argument("--noise-rows", type=int, default=N_NOISE_ROWS,
                    help="rows of the noise block (default %d)" % N_NOISE_ROWS)
    ap.add_argument("--start-utc", default=None,
                    help="session start, ISO 8601 UTC (e.g. "
                         "2026-10-05T07:00:00Z): lays every block on a "
                         "wall-clock timeline, writes clock_audit "
                         "wall_start_ms/wall_end_ms for all of them and "
                         "consistent started_local/finished_local")
    ap.add_argument("--tz-offset-min", type=int, default=0,
                    help="local_timezone_offset_min of a timed session "
                         "(started_local = UTC + this; default 0)")
    ap.add_argument("--row-cadence-s", type=float, default=None,
                    help="wall seconds per noise row (default AQ + d1 + d11 "
                         "+ DE = %.4g s); a longer cadence is realised as "
                         "a longer zgnoise2d d1, in acqus D[1] and in the "
                         "recorded expectation alike"
                         % noise_row_true_s())
    ap.add_argument("--site", type=_parse_site, default=None,
                    help="facility coordinates LAT,LON (deg, longitude "
                         "east-positive) -> facility.latitude_deg / "
                         "longitude_east_deg, coordinates_basis 'operator'")
    ap.add_argument("--city", default=None,
                    help="facility.city; must resolve in the registry "
                         "gazetteer (analysis/registry_report.py) unless "
                         "--site is given. Default 'Nowhere' (unresolvable)")
    ap.add_argument("--b0-orientation", choices=B0_ORIENTATIONS,
                    default="vertical",
                    help="spectrometer.b0_orientation (default vertical)")
    ap.add_argument("--b0-azimuth-deg", type=float, default=0.0,
                    help="spectrometer.b0_azimuth_deg of a horizontal B0 "
                         "(north 0, east 90; default 0)")
    ap.add_argument("--inject-modulation", type=_parse_modulation,
                    default=None, metavar="P_A[@OFFSET_HZ]",
                    help="extra line power P_A x f(t_k; OFFSET) counts^2 per "
                         "noise row, f the Sec. 9.1 Monte-Carlo response "
                         "template at scan offset OFFSET (nu_a - nu_L, Hz; "
                         "default: the wind-aware best offset for the "
                         "fixture's line, -420 Hz on the stock fixture) at "
                         "the session's site/times (needs --start-utc and "
                         "--site or --city); e.g. 5e5@-420")
    ap.add_argument("--inject-modulation-template",
                    choices=MODULATION_TEMPLATES, default="mc_response",
                    help="which template the excess follows: mc_response "
                         "(default; the report's) or closed_form (the "
                         "Sec. 9.1 intuition; the truth records the "
                         "offset-dependent slope the report recovers it "
                         "with)")
    ap.add_argument("--lab-cycle-pct", type=float, default=0.0, metavar="P",
                    help="24 h laboratory cycle of +/-P percent on the "
                         "floor AND the dip depth (needs --start-utc)")
    ap.add_argument("--lab-cycle-peak-utc", default=None,
                    help="ISO 8601 UTC time of the laboratory cycle's "
                         "maximum (default: the session start)")
    args = ap.parse_args(argv)
    if args.feature == "dip" and abs(args.amp) >= 1.0:
        ap.error("a dip deeper than the floor is unphysical (--amp < 1)")
    if args.noise_rows < 1:
        ap.error("--noise-rows must be at least 1")
    # row cadence -> noise d1
    args.noise_d1_s = D1_NOISE_S
    if args.row_cadence_s is not None:
        floor_s = noise_row_true_s(D1_NOISE_S, args.de_us)
        if args.row_cadence_s < floor_s - 1e-9:
            ap.error("--row-cadence-s %.4g is shorter than one row's AQ + "
                     "d1 + d11 + DE = %.4f s at this geometry"
                     % (args.row_cadence_s, floor_s))
        args.noise_d1_s = args.row_cadence_s - (floor_s - D1_NOISE_S)
    # site
    args.site_given = args.site is not None
    if args.city is not None:
        coords = resolve_city(args.city)
        if coords is None and not args.site_given:
            ap.error("--city %r does not resolve in the registry gazetteer "
                     "(analysis/registry_report.py GAZETTEER); give --site "
                     "LAT,LON as well, or a listed city" % args.city)
        if not args.site_given:
            args.site = (float(coords[0]), float(coords[1]))
    timed = args.start_utc is not None
    if timed:
        try:
            parse_utc(args.start_utc)
        except ValueError:
            ap.error("--start-utc %r is not ISO 8601 (e.g. "
                     "2026-10-05T07:00:00Z)" % args.start_utc)
    if args.lab_cycle_peak_utc is not None:
        try:
            parse_utc(args.lab_cycle_peak_utc)
        except ValueError:
            ap.error("--lab-cycle-peak-utc %r is not ISO 8601"
                     % args.lab_cycle_peak_utc)
    if args.lab_cycle_pct and not timed:
        ap.error("--lab-cycle-pct needs --start-utc (row times)")
    if not (0.0 <= args.lab_cycle_pct < 100.0):
        ap.error("--lab-cycle-pct must be in [0, 100)")
    if args.inject_modulation is not None:
        if args.inject_modulation[0] < 0.0:
            ap.error("--inject-modulation is a power, >= 0 counts^2")
        off = args.inject_modulation[1]
        if off is not None and not (EXCL_SCAN_HZ[0] <= off < EXCL_SCAN_HZ[1]):
            ap.error("--inject-modulation offset %+g Hz is outside the "
                     "exclusion's scan %+g..%+g Hz" % (off, EXCL_SCAN_HZ[0],
                                                      EXCL_SCAN_HZ[1]))
        if not timed:
            ap.error("--inject-modulation needs --start-utc (row times)")
        if args.site is None:
            ap.error("--inject-modulation needs a site (--site LAT,LON or "
                     "a gazetteer --city)")
        if args.b0_orientation == "unknown":
            ap.error("--inject-modulation needs a known B0 orientation "
                     "(the wind angle is undefined for 'unknown')")
    return build_bundle(args)


if __name__ == "__main__":
    sys.exit(main())
