#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_synthetic_bundle.py -- construct a synthetic spin-noise bundle zip for
testing uploader/upload_bundle.py --selftest without a spectrometer.

    python3 testing/make_synthetic_bundle.py                     # v2.0 bundle
    python3 testing/make_synthetic_bundle.py --schema-version 1.2
    python3 testing/make_synthetic_bundle.py --schema-version 1.1
    python3 testing/make_synthetic_bundle.py --schema-version 1.0
    python3 testing/make_synthetic_bundle.py --out-dir /tmp
    python3 testing/make_synthetic_bundle.py --nutation          # + expnos 80..
    python3 testing/make_synthetic_bundle.py --nutation --nutation-clip 1.0
    python3 testing/make_synthetic_bundle.py --dense-ladder       # + expnos 300..

Prints exactly one line on stdout: the path of the created bundle. All
progress goes to stderr, so CI (and CONTRIBUTING.md) can do:

    python3 uploader/upload_bundle.py \\
        "$(python3 testing/make_synthetic_bundle.py)" --selftest

The bundle mimics what topspin/spin_noise_run.py produces -- meta.json at
the zip root (valid against schema/meta.schema.json for the requested
schema version), fake Bruker experiment files under data/<expno>/, correct
sha256 checksums, and a filename following the
spinnoise_<slug>_<YYYYMMDD_HHMMSSZ>_<4hex>.zip convention. It contains no
measurement data; run_mode is stamped 'desktest' so it can never be
mistaken for a real record.

--nutation (v0.8.2) adds the pulse calibration kept as data: the
calibration.nutation block the console script writes (expnos 80.., P1 =
multiplier x the DECLARED P90 at the calibrated power, one receiver gain
for every step) with real little-endian int32 Bruker fids synthesized
from a KNOWN true P90 (NUTATION_P90_TRUE_US; the declared value differs
from it by 3%, as the TopSpin stub's does), amplitude A90 |sin(pi/2
P1/P90_true)| with a radiation-damping-shortened decay, and optionally
one step driven into saturation (--nutation-clip). The block's
generator, nutation_block(), is importable so testing/test_nutation_
report.py can graft the same steps onto a physics bundle. The
experiments[] entries (role "nutation") are written only when the
shipped schema's role enum accepts that role; the report reads the
steps through calibration.nutation.expnos either way.

--dense-ladder (v0.8.2 addendum) adds the dense receiver-gain ladder:
the four legacy rungs (expnos 10, 14, 15, 16 at RG 1, 8, 64, max_rg) plus
two visits of every level at two per octave from RG 1 to max_rg with the
reference gain (max_rg / 4) and the noise gain (max_rg) as levels of
their own (expnos 300..; visit 1 in a seeded permutation, visit 2 in
reverse), every rung a small-flip fid whose amplitude follows a KNOWN
compression curve c(RG) = 1 - DENSE_COMPRESSION_MAX (RG/max_rg)^1.5 and a
KNOWN linear drift (DENSE_DRIFT_PCT_PER_MIN), recorded under
meta["dense_ladder_truth"]; calibration.rg_ladder carries every rung
(the dense ones with "visit") and calibration.rg_ladder_dense the block
record.  dense_ladder_block() is importable for grafting onto a physics
bundle.

