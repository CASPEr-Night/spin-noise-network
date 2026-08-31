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
the pulse-program texts written into each expno (see PP_TEXTS: extra
30m/p1/DE terms, and a second d1 per zg2d reference row) times (1 + F),
plus a constant per-block overhead and ms-scale jitter. The report's
pulse-program-derived fit must recover F within its stated uncertainty;
without refinement the mis-modeled reference blocks are gate-excluded,
so --expect-refined catches a dead refinement. --de-us sets DE (default
6.5 us, the stock value; crank it, e.g. 20000, to make the per-row DE
shortfall itself many sigma).
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
D1_REF_S = 2.0             # relaxation delay, references/ladder (run script)
D1_NOISE_S = 0.05          # zgnoise2d loop delay (spent TWICE per row)
DE_US_DEFAULT = 6.5        # stock pre-acquisition delay DE, microseconds
P1_US = 10.0               # small-flip pulse length in the pulsed sequences

# Pulse-program texts written into each expno, mirroring what real
# TopSpin stores in data/<expno>/pulseprogram. The report's clock-audit
# refinement parses these texts (NOT a name-keyed table) to derive each
# block's true OCXO duration, so the fixture's wall clocks below are
# built from the same structures. zgnoise2d is the repo-shipped
# sequence verbatim (2 x d1 per row); the zg2d stand-in follows its
# documented lineage ('zg2d with the pulse line deleted') and spends
# 2 x d1 + p1 per row; the zg stand-in carries the library sequence's
# 30m loop/write delays.
PP_TEXT_ZGNOISE2D = (
    ";zgnoise2d (fixture copy of topspin/pp/zgnoise2d)\n"
    "#include <Avance.incl>\n"
    "1 ze\n"
    "2 d1\n"
    "  go=2 ph31\n"
    "  d1 wr #0 if #0 ze\n"
    "  lo to 2 times td1\n"
    "exit\n"
    "ph31=0\n").encode("ascii")
PP_TEXT_ZG2D = (
    ";zg2d (fixture stand-in: zgnoise2d lineage WITH the pulse line)\n"
    "1 ze\n"
    "2 d1\n"
    "  p1 ph1\n"
    "  go=2 ph31\n"
    "  d1 wr #0 if #0 zd\n"
    "  lo to 2 times td1\n"
    "exit\n"
    "ph1=0\n"
    "ph31=0\n").encode("ascii")
PP_TEXT_ZG = (
    ";zg (fixture stand-in for the library 1D sequence, with its 30m\n"
    ";loop and write delays)\n"
    "1 ze\n"
    "2 30m\n"
    "  d1\n"
    "  p1 ph1\n"
    "  go=2 ph31\n"
    "30m mc #0 to 2 F0(zd)\n"
    "exit\n"
    "ph1=0\n"
    "ph31=0\n").encode("ascii")
PP_TEXTS = {"zg": PP_TEXT_ZG, "zg2d": PP_TEXT_ZG2D,
            "zgnoise2d": PP_TEXT_ZGNOISE2D}


def info(msg):
    print(msg, file=sys.stderr)


def acqus_text(td, sw, rg, o1=0.0, pulprog="zgnoise2d", grpdly=GRPDLY,
               d1_s=D1_NOISE_S, de_us=DE_US_DEFAULT, p1_us=P1_US):
    # D and P arrays formatted like real Bruker acqus: "(0..63)" header,
    # values on continuation lines (element 1 carries D1/P1; rest zero).
    # FRQLO3 is a DBL_MAX sentinel: real consoles write these for unset
    # doubles, and parse_jcamp must survive them (regression coverage
    # for the OverflowError found in review against the 2020 dataset).
    d_line = "0 %.6g " % d1_s + " ".join(["0"] * 62)
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


