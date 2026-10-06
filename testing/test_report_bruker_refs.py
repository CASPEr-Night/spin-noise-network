#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_report_bruker_refs.py -- the two analysis/facility_report.py defects
found on the first complete Bruker sessions (University of Oulu, Avance III
HD 500, TopSpin 3.7.0, script v0.7.8, 2026-10-05/06) and fixed in v0.7.9.

    python3 testing/test_report_bruker_refs.py [--out-dir DIR]
                                               [--bundle spinnoise_*.zip]

  1. Bruker reference tip angle.  TopSpin 3.x/4.x write the channel power
     as PLW (watts; PLdB on 4.x) and leave the legacy PL array (dB of
     attenuation, TopSpin 2.x) at its 'never set' value of 120 dB.  v0.7.8
     read PL first: Oulu's acqus carries PL[1] = 120 with PLW[1] =
     0.00321366 W, so the reference tip came out as 90 x 10^(-(120 -
     (-14.1))/20) = 1.77e-5 deg instead of ~1.0 deg, kappa*M0 5.7e4 too
     large (6.3e12 counts) and the exclusion 5.7e4 too strong (g90
     1.46e-9 GeV^-1 on the 3 h session).  Checks:
       * bruker_power_level_db: PL 120 + PLW set -> -10 log10(PLW) =
         24.9 dB with source 'PLW1 ...'; PLdB present -> read; legacy PL
         only -> read; PL at the 120 sentinel only -> None (so the tip
         falls back to the protocol's ladder tip, never to 1.77e-5 deg);
       * reference_tip_deg with Oulu's record (P1 = P90 = 8.27 us, PLW1
         3.21e-3 W, p90 power '-14.1497 dB (PLdB 1)') -> 1.0 deg, the
         basis quoting the PLW entry;
       * analyze_reference_exp on a synthetic Bruker reference expno ->
         pl1_db / pl1_source from PLW, p1_us from P;
       * the Agilent path (procpar pw/tpwr) is untouched: the same
         reference_tip_deg call as before gives the same number and text.
  2. Clock audit.  Every zgref2d / zgnoise2d text TopSpin 3.7.0 stored in
     the Oulu expnos carries a 'dccorr' statement its preprocessor inserts
     by itself ('dc-measurement inserted automatically'); the report's
     timing parser refused it ('unrecognized statement'), every usable
     block kept the script-recorded expectation (AQ from the REQUESTED
     6900 Hz while the console acquired at 6893.38 Hz) and the fit
     declared +1.06e-3 +/- 1.7e-6 a conclusive console-clock offset on a
     chrony-synced workstation.  Checks:
       * pp_timing_model accepts TopSpin 3.x's stored text (the '# N
         "mc_line ..."' marker and dccorr) for both sequences;
       * with pulse-program text the refinement engages on every usable
         block (n_refined 3);
       * WITHOUT pulse-program text the recorded expectation is corrected
         for the console's SW_h -- rows * TD/2 * (1/SW_h - 1/6900) --
         exactly, labelled 'SW_h-corrected', and skipped (with a note)
         when the recorded value was already built from the console's
         SW_h or the console's SW_h is the requested one;
       * conclusiveness: a fit of ~2.5e-4 (5 ms of unmodelled overhead
         per 20 s row) over a > 1 h span says 'expectation model
         incomplete', conclusive False, every tier 'unassessed
         (expectation model incomplete)'; a fit consistent with zero over
         the same span is conclusive as before.

