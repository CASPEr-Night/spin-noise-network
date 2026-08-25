#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
repackage_epfl.py -- repackage the real 2020-05-29 EPFL acetone spin-noise
dataset into a network-format bundle, as ground truth for
analysis/facility_report.py.

    python3 testing/repackage_epfl.py [--npz PATH] [--out-dir DIR]

Prints exactly one line on stdout: the bundle path (progress on stderr).

Mapping (2020 session -> network expno tree):
  exp 2  (pulsed zg30, AQ 2.73 s, RG 0.96)   -> expno 11, reference_open
  exps 3-9 (seven 101 s pure-noise records,
            RG 184.37)                        -> expno 12, noise, 7 rows
  exp 10 (pulsed zg30, AQ 101 s, RG 0.96,
          run between noise runs 3 and 4)     -> expno 13, reference_close

Real acquisition parameters (verified): SW 12019.23 Hz, SFO1 600.133705802
MHz, O1 +3705.8 Hz, DECIM 1664, DSPFVS 20, GRPDLY 67.984, int32 little-endian.
The extracted complex records in fids_extracted.npz came from int32 raw data,
so int32 round-tripping is exact.

meta.json declares run_mode 'archival-repackage' (schema enum, v1.1): real
science data, but not produced by the live orchestrator -- no RG ladder was
acquired in 2020 (the report must and does surface that), timestamps are
reconstructed from audita.txt mid-times, and coil/preamp temperatures were
not logged (the report must and does say the temperature-contrast point
requires them).

