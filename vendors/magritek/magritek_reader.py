#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
magritek_reader.py -- Magritek Spinsolve session reader and bundle packer
for the spin-noise network (Work Package B).

Reads a session directory produced by spin_noise_run_spinsolve.mac
(answers.json + numbered experiment folders, each holding acqu.par +
data.1d) and packs it into a spin-noise network bundle zip:

    python3 vendors/magritek/magritek_reader.py pack <session_dir> [--out-dir D]
        [--schema-version 2.0|1.2]
    python3 vendors/magritek/magritek_reader.py inspect <file.1d | acqu.par>
    python3 vendors/magritek/magritek_reader.py meta <session_dir>   # print meta.json

`pack` prints exactly one line on stdout (the bundle path); progress goes
to stderr, so scripts can do:

    python3 uploader/upload_bundle.py \\
        "$(python3 vendors/magritek/magritek_reader.py pack SESSION)" --selftest

STATUS / HONESTY:
  * The .1d binary layout and acqu.par text format implemented here follow
    nmrglue's Spinsolve reader (nmrglue/fileio/spinsolve.py, BSD-3-Clause,
    https://github.com/jjhelmus/nmrglue) -- logic re-implemented, not
    copied; nmrglue is the citation for the format facts:
      - acqu.par: one `key = value` per line; values in double quotes are
        strings, else int-then-float-then-string.
      - .1d: 32-byte header of eight 4-byte little-endian integers
        [owner, format, version, dataType, xDim, yDim, zDim, qDim], then
        IEEE float32 LE payload; a 1D of N complex points carries 3N
        floats (N x-axis values, then interleaved re/im).
    UNVERIFIED: the meaning of the header magic/dataType values (nmrglue
    reads past them without decoding). This reader checks STRUCTURAL
    consistency (payload length vs xDim) instead of magic values, and
    records the raw header in the packing report.
  * UNVERIFIED (bench-session items, see vendors/magritek/README.md
    checklist): acqu.par parameter spelling/units as written by a REAL
    Spinsolve (`b1Freq` MHz, `dwellTime` us, `nrPnts`, `nrScans`,
    `rxGain` dB are the Prospa-ecosystem names and are parsed leniently),
    and every answers.json field (operator/macro supplied).
  * This adapter has NEVER seen real Spinsolve data. Real sessions are
    stamped run_mode "external-acquisition"; the synthetic generator
    stamps "desktest" via answers.json run_mode_hint so a test bundle can
    never masquerade as data.

Schema: emits schema 2.0 by default (vendor "magritek" + the
instrument.magritek block, per schema/meta.schema.json). `--schema-version
1.2` exists as a fallback for a facility stuck on the pre-2.0 toolchain;
in that mode the two Bruker-specific 1.x fields are filled with explicit
"n/a (Magritek Spinsolve)" markers, documented in README.md.

Adapter interface note (for packer/pack_bundle.py, package A): the
function `read_session(session_dir)` returns a vendor-neutral dict and
`build_meta(session, schema_version)` turns it into a full meta.json
dict; the CLI below is a thin standalone wrapper so the chain is testable
before the central packer lands.

Python 3 standard library only, nothing newer than 3.6 (same portability
rules as the uploader).