Exit 0 iff every check passes.  Python 3 standard library in this file;
the report module needs numpy.
"""

from __future__ import print_function

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
import struct
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REPORT_PY = os.path.join(REPO, "analysis", "facility_report.py")

FAILED = []


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    print("%s: %s%s" % (tag, name, ("" if ok or not detail else
                                    " -- " + str(detail))))
    if not ok:
        FAILED.append(name)


def load_report_module():
    spec = importlib.util.spec_from_file_location("facility_report", REPORT_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures: Oulu's numbers
# ---------------------------------------------------------------------------

SWH_REQ = 6900.0                    # the run script's requested SWH
SWH_OULU = 6893.38235294118         # what the console acquired at
TD_ROW = 262144
P90_US = 8.27
PLW1_W = 0.00321366                 # 24.93 dB on the PLdB scale
PLW90_W = 26.0                      # -14.15 dB: calibration.p90_power_db_or_w
D1_REF, D1_NOISE, D11, DE_US = 2.0, 0.05, 1.0, 6.5

# TopSpin 3.7.0's stored text, as Oulu's data/11/pulseprogram carries it
# (comments cut): the cpp-style markers and the auto-inserted dccorr.
PP_ZGREF2D_TS37 = """# 1 "/opt/topspin3.7.0/exp/stan/nmr/lists/pp/user/zgref2d"
# 1 "<built-in>"
# 1 "<command-line>"
# 1 "/opt/topspin3.7.0/exp/stan/nmr/lists/pp/user/zgref2d"
;zgref2d
# 1 "/opt/topspin3.7.0/exp/stan/nmr/lists/pp/Avance.incl" 1
;Avance3aqs.incl
# 170 "/opt/topspin3.7.0/exp/stan/nmr/lists/pp/Avance.incl"
# 55 "/opt/topspin3.7.0/exp/stan/nmr/lists/pp/user/zgref2d" 2

"acqt0=-p1*2/3.1416"

# 1 "mc_line 71 file /opt/topspin3.7.0/exp/stan/nmr/lists/pp/user/zgref2d dc-measurement inserted automatically"

    dccorr
# 71 "/opt/topspin3.7.0/exp/stan/nmr/lists/pp/user/zgref2d"
# 71 "/opt/topspin3.7.0/exp/stan/nmr/lists/pp/user/zgref2d"
1 ze
  30m
2 d1
  p1 ph1
  go=2 ph31
  d11 wr #0 if #0 ze
  lo to 2 times td1
exit

ph1=0
ph31=0
"""

PP_ZGNOISE2D_TS37 = """# 1 "/opt/topspin3.7.0/exp/stan/nmr/lists/pp/user/zgnoise2d"
# 1 "<built-in>"
# 1 "<command-line>"
# 1 "/opt/topspin3.7.0/exp/stan/nmr/lists/pp/user/zgnoise2d"
;zgnoise2d
# 41 "/opt/topspin3.7.0/exp/stan/nmr/lists/pp/user/zgnoise2d" 2

"acqt0=0"

# 1 "mc_line 58 file /opt/topspin3.7.0/exp/stan/nmr/lists/pp/user/zgnoise2d dc-measurement inserted automatically"

    dccorr
# 58 "/opt/topspin3.7.0/exp/stan/nmr/lists/pp/user/zgnoise2d"
# 58 "/opt/topspin3.7.0/exp/stan/nmr/lists/pp/user/zgnoise2d"
1 ze
2 d1
  go=2 ph31
  d11 wr #0 if #0 ze
  lo to 2 times td1
exit

