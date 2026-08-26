#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jeol_reader.py -- read JEOL Delta .jdf files and JCAMP-DX exports for the
spin-noise network bundle packer.

STATUS: DRAFT PENDING HARDWARE VALIDATION (no JEOL spectrometer has run the
network protocol yet). The JCAMP-DX path is the robust ingestion route: it is
a published, openly documented text format. The native .jdf path is a port of
an open-source parser plus direct empirical verification against real Delta
files; everything that could NOT be verified is marked UNVERIFIED inline and
collected in vendors/jeol/README.md ("Partner validation checklist").

Format provenance for the native .jdf reader
--------------------------------------------
The .jdf binary layout has no public specification from JEOL. The layout
implemented here is ported from the MIT-licensed open-source parser

    cheminfo/jeolconverter v1.0.1, src/parseJEOL.js and
    src/conversionTables.js (Julien Wist and contributors, MIT license,
    https://www.npmjs.com/package/jeolconverter)

with the following facts INDEPENDENTLY VERIFIED during development against
38 real Delta .jdf files (1D and 2D, JNM-ECA/ECZ-era, Delta 5.x-6.x) from
the openly published test corpus `jeol-data-test` (npm, cheminfo):

  VERIFIED: magic bytes b"JEOL.NMR" at offset 0 (38/38 files);
  VERIFIED: endian flag at offset 8 (0=big, 1=little) governing the
            parameter and data sections while the header block itself stays
            big-endian;
  VERIFIED: the fixed 1360-byte header layout used below (param_start
            pointer at offset 1212 and data_start pointer at offset 1284
            resolve to a well-formed parameter section and readable data
            in every file tried);
  VERIFIED: 64-byte parameter records (4 unknown + int16 scaler + 10 unit
            + 16 value + int32 value_type + 28 name, space-padded names);
  VERIFIED: parameter names including x_domain, x_freq, x_offset, x_sweep,
            x_points, x_acq_time, scans, temp_get, field_strength, x90,
            relaxation_delay, recvr_gain, recvr_gain_limit,
            actual_start_time, end_time;
  VERIFIED: creation/revision date bit-packing (7-bit year since 1990,
            4-bit month, 5-bit day from the MSB) -- decoded dates match
            dates embedded in the corpus filenames (e.g. 20190228,
            20190328) and in one file's operator-typed title;
  VERIFIED (day-level): actual_start_time / end_time are seconds since
            1990-01-01; one corpus file decodes to the calendar day its
            title records.

  UNVERIFIED: the timezone of actual_start_time / end_time (local vs UTC)
            and their sub-day accuracy; the meaning of header bytes 2-3 of
            the packed date words (possibly time-of-day); receiver-gain
            (recvr_gain) UNITS and LINEARITY semantics; behavior on
            big-endian .jdf files (none in the corpus); 3D+ data formats
            (not implemented); non-float data types (tables reserve them).

JCAMP-DX provenance
-------------------
The JCAMP-DX reader implements the published standard:
  R. S. McDonald, P. A. Wilks, "JCAMP-DX: A Standard Form for Exchange of
  Infrared Spectra in Computer Readable Form", Appl. Spectrosc. 42, 151
  (1988) -- core syntax, AFFN and ASDF (SQZ/DIF/DUP) data forms;
  A. N. Davies, P. Lampen, "JCAMP-DX for NMR", Appl. Spectrosc. 47, 1093
  (1993) -- NMR FID NTUPLES layout (R/I pages, .OBSERVE FREQUENCY etc.).
Delta exports JCAMP-DX via File > Save As (JEOL "Delta Tips" application
notes). UNVERIFIED: the exact flavor Delta writes (AFFN vs DIFDUP, NTUPLES
vs paired blocks) -- the reader accepts the standard forms of both, and the
partner session must confirm a real Delta export parses cleanly.

Portability: Python 3 standard library only, nothing newer than 3.6
(same rules as uploader/upload_bundle.py).

Contact: John W. Blanchard <jwbquantum@gmail.com>
"""

from __future__ import print_function

import json
import os
import re
import struct
import sys

READER_VERSION = "0.1.0"

JDF_MAGIC = b"JEOL.NMR"
JDF_HEADER_SIZE = 1360          # VERIFIED: param sections start at 1360 in
                                # freshly written Delta files (header size)

# ---------------------------------------------------------------------------
# Conversion tables, ported from cheminfo/jeolconverter (MIT)
# src/conversionTables.js -- values are JEOL enum codes.
# ---------------------------------------------------------------------------

INSTRUMENT_TABLE = {
    0: "NONE", 1: "GSX", 2: "ALPHA", 3: "ECLIPSE", 4: "MASS_SPEC",
    5: "COMPILER", 6: "OTHER_NMR", 7: "UNKNOWN", 8: "GEMINI", 9: "UNITY",
    10: "ASPECT", 11: "UX", 12: "FELIX", 13: "LAMBDA", 14: "GE_1280",
    15: "GE_OMEGA", 16: "CHEMAGNETICS", 17: "CDFF", 18: "GALACTIC",
    19: "TRIAD", 20: "GENERIC_NMR", 21: "GAMMA", 22: "JCAMP_DX", 23: "AMX",
    24: "DMX", 25: "ECA", 26: "ALICE", 27: "NMR_PIPE", 28: "SIMPSON",
}

DATA_TYPE_TABLE = {0: "float64", 1: "float32", 2: "reserved", 3: "reserved"}

DATA_FORMAT_TABLE = {
    1: "One_D", 2: "Two_D", 3: "Three_D", 4: "Four_D", 5: "Five_D",
    6: "Six_D", 7: "Seven_D", 8: "Eight_D", 12: "Small_Two_D",
    13: "Small_Three_D", 14: "Small_Four_D",
}

AXIS_TYPE_TABLE = {
    0: "None", 1: "Real", 2: "TPPI", 3: "Complex", 4: "Real_Complex",
    5: "Envelope",
}

VALUE_TYPE_TABLE = {0: "String", 1: "Integer", 2: "Float", 3: "Complex",
                    4: "Infinity"}

# Unit prefix code -> power of ten (ported table; codes are signed nibbles).
PREFIX_POWER = {
    -8: 24, -7: 21, -6: 18, -5: 15, -4: 12, -3: 9, -2: 6, -1: 3,
    0: 0, 1: -3, 2: -6, 3: -9, 4: -12, 5: -15, 6: -18, 7: -21,
}

UNIT_BASE_TABLE = {
    0: "None", 1: "Abundance", 2: "Ampere", 3: "Candela", 4: "Celsius",
    5: "Coulomb", 6: "Degree", 7: "Electronvolt", 8: "Farad", 9: "Sievert",
    10: "Gram", 11: "Gray", 12: "Henry", 13: "Hertz", 14: "Kelvin",
    15: "Joule", 16: "Liter", 17: "Lumen", 18: "Lux", 19: "Meter",
    20: "Mole", 21: "Newton", 22: "Ohm", 23: "Pascal", 24: "Percent",
    25: "Point", 26: "Ppm", 27: "Radian", 28: "Second", 29: "Siemens",
    30: "Steradian", 31: "Tesla", 32: "Volt", 33: "Watt", 34: "Weber",
    35: "Decibel", 36: "Dalton", 37: "Thompson", 38: "Ugeneric",
    39: "LPercent", 40: "PPT", 41: "PPB", 42: "Index",
}


class JeolReadError(Exception):
    """Raised when a file cannot be parsed as .jdf or JCAMP-DX."""


# ---------------------------------------------------------------------------
# .jdf native reader
# ---------------------------------------------------------------------------

def _cstring(raw):
    """Decode a NUL/space-padded fixed-width string field."""
    return raw.split(b"\x00")[0].decode("ascii", "replace").strip()


def _param_name(raw):
    """Decode a space-padded parameter-name field.

    Note: the reference JS parser strips ALL spaces; we strip only leading/
    trailing padding so that interior spaces in string VALUES survive
    (verified example value: '5mm Broadband Gr').
    """
    return raw.decode("ascii", "replace").strip().lower()


def _string_value(raw):
    return raw.decode("ascii", "replace").strip()


def _packed_date(word_be):
    """Decode the 4-byte packed date (offsets 400/404).

    Layout from the MSB: 7-bit (year-1990), 4-bit month, 5-bit day.
    VERIFIED against filename-embedded dates in the public corpus.
    The remaining 16 bits are UNVERIFIED (possibly time-of-day) and are
    returned raw for the partner session to characterize.
    """
    val = struct.unpack(">I", word_be)[0]
    return {
        "year": 1990 + (val >> 25),
        "month": (val >> 21) & 0xF,
        "day": (val >> 16) & 0x1F,
        "unparsed_low16": val & 0xFFFF,  # UNVERIFIED content
    }


def _epoch1990_iso(seconds):
    """Convert a JEOL 1990-epoch timestamp to an ISO-like string.

    VERIFIED at day-level against one corpus file; the TIMEZONE of the
    stored value is UNVERIFIED (see module docstring), so the string is
    labeled 'zone-unknown' rather than given a Z/offset suffix.
    """
    if seconds is None:
        return None
    try:
        sec = float(seconds)
    except (TypeError, ValueError):
        return None
    # days since 1990-01-01 -> civil date (proleptic Gregorian, stdlib-free
    # of local-timezone contamination on purpose)
    days = int(sec // 86400)
    rem = sec - days * 86400
    # 1990-01-01 is day 726468 of the proleptic Gregorian calendar
    ordinal = 726468 + days
    import datetime
    d = datetime.date.fromordinal(ordinal)
    hh = int(rem // 3600)
    mm = int((rem % 3600) // 60)
    ss = rem % 60
    return "%04d-%02d-%02dT%02d:%02d:%06.3f (zone-unknown)" % (
        d.year, d.month, d.day, hh, mm, ss)


def _read_units(buf, off, count):
    """Read `count` unit descriptors of 2 bytes each: prefix/power byte +
    signed base code. Ported from jeolconverter utils.getUnit."""
    units = []
    for i in range(count):
        b0 = buf[off + 2 * i]
        base = struct.unpack("b", buf[off + 2 * i + 1:off + 2 * i + 2])[0]
        prefix_code = b0 >> 4
        if prefix_code > 7:            # signed nibble
            prefix_code -= 16
        units.append({
            "prefix_power10": PREFIX_POWER.get(prefix_code, 0),
            "power": b0 & 0x0F,
            "base": UNIT_BASE_TABLE.get(base, "code_%d" % base),
        })
    return units


def read_jdf_header(buf):
    """Parse the fixed 1360-byte .jdf header (big-endian block)."""
    if len(buf) < JDF_HEADER_SIZE:
        raise JeolReadError("file shorter than the 1360-byte JDF header")
    if buf[0:8] != JDF_MAGIC:
        raise JeolReadError("bad magic %r (expected %r)" % (buf[0:8], JDF_MAGIC))

    h = {}
    h["endian"] = "little" if buf[8] == 1 else "big"
    h["major_version"] = buf[9]
    h["minor_version"] = struct.unpack(">H", buf[10:12])[0]
    h["ndim"] = buf[12]
    h["dim_exist_bits"] = buf[13]
    h["data_type"] = DATA_TYPE_TABLE.get(buf[14] >> 6, "unknown")
    h["data_format"] = DATA_FORMAT_TABLE.get(buf[14] & 0x3F, "unknown")
    instr = struct.unpack("b", buf[15:16])[0]
    h["instrument"] = INSTRUMENT_TABLE.get(instr, "code_%d" % instr)
    h["translate"] = list(buf[16:24])
    h["axis_type"] = [AXIS_TYPE_TABLE.get(x, "code_%d" % x) for x in buf[24:32]]
    h["data_units"] = _read_units(buf, 32, 8)
    h["title"] = _cstring(buf[48:172])
    # axis-ranged nibbles at 172..176 (unused downstream; kept raw)
    h["axis_ranged_raw"] = list(buf[172:176])
    h["data_points"] = list(struct.unpack(">8I", buf[176:208]))
    h["data_offset_start"] = list(struct.unpack(">8I", buf[208:240]))
    h["data_offset_stop"] = list(struct.unpack(">8I", buf[240:272]))
    h["axis_start"] = list(struct.unpack(">8d", buf[272:336]))
    h["axis_stop"] = list(struct.unpack(">8d", buf[336:400]))
    h["creation_date"] = _packed_date(buf[400:404])
    h["revision_date"] = _packed_date(buf[404:408])
    h["node_name"] = _cstring(buf[408:424])
    h["site"] = _cstring(buf[424:552])
    h["author"] = _cstring(buf[552:680])
    h["comment"] = _cstring(buf[680:808])
    h["axis_titles"] = [_cstring(buf[808 + 32 * i:808 + 32 * (i + 1)])
                        for i in range(8)]
    h["base_freq_mhz"] = list(struct.unpack(">8d", buf[1064:1128]))
    h["zero_point"] = list(struct.unpack(">8d", buf[1128:1192]))
    h["reversed"] = [bool(x) for x in buf[1192:1200]]
    # 3 skip bytes + annotation flag at 1203
    h["param_start"] = struct.unpack(">I", buf[1212:1216])[0]
    h["param_length"] = struct.unpack(">I", buf[1216:1220])[0]
    h["list_start"] = list(struct.unpack(">8I", buf[1220:1252]))
    h["list_length"] = list(struct.unpack(">8I", buf[1252:1284]))
    h["data_start"] = struct.unpack(">I", buf[1284:1288])[0]
    h["data_length"] = struct.unpack(">Q", buf[1288:1296])[0]
    return h


def read_jdf_params(buf, header):
    """Parse the parameter section: 16-byte section header + 64-byte records."""
    ps = header["param_start"]
    endian = "<" if header["endian"] == "little" else ">"
    if ps + 16 > len(buf):
        raise JeolReadError("parameter section start beyond end of file")
    psize, lo, hi, total = struct.unpack(endian + "IIII", buf[ps:ps + 16])
    if psize != 64:
        # Every corpus file uses 64-byte records; anything else is a format
        # we have never seen.
        raise JeolReadError("unexpected parameter record size %d" % psize)
    params = {}
    order = []
    off = ps + 16
    for _ in range(hi + 1):
        rec = buf[off:off + 64]
        if len(rec) < 64:
            raise JeolReadError("truncated parameter record")
        off += 64
        scaler = struct.unpack(endian + "h", rec[4:6])[0]
        unit = _read_units(rec, 6, 5)
        vtype_code = struct.unpack(endian + "i", rec[32:36])[0]
        vtype = VALUE_TYPE_TABLE.get(vtype_code, "unknown")
        if vtype == "String":
            value = _string_value(rec[16:32])
        elif vtype == "Integer":
            value = struct.unpack(endian + "i", rec[16:20])[0]
        elif vtype == "Float":
            value = struct.unpack(endian + "d", rec[16:24])[0]
        elif vtype == "Complex":
            re_, im_ = struct.unpack(endian + "dd", rec[16:32])
            value = {"real": re_, "imag": im_}
        elif vtype == "Infinity":
            value = struct.unpack(endian + "i", rec[16:20])[0]
        else:
            value = None
        name = _param_name(rec[36:64])
        params[name] = {"value": value, "unit": unit, "scaler": scaler,
                        "value_type": vtype}
        order.append(name)
    return params, order


def _unpack_floats(buf, off, n, endian, dtype):
    if dtype == "float64":
        return list(struct.unpack(endian + "%dd" % n, buf[off:off + 8 * n])), 8 * n
    if dtype == "float32":
        return list(struct.unpack(endian + "%df" % n, buf[off:off + 4 * n])), 4 * n
    raise JeolReadError("unsupported data type %r" % dtype)


def read_jdf_data(buf, header):
    """Read the data section. 1D fully supported; 2D de-tiled from the
    32x32 submatrix layout (ported from parseJEOL.js; the tile size 32 is
    the reference parser's constant)."""
    endian = "<" if header["endian"] == "little" else ">"
    dtype = header["data_type"]
    off = header["data_start"]

    # number of stored sections from the axis types (port of parseJEOL.js)
    sections = 1
    seen_real_complex = False
    for t in header["axis_type"]:
        if t == "Real_Complex" and not seen_real_complex:
            sections += 1
            seen_real_complex = True
        if t == "Complex":
            sections *= 2

    fmt = header["data_format"]
    data = {}
    if fmt == "One_D":
        n = header["data_points"][0]
        for s in range(sections):
            vals, used = _unpack_floats(buf, off, n, endian, dtype)
            off += used
            if s == 0:
                data["re"] = vals
            elif s == 1:
                data["im"] = vals
        return data

    if fmt == "Two_D":
        tile = 32
        dim1 = header["data_points"][0]
        dim2 = header["data_points"][1]
        if dim1 % tile or dim2 % tile:
            raise JeolReadError("2D dims not multiples of the 32-point tile")
        n_tile = tile * tile
        keys = {2: ["re", "im"], 4: ["re_re", "re_im", "im_re", "im_im"]}
        names = keys.get(sections, ["s%d" % i for i in range(sections)])
        for s in range(sections):
            rows = [[0.0] * dim1 for _ in range(dim2)]
            for ti in range(dim2 // tile):        # tile rows
                for tj in range(dim1 // tile):    # tile cols
                    vals, used = _unpack_floats(buf, off, n_tile, endian, dtype)
                    off += used
                    for k in range(tile):
                        row = rows[ti * tile + k]
                        row[tj * tile:(tj + 1) * tile] = vals[k * tile:(k + 1) * tile]
            data[names[s]] = rows
        return data

    raise JeolReadError("data format %r not implemented (1D/2D only)" % fmt)


def _pval(params, name, default=None):
    p = params.get(name)
    return p["value"] if p is not None else default


def _pmag(params, name):
    """Value scaled by its unit prefix (port of utils.getMagnitude)."""
    p = params.get(name)
    if p is None or not isinstance(p["value"], (int, float)):
        return None
    power = p["unit"][0]["prefix_power10"] if p["unit"] else 0
    return p["value"] * (10 ** power)


def read_jdf(path_or_bytes):
    """Read a .jdf file (path or raw bytes). Returns
    {header, params, param_order, data, info} where info is the digest the
    bundle packer consumes."""
    if isinstance(path_or_bytes, bytes):
        buf = path_or_bytes
        src = "<bytes>"
    else:
        src = path_or_bytes
        with open(path_or_bytes, "rb") as fh:
            buf = fh.read()

    header = read_jdf_header(buf)
    params, order = read_jdf_params(buf, header)
    data = read_jdf_data(buf, header)

    info = {
        "source_file": os.path.basename(src) if src != "<bytes>" else src,
        "format": "jdf",
        "title": header["title"],
        "author": header["author"],
        "site": header["site"],
        "comment": header["comment"],
        "instrument": header["instrument"],
        "ndim": header["ndim"],
        "data_format": header["data_format"],
        "data_sections": sorted(data.keys()),
        "points": header["data_points"][:max(header["ndim"], 1)],
        "creation_date": header["creation_date"],
        "nucleus": _pval(params, "x_domain"),
        "experiment": _pval(params, "experiment"),
        "sample_id": _pval(params, "sample_id"),
        "solvent": _pval(params, "solvent"),
        "spectrometer_freq_hz": _pmag(params, "x_freq"),
        "sweep_hz": _pmag(params, "x_sweep"),
        "sweep_clipped_hz": _pmag(params, "x_sweep_clipped"),
        "offset": _pval(params, "x_offset"),  # unit context in params
        "offset_unit": (params["x_offset"]["unit"][0]["base"]
                        if params.get("x_offset", {}).get("unit") else None),
        # 'version' parameter: VERIFIED present in real files with a value
        # like '5.3.2 [Windows -' (16-char format truncation); whether it
        # is authoritative for the Delta release across versions is
        # UNVERIFIED (partner checklist item 6).
        "delta_version_guess": _pval(params, "version"),
        "acq_time_s": _pmag(params, "x_acq_time"),
        "x_points": _pval(params, "x_points"),
        "scans": _pval(params, "scans"),
        "total_scans": _pval(params, "total_scans"),
        "relaxation_delay_s": _pmag(params, "relaxation_delay"),
        "x90_us": _pval(params, "x90"),
        "temp_get": _pval(params, "temp_get"),
        "temp_set": _pval(params, "temp_set"),
        "field_strength_t": _pmag(params, "field_strength"),
        "probe_id": _pval(params, "probe_id"),
        # Receiver gain: parameter NAME verified against real files; the
        # UNITS and LINEARITY semantics are UNVERIFIED (partner checklist
        # item 1) -- record verbatim, do not convert.
        "recvr_gain_raw": _pval(params, "recvr_gain"),
        "recvr_gain_limit_raw": _pval(params, "recvr_gain_limit"),
        # 1990-epoch timestamps, day-level verified, timezone UNVERIFIED
        # (partner checklist item 3).
        "actual_start_time_raw": _pval(params, "actual_start_time"),
        "end_time_raw": _pval(params, "end_time"),
        "actual_start_time_iso": _epoch1990_iso(_pval(params, "actual_start_time")),
        "end_time_iso": _epoch1990_iso(_pval(params, "end_time")),
    }
    return {"header": header, "params": params, "param_order": order,
            "data": data, "info": info}


# ---------------------------------------------------------------------------
# JCAMP-DX reader (published standard; the robust fallback path)
# ---------------------------------------------------------------------------

# ASDF pseudo-digit tables (JCAMP-DX standard, McDonald & Wilks 1988).
_SQZ = {}
for i, c in enumerate("@ABCDEFGHI"):
    _SQZ[c] = i
for i, c in enumerate("abcdefghi"):
    _SQZ[c] = -(i + 1)
_DIF = {}
for i, c in enumerate("%JKLMNOPQR"):
    _DIF[c] = i
for i, c in enumerate("jklmnopqr"):
    _DIF[c] = -(i + 1)
_DUP = {}
for i, c in enumerate("STUVWXYZ"):
    _DUP[c] = i + 1
_DUP["s"] = 9

_TOKEN_RE = re.compile(r"([@A-Ia-i%J-Rj-rS-Zs]|[+-])?([0-9.]+)|(\?)")


def _asdf_tokens(line):
    """Split an ASDF/AFFN data line into tokens. A new token starts at a
    sign, a pseudo-digit (SQZ/DIF/DUP), or '?'; whitespace and commas also
    separate. An E-notation exponent sign does not start a new token."""
    tokens = []
    cur = ""
    for ch in line:
        if ch in " \t,;":
            if cur:
                tokens.append(cur)
                cur = ""
        elif ch in "+-" and cur and cur[-1] in "Ee" and cur[0] not in _DUP:
            cur += ch                     # exponent sign inside an AFFN number
        elif ch in "+-" or ch in _SQZ or ch in _DIF or ch in _DUP or ch == "?":
            if cur:
                tokens.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        tokens.append(cur)
    return tokens


def _asdf_tokval(tok):
    """Classify one token -> (mode, value); mode in affn/sqz/dif/dup/missing.
    The pseudo-digit replaces the leading digit of the value (JCAMP-DX
    standard, McDonald & Wilks 1988, ASDF forms)."""
    head = tok[0]
    rest = tok[1:]
    if head == "?":
        return ("missing", None)
    if head in _SQZ:
        base, mode = _SQZ[head], "sqz"
    elif head in _DIF:
        base, mode = _DIF[head], "dif"
    elif head in _DUP:
        base, mode = _DUP[head], "dup"
    else:
        return ("affn", float(tok))
    if rest:
        mag = float(str(abs(base)) + rest)
        val = -mag if base < 0 else mag
    else:
        val = float(base)
    return (mode, val)


def _asdf_line(line, prev_y):
    """Decode one ASDF/AFFN data line: leading AFFN X value, then Y items.
    Returns (x, ys, last_was_dif). prev_y is the running ordinate carried
    across lines (needed when a line opens in DIF mode)."""
    tokens = _asdf_tokens(line)
    if not tokens:
        return None, [], False

    x = None
    ys = []
    y = prev_y
    last_kind = None       # 'val' or 'dif' -- what a DUP would repeat
    last_dif = 0.0
    for t, tok in enumerate(tokens):
        mode, val = _asdf_tokval(tok)
        if t == 0:
            if mode != "affn":
                raise JeolReadError(
                    "JCAMP data line must start with an AFFN X value")
            x = val
            continue
        if mode == "missing":
            ys.append(None)
            y = None
            last_kind = "val"
        elif mode in ("affn", "sqz"):
            y = val
            ys.append(y)
            last_kind = "val"
        elif mode == "dif":
            if y is None:
                raise JeolReadError("DIF value with no previous ordinate")
            last_dif = val
            y = y + val
            ys.append(y)
            last_kind = "dif"
        elif mode == "dup":
            if last_kind is None or y is None:
                raise JeolReadError("DUP with nothing to duplicate")
            count = int(val)
            for _ in range(count - 1):   # total occurrences = count
                if last_kind == "dif":
                    y = y + last_dif
                ys.append(y)
    return x, ys, last_kind == "dif"


def _parse_data_table(lines, npoints=None):
    """Decode an XYDATA/DATA TABLE block of (X++(Y..Y)) lines."""
    ys = []
    prev_y = None
    prev_dif = False
    for line in lines:
        line = line.split("$$")[0].strip()
        if not line:
            continue
        x, row, was_dif = _asdf_line(line, prev_y)
        if x is None:
            continue
        if prev_dif and row:
            # checkpoint: first ordinate of a line following a DIF line
            # repeats the last ordinate of the previous line -- verify, drop.
            if ys and row[0] is not None and ys[-1] is not None:
                if abs(row[0] - ys[-1]) > max(1e-6 * max(abs(ys[-1]), 1.0), 1e-9):
                    raise JeolReadError(
                        "JCAMP DIF checkpoint mismatch (%g vs %g)"
                        % (row[0], ys[-1]))
            row = row[1:]
        ys.extend(row)
        prev_y = ys[-1] if ys else None
        prev_dif = was_dif
    if npoints is not None and len(ys) > npoints:
        ys = ys[:npoints]
    return ys


def read_jcamp(path_or_text):
    """Read a JCAMP-DX file: simple XYDATA blocks and NMR NTUPLES
    (R/I pages). Returns {labels, blocks:[{labels, x, y} ...],
    data:{re,im}|{y}, info}."""
    if isinstance(path_or_text, str) and "\n" not in path_or_text \
            and os.path.exists(path_or_text):
        with open(path_or_text, "r", errors="replace") as fh:
            text = fh.read()
        src = os.path.basename(path_or_text)
    else:
        text = path_or_text
        src = "<text>"

    # split into LDRs
    ldrs = []           # (label, value_first_line, [continuation lines])
    cur = None
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if line.startswith("##"):
            body = line[2:]
            if "=" not in body:
                continue
            label, val = body.split("=", 1)
            label = re.sub(r"[ \t_-]", "", label).upper()
            cur = [label, val.strip(), []]
            ldrs.append(cur)
        else:
            if cur is not None:
                cur[2].append(line)

    if not ldrs or ldrs[0][0] != "TITLE":
        raise JeolReadError("not a JCAMP-DX file (no ##TITLE= first)")

    labels = {}
    for label, val, _ in ldrs:
        if label not in ("XYDATA", "DATATABLE", "PAGE"):
            labels.setdefault(label, val)

    data = {}
    pages = []
    ntuples = {}
    page_var = None
    factors = {}
    symbols = []

    def _split_list(s):
        return [t.strip() for t in s.split(",")]

    i = 0
    while i < len(ldrs):
        label, val, cont = ldrs[i]
        if label == "SYMBOL":
            symbols = _split_list(val)
        elif label == "FACTOR":
            fvals = _split_list(val)
            for k, sym in enumerate(symbols):
                if k < len(fvals):
                    try:
                        factors[sym] = float(fvals[k])
                    except ValueError:
                        factors[sym] = 1.0
        elif label == "VARDIM":
            ntuples["var_dim"] = _split_list(val)
        elif label == "PAGE":
            page_var = val.strip()
        elif label == "DATATABLE":
            ys = _parse_data_table(cont)
            pages.append({"page": page_var, "spec": val.strip(), "y": ys})
        elif label == "XYDATA":
            npoints = None
            try:
                npoints = int(float(labels.get("NPOINTS", "")))
            except ValueError:
                pass
            ys = _parse_data_table(cont, npoints)
            yfac = 1.0
            try:
                yfac = float(labels.get("YFACTOR", "1"))
            except ValueError:
                pass
            data["y"] = [None if v is None else v * yfac for v in ys]
        i += 1

    # assemble NTUPLES pages: match '(X++(R..R))' / '(X++(I..I))'
    for page in pages:
        m = re.search(r"\(X\+\+\((\w)\.\.\1\)\)", page["spec"])
        sym = m.group(1) if m else None
        fac = factors.get(sym, 1.0)
        scaled = [None if v is None else v * fac for v in page["y"]]
        if sym == "R":
            data["re"] = scaled
        elif sym == "I":
            data["im"] = scaled
        elif sym is not None:
            data.setdefault("pages_" + sym, scaled)

    def _flabel(name, default=None):
        v = labels.get(name)
        if v is None:
            return default
        try:
            return float(v)
        except ValueError:
            return default

    info = {
        "source_file": src,
        "format": "jcamp-dx",
        "title": labels.get("TITLE"),
        "jcamp_version": labels.get("JCAMPDX"),
        "data_type": labels.get("DATATYPE"),
        "data_class": labels.get("DATACLASS"),
        "origin": labels.get("ORIGIN"),
        "nucleus": labels.get(".OBSERVENUCLEUS"),
        "spectrometer_freq_mhz": _flabel(".OBSERVEFREQUENCY"),
        "sweep_hz": _flabel(".SW") or _flabel("$SWEEP"),
        "firstx": _flabel("FIRSTX"),
        "lastx": _flabel("LASTX"),
        "npoints": _flabel("NPOINTS"),
        "x_factor": factors.get("X"),      # NTUPLES: the dwell for a FID
        "npoints_data": len(data.get("re", data.get("y", []))),
        "data_sections": sorted(data.keys()),
    }
    return {"labels": labels, "data": data, "info": info}


# ---------------------------------------------------------------------------
# dispatch + bundle-adapter helper
# ---------------------------------------------------------------------------

def read_any(path):
    """Read a JEOL data file by sniffing: .jdf magic first, JCAMP-DX text
    otherwise."""
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head == JDF_MAGIC:
        return read_jdf(path)
    return read_jcamp(path)


def to_experiment_record(result, expno, role, started_local=None,
                         finished_local=None):
    """Map a read_jdf()/read_jcamp() result onto the meta.json 'experiments'
    item shape (schema/meta.schema.json). Conventions:

      td        -- points per row in the Bruker convention (real+imag
                   count), i.e. 2 x complex points for a complex record;
      pulprog   -- the Delta experiment name (jdf 'experiment' param) or
                   the JCAMP DATA TYPE string;
      rg        -- verbatim recvr_gain value. UNVERIFIED semantics: JEOL
                   receiver gain units/linearity are a partner-session
                   deliverable; do NOT treat as a Bruker-style linear
                   amplitude factor.
      o1_hz     -- 0.0 placeholder when the offset cannot be expressed in
                   Hz from available fields (JEOL x_offset unit context
                   varies); the verbatim offset is in vendor_notes.

    Times: prefer operator-log wall-clock times (started_local/
    finished_local args); fall back to jdf-internal 1990-epoch times,
    which carry an UNVERIFIED timezone."""
    info = result["info"]
    notes = {}
    if info["format"] == "jdf":
        npts = info.get("x_points") or (info.get("points") or [0])[0]
        complex_data = "im" in result["data"]
        td = int(npts) * (2 if complex_data else 1)
        sw = info.get("sweep_hz") or 0.0
        aq = info.get("acq_time_s") or 0.0
        ns = info.get("scans") or 1
        rg = info.get("recvr_gain_raw")
        pulprog = info.get("experiment") or "unknown-jxp"
        start = started_local or info.get("actual_start_time_iso") or ""
        finish = finished_local or info.get("end_time_iso") or ""
        notes["offset_verbatim"] = info.get("offset")
        notes["recvr_gain_semantics"] = "UNVERIFIED (JEOL units; partner item 1)"
        if started_local is None:
            notes["time_source"] = "jdf actual_start_time (timezone UNVERIFIED)"
    else:
        npts = int(info.get("npoints") or info.get("npoints_data") or 0)
        complex_data = "im" in result["data"]
        td = npts * (2 if complex_data else 1)
        dwell = info.get("x_factor")
        if dwell:                                   # NTUPLES FID time axis
            sw = 1.0 / dwell
            aq = npts * dwell
        else:
            firstx = info.get("firstx") or 0.0
            lastx = info.get("lastx") or 0.0
            dur = abs(lastx - firstx)
            sw = (npts - 1) / dur if dur > 0 else 0.0
            aq = dur
        ns = 1
        rg = None
        pulprog = info.get("data_type") or "JCAMP-DX"
        start = started_local or ""
        finish = finished_local or ""
        notes["rg"] = ("absent from generic JCAMP-DX export; must come from "
                       "the operator log")

    rec = {
        "expno": int(expno),
        "role": role,
        "pulprog": str(pulprog),
        "td": int(td),
        "td1_rows": 1,               # Tier-1 JEOL protocol uses repeated 1Ds
        "sw_hz": float(sw),
        "o1_hz": 0.0,
        "rg": float(rg) if isinstance(rg, (int, float)) else 0.0,
        "ns": int(ns),
        "aq_s_per_row": float(aq),
        "started_local": str(start),
        "finished_local": str(finish),
    }
    return rec, notes


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="Read a JEOL .jdf or JCAMP-DX file and print its digest.")
    ap.add_argument("path", help=".jdf or .jdx/.dx file")
    ap.add_argument("--json", action="store_true",
                    help="dump the full info digest as JSON")
    ap.add_argument("--data-head", type=int, default=0, metavar="N",
                    help="also print the first N data points per section")
    args = ap.parse_args(argv)

    result = read_any(args.path)
    info = result["info"]
    if args.json:
        print(json.dumps(info, indent=2, default=str))
    else:
        for key in sorted(info):
            print("%-26s %s" % (key, info[key]))
    if args.data_head:
        for name in sorted(result["data"]):
            sec = result["data"][name]
            if sec and isinstance(sec[0], list):
                print("%s: 2D, %d rows" % (name, len(sec)))
            else:
                print("%s: %s" % (name, sec[:args.data_head]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
