#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_synthetic_agilent_data.py -- build a fake Agilent/Varian (VnmrJ)
spin-noise session directory so the agilent adapter + packer + uploader
selftest chain is testable today, without hardware.

    python3 vendors/agilent/make_synthetic_agilent_data.py [--out-dir D]

Prints exactly one line on stdout: the session directory path. The
session mimics what the Tier-1 operator checklist (or the draft
spin_noise_run.mac) is designed to leave behind -- one VnmrJ save
directory per experiment, named with the Bruker-expno-plan prefix:

    <session>/10,14,15,16_sn_ladder_*.fid   gain ladder (procpar + fid)
    <session>/11_sn_ref_open.fid            reference_open
    <session>/12,17,18_sn_noise*.fid        three noise blocks
    <session>/13_sn_ref_close.fid           reference_close
    <session>/answers_packer.json           packer questionnaire

The fid payloads are white pseudo-noise in the layout documented by
nmrglue's varian reader (32-byte big-endian file header '>6ihhi'
[nblocks, ntraces, np, ebytes, tbytes, bbytes, vers_id, status,
nbheaders], one 28-byte block header '>4hi4f' per block, then np
big-endian int32 points -- interleaved re/im). The vers_id and status
values written here are SYNTHETIC placeholders beyond the documented
dtype bits (S_32 set, S_FLT clear) -- no real VnmrJ 3.2 fid has been
inspected yet (partner checklist item 10); the reader deliberately
checks header arithmetic, not magic.

The procpar records follow the nmrglue-documented record shape (11-field
first line, count-prefixed values line, enumerable line) with the
standard parameter names (np, sw, at, sfrq, tof, gain, nt, pw, tpwr, d1,
tn, seqfil, solvent, temp).

answers_packer.json carries run_mode "desktest" so the resulting bundle
can NEVER be mistaken for a real record.

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

SFRQ_MHZ = 399.945          # synthetic 400 MHz DD2
SW_HZ = 10000.0
TOF_HZ = 0.0
REF_NP = 4096               # np = TOTAL points (re+im), Varian convention
NOISE_NP = 16384            # small, so the test bundle stays tiny
LADDER = [(10, "a", 0.0), (14, "b", 20.0), (15, "c", 40.0),
          (16, "d", 60.0)]
NOISE_GAIN_DB = 60.0
REF_GAIN_DB = 20.0
NOISE_EXPNOS = (12, 17, 18)   # 12 first, then count up from 17 (13 is
                              # the closing reference -- Bruker plan)

S_DATA = 0x1
S_32 = 0x4
BLOCK_HEADER_BYTES = 28


def info(msg):
    print(msg, file=sys.stderr)


def _real_record(name, value):
    """One procpar record for a single real value (record shape per
    nmrglue's varian reader; subtype/group/protection fields are
    plausible fillers -- the reader keys on fields 0 and 2 only)."""
    return ("%s 1 1 1e+30 -1e+30 0 1 0 0 1 64\n1 %.12g\n0 \n"
            % (name, value))


def _string_record(name, value):
    return ('%s 2 2 0 0 0 1 0 0 1 64\n1 "%s"\n0 \n' % (name, value))


def write_procpar(path, seqfil, np_pts, gain_db, at_s, pw_us, tpwr_db):
    recs = [
        _real_record("np", np_pts),
        _real_record("sw", SW_HZ),
        _real_record("at", at_s),
        _real_record("sfrq", SFRQ_MHZ),
        _real_record("tof", TOF_HZ),
        _real_record("gain", gain_db),
        _real_record("nt", 1),
        _real_record("pw", pw_us),
        _real_record("tpwr", tpwr_db),
        _real_record("d1", 1.0),
        _real_record("temp", 25.0),
        _string_record("tn", "H1"),
        _string_record("seqfil", seqfil),
        _string_record("pslabel", seqfil),
        _string_record("solvent", "None"),
        _string_record("comment",
                       "synthetic CI session; not data; never upload"),
    ]
    with open(path, "w") as fh:
        fh.write("".join(recs))