Python 3 + numpy only.
"""

from __future__ import print_function

import argparse
import hashlib
import io
import json
import os
import random
import sys
import time
import zipfile

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_NPZ = os.path.abspath(os.path.join(
    REPO, "..", "Spin_Noise_2020", "extracted", "fids_extracted.npz"))

FACILITY_SLUG = "epfl-archival-2020"

NOISE_EXPS = ["3", "4", "5", "6", "7", "8", "9"]
RG_NOISE = 184.37
RG_PULSED = 0.96

# mid-acquisition wall clock, minutes after 09:00 CEST on 2020-05-29
# (from the original audita.txt; used to reconstruct start/finish times)
T_MID_MIN = {"2": 45.4, "3": 60.8, "10": 70.7, "4": 82.9, "5": 85.1,
             "6": 87.6, "7": 89.4, "8": 91.1, "9": 92.9}


def info(msg):
    print(msg, file=sys.stderr)


def acqus_text(td, rg, pulprog):
    return (
        "##TITLE= Parameter file, archival repackage of the 2020-05-29 EPFL run\n"
        "##$PULPROG= <%s>\n"
        "##$TD= %d\n"
        "##$SW_h= 12019.2307692308\n"
        "##$SFO1= 600.133705802\n"
        "##$O1= 3705.802\n"
        "##$RG= %.6g\n"
        "##$NS= 1\n"
        "##$BYTORDA= 0\n"
        "##$DTYPA= 0\n"
        "##$DECIM= 1664\n"
        "##$DSPFVS= 20\n"
        "##$GRPDLY= 67.9841613769531\n"
        "##END=\n" % (pulprog, td, rg)
    ).encode("ascii")


def acqu2s_text(rows):
    return ("##TITLE= Parameter file F1, archival repackage\n"
            "##$TD= %d\n##END=\n" % rows).encode("ascii")


def to_bruker_int32(rows_complex):
    """Interleave re/im as little-endian int32, pad rows to 1024 bytes
    (Bruker ser convention)."""
    out = io.BytesIO()
    for row in rows_complex:
        v = np.empty(row.size * 2, dtype="<i4")
        v[0::2] = np.round(row.real)
        v[1::2] = np.round(row.imag)
        b = v.tobytes()
        pad = (-len(b)) % 1024
        out.write(b + b"\x00" * pad)
    return out.getvalue()


def local_time(min_after_9, half_span_min):
    """Reconstructed local start/finish around a mid-time on 2020-05-29."""
    def stamp(m):
        h = 9 + int(m) // 60
        mm = int(m) % 60
        s = int((m - int(m)) * 60)
        return "2020-05-29T%02d:%02d:%02d" % (h, mm, s)
    return (stamp(min_after_9 - half_span_min),
            stamp(min_after_9 + half_span_min))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="repackage the 2020 EPFL dataset as a network bundle")
    ap.add_argument("--npz", default=DEFAULT_NPZ)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)

    info("loading %s" % args.npz)
    z = np.load(args.npz, allow_pickle=True)
    params = json.loads(str(z["params_json"]))
    td_noise = int(params["3"]["TD"])          # 2,427,882 ints per record
    td_ref2 = int(2 * z["exp2"].size)          # 65,536
    td_ref10 = int(2 * z["exp10"].size)

    files = []

    # reference_open  <- exp 2
    files.append(("data/11/acqus", acqus_text(td_ref2, RG_PULSED, "zg30")))
    files.append(("data/11/fid", to_bruker_int32([z["exp2"]])))
    # noise           <- exps 3-9 as 7 rows of one pseudo-2D ser
    info("packing 7 noise records as pseudo-2D rows")
    files.append(("data/12/acqus", acqus_text(td_noise, RG_NOISE, "zgnoise2d")))
    files.append(("data/12/acqu2s", acqu2s_text(len(NOISE_EXPS))))
    files.append(("data/12/ser",
                  to_bruker_int32([z["exp" + e] for e in NOISE_EXPS])))
    # reference_close <- exp 10
    files.append(("data/13/acqus", acqus_text(td_ref10, RG_PULSED, "zg30")))
    files.append(("data/13/fid", to_bruker_int32([z["exp10"]])))

    with open(os.path.join(REPO, "VERSION")) as fh:
        version = fh.read().strip()
    aq_noise = td_noise / 2.0 / 12019.2307692308

    s2, f2 = local_time(T_MID_MIN["2"], 1.4)
    s10, f10 = local_time(T_MID_MIN["10"], 0.9)
    sn, fn = local_time((T_MID_MIN["3"] + T_MID_MIN["9"]) / 2.0,
                        (T_MID_MIN["9"] - T_MID_MIN["3"]) / 2.0 + 0.9)

    def expmeta(expno, role, pulprog, td, rows, rg, aq, t0, t1):
        return {"expno": expno, "role": role, "pulprog": pulprog, "td": td,
                "td1_rows": rows, "sw_hz": 12019.2307692308,
                "o1_hz": 3705.802, "rg": rg, "ns": 1, "aq_s_per_row": aq,
                "started_local": t0, "finished_local": t1}

    meta = {
        "schema_version": "1.1",
        "program_version": version,
        "software": {"script_version": version, "schema_version": "1.1",
                     "script_sha256": "unavailable",
                     "run_mode": "archival-repackage"},
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "local_timezone_offset_min": 120,      # CEST at acquisition time
        "facility": {
            "institution": "EPFL (Lyndon Emsley laboratory)",
            "city": "Lausanne", "country": "CH",
            "facility_slug": FACILITY_SLUG,
            "contact_email": "jwbquantum@gmail.com",
            "contact_consent": True},
        "spectrometer": {
            "topspin_version": "3.x (archival; exact version not retained)",
            "h1_freq_mhz": 600.133705802, "field_tesla": 14.095,
            "console": "Avance III HD",
            "probe_string": "CryoProbe Prodigy BBO (N2-cooled)",
            "probe_type": "N2-cryo",
            "coil_temp_k": None,               # not logged in 2020 -- the
            "preamp_temp_k": None},            # report must say so honestly
        "sample": {
            "description": "neat protonated acetone (2020-05-29 session)",
            "h2o_fraction_pct": 0.0, "d2o_pct": 0.0, "additives": "none",
            "tube_od_mm": 5.0, "sample_volume_ul": 550.0,
            "vt_setpoint_k": 298.0},
        "environment": {
            "locked": False,                   # deuterium lock OFF
            "lock_sweep_confirmed_off": True,
            "operator_notes":
                "ARCHIVAL REPACKAGE of the 2020-05-29 EPFL session "
                "(transmitter cable physically detached for noise runs; "
                "lock off). Timestamps reconstructed from audita.txt "
                "mid-times. reference_close (original exp 10) was acquired "
                "BETWEEN noise records 1 and 2 of the original session. "
                "p90_us is nominal: the original session used factory zg30 "
                "(flip pinned 30.0 +/- 1.5 deg, p0=p1/3). No RG ladder was "
                "acquired in 2020."},
        "calibration": {
            "p90_us": 10.0,                    # nominal placeholder, see notes
            "p90_power_db_or_w": "not retained (100 W amplifier)",
            "rg_ladder": [{"expno": 11, "rg": RG_PULSED, "tip_deg": 30.0}],
            "topshim_ok": True},
        "experiments": [
            expmeta(11, "reference_open", "zg30", td_ref2, 1, RG_PULSED,
                    td_ref2 / 2.0 / 12019.2307692308, s2, f2),
            expmeta(12, "noise", "zgnoise2d", td_noise, len(NOISE_EXPS),
                    RG_NOISE, aq_noise, sn, fn),
            expmeta(13, "reference_close", "zg30", td_ref10, 1, RG_PULSED,
                    td_ref10 / 2.0 / 12019.2307692308, s10, f10),
        ],
        "checksums": {},
        "archival_provenance": {
            "source": "Spin_Noise_2020/extracted/fids_extracted.npz",
            "original_expnos": {"reference_open": 2,
                                "noise_rows_in_order": NOISE_EXPS,
                                "reference_close": 10},
            "acquired": "2020-05-29, Bruker Avance III HD 600 (14.095 T)",
            "verified_published_numbers": {
                "coadded_peak_excess_x_floor": 2.30,
                "coadded_fwhm_hz": 11.9,
                "dispersive_b_over_a": 0.46}},
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
    info("zipping (7 x %.1f MB noise rows; a minute or two)"
         % (td_noise * 4 / 1e6))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta, indent=1) + "\n")
        for arc, payload in files:
            zf.writestr(arc, payload)
    info("bundle: %s (%.1f MiB)" % (path, os.path.getsize(path) / 1048576.0))
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
