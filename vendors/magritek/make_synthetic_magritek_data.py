#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_synthetic_magritek_data.py -- build a fake Spinsolve spin-noise
session directory so the magritek adapter + packer + uploader selftest
chain is testable today, without hardware.

    python3 vendors/magritek/make_synthetic_magritek_data.py [--out-dir D]

Prints exactly one line on stdout: the session directory path. The
session mimics what spin_noise_run_spinsolve.mac is designed to write:

    <session>/answers.json
    <session>/10,14,15,16   rxGain ladder   (acqu.par + data.1d each)
    <session>/11            reference_open
    <session>/20..22        three noise blocks
    <session>/13            reference_close

The .1d payloads are white pseudo-noise in the layout documented by
nmrglue's Spinsolve reader (32-byte header of eight uint32 LE
[owner, format, version, dataType, xDim, yDim, zDim, qDim], then float32
LE: xDim x-axis values followed by xDim interleaved re/im pairs). The
header magic values written here are SYNTHETIC placeholders -- the real
values are UNVERIFIED until the bench session; the reader deliberately
checks structure, not magic.

answers.json carries run_mode_hint "desktest" so the resulting bundle can
NEVER be mistaken for a real record.

Python 3 stdlib only, nothing newer than 3.6.
"""

from __future__ import print_function

import argparse
import json
import os
import random
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

B1_FREQ_MHZ = 60.0          # synthetic Spinsolve 60
DWELL_US = 100.0            # 10 kHz bandwidth
REF_PNTS = 2048
NOISE_PNTS = 8192           # small, so the test bundle stays tiny
LADDER = [(10, 0.0), (14, 20.0), (15, 40.0), (16, 60.0)]
NOISE_GAIN_DB = 60.0
REF_GAIN_DB = 20.0


def info(msg):
    print(msg, file=sys.stderr)


def write_acqu_par(path, experiment, pnts, gain_db):
    """Plausible Spinsolve acqu.par (key spellings per the Prospa
    ecosystem; real-file ground truth is bench checklist item 6)."""
    lines = [
        'b1Freq = %.6f' % B1_FREQ_MHZ,
        'bandwidth = %.3f' % (1000.0 / DWELL_US),   # kHz (assumed unit)
        'dwellTime = %.1f' % DWELL_US,
        'experiment = "%s"' % experiment,
        'nrPnts = %d' % pnts,
        'nrScans = 1',
        'rxChannel = "1H"',
        'rxGain = %.1f' % gain_db,
        'rxPhase = 0.0',
        'lowestFrequency = %.1f' % (-1000.0 / DWELL_US / 2.0 * 1000.0),
        'startTime = "%s"' % time.strftime("%Y-%m-%dT%H:%M:%S"),
        'Sample = "synthetic water (no sample exists)"',
        'Solvent = "None"',
    ]
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def write_1d(path, pnts, seed):
    """Synthetic data.1d: header + x axis + interleaved re/im noise."""
    rng = random.Random(seed)
    # Synthetic header placeholders (magic meanings UNVERIFIED; the
    # reader checks payload structure only).
    header = struct.pack("<8I", 0x534F5250, 0x41544144, 0x312E3156,
                         504, pnts, 1, 1, 1)
    xs = [i * DWELL_US * 1.0e-6 for i in range(pnts)]
    payload = list(xs)
    for _ in range(pnts):
        payload.append(rng.gauss(0.0, 1.0))
        payload.append(rng.gauss(0.0, 1.0))
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(struct.pack("<%df" % len(payload), *payload))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a synthetic Spinsolve spin-noise session.")
    parser.add_argument("--out-dir", default=None,
                        help="parent directory (default: "
                             "vendors/magritek/synthetic_sessions/)")
    args = parser.parse_args(argv)

    parent = args.out_dir or os.path.join(HERE, "synthetic_sessions")
    if not os.path.isdir(parent):
        os.makedirs(parent)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    session = os.path.join(parent, "spinnoise_ci-magritek_%s" % stamp)
    os.makedirs(session)

    now_ms = int(time.time() * 1000)
    blocks = []
    plan = []
    for expno, gain in LADDER:
        plan.append((expno, "rg_ladder", "spin_noise_smallflip",
                     REF_PNTS, gain))
    plan.append((11, "reference_open", "spin_noise_smallflip",
                 REF_PNTS, REF_GAIN_DB))
    for k in range(3):
        plan.append((20 + k, "noise", "spin_noise_nopulse",
                     NOISE_PNTS, NOISE_GAIN_DB))
    plan.append((13, "reference_close", "spin_noise_smallflip",
                 REF_PNTS, REF_GAIN_DB))

    t = now_ms
    for expno, role, experiment, pnts, gain in plan:
        d = os.path.join(session, str(expno))
        os.makedirs(d)
        write_acqu_par(os.path.join(d, "acqu.par"), experiment, pnts, gain)
        write_1d(os.path.join(d, "data.1d"), pnts, seed=expno * 7919)
        dur_ms = int(pnts * DWELL_US * 1e-3) + 500   # acq + overhead
        blocks.append({"expno": expno, "role": role, "rx_gain_db": gain,
                       "wall_start_ms": t, "wall_end_ms": t + dur_ms})
        t += dur_ms + 1000

    answers = {
        "writer": "make_synthetic_magritek_data.py (SYNTHETIC -- not a "
                  "macro product; never a science record)",
        "run_mode_hint": "desktest",
        "facility_slug": "ci-magritek",
        "institution": "CI selftest (synthetic)",
        "city": "Nowhere",
        "country": "n/a",
        "contact_email": "",
        "contact_consent": "no",
        "sample_description": "synthetic water (no sample exists)",
        "h2o_fraction_pct": 100.0,
        "d2o_pct": 0.0,
        "additives": "none",
        "tube_od_mm": 5.0,
        "sample_volume_ul": 550.0,
        "vt_setpoint_k": 300.0,
        "lock_state": "unknown",
        "b1_freq_mhz": B1_FREQ_MHZ,
        "ref_tip_deg": 5.0,
        "spinsolve_software_version": "synthetic",
        "model": "Spinsolve 60 (synthetic)",
        "operator_notes": "synthetic CI session; not data; never upload",
        "blocks": blocks,
    }
    with open(os.path.join(session, "answers.json"), "w") as fh:
        json.dump(answers, fh, indent=2)
        fh.write("\n")

    # Also write the packer-style questionnaire (packer/pack_bundle.py
    # --answers shape, see packer/answers.example.json) so the central
    # packer path is testable against the same session:
    #   python3 packer/pack_bundle.py <session> \
    #       --answers <session>/answers_packer.json --vendor magritek
    packer_answers = {
        "_comment": "SYNTHETIC packer questionnaire for the magritek "
                    "adapter chain test; never a science record.",
        "vendor": "magritek",
        "run_mode": "desktest",
        "facility": {
            "institution": answers["institution"],
            "city": answers["city"],
            "country": answers["country"],
            "facility_slug": answers["facility_slug"],
            "contact_email": "",
            "contact_consent": False,
        },
        "sample": {
            "description": answers["sample_description"],
            "h2o_fraction_pct": answers["h2o_fraction_pct"],
            "d2o_pct": answers["d2o_pct"],
            "additives": answers["additives"],
            "tube_od_mm": answers["tube_od_mm"],
            "sample_volume_ul": answers["sample_volume_ul"],
            "vt_setpoint_k": answers["vt_setpoint_k"],
        },
        "environment": {
            "locked": False,
            "operator_notes": answers["operator_notes"],
        },
        "spectrometer": {
            "probe_type": "permanent-magnet-benchtop",
            "console": answers["model"],
            "probe_string": "Spinsolve integrated benchtop probe",
            "coil_temp_k": None,
            "preamp_temp_k": None,
        },
        "instrument": {
            "spinsolve_software_version": answers[
                "spinsolve_software_version"],
            "model": answers["model"],
            "expert_mode": True,
        },
        "calibration": {
            "p90_us": 10.0,
            "p90_power_db_or_w": "n/a (Spinsolve internal amplitude units)",
            "topshim_ok": False,
            "rg_ladder": [
                {"expno": expno, "rg": 10.0 ** (gain / 20.0),
                 "tip_deg": answers["ref_tip_deg"]}
                for expno, gain in LADDER
            ],
        },
        "experiments": [
            {"expno": blk["expno"], "role": blk["role"]}
            for blk in blocks
        ],
    }
    with open(os.path.join(session, "answers_packer.json"), "w") as fh:
        json.dump(packer_answers, fh, indent=2)
        fh.write("\n")

    info("synthetic Spinsolve session (%d experiments): %s"
         % (len(plan), session))
    print(session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
