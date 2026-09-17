#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_synthetic_agilent_data.py -- build a fake Agilent/Varian (VnmrJ)
spin-noise session directory so the agilent adapter + packer + uploader
selftest chain is testable today, without hardware.

    python3 vendors/agilent/make_synthetic_agilent_data.py [--out-dir D]
        [--base-time YYYY-MM-DDTHH:MM:SS] [--clock-skew-s SECONDS]
        [--ladder-levels-db "0,5,...,40" --ladder-repeats 3
         --ladder-random-seed S] [--tuning-ladder N] [--signcal]

Prints exactly one line on stdout: the session directory path. The
session mimics what the Tier-1 operator checklist (or the draft
spin_noise_run.mac) is designed to leave behind -- one VnmrJ save
directory per experiment, named with the Bruker-expno-plan prefix:

    <session>/10,14,15,16_sn_ladder_*.fid   gain ladder (procpar + fid)
    <session>/11_sn_ref_open.fid            reference_open
    <session>/12,17,18_sn_noise*.fid        three noise blocks
    <session>/13_sn_ref_close.fid           reference_close
    <session>/vnmrrev                       copy of /vnmr/vnmrrev
    <session>/answers_packer.json           packer questionnaire

and, with the v0.7.1 options (each off by default, so the default
session is unchanged):

    --ladder-levels-db "0,5,...,40"   replaces the four-rung ladder by
        the randomized repeated ladder of the quickstart: every listed
        integer-dB level visited --ladder-repeats times in one random
        permutation (--ladder-random-seed), one rung per .fid named
        1000+i_sn_ladder_<gain>db in acquisition order, procpar gain =
        the level, pad 1 s, time_run stamps ~6 s apart; references and
        noise blocks then run at the ladder maximum, as the protocol
        says, and calibration.rg_ladder lists the rungs in acquisition
        order with duplicate gains (never sorted).
    --tuning-ladder N                 N tuning settings x 2 pulse-free
        noise_tune blocks, 2000+10k+j_sn_tune_k.fid (setting k, block
        j), each answers.json entry carrying a tuning object with
        setting_index k (0 = the operator's normal tuning).
    --signcal                         one 3000_sn_signcal.fid: the
        reference acquisition with tof displaced by +200 Hz; its
        procpar sfrq is displaced by the same 200 Hz, as on VnmrJ,
        where sfrq is the observe transmitter frequency and tracks
        tof (the packer must not let it vote on h1_freq_mhz).

Every procpar also carries VnmrJ's lock referencing -- reffrq (the
0 ppm frequency, MHz), rfl and rfp (Hz) -- consistent with the
session's own line: reffrq is fixed so that water at 4.75 ppm sits at
the synthetic line's +537.5 Hz PHYSICAL offset from the carrier, and
rfl follows the VnmrJ identity reffrq = sfrq - sw/2 + rfl - rfp in
each experiment (the signcal's displaced sfrq included). The
generator's convention is physical (a +200 Hz tof step drops the
apparent line by 200 Hz), so the report's lock-referencing cross-check
must find sign +1 on these sessions.

Any of --ladder-levels-db / --signcal also puts a synthetic water
line into every pulsed 1D (ladder rungs, references, sign check): a
decaying complex exponential at apparent offset +537.5 Hz from the
carrier (close to the SIU 2026-09-14 values, 534.4 and 540.8 Hz in
the two sessions), amplitude 1e5 counts x 10^(gain/20), T2* 20 ms.
In these sessions the noise scales with the gain too, sigma 1000
counts x 10^(gain/20) in every experiment (receiver noise referred to
the input is amplified along with the signal; the default session
keeps its gain-independent sigma 1000 for byte-identity), so every
rung has a matched-filter SNR of ~440 (0.23% amplitude scatter) and a
fixture generated with zero compression and zero drift reads as
linear to a few tenths of a percent at every level -- the analysis
anchors its compression scale on the lowest level, and a
noise-limited lowest rung (the 2000-count line of the first draft, SNR
9) printed a spurious 20% compression envelope.  The fid convention
is re + i*im with a positive offset = exp(+2*pi*i*f*t); with the
carrier moved +200 Hz the line's apparent offset DROPS to +337.5 Hz
-- the PHYSICAL convention the report's axis-sign check tests for (a
rise means the axis is mirrored).  Noise blocks carry no line.