ph31=0
"""


def jcamp_array(name, values, n=64):
    vals = ["0"] * n
    for i, v in values.items():
        vals[i] = "%.10g" % v
    return "##$%s= (0..%d)\n%s\n" % (name, n - 1, " ".join(vals))


def acqus_text(td, swh, pulprog, d1_s, rg=10.05, pl=None, plw=None,
               pldb=None, p1_us=P90_US, de_us=DE_US):
    """A Bruker acqus in TopSpin 3.x's shape.  pl / plw / pldb are
    {index: value} dicts for the PL / PLW / PLdB arrays (None = array
    absent); the default pl is the 120 dB 'never set' sentinel
    everywhere, as TopSpin 3.7.0 writes it."""
    if pl is None:
        pl = dict((i, 120.0) for i in range(64))
    out = ("##TITLE= Parameter file, Bruker refs test\n"
           "##$PULPROG= <%s>\n"
           "##$TD= %d\n"
           "##$SW_h= %.15g\n"
           "##$SFO1= 500.08885041\n"
           "##$O1= 2350.41\n"
           "##$RG= %.6g\n"
           "##$NS= 1\n"
           "##$DS= 0\n"
           "##$DE= %.6g\n"
           "##$BYTORDA= 0\n"
           "##$DTYPA= 0\n"
           "##$DECIM= 2901.33333333333\n"
           "##$DSPFVS= 20\n"
           "##$GRPDLY= 67.9858093261719\n"
           % (pulprog, td, swh, rg, de_us))
    out += jcamp_array("D", {1: d1_s, 11: D11, 16: 0.0002})
    out += jcamp_array("P", {0: P90_US, 1: p1_us, 2: 2 * P90_US})
    if pl is not None:
        out += jcamp_array("PL", pl)
    if plw is not None:
        out += jcamp_array("PLW", plw)
    if pldb is not None:
        out += jcamp_array("PLdB", pldb)
    out += "##END=\n"
    return out.encode("ascii")


OULU_PLW = {1: PLW1_W, 9: 6.8403e-05, 10: 1.9001, 11: 0.14133, 18: PLW90_W}


def int32_rows(n_rows, td, seed):
    out = bytearray()
    v = seed
    for _r in range(n_rows):
        for _i in range(td):
            v = (v * 1103515245 + 12345) & 0x7fffffff
            out += struct.pack("<i", (v % 20001) - 10000)
    return bytes(out)


def exp_meta(expno, role, td, rows, pulprog, rg):
    return {"expno": expno, "role": role, "pulprog": pulprog, "td": td,
            "td1_rows": rows, "sw_hz": SWH_OULU, "o1_hz": 2350.41,
            "rg": rg, "ns": 1, "aq_s_per_row": td / (2.0 * SWH_OULU),
            "started_local": "2026-10-05T10:00:00",
            "finished_local": "2026-10-05T10:03:00"}


def base_meta(experiments, clock_blocks=None):
    m = {
        "schema_version": "1.2",
        "program_version": "0.7.8",
        "created_utc": "2026-10-05T08:17:48Z",
        "facility": {"institution": "Bruker refs test",
                     "facility_slug": "bruker-refs-test"},
        "environment": {"locked": False, "lock_sweep_confirmed_off": True},
        "software": {"script_version": "0.7.8", "schema_version": "1.2",
                     "script_sha256": "unavailable", "run_mode": "live"},
        "spectrometer": {"topspin_version": "topspin3.7.0",
                         "observe_freq_mhz": 500.0865,
                         "h1_freq_mhz": 500.0865, "probe_type": "RT"},
        "calibration": {"p90_us": P90_US,
                        "p90_power_db_or_w": "-14.1497 dB (PLdB 1)",
                        "rg_ladder": [{"tip_deg": 1.0, "rg": 1.0,
                                       "expno": 10}]},
        "experiments": experiments,
        "checksums": {},
    }
    if clock_blocks is not None:
        m["clock_audit"] = {"workstation_time_source": "chrony",
                            "ntp_status_raw": "", "blocks": clock_blocks}
    return m


def write_bundle(path, meta, files):
    meta = copy.deepcopy(meta)
    for arc, payload in files:
        meta["checksums"][arc] = "sha256:" + hashlib.sha256(payload).hexdigest()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta, indent=1) + "\n")
        for arc, payload in files:
            zf.writestr(arc, payload)
    return meta


# ---------------------------------------------------------------------------
# 1. Bruker reference tip angle
# ---------------------------------------------------------------------------

def power_cases(fr, work):
    db_expect = -10.0 * math.log10(PLW1_W)                   # 24.93 dB

    # a) TopSpin 3.x: PL at the sentinel everywhere, PLW set
    acq = fr.parse_jcamp(acqus_text(TD_ROW, SWH_OULU, "zgref2d", D1_REF,
                                    plw=OULU_PLW).decode("ascii"))
    db, src = fr.bruker_power_level_db(acq, 1)
    check("bruker_power_level_db: PL 120 everywhere + PLW1 %.6g W -> "
          "%.2f dB from PLW (never the 120 dB sentinel)" % (PLW1_W, db_expect),
          db is not None and abs(db - db_expect) < 1e-9
          and src.startswith("PLW1") and "W = 24.9 dB" in src, (db, src))
    db90, src90 = fr.bruker_power_level_db(acq, 18)
    check("bruker_power_level_db: the 26 W entry reads -14.15 dB, the "
          "calibration's own p90 power",
          db90 is not None and abs(db90 - (-14.1497)) < 1e-3, (db90, src90))

    # b) PLdB present, PLW zero (TopSpin 4.x shape with the watts unset)
    acq = fr.parse_jcamp(acqus_text(TD_ROW, SWH_OULU, "zgref2d", D1_REF,
                                    plw={1: 0.0}, pldb={1: 24.93}
                                    ).decode("ascii"))
    db, src = fr.bruker_power_level_db(acq, 1)
    check("bruker_power_level_db: PLW1 0 with PLdB1 24.93 -> 24.93 dB from "
          "PLdB", db == 24.93 and src.startswith("PLdB1"), (db, src))

    # c) legacy PL only (TopSpin 2.x): a real attenuation is read
    acq = fr.parse_jcamp(acqus_text(TD_ROW, SWH_OULU, "zgref2d", D1_REF,
                                    pl={1: 24.93}).decode("ascii"))
    db, src = fr.bruker_power_level_db(acq, 1)
    check("bruker_power_level_db: legacy PL1 24.93 dB alone -> read, said "
          "'legacy attenuation'",
          db == 24.93 and src.startswith("PL1") and "legacy" in src,
          (db, src))

    # d) PL at the sentinel and nothing else -> unresolved, never 120
    acq = fr.parse_jcamp(acqus_text(TD_ROW, SWH_OULU, "zgref2d", D1_REF
                                    ).decode("ascii"))
    db, src = fr.bruker_power_level_db(acq, 1)
    check("bruker_power_level_db: PL1 120 (sentinel) and no PLW/PLdB -> "
          "None with the sentinel named", db is None and "120" in src
          and "never set" in src, (db, src))
    acq = fr.parse_jcamp(acqus_text(TD_ROW, SWH_OULU, "zgref2d", D1_REF,
                                    pl={}).decode("ascii"))
    acq.pop("PL", None)
    db, src = fr.bruker_power_level_db(acq, 1)
    check("bruker_power_level_db: no PL/PLW/PLdB arrays at all -> None",
          db is None and "no PLW/PLdB/PL" in src, (db, src))

    # e) the tip angle from Oulu's record: ~1.0 deg, basis quoting PLW1
    cal = {"p90_us": P90_US, "p90_power_db_or_w": "-14.1497 dB (PLdB 1)",
           "rg_ladder": [{"tip_deg": 1.0, "rg": 1.0}]}
    ref = {"data_format": "bruker", "p1_us": P90_US, "pl1_db": db_expect,
           "pl1_source": "PLW1 %.3g W = %.3g dB" % (PLW1_W, db_expect)}
    tip, basis = fr.reference_tip_deg(ref, cal)
    want = 90.0 * 10.0 ** (-(db_expect - (-14.1497)) / 20.0)
    check("reference_tip_deg (Oulu): P1 = P90 = %.4g us, PLW1 %.6g W "
          "against -14.1497 dB -> %.4f deg (1.0 deg to 0.1%%), basis quotes "
          "the PLW entry" % (P90_US, PLW1_W, want),
          tip is not None and abs(tip - want) < 1e-9
          and abs(tip - 1.0) < 1e-3 and "PLW1" in basis
          and "P90 power -14.1 dB" in basis, (tip, basis))
    # the sentinel path: the tip falls back to the protocol's ladder tip
    tip_s, basis_s = fr.reference_tip_deg(
        {"data_format": "bruker", "p1_us": P90_US}, cal)
    check("reference_tip_deg (sentinel unresolved): falls back to the "
          "ladder tip 1 deg x P1/P90, never to 90 x 10^(-134/20) = 1.8e-5 deg",
          tip_s is not None and abs(tip_s - 1.0) < 1e-9
          and "rg_ladder" in basis_s, (tip_s, basis_s))
    # v0.7.8's number, for the record: a 120 dB 'attenuation' read as real
    tip_bad = 90.0 * 10.0 ** (-(120.0 - (-14.1497)) / 20.0)
    check("the v0.7.8 defect reproduced arithmetically: PL1 120 dB taken as "
          "real gives %.3g deg, %.3g times too small" % (tip_bad,
                                                            1.0 / tip_bad),
          abs(tip_bad - 1.77e-5) < 0.02e-5)
    # a pl1_db without a source (older dicts) still renders a basis
    tip_n, basis_n = fr.reference_tip_deg(
        {"data_format": "bruker", "p1_us": 10.0, "pl1_db": 29.08},
        {"p90_us": 10.0, "p90_power_db_or_w": "0.1 W"})
    check("reference_tip_deg: pl1_db without pl1_source -> basis says "
          "'PL1 29.1 dB', number unchanged (10 deg)",
          abs(tip_n - 90.0 * 10 ** (-(29.08 - 10.0) / 20.0)) < 1e-9
          and "PL1 29.1 dB" in basis_n, (tip_n, basis_n))

    # f) the Agilent path is untouched
    tip_ag, basis_ag = fr.reference_tip_deg(
        {"data_format": "agilent", "pw_us": 0.2, "tpwr_db": 44.0},
        {"p90_us": 4.5, "p90_power_db_or_w": "56 dB (tpwr)"})
    check("Agilent path untouched: pw 0.2 / p90 4.5 us, tpwr 44 vs 56 dB -> "
          "%.4f deg with the tpwr basis text of the SIU reports"
          % (90.0 * 0.2 / 4.5 * 10 ** ((44.0 - 56.0) / 20.0)),
          abs(tip_ag - 90.0 * 0.2 / 4.5 * 10 ** ((44.0 - 56.0) / 20.0)) < 1e-9
          and basis_ag == ("90 deg x pw 0.2 us / p90 4.5 us x 10^((tpwr 44 "
                           "dB - p90 power 56 dB)/20)"), (tip_ag, basis_ag))

    # g) analyze_reference_exp end to end on a synthetic Bruker reference
    td = 4096
    exps = [exp_meta(11, "reference_open", td, 2, "zgref2d", 10.05)]
    meta = base_meta(exps)
    files = [("data/11/acqus", acqus_text(td, SWH_OULU, "zgref2d", D1_REF,
                                          plw=OULU_PLW)),
             ("data/11/ser", int32_rows(2, td, 7))]
    p = os.path.join(work, "bruker_ref.zip")
    write_bundle(p, meta, files)
    b = fr.Bundle(p)
    res = fr.analyze_reference_exp(b, exps[0], SWH_OULU)
    check("analyze_reference_exp (Bruker): pl1_db from PLW1 (%.2f dB), "
          "pl1_source 'PLW1 ...', p1_us %.4g from the P array" % (db_expect,
                                                                  P90_US),
          res.get("readable") and abs(res.get("pl1_db", 0) - db_expect) < 1e-9
          and str(res.get("pl1_source", "")).startswith("PLW1")
          and res.get("p1_us") == P90_US,
          dict((k, res.get(k)) for k in ("readable", "pl1_db", "pl1_source",
                                         "p1_us", "why")))


# ---------------------------------------------------------------------------
# 2. Clock audit
# ---------------------------------------------------------------------------

def rec_expectation(td, rows, d1_s, fixed_s):
    """The run script's recorded expectation: rows*(TD/(2*SWH_req) + d1 +
    fixed), AQ from the REQUESTED SWH."""
    return rows * (td / (2.0 * SWH_REQ) + d1_s + fixed_s)


def true_duration(td, rows, d1_s, pre_s, p1_us, per_row_overhead_s=0.0):
    """What the OCXO actually times: the pulse-program text's terms at the
    console's SW_h, plus an (unmodelled) per-row overhead."""
    return pre_s + rows * (d1_s + p1_us * 1e-6 + DE_US * 1e-6
                           + td / (2.0 * SWH_OULU) + D11 + per_row_overhead_s)


