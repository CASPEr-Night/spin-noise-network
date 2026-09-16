#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agilent_reader.py -- Agilent/Varian (VnmrJ / OpenVnmrJ) file readers for
the spin-noise network (converter path).

Parses the two files every VnmrJ ``.fid`` save directory contains:

    procpar   text parameter file (the full parameter tree at save time)
    fid       binary FID data (file header + per-block headers + traces)

CLI (inspection only -- packing goes through packer/pack_bundle.py
--vendor agilent, which delegates parsing to this module):

    python3 vendors/agilent/agilent_reader.py inspect <dir.fid | fid | procpar>
    python3 vendors/agilent/agilent_reader.py inspect <path> --json

STATUS / HONESTY:
  * The binary ``fid`` layout and the ``procpar`` text format implemented
    here follow nmrglue's Varian/Agilent reader (nmrglue/fileio/varian.py,
    BSD-3-Clause, https://github.com/jjhelmus/nmrglue) -- logic
    re-implemented, not copied; nmrglue is the citation for the format
    facts (its own sources: "Varian MR News 2005-04-18" and the Agilent
    "VnmrJ User Programming" manual):
      - fid: 32-byte big-endian file header, struct '>6ihhi' =
        [nblocks, ntraces, np, ebytes, tbytes, bbytes, vers_id, status,
        nbheaders]; then nblocks blocks, each nbheaders x 28-byte block
        headers (struct '>4hi4f' = [scale, status, index, mode, ctcount,
        lpval, rpval, lvl, tlt]) followed by ntraces traces of np points
        of ebytes bytes each. All big-endian. The file-header status
        bits select the element type: S_FLT (0x8) -> float32, else
        S_32 (0x4) -> int32, else int16.
      - procpar: one record per parameter; first line has 11
        whitespace-separated fields [name, subtype, basictype, maxvalue,
        minvalue, stepsize, Ggroup, Dgroup, protection, active, intptr];
        then the values line(s): count followed by the values --
        numeric values (basictype 1) all on the count line, string
        values (basictype 2) double-quoted, the first on the count line
        and each further value on its own line; then one enumerable
        line (a count and the allowed values), which this reader skips.
  * VERIFIED against real VnmrJ 3.2 / DD2 output (SIU Carbondale,
    2026-09-14, checklist item 10): two sessions, 134 .fid directories
    (no-pulse noise blocks, references, gain-ladder steps), 526 procpar
    records each (parse_procpar returns all 526, nothing under
    _unparsed), zero reader warnings; tbytes = np*ebytes, bbytes =
    tbytes+28, file = 32+28+data, status 201 -> float32 (ebytes 4).
    The checks below stay STRUCTURAL (header arithmetic vs file size)
    rather than asserting magic values, so an unexpected variant
    surfaces as a warning instead of a silent misparse.
  * procpar wall-clock stamps (confirmed on the same files): time_run,
    time_complete, time_saved (also time_submitted, time_svfdate) are
    strings "YYYYMMDDTHHMMSS" in the CONSOLE's local time -- no zone,
    and only as trustworthy as the console clock/NTP (checklist item
    6); vnmrj_time_iso() converts them.  The VnmrJ version is machine-
    readable from /vnmr/vnmrrev (checklist item 8): parse_vnmrrev().
  * Still UNVERIFIED (marked UNVERIFIED(n) against the checklist in
    vendors/agilent/README.md): items 2 (tof/sfrq conventions) and 3
    (transmitter silence at pw=0); item 1 partially (legal gains are
    integer dB; the amplitude transfer curve is not fitted yet).

Python 3 standard library only, nothing newer than 3.6 (same
portability rules as the uploader and packer).