The fid payloads are white pseudo-noise in the layout documented by
nmrglue's varian reader (32-byte big-endian file header '>6ihhi'
[nblocks, ntraces, np, ebytes, tbytes, bbytes, vers_id, status,
nbheaders], one 28-byte block header '>4hi4f' per block, then np
big-endian int32 points -- interleaved re/im). Real DD2 files
(2026-09-14) carry status 201 = float32; int32 (S_32 set, S_FLT clear)
is kept here so the other dtype branch stays exercised, and vers_id
stays a placeholder -- the reader deliberately checks header
arithmetic, not magic.

The procpar records follow the nmrglue-documented record shape (11-field
first line, count-prefixed values line, enumerable line) with the
standard parameter names (np, sw, at, sfrq, tof, gain, nt, pw, tpwr, d1,
pad, tn, seqfil, solvent, temp, reffrq, rfl, rfp) plus the VnmrJ 3.2
wall-clock stamps
time_run / time_complete / time_saved ("YYYYMMDDTHHMMSS", console-local,
as on the real console): experiments run back to back from --base-time
(a fixed default, so the session is deterministic; the literal "now"
means the current wall clock), each lasting its at plus a few seconds
of overhead. --clock-skew-s shifts every stamp (a fast console clock --
the state SIU found on its console before the 2026-09-14 sessions);
--base-time now with a positive skew exercises the packer's ahead-clock
WARN.

answers_packer.json carries run_mode "desktest" so the resulting bundle
can NEVER be mistaken for a real record.