Same portability rules as the uploader: Python 3 standard library only,
nothing newer than 3.6.
"""

from __future__ import print_function

import argparse
import calendar
import hashlib
import json
import os
import random
import sys
import time
import zipfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

FACILITY_SLUG = "ci-selftest"

# ---- nutation block (v0.8.2): the pulse calibration kept as data ----------
# Mirrors spin_noise_run.py's constants: EXP_NUTATION 80.., NUTATION_MULT,
# NUTATION_D1_S, TD_NUTATION = TD_LADDER; the console acquires P1 = P90
# first (rga runs there) and the other multipliers in list order.
NUTATION_EXPNO0 = 80
NUTATION_MULT = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
NUTATION_ORDER = [1.0] + [m for m in NUTATION_MULT if m != 1.0]
NUTATION_D1_S = 20.0
NUTATION_TD = 16384
NUTATION_SW_HZ = 6900.0
NUTATION_GRPDLY = 67.984
NUTATION_O1_HZ = 2350.41
NUTATION_F0_HZ = -812.0            # line offset from the carrier
NUTATION_P90_TRUE_US = 8.5         # the stub's truth
NUTATION_P90_DECLARED_US = 8.76    # ... declared 3% long, as the stub sees it
NUTATION_A90_COUNTS = 5.0e7        # full-power amplitude at NUTATION_RG
NUTATION_RG = 1.0
NUTATION_POWER_DB = -14.15         # PLdB 1 of the calibrated 90-degree pulse
NUTATION_DECAY0_PER_S = 21.85      # 1/T2* + pi x 6 Hz (the physics fixture's)
NUTATION_RD_RATE_PER_S = 80.0      # radiation damping: + lambda_r |sin theta|
NUTATION_NOISE_SIGMA = 2.0e4       # per-sample noise, counts, each channel
INT32_MAX = 2147483647

# ---- dense receiver-gain ladder (v0.8.2 addendum) -------------------------
DENSE_EXPNO0 = 300
DENSE_MAX_RG = 150.0               # rung 16's rga; levels 1 .. 128 + 37.5 + 150
DENSE_LEGACY = [(10, 1.0), (14, 8.0), (15, 64.0)]   # + (16, max_rg)
DENSE_COMPRESSION_MAX = 0.04       # c(max_rg) = 0.96 (amplitude)
DENSE_COMPRESSION_POWER = 1.5
DENSE_DRIFT_PCT_PER_MIN = 0.25     # signal drift, linear in time
DENSE_A1_COUNTS_PER_RG = 2.0e4     # small-flip FID amplitude per unit RG
DENSE_NOISE_SIGMA_PER_RG = 20.0    # per-sample noise per unit RG (constant SNR)
DENSE_RUNG_SPACING_S = 4           # one quick 1D every 4 s (1.2 s AQ + 2 s D1)
DENSE_SEED = 20261019
DENSE_P90_US = 10.0
DENSE_POWER_DB = -14.15            # calibrated 90-degree power (PLdB)
SMALL_FLIP_EXTRA_DB = 39.08        # the protocol's small-flip attenuation


def info(msg):
    print(msg, file=sys.stderr)


def repo_version():
    with open(os.path.join(REPO, "VERSION"), "r") as fh:
        return fh.read().strip()


def build_meta(schema_version, version):
    """A minimal-but-complete meta.json in the shape spin_noise_run.py writes."""
    utc = time.gmtime()
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", utc)
    started = time.strftime("%Y-%m-%dT%H:%M:%S", utc)
    meta = {
        "schema_version": schema_version,
        "program_version": version,
        "created_utc": created,
        "local_timezone_offset_min": 0,
        "facility": {
            "institution": "CI selftest (synthetic)",
            "city": "Nowhere",
            "country": "n/a",
            "facility_slug": FACILITY_SLUG,
            "contact_email": "",
            "contact_consent": False,
        },
        "spectrometer": {
            "topspin_version": "4.1.4",
            "h1_freq_mhz": 600.13,
            "field_tesla": 14.095,
            "console": "synthetic",
            "probe_string": "synthetic 5 mm probe",
            "probe_type": "unknown",
            "coil_temp_k": None,
            "preamp_temp_k": None,
        },
        "sample": {
            "description": "synthetic water (no sample exists)",
            "h2o_fraction_pct": 100.0,
            "d2o_pct": 0.0,
            "additives": "none",
            "tube_od_mm": 5.0,
            "sample_volume_ul": 550.0,
            "vt_setpoint_k": 298.0,
        },
        "environment": {
            "locked": False,
            "lock_sweep_confirmed_off": True,
            "operator_notes": "synthetic CI bundle; not data; never upload",
        },
        "calibration": {
            "p90_us": 10.0,
            "p90_power_db_or_w": "unknown",
            "rg_ladder": [
                {"expno": 10, "rg": 1.0, "tip_deg": 1.0},
                {"expno": 14, "rg": 8.0, "tip_deg": 1.0},
            ],
            "topshim_ok": False,
        },
        "experiments": [
            {
                "expno": 11,
                "role": "reference_open",
                "pulprog": "zgref2d",
                "td": 32768,
                "td1_rows": 8,
                "sw_hz": 12019.23,
                "o1_hz": 3705.8,
                "rg": 25.0,
                "ns": 1,
                "aq_s_per_row": 1.36,
                "started_local": started,
                "finished_local": started,
            },
            {
                "expno": 12,
                "role": "noise",
                "pulprog": "zgnoise2d",
                "td": 32768,
                "td1_rows": 4,
                "sw_hz": 12019.23,
                "o1_hz": 3705.8,
                "rg": 101.0,
                "ns": 1,
                "aq_s_per_row": 1.36,
                "started_local": started,
                "finished_local": started,
            },
        ],
        "checksums": {},  # filled in below
    }
    if schema_version in ("1.1", "1.2", "2.0"):
        meta["software"] = {
            "script_version": version,
            "schema_version": schema_version,
            "script_sha256": "unavailable",
            "run_mode": "desktest",
        }
    if schema_version == "2.0":
        # The vendor-neutral schema: required vendor enum + instrument
        # block (the Bruker fields moved out of the 1.x common core; the
        # deprecated aliases above are left in place on purpose -- the
        # schema keeps them optional and old tooling reads them).
        meta["vendor"] = "bruker"
        meta["instrument"] = {
            "bruker": {
                "topspin_version": meta["spectrometer"]["topspin_version"],
                "bsms_field_sweep_confirmed_off":
                    meta["environment"]["lock_sweep_confirmed_off"],
            }
        }
    if schema_version in ("1.2", "2.0"):
        # Minimal clock_audit (the 1.2 addition): two blocks bracketing
        # the two experiments above, wall == OCXO (zero offset), plus the
        # NTP-status fields. Exercises the uploader's 1.2 validation path.
        meta["clock_audit"] = {
            "blocks": [
                {"expno": 11, "role": "reference_open",
                 "wall_start_ms": 1787000000000,
                 "wall_end_ms": 1787000010880,
                 "ocxo_expected_s": 8 * 1.36},
                {"expno": 12, "role": "noise",
                 "wall_start_ms": 1787000013000,
                 "wall_end_ms": 1787000018440,
                 "ocxo_expected_s": 4 * 1.36},
            ],
            "ntp_status_raw": "synthetic CI bundle (no NTP daemon queried)",
            "workstation_time_source": "synthetic",
        }
    return meta


def schema_role_accepted(role):
    """Whether schema/meta.schema.json's experiments[].role enum lists
    `role` (False when the schema cannot be read)."""
    try:
        with open(os.path.join(REPO, "schema", "meta.schema.json"), "r") as fh:
            schema = json.load(fh)
        enum = (schema["properties"]["experiments"]["items"]["properties"]
                ["role"].get("enum") or [])
        return role in enum
    except (IOError, OSError, ValueError, KeyError, TypeError):
        return False


def nutation_acqus_text(p1_us, rg=NUTATION_RG, power_db=NUTATION_POWER_DB,
                        td=NUTATION_TD, sw_hz=NUTATION_SW_HZ,
                        o1_hz=NUTATION_O1_HZ, d1_s=NUTATION_D1_S,
                        grpdly=NUTATION_GRPDLY, sfo1_mhz=500.13,
                        title="synthetic nutation step"):
    """A Bruker acqus in TopSpin 3.x's shape for one quick 1D (a nutation
    step or a ladder rung): TD, SW_h, RG, GRPDLY, P[1] = the pulse,
    PLW[1] = the channel-1 power (watts; power_db on the PLdB scale),
    D[1] = the relaxation delay, D[11] = 0."""
    d_vals = ["0"] * 64
    d_vals[1] = "%.6g" % d1_s
    p_vals = ["0"] * 64
    p_vals[1] = "%.6g" % p1_us
    plw = ["0"] * 64
    plw[1] = "%.6g" % (10.0 ** (-power_db / 10.0))
    pl = ["120"] * 64
    return (
        "##TITLE= Parameter file, %s\n"
        "##$PULPROG= <zg>\n"
        "##$TD= %d\n"
        "##$SW_h= %.10g\n"
        "##$SFO1= %.6g\n"
        "##$O1= %.6g\n"
        "##$RG= %.6g\n"
        "##$NS= 1\n"
        "##$DS= 0\n"
        "##$DE= 6.5\n"
        "##$D= (0..63)\n%s\n"
        "##$P= (0..63)\n%s\n"
        "##$PL= (0..63)\n%s\n"
        "##$PLW= (0..63)\n%s\n"
        "##$BYTORDA= 0\n"
        "##$DTYPA= 0\n"
        "##$DECIM= 2901.33333333333\n"
        "##$DSPFVS= 20\n"
        "##$GRPDLY= %.6g\n"
        "##END=\n" % (title, td, sw_hz, sfo1_mhz, o1_hz, rg, " ".join(d_vals),
                       " ".join(p_vals), " ".join(pl), " ".join(plw), grpdly)
    ).encode("ascii")


def synth_fid_bytes(amp, decay_per_s, rng, td=NUTATION_TD, sw_hz=NUTATION_SW_HZ,
                    grpdly=NUTATION_GRPDLY, f0_hz=NUTATION_F0_HZ,
                    noise_sigma=NUTATION_NOISE_SIGMA, clip=False):
    """One quick-1D FID as a little-endian int32 Bruker fid (re/im
    interleaved, padded to 1024 bytes): a group-delay transient of
    `grpdly` near-zero points, then amp exp(-decay t) exp(2 pi i f0 t),
    white Gaussian noise of `noise_sigma` counts per channel throughout.
    clip=True drives the FID 50% over the int32 full scale, clamped -- a
    saturated receiver."""
    import math
    import struct
    n = td // 2
    g = int(round(grpdly))
    if clip:
        amp = 1.5 * INT32_MAX
    dt = 1.0 / sw_hz
    step = complex(math.exp(-decay_per_s * dt) * math.cos(2.0 * math.pi * f0_hz * dt),
                   math.exp(-decay_per_s * dt) * math.sin(2.0 * math.pi * f0_hz * dt))
    out = bytearray()
    z = complex(amp, 0.0)
    pack = struct.Struct("<ii").pack
    for i in range(n):
        if i < g:
            re = 0.1 * rng.gauss(0.0, noise_sigma)
            im = 0.1 * rng.gauss(0.0, noise_sigma)
        else:
            re = z.real + rng.gauss(0.0, noise_sigma)
            im = z.imag + rng.gauss(0.0, noise_sigma)
            z *= step
        vi = int(round(re))
        vq = int(round(im))
        vi = INT32_MAX if vi > INT32_MAX else (-INT32_MAX if vi < -INT32_MAX else vi)
        vq = INT32_MAX if vq > INT32_MAX else (-INT32_MAX if vq < -INT32_MAX else vq)
        out += pack(vi, vq)
    pad = (-len(out)) % 1024
    out += b"\x00" * pad
    return bytes(out)


def nutation_fid_bytes(p1_us, p90_true_us, a90, rng, td=NUTATION_TD,
                       sw_hz=NUTATION_SW_HZ, grpdly=NUTATION_GRPDLY,
                       f0_hz=NUTATION_F0_HZ, decay0=NUTATION_DECAY0_PER_S,
                       rd_rate=NUTATION_RD_RATE_PER_S,
                       noise_sigma=NUTATION_NOISE_SIGMA, clip=False):
    """One nutation step as a little-endian int32 Bruker fid (re/im
    interleaved, padded to 1024 bytes): a group-delay transient of
    `grpdly` near-zero points, then A exp(-r t) exp(2 pi i f0 t) with
    A = a90 |sin(pi/2 P1/P90_true)| and the radiation-damping-shortened
    decay r = decay0 + rd_rate |sin theta|, white Gaussian noise of
    `noise_sigma` counts per channel throughout.  clip=True drives the
    step 50% over the int32 full scale, clamped -- a saturated receiver,
    which the report must exclude from the fit."""
    import math
    theta = math.pi / 2.0 * p1_us / p90_true_us
    amp = a90 * abs(math.sin(theta))
    r = decay0 + rd_rate * abs(math.sin(theta))
    return synth_fid_bytes(amp, r, rng, td=td, sw_hz=sw_hz, grpdly=grpdly,
                           f0_hz=f0_hz, noise_sigma=noise_sigma, clip=clip)


def dense_ladder_levels(max_rg, rg_ref=None):
    """The console's dense-ladder levels: 2^(k/2) for k = 0.. while
    <= max_rg, plus the reference gain (max_rg / 4 unless given) and
    max_rg exactly when no level is already within 0.5% of them;
    ascending."""
    levels = []
    k = 0
    while 2.0 ** (k / 2.0) <= max_rg * (1.0 + 1e-9):
        levels.append(2.0 ** (k / 2.0))
        k += 1
    for extra in ((max_rg / 4.0) if rg_ref is None else rg_ref, max_rg):
        if not any(abs(l / extra - 1.0) <= 0.005 for l in levels):
            levels.append(float(extra))
    return sorted(levels)


def dense_compression_true(rg, max_rg, cmax=DENSE_COMPRESSION_MAX,
                           power=DENSE_COMPRESSION_POWER):
    """The injected amplitude compression c(RG), relative to RG -> 0."""
    return 1.0 - cmax * (rg / max_rg) ** power


def _iso_plus(iso, seconds):
    t = calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t + int(seconds)))


def dense_ladder_block(max_rg=DENSE_MAX_RG, rg_ref=None,
                       compression_max=DENSE_COMPRESSION_MAX,
                       drift_pct_per_min=DENSE_DRIFT_PCT_PER_MIN,
                       seed=DENSE_SEED, start="2026-10-19T10:00:00",
                       expno0=DENSE_EXPNO0, td=NUTATION_TD, sw_hz=NUTATION_SW_HZ,
                       f0_hz=NUTATION_F0_HZ, o1_hz=NUTATION_O1_HZ,
                       sfo1_mhz=500.13, grpdly=NUTATION_GRPDLY,
                       a1=DENSE_A1_COUNTS_PER_RG,
                       noise_sigma_per_rg=DENSE_NOISE_SIGMA_PER_RG,
                       p90_us=DENSE_P90_US, power_db=DENSE_POWER_DB,
                       decay_per_s=NUTATION_DECAY0_PER_S, legacy=True):
    """The dense ladder as the console script records it plus its raw
    files: {"files": [(arcname, bytes)...] (acqus + fid for every rung,
    the four legacy rungs 10/14/15/16 included when legacy=True),
    "rg_ladder": the calibration.rg_ladder entries (legacy first, the
    dense ones with "visit"), "rg_ladder_dense": the block record,
    "experiments": the experiments[] entries (role rg_ladder, stamped
    DENSE_RUNG_SPACING_S apart from `start`), "truth": what was
    injected}.  Amplitude law: a1 x RG x c(RG) x (1 + beta t), noise
    sigma = noise_sigma_per_rg x RG (constant SNR)."""
    rng = random.Random(seed)
    levels = dense_ladder_levels(max_rg, rg_ref)
    rg_ref_eff = (max_rg / 4.0) if rg_ref is None else float(rg_ref)
    perm = list(levels)
    rng.shuffle(perm)
    order = perm + perm[::-1]
    rungs = []                      # (expno, rg, visit)
    if legacy:
        rungs += [(e, r, None) for e, r in DENSE_LEGACY] + [(16, float(max_rg), None)]
    for k, rg in enumerate(order):
        rungs.append((expno0 + k, float(rg), 1 if k < len(perm) else 2))
    beta = drift_pct_per_min / 100.0
    files, entries, exps, truth_rungs = [], [], [], []
    for k, (expno, rg, visit) in enumerate(rungs):
        t_s = k * DENSE_RUNG_SPACING_S
        c = dense_compression_true(rg, max_rg, compression_max)
        amp = a1 * rg * c * (1.0 + beta * t_s / 60.0)
        files.append(("data/%d/acqus" % expno,
                      nutation_acqus_text(p90_us, rg=rg,
                                          power_db=power_db + SMALL_FLIP_EXTRA_DB,
                                          td=td, sw_hz=sw_hz, o1_hz=o1_hz,
                                          d1_s=2.0, grpdly=grpdly,
                                          sfo1_mhz=sfo1_mhz,
                                          title="synthetic ladder rung")))
        files.append(("data/%d/fid" % expno,
                      synth_fid_bytes(amp, decay_per_s, rng, td=td, sw_hz=sw_hz,
                                      grpdly=grpdly, f0_hz=f0_hz,
                                      noise_sigma=noise_sigma_per_rg * rg)))
        entry = {"expno": expno, "rg": rg, "tip_deg": 1.0}
        if visit is not None:
            entry["visit"] = visit
        entries.append(entry)
        started = _iso_plus(start, t_s)
        exps.append({"expno": expno, "role": "rg_ladder", "pulprog": "zg",
                     "td": td, "td1_rows": 1, "sw_hz": sw_hz, "o1_hz": o1_hz,
                     "rg": rg, "ns": 1, "aq_s_per_row": td / (2.0 * sw_hz),
                     "started_local": started,
                     "finished_local": _iso_plus(start, t_s + 2)})
        truth_rungs.append({"expno": expno, "rg": rg, "visit": visit,
                            "t_min": t_s / 60.0, "compression": c,
                            "amplitude_counts": amp})
    dense_expnos = [e for e, _r, v in rungs if v is not None]
    c_ref = dense_compression_true(rg_ref_eff, max_rg, compression_max)
    c_noise = dense_compression_true(max_rg, max_rg, compression_max)
    return {
        "files": files, "rg_ladder": entries, "experiments": exps,
        "rg_ladder_dense": {"expnos": dense_expnos, "levels": levels,
                            "seed": seed, "visits": 2, "skipped": False},
        "truth": {"max_rg": float(max_rg), "rg_ref": rg_ref_eff,
                  "rg_noise": float(max_rg), "levels": levels,
                  "n_levels": len(levels), "n_rungs": len(rungs),
                  "compression_by_level": [
                      dense_compression_true(l, max_rg, compression_max)
                      for l in levels],
                  "compression_max": compression_max,
                  "compression_power": DENSE_COMPRESSION_POWER,
                  "compression_law": "c(RG) = 1 - compression_max x "
                                     "(RG / max_rg)^compression_power",
                  "drift_pct_per_min": drift_pct_per_min,
                  "a1_counts_per_rg": a1, "noise_sigma_per_rg": noise_sigma_per_rg,
                  "ratio_of_ratios_true": c_noise / c_ref,
                  "seed": seed, "order": order, "rungs": truth_rungs}}


def nutation_block(p90_declared_us=NUTATION_P90_DECLARED_US,
                   p90_true_us=NUTATION_P90_TRUE_US, a90=NUTATION_A90_COUNTS,
                   rg=NUTATION_RG, power_db=NUTATION_POWER_DB,
                   clip_mults=(), expno0=NUTATION_EXPNO0, seed=20261019,
                   started="2026-10-19T10:30:00", sw_hz=NUTATION_SW_HZ,
                   td=NUTATION_TD, o1_hz=NUTATION_O1_HZ,
                   f0_hz=NUTATION_F0_HZ, noise_sigma=NUTATION_NOISE_SIGMA,
                   with_experiments=None):
    """The nutation block as the console script records it plus its raw
    files: {"files": [(arcname, bytes)...], "calibration": the
    calibration.nutation object, "experiments": the experiments[]
    entries (role "nutation"; empty when the shipped schema does not yet
    accept the role and with_experiments is None), "truth": what was
    injected}.  Steps are acquired in NUTATION_ORDER (P1 = P90 first) at
    expno0, expno0 + 1, ...; clip_mults names multipliers driven into
    saturation."""
    rng = random.Random(seed)
    expnos, p1s, mults, files, exps = [], [], [], [], []
    if with_experiments is None:
        with_experiments = schema_role_accepted("nutation")
    for k, mult in enumerate(NUTATION_ORDER):
        expno = expno0 + k
        p1 = mult * p90_declared_us
        clip = any(abs(mult - c) < 1e-9 for c in clip_mults)
        files.append(("data/%d/acqus" % expno,
                      nutation_acqus_text(p1, rg=rg, power_db=power_db,
                                          td=td, sw_hz=sw_hz, o1_hz=o1_hz)))
        files.append(("data/%d/fid" % expno,
                      nutation_fid_bytes(p1, p90_true_us, a90, rng, td=td,
                                         sw_hz=sw_hz, f0_hz=f0_hz,
                                         noise_sigma=noise_sigma,
                                         clip=clip)))
        expnos.append(expno)
        p1s.append(p1)
        mults.append(mult)
        if with_experiments:
            exps.append({"expno": expno, "role": "nutation", "pulprog": "zg",
                         "td": td, "td1_rows": 1, "sw_hz": sw_hz,
                         "o1_hz": o1_hz, "rg": rg, "ns": 1,
                         "aq_s_per_row": td / (2.0 * sw_hz),
                         "started_local": started, "finished_local": started})
    cal = {"expnos": expnos, "p1_us": p1s, "multipliers": mults,
           "p90_us_assumed": p90_declared_us, "power_db": power_db,
           "power_parname": "PLdB1", "rg": rg, "d1_s": NUTATION_D1_S,
           "td": td, "skipped": False}
    truth = {"p90_true_us": p90_true_us, "a90_counts": a90, "rg": rg,
             "clipped_mults": list(clip_mults),
             "clipped_expnos": [expno0 + k for k, m in enumerate(NUTATION_ORDER)
                                if any(abs(m - c) < 1e-9 for c in clip_mults)],
             "decay0_per_s": NUTATION_DECAY0_PER_S,
             "rd_rate_per_s": NUTATION_RD_RATE_PER_S,
             "noise_sigma_counts": noise_sigma, "seed": seed,
             "experiments_written": bool(with_experiments)}
    return {"files": files, "calibration": cal, "experiments": exps,
            "truth": truth}


def fake_data_files(ser_mib=None):
    """(in-zip path, bytes) pairs standing in for Bruker experiment dirs.
    With ser_mib set, the 'ser' payload is that many MiB of incompressible
    random bytes -- used by testing/test_upload_integration.sh to exercise
    the uploader's chunked multipart path with a realistically large bundle."""
    acqus = (
        "##TITLE= Parameter file, synthetic\n"
        "##$PULPROG= <zgnoise2d>\n"
        "##$TD= 32768\n"
        "##$NS= 1\n"
        "##END=\n"
    ).encode("ascii")
    if ser_mib:
        # os.urandom is incompressible, so the zip lands at ~ser_mib MiB.
        ser = b"".join(os.urandom(1024 * 1024) for _ in range(ser_mib))
    else:
        # Deterministic pseudo-noise 'ser' payload (content is irrelevant; the
        # selftest only checks that the sha256 in meta.json matches the bytes).
        rng = random.Random(20260825)
        ser = bytes(bytearray(rng.getrandbits(8) for _ in range(4096)))
    return [
        ("data/11/acqus", acqus),
        ("data/12/acqus", acqus),
        ("data/12/ser", ser),
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a synthetic spin-noise bundle zip for selftest.")
    parser.add_argument("--schema-version",
                        choices=("1.0", "1.1", "1.2", "2.0"),
                        default="2.0",
                        help="schema_version the bundle declares (default "
                             "2.0, the current vendor-neutral contract with "
                             "vendor + instrument blocks; 1.2 is the last "
                             "Bruker-only vintage that the TopSpin "
                             "orchestrator still writes; 1.1 omits the "
                             "'clock_audit' object and 1.0 additionally "
                             "omits 'software', as real bundles of those "
                             "vintages did)")
    parser.add_argument("--out-dir", default=None,
                        help="directory for the zip (default: a fresh "
                             "'synthetic_bundles' dir under testing/)")
    parser.add_argument("--ser-mib", type=int, default=None,
                        help="size of the synthetic ser payload in MiB "
                             "(random, incompressible) -- for exercising the "
                             "uploader's chunked path; default: tiny")
    parser.add_argument("--nutation", action="store_true",
                        help="add the v0.8.2 nutation block: calibration."
                             "nutation plus real int32 fids at expnos %d.. "
                             "synthesized from a true P90 of %.4g us while "
                             "the bundle declares %.4g us"
                             % (NUTATION_EXPNO0, NUTATION_P90_TRUE_US,
                                NUTATION_P90_DECLARED_US))
    parser.add_argument("--nutation-clip", default="",
                        help="comma-separated multipliers (e.g. '1.0') whose "
                             "nutation step is driven into int32 saturation")
    parser.add_argument("--nutation-p90-true", type=float,
                        default=NUTATION_P90_TRUE_US)
    parser.add_argument("--nutation-p90-declared", type=float,
                        default=NUTATION_P90_DECLARED_US)
    parser.add_argument("--dense-ladder", action="store_true",
                        help="add the v0.8.2 dense receiver-gain ladder: the "
                             "four legacy rungs plus two visits of every "
                             "level (expnos %d..) with a known compression "
                             "curve (%.0f%% at max_rg %.4g) and drift (%+.2f "
                             "%%/min); calibration.rg_ladder_dense written"
                             % (DENSE_EXPNO0, 100 * DENSE_COMPRESSION_MAX,
                                DENSE_MAX_RG, DENSE_DRIFT_PCT_PER_MIN))
    parser.add_argument("--dense-compression", type=float,
                        default=DENSE_COMPRESSION_MAX,
                        help="amplitude compression at max_rg (default %.2f)"
                             % DENSE_COMPRESSION_MAX)
    args = parser.parse_args(argv)

    out_dir = args.out_dir or os.path.join(REPO, "testing", "synthetic_bundles")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    version = repo_version()
    meta = build_meta(args.schema_version, version)

    files = fake_data_files(args.ser_mib)
    if args.dense_ladder:
        block = dense_ladder_block(compression_max=args.dense_compression)
        files += block["files"]
        meta["calibration"]["rg_ladder"] = block["rg_ladder"]
        meta["calibration"]["rg_ladder_dense"] = block["rg_ladder_dense"]
        meta["calibration"]["p90_us"] = DENSE_P90_US
        meta["calibration"]["p90_power_db_or_w"] = (
            "%.4g dB (PLdB 1)" % DENSE_POWER_DB)
        meta["experiments"] = block["experiments"] + meta["experiments"]
        meta["dense_ladder_truth"] = block["truth"]
        info("dense ladder: %d rungs (%d levels x 2 visits + %d legacy), "
             "compression %.2f%% at max_rg %.4g, drift %+.2f %%/min, seed %d"
             % (block["truth"]["n_rungs"], block["truth"]["n_levels"],
                len(DENSE_LEGACY) + 1, 100 * args.dense_compression,
                DENSE_MAX_RG, DENSE_DRIFT_PCT_PER_MIN, DENSE_SEED))
    if args.nutation:
        clip = [float(v) for v in args.nutation_clip.split(",") if v.strip()]
        block = nutation_block(p90_declared_us=args.nutation_p90_declared,
                               p90_true_us=args.nutation_p90_true,
                               clip_mults=clip)
        files += block["files"]
        meta["calibration"]["p90_us"] = args.nutation_p90_declared
        meta["calibration"]["p90_power_db_or_w"] = (
            "%.4g dB (PLdB 1)" % NUTATION_POWER_DB)
        meta["calibration"]["nutation"] = block["calibration"]
        meta["experiments"] += block["experiments"]
        meta["nutation_truth"] = block["truth"]
        info("nutation block: expnos %s, true P90 %.4g us, declared %.4g us%s"
             % (block["calibration"]["expnos"], args.nutation_p90_true,
                args.nutation_p90_declared,
                ("; clipped %s" % block["truth"]["clipped_expnos"]) if clip
                else ""))
        if not block["experiments"]:
            info("nutation block: the shipped schema does not list the role "
                 "'nutation' yet -- experiments[] entries omitted (the report "
                 "reads the steps through calibration.nutation.expnos)")
    for arc_name, payload in files:
        meta["checksums"][arc_name] = "sha256:" + hashlib.sha256(payload).hexdigest()

    stamp = time.strftime("%Y%m%d_%H%M%SZ", time.gmtime())
    suffix = "%04x" % random.randint(0, 0xFFFF)
    bundle_name = "spinnoise_%s_%s_%s.zip" % (FACILITY_SLUG, stamp, suffix)
    bundle_path = os.path.join(out_dir, bundle_name)

    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta, indent=2) + "\n")
        for arc_name, payload in files:
            zf.writestr(arc_name, payload)

    info("synthetic bundle (schema %s, program %s): %s"
         % (args.schema_version, version, bundle_path))
    print(bundle_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