Maintainer: John W. Blanchard, jwbquantum@gmail.com
Co-developed with Claude (Anthropic).
"""

from __future__ import print_function

import argparse
import hashlib
import json
import os
import random
import struct
import sys
import time
import zipfile

ADAPTER_VERSION = "0.1.0-draft"
VENDOR = "magritek"

# Session-layout convention shared with spin_noise_run_spinsolve.mac:
# expno -> role. Noise blocks are 20, 21, 22, ... (open-ended).
ROLE_BY_EXPNO = {10: "rg_ladder", 14: "rg_ladder", 15: "rg_ladder",
                 16: "rg_ladder", 11: "reference_open",
                 13: "reference_close"}
NOISE_EXPNO_MIN = 20

GAMMA_H_MHZ_PER_T = 42.5774689

HEADER_KEYS = ("owner", "format", "version", "dataType",
               "xDim", "yDim", "zDim", "qDim")


def info(msg):
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Format parsers (format facts per nmrglue's Spinsolve reader; see header)
# ---------------------------------------------------------------------------

def parse_acqu_par(path):
    """Parse a Spinsolve/Prospa acqu.par file into a dict.

    Rules (per nmrglue): `key = value` lines; a value wrapped in double
    quotes is a string; otherwise try int, then float, then keep string.
    Unparseable lines are kept verbatim under the key `_unparsed`.
    """
    out = {}
    unparsed = []
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or "=" not in line:
                if line:
                    unparsed.append(line)
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                out[name] = value[1:-1]
                continue
            try:
                out[name] = int(value)
                continue
            except ValueError:
                pass
            try:
                out[name] = float(value)
            except ValueError:
                out[name] = value
    if unparsed:
        out["_unparsed"] = unparsed
    return out


def read_1d(path):
    """Read a Prospa .1d file; return dict with header, counts, raw size.

    Structural checks only -- the header magic/dataType meanings are
    UNVERIFIED (see module docstring), so nothing is asserted about them
    beyond internal consistency of the payload length:
      expected payload = 3 * xDim float32 (x axis + interleaved re/im).
    """
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        raw_header = fh.read(32)
    if len(raw_header) < 32:
        raise ValueError("%s: shorter than the 32-byte .1d header" % path)
    values = struct.unpack("<8I", raw_header)
    header = dict(zip(HEADER_KEYS, values))
    payload_bytes = size - 32
    result = {
        "path": path,
        "header": header,
        "file_bytes": size,
        "payload_floats": payload_bytes // 4,
        "structure_ok": False,
        "n_complex_points": None,
    }
    if payload_bytes % 4 != 0:
        return result
    nfloats = payload_bytes // 4
    xdim = header["xDim"]
    if xdim > 0 and nfloats == 3 * xdim:
        result["structure_ok"] = True
        result["n_complex_points"] = xdim
    return result


# ---------------------------------------------------------------------------
# Session reading
# ---------------------------------------------------------------------------

def _role_for_expno(expno):
    if expno in ROLE_BY_EXPNO:
        return ROLE_BY_EXPNO[expno]
    if expno >= NOISE_EXPNO_MIN:
        return "noise"
    return None


def read_session(session_dir):
    """Read a Spinsolve spin-noise session directory.

    Returns a dict:
      answers      -- parsed answers.json (or {} with a warning)
      experiments  -- list of dicts, session order (sorted by role phase
                      then expno): expno, role, dir, acqu (dict),
                      one_d (read_1d result or None), files [(arc, abs)]
      warnings     -- list of strings
    """
    warnings = []
    answers = {}
    apath = os.path.join(session_dir, "answers.json")
    if os.path.isfile(apath):
        try:
            with open(apath, "r") as fh:
                answers = json.load(fh)
        except ValueError as exc:
            warnings.append("answers.json unparseable (%s) -- proceeding "
                            "with acqu.par data only" % exc)
    else:
        warnings.append("no answers.json in session -- operator fields "
                        "will be placeholders")

    experiments = []
    for entry in sorted(os.listdir(session_dir)):
        full = os.path.join(session_dir, entry)
        if not (os.path.isdir(full) and entry.isdigit()):
            continue
        expno = int(entry)
        role = _role_for_expno(expno)
        if role is None:
            warnings.append("folder %s: expno outside the session "
                            "convention -- skipped" % entry)
            continue
        acqu_path = os.path.join(full, "acqu.par")
        data_path = os.path.join(full, "data.1d")
        acqu = {}
        one_d = None
        files = []
        if os.path.isfile(acqu_path):
            acqu = parse_acqu_par(acqu_path)
            files.append(("data/%d/acqu.par" % expno, acqu_path))
        else:
            warnings.append("expno %d: no acqu.par" % expno)
        if os.path.isfile(data_path):
            one_d = read_1d(data_path)
            if not one_d["structure_ok"]:
                warnings.append("expno %d: data.1d payload does not match "
                                "the expected 3*xDim float32 layout "
                                "(UNVERIFIED format variant?) -- packed "
                                "verbatim anyway" % expno)
            files.append(("data/%d/data.1d" % expno, data_path))
        else:
            warnings.append("expno %d: no data.1d" % expno)
        experiments.append({"expno": expno, "role": role, "dir": full,
                            "acqu": acqu, "one_d": one_d, "files": files})

    # Session order: ladder, reference_open, noise (by expno), close.
    phase = {"rg_ladder": 0, "reference_open": 1, "noise": 2,
             "reference_close": 3}
    experiments.sort(key=lambda e: (phase[e["role"]], e["expno"]))

    if not experiments:
        raise SystemExit("no experiment folders found under %s" % session_dir)
    return {"answers": answers, "experiments": experiments,
            "answers_path": apath if os.path.isfile(apath) else None,
            "warnings": warnings}


# ---------------------------------------------------------------------------
# meta.json construction
# ---------------------------------------------------------------------------

def _block_record(answers, expno):
    for blk in answers.get("blocks", []):
        if blk.get("expno") == expno:
            return blk
    return {}


def _local_iso(ms, fallback):
    if isinstance(ms, (int, float)) and ms > 0:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ms / 1000.0))
    return fallback


def _experiment_entry(exp, answers, fallback_time):
    """Map one Spinsolve experiment to a schema `experiments[]` item.

    Mapping (README.md table; UNVERIFIED unit assumptions flagged there):
      td   = 2 * nrPnts        (Bruker TD convention: re+im points)
      sw_hz = 1e6 / dwellTime  (dwellTime assumed us)
      rg   = 10^(rxGain/20)    (linear amplitude, Bruker-comparable)
      o1_hz = 0.0              (b1Freq is absolute; offset convention n/a)
      aq_s_per_row = nrPnts * dwellTime * 1e-6
    """
    acqu = exp["acqu"]
    blk = _block_record(answers, exp["expno"])

    nr_pnts = acqu.get("nrPnts")
    if not isinstance(nr_pnts, int) or nr_pnts <= 0:
        one_d = exp.get("one_d")
        nr_pnts = (one_d or {}).get("n_complex_points") or 1
    dwell_us = acqu.get("dwellTime")
    if not isinstance(dwell_us, (int, float)) or dwell_us <= 0:
        dwell_us = 100.0  # placeholder; flagged by warnings upstream
    rx_gain_db = acqu.get("rxGain")
    if not isinstance(rx_gain_db, (int, float)):
        rx_gain_db = blk.get("rx_gain_db", 0.0)
    ns = acqu.get("nrScans")
    if not isinstance(ns, int) or ns < 1:
        ns = 1

    pulprog = acqu.get("experiment")
    if not isinstance(pulprog, str) or not pulprog:
        pulprog = ("spin_noise_nopulse" if exp["role"] == "noise"
                   else "spin_noise_smallflip")

    return {
        "expno": exp["expno"],
        "role": exp["role"],
        "pulprog": pulprog,
        "td": 2 * int(nr_pnts),
        "td1_rows": 1,
        "sw_hz": 1.0e6 / float(dwell_us),
        "o1_hz": 0.0,
        "rg": 10.0 ** (float(rx_gain_db) / 20.0),
        "ns": ns,
        "aq_s_per_row": int(nr_pnts) * float(dwell_us) * 1.0e-6,
        "started_local": _local_iso(blk.get("wall_start_ms"), fallback_time),
        "finished_local": _local_iso(blk.get("wall_end_ms"), fallback_time),
    }


def _clock_audit(answers, experiments):
    """Build the optional schema-1.2+ clock_audit object from the macro's
    per-block wall-clock stamps, when present and non-placeholder."""
    blocks = []
    by_expno = {e["expno"]: e for e in experiments}
    for blk in answers.get("blocks", []):
        t0, t1 = blk.get("wall_start_ms"), blk.get("wall_end_ms")
        exp = by_expno.get(blk.get("expno"))
        if exp is None or not isinstance(t0, int) or not isinstance(t1, int):
            continue
        if t0 <= 0 or t1 <= t0:
            continue
        acqu = exp["acqu"]
        nr_pnts = acqu.get("nrPnts")
        dwell_us = acqu.get("dwellTime")
        ns = acqu.get("nrScans", 1)
        if (isinstance(nr_pnts, int) and isinstance(dwell_us, (int, float))
                and nr_pnts > 0 and dwell_us > 0):
            expected = nr_pnts * dwell_us * 1.0e-6 * max(int(ns), 1)
        else:
            expected = None
        blocks.append({"expno": blk["expno"], "role": exp["role"],
                       "wall_start_ms": t0, "wall_end_ms": t1,
                       "ocxo_expected_s": expected})
    if not blocks:
        return None
    return {
        "blocks": blocks,
        # Packed after the fact on a different machine, so the
        # acquisition workstation's NTP state is unknown here; the bench
        # session decides whether the macro can capture it (checklist 8).
        "ntp_status_raw": "unavailable (packed offline by magritek_reader.py)",
        "workstation_time_source": "unknown",
    }


def build_meta(session, schema_version="2.0"):
    """Build the full meta.json dict for a read_session() result."""
    if schema_version not in ("2.0", "1.2"):
        raise ValueError("unsupported schema_version %r" % schema_version)
    answers = session["answers"]
    experiments = session["experiments"]

    utc = time.gmtime()
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", utc)
    fallback_local = time.strftime("%Y-%m-%dT%H:%M:%S")
    if time.daylight and time.localtime().tm_isdst:
        tz_off_min = -int(time.altzone // 60)
    else:
        tz_off_min = -int(time.timezone // 60)

    # 1H frequency: first acqu.par b1Freq (assumed MHz), else answers.
    b1 = None
    for exp in experiments:
        v = exp["acqu"].get("b1Freq")
        if isinstance(v, (int, float)) and v > 0:
            b1 = float(v)
            break
    if b1 is None:
        v = answers.get("b1_freq_mhz")
        b1 = float(v) if isinstance(v, (int, float)) and v > 0 else 60.0

    run_mode = answers.get("run_mode_hint", "external-acquisition")
    if run_mode not in ("external-acquisition", "desktest", "simulate",
                        "synthetic-injection", "archival-repackage"):
        run_mode = "external-acquisition"

    ref_tip = answers.get("ref_tip_deg")
    if not isinstance(ref_tip, (int, float)) or ref_tip <= 0:
        ref_tip = 5.0

    noise_gain_db = None
    for exp in experiments:
        if exp["role"] == "noise":
            g = exp["acqu"].get("rxGain")
            if isinstance(g, (int, float)):
                noise_gain_db = float(g)
                break
    if noise_gain_db is None:
        for blk in answers.get("blocks", []):
            if blk.get("role") == "noise":
                g = blk.get("rx_gain_db")
                if isinstance(g, (int, float)):
                    noise_gain_db = float(g)
                    break

    lock_state = str(answers.get("lock_state", "unknown"))
    notes = str(answers.get("operator_notes", ""))
    lock_note = ("Spinsolve external hardware lock state during noise "
                 "blocks: %s." % lock_state)
    operator_notes = (notes + " " + lock_note).strip()

    sw_version = str(answers.get("spinsolve_software_version",
                                 "unknown (pending bench validation)"))

    meta = {
        "schema_version": schema_version,
        "program_version": ADAPTER_VERSION,
        "software": {
            "script_version": ADAPTER_VERSION,
            "schema_version": schema_version,
            "script_sha256": _self_sha256(),
            "writer": "vendors/magritek/magritek_reader.py",
            "run_mode": run_mode,
        },
        "created_utc": created,
        "local_timezone_offset_min": tz_off_min,
        "facility": {
            "institution": str(answers.get("institution", "UNKNOWN")),
            "city": str(answers.get("city", "UNKNOWN")),
            "country": str(answers.get("country", "UNKNOWN")),
            "facility_slug": str(answers.get("facility_slug",
                                             "unknown-facility")),
            "contact_email": (str(answers.get("contact_email", ""))
                              if str(answers.get("contact_consent", "no"))
                              .lower() in ("yes", "true") else ""),
            "contact_consent": str(answers.get("contact_consent", "no"))
                               .lower() in ("yes", "true"),
        },
        "spectrometer": {
            "h1_freq_mhz": b1,
            "field_tesla": b1 / GAMMA_H_MHZ_PER_T,
            "console": str(answers.get("model", "Magritek Spinsolve "
                                                "(model pending bench "
                                                "session)")),
            "probe_string": "Spinsolve integrated benchtop probe",
            # schema 2.0 added the accurate class; the 1.2 enum lacks it,
            # so the fallback keeps the closest 1.x truth (an RT coil).
            "probe_type": ("permanent-magnet-benchtop"
                           if schema_version == "2.0" else "RT"),
            "coil_temp_k": answers.get("vt_setpoint_k")
                           if isinstance(answers.get("vt_setpoint_k"),
                                         (int, float)) else None,
            "preamp_temp_k": None,
        },
        "sample": {
            "description": str(answers.get("sample_description", "UNKNOWN")),
            "h2o_fraction_pct": float(answers.get("h2o_fraction_pct", 100.0)),
            "d2o_pct": float(answers.get("d2o_pct", 0.0)),
            "additives": str(answers.get("additives", "")),
            "tube_od_mm": float(answers.get("tube_od_mm", 5.0)),
            "sample_volume_ul": float(answers.get("sample_volume_ul", 550.0)),
            "vt_setpoint_k": float(answers.get("vt_setpoint_k", 300.0)),
        },
        "environment": {
            "locked": lock_state == "on",
            "operator_notes": operator_notes,
        },
        "calibration": {
            # p90 is not separately calibrated on a stock benchtop run;
            # the macro records the small-flip reference tip instead.
            # Placeholder pulse length pending bench item 6.
            "p90_us": float(answers.get("p90_us", 10.0)),
            "p90_power_db_or_w": "n/a (Spinsolve internal amplitude units)",
            "rg_ladder": [
                {"expno": e["expno"],
                 "rg": _experiment_entry(e, answers, fallback_local)["rg"],
                 "tip_deg": float(ref_tip)}
                for e in experiments if e["role"] == "rg_ladder"
            ] or [{"expno": experiments[0]["expno"], "rg": 1.0,
                   "tip_deg": float(ref_tip)}],
            "topshim_ok": bool(answers.get("shim_ok", False)),
        },
        "experiments": [_experiment_entry(e, answers, fallback_local)
                        for e in experiments],
        "checksums": {},  # filled by pack()
    }

    if schema_version == "2.0":
        meta["vendor"] = VENDOR
        meta["instrument"] = {
            "magritek": {
                "spinsolve_software_version": sw_version,
                "model": str(answers.get("model", "")) or "unknown",
                "expert_mode": True,
                "rx_gain": noise_gain_db,
                "data_format": "prospa-1d",
            }
        }
    else:
        # 1.2 fallback: the two Bruker-specific 1.x fields carry explicit
        # n/a markers (schema types: string / boolean). See README.md.
        meta["spectrometer"]["topspin_version"] = "n/a (Magritek Spinsolve)"
        meta["environment"]["lock_sweep_confirmed_off"] = True
        meta["environment"]["operator_notes"] += (
            " NOTE: no BSMS exists on a Spinsolve; lock_sweep_confirmed_off"
            " is a schema-1.2 compatibility placeholder (vendor-specific"
            " state lives in the 2.0 instrument block).")

    audit = _clock_audit(answers, experiments)
    if audit is not None:
        meta["clock_audit"] = audit

    return meta


def _self_sha256():
    try:
        with open(os.path.abspath(__file__), "rb") as fh:
            return "sha256:" + hashlib.sha256(fh.read()).hexdigest()
    except (IOError, OSError):
        return "unavailable"


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------

def pack(session_dir, out_dir=None, schema_version="2.0"):
    session = read_session(session_dir)
    for w in session["warnings"]:
        info("WARN : %s" % w)
    meta = build_meta(session, schema_version)

    files = []
    for exp in session["experiments"]:
        files.extend(exp["files"])
    if session["answers_path"]:
        files.append(("data/answers.json", session["answers_path"]))

    for arc, path in files:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        meta["checksums"][arc] = "sha256:" + h.hexdigest()

    out_dir = out_dir or os.getcwd()
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    stamp = time.strftime("%Y%m%d_%H%M%SZ", time.gmtime())
    suffix = "%04x" % random.randint(0, 0xFFFF)
    name = "spinnoise_%s_%s_%s.zip" % (meta["facility"]["facility_slug"],
                                       stamp, suffix)
    bundle = os.path.join(out_dir, name)
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta, indent=2) + "\n")
        for arc, path in files:
            zf.write(path, arc)
    info("packed %d files, schema %s, run_mode %s"
         % (len(files), schema_version, meta["software"]["run_mode"]))
    print(bundle)
    return bundle


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Magritek Spinsolve session reader/packer "
                    "(spin-noise network, draft pending bench validation).")
    sub = parser.add_subparsers(dest="cmd")

    p_pack = sub.add_parser("pack", help="pack a session dir into a bundle")
    p_pack.add_argument("session_dir")
    p_pack.add_argument("--out-dir", default=None)
    p_pack.add_argument("--schema-version", choices=("2.0", "1.2"),
                        default="2.0")

    p_meta = sub.add_parser("meta", help="print the meta.json for a session")
    p_meta.add_argument("session_dir")
    p_meta.add_argument("--schema-version", choices=("2.0", "1.2"),
                        default="2.0")

    p_insp = sub.add_parser("inspect", help="dump a .1d header or acqu.par")
    p_insp.add_argument("path")

    args = parser.parse_args(argv)
    if args.cmd == "pack":
        pack(args.session_dir, args.out_dir, args.schema_version)
    elif args.cmd == "meta":
        session = read_session(args.session_dir)
        for w in session["warnings"]:
            info("WARN : %s" % w)
        print(json.dumps(build_meta(session, args.schema_version), indent=2))
    elif args.cmd == "inspect":
        if args.path.endswith(".1d"):
            print(json.dumps(read_1d(args.path), indent=2))
        else:
            print(json.dumps(parse_acqu_par(args.path), indent=2))
    else:
        parser.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