def build_clock_audit(offset, rng, de_us=DE_US_DEFAULT):
    """Schema-1.2 clock_audit object with a KNOWN injected fractional
    clock offset: each acquisition block's wall duration is its OCXO-implied
    duration times (1 + offset), plus a constant 0.18 s per-block overhead
    (disk writes; the fit's intercept must absorb it) and +/-4 ms jitter
    (NTP timestamp granularity). A setup block with no OCXO prediction is
    included to exercise the fit's exclusion path.

    PHYSICAL timing model (mirrors the fixture PP_TEXTS, which the
    report's refinement parses): the RECORDED ocxo_expected_s is the
    acquisition-side formula rows*(AQ + n_d1*D1), exactly like
    spin_noise_run.py (ladder/refs n_d1=1, noise n_d1=2) -- while the
    TRUE (wall) duration follows the pulse-program text: the zg ladder
    additionally spends 2x30m + p1 + DE per pass, the zg2d references a
    SECOND d1 + p1 + DE per row (their recorded expectation is ~9.5%
    short, so a fit without refinement gate-excludes them; that is what
    --expect-refined guards), and zgnoise2d spends DE per row. The
    report's pulse-program-derived fit removes the shortfalls; its
    recorded-model comparison fit keeps them."""
    de_s = de_us * 1e-6
    p1_s = P1_US * 1e-6
    aq_lad = TD_LADDER / 2 / SW_HZ
    aq_row = TD_ROW / 2 / SW_HZ
    lad_rec = aq_lad + D1_REF_S
    lad_true = aq_lad + D1_REF_S + p1_s + de_s + 0.060   # two 30m lines
    ref_rec = aq_row + D1_REF_S
    ref_true = aq_row + 2.0 * D1_REF_S + p1_s + de_s
    noi_rec = aq_row + 2.0 * D1_NOISE_S
    noi_true = noi_rec + de_s
    # (expno, role, rows, rec_per_row, true_per_row); rows None = setup
    plan = [(1, "setup", None, None, None)]
    plan += [(e, "rg_ladder", 1, lad_rec, lad_true) for e, _rg in RG_LADDER]
    plan += [(11, "reference_open", REF_ROWS, ref_rec, ref_true),
             (12, "noise", N_NOISE_ROWS, noi_rec, noi_true),
             (13, "reference_close", REF_ROWS, ref_rec, ref_true)]
    t_ms = 1787000000000            # arbitrary 2026-ish epoch
    blocks = []
    for expno, role, rows, rec_row, true_row in plan:
        if rows is None:
            ocxo_s = None
            dur_ms = 120000         # setup: tune/shim/dialogs, wall only
        else:
            ocxo_s = rows * rec_row
            true_s = rows * true_row
            dur_ms = int(round(true_s * 1000.0 * (1.0 + offset)
                               + 180.0 + rng.integers(-4, 5)))
        blocks.append({"expno": expno, "role": role,
                       "wall_start_ms": t_ms,
                       "wall_end_ms": t_ms + dur_ms,
                       "ocxo_expected_s": ocxo_s})
        t_ms += dur_ms + 2500       # inter-block gap (dataset switching)
    return {"blocks": blocks,
            "ntp_status_raw": "synthetic clock-audit fixture (no NTP "
                              "daemon was queried)",
            "workstation_time_source": "synthetic"}


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
                  acqus_text(TD_LADDER, SW_HZ, 1.0, pulprog="zg",
                             d1_s=D1_REF_S, de_us=args.de_us)))
    files.append(("data/1/pulseprogram", PP_TEXTS["zg"]))

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
        files.append(("data/%d/pulseprogram" % expno, PP_TEXTS["zg"]))
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
                      acqus_text(TD_ROW, SW_HZ, RG_REF, pulprog="zg2d",
                                 d1_s=D1_REF_S, de_us=args.de_us)))
        files.append(("data/%d/pulseprogram" % expno, PP_TEXTS["zg2d"]))
        files.append(("data/%d/acqu2s" % expno, acqu2s_text(REF_ROWS)))
        files.append(("data/%d/ser" % expno, to_bruker_int32(rows)))

    # noise block (12)
    noise_rows = [synth_noise_row(rng, n_row, SW_HZ, FLOOR_C2HZ,
                                  a_inj, b_inj, F0_HZ, args.fwhm)
                  for _ in range(N_NOISE_ROWS)]
    files.append(("data/12/acqus",
                  acqus_text(TD_ROW, SW_HZ, RG_NOISE,
                             d1_s=D1_NOISE_S, de_us=args.de_us)))
    files.append(("data/12/pulseprogram", PP_TEXTS["zgnoise2d"]))
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
        "schema_version": "1.2",
        "program_version": version,
        "software": {"script_version": version, "schema_version": "1.2",
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
            "ref_a0_counts": REF_A0, "seed": args.seed,
            "clock_fractional_offset": args.clock_offset,
            "clock_de_us": args.de_us},
    }

    # ---- optional clock audit with a known injected fractional offset
    if args.clock_offset is not None:
        meta["clock_audit"] = build_clock_audit(args.clock_offset, rng,
                                                args.de_us)
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
                         "behavior)")
    ap.add_argument("--de-us", type=float, default=DE_US_DEFAULT,
                    help="pre-acquisition delay DE in microseconds, written "
                         "to acqus AND spent per scan in the clock-audit "
                         "wall durations but ABSENT from the recorded "
                         "expectations (the physical shortfall the report's "
                         "acqus-refined fit must remove); default %.3g"
                         % DE_US_DEFAULT)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)
    if args.feature == "dip" and abs(args.amp) >= 1.0:
        ap.error("a dip deeper than the floor is unphysical (--amp < 1)")
    return build_bundle(args)


if __name__ == "__main__":
    sys.exit(main())