Maintainer: John W. Blanchard, jwbquantum@gmail.com
Co-developed with Claude (Anthropic).
"""

from __future__ import print_function

import argparse
import json
import os
import re
import struct
import sys
import time

READER_VERSION = "0.2.0"

_VNMRJ_TIME_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$")
_VNMRREV_RE = re.compile(r"^\s*(?:Open)?VnmrJ\s+VERSION\s+(\S+)\s+REVISION\s+"
                         r"(\S+)", re.IGNORECASE)

# fid file-header status bits (names and values per nmrglue varian.py)
S_DATA = 0x1
S_SPEC = 0x2
S_32 = 0x4
S_FLT = 0x8
S_COMPLEX = 0x10
S_HYPERCOMPLEX = 0x20

FILE_HEADER_FIELDS = ("nblocks", "ntraces", "np", "ebytes", "tbytes",
                      "bbytes", "vers_id", "status", "nbheaders")
BLOCK_HEADER_FIELDS = ("scale", "status", "index", "mode", "ctcount",
                       "lpval", "rpval", "lvl", "tlt")

BLOCK_HEADER_BYTES = 28
FILE_HEADER_BYTES = 32


class AgilentReadError(Exception):
    """Unreadable/implausible Agilent file, with an operator message."""


# ---------------------------------------------------------------------------
# procpar
# ---------------------------------------------------------------------------

def _unquote(token):
    token = token.strip()
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    return token


def parse_procpar(path):
    """Parse a VnmrJ procpar file into {name: value}.

    Values: float for basictype 1, str for basictype 2; a parameter with
    more than one value (an arrayed parameter) becomes a list. Records
    that do not match the documented shape are collected verbatim under
    the key ``_unparsed`` instead of aborting -- the packer treats
    everything here as discovered-if-possible.
    """
    try:
        with open(path, "r", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        raise AgilentReadError("cannot read %s: %s" % (path, exc))

    out = {}
    unparsed = []
    i = 0
    n = len(lines)
    while i < n:
        header = lines[i].split()
        i += 1
        if len(header) < 3:
            if lines[i - 1].strip():
                unparsed.append(lines[i - 1])
            continue
        name = header[0]
        basictype = header[2]
        if i >= n:
            unparsed.append(lines[i - 1])
            break
        value_line = lines[i].split(None, 1)
        i += 1
        try:
            count = int(value_line[0])
        except (ValueError, IndexError):
            unparsed.append(name + ": " + " ".join(value_line))
            continue
        rest = value_line[1] if len(value_line) > 1 else ""
        values = []
        if basictype == "2":
            # string values: first on the count line, further values one
            # per following line, each double-quoted
            if count >= 1:
                values.append(_unquote(rest))
            for _k in range(count - 1):
                if i < n:
                    values.append(_unquote(lines[i]))
                    i += 1
        else:
            # numeric values: all on the count line
            for tok in rest.split():
                try:
                    values.append(float(tok))
                except ValueError:
                    values.append(tok)
            while len(values) < count and i < n and basictype == "1":
                # defensive: some writers may wrap long arrays (not in
                # nmrglue's description, not seen in the real DD2 files
                # of 2026-09-14 either; kept as a guard)
                extra = lines[i].split()
                probe = []
                ok = True
                for tok in extra:
                    try:
                        probe.append(float(tok))
                    except ValueError:
                        ok = False
                        break
                if not ok or not extra:
                    break
                values.extend(probe)
                i += 1
        # the enumerable line (count + allowed values); skip it
        if i < n:
            enum_fields = lines[i].split()
            if enum_fields:
                try:
                    int(enum_fields[0])
                    i += 1
                except ValueError:
                    pass
        if not values:
            out[name] = None
        elif count == 1 or len(values) == 1:
            out[name] = values[0]
        else:
            out[name] = values
    if unparsed:
        out["_unparsed"] = unparsed
    return out


def scalar(params, name):
    """First value of a possibly-arrayed procpar parameter (or None)."""
    v = params.get(name)
    if isinstance(v, list):
        return v[0] if v else None
    return v


def vnmrj_time_iso(value):
    """A procpar wall-clock stamp ("YYYYMMDDTHHMMSS", console-local, as
    VnmrJ 3.2 writes time_run/time_complete/time_saved) as ISO 8601
    "YYYY-MM-DDTHH:MM:SS" without zone; None for anything that is not a
    well-formed calendar stamp (the empty time_exp/time_processed
    strings included) -- the caller must then leave the field alone."""
    if not isinstance(value, str):
        return None
    m = _VNMRJ_TIME_RE.match(value.strip())
    if not m or int(m.group(6)) > 59:   # strptime admits leap seconds 60/61
        return None
    iso = "%s-%s-%sT%s:%s:%s" % m.groups()
    try:
        time.strptime(iso, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return iso


def parse_vnmrrev(path):
    """Parse a copy of the console's /vnmr/vnmrrev (three lines on VnmrJ
    3.2: "VnmrJ VERSION 3.2 REVISION A" / "September 21, 2011" /
    "vnmrsdd2").  Returns {"version", "revision", "vnmrj_version",
    "date", "system"} with vnmrj_version = "<ver> Revision <rev>", or
    None when the first line does not carry the VERSION/REVISION pair."""
    try:
        # utf-8-sig: a copy saved through Windows Notepad carries a BOM
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            lines = [ln.strip() for ln in fh.read().splitlines()]
    except OSError as exc:
        raise AgilentReadError("cannot read %s: %s" % (path, exc))
    lines = [ln for ln in lines if ln]
    if not lines:
        return None
    m = _VNMRREV_RE.match(lines[0])
    if not m:
        return None
    version, revision = m.group(1), m.group(2)
    return {
        "version": version,
        "revision": revision,
        "vnmrj_version": "%s Revision %s" % (version, revision),
        "date": lines[1] if len(lines) > 1 else "",
        "system": lines[2] if len(lines) > 2 else "",
    }


# ---------------------------------------------------------------------------
# fid
# ---------------------------------------------------------------------------

def read_fid(path):
    """Read a Varian/Agilent ``fid`` file's headers; structural checks only.

    Returns a dict with the parsed file header, the first block header,
    the inferred element dtype, and ``structure_ok`` -- True iff
    tbytes == np*ebytes, bbytes == nbheaders*28 + ntraces*tbytes, and
    file size == 32 + nblocks*bbytes (the arithmetic the nmrglue-
    documented layout implies). Nothing is asserted about vers_id or the
    individual status flags beyond the dtype selection.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            raw = fh.read(FILE_HEADER_BYTES + BLOCK_HEADER_BYTES)
    except OSError as exc:
        raise AgilentReadError("cannot read %s: %s" % (path, exc))
    if len(raw) < FILE_HEADER_BYTES:
        raise AgilentReadError("%s: shorter than the 32-byte fid file "
                               "header" % path)
    fh_values = struct.unpack(">6ihhi", raw[:FILE_HEADER_BYTES])
    header = dict(zip(FILE_HEADER_FIELDS, fh_values))

    status = header["status"]
    if status & S_FLT:
        dtype = "float32"
    elif status & S_32:
        dtype = "int32"
    else:
        dtype = "int16"

    block_header = None
    if len(raw) >= FILE_HEADER_BYTES + BLOCK_HEADER_BYTES \
            and header["nbheaders"] >= 1 and header["nblocks"] >= 1:
        bh_values = struct.unpack(
            ">4hi4f",
            raw[FILE_HEADER_BYTES:FILE_HEADER_BYTES + BLOCK_HEADER_BYTES])
        block_header = dict(zip(BLOCK_HEADER_FIELDS, bh_values))

    ok = (header["np"] > 0 and header["ebytes"] in (2, 4)
          and header["tbytes"] == header["np"] * header["ebytes"]
          and header["bbytes"] == (header["nbheaders"] * BLOCK_HEADER_BYTES
                                   + header["ntraces"] * header["tbytes"])
          and size == FILE_HEADER_BYTES
          + header["nblocks"] * header["bbytes"])

    return {
        "path": path,
        "file_bytes": size,
        "header": header,
        "first_block_header": block_header,
        "dtype": dtype,
        "structure_ok": bool(ok),
        # np is TOTAL points per trace (real+imag interleaved), the same
        # counting convention as Bruker TD -- see the at = np/(2*sw)
        # relation in the README's format-facts section.
        "np_total_points": header["np"] if ok else None,
        "nblocks": header["nblocks"],
    }


