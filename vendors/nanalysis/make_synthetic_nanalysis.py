#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_synthetic_nanalysis.py -- build a fake NMReady spin-noise session so the
whole Nanalysis chain (reader -> bundle -> validation) is testable today
without an instrument.

    python3 vendors/nanalysis/make_synthetic_nanalysis.py [--out-dir D]

Prints exactly one line on stdout: the session directory path.

The synthetic JCAMP-DX files copy the exact label vocabulary and NTUPLES
FID layout of a REAL NMReady 60 MHz export (Nanalysis Corp.'s open-source
jcamp-parser test corpus, file NMReady_1D_1H_20210909_Test_formates.dx,
writer 'Nanalysis NMReady v2.2.4.5'): DATA CLASS=NTUPLES, VAR_FORM=AFFN,
(X++(R..R)) / (X++(I..I)) pages, FACTOR scaling, and the Bruker-style
$-labels ($TD, $SWH, $O1, $RECVR_GAIN, $NS, $AQ, ...).  Values are
deterministic (seeded) so tests can assert exact numeric recovery.

The session is stamped run_mode "desktest" via answers.json run_mode_hint:
a synthetic bundle can never masquerade as data.

Session layout written (README.md convention):
    10/14/15/16_sn_ladder_*.dx   rg_ladder      (RECVR_GAIN 1, 4, 8, 14)
    11_sn_ref_open.dx            reference_open
    20..22_sn_noise.dx           noise          (RECVR_GAIN 14)
    13_sn_ref_close.dx           reference_close
    answers.json                 operator questionnaire

