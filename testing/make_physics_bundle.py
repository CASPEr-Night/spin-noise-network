#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_physics_bundle.py -- synthesize a physically sensible spin-noise bundle
with a KNOWN injected feature, for injection-recovery validation of
analysis/facility_report.py.

    python3 testing/make_physics_bundle.py --feature bump --amp 1.5 --fwhm 12
    python3 testing/make_physics_bundle.py --feature dip  --amp 0.35
    python3 testing/make_physics_bundle.py --feature none            # null case

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

meta.json declares run_mode 'synthetic-injection' (schema enum, v1.1):
the report generator runs its full science path on such bundles but
watermarks the report as validation, never as a measurement.

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

Python 3 + numpy only.
"""

from __future__ import print_function

import argparse
import hashlib
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
RG_NOISE = 101.0
RG_REF = 25.0
RG_LADDER = [(10, 1.0), (14, 8.0), (15, 64.0), (16, 101.0)]
FLOOR_C2HZ = 40000.0       # flat noise floor, counts^2/Hz (comfortably int32)
REF_A0 = 2.0e6             # injected reference amplitude, counts
REF_DECAY_S = 3.0          # reference decay rate 1/s (line ~1 Hz + inhomog.)
REF_FWHM_HZ = 6.0          # reference line FWHM via extra Lorentzian decay


def info(msg):
    print(msg, file=sys.stderr)


def acqus_text(td, sw, rg, o1=0.0, pulprog="zgnoise2d", grpdly=GRPDLY):
    return (
        "##TITLE= Parameter file, synthetic injection bundle\n"
        "##$PULPROG= <%s>\n"
        "##$TD= %d\n"
        "##$SW_h= %.10g\n"
        "##$SFO1= 600.13\n"
        "##$O1= %.6g\n"
        "##$RG= %.6g\n"
        "##$NS= 1\n"
        "##$BYTORDA= 0\n"
        "##$DTYPA= 0\n"
        "##$DECIM= 1664\n"
        "##$DSPFVS= 20\n"
        "##$GRPDLY= %.6g\n"
        "##END=\n" % (pulprog, td, sw, o1, rg, grpdly)
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


def build_bundle(args):
    rng = np.random.default_rng(args.seed)
    sign = {"bump": 1.0, "dip": -1.0, "none": 0.0}[args.feature]
    a_inj = sign * abs(args.amp) if sign else 0.0
    b_inj = a_inj * args.b_over_a
    info("injected: a=%.4g b=%.4g f0=%.4g Hz fwhm=%.4g Hz"
         % (a_inj, b_inj, F0_HZ, args.fwhm))

    n_row = TD_ROW // 2
    files = []          # (arcname, bytes)

    # setup expno 1: acqus only
    files.append(("data/1/acqus",
                  acqus_text(TD_LADDER, SW_HZ, 1.0, pulprog="zg")))

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
                      acqus_text(TD_LADDER, SW_HZ, rg, pulprog="zg")))
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
                      acqus_text(TD_ROW, SW_HZ, RG_REF, pulprog="zg2d")))
        files.append(("data/%d/acqu2s" % expno, acqu2s_text(REF_ROWS)))
        files.append(("data/%d/ser" % expno, to_bruker_int32(rows)))

    # noise block (12)
    noise_rows = [synth_noise_row(rng, n_row, SW_HZ, FLOOR_C2HZ,
                                  a_inj, b_inj, F0_HZ, args.fwhm)
                  for _ in range(N_NOISE_ROWS)]
    files.append(("data/12/acqus", acqus_text(TD_ROW, SW_HZ, RG_NOISE)))
    files.append(("data/12/acqu2s", acqu2s_text(N_NOISE_ROWS)))
    files.append(("data/12/ser", to_bruker_int32(noise_rows)))

    # ---- meta.json
    with open(os.path.join(REPO, "VERSION")) as fh:
        version = fh.read().strip()
    utc = time.gmtime()
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", utc)
    t0 = time.strftime("%Y-%m-%dT%H:%M:%S", utc)
    aq_row = n_row / SW_HZ

    def expmeta(expno, role, pulprog, td, rows, rg, aq):
        return {"expno": expno, "role": role, "pulprog": pulprog,
                "td": td, "td1_rows": rows, "sw_hz": SW_HZ, "o1_hz": 0.0,
                "rg": rg, "ns": 1, "aq_s_per_row": aq,
                "started_local": t0, "finished_local": t0}

    meta = {
        "schema_version": "1.1",
        "program_version": version,
        "software": {"script_version": version, "schema_version": "1.1",
                     "script_sha256": "unavailable",
                     "run_mode": "synthetic-injection"},
        "created_utc": created,
        "local_timezone_offset_min": 0,
        "facility": {"institution": "Injection-recovery validation (synthetic)",
                     "city": "Nowhere", "country": "n/a",
                     "facility_slug": FACILITY_SLUG,
                     "contact_email": "jwbquantum@gmail.com",
                     "contact_consent": True},
        "spectrometer": {"topspin_version": "n/a (synthetic)",
                         "h1_freq_mhz": 600.13, "field_tesla": 14.095,
                         "console": "synthetic",
                         "probe_string": "synthetic 5 mm probe",
                         "probe_type": ("N2-cryo" if a_inj > 0 else "RT"),
                         "coil_temp_k": None, "preamp_temp_k": None},
        "sample": {"description": "synthetic water (numerical)",
                   "h2o_fraction_pct": 100.0, "d2o_pct": 0.0,
                   "additives": "none", "tube_od_mm": 5.0,
                   "sample_volume_ul": 550.0, "vt_setpoint_k": 298.0},
        "environment": {"locked": False, "lock_sweep_confirmed_off": True,
                        "operator_notes":
                            "SYNTHETIC injection bundle; injected a=%.4g "
                            "b/a=%.3g f0=%.4g Hz fwhm=%.4g Hz; never a "
                            "measurement" % (a_inj, args.b_over_a, F0_HZ,
                                             args.fwhm)},
        "calibration": {"p90_us": 10.0, "p90_power_db_or_w": "n/a",
                        "rg_ladder": ladder_meta, "topshim_ok": False},
        "experiments": [
            expmeta(1, "setup", "zg", TD_LADDER, 1, 1.0, TD_LADDER / 2 / SW_HZ)]
        + [expmeta(e, "rg_ladder", "zg", TD_LADDER, 1, rg,
                   TD_LADDER / 2 / SW_HZ) for e, rg in RG_LADDER]
        + [expmeta(11, "reference_open", "zg2d", TD_ROW, REF_ROWS, RG_REF, aq_row),
           expmeta(12, "noise", "zgnoise2d", TD_ROW, N_NOISE_ROWS, RG_NOISE, aq_row),
           expmeta(13, "reference_close", "zg2d", TD_ROW, REF_ROWS, RG_REF, aq_row)],
        "checksums": {},
        "injection_truth": {   # extra key (schema allows additional props)
            "feature": args.feature, "amp_norm": a_inj,
            "b_over_a": args.b_over_a, "f0_hz": F0_HZ,
            "fwhm_hz": args.fwhm, "floor_counts2perhz": FLOOR_C2HZ,
            "ref_a0_counts": REF_A0, "seed": args.seed},
    }
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
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)
    if args.feature == "dip" and abs(args.amp) >= 1.0:
        ap.error("a dip deeper than the floor is unphysical (--amp < 1)")
    return build_bundle(args)


if __name__ == "__main__":
    sys.exit(main())
