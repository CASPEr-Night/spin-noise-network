#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_synthetic_jeol.py -- generate synthetic JEOL-style test files so the
JEOL ingestion chain (jeol_reader.py -> bundle packer) can be exercised
without a spectrometer.

    python3 vendors/jeol/make_synthetic_jeol.py                 # both formats
    python3 vendors/jeol/make_synthetic_jeol.py --out-dir /tmp/x
    python3 vendors/jeol/make_synthetic_jeol.py --formats jcamp

Produces a small Tier-1-shaped session:
    sn_ref_open_001    a decaying complex sinusoid (pulsed reference FID)
    sn_noise_001..002  Gaussian noise records with a weak injected
                       Lorentzian line (the spin-noise stand-in)
each as .jdf (native Delta layout) and/or .jdx (JCAMP-DX 5.00 NTUPLES,
one AFFN copy and one DIFDUP-compressed copy of the first noise record to
exercise the ASDF decoder).

HONESTY NOTE. The .jdf files written here follow the layout that
vendors/jeol/jeol_reader.py reads: the header offsets and parameter-record
format were verified against real Delta files (see jeol_reader.py's
provenance block), so round-tripping through these synthetic files tests
our reader's internal consistency and the documented offsets -- it does
NOT prove that every field a real ECZ/ECZL console writes is understood.
Real-hardware validation is a partner-session deliverable. The JCAMP-DX
files, by contrast, follow a published standard and are a genuine
end-to-end test of that path.

All content is deterministic (seeded); files carry titles marking them as
synthetic so they can never be mistaken for data.