Python 3 standard library only, nothing newer than 3.6.
Maintainer: John W. Blanchard, jwbquantum@gmail.com
"""

from __future__ import print_function

import argparse
import json
import math
import os
import random
import sys
import tempfile

# Constants mirroring the real 60 MHz corpus file (see module docstring).
SFO1_MHZ = 60.05649983018314
BF1_MHZ = 60.05619954768398
O1_HZ = 300.28249915091567
FIELD_T = 1.410588
SWH_HZ = 735.29413622215111
COMPLEX_POINTS = 2048
TD = 2 * COMPLEX_POINTS
DWELL_S = 1.0 / SWH_HZ
AQ_S = COMPLEX_POINTS * DWELL_S
OBSERVE_90_US = 16.574221
SOFTWARE_VERSION = "2.2.6.2"      # the validation partner's version
NOISE_GAIN = 14
LADDER_GAINS = (1, 4, 8, 14)

BASE_EPOCH = 1770000000           # deterministic session start (epoch s)
TZ = "+0100"


def _fid(seed, n, tip_scale):
    """Deterministic complex FID: decaying cosine + reproducible noise."""
    rng = random.Random(seed)
    re, im = [], []
    for i in range(n):
        t = i * DWELL_S
        env = math.exp(-t / 0.8) * tip_scale
        re.append(env * math.cos(2.0 * math.pi * 55.0 * t)
                  + rng.gauss(0.0, 1.0))
        im.append(env * math.sin(2.0 * math.pi * 55.0 * t)
                  + rng.gauss(0.0, 1.0))
    return re, im


def _page_lines(xfactor, factor, vals, per_line=4):
    """AFFN (X++(Y..Y)) lines: X in scaled units, then Y in scaled units."""
    lines = []
    for start in range(0, len(vals), per_line):
        chunk = vals[start:start + per_line]
        x = start          # X column is index * xfactor after FACTOR scaling
        ys = " ".join("%.6f" % (v / factor) for v in chunk)
        lines.append("%d %s" % (x, ys))
    return lines


def write_dx(path, title, epoch_s, gain, x_pulse_us, ns, total_duration_s,
             seed, tip_scale):
    """Write one synthetic NMReady-style JCAMP-DX FID export."""
    import time
    re, im = _fid(seed, COMPLEX_POINTS, tip_scale)
    max_abs = max(max(abs(v) for v in re), max(abs(v) for v in im)) or 1.0
    yfactor = max_abs / 2147483647.0
    xfactor = DWELL_S

    lt = time.gmtime(epoch_s + 3600)   # pretend local = UTC+1, matching TZ
    long_date = time.strftime("%Y/%m/%d %H:%M:%S", lt) + TZ

    lines = []
    a = lines.append
    a("##TITLE=%s" % title)
    a("##JCAMP-DX=5.01 $$ Nanalysis NMReady v%s" % SOFTWARE_VERSION)
    a("##DATA TYPE=NMR FID")
    a("##DATA CLASS=NTUPLES")
    a("##ORIGIN=Nanalysis Corp.")
    a("##OWNER=Nanalysis")
    a("##LONG DATE=%s" % long_date)
    a("##SPECTROMETER/DATA SYSTEM=NMReady 60/synthetic-desktest")
    a("##TEMPERATURE=33.000042")
    a("##SAMPLE DESCRIPTION=synthetic desktest water")
    a("##.OBSERVE FREQUENCY=%.14f" % SFO1_MHZ)
    a("##.OBSERVE NUCLEUS=^1H")
    a("##.SOLVENT NAME=H2O")
    a("##.PULSE SEQUENCE=1D")
    a("##.FIELD=%.6f  $$ Tesla" % FIELD_T)
    a("##.OBSERVE 90=%.6f" % OBSERVE_90_US)
    a("##.ACQUISITION TIME=%.14f" % AQ_S)
    a("##.AVERAGES=%d" % ns)
    a("##$SCANS=%d" % ns)
    a("##$SCAN DELAY=1.042978")
    a("##$TOTAL DURATION=%.6f" % total_duration_s)
    a("##$X_PULSE=%.6f" % x_pulse_us)
    a("##$RECVR_GAIN=%d" % gain)
    a("##$PULSE_AMPLITUDE=13.000000")
    a("##$LOCKOFFSET=7.260000")
    a("##$DATE=%d" % epoch_s)
    a("##$SFO1= %.14f" % SFO1_MHZ)
    a("##$O1= %.14f" % O1_HZ)
    a("##$BF1= %.14f" % BF1_MHZ)
    a("##$AQ= %.14f" % AQ_S)
    a("##$SWH= %.14f" % SWH_HZ)
    a("##$TD= %d" % TD)
    a("##$DW= %.14f" % (1.0e6 / (2.0 * SWH_HZ)))
    a("##$SPECTYP= PROTON")
    a("##$NS= %d" % ns)
    a("##$DS= 0")
    a("##$RG=%d" % gain)
    a("##$P1=%.6f" % x_pulse_us)
    a("##NPOINTS=%d" % COMPLEX_POINTS)
    a("##DELTAX=%.10f" % DWELL_S)
    a("##NTUPLES=NMR FID")
    a("##VAR_NAME=TIME,FID/REAL,FID/IMAG,PAGE NUMBER")
    a("##SYMBOL=X,R,I,N")
    a("##VAR_TYPE=INDEPENDENT,DEPENDENT,DEPENDENT,PAGE")
    a("##VAR_FORM=AFFN,AFFN,AFFN,AFFN")
    a("##VAR_DIM=%d,%d,%d,2" % (COMPLEX_POINTS, COMPLEX_POINTS,
                                COMPLEX_POINTS))
    a("##UNITS=SECONDS,ARBITRARY UNITS,ARBITRARY UNITS,")
    a("##FACTOR=%.20f,%.20f,%.20f,1" % (xfactor, yfactor, yfactor))
    a("##PAGE=N=1")
    a("##DATA TABLE= (X++(R..R)), XYDATA")
    lines.extend(_page_lines(xfactor, yfactor, re))
    a("##PAGE=N=2")
    a("##DATA TABLE= (X++(I..I)), XYDATA")
    lines.extend(_page_lines(xfactor, yfactor, im))
    a("##END NTUPLES=NMR FID")
    a("##END=")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return re, im, yfactor


def build_session(out_dir):
    session = os.path.join(out_dir, "nanalysis_synth_session")
    os.makedirs(session)

    plan = []
    t = BASE_EPOCH
    for i, (expno, gain) in enumerate(zip((10, 14, 15, 16), LADDER_GAINS)):
        plan.append((expno, "%02d_sn_ladder_%s.dx" % (expno, "abcd"[i]),
                     gain, OBSERVE_90_US / 90.0, 1, 8.0, t))
        t += 30
    plan.append((11, "11_sn_ref_open.dx", NOISE_GAIN,
                 OBSERVE_90_US / 90.0, 1, 8.0, t)); t += 30
    for expno in (20, 21, 22):
        # noise records: minimal tip bookkeeping (X_PULSE tiny), 1 scan
        plan.append((expno, "%d_sn_noise.dx" % expno, NOISE_GAIN,
                     0.5, 1, AQ_S + 1.0, t))
        t += int(AQ_S) + 5
    plan.append((13, "13_sn_ref_close.dx", NOISE_GAIN,
                 OBSERVE_90_US / 90.0, 1, 8.0, t))

    for expno, fname, gain, x_pulse, ns, dur, epoch in plan:
        tip_scale = 2000.0 if expno < 20 else 0.0
        write_dx(os.path.join(session, fname),
                 os.path.splitext(fname)[0], epoch, gain, x_pulse, ns,
                 dur, seed=expno, tip_scale=tip_scale)

    answers = {
        "run_mode_hint": "desktest",
        "institution": "Desktest Virtual Facility",
        "city": "Nowhere",
        "country": "n/a",
        "facility_slug": "desktest-nanalysis",
        "contact_email": "jwbquantum@gmail.com",
        "contact_consent": "yes",
        "model": "NMReady-60PRO",
        "software_version": SOFTWARE_VERSION,
        "firmware_version": "2.6.0",
        "lock_nucleus": "unknown",
        "field_state_notes": "synthetic desktest session; no hardware",
        "sample_description": "synthetic water (desktest)",
        "h2o_fraction_pct": 100.0,
        "d2o_pct": 0.0,
        "additives": "none",
        "tube_od_mm": 5.0,
        "sample_volume_ul": 550.0,
        "vt_setpoint_k": 306.0,
        "operator_notes": "synthetic session from "
                          "make_synthetic_nanalysis.py",
        "ref_tip_deg": 1.0,
        "shim_ok": True,
    }
    with open(os.path.join(session, "answers.json"), "w") as fh:
        json.dump(answers, fh, indent=2)
    return session


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build a synthetic NMReady spin-noise session "
                    "(desktest; never data).")
    ap.add_argument("--out-dir", default=None,
                    help="parent directory (default: a fresh temp dir)")
    args = ap.parse_args(argv)
    out_dir = args.out_dir or tempfile.mkdtemp(prefix="nanalysis_synth_")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    session = build_session(out_dir)
    print(session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