def write_fid(path, np_pts, seed):
    """Synthetic fid: file header + one block header + int32 BE noise."""
    rng = random.Random(seed)
    ebytes = 4
    tbytes = np_pts * ebytes
    bbytes = BLOCK_HEADER_BYTES + tbytes
    header = struct.pack(">6ihhi", 1, 1, np_pts, ebytes, tbytes, bbytes,
                         0, S_DATA | S_32, 1)
    block_header = struct.pack(">4hi4f", 0, S_DATA | S_32, 1, 0, 1,
                               0.0, 0.0, 0.0, 0.0)
    data = [int(rng.gauss(0.0, 1000.0)) for _ in range(np_pts)]
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(block_header)
        fh.write(struct.pack(">%di" % np_pts, *data))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a synthetic Agilent/VnmrJ spin-noise session.")
    parser.add_argument("--out-dir", default=None,
                        help="parent directory (default: "
                             "vendors/agilent/synthetic_sessions/)")
    args = parser.parse_args(argv)

    parent = args.out_dir or os.path.join(HERE, "synthetic_sessions")
    if not os.path.isdir(parent):
        os.makedirs(parent)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    session = os.path.join(parent, "spinnoise_ci-agilent_%s" % stamp)
    os.makedirs(session)

    plan = []
    for expno, tag, gain in LADDER:
        plan.append((expno, "%d_sn_ladder_%s.fid" % (expno, tag),
                     "rg_ladder", "s2pul", REF_NP, gain, 1.0, -16.0))
    plan.append((11, "11_sn_ref_open.fid", "reference_open", "s2pul",
                 REF_NP, REF_GAIN_DB, 1.0, 40.0))
    for k, expno in enumerate(NOISE_EXPNOS):
        plan.append((expno, "%d_sn_noise%s.fid"
                     % (expno, "" if k == 0 else "_%d" % k),
                     "noise", "s2pul", NOISE_NP, NOISE_GAIN_DB, 0.0,
                     -16.0))
    plan.append((13, "13_sn_ref_close.fid", "reference_close", "s2pul",
                 REF_NP, REF_GAIN_DB, 1.0, 40.0))

    for expno, dirname, role, seqfil, np_pts, gain, pw_us, tpwr in plan:
        d = os.path.join(session, dirname)
        os.makedirs(d)
        at_s = np_pts / (2.0 * SW_HZ)
        write_procpar(os.path.join(d, "procpar"), seqfil, np_pts, gain,
                      at_s, pw_us, tpwr)
        write_fid(os.path.join(d, "fid"), np_pts, seed=expno * 7919)
        with open(os.path.join(d, "text"), "w") as fh:
            fh.write("synthetic spin-noise %s block (never a science "
                     "record)\n" % role)

    # The packer questionnaire (packer/pack_bundle.py --answers shape,
    # see packer/answers.example.json):
    #   python3 packer/pack_bundle.py <session> \
    #       --answers <session>/answers_packer.json --vendor agilent
    answers = {
        "_comment": "SYNTHETIC packer questionnaire for the agilent "
                    "adapter chain test; never a science record.",
        "vendor": "agilent",
        "run_mode": "desktest",
        "facility": {
            "institution": "CI selftest (synthetic)",
            "city": "Nowhere",
            "country": "n/a",
            "facility_slug": "ci-agilent",
            "contact_email": "",
            "contact_consent": False,
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
            "operator_notes": "synthetic CI session; not data; "
                              "never upload",
        },
        "spectrometer": {
            "probe_type": "RT",
            "console": "Agilent DD2 400 (synthetic)",
            "probe_string": "5 mm HCN (synthetic)",
            "coil_temp_k": None,
            "preamp_temp_k": None,
        },
        "instrument": {
            "vnmrj_version": "synthetic (VnmrJ 3.2-style)",
            "spectrometer_model": "Agilent DD2 400 (synthetic)",
            "field_state_notes": "synthetic session -- no field exists",
        },
        "calibration": {
            "p90_us": 10.0,
            "p90_power_db_or_w": "40 dB tpwr (synthetic)",
            "topshim_ok": False,
            "rg_ladder": [
                {"expno": expno, "rg": 10.0 ** (gain / 20.0),
                 "tip_deg": 1.0}
                for expno, _tag, gain in LADDER
            ],
        },
        "experiments": (
            [{"expno": expno, "role": "rg_ladder"}
             for expno, _tag, _gain in LADDER]
            + [{"expno": 11, "role": "reference_open"}]
            + [{"expno": e, "role": "noise"} for e in NOISE_EXPNOS]
            + [{"expno": 13, "role": "reference_close"}]
        ),
    }
    with open(os.path.join(session, "answers_packer.json"), "w") as fh:
        json.dump(answers, fh, indent=2)
        fh.write("\n")

    info("synthetic Agilent/VnmrJ session (%d experiments): %s"
         % (len(plan), session))
    print(session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