Python 3 stdlib only, nothing newer than 3.6.
Contact: John W. Blanchard <jwbquantum@gmail.com>
"""

from __future__ import print_function

import argparse
import math
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# JEOL 1990 epoch (verified day-level; see jeol_reader.py)
EPOCH1990_2026 = 1_155_600_000  # ~2026-08-14 00:00:00 in 1990-epoch seconds


# ---------------------------------------------------------------------------
# waveforms
# ---------------------------------------------------------------------------

def synth_fid(npts, dwell_s, freq_hz, decay_s, amp, seed, noise_rms):
    """Complex decaying sinusoid + Gaussian noise."""
    rng = random.Random(seed)
    re, im = [], []
    for i in range(npts):
        t = i * dwell_s
        env = amp * math.exp(-t / decay_s)
        re.append(env * math.cos(2 * math.pi * freq_hz * t)
                  + rng.gauss(0.0, noise_rms))
        im.append(env * math.sin(2 * math.pi * freq_hz * t)
                  + rng.gauss(0.0, noise_rms))
    return re, im


def synth_noise(npts, dwell_s, line_hz, line_fwhm_hz, line_amp, seed,
                noise_rms):
    """Gaussian noise plus a weak Lorentzian-line stand-in: exponentially
    damped complex oscillation with random phase kicks would be the honest
    spin-noise model; for a *reader* test a deterministic damped tone at
    low amplitude is sufficient and keeps the file exactly reproducible."""
    lam = math.pi * line_fwhm_hz
    re, im = synth_fid(npts, dwell_s, line_hz, 1.0 / lam, line_amp, seed,
                       noise_rms)
    return re, im


# ---------------------------------------------------------------------------
# native .jdf writer (layout mirrors jeol_reader.read_jdf_header)
# ---------------------------------------------------------------------------

def _packed_date(year, month, day):
    return struct.pack(">I", ((year - 1990) << 25) | (month << 21) | (day << 16))


def _unit_bytes(pairs):
    """pairs: list of (prefix_power_code, base_code); pad to 5 pairs."""
    out = b""
    for prefix, base in pairs:
        out += struct.pack("Bb", ((prefix & 0xF) << 4) | 1, base)
    out += b"\x00\x00" * (5 - len(pairs))
    return out


def _param_record(name, value, vtype, unit_pairs=(), endian="<"):
    rec = b"\x00" * 4
    rec += struct.pack(endian + "h", 0)                    # scaler
    rec += _unit_bytes(list(unit_pairs))                   # 10 bytes
    if vtype == 0:      # String
        rec += ("%-16s" % str(value)[:16]).encode("ascii")
    elif vtype == 1:    # Integer
        rec += struct.pack(endian + "i", int(value)) + b"\x00" * 12
    elif vtype == 2:    # Float
        rec += struct.pack(endian + "d", float(value)) + b"\x00" * 8
    else:
        raise ValueError("unsupported vtype %r" % vtype)
    rec += struct.pack(endian + "i", vtype)
    rec += ("%-28s" % name[:28]).encode("ascii")
    assert len(rec) == 64
    return rec


HERTZ = 13
SECOND = 28
KELVIN = 14
TESLA = 31
NONE_UNIT = 0


def write_jdf(path, re, im, sfrq_hz, sw_hz, title, experiment, recvr_gain,
              start_1990, scans=1, temp_k=298.0):
    """Write a 1D complex .jdf in the verified little-endian float64 layout."""
    npts = len(re)
    aq_s = npts / sw_hz
    params = [
        _param_record("x_domain", "Proton", 0),
        _param_record("experiment", experiment, 0),
        _param_record("sample_id", "synthetic", 0),
        _param_record("solvent", "WATER", 0),
        _param_record("x_freq", sfrq_hz, 2, [(0, HERTZ)]),
        _param_record("x_offset", 4.7, 2, [(0, NONE_UNIT)]),   # ppm-ish, verbatim
        _param_record("x_sweep", sw_hz, 2, [(0, HERTZ)]),
        _param_record("x_sweep_clipped", sw_hz * 0.8, 2, [(0, HERTZ)]),
        _param_record("x_points", npts, 1),
        _param_record("x_acq_time", aq_s, 2, [(0, SECOND)]),
        _param_record("x_resolution", sw_hz / npts, 2, [(0, HERTZ)]),
        _param_record("scans", scans, 1),
        _param_record("total_scans", scans, 1),
        _param_record("x90", 10.0, 2),
        _param_record("relaxation_delay", 0.1, 2, [(0, SECOND)]),
        _param_record("recvr_gain", recvr_gain, 2),
        _param_record("recvr_gain_limit", 102.0, 2),
        _param_record("temp_get", temp_k - 273.15, 2, [(0, 4)]),  # Celsius
        _param_record("temp_set", temp_k - 273.15, 2, [(0, 4)]),
        _param_record("field_strength", sfrq_hz / 42.577478518e6, 2,
                      [(0, TESLA)]),
        _param_record("probe_id", 0.0, 2),
        _param_record("actual_start_time", int(start_1990), 1,
                      [(0, SECOND)]),
        _param_record("end_time", float(start_1990) + aq_s, 2, [(0, SECOND)]),
    ]
    n_par = len(params)
    param_payload = struct.pack("<IIII", 64, 0, n_par - 1, 16 + 64 * n_par)
    param_payload += b"".join(params)

    param_start = 1360
    data_start = param_start + len(param_payload)
    # align data to 16 bytes for tidiness (alignment requirement UNVERIFIED,
    # readers must follow the header pointer anyway)
    pad = (-data_start) % 16
    data_start += pad

    header = bytearray(1360)
    header[0:8] = b"JEOL.NMR"
    header[8] = 1                       # little-endian sections
    header[9] = 1                       # major version
    header[10:12] = struct.pack(">H", 2)
    header[12] = 1                      # ndim
    header[13] = 0b10000000             # dim-exist bits (cosmetic)
    header[14] = (0 << 6) | 1           # float64, One_D
    header[15:16] = struct.pack("b", 25)  # instrument: ECA
    # translate 16..24 zeros
    header[24] = 3                      # axis 0: Complex
    # data units 32..48: axis 0 = Second (time domain)
    header[32:34] = struct.pack("Bb", (0 << 4) | 1, SECOND)
    header[48:172] = ("%-124s" % title[:124]).encode("ascii")
    header[176:180] = struct.pack(">I", npts)       # data_points[0]
    for i in range(1, 8):
        header[176 + 4 * i:180 + 4 * i] = struct.pack(">I", 1)
    header[208:212] = struct.pack(">I", 0)          # offset start
    header[240:244] = struct.pack(">I", npts - 1)   # offset stop
    header[272:280] = struct.pack(">d", 0.0)        # axis start
    header[336:344] = struct.pack(">d", aq_s)       # axis stop
    header[400:404] = _packed_date(2026, 8, 14)
    header[404:408] = _packed_date(2026, 8, 14)
    header[408:424] = ("%-16s" % "synthnode").encode("ascii")
    header[424:552] = ("%-128s" % "spin-noise network synthetic").encode("ascii")
    header[552:680] = ("%-128s" % "make_synthetic_jeol.py").encode("ascii")
    header[680:808] = ("%-128s" % "SYNTHETIC TEST FILE - NOT DATA").encode("ascii")
    header[808:840] = ("%-32s" % "Time").encode("ascii")
    header[1064:1072] = struct.pack(">d", sfrq_hz / 1e6)   # base_freq[0], MHz
    header[1212:1216] = struct.pack(">I", param_start)
    header[1216:1220] = struct.pack(">I", len(param_payload))
    header[1284:1288] = struct.pack(">I", data_start)
    header[1288:1296] = struct.pack(">Q", 8 * npts * 2)

    body = bytes(header) + param_payload + b"\x00" * pad
    body += struct.pack("<%dd" % npts, *re)
    body += struct.pack("<%dd" % npts, *im)
    with open(path, "wb") as fh:
        fh.write(body)
    return path


# ---------------------------------------------------------------------------
# JCAMP-DX writers (published standard; AFFN and DIFDUP forms)
# ---------------------------------------------------------------------------

def _affn_table(xs, ys, per_line=6):
    lines = []
    for i in range(0, len(ys), per_line):
        chunk = ys[i:i + per_line]
        lines.append(" ".join(["%d" % xs[i]] + ["%d" % v for v in chunk]))
    return lines


_SQZ_POS = "@ABCDEFGHI"
_SQZ_NEG = "abcdefghi"
_DIF_POS = "%JKLMNOPQR"
_DIF_NEG = "jklmnopqr"
_DUP_CH = "0STUVWXYZs"     # index = count (1..9); index 0 unused


def _sqz(v):
    s = "%d" % abs(v)
    lead = int(s[0])
    ch = _SQZ_POS[lead] if v >= 0 else _SQZ_NEG[lead - 1] if lead else "@"
    if v < 0 and lead == 0:
        ch = "@"
    return ch + s[1:]


def _dif(d):
    s = "%d" % abs(d)
    lead = int(s[0])
    if d >= 0:
        ch = _DIF_POS[lead]
    else:
        ch = _DIF_NEG[lead - 1] if lead else "%"
    return ch + s[1:]


def _difdup_table(xs, ys, per_line=8):
    """Encode integer ordinates in DIFDUP form: each line = AFFN X, SQZ
    first ordinate, then DIF deltas with DUP run-length compression, and a
    SQZ checkpoint opening the next line (JCAMP-DX standard)."""
    lines = []
    i = 0
    n = len(ys)
    while i < n:
        j = min(i + per_line, n)
        parts = ["%d" % xs[i], _sqz(ys[i])]
        items = []          # encoded DIF items for run-length pass
        for k in range(i + 1, j):
            items.append(ys[k] - ys[k - 1])
        # run-length encode consecutive equal diffs
        k = 0
        while k < len(items):
            run = 1
            while (k + run < len(items) and items[k + run] == items[k]
                   and run < 9):
                run += 1
            parts.append(_dif(items[k]))
            if run > 1:
                parts.append(_DUP_CH[run])
            k += run
        lines.append("".join(parts[:1]) + "".join(parts[1:]))
        i = j
        if i < n:
            # checkpoint: the NEXT line will re-state ys[i-1]... no --
            # the checkpoint is the first ordinate of the next line being
            # the LAST of this line; achieved by starting the next chunk
            # at i-1.
            i -= 1
    return lines


def write_jcamp_ntuples(path, re, im, dwell_s, sfrq_mhz, title, form="AFFN"):
    """Write an NMR FID as JCAMP-DX 5.00 NTUPLES with R and I pages."""
    npts = len(re)
    # integerize with a scale factor (JCAMP tables carry integers; FACTOR
    # restores physical values)
    peak = max(max(abs(v) for v in re), max(abs(v) for v in im), 1e-30)
    yfac = peak / 2 ** 24
    r_int = [int(round(v / yfac)) for v in re]
    i_int = [int(round(v / yfac)) for v in im]
    xs = list(range(npts))

    if form == "AFFN":
        r_lines = _affn_table(xs, r_int)
        i_lines = _affn_table(xs, i_int)
    elif form == "DIFDUP":
        r_lines = _difdup_table(xs, r_int)
        i_lines = _difdup_table(xs, i_int)
    else:
        raise ValueError(form)

    out = []
    out.append("##TITLE= %s" % title)
    out.append("##JCAMP-DX= 5.00")
    out.append("##DATA TYPE= NMR FID")
    out.append("##DATA CLASS= NTUPLES")
    out.append("##ORIGIN= spin-noise network synthetic generator")
    out.append("##OWNER= public domain test file")
    out.append("##.OBSERVE FREQUENCY= %.9f" % sfrq_mhz)
    out.append("##.OBSERVE NUCLEUS= ^1H")
    out.append("##.DELAY= (0, 0)")
    out.append("##NTUPLES= NMR FID")
    out.append("##VAR_NAME=  TIME, FID/REAL, FID/IMAG, PAGE NUMBER")
    out.append("##SYMBOL=    X, R, I, N")
    out.append("##VAR_TYPE=  INDEPENDENT, DEPENDENT, DEPENDENT, PAGE")
    out.append("##VAR_FORM=  AFFN, %s, %s, AFFN" % (form, form))
    out.append("##VAR_DIM=   %d, %d, %d, 2" % (npts, npts, npts))
    out.append("##UNITS=     SECONDS, ARBITRARY UNITS, ARBITRARY UNITS,")
    out.append("##FACTOR=    %.12g, %.12g, %.12g, 1" % (dwell_s, yfac, yfac))
    out.append("##FIRST=     0.0, %.12g, %.12g, 1" % (re[0], im[0]))
    out.append("##LAST=      %.12g, %.12g, %.12g, 2"
               % ((npts - 1) * dwell_s, re[-1], im[-1]))
    out.append("##PAGE= N=1")
    out.append("##NPOINTS= %d" % npts)
    out.append("##DATA TABLE= (X++(R..R)), XYDATA")
    out.extend(r_lines)
    out.append("##PAGE= N=2")
    out.append("##NPOINTS= %d" % npts)
    out.append("##DATA TABLE= (X++(I..I)), XYDATA")
    out.extend(i_lines)
    out.append("##END NTUPLES= NMR FID")
    out.append("##END=")
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")
    return path


def write_jcamp_xydata(path, ys, firstx, lastx, title, xunits="HZ"):
    """Write a real 1D spectrum as a classic XYDATA (X++(Y..Y)) block."""
    npts = len(ys)
    peak = max(abs(v) for v in ys) or 1.0
    yfac = peak / 2 ** 24
    y_int = [int(round(v / yfac)) for v in ys]
    dx = (lastx - firstx) / (npts - 1)
    lines = _affn_table([int(round(firstx / dx)) + i for i in range(npts)],
                        y_int)
    out = [
        "##TITLE= %s" % title,
        "##JCAMP-DX= 4.24",
        "##DATA TYPE= NMR SPECTRUM",
        "##ORIGIN= spin-noise network synthetic generator",
        "##OWNER= public domain test file",
        "##XUNITS= %s" % xunits,
        "##YUNITS= ARBITRARY UNITS",
        "##XFACTOR= %.12g" % dx,
        "##YFACTOR= %.12g" % yfac,
        "##FIRSTX= %.12g" % firstx,
        "##LASTX= %.12g" % lastx,
        "##NPOINTS= %d" % npts,
        "##FIRSTY= %.12g" % ys[0],
        "##XYDATA= (X++(Y..Y))",
    ]
    out.extend(lines)
    out.append("##END=")
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")
    return path


# ---------------------------------------------------------------------------
# session assembly
# ---------------------------------------------------------------------------

def build_session(out_dir, formats=("jdf", "jcamp"), npts=2048,
                  sw_hz=6900.0, sfrq_hz=400.13e6):
    """Write a Tier-1-shaped session. Filenames follow the operator
    checklist convention (vendors/jeol/README.md): a numeric prefix maps
    each file onto the Bruker expno plan (11 = opening reference,
    12/17/18/... = noise records, 13 = closing reference); the packer's
    JEOL adapter reads the prefix as the expno. Extra format-exercise
    files live under extras/ so experiment discovery never sees them."""
    os.makedirs(out_dir, exist_ok=True)
    dwell = 1.0 / sw_hz
    written = []

    records = []
    ref_re, ref_im = synth_fid(npts, dwell, freq_hz=250.0, decay_s=0.5,
                               amp=1000.0, seed=1, noise_rms=1.0)
    records.append((11, "sn_ref_open", ref_re, ref_im, 20.0))
    nre1, nim1 = synth_noise(npts, dwell, line_hz=250.0, line_fwhm_hz=12.0,
                             line_amp=3.0, seed=101, noise_rms=1.0)
    records.append((12, "sn_noise", nre1, nim1, 60.0))
    nre2, nim2 = synth_noise(npts, dwell, line_hz=250.0, line_fwhm_hz=12.0,
                             line_amp=3.0, seed=102, noise_rms=1.0)
    records.append((17, "sn_noise", nre2, nim2, 60.0))

    t0 = EPOCH1990_2026
    for idx, (expno, name, re, im, rg) in enumerate(records):
        base = "%02d_%s" % (expno, name)
        title = "SYNTHETIC %s (spin-noise network test, not data)" % base
        if "jdf" in formats:
            p = write_jdf(os.path.join(out_dir, base + ".jdf"), re, im,
                          sfrq_hz, sw_hz, title,
                          experiment="single_pulse.jxp" if "ref" in name
                          else "sn_nopulse.jxp",
                          recvr_gain=rg, start_1990=t0 + 120 * idx)
            written.append(p)
        if "jcamp" in formats:
            p = write_jcamp_ntuples(os.path.join(out_dir, base + ".jdx"),
                                    re, im, dwell, sfrq_hz / 1e6, title,
                                    form="AFFN")
            written.append(p)

    if "jcamp" in formats:
        extras = os.path.join(out_dir, "extras")
        os.makedirs(extras, exist_ok=True)
        # DIFDUP copy of the first noise record (ASDF decoder exercise)
        expno, name, re, im, rg = records[1]
        p = write_jcamp_ntuples(
            os.path.join(extras, "12_sn_noise_difdup.jdx"), re, im, dwell,
            sfrq_hz / 1e6,
            "SYNTHETIC 12_sn_noise DIFDUP copy (not data)", form="DIFDUP")
        written.append(p)
        # and one classic XYDATA spectrum block
        spec = [1000.0 / (1.0 + ((i - npts // 3) / 25.0) ** 2)
                for i in range(512)]
        p = write_jcamp_xydata(
            os.path.join(extras, "sn_spectrum_demo.jdx"), spec,
            firstx=-sw_hz / 2, lastx=sw_hz / 2,
            title="SYNTHETIC Lorentzian spectrum (not data)")
        written.append(p)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Generate synthetic JEOL .jdf / JCAMP-DX test files.")
    ap.add_argument("--out-dir",
                    default=os.path.join(HERE, "synthetic_jeol"),
                    help="output directory (default vendors/jeol/synthetic_jeol)")
    ap.add_argument("--formats", default="jdf,jcamp",
                    help="comma list: jdf,jcamp (default both)")
    ap.add_argument("--npts", type=int, default=2048)
    args = ap.parse_args(argv)

    formats = tuple(f.strip() for f in args.formats.split(",") if f.strip())
    written = build_session(args.out_dir, formats, npts=args.npts)
    for p in written:
        print(p, file=sys.stderr)
    print(args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