# ---------------------------------------------------------------------------
# one experiment directory (<name>.fid/ with procpar + fid)
# ---------------------------------------------------------------------------

def read_experiment_dir(dirpath):
    """Read one VnmrJ save directory (procpar + fid).

    Returns {"procpar": dict-or-{}, "fid": read_fid-result-or-None,
    "files": [(relname, abspath)], "warnings": [str]}.
    """
    warnings = []
    procpar_path = os.path.join(dirpath, "procpar")
    fid_path = os.path.join(dirpath, "fid")
    procpar = {}
    fid = None
    files = []
    if os.path.isfile(procpar_path):
        procpar = parse_procpar(procpar_path)
        files.append(("procpar", procpar_path))
    else:
        warnings.append("%s: no procpar" % dirpath)
    if os.path.isfile(fid_path):
        fid = read_fid(fid_path)
        if not fid["structure_ok"]:
            warnings.append("%s: fid header arithmetic does not match the "
                            "file size (unexpected format variant?) -- "
                            "pack verbatim, verify at the partner session"
                            % fid_path)
        files.append(("fid", fid_path))
    else:
        warnings.append("%s: no fid" % dirpath)
    # text and log are part of every svf() save; pack them if present
    for extra in ("text", "log"):
        p = os.path.join(dirpath, extra)
        if os.path.isfile(p):
            files.append((extra, p))
    return {"procpar": procpar, "fid": fid, "files": files,
            "warnings": warnings}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Agilent/Varian fid + procpar inspector (spin-noise "
                    "network; packing goes through packer/pack_bundle.py "
                    "--vendor agilent).")
    sub = parser.add_subparsers(dest="cmd")
    p_insp = sub.add_parser("inspect",
                            help="dump a fid header, a procpar, or a whole "
                                 ".fid directory")
    p_insp.add_argument("path")
    p_insp.add_argument("--json", action="store_true",
                        help="machine-readable output")
    args = parser.parse_args(argv)

    if args.cmd != "inspect":
        parser.print_help()
        return 2

    path = args.path
    if os.path.isdir(path):
        result = read_experiment_dir(path)
        result = {"procpar_keys": sorted(k for k in result["procpar"]
                                         if not k.startswith("_")),
                  "np": scalar(result["procpar"], "np"),
                  "sw": scalar(result["procpar"], "sw"),
                  "at": scalar(result["procpar"], "at"),
                  "sfrq": scalar(result["procpar"], "sfrq"),
                  "gain": scalar(result["procpar"], "gain"),
                  "nt": scalar(result["procpar"], "nt"),
                  "tn": scalar(result["procpar"], "tn"),
                  "seqfil": scalar(result["procpar"], "seqfil"),
                  "time_run": scalar(result["procpar"], "time_run"),
                  "time_complete": scalar(result["procpar"],
                                          "time_complete"),
                  "started_local": vnmrj_time_iso(
                      scalar(result["procpar"], "time_run")),
                  "fid": result["fid"],
                  "warnings": result["warnings"]}
    elif os.path.basename(path) == "procpar" or path.endswith(".par"):
        result = parse_procpar(path)
    else:
        result = read_fid(path)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