def clock_bundle(work, tag, with_pp, per_row_overhead_s, delta=0.0,
                 recorded_swh=SWH_REQ, acqus_swh=SWH_OULU,
                 block_overhead_s=2.5):
    """A bundle with three usable clock-audit blocks (8-row zgref2d, 85-row
    zgnoise2d, 8-row zgref2d) whose wall times are the OCXO-true durations
    x (1 + delta) plus a constant per-block overhead and the given per-row
    overhead, placed so the session spans > 1 h."""
    td = TD_ROW
    plan = [(11, "reference_open", 8, "zgref2d", D1_REF, 0.030, P90_US),
            (12, "noise", 85, "zgnoise2d", D1_NOISE, 0.0, 0.0),
            (13, "reference_close", 8, "zgref2d", D1_REF, 0.030, P90_US)]
    exps, blocks, files = [], [], []
    t_ms = 1791183535738
    for expno, role, rows, pp, d1, pre, p1 in plan:
        exps.append(exp_meta(expno, role, td, rows, pp, 203.0 if role ==
                             "noise" else 10.05))
        rec = rows * (td / (2.0 * recorded_swh) + d1 + D11)
        true_s = (pre + rows * (d1 + p1 * 1e-6 + DE_US * 1e-6
                                + td / (2.0 * acqus_swh) + D11
                                + per_row_overhead_s))
        wall_s = true_s * (1.0 + delta) + block_overhead_s
        blocks.append({"expno": expno, "role": role,
                       "wall_start_ms": t_ms,
                       "wall_end_ms": t_ms + int(round(wall_s * 1000.0)),
                       "ocxo_expected_s": rec})
        t_ms += int(round(wall_s * 1000.0)) + 1800000   # 30 min gaps
        files.append(("data/%d/acqus" % expno,
                      acqus_text(td, acqus_swh, pp, d1, plw=OULU_PLW,
                                 p1_us=p1 or P90_US)))
        files.append(("data/%d/acqu2s" % expno,
                      ("##TITLE= acqu2s\n##$TD= %d\n##END=\n" % rows)
                      .encode("ascii")))
        if with_pp:
            files.append(("data/%d/pulseprogram" % expno,
                          (PP_ZGREF2D_TS37 if pp == "zgref2d"
                           else PP_ZGNOISE2D_TS37).encode("ascii")))
    meta = base_meta(exps, blocks)
    p = os.path.join(work, "clock_%s.zip" % tag)
    write_bundle(p, meta, files)
    return p