Python 3 stdlib only, nothing newer than 3.6.
"""

from __future__ import print_function

import argparse
import datetime
import json
import math
import os
import random
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

BASE_TIME = "2026-03-01T09:00:00"   # first time_run (console-local)
SETUP_OVERHEAD_S = 4                # submit -> time_run, per experiment
SAVE_OVERHEAD_S = 2                 # time_run + at -> time_complete
LADDER_SUBMIT_S = 2                 # scripted ladder rung: submit -> time_run
LADDER_PAD_S = 1.0                  # settle after each gain change
VNMRREV_LINES = ("VnmrJ VERSION 3.2 REVISION A", "September 21, 2011",
                 "vnmrsdd2")        # verbatim /vnmr/vnmrrev of the SIU DD2

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
REF_PW_US = 1.0
REF_TPWR_DB = 40.0
NOISE_TPWR_DB = -16.0

# v0.7.1 series: four-digit prefixes can never collide with noise
# blocks counting up from 17 (docs/agilent_quickstart_siu.tex)
LADDER_EXPNO_BASE = 1000      # rung i -> 1000 + i, acquisition order
TUNE_EXPNO_BASE = 2000        # setting k, block j -> 2000 + 10 k + j
SIGNCAL_EXPNO = 3000
LADDER_REPEATS = 3
LADDER_SEED = 20260914

LINE_OFFSET_HZ = 537.5        # apparent offset in the reference window
LINE_AMPLITUDE_0DB = 1.0e5    # counts at gain 0 dB; x 10^(gain/20)
LINE_T2_S = 0.02
SIGNCAL_DISPLACEMENT_HZ = 200.0
NOISE_SIGMA = 1000.0

# VnmrJ lock referencing, physical convention: water (4.75 ppm) at the
# line's +537.5 Hz physical offset fixes reffrq, the 0 ppm frequency
WATER_SHIFT_PPM = 4.75
REFFRQ_MHZ = ((SFRQ_MHZ * 1e6 + LINE_OFFSET_HZ)
              / (1.0 + WATER_SHIFT_PPM * 1e-6) / 1e6)
RFP_HZ = 0.0

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


def vnmrj_stamp(dt):
    return dt.strftime("%Y%m%dT%H%M%S")


def write_procpar(path, seqfil, np_pts, gain_db, at_s, pw_us, tpwr_db,
                  t_run, t_complete, tof_hz=TOF_HZ, pad_s=0.0):
    # sfrq is the observe transmitter frequency and tracks tof on VnmrJ;
    # reffrq (0 ppm, from the lock) does not, so rfl follows the identity
    # reffrq = sfrq - sw/2 + rfl - rfp in every experiment
    sfrq_mhz = SFRQ_MHZ + (tof_hz - TOF_HZ) / 1e6
    rfl_hz = REFFRQ_MHZ * 1e6 - sfrq_mhz * 1e6 + SW_HZ / 2.0 + RFP_HZ
    recs = [
        _real_record("np", np_pts),
        _real_record("sw", SW_HZ),
        _real_record("at", at_s),
        _real_record("sfrq", sfrq_mhz),
        _real_record("tof", tof_hz),
        _real_record("reffrq", REFFRQ_MHZ),
        _real_record("rfl", rfl_hz),
        _real_record("rfp", RFP_HZ),
        _real_record("gain", gain_db),
        _real_record("nt", 1),
        _real_record("pw", pw_us),
        _real_record("tpwr", tpwr_db),
        _real_record("d1", 1.0),
        _real_record("pad", pad_s),
        _real_record("temp", 25.0),
        _string_record("tn", "H1"),
        _string_record("seqfil", seqfil),
        _string_record("pslabel", seqfil),
        _string_record("solvent", "None"),
        _string_record("date", t_run.strftime("%b %d %Y")),
        _string_record("time_run", vnmrj_stamp(t_run)),
        _string_record("time_complete", vnmrj_stamp(t_complete)),
        _string_record("time_saved", vnmrj_stamp(t_complete)),
        _string_record("time_processed", ""),
        _string_record("comment",
                       "synthetic CI session; not data; never upload"),
    ]
    with open(path, "w") as fh:
        fh.write("".join(recs))


def write_fid(path, np_pts, seed, line=None, sigma=NOISE_SIGMA):
    """Synthetic fid: file header + one block header + int32 BE white
    noise of the given sigma per component, plus -- when line =
    (offset_hz, amplitude_counts, t2_s) -- a decaying complex
    exponential exp(2*pi*i*offset*t - t/t2) in the re + i*im
    convention (complex sample k at t = k/sw)."""
    rng = random.Random(seed)
    ebytes = 4
    tbytes = np_pts * ebytes
    bbytes = BLOCK_HEADER_BYTES + tbytes
    header = struct.pack(">6ihhi", 1, 1, np_pts, ebytes, tbytes, bbytes,
                         0, S_DATA | S_32, 1)
    block_header = struct.pack(">4hi4f", 0, S_DATA | S_32, 1, 0, 1,
                               0.0, 0.0, 0.0, 0.0)
    data = []
    for k in range(np_pts // 2):
        re = rng.gauss(0.0, sigma)
        im = rng.gauss(0.0, sigma)
        if line is not None:
            f_hz, amp, t2_s = line
            t = k / SW_HZ
            env = amp * math.exp(-t / t2_s)
            re += env * math.cos(2.0 * math.pi * f_hz * t)
            im += env * math.sin(2.0 * math.pi * f_hz * t)
        data.append(int(re))
        data.append(int(im))
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(block_header)
        fh.write(struct.pack(">%di" % np_pts, *data))


def parse_levels(text):
    """'0,5,10' -> [0, 5, 10] (integer dB, the console's legal values);
    ValueError names the offending token."""
    levels = []
    for tok in text.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            v = float(tok)
        except ValueError:
            raise ValueError("gain level %r is not a number" % tok)
        if v != int(v):
            raise ValueError("gain level %r is not an integer dB" % tok)
        levels.append(int(v))
    if not levels:
        raise ValueError("no gain levels given")
    return levels


def tuning_object(k):
    """The answers.json tuning object of setting k (synthetic readings,
    labeled as such): setting 0 is the operator's normal tuning, the
    others alternate sides of it, one more turn per pair."""
    if k == 0:
        return {"setting_index": 0,
                "label": "operator's normal tuning (wobble minimum)",
                "tune_reading": 0.0, "match_reading": 0.0,
                "units": "turns from normal",
                "note": "synthetic fixture -- readings invented, never a "
                        "record"}
    turns = (k + 1) // 2 * (1 if k % 2 else -1)
    return {"setting_index": k,
            "label": "tune capacitor %+d turn(s) from normal" % turns,
            "tune_reading": float(turns), "match_reading": None,
            "units": "turns from normal",
            "note": "synthetic fixture -- readings invented, never a record"}


def pulsed(expno, dirname, role, np_pts, gain_db, pad_s, submit_s,
           tof_hz, line_on, tpwr_db=REF_TPWR_DB, tuning=None):
    line = None
    if line_on:
        line = (LINE_OFFSET_HZ - (tof_hz - TOF_HZ),
                LINE_AMPLITUDE_0DB * 10.0 ** (gain_db / 20.0), LINE_T2_S)
    return {"expno": expno, "dirname": dirname, "role": role,
            "seqfil": "s2pul", "np": np_pts, "gain": gain_db,
            "pw": REF_PW_US, "tpwr": tpwr_db, "tof": tof_hz,
            "pad": pad_s, "submit_s": submit_s, "line": line,
            "tuning": tuning}


def pulse_free(expno, dirname, role, gain_db, tuning=None):
    return {"expno": expno, "dirname": dirname, "role": role,
            "seqfil": "s2pul", "np": NOISE_NP, "gain": gain_db,
            "pw": 0.0, "tpwr": NOISE_TPWR_DB, "tof": TOF_HZ, "pad": 0.0,
            "submit_s": SETUP_OVERHEAD_S, "line": None, "tuning": tuning}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a synthetic Agilent/VnmrJ spin-noise session.")
    parser.add_argument("--out-dir", default=None,
                        help="parent directory (default: "
                             "vendors/agilent/synthetic_sessions/)")
    parser.add_argument("--base-time", default=BASE_TIME,
                        help="time_run of the first experiment, console-"
                             "local YYYY-MM-DDTHH:MM:SS, or 'now' "
                             "(default %s)" % BASE_TIME)
    parser.add_argument("--clock-skew-s", type=float, default=0.0,
                        help="shift every procpar time stamp by this many "
                             "seconds (a fast console clock when positive; "
                             "default 0)")
    parser.add_argument("--ladder-levels-db", default=None,
                        help="comma-separated integer-dB gain levels of the "
                             "randomized repeated ladder, e.g. "
                             "\"0,5,10,15,20,25,30,35,40\" (default: the "
                             "four-rung 0/20/40/60 ladder at expnos "
                             "10/14/15/16); references and noise blocks "
                             "then run at the ladder maximum")
    parser.add_argument("--ladder-repeats", type=int, default=LADDER_REPEATS,
                        help="visits per level in the randomized ladder "
                             "(default %d)" % LADDER_REPEATS)
    parser.add_argument("--ladder-random-seed", type=int, default=LADDER_SEED,
                        help="seed of the ladder permutation (default %d, "
                             "so the session stays deterministic)"
                             % LADDER_SEED)
    parser.add_argument("--tuning-ladder", type=int, default=0,
                        metavar="N",
                        help="emit N tuning settings x 2 noise_tune blocks "
                             "(expnos 2000+10k+j) with answers.json tuning "
                             "objects, setting_index 0..N-1 (default 0)")
    parser.add_argument("--signcal", action="store_true",
                        help="emit one sweep_signcal 1D (expno 3000) with "
                             "tof displaced by +%g Hz and the synthetic line "
                             "moved accordingly" % SIGNCAL_DISPLACEMENT_HZ)
    args = parser.parse_args(argv)

    if args.base_time == "now":
        clock = datetime.datetime.now().replace(microsecond=0)
    else:
        try:
            clock = datetime.datetime.strptime(args.base_time,
                                               "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            info("--base-time must be YYYY-MM-DDTHH:MM:SS or 'now', got %r"
                 % args.base_time)
            return 2
    clock += datetime.timedelta(seconds=args.clock_skew_s)
    if args.tuning_ladder < 0 or args.ladder_repeats < 1:
        info("--tuning-ladder must be >= 0 and --ladder-repeats >= 1")
        return 2

    line_on = args.signcal or args.ladder_levels_db is not None
    if args.ladder_levels_db is None:
        rungs = [(expno, "%d_sn_ladder_%s.fid" % (expno, tag), gain)
                 for expno, tag, gain in LADDER]
        ladder_pad, ladder_submit = 0.0, SETUP_OVERHEAD_S
        # the legacy fixture's rungs carry tpwr -16 (kept byte-identical
        # for the tests built on it); the randomized ladder uses the
        # references' tpwr, as the protocol says
        ladder_tpwr = NOISE_TPWR_DB
        ref_gain, noise_gain = REF_GAIN_DB, NOISE_GAIN_DB
    else:
        try:
            levels = parse_levels(args.ladder_levels_db)
        except ValueError as exc:
            info("--ladder-levels-db: %s" % exc)
            return 2
        order = levels * args.ladder_repeats
        random.Random(args.ladder_random_seed).shuffle(order)
        rungs = [(LADDER_EXPNO_BASE + i,
                  "%d_sn_ladder_%02ddb.fid" % (LADDER_EXPNO_BASE + i, g),
                  float(g))
                 for i, g in enumerate(order)]
        ladder_pad, ladder_submit = LADDER_PAD_S, LADDER_SUBMIT_S
        ladder_tpwr = REF_TPWR_DB
        ref_gain = noise_gain = float(max(levels))

    parent = args.out_dir or os.path.join(HERE, "synthetic_sessions")
    if not os.path.isdir(parent):
        os.makedirs(parent)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    session = os.path.join(parent, "spinnoise_ci-agilent_%s" % stamp)
    os.makedirs(session)
    with open(os.path.join(session, "vnmrrev"), "w") as fh:
        fh.write("\n".join(VNMRREV_LINES) + "\n")

    plan = []
    for expno, dirname, gain in rungs:
        plan.append(pulsed(expno, dirname, "rg_ladder", REF_NP, gain,
                           ladder_pad, ladder_submit, TOF_HZ, line_on,
                           tpwr_db=ladder_tpwr))
    plan.append(pulsed(11, "11_sn_ref_open.fid", "reference_open", REF_NP,
                       ref_gain, 0.0, SETUP_OVERHEAD_S, TOF_HZ, line_on))
    for k, expno in enumerate(NOISE_EXPNOS):
        plan.append(pulse_free(expno, "%d_sn_noise%s.fid"
                               % (expno, "" if k == 0 else "_%d" % k),
                               "noise", noise_gain))
    plan.append(pulsed(13, "13_sn_ref_close.fid", "reference_close", REF_NP,
                       ref_gain, 0.0, SETUP_OVERHEAD_S, TOF_HZ, line_on))
    for k in range(args.tuning_ladder):
        for j in (0, 1):
            expno = TUNE_EXPNO_BASE + 10 * k + j
            plan.append(pulse_free(expno, "%d_sn_tune_%d.fid" % (expno, k),
                                   "noise_tune", noise_gain,
                                   tuning=tuning_object(k)))
    if args.signcal:
        plan.append(pulsed(SIGNCAL_EXPNO, "%d_sn_signcal.fid" % SIGNCAL_EXPNO,
                           "sweep_signcal", REF_NP, ref_gain, 0.0,
                           SETUP_OVERHEAD_S,
                           TOF_HZ + SIGNCAL_DISPLACEMENT_HZ, line_on))

    for p in plan:
        d = os.path.join(session, p["dirname"])
        os.makedirs(d)
        at_s = p["np"] / (2.0 * SW_HZ)
        t_run = clock + datetime.timedelta(seconds=p["submit_s"])
        t_complete = t_run + datetime.timedelta(
            seconds=int(p["pad"] + at_s + 0.999999) + SAVE_OVERHEAD_S)
        clock = t_complete
        write_procpar(os.path.join(d, "procpar"), p["seqfil"], p["np"],
                      p["gain"], at_s, p["pw"], p["tpwr"], t_run, t_complete,
                      tof_hz=p["tof"], pad_s=p["pad"])
        # extended sessions amplify the noise with the gain, as a
        # receiver does; the default session's sigma is frozen
        sigma = NOISE_SIGMA * 10.0 ** (p["gain"] / 20.0) if line_on \
            else NOISE_SIGMA
        write_fid(os.path.join(d, "fid"), p["np"], seed=p["expno"] * 7919,
                  line=p["line"], sigma=sigma)
        with open(os.path.join(d, "text"), "w") as fh:
            fh.write("synthetic spin-noise %s block (never a science "
                     "record)\n" % p["role"])

    # The packer questionnaire (packer/pack_bundle.py --answers shape,
    # see packer/answers.example.json):
    #   python3 packer/pack_bundle.py <session> \
    #       --answers <session>/answers_packer.json --vendor agilent
    experiments = []
    for p in plan:
        entry = {"expno": p["expno"], "role": p["role"]}
        if p["tuning"] is not None:
            entry["tuning"] = p["tuning"]
        experiments.append(entry)
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
                for expno, _dirname, gain in rungs
            ],
        },
        "experiments": experiments,
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