def clock_cases(fr, work):
    # a) the parser on TopSpin 3.7.0's stored text
    pre, row, why = fr.pp_timing_model(PP_ZGREF2D_TS37, 8)
    check("pp_timing_model: TopSpin 3.x stored zgref2d (mc_line marker + "
          "auto-inserted dccorr) -> pre [30m], row [d1, p1, go, d11]",
          why is None and pre == [("lit", 0.03)]
          and row == [("d", 1), ("p", 1), ("go",), ("d", 11)],
          (pre, row, why))
    pre, row, why = fr.pp_timing_model(PP_ZGNOISE2D_TS37, 85)
    check("pp_timing_model: TopSpin 3.x stored zgnoise2d -> pre [], row "
          "[d1, go, d11]", why is None and pre == []
          and row == [("d", 1), ("go",), ("d", 11)], (pre, row, why))

    # b) refinement engages with the text present (null offset, constant
    #    overhead only): conclusive, offset consistent with zero
    p = clock_bundle(work, "pp_null", with_pp=True, per_row_overhead_s=0.0)
    b = fr.Bundle(p)
    ca = fr.analyze_clock_audit(b.meta, b)
    er = ca.get("expectation_refinement") or {}
    check("clock audit (text present): refinement engaged on all 3 usable "
          "blocks (n_refined 3, none recorded-only)",
          ca.get("n_usable") == 3 and er.get("n_refined") == 3
          and er.get("n_recorded_only") == 0 and er.get("n_swh_corrected") == 0
          and all(x.get("expected_source") == "acqus-refined"
                  for x in ca["blocks"] if x.get("used")),
          (ca.get("n_usable"), er, [x.get("expected_source") for x in
                                    ca["blocks"]]))
    exp11 = [x for x in ca["blocks"] if x["expno"] == 11][0]
    want11 = true_duration(TD_ROW, 8, D1_REF, 0.030, P90_US)
    check("clock audit (text present): expno 11 expected = 30m + 8 x (d1 + "
          "p1 + DE + AQ(SW_h) + d11) = %.6f s" % want11,
          abs(exp11["ocxo_expected_s"] - want11) < 1e-6,
          exp11.get("ocxo_expected_s"))
    check("clock audit (text present, null): offset consistent with zero "
          "(|delta| < 3 err), conclusive, status 'fractional console-clock "
          "offset', model_incomplete False, per-block excess ~2.5 s listed",
          ca.get("conclusive") is True and not ca.get("model_incomplete")
          and abs(ca["fractional_offset"]) < 3 * ca["fractional_offset_err"]
          and str(ca.get("status", "")).startswith("fractional console-clock "
                                                   "offset")
          and "expno 12 +2.5" in ca.get("per_block_excess", "")
          and all(t["verdict"].startswith(("satisfied", "audit precision"))
                  for t in ca["tiers"]),
          (ca.get("fractional_offset"), ca.get("fractional_offset_err"),
           ca.get("status"), ca.get("per_block_excess")))

    # c) NO text: the recorded expectation is SW_h-corrected, exactly
    p = clock_bundle(work, "nopp_null", with_pp=False, per_row_overhead_s=0.0)
    b = fr.Bundle(p)
    ca = fr.analyze_clock_audit(b.meta, b)
    er = ca.get("expectation_refinement") or {}
    used = [x for x in ca["blocks"] if x.get("used")]
    corr_ok = True
    for x in used:
        rows = x["rows"]
        rec = x["ocxo_recorded_s"]
        want = rec + rows * (TD_ROW / 2.0) * (1.0 / SWH_OULU - 1.0 / SWH_REQ)
        corr_ok = corr_ok and abs(x["ocxo_expected_s"] - want) < 1e-9 \
            and x.get("expected_source") == "SW_h-corrected" \
            and "corrected for the console's SW_h" in x.get("swh_note", "") \
            and "no pulseprogram text" in x.get("refine_note", "")
    check("clock audit (no text): every usable block 'SW_h-corrected' by "
          "rows x TD/2 x (1/SW_h - 1/6900) exactly, the refine_note still "
          "saying no text; n_swh_corrected 3, n_refined 0",
          len(used) == 3 and corr_ok and er.get("n_swh_corrected") == 3
          and er.get("n_refined") == 0 and er.get("n_recorded_only") == 0
          and "swh_note" in er,
          [(x.get("expected_source"), x.get("ocxo_expected_s"),
            x.get("ocxo_recorded_s")) for x in used])
    check("clock audit (no text, null): the SW_h correction removes the "
          "dominant bias -- |delta| < 1e-4 (the raw recorded model would "
          "sit at ~+1e-3), and no 'recorded_model' comparison (nothing was "
          "pulse-program-derived)",
          abs(ca["fractional_offset"]) < 1e-4
          and "recorded_model" not in ca,
          (ca.get("fractional_offset"), ca.get("fractional_offset_err")))

    # d) the correction is skipped when not applicable, with a note
    p = clock_bundle(work, "nopp_actual", with_pp=False,
                     per_row_overhead_s=0.0, recorded_swh=SWH_OULU)
    b = fr.Bundle(p)
    ca = fr.analyze_clock_audit(b.meta, b)
    used = [x for x in ca["blocks"] if x.get("used")]
    check("clock audit (no text, recorded already at the console's SW_h): "
          "no correction, source 'script-recorded', note says so",
          len(used) == 3 and all(
              x.get("expected_source") == "script-recorded"
              and "already built from the console's SW_h" in x.get("swh_note",
                                                                  "")
              and abs(x["ocxo_expected_s"] - x["ocxo_recorded_s"]) < 1e-12
              for x in used),
          [(x.get("expected_source"), x.get("swh_note")) for x in used][:1])
    p = clock_bundle(work, "nopp_6900", with_pp=False, per_row_overhead_s=0.0,
                     recorded_swh=SWH_REQ, acqus_swh=SWH_REQ)
    b = fr.Bundle(p)
    ca = fr.analyze_clock_audit(b.meta, b)
    used = [x for x in ca["blocks"] if x.get("used")]
    check("clock audit (no text, console SW_h = requested 6900 Hz): nothing "
          "to correct, source 'script-recorded'",
          len(used) == 3 and all(
              x.get("expected_source") == "script-recorded"
              and "nothing to correct" in x.get("swh_note", "") for x in used),
          [(x.get("expected_source"), x.get("swh_note")) for x in used][:1])

    # e) conclusiveness: 5 ms of unmodelled overhead per 20 s row, text
    #    present, > 1 h span -> 'expectation model incomplete'
    p = clock_bundle(work, "pp_overhead", with_pp=True,
                     per_row_overhead_s=0.005)
    b = fr.Bundle(p)
    ca = fr.analyze_clock_audit(b.meta, b)
    check("clock audit (5 ms/row unmodelled, text present, %.1f h span): "
          "fit ~+2.5e-4 >> 1e-5 and significant -> model_incomplete True, "
          "conclusive False, status 'expectation model incomplete', every "
          "tier 'unassessed (expectation model incomplete)', the per-block "
          "excess quoted" % (ca.get("session_span_s", 0) / 3600.0),
          ca.get("session_span_s", 0) > fr.CLOCK_MIN_SPAN_S
          and ca.get("model_incomplete") is True
          and ca.get("conclusive") is False
          and 1e-4 < ca["fractional_offset"] < 5e-4
          and str(ca.get("status", "")).startswith("expectation model "
                                                   "incomplete")
          and "per-block excess" in ca.get("status", "")
          and "every usable block is pulse-program-derived" in ca.get(
              "model_incomplete_why", "")
          and all(t["verdict"] == "unassessed (expectation model incomplete)"
                  for t in ca["tiers"]),
          (ca.get("fractional_offset"), ca.get("fractional_offset_err"),
           ca.get("conclusive"), ca.get("status", "")[:200]))
    # ... and the same without text: the wording names the recorded blocks
    p = clock_bundle(work, "nopp_overhead", with_pp=False,
                     per_row_overhead_s=0.005)
    b = fr.Bundle(p)
    ca = fr.analyze_clock_audit(b.meta, b)
    check("clock audit (5 ms/row unmodelled, no text): 'expectation model "
          "incomplete' naming the 3 script-recorded (SW_h-corrected) blocks, "
          "conclusive False",
          ca.get("model_incomplete") is True and ca.get("conclusive") is False
          and "3 of 3 usable blocks keep the script-recorded expectation"
          in ca.get("model_incomplete_why", "")
          and "SW_h-corrected" in ca.get("model_incomplete_why", ""),
          ca.get("model_incomplete_why", "")[:300])
    # a short session that is also incomplete says both
    html = fr.render_clock_audit_html(ca)
    check("render_clock_audit_html: renders the incomplete audit with the "
          "excess column and the status, no exception",
          "expectation model incomplete" in html
          and "excess wall" in html and "SW_h-corrected" in html)

    # f) a genuine small offset over > 1 h with text: recovered, conclusive
    p = clock_bundle(work, "pp_3e-7", with_pp=True, per_row_overhead_s=0.0,
                     delta=3e-7)
    b = fr.Bundle(p)
    ca = fr.analyze_clock_audit(b.meta, b)
    check("clock audit (injected 3e-7, text present): fit within 3 sigma of "
          "3e-7, conclusive, not model-incomplete (a plausible offset is "
          "never gated)",
          ca.get("conclusive") is True and not ca.get("model_incomplete")
          and abs(ca["fractional_offset"] - 3e-7) < 3 * ca[
              "fractional_offset_err"],
          (ca.get("fractional_offset"), ca.get("fractional_offset_err")))


# ---------------------------------------------------------------------------
# optional: a real Oulu bundle
# ---------------------------------------------------------------------------

def real_bundle_cases(fr, path):
    b = fr.Bundle(path)
    acq = b.acqus(11)
    db, src = fr.bruker_power_level_db(acq, 1)
    check("real bundle: expno 11 channel-1 power from PLW, 20-30 dB "
          "(a ~1 degree small flip against the calibrated 26 W)",
          db is not None and 20.0 < db < 30.0 and src.startswith("PLW1"),
          (db, src))
    exps = dict((e["expno"], e) for e in b.meta["experiments"])
    ca = fr.analyze_clock_audit(b.meta, b)
    er = ca.get("expectation_refinement") or {}
    check("real bundle: refinement engaged on every usable block "
          "(TopSpin 3.x text with dccorr)",
          er.get("n_refined") == ca.get("n_usable") and ca.get("n_usable", 0)
          >= 1, (er, ca.get("n_usable")))
    print("       real bundle clock status: %s" % ca.get("status", "")[:400])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--bundle", default=None,
                    help="a real Oulu bundle zip to check as well (read-only)")
    args = ap.parse_args(argv)
    work = args.out_dir or tempfile.mkdtemp(prefix="report_bruker_refs_")
    if not os.path.isdir(work):
        os.makedirs(work)
    try:
        fr = load_report_module()
    except ImportError as exc:
        print("FAIL: cannot import analysis/facility_report.py (%s); the "
              "report needs numpy" % exc)
        return 1

    print("--- 1. Bruker reference tip angle (PLW before the PL sentinel) ---")
    power_cases(fr, work)
    print("--- 2. clock audit (dccorr, SW_h correction, conclusiveness) ---")
    clock_cases(fr, work)
    if args.bundle:
        print("--- 3. real bundle: %s ---" % args.bundle)
        real_bundle_cases(fr, args.bundle)

    print("")
    if FAILED:
        print("REPORT BRUKER REFS: %d FAILED" % len(FAILED))
        for n in FAILED:
            print("  - %s" % n)
        return 1
    print("REPORT BRUKER REFS: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
