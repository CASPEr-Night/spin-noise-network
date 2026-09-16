#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
facility_report.py -- the per-facility deliverable of the spin-noise network:
"your receiver's measured distance from the fundamental sensitivity ceiling".

    python3 analysis/facility_report.py <bundle.zip> [--out DIR]

Input : one bundle zip in the network format (expno tree per topspin/INSTALL.md,
        meta.json per schema/meta.schema.json). Raw data are read by the
        format each experiment's own parameter file declares: Bruker
        acqus + ser/fid, or Agilent/Varian procpar + fid (schema 2.0
        vendor "agilent"; Tier-1 sessions of N single-row noise
        experiments are aggregated into one noise block). An experiment
        declaring neither is refused and reported, never guessed.
Output: report.html (single self-contained file; figures inlined as base64
        PNG; light/dark friendly) and report.json (machine-readable numbers)
        in the output directory (default: <bundle_stem>_report next to the
        bundle).

Pipeline (adapted from the validated 2020 EPFL pilot analysis,
Spin_Noise_2020/extracted/spin_noise_pipeline.py; scipy replaced with
numpy-only equivalents so facility machines need nothing beyond numpy +
matplotlib):

  1. Bundle validation via the uploader's own selftest validator.
  2. Run-mode gate: simulate/desktest bundles get a clearly marked
     SOFTWARE-TEST report and NO science numbers (protects against test
     data masquerading as results). synthetic-injection bundles run the
     full science pipeline but are watermarked as validation, not data.
  3. Noise block: per-row Welch PSDs (Hann, 50% overlap, power co-added --
     never amplitude), spike replacement outside the protected line region,
     broad-Savitzky-Golay baseline normalization, absorptive+dispersive
     line fit per row, drift alignment, co-add, master refit. Both signs
     handled (RT Gueron dip / cryo emission bump). If no significant
     feature: calibrated upper limit on the feature amplitude.
  4. RG ladder: amplitude linearity across the ladder expnos (the 2020
     pilot's biggest untested systematic; surfaced prominently).
  5. References: A0 back-extrapolation, linewidth, open/close line-position
     stability, reference-tail floor vs gain-bridged noise floor.
  6. Headline numbers: spin-coupled floor fraction, temperature-contrast
     point (honest 'requires coil/preamp temperatures' when absent),
     distance from the fundamental ceiling with stated assumptions
     (2020 methodology: pairing factor, back-action).
  7. QA flags: sweep state, lock state, ADC clipping, spike counts,
     timestamp sanity, software provenance.
  8. Clock audit (schema 1.2 bundles): fits the fractional console-clock
     offset from wall-clock vs OCXO-implied elapsed time across blocks,
     re-deriving each block's expected duration from its bundled
     pulse-program text plus acqus (every programmed delay, pulse, and
     the per-scan pre-acquisition delay DE -- the acquisition-side
     formula models only AQ and d1, and a per-scan shortfall biases the
     offset by ~shortfall/scan-duration), states which
     absolute-frequency requirement tiers the offset satisfies, and
     flags short sessions as inconclusive. Older bundles without the
     audit are reported as such, without penalty; blocks whose
     pulse-program text or acqus cannot be modeled with certainty fall
     back to the script-recorded expectations, flagged per block.

Spin-noise master formula (FDT-verified; see the project fact brief):
  S_V(Delta)/S_floor = 1 + f_c*lambda_r*[(Ts/Tc - 2)*lambda - lambda_r]
                           / (lambda_tot^2 + Delta^2),   f_c = Tc/(Tc+T_A).
Uniform temperature -> pure absorption DIP (Gueron dip); cold circuit ->
emission BUMP with larger contrast. RT probes absolutely measure spin noise
(McCoy & Ernst 1989; Gueron & Leroy 1989).

Authors: Blanchard, Ebadi, Claude (Anthropic).
Contact: John W. Blanchard <jwbquantum@gmail.com>.
"""

from __future__ import print_function

import argparse
import base64
import datetime
import importlib.util
import io
import json
import math
import os
import re
import struct
import sys
import tempfile
import zipfile

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(REPO, "uploader"))
import upload_bundle  # noqa: E402  (the repo's own validator)

try:
    with open(os.path.join(REPO, "VERSION"), "r") as _fh:
        REPORT_VERSION = _fh.read().strip()
except Exception:
    REPORT_VERSION = "unknown"

CONTACT = "John W. Blanchard <jwbquantum@gmail.com>"

# ---------------------------------------------------------------- constants
# Kept in Hz (physics scales), converted to bins per bundle. Values mirror
# the validated 2020 pipeline where a direct analogue exists.
NPERSEG_MAX = 32768        # Welch segment cap (2020 value)
DROP_ROW = 512             # samples dropped at each noise-row start (filter transient)
EDGE_FRAC = 0.43           # analysis band |f| < EDGE_FRAC*SW (digital-filter rolloff outside)
LINE_SEARCH_HZ = 40.0      # per-row search half-window around the reference-derived guess
LINE_MASK_HZ = 100.0       # masked around the line for baseline/spike protection
FIT_HALF_HZ = 150.0        # fit window half-width
SPIKE_NSIGMA = 6.0         # spike threshold in robust sigmas of narrow-SG residual
SG_NARROW_HZ = 7.0         # narrow SG window (~half the expected linewidth)
SG_BROAD_HZ = 1500.0       # broad SG baseline window (~hundred linewidths)
COADD_HALF_HZ = 300.0      # co-added grid half-width
DETECT_NSIGMA = 5.0        # amplitude significance required to claim a feature
UL_CL = 1.645              # one-sided 95% CL multiplier for upper limits
GRPDLY_DEFAULT = 68        # digital-filter group delay (points) of the 2020 consoles

# 2020-methodology systematic envelope (stated, never silently applied):
PAIRING_FACTOR_2020 = 1.4          # A0-vs-window pairing factor (absolute calibrations)
BACKACTION_RANGE_2020 = (2.7, 3.7)  # cold-circuit back-action suppression range
RG_POWER_ENVELOPE_UNTESTED = 0.20   # +/-20% in power if the RG ladder is absent

# Axion-coupling exclusion (worst-case, per session): the 2020 pilot's
# construction (Spin_Noise_2020/extracted/compute_axion_limit.py) with
# every input taken from the bundle at hand.
C_KMS = 299792.458
HBARC_GEV_CM = 1.9733e-14
GEV_TO_RADS = 1.519268e24          # 1/hbar
EV_PER_HZ = 4.135667696e-15        # h
RHO_DM_GEV_CM3 = 0.3
SHM_V0_KMS = 220.0
SHM_VLAB_KMS = 233.0
SHM_VESC_KMS = 544.0
# D_CAL_PILOT is the 2020 EPFL pilot's calibration envelope: the full
# observed noise-vs-pulsed power deficit of THAT session, one factor over
# every multiplicative calibration error (RG nonlinearity, A0/window
# pairing, flip angle, back-action interpretation), never stacked with
# them. It is reused here UNMEASURED at the site and recorded in the JSON
# so a calibrated per-site decomposition can replace it.
D_CAL_PILOT = 4.6
ESTIMATOR_BANDWIDTH_REL = 0.011    # pilot's power-estimator bandwidth term
EXCL_WINDOW_MIN_HZ = 80.0
EXCL_WINDOW_FWHM_MULT = 4.0
EXCL_SCAN_HZ = (-4000.0, 400.0, 4.0)        # nu_a - nu_L: start, stop, step
EXCL_LINESHAPE_GRID_HZ = (0.0, 6000.0, 2.0)  # offsets above nu_a
EXCL_MC_SAMPLES = 2000000
EXCL_MC_SEED = 20200529
SN1987A_GAP_GEV_INV = 3.3e-10      # SN1987A cooling bound on g_ap (Carenza 2019, pilot paper)

SOFTWARE_TEST_MODES = ("simulate", "desktest")

# Clock audit (schema 1.2). Acquisition durations derive from the console's
# OCXO master clock; the workstation wall clock is normally NTP-disciplined.
# Fitting wall-clock elapsed vs OCXO-implied elapsed across the session's
# blocks measures the fractional console-clock offset for free.
NTP_JITTER_S = 0.010          # assumed wall-clock timestamp jitter (5-10 ms typical)
CLOCK_MIN_SPAN_S = 3600.0     # audits spanning less than 1 h are inconclusive
CLOCK_CONSISTENCY_MAX = 0.05  # blocks whose wall/OCXO ratio is off by more than
                              # this are overhead-dominated (dialogs, tune) and
                              # useless at the 1e-7 level: excluded from the fit

# Requirement tiers for absolute-frequency (axion-search) use of the data.
# Each entry: (tier id, name, fractional requirement, note).
CLOCK_TIERS = (
    ("i", "detection + per-site exclusion", 6.0e-7,
     "the axion virial linewidth (~6e-7 fractional, ~380 Hz at 600 MHz) "
     "dominates; a stock OCXO is fine"),
    ("ii", "mass-scale labeling", 1.0e-6,
     "a 1 ppm clock error is a 1 ppm axion-mass error; a stock OCXO is "
     "fine provided the offset is recorded -- which this audit does"),
    ("iii", "cross-site coincidence", 1.0e-7,
     "sites must agree to under ~1e-7; aged OCXOs can miss, which is "
     "exactly what this audit screens for"),
    ("iv", "sidereal-Doppler signature", 1.2e-9,
     "~1.2e-9 fractional (~0.7 Hz at 600 MHz); beyond any software audit "
     "of practical span -- needs disciplined or per-record-calibrated "
     "clocks (GPSDO reference input, or a GPSDO-locked pilot tone)"),
)


# ============================================================================
# Raw-data readers (zip-resident): Bruker acqus + ser/fid, Agilent/Varian
# procpar + fid. Every experiment is read by the format its own parameter
# file declares; an experiment with neither parameter file is REFUSED and
# the refusal is reported -- dtype, endianness and record layout are never
# guessed from defaults (a misread noise row silently produces a
# confident null result, the worst failure mode this report can have).
# ============================================================================

def parse_jcamp(text):
    """Parse a Bruker JCAMP-DX parameter file into {name: value}.

    Scalars become float/int/str; <bracketed> strings are unwrapped;
    array blocks (e.g. the delay list D) are stored as whitespace-joined
    raw strings -- split() to index them.
    """
    out = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"##\$?([A-Za-z0-9_]+)=\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if re.match(r"^\(\d+\.\.\d+\)$", val) or (
                    val.startswith("(") and ")" in val
                    and not val.endswith(")")):
                # array header like (0..63); values on following lines
                buf = []
                i += 1
                while i < len(lines) and not lines[i].startswith("##"):
                    buf.append(lines[i].strip())
                    i += 1
                out[key] = " ".join(buf)
                continue
            if val.startswith("<") and val.endswith(">"):
                out[key] = val[1:-1]
            else:
                try:
                    # string checks FIRST: real acqus carry DBL_MAX
                    # sentinels ('1.79769313486232e+308', parsed as inf)
                    # for unset doubles, and int(inf) raises OverflowError
                    fv = float(val)
                    out[key] = int(fv) if "." not in val \
                        and "e" not in val.lower() and fv == int(fv) else fv
                except (ValueError, OverflowError):
                    out[key] = val
        i += 1
    return out


# Varian/Agilent fid layout (nmrglue varian.py; verified on VnmrJ 3.2 DD2
# output at SIU Carbondale, 2026-09): 32-byte big-endian file header,
# then per block nbheaders x 28-byte block headers followed by ntraces
# traces of np points (np counts re+im, like Bruker TD). The element type
# comes from the file-status bits, never from a default; the first block
# header of every block repeats those bits and carries the block's
# scale (samples stored divided by 2^scale when an accumulation would
# have overflowed) and the lvl/tlt DC-correction levels. Mirrors the
# constants in vendors/agilent/agilent_reader.py, whose procpar parser
# this module imports by path.
VARIAN_FILE_HEADER = ">6ihhi"
VARIAN_FILE_HEADER_FIELDS = ("nblocks", "ntraces", "np", "ebytes", "tbytes",
                             "bbytes", "vers_id", "status", "nbheaders")
VARIAN_BLOCK_HEADER = ">4hi4f"
VARIAN_BLOCK_HEADER_FIELDS = ("scale", "status", "index", "mode", "ctcount",
                              "lpval", "rpval", "lvl", "tlt")
VARIAN_FILE_HEADER_BYTES = 32
VARIAN_BLOCK_HEADER_BYTES = 28
VARIAN_S_32 = 0x4
VARIAN_S_FLT = 0x8
VARIAN_S_TYPE_BITS = VARIAN_S_32 | VARIAN_S_FLT
# Full scale of an integer Varian sample: dp='n' stores 16-bit ADC words.
# dp='y' (S_32) stores the digital receiver's output in 32-bit containers
# whose word width the file does not declare, so no full scale is claimed
# for it (2^31-1 is a Bruker data-word assumption that would let a railed
# 16-bit ADC pass as 0.0015% of full scale).
INT_FULLSCALE = {"int16": 32767.0}

_AGILENT_READER_MOD = None


def agilent_reader_module():
    """vendors/agilent/agilent_reader.py loaded by file path (the same
    module packer/pack_bundle.py delegates to); the repo checkout is
    required, as for the packer."""
    global _AGILENT_READER_MOD
    if _AGILENT_READER_MOD is None:
        path = os.path.join(REPO, "vendors", "agilent", "agilent_reader.py")
        if not os.path.isfile(path):
            raise RuntimeError("vendors/agilent/agilent_reader.py not found "
                               "at %s -- the Agilent read path needs the "
                               "repository checkout" % path)
        spec = importlib.util.spec_from_file_location("snn_agilent_reader",
                                                      path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _AGILENT_READER_MOD = mod
    return _AGILENT_READER_MOD


_SITE_EXCLUSION_MOD = None


def site_exclusion_module():
    """analysis/site_exclusion.py loaded by file path (the per-site
    combiner --prior-reports delegates to); the repo checkout is
    required, as for the vendor readers."""
    global _SITE_EXCLUSION_MOD
    if _SITE_EXCLUSION_MOD is None:
        path = os.path.join(REPO, "analysis", "site_exclusion.py")
        if not os.path.isfile(path):
            raise RuntimeError("analysis/site_exclusion.py not found at %s "
                               "-- --prior-reports needs the repository "
                               "checkout" % path)
        spec = importlib.util.spec_from_file_location("snn_site_exclusion",
                                                      path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _SITE_EXCLUSION_MOD = mod
    return _SITE_EXCLUSION_MOD


class VarianFormatError(ValueError):
    """A fid whose headers do not describe its bytes; never read anyway."""


def varian_fid_header(raw):
    """Parse and structurally verify a Varian fid file header. Returns the
    header dict with 'dtype' ('float32' / 'int32' / 'int16' from the
    status bits) and 'numpy_dtype' (big-endian)."""
    if len(raw) < VARIAN_FILE_HEADER_BYTES:
        raise VarianFormatError("fid is shorter than the 32-byte file header")
    hdr = dict(zip(VARIAN_FILE_HEADER_FIELDS,
                   struct.unpack(VARIAN_FILE_HEADER,
                                 raw[:VARIAN_FILE_HEADER_BYTES])))
    status = hdr["status"]
    if status & VARIAN_S_FLT:
        hdr["dtype"], hdr["numpy_dtype"] = "float32", ">f4"
    elif status & VARIAN_S_32:
        hdr["dtype"], hdr["numpy_dtype"] = "int32", ">i4"
    else:
        hdr["dtype"], hdr["numpy_dtype"] = "int16", ">i2"
    ebytes = np.dtype(hdr["numpy_dtype"]).itemsize
    problems = []
    if hdr["ebytes"] != ebytes:
        problems.append("ebytes %d disagrees with the status-bit element "
                        "type %s (%d bytes)"
                        % (hdr["ebytes"], hdr["dtype"], ebytes))
    if hdr["np"] < 2 or hdr["np"] % 2:
        problems.append("np %d is not an even count of interleaved re/im "
                        "points" % hdr["np"])
    if hdr["nblocks"] < 1 or hdr["ntraces"] < 1 or hdr["nbheaders"] < 1:
        problems.append("nblocks/ntraces/nbheaders = %d/%d/%d"
                        % (hdr["nblocks"], hdr["ntraces"], hdr["nbheaders"]))
    if hdr["tbytes"] != hdr["np"] * hdr["ebytes"]:
        problems.append("tbytes %d != np*ebytes %d"
                        % (hdr["tbytes"], hdr["np"] * hdr["ebytes"]))
    want_bbytes = (hdr["nbheaders"] * VARIAN_BLOCK_HEADER_BYTES
                   + hdr["ntraces"] * hdr["tbytes"])
    if hdr["bbytes"] != want_bbytes:
        problems.append("bbytes %d != nbheaders*28 + ntraces*tbytes %d"
                        % (hdr["bbytes"], want_bbytes))
    want_size = VARIAN_FILE_HEADER_BYTES + hdr["nblocks"] * hdr["bbytes"]
    if len(raw) != want_size:
        problems.append("file size %d != 32 + nblocks*bbytes %d"
                        % (len(raw), want_size))
    if problems:
        raise VarianFormatError(
            "Varian fid header does not describe the file (%s); status 0x%x, "
            "header %s" % ("; ".join(problems), status,
                           {k: hdr[k] for k in VARIAN_FILE_HEADER_FIELDS}))
    return hdr


def read_varian_fid(raw):
    """(rows, header, raw_values): rows is an (n_rows, np/2) complex
    float64 array, one row per trace in block-then-trace order;
    raw_values is the flat float64 view of every stored sample (for the
    clipping check). The first block header of every block is parsed
    (header['block_headers']), never read as samples: a block whose
    element-type status bits disagree with the file header, or whose
    scale is non-zero, raises VarianFormatError -- the stored samples
    would be 2^scale below the acquired counts and this reader does not
    rescale."""
    hdr = varian_fid_header(raw)
    dt = np.dtype(hdr["numpy_dtype"])
    rows, values, blocks = [], [], []
    off = VARIAN_FILE_HEADER_BYTES
    for b in range(hdr["nblocks"]):
        bh = dict(zip(VARIAN_BLOCK_HEADER_FIELDS,
                      struct.unpack(VARIAN_BLOCK_HEADER,
                                    raw[off:off + VARIAN_BLOCK_HEADER_BYTES])))
        if (bh["status"] & VARIAN_S_TYPE_BITS) != (hdr["status"]
                                                   & VARIAN_S_TYPE_BITS):
            raise VarianFormatError(
                "block %d header status 0x%x disagrees with the file header "
                "status 0x%x on the element type (S_32/S_FLT bits); refused"
                % (b + 1, bh["status"], hdr["status"]))
        if bh["scale"] != 0:
            raise VarianFormatError(
                "block %d header scale %d: samples are stored divided by "
                "2^%d and this report does not rescale -- refused rather "
                "than read %g-fold low" % (b + 1, bh["scale"], bh["scale"],
                                            2.0 ** bh["scale"]))
        blocks.append(bh)
        off += hdr["nbheaders"] * VARIAN_BLOCK_HEADER_BYTES
        for _t in range(hdr["ntraces"]):
            v = np.frombuffer(raw, dtype=dt, count=hdr["np"],
                              offset=off).astype(np.float64)
            rows.append(v[0::2] + 1j * v[1::2])
            values.append(v)
            off += hdr["tbytes"]
    hdr["block_headers"] = blocks
    return np.array(rows), hdr, np.concatenate(values)


class Bundle(object):
    """Read-only access to the bundle zip contents.

    read_rows / raw_int_stats dispatch on the experiment's own parameter
    file (acqus -> Bruker, procpar -> Agilent/Varian). Refusals are kept in
    read_errors (expno -> reason) and successful reads in read_log (expno
    -> format, dtype, rows, points) so the report can state exactly what
    was and was not read.
    """

    def __init__(self, path):
        self.path = path
        self.zf = zipfile.ZipFile(path, "r")
        self.names = set(self.zf.namelist())
        self.meta = json.loads(self.zf.read("meta.json").decode("utf-8"))
        # schema 1.x bundles carry no vendor field: every 1.x writer was
        # the TopSpin orchestrator
        self.vendor = str(self.meta.get("vendor") or "bruker").lower()
        self.read_errors = {}
        self.read_log = {}
        self._procpar_cache = {}

    def has(self, name):
        return name in self.names

    def read(self, name):
        return self.zf.read(name)

    def acqus(self, expno):
        p = "data/%d/acqus" % expno
        if not self.has(p):
            return {}
        try:
            return parse_jcamp(self.read(p).decode("utf-8", "replace"))
        except Exception:
            return {}

    def experiment_format(self, expno):
        """'bruker' (acqus present), 'agilent' (declared vendor agilent, or
        procpar without acqus), or None when nothing declares the layout."""
        has_acqus = self.has("data/%d/acqus" % expno)
        has_procpar = self.has("data/%d/procpar" % expno)
        if self.vendor == "agilent" or (has_procpar and not has_acqus):
            return "agilent"
        if has_acqus:
            return "bruker"
        return None

    def _refuse(self, expno, why):
        self.read_errors.setdefault(int(expno), why)

    def procpar(self, expno):
        """Parsed Varian procpar of an expno ({} when absent/unparseable,
        the reason kept for procpar_problem); parse_procpar works on a
        path, so the zip member goes through a temporary file."""
        if expno in self._procpar_cache:
            return self._procpar_cache[expno][0]
        p = "data/%d/procpar" % expno
        out, problem = {}, None
        if not self.has(p):
            problem = "no procpar in data/%d/" % expno
        else:
            mod = agilent_reader_module()
            tmp = tempfile.NamedTemporaryFile(prefix="snn_procpar_",
                                              delete=False)
            try:
                tmp.write(self.read(p))
                tmp.close()
                out = mod.parse_procpar(tmp.name) or {}
            except Exception as exc:
                out = {}
                problem = "procpar unparseable (%s: %s)" % (
                    type(exc).__name__, exc)
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
        self._procpar_cache[expno] = (out, problem)
        return out

    def procpar_problem(self, expno):
        """Why acq_from_procpar(expno) is empty -- missing file, parser
        exception, or a parsed procpar without a usable sw/np -- with the
        files present in the expno dir; None when it is usable."""
        pp = self.procpar(expno)
        problem = self._procpar_cache[expno][1]
        if problem is None and not self.acq_from_procpar(expno):
            sc = agilent_reader_module().scalar
            n_bad = len(pp.get("_unparsed") or [])
            problem = ("procpar parsed but unusable: %d parameter(s) "
                       "recognised%s, sw=%r, np=%r"
                       % (len([k for k in pp if k != "_unparsed"]),
                          (", %d line(s) not in procpar record format"
                           % n_bad) if n_bad else "",
                          sc(pp, "sw"), sc(pp, "np")))
        if problem is None:
            return None
        present = sorted(os.path.basename(n) for n in self.names
                         if n.startswith("data/%d/" % expno))
        return "%s (files present: %s)" % (problem,
                                           ", ".join(present) or "none")

    def acq_from_procpar(self, expno):
        """The acquisition dict the analysis stages consume, synthesized
        from a Varian procpar under the Bruker acqus key names:
        SW_h = sw, TD = np (total re+im points, the TD convention),
        RG = 10^(gain/20) (the packer's linear meta rg), NS = nt,
        DS = ss, DE = 0 and GRPDLY = 0 (no Bruker digital-filter group
        delay in a Varian fid), PULPROG = seqfil, O1 = tof (sign and
        reference convention UNVERIFIED -- vendor checklist item 2),
        SFO1 = sfrq. {} when procpar is absent or lacks sw/np."""
        pp = self.procpar(expno)
        if not pp:
            return {}
        sc = agilent_reader_module().scalar
        sw, npts = sc(pp, "sw"), sc(pp, "np")
        if not isinstance(sw, (int, float)) or sw <= 0 \
                or not isinstance(npts, (int, float)) or npts < 2:
            return {}
        acq = {"_vendor": "agilent", "_source": "procpar",
               "SW_h": float(sw), "TD": int(npts), "DE": 0.0, "GRPDLY": 0.0}
        gain = sc(pp, "gain")
        if isinstance(gain, (int, float)):
            acq["RG"] = 10.0 ** (float(gain) / 20.0)
            acq["RG_DB"] = float(gain)
        nt = sc(pp, "nt")
        acq["NS"] = int(nt) if isinstance(nt, (int, float)) and nt >= 1 else 1
        ss = sc(pp, "ss")
        acq["DS"] = int(ss) if isinstance(ss, (int, float)) and ss > 0 else 0
        seqfil = sc(pp, "seqfil") or sc(pp, "pslabel")
        if isinstance(seqfil, str) and seqfil:
            acq["PULPROG"] = seqfil
        for src, dst in (("tof", "O1"), ("sfrq", "SFO1"), ("at", "AQ"),
                         ("pw", "PW_US"), ("tpwr", "TPWR_DB")):
            v = sc(pp, src)
            if isinstance(v, (int, float)):
                acq[dst] = float(v)
        return acq

    def acq_params(self, expno):
        """Acquisition parameters by the experiment's own format."""
        fmt = self.experiment_format(expno)
        if fmt == "agilent":
            return self.acq_from_procpar(expno)
        if fmt == "bruker":
            return self.acqus(expno)
        return {}

    def acqu2s(self, expno):
        p = "data/%d/acqu2s" % expno
        if not self.has(p):
            return {}
        try:
            return parse_jcamp(self.read(p).decode("utf-8", "replace"))
        except Exception:
            return {}

    def pulseprogram_text(self, expno):
        """The pulse-program text TopSpin stores in the expno dir, or None."""
        p = "data/%d/pulseprogram" % expno
        if not self.has(p):
            return None
        try:
            return self.read(p).decode("utf-8", "replace")
        except Exception:
            return None

    def _unrecognised(self, expno):
        present = sorted(os.path.basename(n) for n in self.names
                         if n.startswith("data/%d/" % expno))
        self._refuse(expno, (
            "no acqus (Bruker) and no procpar (Agilent/Varian) in data/%d/ "
            "(files present: %s); vendor '%s' raw data are not readable by "
            "this report -- dtype, byte order and record layout were NOT "
            "guessed, the experiment is excluded"
            % (expno, ", ".join(present) or "none", self.vendor)))

    def read_rows(self, expno, exp_meta):
        """Return (rows, acq) where rows is an (n_rows, n_complex) complex
        array from the expno's raw data file, or (None, acq) when the
        experiment is refused (reason in read_errors[expno]).
        """
        fmt = self.experiment_format(expno)
        if fmt == "agilent":
            return self._read_rows_agilent(expno, exp_meta)
        if fmt == "bruker":
            return self._read_rows_bruker(expno, exp_meta)
        self._unrecognised(expno)
        return None, {}

    def _read_rows_bruker(self, expno, exp_meta):
        acq = self.acqus(expno)
        if not acq:
            self._refuse(expno, "acqus present but unparseable: BYTORDA/"
                                "DTYPA/TD unknown, refused rather than "
                                "guessed")
            return None, acq
        td = int(acq.get("TD", exp_meta.get("td", 0)) or 0)
        n_rows = int(exp_meta.get("td1_rows", 1) or 1)
        bytord = int(acq.get("BYTORDA", 0) or 0)
        dtypa = int(acq.get("DTYPA", 0) or 0)
        if dtypa == 2:
            dt = np.dtype("<f8" if bytord == 0 else ">f8")
        else:
            dt = np.dtype("<i4" if bytord == 0 else ">i4")
        raw = None
        is_ser = False
        for fn in ("ser", "fid"):
            p = "data/%d/%s" % (expno, fn)
            if self.has(p):
                raw = self.read(p)
                is_ser = (fn == "ser")
                break
        if raw is None:
            self._refuse(expno, "no ser/fid data file in data/%d/" % expno)
            return None, acq
        if td < 4:
            self._refuse(expno, "TD=%d (acqus/meta): no usable record" % td)
            return None, acq
        row_bytes = td * dt.itemsize
        padded = int(math.ceil(row_bytes / 1024.0)) * 1024
        rows = []
        if is_ser and n_rows > 1:
            stride = padded if len(raw) >= n_rows * padded else row_bytes
            for r in range(n_rows):
                chunk = raw[r * stride: r * stride + row_bytes]
                if len(chunk) < row_bytes:
                    break
                v = np.frombuffer(chunk, dtype=dt).astype(np.float64)
                rows.append(v[0::2] + 1j * v[1::2])
        else:
            v = np.frombuffer(raw[:row_bytes], dtype=dt).astype(np.float64)
            if v.size < td:
                v = np.frombuffer(raw, dtype=dt).astype(np.float64)
            rows.append(v[0::2] + 1j * v[1::2])
        if not rows:
            self._refuse(expno, "ser shorter than one TD=%d row" % td)
            return None, acq
        rows = np.array(rows)
        self.read_log[int(expno)] = {
            "format": "bruker", "dtype": str(dt),
            "n_rows": int(rows.shape[0]), "n_points_complex": int(rows.shape[1]),
            "dc_offset_subtracted": False}
        return rows, acq

    def _agilent_fid(self, expno):
        """(acq, header, rows, raw_values) of an Agilent/Varian expno, or
        None once the refusal is recorded: unusable procpar, no fid,
        headers that do not describe the file, or fid np != procpar np."""
        acq = self.acq_from_procpar(expno)
        if not acq:
            self._refuse(expno, (
                "Agilent/Varian experiment: %s -- sw, np and gain unknown; "
                "dtype, byte order and record layout were NOT guessed, the "
                "experiment is excluded" % self.procpar_problem(expno)))
            return None
        p = "data/%d/fid" % expno
        if not self.has(p):
            self._refuse(expno, "no fid data file in data/%d/" % expno)
            return None
        try:
            rows, hdr, values = read_varian_fid(self.read(p))
        except VarianFormatError as exc:
            self._refuse(expno, str(exc))
            return None
        if hdr["np"] != acq["TD"]:
            self._refuse(expno, "fid header np %d disagrees with procpar np "
                                "%d -- inconsistent experiment, refused"
                                % (hdr["np"], acq["TD"]))
            return None
        return acq, hdr, rows, values

    def _read_rows_agilent(self, expno, exp_meta):
        got = self._agilent_fid(expno)
        if got is None:
            return None, self.acq_from_procpar(expno)
        acq, hdr, rows, _values = got
        # the per-row complex mean is recorded, not subtracted: the Welch
        # PSD detrends every segment and the reference/ladder spectra
        # remove their own mean, whereas subtracting it from a pulsed
        # reference whose line sits on the carrier removes signal from
        # the time-domain A0 back-extrapolation
        dc = np.mean(rows, axis=1)
        bh = hdr["block_headers"][0]
        acq["_dtype"] = hdr["dtype"]
        acq["_fid_header"] = {k: hdr[k] for k in VARIAN_FILE_HEADER_FIELDS}
        entry = {"format": "agilent", "dtype": hdr["dtype"],
                 "n_rows": int(rows.shape[0]),
                 "n_points_complex": int(rows.shape[1]),
                 "fid_nblocks": hdr["nblocks"], "fid_ntraces": hdr["ntraces"],
                 "fid_status": hdr["status"],
                 "fid_block_header": {k: bh[k] for k in (
                     "scale", "status", "index", "mode", "ctcount",
                     "lvl", "tlt")},
                 "dc_offset_subtracted": False,
                 "dc_offset_max_abs": float(np.max(np.abs(dc)))}
        meta_td = exp_meta.get("td")
        if meta_td is not None and int(meta_td) != hdr["np"]:
            entry["note"] = ("meta.json td %s != fid np %d; the fid header "
                             "governs" % (meta_td, hdr["np"]))
        meta_rows = exp_meta.get("td1_rows")
        if meta_rows is not None and int(meta_rows) != rows.shape[0]:
            entry["note"] = ((entry.get("note", "") + "; ").lstrip("; ")
                             + "meta.json td1_rows %s != rows read %d"
                             % (meta_rows, rows.shape[0]))
        self.read_log[int(expno)] = entry
        return rows, acq

    def raw_int_stats(self, expno):
        """Max |value| and full-scale fraction for the ADC-clipping check,
        by the experiment's own declared element type."""
        fmt = self.experiment_format(expno)
        if fmt == "agilent":
            got = self._agilent_fid(expno)
            if got is None:
                return None
            _acq, hdr, _rows, values = got
            mx = float(np.max(np.abs(values))) if values.size else 0.0
            full = INT_FULLSCALE.get(hdr["dtype"])
            return {"max_abs": mx,
                    "fullscale_fraction": (mx / full) if full else None,
                    "dtype": hdr["dtype"]}
        if fmt != "bruker":
            self._unrecognised(expno)
            return None
        acq = self.acqus(expno)
        if not acq:
            self._refuse(expno, "acqus present but unparseable: BYTORDA/"
                                "DTYPA/TD unknown, refused rather than "
                                "guessed")
            return None
        bytord = int(acq.get("BYTORDA", 0) or 0)
        dtypa = int(acq.get("DTYPA", 0) or 0)
        for fn in ("ser", "fid"):
            p = "data/%d/%s" % (expno, fn)
            if self.has(p):
                raw = self.read(p)
                if dtypa == 2:
                    v = np.frombuffer(raw[: (len(raw) // 8) * 8],
                                      dtype="<f8" if bytord == 0 else ">f8")
                    return {"max_abs": float(np.max(np.abs(v))) if v.size else 0.0,
                            "fullscale_fraction": None, "dtype": "float64"}
                v = np.frombuffer(raw[: (len(raw) // 4) * 4],
                                  dtype="<i4" if bytord == 0 else ">i4")
                if not v.size:
                    return None
                mx = float(np.max(np.abs(v.astype(np.float64))))
                return {"max_abs": mx, "fullscale_fraction": mx / 2147483647.0,
                        "dtype": "int32"}
        return None


def group_delay_points(acq):
    """Digital-filter group delay in complex points, the index of the true
    FID start: Bruker GRPDLY when acqus states it, else the stock 68 of
    the 2020 consoles; a Varian fid starts at its first sample."""
    if acq.get("_vendor") == "agilent":
        return 0
    g = float(acq.get("GRPDLY", 0) or 0)
    return int(round(g)) if g > 0 else GRPDLY_DEFAULT


# ============================================================================
# numpy-only DSP (scipy-free equivalents of the 2020 pipeline stages)
# ============================================================================

def savgol(y, window, order):
    """Savitzky-Golay smoothing via convolution (reflect-padded edges)."""
    window = int(window)
    if window % 2 == 0:
        window += 1
    window = max(window, order + 2 if (order + 2) % 2 == 1 else order + 3)
    if window >= y.size:
        window = (y.size // 2) * 2 - 1
    half = window // 2
    x = np.arange(-half, half + 1, dtype=np.float64)
    A = np.vander(x, order + 1, increasing=True)
    coeffs = np.linalg.pinv(A)[0]          # evaluates the LSQ polynomial at 0
    ypad = np.concatenate([y[half:0:-1], y, y[-2:-half - 2:-1]])
    return np.convolve(ypad, coeffs[::-1], mode="valid")


def welch_psd(x, fs, nperseg):
    """Two-sided Welch PSD of complex data (Hann, 50% overlap, constant
    detrend, density scaling), fftshifted. Matches scipy.signal.welch."""
    nperseg = int(min(nperseg, x.size))
    step = nperseg // 2
    n = np.arange(nperseg)
    win = 0.5 - 0.5 * np.cos(2.0 * np.pi * n / nperseg)   # periodic Hann
    scale = 1.0 / (fs * np.sum(win * win))
    nseg = (x.size - nperseg) // step + 1
    acc = np.zeros(nperseg)
    for i in range(nseg):
        seg = x[i * step: i * step + nperseg]
        seg = seg - np.mean(seg)
        X = np.fft.fft(seg * win)
        acc += (X * np.conj(X)).real
    p = acc * scale / max(nseg, 1)
    f = np.fft.fftfreq(nperseg, d=1.0 / fs)
    return np.fft.fftshift(f), np.fft.fftshift(p), nseg


def robust_sigma(x):
    return 1.4826 * np.median(np.abs(x - np.median(x)))


def lineshape(f, a, b, f0, w, c):
    """Lorentzian absorption (a) + dispersive (b) + flat offset c; w = FWHM."""
    u = (f - f0) / (w / 2.0)
    return c + (a + b * u) / (1.0 + u ** 2)


def _linear_abc(fm, pm, f0, w):
    """For fixed (f0, w) the model is linear in (a, b, c): solve exactly."""
    u = (fm - f0) / (w / 2.0)
    L = 1.0 / (1.0 + u ** 2)
    M = np.column_stack([L, u * L, np.ones_like(fm)])
    coef, res, _, _ = np.linalg.lstsq(M, pm, rcond=None)
    model = M.dot(coef)
    ssr = float(np.sum((pm - model) ** 2))
    return coef, ssr


def fit_line(f, pnorm, f0_guess, search_hz=LINE_SEARCH_HZ,
             w_lo=1.0, w_hi=80.0, allow_dip=True):
    """Absorptive+dispersive line fit by coarse-to-fine grid over (f0, w)
    with exact linear solves for (a, b, c). Returns (popt, perr, ssr, m).

    Handles both signs: the linear solve places no sign constraint on a.
    """
    m = np.abs(f - f0_guess) < FIT_HALF_HZ
    fm, pm = f[m], pnorm[m]
    if fm.size < 30:
        raise ValueError("fit window too small")
    df = f[1] - f[0]
    f0s = np.arange(f0_guess - search_hz, f0_guess + search_hz + df, max(df, 0.25))
    ws = np.geomspace(w_lo, w_hi, 40)
    best = (None, np.inf)
    for f0 in f0s:
        for w in ws:
            coef, ssr = _linear_abc(fm, pm, f0, w)
            if ssr < best[1]:
                best = ((coef, f0, w), ssr)
    (coef, f0, w), ssr = best
    # two refinement passes
    for span_f, span_w, nf, nw in ((2.0, 1.6, 21, 21), (0.4, 1.12, 21, 21)):
        f0s = np.linspace(f0 - span_f, f0 + span_f, nf)
        ws = np.geomspace(max(w / span_w, w_lo), min(w * span_w, w_hi * 1.5), nw)
        best = (None, np.inf)
        for f0c in f0s:
            for wc in ws:
                c2, s2 = _linear_abc(fm, pm, f0c, wc)
                if s2 < best[1]:
                    best = ((c2, f0c, wc), s2)
        (coef, f0, w), ssr = best
    a, b, c = [float(v) for v in coef]
    popt = np.array([a, b, f0, w, c])
    # parameter covariance from the numerical Jacobian, curve_fit-style
    # (cov scaled by reduced chi^2, i.e. sigma estimated from residuals)
    J = np.empty((fm.size, 5))
    eps = [1e-6, 1e-6, max(df, 0.05) * 0.1, max(w * 1e-3, 1e-3), 1e-6]
    for k in range(5):
        pp = popt.copy()
        pm_ = popt.copy()
        pp[k] += eps[k]
        pm_[k] -= eps[k]
        J[:, k] = (lineshape(fm, *pp) - lineshape(fm, *pm_)) / (2 * eps[k])
    dof = max(fm.size - 5, 1)
    s2 = ssr / dof
    try:
        cov = s2 * np.linalg.pinv(J.T.dot(J))
        perr = np.sqrt(np.clip(np.diag(cov), 0, None))
    except Exception:
        perr = np.full(5, np.nan)
    return popt, perr, ssr, int(fm.size)


def matched_filter_npe(f, pnorm, w, f0_line, edge_hz, exclude_dc_hz=50.0):
    """CASPEr-style normalized power excess: convolve (pnorm-1) with a
    unit-power Lorentzian kernel, normalize by the off-line std."""
    df = f[1] - f[0]
    half = int(np.ceil(5 * w / df))
    fk = np.arange(-half, half + 1) * df
    kern = 1.0 / (1.0 + (fk / (w / 2.0)) ** 2)
    kern /= np.sqrt(np.sum(kern ** 2))
    y = np.convolve(pnorm - 1.0, kern[::-1], mode="same")
    inwin = (np.abs(f) < edge_hz) & (np.abs(f) > exclude_dc_hz)
    offline = inwin & (np.abs(f - f0_line) > 300.0)
    npe = (y - np.mean(y[offline])) / np.std(y[offline])
    return npe, offline


# ============================================================================
# Analysis stages
# ============================================================================

def pick_nperseg(n):
    """Largest power of two <= n/4, capped at the 2020 value."""
    if n < 4096:
        return max(256, 2 ** int(math.floor(math.log(max(n, 2), 2))) // 2)
    return int(min(NPERSEG_MAX, 2 ** int(math.floor(math.log(n / 4.0, 2)))))


def analyze_reference_row(x, fs, grpdly):
    """One pulsed small-flip row: A0 back-extrapolation + amplitude-spectrum
    lineshape fit (same model as the noise line: apples-to-apples width)."""
    out = {}
    g = int(round(grpdly)) if grpdly is not None and grpdly >= 0 \
        else GRPDLY_DEFAULT
    t = (np.arange(x.size) - g) / fs
    env = np.abs(x)
    # earliest clean decay rate (8-20 ms) and direct A0 back-extrapolation
    m_r = (t > 0.008) & (t < 0.020) & (env > 0)
    m0 = (t > 0.008) & (t < 0.014)
    if m_r.sum() > 8 and m0.sum() > 3:
        cr = np.polyfit(t[m_r], np.log(env[m_r]), 1)
        r_early = float(-cr[0])
        out["early_decay_rate_per_s"] = r_early
        out["A0_counts"] = float(np.median(env[m0]) * math.exp(r_early * 0.011))
    else:
        out["A0_counts"] = float(np.max(env)) if env.size else 0.0
        out["early_decay_rate_per_s"] = None
    # spectrum from the true FID start
    start = g + 12
    n_after = x.size - start
    nfft = 2 ** int(math.floor(math.log(max(n_after, 256), 2)))
    seg = x[start:start + nfft]
    seg = seg - np.mean(seg[nfft // 2:])
    spec = np.fft.fftshift(np.fft.fft(seg))
    fax = np.fft.fftshift(np.fft.fftfreq(nfft, d=1.0 / fs))
    amp = np.abs(spec)
    i0 = int(np.argmax(amp))
    f0_pk = float(fax[i0])
    try:
        popt, perr, ssr, npts = fit_line(fax, amp, f0_pk, search_hz=10.0,
                                         w_lo=0.5, w_hi=120.0)
        out["line_center_hz"] = float(popt[2])
        out["fwhm_amp_hz"] = float(abs(popt[3]))
        out["fwhm_amp_err_hz"] = float(perr[3])
        out["amp_b_over_a"] = float(popt[1] / popt[0]) if popt[0] else None
    except Exception as exc:
        out["line_center_hz"] = f0_pk
        out["fwhm_amp_hz"] = None
        out["fit_error"] = str(exc)
    # tail floor (counts^2/Hz) from the last 30% of the row -- valid once the
    # signal has decayed; flagged as approximate in the report
    tail = x[int(x.size * 0.7):]
    if tail.size >= 2048:
        ftl, ptl, _ = welch_psd(tail, fs, pick_nperseg(tail.size))
        band = np.abs(ftl) < EDGE_FRAC * fs
        out["tail_floor_counts2perhz"] = float(np.median(ptl[band]))
    else:
        out["tail_floor_counts2perhz"] = None
    return out


def analyze_reference_exp(bundle, exp, fs_default):
    rows, acq = bundle.read_rows(exp["expno"], exp)
    if rows is None:
        return {"expno": exp["expno"], "role": exp["role"], "readable": False,
                "why": bundle.read_errors.get(int(exp["expno"]),
                                              "raw data unreadable")}
    fs = float(acq.get("SW_h", exp.get("sw_hz", fs_default)))
    g = group_delay_points(acq)
    per_row = [analyze_reference_row(r, fs, g) for r in rows]
    ok = [r for r in per_row if r.get("fwhm_amp_hz")]
    res = {"expno": exp["expno"], "role": exp["role"], "readable": True,
           "n_rows": len(rows), "n_points_complex": int(rows.shape[1]),
           "fs_hz": fs, "grpdly": float(acq.get("GRPDLY", 0) or 0),
           "group_delay_points_used": g,
           "data_format": acq.get("_vendor", "bruker"),
           "rg": float(exp.get("rg", acq.get("RG", 0)) or 0),
           "per_row": per_row}
    for key in ("PW_US", "TPWR_DB", "RG_DB"):
        if acq.get(key) is not None:
            res[key.lower()] = acq[key]
    if acq.get("_vendor") != "agilent":
        # Bruker small-flip record: P1 (us) and the channel-1 power level,
        # PL in dB of attenuation (TopSpin 2/3) or PLW in watts (TopSpin
        # 3/4, converted to the same dB scale); the exclusion's tip angle
        # reads them
        pvals = _jcamp_array(acq, "P")
        if len(pvals) > 1 and pvals[1] > 0:
            res["p1_us"] = pvals[1]
        plv = _jcamp_array(acq, "PL")
        plw = _jcamp_array(acq, "PLW")
        if len(plv) > 1 and math.isfinite(plv[1]):
            res["pl1_db"] = plv[1]
        elif len(plw) > 1 and math.isfinite(plw[1]) and plw[1] > 0:
            res["pl1_db"] = -10.0 * math.log10(plw[1])
    if ok:
        res["line_center_hz"] = float(np.mean([r["line_center_hz"] for r in ok]))
        res["line_center_std_hz"] = float(np.std([r["line_center_hz"] for r in ok]))
        res["fwhm_amp_hz"] = float(np.mean([r["fwhm_amp_hz"] for r in ok]))
        res["A0_counts"] = float(np.mean([r["A0_counts"] for r in per_row]))
        tails = [r["tail_floor_counts2perhz"] for r in per_row
                 if r.get("tail_floor_counts2perhz")]
        res["tail_floor_counts2perhz"] = float(np.mean(tails)) if tails else None
    return res


def analyze_noise_row(x, fs, f0_guess, edge_hz):
    """2020 stages 1-4 on one noise row."""
    nps = pick_nperseg(x.size - DROP_ROW)
    f, p, nseg = welch_psd(x[DROP_ROW:], fs, nps)
    df = f[1] - f[0]
    line_mask = np.abs(f - f0_guess) < LINE_MASK_HZ
    # spike replacement guided by narrow-SG residuals (line protected)
    nb = max(5, int(round(SG_NARROW_HZ / df)) | 1)
    smooth = savgol(p, nb, 2)
    resid = p - smooth
    sig = robust_sigma(resid)
    spikes = (np.abs(resid) > SPIKE_NSIGMA * sig) & (~line_mask)
    pc = p.copy()
    pc[spikes] = smooth[spikes]
    # broad-SG baseline with the line bridged, then divide
    pb = pc.copy()
    if line_mask.any():
        pb[line_mask] = np.interp(f[line_mask], f[~line_mask], pc[~line_mask])
    bb = min(int(round(SG_BROAD_HZ / df)) | 1, (pb.size // 3) * 2 - 1)
    base = savgol(pb, bb, 3)
    pnorm = pc / base
    return {"f": f, "pnorm": pnorm, "base": base, "psd": p, "nseg": nseg,
            "n_spikes": int(np.count_nonzero(spikes)), "df": df,
            "nperseg": nps}


def noise_group_key(exp):
    """Acquisition parameters that must agree for noise experiments to be
    rows of one block: (sw_hz, td, rg)."""
    return (round(float(exp.get("sw_hz") or 0.0), 6),
            int(exp.get("td") or 0),
            round(float(exp.get("rg") or 0.0), 6))


def group_noise_experiments(exps):
    """Partition noise-role experiments into same-parameter groups, each a
    dict {key, exps} with exps in meta order (the operator's acquisition
    order). A Bruker pseudo-2D noise expno is a group of one; a Tier-1
    Agilent session's N single-row noise experiments become one group of
    N rows. Groups come back largest first (total rows), ties in meta
    order, so groups[0] is the headline block."""
    groups = []
    for e in exps:
        key = noise_group_key(e)
        for g in groups:
            if g["key"] == key:
                g["exps"].append(e)
                break
        else:
            groups.append({"key": key, "exps": [e]})

    def total_rows(g):
        return sum(int(e.get("td1_rows") or 1) for e in g["exps"])

    groups.sort(key=lambda g: -total_rows(g))
    return groups


def read_noise_group(bundle, exps):
    """Rows of a same-parameter noise group in meta order.

    Returns (rows, acq, sources, skipped): rows an (n, n_complex) array
    (None when nothing was readable), acq the first readable experiment's
    parameters, sources one {expno, row_in_expno, started_local} per row,
    skipped [{expno, why}] for experiments left out.
    """
    all_rows, sources, skipped, acq0 = [], [], [], None
    n_complex = None
    for e in exps:
        expno = int(e["expno"])
        rows, acq = bundle.read_rows(expno, e)
        if rows is None:
            skipped.append({"expno": expno,
                            "why": bundle.read_errors.get(
                                expno, "raw data unreadable")})
            continue
        if n_complex is None:
            n_complex = int(rows.shape[1])
            acq0 = acq
        if int(rows.shape[1]) != n_complex:
            skipped.append({"expno": expno,
                            "why": "row length %d differs from the group's "
                                   "%d complex points" % (rows.shape[1],
                                                          n_complex)})
            continue
        for i, x in enumerate(rows):
            all_rows.append(x)
            sources.append({"expno": expno, "row_in_expno": i + 1,
                            "started_local": e.get("started_local")})
    if not all_rows:
        return None, (acq0 or {}), sources, skipped
    return np.array(all_rows), acq0, sources, skipped


def analyze_noise_block(bundle, exps, f0_guess, fs_default):
    """2020 stages on one noise block. `exps` is one noise experiment
    (a Bruker pseudo-2D expno whose rows are the block) or a list of
    same-parameter experiments whose rows, in meta order, form the block
    (the Agilent Tier-1 layout of N single-row experiments)."""
    if isinstance(exps, dict):
        exps = [exps]
    rows, acq, sources, skipped = read_noise_group(bundle, exps)
    if rows is None:
        return None
    exp = exps[0]
    fs = float(acq.get("SW_h", exp.get("sw_hz", fs_default)))
    edge_hz = EDGE_FRAC * fs
    expnos = []
    for s in sources:
        if s["expno"] not in expnos:
            expnos.append(s["expno"])
    out = {"expno": exp["expno"], "expnos": expnos,
           "n_experiments": len(expnos), "fs_hz": fs,
           "n_rows": int(rows.shape[0]),
           "n_points_complex": int(rows.shape[1]),
           "row_seconds": float(rows.shape[1] / fs),
           "data_format": acq.get("_vendor", "bruker"),
           "rg": float(exp.get("rg", acq.get("RG", 0)) or 0),
           "edge_hz": edge_hz, "per_row": [], "_rows": []}
    if acq.get("_vendor") == "agilent":
        out["axis_sign_unverified"] = True
    if skipped:
        out["skipped_experiments"] = skipped
    for x, src in zip(rows, sources):
        r = analyze_noise_row(x, fs, f0_guess, edge_hz)
        row = {"expno": src["expno"], "row_in_expno": src["row_in_expno"],
               "started_local": src["started_local"],
               "nseg": r["nseg"], "n_spikes": r["n_spikes"],
               "resolution_hz": r["df"], "nperseg": r["nperseg"],
               "psd_median_in_band": float(np.median(
                   r["psd"][np.abs(r["f"]) < edge_hz]))}
        try:
            popt, perr, ssr, npts = fit_line(r["f"], r["pnorm"], f0_guess)
            a, b, f0, w, c = [float(v) for v in popt]
            row["fit"] = {"amp_norm": a, "amp_err": float(perr[0]),
                          "disp_norm": b, "disp_err": float(perr[1]),
                          "center_hz": f0, "center_err_hz": float(perr[2]),
                          "fwhm_hz": w, "fwhm_err_hz": float(perr[3]),
                          "offset": c}
            row["asymmetry_b_over_a"] = b / a if a else None
            base_at_line = float(np.median(
                r["base"][np.abs(r["f"] - f0) < 50.0]))
            row["baseline_psd_at_line"] = base_at_line
            row["integrated_power_counts2"] = a * math.pi * w / 2.0 * base_at_line
            npe, offline = matched_filter_npe(r["f"], r["pnorm"], w, f0, edge_hz)
            i_line = int(np.argmin(np.abs(r["f"] - f0)))
            row["npe_at_line"] = float(npe[i_line])
            row["offline_norm_var"] = float(
                np.var(r["pnorm"][offline & (np.abs(r["f"]) < edge_hz)]))
        except Exception as exc:
            row["fit_error"] = str(exc)
        out["per_row"].append(row)
        out["_rows"].append(r)
    # drift-aligned power co-add of the normalized PSDs.
    # Alignment uses only CONFIDENT per-row centers (>=3 sigma amplitude of
    # the majority sign, center error < FWHM/2): for a weak feature, aligning
    # every row on its own noisy center smears the co-added line and biases
    # the recovered amplitude low (injection-verified failure mode). Rows
    # without a confident center get the confident rows' weighted mean shift.
    fitted = [(rr, pr) for rr, pr in zip(out["_rows"], out["per_row"])
              if "fit" in pr]
    if fitted:
        amps = np.array([pr["fit"]["amp_norm"] for _, pr in fitted])
        errs = np.array([max(pr["fit"]["amp_err"], 1e-12) for _, pr in fitted])
        maj_sign = 1.0 if np.sum(amps / errs ** 2) >= 0 else -1.0
        conf = []
        for rr, pr in fitted:
            ft = pr["fit"]
            good = (np.sign(ft["amp_norm"]) == maj_sign
                    and abs(ft["amp_norm"]) >= 3.0 * ft["amp_err"]
                    and ft["center_err_hz"] < ft["fwhm_hz"] / 2.0)
            pr["center_used_for_alignment"] = bool(good)
            conf.append(good)
        if any(conf):
            cc = np.array([pr["fit"]["center_hz"] for (_, pr), g
                           in zip(fitted, conf) if g])
            ce = np.array([max(pr["fit"]["center_err_hz"], 1e-6)
                           for (_, pr), g in zip(fitted, conf) if g])
            mean_shift = float(np.sum(cc / ce ** 2) / np.sum(1.0 / ce ** 2))
        else:
            mean_shift = f0_guess
        out["coadd_n_rows_self_aligned"] = int(np.count_nonzero(conf))
        df = fitted[0][0]["df"]
        grid = np.arange(-COADD_HALF_HZ, COADD_HALF_HZ + df / 2, df)
        acc = np.zeros_like(grid)
        for (rr, pr), g in zip(fitted, conf):
            shift = pr["fit"]["center_hz"] if g else mean_shift
            acc += np.interp(grid, rr["f"] - shift, rr["pnorm"])
        avg = acc / len(fitted)
        out["_coadd"] = {"grid": grid, "avg": avg}
        popt, perr, ssr, npts = fit_line(grid, avg, 0.0, search_hz=15.0)
        a, b, f0, w, c = [float(v) for v in popt]
        out["coadd_fit"] = {
            "amp_norm": a, "amp_err": float(perr[0]),
            "disp_norm": b, "disp_err": float(perr[1]),
            "center_shift_hz": f0, "fwhm_hz": w, "fwhm_err_hz": float(perr[3]),
            "offset": c, "asymmetry_b_over_a": (b / a if a else None),
            "asymmetry_err": (abs(b / a) * math.sqrt(
                (perr[0] / a) ** 2 + (perr[1] / b) ** 2)
                if a and b else None),
            "n_rows_coadded": len(fitted),
        }
        out["headline_fit"] = "coadd_fit"
        # Cross-check WITHOUT alignment: the same rows stacked at their
        # recorded frequencies and fitted at the seed. When the per-row
        # feature is marginal, the confidence gate still passes rows whose
        # fits landed on noise (widths railed at the fit floor, centers
        # scattered over the whole search window); aligning those sharpens
        # noise into a spurious narrow line and inflates the co-added
        # amplitude. Slow drift smears this stack instead, so the two
        # agree only when the feature is real and per-row confident.
        stack = np.mean([rr["pnorm"] for rr, _ in fitted], axis=0)
        f_axis = fitted[0][0]["f"]
        try:
            u_popt, u_perr, _ssr, _n = fit_line(f_axis, stack, f0_guess,
                                                search_hz=15.0)
            ua, ub, uf0, uw, uc = [float(v) for v in u_popt]
            out["unaligned_fit"] = {
                "amp_norm": ua, "amp_err": float(u_perr[0]),
                "disp_norm": ub, "disp_err": float(u_perr[1]),
                "center_hz": uf0, "center_err_hz": float(u_perr[2]),
                "fwhm_hz": uw, "fwhm_err_hz": float(u_perr[3]),
                "offset": uc, "asymmetry_b_over_a": (ub / ua if ua else None),
                "asymmetry_err": (abs(ub / ua) * math.sqrt(
                    (u_perr[0] / ua) ** 2 + (u_perr[1] / ub) ** 2)
                    if ua and ub else None),
                "n_rows_coadded": len(fitted)}
            out["_stack"] = {"f": f_axis, "avg": stack}
            railed = sum(1 for (_, pr), g in zip(fitted, conf)
                         if g and pr["fit"]["fwhm_hz"] <= 1.05)
            spread = (float(max(cc) - min(cc)) if any(conf) else 0.0)
            amp_ratio = abs(a) / abs(ua) if ua else None
            width_ratio = uw / w if w else None
            suspect = (amp_ratio is not None and amp_ratio > 1.5) \
                or (width_ratio is not None and width_ratio > 2.0) \
                or (any(conf) and railed >= 0.5 * np.count_nonzero(conf))
            out["headline_fit"] = "unaligned_fit" if suspect else "coadd_fit"
            out["alignment_check"] = {
                "aligned_amp": a, "unaligned_amp": ua,
                "amp_ratio_aligned_over_unaligned": amp_ratio,
                "aligned_fwhm_hz": w, "unaligned_fwhm_hz": uw,
                "n_rows_self_aligned": int(np.count_nonzero(conf)),
                "n_self_aligned_rows_width_railed": int(railed),
                "self_aligned_center_spread_hz": spread,
                "suspect": bool(suspect),
                "verdict": (
                    "aligned and unaligned co-adds agree; alignment is "
                    "tracking a real, per-row-confident feature"
                    if not suspect else
                    "SUSPECT: the self-aligned co-add is %s the unaligned "
                    "stack (amplitude x%.2f, width x%.2f narrower; %d of %d "
                    "aligning rows have widths railed at the fit floor, "
                    "centers spread %.1f Hz) -- the per-row centers are "
                    "noise-dominated and aligning on them manufactures a "
                    "narrow line. The UNALIGNED fit (amp %.3f +/- %.3f, "
                    "FWHM %.1f Hz) is the honest estimate of this block's "
                    "feature and is what the headline, detection and "
                    "floor-calibration numbers use; the aligned fit is kept "
                    "as coadd_fit for reference only."
                    % ("inflated relative to", amp_ratio or float("nan"),
                       width_ratio or float("nan"), railed,
                       np.count_nonzero(conf), spread, ua,
                       float(u_perr[0]), uw))}
        except Exception as exc:
            out["alignment_check"] = {"suspect": False,
                                      "error": str(exc)}
    return out


def headline_fit(res):
    """(fit, key): the co-added line fit the headline, detection and
    floor-calibration numbers use -- the drift-aligned 'coadd_fit',
    unless the alignment cross-check marked it SUSPECT, when the
    'unaligned_fit' of the stack at recorded frequencies is the honest
    estimate. (None, None) without a fitted block."""
    if not res:
        return None, None
    key = res.get("headline_fit") or "coadd_fit"
    fit = res.get(key)
    if fit is None:
        key = "coadd_fit"
        fit = res.get(key)
    return fit, (key if fit is not None else None)


def reference_pair_check(refs):
    """Whether the readable references share one pulse (pw, tpwr): the
    open/close A0 ratio and line-position drift compare flip angles
    otherwise (SIU session 1, 2026-09-14: pw 0.05 vs 0.1125 us). None
    when fewer than two references record a pulse width (Bruker acqus
    are not read for it)."""
    pulses = [{"expno": r["expno"], "role": r.get("role"),
               "pw_us": r.get("pw_us"), "tpwr_db": r.get("tpwr_db")}
              for r in refs
              if r.get("readable") and r.get("pw_us") is not None]
    if len(pulses) < 2:
        return None
    out = {"matched": len(set((p["pw_us"], p["tpwr_db"])
                             for p in pulses)) == 1,
           "pulses": pulses}
    if not out["matched"]:
        out["note"] = (
            "references are NOT a matched pair: %s -- their A0 ratio and "
            "line-position drift compare different flip angles"
            % "; ".join("expno %d pw %.4g us / tpwr %s dB"
                        % (p["expno"], p["pw_us"],
                           "%.3g" % p["tpwr_db"] if p["tpwr_db"] is not None
                           else "?") for p in pulses))
    return out


def upper_limit_at(f, pnorm, f0_ref, w_candidates):
    """Calibrated 95% upper limit on |feature amplitude| (normalized units)
    at a fixed line position, profiled over candidate widths."""
    best = 0.0
    details = []
    m = np.abs(f - f0_ref) < FIT_HALF_HZ
    fm, pm = f[m], pnorm[m]
    for w in w_candidates:
        coef, ssr = _linear_abc(fm, pm, f0_ref, w)
        a = float(coef[0])
        u = (fm - f0_ref) / (w / 2.0)
        L = 1.0 / (1.0 + u ** 2)
        M = np.column_stack([L, u * L, np.ones_like(fm)])
        s2 = ssr / max(fm.size - 3, 1)
        cov = s2 * np.linalg.pinv(M.T.dot(M))
        sa = math.sqrt(max(cov[0, 0], 0))
        ul = abs(a) + UL_CL * sa
        details.append({"fwhm_hz": float(w), "amp": a, "amp_err": sa,
                        "ul95": ul})
        best = max(best, ul)
    return best, details


# ============================================================================
# v0.6 analysis riders
#
# (a) Persistent-line catalog: every narrow excess in every readable
#     noise block, classified by how it moves when the carrier moves.
#     Under the v0.6 carrier-follow sweep the receiver window tracks
#     each field step, so across steps a SPIN line stays near its
#     baseline window position, a RECEIVER-CHAIN spur stays fixed in the
#     window frame, and an ABSOLUTE-frequency line (external RFI, a
#     console clock spur -- or a dark-matter candidate) marches through
#     the window by minus the carrier shift. This 3-way separation is
#     the groundwork for the dark-photon line search and the spur
#     catalog; without carrier diversity the window/absolute split is
#     recorded as indeterminate.
#
# (b) Sub-virial pass: a native-resolution (1/T_row) mean periodogram of
#     the headline noise block with a narrow-candidate list.
#     INFRASTRUCTURE ONLY: a real sub-virial dark-matter feature chirps
#     with Earth's rotation (~0.9 Hz over a night at 600 MHz), so no
#     physics claim is possible without the diurnal chirp templates --
#     which this pass does not yet apply, and says so.
# ============================================================================

CATALOG_NSIGMA = 5.0          # feature threshold, stacked-PSD sigmas
CATALOG_DC_EXCLUDE_HZ = 50.0  # skip the DC/carrier-leakage region
CATALOG_CLUSTER_TOL_HZ = 3.0  # same-line tolerance across blocks
CATALOG_MAX_LISTED = 40
SUBVIRIAL_NSIGMA = 6.0
SUBVIRIAL_MAX_WIDTH_HZ = 1.0
SUBVIRIAL_MAX_LISTED = 30


def _block_features(res, f0_local, w_ref):
    """Narrow excess features in one block's stacked normalized PSD:
    [{window_hz, excess, width_hz, is_spin}]. DC and band edges are
    excluded; 'is_spin' tags features within 3 linewidths of the
    block's expected line position."""
    rows = res.get("_rows") or []
    if not rows:
        return []
    f = rows[0]["f"]
    stack = np.mean([rr["pnorm"] for rr in rows], axis=0)
    edge = res.get("edge_hz") or 0.45 * res.get("fs_hz", 12000.0)
    core = (np.abs(f) < edge) & (np.abs(f) > CATALOG_DC_EXCLUDE_HZ)
    if not np.any(core):
        return []
    sig = robust_sigma(stack[core])
    if not sig or not np.isfinite(sig):
        return []
    hot = core & (stack - 1.0 > CATALOG_NSIGMA * sig)
    feats = []
    i = 0
    idx = np.where(hot)[0]
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
            j += 1
        seg = idx[i:j + 1]
        wts = stack[seg] - 1.0
        center = float(np.sum(f[seg] * wts) / np.sum(wts))
        spin_tol = 3.0 * max(w_ref or 0.0, 5.0)
        feats.append({
            "window_hz": center,
            "excess": float(np.max(stack[seg]) - 1.0),
            "excess_nsigma": float((np.max(stack[seg]) - 1.0) / sig),
            "width_hz": float(f[seg[-1]] - f[seg[0]]) if len(seg) > 1
                        else float(f[1] - f[0]),
            "is_spin": abs(center - f0_local) < spin_tol,
        })
        i = j + 1
    return feats


def _cluster_1d(items, key, tol):
    """Greedy 1-d clustering of dicts by items[key] within tol."""
    out = []
    for ft in sorted(items, key=lambda x: x[key]):
        if out and abs(ft[key] - out[-1]["center"]) <= tol:
            out[-1]["members"].append(ft)
            out[-1]["center"] = float(np.mean(
                [m[key] for m in out[-1]["members"]]))
        else:
            out.append({"center": float(ft[key]), "members": [ft]})
    return out


def persistent_line_catalog(catalog_blocks, w_ref):
    """3-way persistent-line classification across noise blocks.

    catalog_blocks: [{expno, res, carrier_shift_hz, f0_local}] --
    carrier_shift_hz is how far the receiver window moved from the
    baseline carrier (0 for standard blocks and legacy fixed-carrier
    sweeps; the step target under carrier-follow)."""
    all_feats = []
    for b in catalog_blocks:
        for ft in _block_features(b["res"], b["f0_local"], w_ref):
            ft["expno"] = b["expno"]
            ft["carrier_shift_hz"] = float(b["carrier_shift_hz"])
            ft["absolute_hz"] = ft["window_hz"] + float(
                b["carrier_shift_hz"])
            all_feats.append(ft)
    shifts = sorted(set(round(b["carrier_shift_hz"], 1)
                        for b in catalog_blocks))
    diversity = len(shifts) > 1
    catalog = {"n_blocks": len(catalog_blocks),
               "carrier_shifts_hz": shifts,
               "carrier_diversity": diversity,
               "n_features_raw": len(all_feats),
               "lines": [],
               "note": (
                   "classification: 'spin_line' = tracks the expected "
                   "line position; 'window_fixed' = constant offset "
                   "from the (moving) carrier -> receiver-chain spur; "
                   "'absolute_fixed' = constant absolute frequency -> "
                   "external RFI, console clock spur, or dark-matter "
                   "candidate; 'persistent_same_shift_indeterminate' = "
                   "repeated, but only in blocks sharing one carrier "
                   "shift, so the frames cannot be separated for it; "
                   "'single_block' = seen once, unclassifiable. "
                   "Window/absolute discrimination "
                   "requires carrier diversity (a carrier-follow sweep); "
                   "this session %s." % (
                       "has it" if diversity else
                       "does NOT have it -- window and absolute frames "
                       "coincide"))}
    nonspin = [ft for ft in all_feats if not ft["is_spin"]]
    spin = [ft for ft in all_feats if ft["is_spin"]]
    spin_tol = 3.0 * max(w_ref or 0.0, 5.0)
    if diversity and spin:
        # Under carrier-follow the spin line IS window-fixed, so any
        # per-block remnant that escaped its own block's is_spin tag
        # (mis-seeded fit, weak row) must not cluster into a
        # "receiver-chain spur" (review F4): fold everything near the
        # mean spin window position back into the spin group.
        mean_spin_w = float(np.mean([ft["window_hz"] for ft in spin]))
        keep = []
        for ft in nonspin:
            if abs(ft["window_hz"] - mean_spin_w) < spin_tol:
                ft["is_spin"] = True
                spin.append(ft)
            else:
                keep.append(ft)
        nonspin = keep
    if spin:
        catalog["lines"].append({
            "class": "spin_line",
            "n_blocks_seen": len(set(ft["expno"] for ft in spin)),
            "mean_window_hz": float(np.mean(
                [ft["window_hz"] for ft in spin])),
            "max_excess_nsigma": float(np.max(
                [ft["excess_nsigma"] for ft in spin]))})
    used = set()
    for frame, cls in (("window_hz", "window_fixed"),
                       ("absolute_hz", "absolute_fixed")):
        for cl in _cluster_1d([ft for ft in nonspin
                               if id(ft) not in used],
                              frame, CATALOG_CLUSTER_TOL_HZ):
            expnos = set(m["expno"] for m in cl["members"])
            csh = set(round(m["carrier_shift_hz"], 1)
                      for m in cl["members"])
            accept_cls = None
            if len(expnos) >= 2 and (len(csh) >= 2 or not diversity):
                accept_cls = cls
                if not diversity:
                    accept_cls = "persistent_frame_indeterminate"
            elif (len(expnos) >= 2 and diversity and len(csh) == 1
                  and frame == "window_hz"):
                # persistent across blocks that share one carrier shift
                # (e.g. the standard noise block + the target-0 step):
                # real and repeated, but the frames cannot be separated
                # for it (review F5)
                accept_cls = "persistent_same_shift_indeterminate"
            if accept_cls is None:
                continue
            for m in cl["members"]:
                used.add(id(m))
            catalog["lines"].append({
                "class": accept_cls,
                "n_blocks_seen": len(expnos),
                "center_hz": cl["center"],
                "frame": frame.replace("_hz", ""),
                "max_excess_nsigma": float(np.max(
                    [m["excess_nsigma"] for m in cl["members"]]))})
        if not diversity:
            break     # one pass is meaningful without carrier diversity
    singles = [ft for ft in nonspin if id(ft) not in used]
    for ft in sorted(singles, key=lambda x: -x["excess_nsigma"]
                     )[:CATALOG_MAX_LISTED]:
        catalog["lines"].append({
            "class": "single_block", "expno": ft["expno"],
            "window_hz": ft["window_hz"],
            "absolute_hz": ft["absolute_hz"],
            "excess_nsigma": ft["excess_nsigma"]})
    catalog["n_lines_listed"] = len(catalog["lines"])
    return catalog


def subvirial_pass(bundle, exps, f0_local, w_ref, fs_default):
    """Native-resolution mean periodogram of one noise block + narrow
    candidates. Infrastructure pass -- no chirp templates yet. `exps` as
    for analyze_noise_block (one experiment or a same-parameter group)."""
    if isinstance(exps, dict):
        exps = [exps]
    exp = exps[0]
    rows, acq, _sources, _skipped = read_noise_group(bundle, exps)
    if rows is None or rows.shape[0] == 0:
        return None
    fs = float(acq.get("SW_h", exp.get("sw_hz", fs_default)))
    n = int(rows.shape[1])
    if n < 4096:
        return None
    # truncate each row to the largest power of two: real console TDs
    # often carry large prime factors, which push numpy's FFT onto the
    # slow Bluestein path (minutes per row instead of seconds). The
    # resolution loss is < 2x and irrelevant for a candidate list.
    n = 1 << (n.bit_length() - 1)
    win = np.hanning(n)
    ps = np.zeros(n)
    for x in rows:
        ps += np.abs(np.fft.fftshift(np.fft.fft(x[:n] * win))) ** 2
    ps /= float(rows.shape[0])
    f = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / fs))
    df = fs / n
    # coarse baseline: chunked medians, interpolated
    chunk = max(1024, n // 2048)
    nb = n // chunk
    fb = np.array([np.mean(f[i * chunk:(i + 1) * chunk])
                   for i in range(nb)])
    bb = np.array([np.median(ps[i * chunk:(i + 1) * chunk])
                   for i in range(nb)])
    bb[bb <= 0] = np.min(bb[bb > 0]) if np.any(bb > 0) else 1.0
    base = np.interp(f, fb, bb)
    pn = ps / base
    edge = 0.45 * fs
    spin_tol = 3.0 * max(w_ref or 0.0, 5.0)
    core = ((np.abs(f) < edge) & (np.abs(f) > CATALOG_DC_EXCLUDE_HZ)
            & (np.abs(f - f0_local) > spin_tol))
    if not np.any(core):
        return {"expno": exp["expno"], "error": "empty analysis band"}
    sig = robust_sigma(pn[core])
    if not sig or not np.isfinite(sig):
        # degenerate spectrum (clipped/constant rows): without this
        # guard a zero sigma reaches divisions and puts Infinity/NaN
        # into report.json, which strict parsers reject (review F6)
        return {"expno": exp["expno"],
                "error": "degenerate spectrum (zero/NaN sigma)"}
    cands = []
    idx = np.where(core & (pn - 1.0 > SUBVIRIAL_NSIGMA * sig))[0]
    i = 0
    while i < len(idx) and len(cands) < 10 * SUBVIRIAL_MAX_LISTED:
        j = i
        while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
            j += 1
        seg = idx[i:j + 1]
        width = (f[seg[-1]] - f[seg[0]]) if len(seg) > 1 else df
        if width <= SUBVIRIAL_MAX_WIDTH_HZ:
            wts = pn[seg] - 1.0
            cands.append({
                "window_hz": float(np.sum(f[seg] * wts) / np.sum(wts)),
                "width_hz": float(width),
                "excess_nsigma": float((np.max(pn[seg]) - 1.0) / sig)})
        i = j + 1
    cands.sort(key=lambda c: -c["excess_nsigma"])
    return {"expno": exp["expno"], "resolution_hz": df,
            "n_rows": int(rows.shape[0]), "sigma_norm": float(sig),
            "n_candidates": len(cands),
            "candidates": cands[:SUBVIRIAL_MAX_LISTED],
            "note": (
                "native-resolution (%.3g Hz) incoherent mean periodogram "
                "of the headline noise block; candidates are narrow "
                "(<= %.1f Hz) excesses away from the spin line and DC. "
                "INFRASTRUCTURE PASS ONLY: no diurnal/annual chirp "
                "templates are applied yet, and a genuine sub-virial "
                "dark-matter line chirps by ~1 Hz per night from Earth's "
                "rotation -- treat every candidate as an instrumental "
                "spur hypothesis until the template search exists."
                % (df, SUBVIRIAL_MAX_WIDTH_HZ))}


def frequency_axis_sign_status(bundle, meta, f0_hz):
    """Whether the sign of this report's frequency axis (offset from the
    carrier) is established for the bundle's vendor.

    Bruker: the 2020 pipeline convention, validated on the EPFL data.
    Agilent/Varian: UNVERIFIED (vendor checklist item 2) -- neither the
    fid re/im sense relative to Bruker nor the tof-vs-o1 sign has been
    established, so every stated offset is known only up to sign: a
    two-way ambiguity in the line's absolute frequency of |f0|/f_carrier.
    Sign-agnostic quantities (line-to-floor contrast, widths, dip depth)
    need no caveat.
    """
    spec = meta.get("spectrometer") or {}
    f_mhz = spec.get("observe_freq_mhz") or spec.get("h1_freq_mhz")
    out = {"vendor": bundle.vendor,
           "verified": bundle.vendor != "agilent"}
    if out["verified"]:
        out["note"] = ("frequency-axis sign: 2020 pipeline (Bruker) "
                       "convention")
        return out
    ppm = (abs(float(f0_hz)) / float(f_mhz)) if (f_mhz and f0_hz) else None
    out["two_way_ambiguity_ppm"] = ppm
    out["note"] = (
        "Agilent/Varian data: the sign of this frequency axis relative to "
        "the Bruker convention (and of tof relative to Bruker o1) is "
        "UNVERIFIED -- vendor checklist item 2. Offsets are stated as "
        "the analysis found them; each is known only up to sign%s. "
        "Line-to-floor contrast, linewidths and dip depth are "
        "sign-agnostic and unaffected."
        % ((" (|offset| %.1f Hz: a two-way ambiguity of +/-%.2f ppm in "
            "the line's absolute frequency at %.1f MHz)"
            % (abs(float(f0_hz)), ppm, float(f_mhz))) if ppm else ""))
    return out


def axion_mass_bookkeeping(meta, sign_status=None):
    """Per-site mass coordinate and coupling-conversion factors for the
    downstream (coordinator-side) limit pipeline. h = 4.135667696e-15
    eV s: 1 MHz of carrier = 4.135667696e-3 ueV of axion mass."""
    spec = meta.get("spectrometer") or {}
    f_mhz = spec.get("observe_freq_mhz") or spec.get("h1_freq_mhz")
    if not f_mhz:
        return None
    m_uev = float(f_mhz) * 4.135667696e-3
    m_gev = m_uev * 1e-15
    out = {
        "observe_freq_mhz": float(f_mhz),
        "axion_mass_coordinate_uev": m_uev,
        "axial_vector_conversion_gev": m_gev * 1e-3,
        "note": (
            "mass coordinate of this session's carrier; the same "
            "candidate lines and limits reinterpret to axial-vector "
            "dark matter via g_A = g_aNN[GeV^-1] * (%.3e GeV) -- the "
            "factor is m_a * v with v = 1e-3 c. No velocity "
            "suppression applies to the axial-vector coupling, which "
            "is why the identical data are 3 orders of magnitude "
            "more constraining there (see the network science "
            "roadmap)." % (m_gev * 1e-3))}
    if sign_status and not sign_status.get("verified"):
        ppm = sign_status.get("two_way_ambiguity_ppm")
        out["offset_sign_caveat"] = (
            "line offsets from this carrier are known only up to SIGN "
            "(Agilent/Varian axis convention unverified, vendor checklist "
            "item 2)%s; the mass coordinate of the carrier itself is "
            "unaffected, but any candidate line's mass point is two-valued "
            "until the sign is established"
            % ((" -- +/-%.2f ppm for the spin line" % ppm) if ppm else ""))
    return out


# ============================================================================
# Axion-coupling exclusion (worst-case, this session)
#
# The 2020 pilot's construction (Spin_Noise_2020/extracted/
# compute_axion_limit.py, paper Sec. 'Worst-case construction') with every
# input taken from THIS bundle and every systematic at its limit-weakening
# extreme. Units: P in mean-square counts^2 (the Welch PSD is density
# scaled, Int PSD dnu = <|x|^2>), kappa*M0 in counts (the FID amplitude a
# full 90-degree tip would give at the noise block's receiver gain), so
# P_sig = (kappa M0)^2 <xi^2> exactly as in the pilot. The result is a
# worst-case, unpublished number many orders of magnitude above the
# astrophysical bounds on the same coupling.
# ============================================================================

def p90_power_db(v, data_format):
    """(dB, note) of calibration.p90_power_db_or_w on the vendor's own
    power scale, or (None, why). The schema types the field 'number in
    dB or watts', so only an explicit unit resolves it: '56 dB (tpwr)'
    -> 56.0; '0.5 W' -> -10 log10(0.5) = +3.01 dB of attenuation on the
    Bruker PL scale, and unresolvable against an Agilent tpwr (a coarse
    attenuator setting, not a watts scale); a bare number is ambiguous
    and refused rather than read as dB."""
    if v is None or isinstance(v, bool):
        return None, "calibration.p90_power_db_or_w absent"
    if isinstance(v, (int, float)):
        return None, ("calibration.p90_power_db_or_w is a bare number "
                      "(%g): dB or watts by schema, not read" % v)
    m = re.match(r"\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*(dB|W|watts?)\b",
                 str(v), re.I)
    if not m:
        return None, ("calibration.p90_power_db_or_w '%s' carries no dB "
                      "or W value" % v)
    num, unit = float(m.group(1)), m.group(2).lower()
    if unit == "db":
        return num, "p90 power %.3g dB" % num
    if data_format == "agilent":
        return None, ("calibration.p90_power_db_or_w '%s' is in watts, "
                      "not comparable to the tpwr scale" % v)
    if num <= 0:
        return None, "calibration.p90_power_db_or_w '%s' is not a power" % v
    db = -10.0 * math.log10(num)
    return db, "p90 power %.3g W = %.3g dB attenuation (PL scale)" % (num, db)


def t_quantile_one_sided(p, dof):
    """Student-t quantile t with P(T <= t) = p at `dof` degrees of freedom,
    by trapezoidal integration of the density (numpy-only; 1.4398 at
    p = 0.9, dof = 6 -- the pilot's t_90 for seven records)."""
    dof = max(int(dof), 1)
    t = np.linspace(0.0, 60.0, 600001)
    logc = (math.lgamma((dof + 1) / 2.0) - math.lgamma(dof / 2.0)
            - 0.5 * math.log(dof * math.pi))
    pdf = np.exp(logc - (dof + 1) / 2.0 * np.log1p(t * t / dof))
    cdf = 0.5 + np.concatenate(
        [[0.0], np.cumsum(0.5 * (pdf[1:] + pdf[:-1]) * np.diff(t))])
    return float(np.interp(p, cdf, t))


def alp_lineshape(grid_hz, nu_a_hz, wind_parallel, nsamp=EXCL_MC_SAMPLES,
                  seed=EXCL_MC_SEED):
    """Monte-Carlo v_perp^2-weighted SHM lineshape lam(nu) on grid_hz
    (offsets above nu_a, in 1/Hz) with Int lam dnu = <v_perp^2/c^2>. z is
    along B0: wind_parallel puts the lab velocity along B0, so only the
    halo dispersion drives the spins (the worst case). Returns
    (lam, <v_perp^2/c^2>)."""
    rng = np.random.default_rng(seed)
    v = rng.normal(0.0, SHM_V0_KMS / math.sqrt(2.0), size=(nsamp, 3))
    v = v[np.einsum("ij,ij->i", v, v) < SHM_VESC_KMS ** 2]
    v[:, 2 if wind_parallel else 0] += SHM_VLAB_KMS
    v2 = np.einsum("ij,ij->i", v, v)
    vperp2 = v[:, 0] ** 2 + v[:, 1] ** 2
    dnu = nu_a_hz * v2 / (2.0 * C_KMS ** 2)
    step = grid_hz[1] - grid_hz[0]
    edges = np.concatenate([grid_hz - 0.5 * step, [grid_hz[-1] + 0.5 * step]])
    w, _ = np.histogram(dnu, bins=edges, weights=vperp2 / C_KMS ** 2)
    return w / v.shape[0] / step, float(np.mean(vperp2) / C_KMS ** 2)


def reference_tip_deg(ref, cal):
    """(tip_deg, basis) of one small-flip reference from its own pulse
    record and the bundle's p90 calibration; (None, why) when unresolved.

    Agilent/Varian: pw and tpwr from procpar (tpwr in dB, larger = more
    power). Bruker: P1 and PL1 from acqus (PL in dB of attenuation,
    larger = less power); when the power levels do not resolve, the
    protocol's declared small-flip tip (calibration.rg_ladder[].tip_deg
    -- the orchestrator sets P1 = P90 at +39.08 dB for the ladder) scaled
    by the reference's own P1/P90 is used, and failing that the
    pulse-length ratio alone."""
    p90 = cal.get("p90_us")
    p90 = float(p90) if isinstance(p90, (int, float)) and p90 > 0 else None
    p90_db, p90_note = p90_power_db(cal.get("p90_power_db_or_w"),
                                    ref.get("data_format"))
    if ref.get("data_format") == "agilent":
        pw = ref.get("pw_us")
        if not pw or not p90:
            return None, "procpar pw or calibration.p90_us missing"
        tip = 90.0 * pw / p90
        basis = "90 deg x pw %.4g us / p90 %.4g us" % (pw, p90)
        tpwr = ref.get("tpwr_db")
        if tpwr is not None and p90_db is not None and tpwr != p90_db:
            tip *= 10.0 ** ((tpwr - p90_db) / 20.0)
            basis += (" x 10^((tpwr %.3g dB - p90 power %.3g dB)/20)"
                      % (tpwr, p90_db))
        elif tpwr is not None and p90_db is None:
            basis += (" --- %s, tpwr %.3g dB ASSUMED equal to the p90 "
                      "calibration power" % (p90_note, tpwr))
        return tip, basis
    p1, pl1 = ref.get("p1_us"), ref.get("pl1_db")
    if p1 and p90 and pl1 is not None and p90_db is not None:
        return (90.0 * p1 / p90 * 10.0 ** (-(pl1 - p90_db) / 20.0),
                "90 deg x P1 %.4g us / P90 %.4g us x 10^(-(PL1 %.3g dB - "
                "P90 power %.3g dB)/20)" % (p1, p90, pl1, p90_db))
    tips = [r.get("tip_deg") for r in (cal.get("rg_ladder") or [])
            if isinstance(r.get("tip_deg"), (int, float))
            and r.get("tip_deg") > 0]
    if tips:
        tip = float(tips[0])
        basis = ("calibration.rg_ladder tip_deg %.3g deg (the protocol's "
                 "small flip, P1 = P90 at +39.08 dB attenuation) with the "
                 "reference ASSUMED at the ladder's power level --- %s"
                 % (tips[0], p90_note if pl1 is None else
                    "acqus PL1 %.3g dB not comparable: %s" % (pl1, p90_note)))
        if p1 and p90 and abs(p1 / p90 - 1.0) > 1e-6:
            tip *= p1 / p90
            basis += " --- x P1 %.4g us / P90 %.4g us" % (p1, p90)
        elif p1 and p90:
            basis += " --- P1 = P90 = %.4g us" % p90
        return tip, basis
    if p1 and p90:
        return (90.0 * p1 / p90,
                "90 deg x P1 %.4g us / P90 %.4g us from pulse lengths "
                "only --- power level unresolved (%s)" % (p1, p90, p90_note))
    return None, "no pulse record (acqus P1, calibration.p90_us) for a tip"


def _power_txt(db):
    return ("%.3g dB" % db) if isinstance(db, (int, float)) else "unrecorded"


def _d_cal_text(d_cal, ladder_factor=None):
    """The D_cal actually used: 'D_cal 4.60', or with gain-bridged
    references 'D_cal 4.80 = pilot envelope 4.6 x ladder power envelope
    1.044' --- the pilot value named as provenance."""
    if ladder_factor is None:
        return "D_cal %.2f" % d_cal
    return ("D_cal %.2f = pilot envelope %.1f x ladder power envelope %.3f"
            % (d_cal, D_CAL_PILOT, ladder_factor))


def _construction_text(d_cal_text, bridged=False):
    return ("worst-case, the 2020 pilot's construction generalized: every "
            "systematic at its limit-weakening extreme --- %s (the 2020 "
            "pilot's calibration envelope, reused unmeasured%s), DM wind "
            "parallel to B0, damping at the broader of the measured widths, "
            "the whole |PSD - baseline| line power (bump, dip and dispersive "
            "wing alike) attributed to a putative signal (no spin-noise "
            "subtraction), one-sided Student-t statistics. Unpublished, and "
            "far above astrophysical bounds."
            % (d_cal_text, " and inflated for the receiver-gain bridge"
               if bridged else ""))


def axion_exclusion(meta, noise_res, refs, detection, floor_cal, ladder,
                    f0_guess, f0_line, sign_status, sweep_present,
                    carrier_shift_hz=0.0):
    """Worst-case 90% CL exclusion on the ALP-proton gradient coupling
    g_ap from this session's headline noise block and small-flip
    references. f0_guess is the reference-anchored line offset from the
    carrier (the pulsed line, taken as the Larmor frequency as in the
    pilot), f0_line the headline block's seed (equal unless the headline
    is a field-stepped sweep block), carrier_shift_hz the headline
    block's window shift under a carrier-follow sweep. Returns the
    science.axion_exclusion dict; available False with a reason when an
    input is missing."""
    out = {"available": False,
           "construction": _construction_text(_d_cal_text(D_CAL_PILOT))}
    spec = meta.get("spectrometer") or {}
    cal = meta.get("calibration") or {}
    f_mhz = spec.get("observe_freq_mhz") or spec.get("h1_freq_mhz")
    if noise_res is None or not noise_res.get("_rows"):
        out["reason"] = "no readable noise block"
        return out
    if not f_mhz:
        out["reason"] = ("meta.json declares no observe/h1 frequency: no "
                         "mass coordinate")
        return out
    rows = noise_res["_rows"]
    n_rows = len(rows)
    if n_rows < 2:
        out["reason"] = ("one noise row: no record-to-record scatter for "
                         "the statistical term")
        return out
    readable = [r for r in refs if r.get("readable") and r.get("A0_counts")]
    if not readable:
        out["reason"] = ("no readable reference with an A0 "
                         "back-extrapolation: no transduction constant")
        return out

    # ---- kappa*M0 from the references at the noise block's gain
    rg_noise = float(noise_res.get("rg") or 0.0)
    if not rg_noise:
        out["reason"] = ("noise block receiver gain unrecorded: kappa*M0 "
                         "cannot be referred to the noise gain")
        return out
    same = [r for r in readable if r.get("rg")
            and abs(r["rg"] / rg_noise - 1.0) <= 0.005]
    used = same or readable
    bridged = not same
    rg_pow = (ladder.get("max_abs_power_deviation")
              if ladder.get("available") else RG_POWER_ENVELOPE_UNTESTED)
    per_ref, unresolved = [], []
    for r in used:
        if bridged and not r.get("rg"):
            unresolved.append("expno %s: receiver gain unrecorded, no gain "
                              "bridge to the noise rg %.4g"
                              % (r.get("expno"), rg_noise))
            continue
        tip, basis = reference_tip_deg(r, cal)
        if tip is None or not (0.0 < tip < 90.0):
            unresolved.append("expno %s: %s" % (r.get("expno"), basis))
            continue
        bridge = (rg_noise / float(r["rg"])) if bridged else 1.0
        per_ref.append({
            "expno": r.get("expno"), "role": r.get("role"),
            "rg": r.get("rg"), "A0_counts": float(r["A0_counts"]),
            "pulse_us": r.get("pw_us", r.get("p1_us")),
            "power_db": r.get("tpwr_db", r.get("pl1_db")),
            "tip_deg": float(tip), "tip_basis": basis,
            "rg_bridge_amplitude": float(bridge),
            "kappa_M0_counts": float(r["A0_counts"]
                                     / math.sin(math.radians(tip)) * bridge)})
    if not per_ref:
        out["reason"] = "reference tip angle unresolved (%s)" % " --- ".join(
            unresolved)
        return out
    pulses = sorted(set((p["pulse_us"], p["power_db"]) for p in per_ref),
                    key=lambda t: [(v is None, 0.0 if v is None else float(v))
                                   for v in t])
    matched = len(pulses) == 1
    kms = [p["kappa_M0_counts"] for p in per_ref]
    if matched:
        km0 = float(np.mean(kms))
        km0_note = ("mean of %d reference(s) at one pulse (%s us, power "
                    "%s), tip %.3g deg" % (
                        len(per_ref), pulses[0][0], _power_txt(pulses[0][1]),
                        per_ref[0]["tip_deg"]))
    else:
        pick = per_ref[int(np.argmin(kms))]
        km0 = float(pick["kappa_M0_counts"])
        km0_note = (
            "references are NOT a matched pair (%s) --- each is used with "
            "its own tip and the SMALLER kappa*M0 (expno %s, %.4g counts) "
            "sets the limit, the weakest choice available"
            % (" and ".join("expno %s pulse %s us power %s tip %.3g deg -> "
                         "%.4g counts" % (p["expno"], p["pulse_us"],
                                          _power_txt(p["power_db"]),
                                          p["tip_deg"], p["kappa_M0_counts"])
                         for p in per_ref), pick["expno"], km0))
    d_cal, ladder_factor, bridge_note = D_CAL_PILOT, None, None
    if bridged:
        ladder_factor = 1.0 + rg_pow
        d_cal *= ladder_factor
        bridge_note = (
            "no reference at the noise block's receiver gain (noise rg "
            "%.4g, reference rg %s): kappa*M0 bridged by the amplitude "
            "gain ratio and D_cal inflated by the RG ladder's %s power "
            "envelope (x%.3f -> %.2f)"
            % (rg_noise, sorted(set(p["rg"] for p in per_ref)),
               ("measured %.1f%%" % (100 * rg_pow)) if ladder.get(
                   "available") else "untested +/-%.0f%%" % (100 * rg_pow),
               ladder_factor, d_cal))
    d_text = _d_cal_text(d_cal, ladder_factor)
    d_note = ("%s: the 2020 pilot's calibration envelope, reused unmeasured "
              "at this site%s" % (d_text, " and inflated for the "
                                          "receiver-gain bridge"
                                  if bridged else ""))
    if bridge_note:
        d_note += " --- " + bridge_note
    out["construction"] = _construction_text(d_text, bridged)
    transduction = {"kappa_M0_counts": km0, "basis": km0_note,
                    "references": per_ref, "matched_references": matched,
                    "rg_bridged": bridged, "rg_bridge_note": bridge_note,
                    "noise_rg": rg_noise, "unresolved": unresolved or None}

    # ---- allowed signal power P_90 from the co-added spectrum
    fit, fit_key = headline_fit(noise_res)
    w_noise = (float(fit["fwhm_hz"])
               if fit and fit.get("fwhm_hz") and fit["fwhm_hz"] > 0 else None)
    detected = bool(detection.get("detected"))
    f_center = (detection.get("line_center_hz")
                if detected and detection.get("line_center_hz") is not None
                else f0_line)
    half = max(EXCL_WINDOW_MIN_HZ, EXCL_WINDOW_FWHM_MULT * (w_noise or 0.0))
    f = rows[0]["f"]
    df = float(rows[0]["df"])
    win = np.abs(f - f_center) <= half
    exc = []
    for rr in rows:
        e = (rr["pnorm"] - 1.0) * rr["base"]
        if rr["f"].shape != f.shape or rr["f"][0] != f[0]:
            e = np.interp(f, rr["f"], e)
        exc.append(e[win])
    exc = np.array(exc)
    mean_exc = exc.mean(axis=0)
    p_pos = float(np.sum(np.clip(mean_exc, 0.0, None)) * df)
    p_net = float(np.sum(mean_exc) * df)
    p_abs = float(np.sum(np.abs(mean_exc)) * df)
    # a Lorentzian of FWHM w keeps (2/pi) atan(2 half / w) of its power
    # inside |f - f0| <= half; the line power is referred to the full line
    win_frac = ((2.0 / math.pi) * math.atan(2.0 * half / w_noise)
                if w_noise else 1.0)
    p_line = p_abs / win_frac
    per_row_p = exc.sum(axis=1) * df
    sigma_stat = float(np.std(per_row_p, ddof=1) / math.sqrt(n_rows))
    sigma_tot = math.sqrt(sigma_stat ** 2
                          + (ESTIMATOR_BANDWIDTH_REL * p_line) ** 2)
    t90 = t_quantile_one_sided(0.90, n_rows - 1)
    p90 = p_line + t90 * sigma_tot
    floor = floor_cal.get("noise_floor_counts2perhz_at_noise_rg")
    if not floor:
        floor = float(np.mean([np.median(rr["base"][win]) for rr in rows]))
    if detected and fit is not None:
        feature = "bump" if fit["amp_norm"] > 0 else "dip"
    else:
        feature = "null"
    signal_power = {
        "window_center_hz": float(f_center), "window_half_hz": float(half),
        "window_basis": ("max(%.0f Hz, %.0f x noise-line FWHM %s Hz) "
                         "around the %s"
                         % (EXCL_WINDOW_MIN_HZ, EXCL_WINDOW_FWHM_MULT,
                            fmt(w_noise, 3) if w_noise else "n/a",
                            "fitted noise-line center" if detected
                            else "reference-anchored line position")),
        "resolution_hz": df, "n_rows": n_rows,
        "noise_seconds": float(n_rows * (noise_res.get("row_seconds") or 0.0)),
        "floor_counts2perhz": float(floor),
        "P_positive_part_counts2": p_pos, "P_net_counts2": p_net,
        "P_absolute_excess_counts2": p_abs,
        "lorentzian_window_fraction": float(win_frac),
        "P_line_counts2": p_line,
        "sigma_stat_counts2": sigma_stat,
        "sigma_estimator_bandwidth_counts2": ESTIMATOR_BANDWIDTH_REL * p_line,
        "sigma_total_counts2": sigma_tot, "t90_one_sided": t90,
        "P_90_counts2": p90,
        "statistical_fraction_of_P90": (t90 * sigma_tot / p90) if p90 else None,
        "feature": feature,
        "basis": ("integral of |PSD - baseline| over the window (positive "
                  "and negative excess alike: a signal hides in a dip or a "
                  "dispersive wing as readily as it shows in a bump), "
                  "per-row Welch PSDs (density scaled, Int PSD dnu = mean "
                  "square) averaged at recorded frequencies, divided by the "
                  "Lorentzian fraction %.3f of the line inside the window, "
                  "plus t_90(n_rows - 1) x sigma with sigma = per-row "
                  "integrated-excess scatter / sqrt(n_rows) in quadrature "
                  "with the pilot's %.1f%% estimator-bandwidth term --- no "
                  "spin-noise subtraction"
                  % (win_frac, 100 * ESTIMATOR_BANDWIDTH_REL))}

    # ---- damping: the broader of the two measured widths, both as POWER
    # widths (a Lorentzian's magnitude-spectrum width is sqrt(3) x its
    # power width)
    w_ref_amp = [r.get("fwhm_amp_hz") for r in used if r.get("fwhm_amp_hz")]
    if not w_ref_amp:
        w_ref_amp = [r.get("fwhm_amp_hz") for r in readable
                     if r.get("fwhm_amp_hz")]
    w_ref_amp = float(np.mean(w_ref_amp)) if w_ref_amp else None
    w_ref_pow = (w_ref_amp / math.sqrt(3.0)) if w_ref_amp else None
    cands = [(w, b) for w, b in ((w_noise, "noise-line power FWHM"),
                                 (w_ref_pow, "reference magnitude FWHM / "
                                             "sqrt(3)")) if w]
    if not cands:
        out["reason"] = "neither a noise-line nor a reference linewidth"
        return out
    gamma_fwhm, gamma_basis = max(cands)
    gamma = math.pi * gamma_fwhm
    damping = {"noise_line_power_fwhm_hz": w_noise,
               "noise_line_fit_basis": fit_key,
               "reference_magnitude_fwhm_hz": w_ref_amp,
               "reference_power_equivalent_fwhm_hz": w_ref_pow,
               "fwhm_used_hz": float(gamma_fwhm), "basis": gamma_basis,
               "gamma_per_s": gamma,
               "note": "Gamma = pi x max(noise-line POWER FWHM, reference "
                       "MAGNITUDE FWHM / sqrt(3)) --- the two width "
                       "definitions are never mixed"}

    # ---- mass coordinate: nu_L = carrier + reference line offset
    carrier_hz = float(f_mhz) * 1e6 + float(carrier_shift_hz or 0.0)
    verified = bool(sign_status.get("verified", True))
    nu_plus = carrier_hz + f0_line
    nu_minus = carrier_hz - f0_line
    basis = "carrier + reference-anchored line offset (line_position_guess_hz"
    if abs(f0_line - f0_guess) > 1e-9:
        basis += " %+.1f Hz for the headline sweep step" % (f0_line - f0_guess)
    if carrier_shift_hz:
        basis += (", carrier window shifted %+.1f Hz for the headline "
                  "carrier-follow step" % carrier_shift_hz)
    line = {"carrier_mhz": float(f_mhz), "carrier_shift_hz": float(
                carrier_shift_hz or 0.0), "offset_hz": float(f0_line),
            "sign_verified": verified,
            "vendor": sign_status.get("vendor"),
            "nu_L_hz_nominal": nu_plus,
            "m_a_uev_nominal": nu_plus * EV_PER_HZ * 1e6,
            "basis": basis + ")"}
    if not verified:
        line["nu_L_hz_mirror"] = nu_minus
        line["m_a_uev_mirror"] = nu_minus * EV_PER_HZ * 1e6
        line["sign_note"] = (
            "frequency-axis sign UNVERIFIED for this vendor: the Larmor "
            "frequency is carrier +/- %.1f Hz, the mass label two-valued "
            "by %.3g ueV --- the best coupling is sign-independent, the "
            "excluded band is quoted as the INTERSECTION of the two sign "
            "hypotheses (robust) with the union noted"
            % (abs(f0_line), abs(nu_plus - nu_minus) * EV_PER_HZ * 1e6))

    # ---- scan nu_a - nu_L
    grid = np.arange(*EXCL_LINESHAPE_GRID_HZ)
    offsets = np.arange(*EXCL_SCAN_HZ)
    lam_w, vp_w = alp_lineshape(grid, nu_plus, True)
    lam_t, vp_t = alp_lineshape(grid, nu_plus, False)
    c_omega2 = 0.5 * RHO_DM_GEV_CM3 * HBARC_GEV_CM ** 3 * GEV_TO_RADS ** 2
    trapz = getattr(np, "trapezoid", None) or np.trapz
    resp = 1.0 / (gamma ** 2 + (2.0 * math.pi
                                * (offsets[:, None] + grid[None, :])) ** 2)
    xi2_w = c_omega2 * trapz(lam_w[None, :] * resp, grid, axis=1)
    xi2_t = c_omega2 * trapz(lam_t[None, :] * resp, grid, axis=1)
    g_w = np.sqrt(p90 * d_cal / (km0 ** 2 * xi2_w))
    g_t = np.sqrt(p90 / (km0 ** 2 * xi2_t))
    best = int(np.argmin(g_w))
    best_t = int(np.argmin(g_t))
    band = offsets[g_w < 10.0 * g_w[best]]
    band_off = [float(band.min()), float(band.max())]

    def _abs(nu_l):
        return [nu_l + band_off[0], nu_l + band_off[1]]

    bands_hz = {"nominal": _abs(nu_plus)}
    if not verified:
        bands_hz["mirror"] = _abs(nu_minus)
        lo = max(bands_hz["nominal"][0], bands_hz["mirror"][0])
        hi = min(bands_hz["nominal"][1], bands_hz["mirror"][1])
        bands_hz["intersection"] = [lo, hi] if lo <= hi else None
        bands_hz["union"] = [min(bands_hz["nominal"][0],
                                 bands_hz["mirror"][0]),
                             max(bands_hz["nominal"][1],
                                 bands_hz["mirror"][1])]
    bands_uev = {k: ([v[0] * EV_PER_HZ * 1e6, v[1] * EV_PER_HZ * 1e6]
                     if v else None) for k, v in bands_hz.items()}
    result = {
        "g90_worst_best_gev_inv": float(g_w[best]),
        "offset_at_best_hz": float(offsets[best]),
        "m_a_at_best_uev": float((nu_plus + offsets[best]) * EV_PER_HZ * 1e6),
        "band_10x_offset_hz": band_off,
        "band_10x_hz_absolute": bands_hz,
        "band_10x_uev": bands_uev,
        "g90_nominal_best_gev_inv": float(g_t[best_t]),
        "offset_at_best_nominal_hz": float(offsets[best_t]),
        "ratio_to_sn1987a_bound": float(g_w[best] / SN1987A_GAP_GEV_INV),
        "allowed_xi2": p90 / km0 ** 2,
    }
    if not verified:
        result["m_a_at_best_mirror_uev"] = float(
            (nu_minus + offsets[best]) * EV_PER_HZ * 1e6)
    halo = {"rho_dm_gev_cm3": RHO_DM_GEV_CM3, "v0_kms": SHM_V0_KMS,
            "v_lab_kms": SHM_VLAB_KMS, "v_esc_kms": SHM_VESC_KMS,
            "vperp2_over_c2_worst": vp_w, "vperp2_over_c2_nominal": vp_t,
            "lineshape": "Monte-Carlo v_perp^2-weighted SHM, %d samples, "
                         "seed %d, %.0f Hz bins to %.0f Hz above nu_a"
                         % (EXCL_MC_SAMPLES, EXCL_MC_SEED,
                            EXCL_LINESHAPE_GRID_HZ[2],
                            EXCL_LINESHAPE_GRID_HZ[1]),
            "scan": "nu_a - nu_L from %+.0f to %+.0f Hz in %.0f Hz steps"
                    % EXCL_SCAN_HZ,
            "nominal_construction": "D_cal 1, wind perpendicular to B0, "
                                    "same Gamma"}

    stat_pct = 100.0 * (t90 * sigma_tot / p90) if p90 else 0.0
    honesty = [
        "Worst-case construction (2020 pilot): %s, wind parallel "
        "to B0, Gamma at the broader measured width (%s, %.1f Hz), the "
        "whole |PSD - baseline| line power (%.3g counts^2: positive part "
        "%.3g, negative part %.3g, over the Lorentzian window fraction "
        "%.3f) attributed to a putative axion signal. UNPUBLISHED --- "
        "%.1e times above the SN1987A cooling bound of %.1e GeV^-1 on the "
        "same coupling."
        % (d_text, gamma_basis, gamma_fwhm, p_line, p_pos, p_abs - p_pos,
           win_frac, result["ratio_to_sn1987a_bound"], SN1987A_GAP_GEV_INV),
        "In this construction the limit does NOT improve with more "
        "measurement time --- its floor is the spin-noise line itself "
        "(P_90 = %.3g counts^2, of which the statistical term is %.1f%%) "
        "--- only a calibrated spin-noise subtraction turns time into "
        "sensitivity." % (p90, stat_pct),
        d_note + " --- a calibrated decomposition (RG linearity x A0/window "
                 "pairing x flip angle) can replace it through "
                 "calibration_derating.D_cal.",
    ]
    if feature == "null":
        honesty.append(
            "No significant spin-noise line in this block: P_90 is "
            "statistics-limited --- the |excess| power %.3g counts^2 is "
            "the residual scatter's absolute integral (%.1f x its per-row "
            "sigma %.3g counts^2), not a line, plus t_90 x sigma = %.3g "
            "counts^2 --- and shrinks with more rows until a line appears. "
            "The time-independence above is the regime every sensitive "
            "session ends in."
            % (p_abs, (p_abs / sigma_stat) if sigma_stat else 0.0,
               sigma_stat, t90 * sigma_tot))
    if feature == "dip":
        honesty.append(
            "The line is an absorption DIP: an axion signal adds power and "
            "is degenerate with a shallower dip, so the signal budget is "
            "the dip's full |power| below baseline (%.3g counts^2) plus "
            "the positive excess (%.3g counts^2) --- the same-|line| dip "
            "and bump give the same bound. A true dip deeper than the "
            "observed one would hide more signal than this budget, and no "
            "construction without a calibrated dip prediction bounds that."
            % (p_abs - p_pos, p_pos))
    if not verified:
        honesty.append(line["sign_note"])
    if not matched:
        honesty.append("Unmatched references: " + km0_note)
    if bridge_note:
        honesty.append("Receiver-gain bridge: " + bridge_note)
    if unresolved:
        honesty.append("Reference(s) left out of kappa*M0: %s"
                       % " --- ".join(unresolved))
    if sweep_present:
        honesty.append("Sweep steps not covered: the exclusion is computed "
                       "for the headline block only, the field-stepped "
                       "noise_sweep blocks (other carriers) are out of "
                       "scope for this card.")
        out["sweep_note"] = "sweep steps not covered"

    out.update({
        "available": True,
        "confidence_level": 0.90,
        "line": line, "transduction": transduction,
        "signal_power": signal_power, "damping": damping,
        "calibration_derating": {"D_cal": d_cal, "D_cal_pilot": D_CAL_PILOT,
                                 "ladder_power_envelope_factor": ladder_factor,
                                 "basis": d_note},
        "halo": halo, "result": result,
        "curve": {"nu_a_minus_nu_L_hz": offsets.tolist(),
                  "m_a_ev": ((nu_plus + offsets) * EV_PER_HZ).tolist(),
                  "g90_worst": g_w.tolist(),
                  "g90_nominal": g_t.tolist()},
        "honesty": honesty,
    })
    if not verified:
        out["curve"]["m_a_ev_mirror"] = ((nu_minus + offsets)
                                         * EV_PER_HZ).tolist()
    return out


def analyze_rg_ladder(bundle, meta, f0_guess, fs_default):
    """Amplitude linearity across the RG ladder (2020's untested link)."""
    ladder_meta = meta.get("calibration", {}).get("rg_ladder", [])
    exps = {e["expno"]: e for e in meta.get("experiments", [])
            if e.get("role") == "rg_ladder"}
    rungs = []
    unreadable = []
    rg_corrected = []
    grpdly_assumed = []
    for entry in ladder_meta:
        expno = entry.get("expno")
        if expno not in exps:
            continue
        rows, acq = bundle.read_rows(expno, exps[expno])
        if rows is None:
            unreadable.append({"expno": expno,
                               "why": bundle.read_errors.get(
                                   int(expno), "raw data unreadable")})
            continue
        fs = float(acq.get("SW_h", exps[expno].get("sw_hz", fs_default)))
        g = group_delay_points(acq)
        if acq.get("_vendor") != "agilent" \
                and float(acq.get("GRPDLY", 0) or 0) <= 0:
            grpdly_assumed.append(expno)
        x = rows[0]
        start = g + 12
        nfft = 2 ** int(math.floor(math.log(max(x.size - start, 256), 2)))
        seg = x[start:start + nfft]
        spec = np.abs(np.fft.fftshift(np.fft.fft(seg - np.mean(seg))))
        fax = np.fft.fftshift(np.fft.fftfreq(nfft, d=1.0 / fs))
        near = np.abs(fax - f0_guess) < 300.0
        if not near.any():
            near = np.abs(fax) < EDGE_FRAC * fs
        off = (np.abs(fax) < EDGE_FRAC * fs) & (np.abs(fax - f0_guess) > 400.0)
        amp = float(np.max(spec[near]) - np.median(spec[off]))
        rung = {"expno": expno, "rg": float(entry.get("rg", 0)),
                "amplitude_counts": amp, "n_rows": int(rows.shape[0]),
                "n_points_complex": int(rows.shape[1]),
                "group_delay_points_used": g}
        # the gain the receiver actually applied is the one its own
        # parameter file records; the declared ladder step can differ
        # when the console rounds a requested gain (Agilent DD2: integer
        # dB only)
        rg_file = acq.get("RG")
        if isinstance(rg_file, (int, float)) and rg_file > 0 and rung["rg"] \
                and abs(rg_file / rung["rg"] - 1.0) > 0.005:
            rung["rg_declared_in_ladder"] = rung["rg"]
            rung["rg"] = float(rg_file)
            if acq.get("RG_DB") is not None:
                rung["rx_gain_db_recorded"] = acq["RG_DB"]
            rg_corrected.append(expno)
        rungs.append(rung)
    if len(rungs) < 2:
        return {"available": False, "n_rungs_with_data": len(rungs),
                "rungs": rungs, "unreadable": unreadable,
                "note": "fewer than 2 ladder acquisitions with readable data; "
                        "receiver-gain linearity UNTESTED for this bundle "
                        "(the 2020 pilot's largest unverified systematic)"
                        + ("; unreadable: %s" % "; ".join(
                            "expno %s -- %s" % (u["expno"], u["why"])
                            for u in unreadable) if unreadable else "")}
    rg = np.array([r["rg"] for r in rungs])
    am = np.array([r["amplitude_counts"] for r in rungs])
    s = float(np.sum(am * rg) / np.sum(rg * rg))     # best line through origin
    dev = am / (s * rg) - 1.0
    for r, d in zip(rungs, dev):
        r["fractional_deviation"] = float(d)
    out = {"available": True, "rungs": rungs,
           "slope_counts_per_rg": s,
           "max_abs_fractional_deviation": float(np.max(np.abs(dev))),
           "max_abs_power_deviation": float(2.0 * np.max(np.abs(dev))),
           "note": "amplitude vs RG fitted through the origin; deviation is "
                   "per-rung amplitude / (slope*RG) - 1"}
    if unreadable:
        out["unreadable"] = unreadable
        out["note"] += ("; rung(s) excluded as unreadable: %s" % "; ".join(
            "expno %s -- %s" % (u["expno"], u["why"]) for u in unreadable))
    if rg_corrected:
        out["rg_source_note"] = (
            "rung(s) %s: the receiver gain in the experiment's own "
            "parameter file differs from the value declared in "
            "calibration.rg_ladder (requested gain rounded by the console?); "
            "the recorded gain is used for the linearity fit and the "
            "declared one is kept as rg_declared_in_ladder" % rg_corrected)
    if grpdly_assumed:
        out["grpdly_note"] = (
            "rung(s) %s: acqus states no positive GRPDLY (TopSpin 2.x/3.0 "
            "leave it to a DECIM/DSPFVS lookup), so the %d-point group "
            "delay of the 2020 consoles was assumed for the FID start, as "
            "for the references" % (grpdly_assumed, GRPDLY_DEFAULT))
    return out


# ============================================================================
# Clock audit (schema 1.2): console-clock offset from wall-vs-OCXO elapsed
# ============================================================================

# The expectation refinement derives each block's OCXO-implied duration
# from the pulse-program text TopSpin stored in that expno (bundled by
# the run script's copy_tree) plus the acqus parameters -- NOT from a
# name-keyed table of assumed structures: the text is the record of what
# the pulse programmer actually executed. The parser is deliberately
# conservative: any statement it does not recognize, any ambiguous loop
# structure, NS != 1, or DS != 0 makes it fall back to the recorded
# expectation, flagged per block. The wall/OCXO consistency gate then
# arbitrates empirically: a wrong timing model shows up as a wall
# mismatch and excludes the block loudly instead of biasing the fit.

_PP_LABEL = re.compile(r"^(\d+)\s+(.*)$")
_PP_GO = re.compile(r"^go=(\d+)$")
_PP_DELAY = re.compile(r"^d(\d+)$")
_PP_PULSE = re.compile(r"^p(\d+)$")
_PP_LITERAL = re.compile(r"^(\d+(?:\.\d+)?)(s|m|u|n)$")
_PP_UNIT_S = {"s": 1.0, "m": 1e-3, "u": 1e-6, "n": 1e-9}
# Zero-duration bookkeeping tokens: write/zero/loop control, phase
# references and phase-program definitions ('ph31' / 'ph31=0' /
# 'ph1 = 0 2'), loop targets and counts.
_PP_ZERO = re.compile(r"^(ze|zd|wr|if|mc|lo|to|times|exit|=|"
                      r"ph\d+(=.*)?|#\d*|td\d*|\d+|"
                      r"F\d\([A-Za-z0-9_]*\))$")


def _jcamp_array(acq, key):
    """A JCAMP array (e.g. D, P) as a list of floats, or []."""
    try:
        return [float(v) for v in str(acq.get(key, "")).split()]
    except (TypeError, ValueError):
        return []


def pp_timing_model(pp_text, rows):
    """Parse pulse-program text into (pre_terms, row_terms) timing lists.

    Terms are ('d', N) / ('p', N) / ('lit', seconds) / ('go',): the
    programmed delays, pulses, and acquisitions of one pass. row_terms
    execute once per row (the go-loop body, delimited by the go target
    label and the lo/mc line that jumps back to it); pre_terms execute
    once. Statements sharing a line with a delay run concurrently with
    it (the 'd1 wr #0 if #0 ze' idiom), so a line's duration is its
    single duration token. Returns (None, None, reason) when the text
    cannot be modeled with certainty -- unknown statement, two durations
    on one line, no go, or a missing loop when rows > 1.
    """
    lines, labels = [], {}
    for raw in pp_text.splitlines():
        code = raw.split(";", 1)[0].strip()
        if not code or code.startswith("#"):    # blank / preprocessor
            continue
        m = _PP_LABEL.match(code)
        if m:
            labels[int(m.group(1))] = len(lines)
            code = m.group(2).strip()
        lines.append(code)

    per_line, go_idx = [], []
    for i, code in enumerate(lines):
        dur = None
        for t in code.split():
            if _PP_GO.match(t):
                term = ("go",)
            elif _PP_DELAY.match(t):
                term = ("d", int(_PP_DELAY.match(t).group(1)))
            elif _PP_PULSE.match(t):
                term = ("p", int(_PP_PULSE.match(t).group(1)))
            elif _PP_LITERAL.match(t):
                m = _PP_LITERAL.match(t)
                term = ("lit", float(m.group(1)) * _PP_UNIT_S[m.group(2)])
            elif _PP_ZERO.match(t):
                continue
            else:
                return None, None, "unrecognized statement '%s'" % t
            if dur is not None:
                return None, None, "two durations on one line: '%s'" % code
            dur = term
        if dur == ("go",):
            go_idx.append(i)
        per_line.append(dur)

    if len(go_idx) != 1:
        return None, None, ("expected exactly one go statement, found %d"
                            % len(go_idx))
    m = _PP_GO.match([t for t in lines[go_idx[0]].split()
                      if _PP_GO.match(t)][0])
    target = int(m.group(1))
    if target not in labels:
        return None, None, "go target label %d not found" % target
    start = labels[target]
    close = None
    for i in range(go_idx[0] + 1, len(lines)):
        toks = lines[i].split()
        if ("lo" in toks or "mc" in toks) and "to" in toks:
            try:
                if int(toks[toks.index("to") + 1]) == target:
                    close = i
                    break
            except (ValueError, IndexError):
                return None, None, "unparseable loop close: '%s'" % lines[i]
    if close is None:
        if rows > 1:
            return None, None, ("no loop back to label %d but rows > 1"
                                % target)
        close = len(lines) - 1
    pre = [d for d in per_line[:start] if d is not None]
    row = [d for d in per_line[start:close + 1] if d is not None]
    return pre, row, None


def refined_block_expectation(bundle, exps_by_no, block):
    """Recompute one clock-audit block's OCXO-implied duration from the
    bundle's own pulse-program text and acqus parameters.

    Everything the pulse programmer executes is OCXO-clocked, so every
    programmed delay, pulse, and the per-scan pre-acquisition delay DE
    belong on the predicted side of the fit; a per-scan shortfall eps
    biases the fitted offset by ~eps/(scan duration) -- ~3e-7 even for a
    stock 6.5 us DE on ~19 s rows (the real 2020 console used 59.4 us),
    which matters at requirement tiers ii/iii. The acquisition-side
    formula in spin_noise_run.py models only AQ and d1; here the bundled
    pulseprogram text supplies the actual per-row structure and acqus
    supplies the parameter values (D/P arrays, DE, TD, SW_h). Row counts
    come from meta['experiments'], with acqu2s TD as fallback. Anything
    the conservative parser cannot model keeps the recorded expectation,
    flagged per block; the wall/OCXO consistency gate arbitrates any
    residual model error empirically.

    Returns (refined_seconds or None, info dict for the report row).
    """
    rec = block.get("ocxo_expected_s")
    try:
        expno = int(block.get("expno"))
    except (TypeError, ValueError):
        return None, {"refine_note": "non-numeric expno"}
    fmt = bundle.experiment_format(expno)
    if fmt == "agilent":
        return None, {"refine_note": "Agilent/Varian experiment: no Bruker "
                                     "pulse-program text to model (seqfil "
                                     "names a compiled sequence); recorded "
                                     "expectation kept"}
    if fmt is None:
        return None, {"refine_note": "no acqus (and no procpar) for this "
                                     "expno; recorded expectation kept"}
    acq = bundle.acqus(expno)
    if not acq:
        return None, {"refine_note": "no acqus readable for this expno"}
    pp_text = bundle.pulseprogram_text(expno)
    if pp_text is None:
        return None, {"refine_note": "no pulseprogram text in the bundle "
                                     "for this expno"}
    exp_meta = exps_by_no.get(block.get("expno")) or {}
    try:
        td = int(acq["TD"])
        swh = float(acq["SW_h"])
        de_s = float(acq["DE"]) * 1e-6           # acqus stores DE in us
        ns = int(acq.get("NS", 1) or 1)
        ds = int(acq.get("DS", 0) or 0)
    except (KeyError, TypeError, ValueError):
        return None, {"refine_note": "acqus lacks TD/SW_h/DE"}
    try:
        rows = int(exp_meta.get("td1_rows", 0) or 0)
    except (TypeError, ValueError):
        rows = 0
    if rows <= 0:
        try:
            rows = int(bundle.acqu2s(expno).get("TD", 0) or 0)
        except (TypeError, ValueError):
            rows = 0
    if td <= 0 or swh <= 0 or rows <= 0 or de_s < 0:
        return None, {"refine_note": "non-physical acqus parameters or "
                                     "unknown row count"}
    if ns != 1:
        return None, {"refine_note": "NS=%d: per-scan loop semantics not "
                                     "modeled (network protocol is NS=1)"
                                     % ns}
    if ds != 0:
        return None, {"refine_note": "DS=%d dummy scans are not in the "
                                     "timing model" % ds}
    pre, row, why = pp_timing_model(pp_text, rows)
    if why is not None:
        return None, {"refine_note": "pulseprogram not modelable: %s" % why}
    aq = td / (2.0 * swh)
    dvals = _jcamp_array(acq, "D")
    pvals = _jcamp_array(acq, "P")

    def term_seconds(terms):
        total = 0.0
        for t in terms:
            if t[0] == "go":
                total += de_s + aq
            elif t[0] == "lit":
                total += t[1]
            elif t[0] == "d":
                if t[1] >= len(dvals):
                    raise IndexError("D%d not in acqus D array" % t[1])
                total += dvals[t[1]]
            elif t[0] == "p":
                if t[1] >= len(pvals):
                    raise IndexError("P%d not in acqus P array" % t[1])
                total += pvals[t[1]] * 1e-6      # P array is microseconds
        return total

    try:
        refined = term_seconds(pre) + rows * term_seconds(row)
    except IndexError as e:
        return None, {"refine_note": str(e)}
    if refined <= 0:
        return None, {"refine_note": "non-positive modeled duration"}
    info = {"de_s_per_scan": de_s, "scans": rows,
            "pulprog": str(acq.get("PULPROG", "")).strip("<> "),
            "pp_row_terms": len(row)}
    if isinstance(rec, (int, float)) and rec > 0:
        # informational only -- the wall gate is the arbiter; a large
        # value here usually means the acquisition-side formula missed
        # programmed delays that the pulse-program text reveals
        info["recorded_discrepancy"] = refined / rec - 1.0
    return refined, info


def analyze_clock_audit(meta, bundle=None):
    """Fit the fractional console-clock offset from the bundle's clock_audit
    blocks.

    Model: wall_i = c + (1 + delta) * ocxo_i across usable blocks, where
    ocxo_i is the OCXO-implied acquisition duration and wall_i the
    NTP-disciplined wall-clock elapsed time. The intercept c absorbs the
    constant per-block housekeeping overhead (disk writes etc., not
    OCXO-derived); delta is the fractional clock offset. Per-point sigma is
    sqrt(2)*NTP_JITTER_S (two timestamps per block). Blocks with no OCXO
    prediction (setup) or with wall/OCXO mismatch beyond
    CLOCK_CONSISTENCY_MAX (overhead-dominated) are excluded and listed.

    When `bundle` is given, each block's expected duration is re-derived
    from its bundled pulse-program text plus acqus via
    refined_block_expectation(); blocks that cannot be modeled with
    certainty keep the script-recorded value, flagged. When any block is
    refined, the recorded-model fit is reported alongside for comparison
    under 'recorded_model'.
    """
    out = {"available": False}
    ca = meta.get("clock_audit")
    if not isinstance(ca, dict) or not isinstance(ca.get("blocks"), list) \
            or not ca.get("blocks"):
        out["note"] = (
            "no clock audit in this bundle -- it predates the audit "
            "(recorded from script v0.2 / schema 1.2 onward). No penalty: "
            "the absolute-clock tiers are simply unassessed for this "
            "session.")
        return out
    out["available"] = True
    out["workstation_time_source"] = str(
        ca.get("workstation_time_source", "unknown"))
    out["ntp_status_raw"] = str(ca.get("ntp_status_raw", ""))[:2000]

    exps_by_no = {}
    for e in (meta.get("experiments") or []):
        if isinstance(e, dict) and e.get("expno") is not None:
            exps_by_no[e.get("expno")] = e

    usable, rows = [], []
    t0_ms, t1_ms = None, None
    for b in ca["blocks"]:
        try:
            ws = int(b["wall_start_ms"])
            we = int(b["wall_end_ms"])
            wall_s = (we - ws) / 1000.0
        except Exception:
            rows.append({"expno": b.get("expno"), "role": b.get("role"),
                         "used": False, "why": "unreadable wall times"})
            continue
        t0_ms = ws if t0_ms is None else min(t0_ms, ws)
        t1_ms = we if t1_ms is None else max(t1_ms, we)
        rec = b.get("ocxo_expected_s")
        exp, src, rinfo = rec, "script-recorded", {}
        if bundle is not None and isinstance(rec, (int, float)) and rec > 0:
            refined, rinfo = refined_block_expectation(bundle, exps_by_no, b)
            if refined is not None:
                exp, src = refined, "acqus-refined"
        row = {"expno": b.get("expno"), "role": b.get("role"),
               "wall_s": wall_s, "ocxo_expected_s": exp,
               "ocxo_recorded_s": rec, "expected_source": src}
        row.update(rinfo)
        if exp is None:
            row.update({"used": False,
                        "why": "no OCXO prediction (tune/shim/dialog time)"})
        elif not isinstance(exp, (int, float)) or exp <= 0 or wall_s <= 0:
            row.update({"used": False, "why": "non-positive duration"})
        elif abs(wall_s / exp - 1.0) > CLOCK_CONSISTENCY_MAX:
            row.update({"used": False,
                        "why": "wall/OCXO mismatch %.1f%% -- overhead-"
                               "dominated, unusable at the 1e-7 level"
                               % (100.0 * abs(wall_s / exp - 1.0))})
        else:
            row.update({"used": True,
                        "block_offset": wall_s / exp - 1.0})
            usable.append((float(exp), wall_s, float(rec),
                           src == "acqus-refined"))
        rows.append(row)
    out["blocks"] = rows
    out["n_blocks"] = len(rows)
    out["n_usable"] = len(usable)
    span_s = ((t1_ms - t0_ms) / 1000.0) if (t0_ms is not None) else 0.0
    out["session_span_s"] = span_s
    out["audited_ocxo_s"] = float(sum(p[0] for p in usable))

    if not usable:
        out["status"] = ("clock audit present but no usable blocks; no "
                         "offset fit possible")
        out["conclusive"] = False
        return out

    def _offset_fit(pairs):
        """wall = c + (1+delta)*ocxo over (ocxo_s, wall_s) pairs; errors
        from the KNOWN per-point sigma sqrt(2)*NTP_JITTER_S (two
        timestamps per block), not from residual scatter."""
        fx = np.array([p[0] for p in pairs])
        fy = np.array([p[1] for p in pairs])
        sigma_pt = math.sqrt(2.0) * NTP_JITTER_S
        f = {}
        if len(pairs) >= 3 and float(np.ptp(fx)) > 1.0:
            # OLS with intercept
            xb, yb = float(np.mean(fx)), float(np.mean(fy))
            sxx = float(np.sum((fx - xb) ** 2))
            slope = float(np.sum((fx - xb) * (fy - yb)) / sxx)
            f["delta"] = slope - 1.0
            f["err"] = sigma_pt / math.sqrt(sxx)
            f["model"] = ("wall = c + (1+delta)*ocxo, intercept absorbs "
                          "constant per-block overhead")
            f["intercept_s"] = yb - slope * xb
        else:
            # too few blocks for an intercept: inverse-variance mean of
            # the per-block ratios (overhead then biases delta high)
            d = fy / fx - 1.0
            w = (fx / sigma_pt) ** 2
            f["delta"] = float(np.sum(w * d) / np.sum(w))
            f["err"] = float(1.0 / math.sqrt(np.sum(w)))
            f["model"] = ("weighted mean of per-block ratios (too few "
                          "blocks for an intercept; per-block overhead "
                          "biases the offset high)")
        return f

    fit = _offset_fit([(p[0], p[1]) for p in usable])
    delta, delta_err = fit["delta"], fit["err"]
    out["fit_model"] = fit["model"]
    if "intercept_s" in fit:
        out["overhead_intercept_s"] = fit["intercept_s"]
    out["fractional_offset"] = float(delta)
    out["fractional_offset_err"] = float(delta_err)
    out["assumed_timestamp_jitter_s"] = NTP_JITTER_S

    n_refined = sum(1 for p in usable if p[3])
    tot_ocxo = sum(p[0] for p in usable)
    unref_ocxo = sum(p[0] for p in usable if not p[3])
    out["expectation_refinement"] = {
        "n_refined": n_refined,
        "n_recorded_only": len(usable) - n_refined,
        "unrefined_usable_ocxo_fraction":
            (unref_ocxo / tot_ocxo) if tot_ocxo > 0 else None,
        "note": ("expected durations re-derived from each block's bundled "
                 "pulse-program text plus acqus (every programmed delay, "
                 "pulse, and the per-scan pre-acquisition delay DE); the "
                 "acquisition-side formula models only AQ and d1, and a "
                 "per-scan shortfall biases the offset by "
                 "~shortfall/scan-duration"),
    }
    if n_refined and tot_ocxo > 0 and unref_ocxo / tot_ocxo > 0.05:
        out["expectation_refinement"]["caution"] = (
            "refinement is partial and the unrefined blocks carry %.0f%% "
            "of the audited OCXO time -- the fit mixes two duration "
            "models; treat fine-tier verdicts with care"
            % (100.0 * unref_ocxo / tot_ocxo))
    if n_refined:
        corr = [(p[0] - p[2]) / p[2] for p in usable if p[3] and p[2] > 0]
        if corr:
            out["expectation_refinement"]["median_fractional_correction"] \
                = float(np.median(np.array(corr)))
        rfit = _offset_fit([(p[2], p[1]) for p in usable])
        out["recorded_model"] = {
            "fractional_offset": float(rfit["delta"]),
            "fractional_offset_err": float(rfit["err"]),
            "fit_model": rfit["model"],
            "note": ("the same usable blocks fitted against the "
                     "acquisition-side (AQ+d1 only) expected durations, "
                     "for comparison; the headline fit uses the "
                     "pulse-program-derived durations"),
        }

    out["conclusive"] = bool(span_s >= CLOCK_MIN_SPAN_S)
    if not out["conclusive"]:
        out["status"] = ("clock audit inconclusive (short session): span "
                         "%.0f s < %.0f s; numbers below are reported but "
                         "should not be used for tier claims"
                         % (span_s, CLOCK_MIN_SPAN_S))
    else:
        out["status"] = ("fractional console-clock offset %.3g +/- %.3g "
                         "over a %.2f h session span"
                         % (delta, delta_err, span_s / 3600.0))

    bound = abs(delta) + delta_err
    out["offset_bound"] = float(bound)
    tiers = []
    for tid, name, req, note in CLOCK_TIERS:
        t = {"tier": tid, "name": name, "requirement": req, "note": note}
        if not out["conclusive"]:
            t["verdict"] = "unassessed (audit inconclusive)"
        elif delta_err > req:
            t["verdict"] = ("audit precision insufficient (+/-%.1g > %.1g "
                            "requirement) -- needs a longer session"
                            % (delta_err, req))
        elif bound <= req:
            t["verdict"] = "satisfied (|offset|+err = %.2g <= %.1g)" \
                % (bound, req)
        else:
            t["verdict"] = ("NOT satisfied: measured offset %.2g exceeds "
                            "the %.1g requirement" % (bound, req))
        tiers.append(t)
    out["tiers"] = tiers
    return out


def render_clock_audit_html(clock):
    """HTML fragment for the clock-audit section (used by both the science
    and the software-test report paths)."""
    parts = []
    A = parts.append
    A("<h2>Clock audit (console OCXO vs workstation NTP)</h2>"
      "<div class='card'>")
    if not clock.get("available"):
        A("<p class='muted'>%s</p></div>" % esc(clock.get("note", "")))
        return "".join(parts)
    A("<p class='small'>Acquisition durations derive from the console's "
      "OCXO master clock; the workstation wall clock is normally "
      "NTP-disciplined. Fitting wall-clock elapsed against OCXO-implied "
      "elapsed across the session's blocks measures the fractional "
      "console-clock offset with zero extra hardware. Precision scales as "
      "the %.0f ms timestamp jitter over the audited span.</p>"
      % (1000 * clock.get("assumed_timestamp_jitter_s", NTP_JITTER_S)))
    if clock.get("fractional_offset") is not None:
        cls = "" if clock.get("conclusive") else " class='warn'"
        A("<p%s><span class='big'>%+.3g &plusmn; %.2g</span> fractional "
          "console-clock offset <span class='small'>(%s)</span></p>"
          % (cls, clock["fractional_offset"], clock["fractional_offset_err"],
             esc(clock.get("fit_model", ""))))
    A("<p class='%s'>%s</p>" % ("small" if clock.get("conclusive")
                                else "warn", esc(clock.get("status", ""))))
    A("<p class='small'>session span %.2f h &middot; audited OCXO time "
      "%.0f s &middot; %d of %d blocks usable &middot; time source: "
      "<code>%s</code></p>"
      % (clock.get("session_span_s", 0) / 3600.0,
         clock.get("audited_ocxo_s", 0), clock.get("n_usable", 0),
         clock.get("n_blocks", 0),
         esc(clock.get("workstation_time_source", "unknown"))))
    er = clock.get("expectation_refinement") or {}
    if er.get("n_refined"):
        med = er.get("median_fractional_correction")
        rm = clock.get("recorded_model") or {}
        extra = ""
        if med is not None:
            extra += " &middot; median DE correction %+.2e" % med
        if rm.get("fractional_offset") is not None:
            extra += (" &middot; recorded-model fit for comparison: "
                      "%+.3g &plusmn; %.2g"
                      % (rm["fractional_offset"],
                         rm["fractional_offset_err"]))
        A("<p class='small'>expected durations re-derived from the "
          "bundled pulse-program text + acqus for %d of %d usable "
          "blocks%s</p>"
          % (er["n_refined"],
             er["n_refined"] + er.get("n_recorded_only", 0), extra))
        if er.get("caution"):
            A("<p class='warn'>%s</p>" % esc(er["caution"]))
    if clock.get("blocks"):
        A("<table><tr><th>expno</th><th>role</th><th>wall (s)</th>"
          "<th>OCXO expected (s)</th><th>expected source</th>"
          "<th>used</th><th>per-block offset</th></tr>")
        for b in clock["blocks"]:
            off = b.get("block_offset")
            src_html = esc(b.get("expected_source", ""))
            if b.get("refine_note"):
                src_html += (" <span class='small'>(%s)</span>"
                             % esc(b["refine_note"]))
            A("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
              "<td>%s</td><td>%s</td><td>%s</td></tr>"
              % (fmt(b.get("expno")), esc(b.get("role", "?")),
                 fmt(b.get("wall_s"), 6), fmt(b.get("ocxo_expected_s"), 6),
                 src_html,
                 "yes" if b.get("used") else
                 "no &mdash; %s" % esc(b.get("why", "")),
                 ("%+.2e" % off) if off is not None else "&mdash;"))
        A("</table>")
    if clock.get("tiers"):
        A("<p class='small'><b>Requirement tiers</b> (absolute-frequency "
          "use of this site's records):</p>")
        A("<table><tr><th>tier</th><th>requirement</th><th>fractional"
          "</th><th>verdict</th></tr>")
        for t in clock["tiers"]:
            v = t["verdict"]
            cls = "ok" if v.startswith("satisfied") else (
                "fail" if v.startswith("NOT") else "warn")
            A("<tr><td>%s</td><td>%s <span class='small'>(%s)</span></td>"
              "<td>%.1e</td><td class='%s'>%s</td></tr>"
              % (esc(t["tier"]), esc(t["name"]), esc(t["note"]),
                 t["requirement"], cls, esc(v)))
        A("</table>")
    if clock.get("ntp_status_raw"):
        A("<p class='small'>NTP status probe (verbatim):</p>"
          "<pre class='small' style='white-space:pre-wrap; overflow-x:auto'>"
          "<code>%s</code></pre>" % esc(clock["ntp_status_raw"][:600]))
    A("</div>")
    return "".join(parts)


def render_rdopt_html(ctx):
    """HTML fragment for the rd-optimize tuning scan (empty when the
    feature did not run). Falls back to the meta object so software-test
    reports show it too."""
    ro = ctx.get("rd_optimize")
    if not isinstance(ro, dict):
        ro = (ctx["meta"].get("calibration") or {}).get("rd_optimize")
    if not isinstance(ro, dict) or not ro.get("enabled"):
        return ""
    parts = []
    A = parts.append
    A("<h2>rd-optimize (probe-tuning scan)</h2><div class='card'>")
    A("<p class='small'>Small-flip FID envelope decay rate vs probe-"
      "tuning offset: the envelope decays at 1/T2* + lambda_r, so with "
      "the shim fixed the largest rate marks the strongest radiation "
      "damping. The session ran at the chosen offset.</p>")
    offs = ro.get("offsets_khz") or []
    rates = ro.get("decay_rates_per_s") or []
    amps = ro.get("amplitudes") or []
    exps = ro.get("scan_expnos") or []
    if offs:
        A("<table><tr><th>offset (kHz)</th><th>decay rate (1/s)</th>"
          "<th>amplitude (counts)</th><th>expno</th></tr>")
        for i in range(len(offs)):
            A("<tr><td>%+.0f</td><td>%s</td><td>%s</td><td>%s</td></tr>"
              % (offs[i],
                 fmt(rates[i] if i < len(rates) else None),
                 fmt(amps[i] if i < len(amps) else None, 0),
                 fmt(exps[i] if i < len(exps) else None)))
        A("</table>")
    A("<p><b>chosen offset: %+.0f kHz</b> <span class='small'>&mdash; "
      "%s</span></p>"
      % (ro.get("chosen_offset_khz") or 0.0, esc(ro.get("note", ""))))
    A("</div>")
    return "".join(parts)


def render_line_catalog_html(ctx):
    """HTML fragment for the v0.6 persistent-line catalog + sub-virial
    pass + mass bookkeeping (empty when none ran)."""
    cat = ctx.get("line_catalog")
    sub = ctx.get("subvirial")
    mb = ctx.get("mass_book")
    if not (cat or sub or mb):
        return ""
    parts = []
    A = parts.append
    A("<h2>Persistent-line catalog (dark-photon / spur groundwork)</h2>")
    if mb:
        A("<p>Axion-mass coordinate of this session: "
          "<b>%s &micro;eV</b> (carrier %s MHz). Axial-vector "
          "reinterpretation factor g<sub>A</sub>/g<sub>aNN</sub> = "
          "%.3e GeV.</p>"
          % (fmt(mb.get("axion_mass_coordinate_uev"), 6),
             fmt(mb.get("observe_freq_mhz"), 6),
             mb.get("axial_vector_conversion_gev") or 0.0))
        if mb.get("offset_sign_caveat"):
            A("<p class='warn small'>%s</p>" % esc(mb["offset_sign_caveat"]))
    if cat:
        A("<p class='note'>%s</p>" % esc(cat.get("note", "")))
        A("<table><tr><th>class</th><th>frame</th><th>center (Hz)</th>"
          "<th>blocks seen</th><th>max excess (&sigma;)</th></tr>")
        for ln in cat.get("lines", []):
            A("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
              "<td>%s</td></tr>"
              % (esc(ln.get("class", "")),
                 esc(str(ln.get("frame", ln.get("expno", "")))),
                 fmt(ln.get("center_hz", ln.get("window_hz",
                     ln.get("mean_window_hz"))), 1),
                 ln.get("n_blocks_seen", 1),
                 fmt(ln.get("max_excess_nsigma",
                            ln.get("excess_nsigma")), 1)))
        A("</table>")
    if sub and not sub.get("error"):
        A("<h3>Sub-virial pass (headline block, %s Hz resolution)</h3>"
          % fmt(sub.get("resolution_hz"), 4))
        A("<p class='note'>%s</p>" % esc(sub.get("note", "")))
        cands = sub.get("candidates") or []
        if cands:
            A("<table><tr><th>window offset (Hz)</th><th>width (Hz)</th>"
              "<th>excess (&sigma;)</th></tr>")
            for c in cands[:15]:
                A("<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
                  % (fmt(c["window_hz"], 2), fmt(c["width_hz"], 3),
                     fmt(c["excess_nsigma"], 1)))
            A("</table>")
        else:
            A("<p>No narrow candidates above %.0f&sigma;.</p>"
              % SUBVIRIAL_NSIGMA)
    elif sub and sub.get("error"):
        A("<p class='note'>sub-virial pass failed: %s</p>"
          % esc(sub["error"]))
    return "".join(parts)


def render_axion_exclusion_html(ctx):
    """HTML card for the per-session worst-case axion-coupling exclusion
    (empty for software-test reports)."""
    ex = ctx.get("axion_exclusion")
    if not isinstance(ex, dict):
        return ""
    parts = []
    A = parts.append
    A("<h2>Axion-coupling exclusion (worst-case, this session)</h2>"
      "<div class='card'>")
    if not ex.get("available"):
        A("<p class='warn'>Not computed: %s</p><p class='small'>%s</p></div>"
          % (esc(ex.get("reason", "")), esc(ex.get("construction", ""))))
        return "".join(parts)
    res, line, tr = ex["result"], ex["line"], ex["transduction"]
    sp, dm, dc = ex["signal_power"], ex["damping"], ex["calibration_derating"]
    A("<p><span class='big'>g<sub>ap</sub> &lt; %.3g GeV<sup>&minus;1</sup>"
      "</span> (90%% CL, worst case) at m<sub>a</sub> = %.7f &micro;eV%s, "
      "best at &nu;<sub>a</sub> &minus; &nu;<sub>L</sub> = %+.0f Hz.</p>"
      % (res["g90_worst_best_gev_inv"], res["m_a_at_best_uev"],
         (" <span class='warn'>(or %.7f &micro;eV under the mirror sign)"
          "</span>" % res["m_a_at_best_mirror_uev"])
         if "m_a_at_best_mirror_uev" in res else "",
         res["offset_at_best_hz"]))
    bu = res["band_10x_uev"]
    A("<p class='small'>Within 10&times; of the best coupling: "
      "&nu;<sub>a</sub> &minus; &nu;<sub>L</sub> in [%+.0f, %+.0f] Hz "
      "&mdash; %.7f to %.7f &micro;eV on the nominal (+offset) mass axis"
      % (res["band_10x_offset_hz"][0], res["band_10x_offset_hz"][1],
         bu["nominal"][0], bu["nominal"][1]))
    if "mirror" in bu:
        inter = bu.get("intersection")
        A(" &mdash; mirror sign %.7f to %.7f &micro;eV &mdash; <b>robust "
          "band (intersection) %s</b> &mdash; union %.7f to %.7f &micro;eV"
          % (bu["mirror"][0], bu["mirror"][1],
             ("%.7f to %.7f &micro;eV" % (inter[0], inter[1])) if inter
             else "EMPTY (the two sign hypotheses do not overlap)",
             bu["union"][0], bu["union"][1]))
    A(".</p>")
    A("<p class='small'>Nominal construction for comparison (D_cal 1, "
      "wind perpendicular to B<sub>0</sub>, same &Gamma;): g<sub>ap</sub> "
      "&lt; %.3g GeV<sup>&minus;1</sup> at %+.0f Hz. The worst-case "
      "number sits %.1e&times; above the SN1987A cooling bound "
      "(%.1e GeV<sup>&minus;1</sup>).</p>"
      % (res["g90_nominal_best_gev_inv"], res["offset_at_best_nominal_hz"],
         res["ratio_to_sn1987a_bound"], SN1987A_GAP_GEV_INV))
    A("<table><tr><th>input</th><th>value</th><th>from this session</th>"
      "</tr>")
    A("<tr><td>&nu;<sub>L</sub></td><td>%.3f MHz %+.1f Hz = %.7f &micro;eV"
      "%s</td><td>%s</td></tr>"
      % (line["carrier_mhz"], line["offset_hz"], line["m_a_uev_nominal"],
         "" if line["sign_verified"] else
         " <span class='warn'>(sign unverified: mirror %.7f &micro;eV)"
         "</span>" % line["m_a_uev_mirror"], esc(line["basis"])))
    A("<tr><td>&kappa;M<sub>0</sub></td><td>%.4g counts at noise RG %.4g"
      "</td><td>%s</td></tr>"
      % (tr["kappa_M0_counts"], tr["noise_rg"], esc(tr["basis"])))
    for p in tr["references"]:
        A("<tr><td class='small'>&nbsp;&nbsp;reference expno %s</td>"
          "<td class='small'>A0 %.5g counts, tip %.3g&deg;%s &rarr; "
          "&kappa;M<sub>0</sub> %.4g</td><td class='small'>%s</td></tr>"
          % (p["expno"], p["A0_counts"], p["tip_deg"],
             (" &times; gain bridge %.3g" % p["rg_bridge_amplitude"])
             if p["rg_bridge_amplitude"] != 1.0 else "",
             p["kappa_M0_counts"], esc(p["tip_basis"])))
    A("<tr><td>P<sub>90</sub></td><td>%.4g counts&sup2;</td><td>line power "
      "%.4g counts&sup2; = &int;|PSD &minus; baseline| %.4g counts&sup2; "
      "(positive part %.4g, net %.4g) over &plusmn;%.0f Hz around %.1f Hz "
      "/ Lorentzian window fraction %.3f, floor %.4g counts&sup2;/Hz "
      "&middot; t<sub>90</sub>(%d) = %.3f &times; &sigma; %.3g counts&sup2; "
      "(scatter %.3g, estimator bandwidth %.3g) &middot; feature: %s "
      "&middot; %d rows, %.0f s</td></tr>"
      % (sp["P_90_counts2"], sp["P_line_counts2"],
         sp["P_absolute_excess_counts2"], sp["P_positive_part_counts2"],
         sp["P_net_counts2"], sp["window_half_hz"], sp["window_center_hz"],
         sp["lorentzian_window_fraction"], sp["floor_counts2perhz"],
         sp["n_rows"] - 1, sp["t90_one_sided"], sp["sigma_total_counts2"],
         sp["sigma_stat_counts2"], sp["sigma_estimator_bandwidth_counts2"],
         esc(sp["feature"]), sp["n_rows"], sp["noise_seconds"]))
    A("<tr><td>&Gamma;</td><td>&pi; &times; %.2f Hz = %.1f s<sup>&minus;1"
      "</sup></td><td>%s won: noise-line POWER FWHM %s Hz (%s) vs "
      "reference MAGNITUDE FWHM %s Hz / &radic;3 = %s Hz</td></tr>"
      % (dm["fwhm_used_hz"], dm["gamma_per_s"], esc(dm["basis"]),
         fmt(dm["noise_line_power_fwhm_hz"], 3),
         esc(dm.get("noise_line_fit_basis") or "no fit"),
         fmt(dm["reference_magnitude_fwhm_hz"], 3),
         fmt(dm["reference_power_equivalent_fwhm_hz"], 3)))
    A("<tr><td>D<sub>cal</sub></td><td>%.2f</td><td>%s</td></tr>"
      % (dc["D_cal"], esc(dc["basis"])))
    A("</table>")
    A("<ul class='small'>")
    for h in ex.get("honesty", []):
        A("<li>%s</li>" % esc(h))
    A("</ul>")
    sc = ex.get("site_combined")
    if isinstance(sc, dict) and sc.get("combined"):
        c = sc["combined"]
        segs = c.get("band_10x_segments_uev") or [c["band_10x_uev"]]
        order = c.get("sign_hypotheses") or []
        step = (sc.get("curve") or {}).get("grid_step_hz") or 0.0
        A("<p><b>Site-combined (%d session%s, %.0f s of noise data)%s:</b> "
          "g<sub>ap</sub> &lt; %.3g GeV<sup>&minus;1</sup> at m<sub>a</sub> "
          "= %.7f &micro;eV, within-10&times; band around the best %.7f to "
          "%.7f &micro;eV (%d contiguous segment%s within 10&times; in "
          "total, set by %s) &mdash; %s.</p>"
          % (sc["n_sessions"], "" if sc["n_sessions"] == 1 else "s",
             c["total_noise_seconds"],
             ", sign-robust headline" if order else "",
             c["g90_worst_best_gev_inv"],
             c["m_a_at_best_uev"], c["band_10x_uev"][0], c["band_10x_uev"][1],
             len(segs), "" if len(segs) == 1 else "s",
             esc(c["session_setting_best"]),
             ("under each joint axis-sign hypothesis the pointwise minimum "
              "over sessions on the site's common %g Hz mass grid (each "
              "session a worst-case inequality, the sign taken as ONE "
              "shared unknown per vendor, a premise the axis-sign lines "
              "below state), the site curve the weaker hypothesis at every "
              "mass" % step) if order else
             ("the pointwise minimum over sessions on the site's common "
              "%g Hz mass grid, each session a worst-case inequality%s"
              % (step, ", every sign-unverified session entered as the "
                       "weaker of its two mass placements"
                 if c.get("sign_unverified_sessions") else ""))))
        if order:
            A("<p>Conditional on the axis sign: %s.</p>" % "; ".join(
                "if the axis sign is <b>%s</b>: g<sub>ap</sub> &lt; %.3g "
                "GeV<sup>&minus;1</sup> at m<sub>a</sub> = %.7f &micro;eV "
                "(within-10&times; band %.7f to %.7f &micro;eV, set by %s)"
                % (esc(l), c["if_sign"][l]["g90_worst_best_gev_inv"],
                   c["if_sign"][l]["m_a_at_best_uev"],
                   c["if_sign"][l]["band_10x_uev"][0],
                   c["if_sign"][l]["band_10x_uev"][1],
                   esc(c["if_sign"][l]["session_setting_best"]))
                for l in order))
        if c.get("sign_note"):
            A("<p class='warn small'>Axis sign: %s</p>"
              % esc(c["sign_note"]))
        if c.get("sign_premise"):
            A("<p class='warn small'>Sign premise: %s</p>"
              % esc(c["sign_premise"]))
        A("<p class='warn small'>Coverage: %s</p>" % esc(c.get(
            "coverage_note", "")))
        A("<table><tr><th>session</th><th>bundle</th><th>carrier (MHz)</th>"
          "<th>axis sign</th>"
          "<th>best g<sub>ap</sub> (GeV<sup>&minus;1</sup>)</th>"
          "<th>at m<sub>a</sub> (&micro;eV)</th><th>statistical term of "
          "P<sub>90</sub></th><th>noise (s)</th></tr>")
        for s in sc["sessions"]:
            A("<tr><td>%s</td><td><code>%s</code></td><td>%s</td><td>%s"
              "</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
              % (esc(s["label"]), esc(s.get("bundle")),
                 fmt(s.get("carrier_mhz"), 6),
                 ("verified" if s.get("sign_verified") else
                  "<span class='warn'>UNVERIFIED</span>")
                 + ((" (%s)" % esc(s["vendor"])) if s.get("vendor") else ""),
                 "%.3g" % s["g90_worst_best_gev_inv"]
                 if s.get("g90_worst_best_gev_inv") else "&mdash;",
                 fmt(s.get("m_a_at_best_uev"), 6),
                 ("%.0f%%" % (100 * s["statistical_fraction_of_P90"]))
                 if s.get("statistical_fraction_of_P90") is not None
                 else "&mdash;",
                 fmt(s.get("noise_seconds"), 0)))
        A("</table>")
        if sc.get("skipped"):
            A("<p class='small'>prior report(s) skipped: %s</p>"
              % esc(" --- ".join("%s (%s)" % (os.path.basename(
                  os.path.dirname(k["report"])) or k["report"], k["why"])
                  for k in sc["skipped"])))
    A("</div>")
    return "".join(parts)


def render_field_sweep_html(ctx):
    """HTML fragment for the field-stepped sweep (empty when the feature
    did not run). Uses the science-path per-step analysis when present,
    else the raw meta object (software-test reports)."""
    fs = ctx.get("field_sweep")
    if not isinstance(fs, dict):
        m = ctx["meta"].get("field_sweep")
        if not isinstance(m, dict) or not m.get("enabled"):
            return ""
        fs = {"steps": m.get("steps") or [], "note": m.get("note", ""),
              "restored_offset_hz": m.get("restored_offset_hz"),
              "headline_expno": None}
    steps = fs.get("steps") or []
    parts = []
    A = parts.append
    A("<h2>Field-stepped sweep (one mass point per step)</h2>"
      "<div class='card'>")
    if fs.get("note"):
        A("<p class='small'>%s</p>" % esc(fs.get("note", "")))
    cf = bool(fs.get("carrier_follow"))
    if steps:
        lc_head = "line center (Hz)"
        if cf:
            lc_head = "line center (Hz, local window)"
        A("<table><tr><th>step</th><th>target (Hz)</th>"
          "<th>measured (Hz)</th><th>basis</th><th>dev (Hz)</th>"
          "<th>rows</th><th>noise expno</th>"
          "<th>%s</th><th>FWHM (Hz)</th></tr>" % lc_head)
        for i in range(len(steps)):
            s = steps[i]
            meas = fmt(s.get("measured_offset_hz"), 1)
            if s.get("offset_basis") == "unverified":
                meas = "&mdash; <span class='small'>(unverified)</span>"
            A("<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td>"
              "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
              "<td>%s</td></tr>"
              % (i + 1, fmt(s.get("target_offset_hz"), 1), meas,
                 esc(s.get("offset_basis", "")),
                 fmt(s.get("local_deviation_hz"), 1),
                 fmt(s.get("rows")),
                 fmt(s.get("noise_expno", s.get("expno"))),
                 fmt(s.get("line_center_hz"), 2),
                 fmt(s.get("fwhm_hz"), 2)))
        A("</table>")
        for s in steps:
            if s.get("orchestrator_note"):
                A("<p class='small'>step expno %s: %s</p>"
                  % (fmt(s.get("noise_expno", s.get("expno"))),
                     esc(s["orchestrator_note"])))
    restored = fs.get("restored_offset_hz")
    if restored is not None:
        A("<p class='small'>field restored to %+.1f Hz of baseline after "
          "the ladder</p>" % restored)
    if fs.get("headline_expno") is not None:
        A("<p class='small'>headline science block: expno %s (the step "
          "nearest the baseline field)</p>" % fs["headline_expno"])
    A("</div>")
    return "".join(parts)


# ============================================================================
# Physics interpretation (master formula; every assumption stated)
# ============================================================================

def temperature_contrast_point(meta, coadd, headline_notes):
    """Master-formula inversion where the bundle declares temperatures.

    S_V(D)/S_floor = 1 + f_c*l_r*[(Ts/Tc-2)*l - l_r]/(l_tot^2+D^2),
    f_c = Tc/(Tc+T_A). With measured contrast a and l_tot = pi*FWHM,
    solve (g+1)*l_r^2 - g*l_tot*l_r + a*l_tot^2/f_c = 0, g = Ts/Tc - 2.
    """
    spec = meta.get("spectrometer", {})
    samp = meta.get("sample", {})
    tc = spec.get("coil_temp_k")
    ta = spec.get("preamp_temp_k")
    ts = samp.get("vt_setpoint_k")
    probe = spec.get("probe_type", "unknown")
    out = {"probe_type": probe, "coil_temp_k": tc, "preamp_temp_k": ta,
           "sample_temp_k": ts}
    missing = [n for n, v in (("coil_temp_k", tc), ("preamp_temp_k", ta),
                              ("vt_setpoint_k", ts)) if v is None]
    if missing:
        out["status"] = ("requires coil/preamp temperatures: bundle does not "
                         "declare %s; the temperature-contrast point cannot "
                         "be computed" % ", ".join(missing))
        headline_notes.append(out["status"])
        return out
    if coadd is None:
        out["status"] = "no fitted feature to invert"
        return out
    a = coadd["amp_norm"]
    l_tot = math.pi * coadd["fwhm_hz"]
    f_c = tc / (tc + ta)
    g = ts / tc - 2.0
    out.update({"f_c": f_c, "g_TsOverTc_minus2": g, "lambda_tot_per_s": l_tot})
    # maximum achievable contrast at this (Ts, Tc, T_A):
    if g + 1.0 > 0:
        k_max = f_c * g * g / (4.0 * (g + 1.0))
        out["max_contrast_at_declared_temps"] = k_max
        out["measured_contrast_within_max"] = bool(a <= k_max + 1e-12)
    A, B, C = (g + 1.0), (-g * l_tot), (a * l_tot ** 2 / f_c)
    disc = B * B - 4 * A * C
    if disc >= 0 and abs(A) > 1e-12:
        roots = [(-B - math.sqrt(disc)) / (2 * A), (-B + math.sqrt(disc)) / (2 * A)]
        roots = [r for r in roots if 0 < r <= l_tot * 1.0001]
        out["lambda_r_solutions_per_s"] = roots
        out["status"] = "computed from declared temperatures (see assumptions)"
    else:
        out["lambda_r_solutions_per_s"] = []
        out["status"] = ("declared temperatures cannot reproduce the measured "
                         "contrast within the master formula -- either a "
                         "temperature is misdeclared or a transmission-line "
                         "phase enhancement is present (as in the 2020 pilot)")
        headline_notes.append(out["status"])
    return out


def headline_numbers(meta, noise, coadd, detection, ladder, notes):
    """Spin-coupled floor fraction + distance-from-ceiling, 2020 methodology,
    with every assumption written out."""
    out = {"assumptions": [
        "Fundamental ceiling defined as a receiver whose on-resonance noise "
        "floor is entirely spin-coupled (Gueron dip depth -> 1, or "
        "equivalently amplifier + uncoupled-circuit contribution -> 0); "
        "distance quoted in dB of on-resonance power.",
        "Contrast-based numbers are as-measured and need no absolute "
        "calibration; converting to absolute spin counts additionally "
        "requires the 2020 pairing factor (~%.1f, A0-vs-window) and, for "
        "cold-circuit probes, the radiation-damping back-action suppression "
        "(x%.1f-%.1f), neither applied here." % (
            PAIRING_FACTOR_2020, BACKACTION_RANGE_2020[0], BACKACTION_RANGE_2020[1]),
    ]}
    if detection.get("unavailable"):
        out["status"] = ("spin-noise analysis UNAVAILABLE -- no noise block "
                         "could be read: %s. No feature claim and no upper "
                         "limit are made." % detection.get("reason", "?"))
        notes.append(out["status"])
        return out
    if not detection.get("detected"):
        out["status"] = ("no significant spin-noise feature; headline numbers "
                         "are upper limits (see detection section)")
        ul = detection.get("upper_limit_95_amp")
        if ul is not None:
            out["spin_coupled_floor_fraction_ul95"] = ul
            out["distance_from_ceiling_db_lower_bound"] = \
                -10.0 * math.log10(min(ul, 1.0)) if ul > 0 else None
        return out
    a = coadd["amp_norm"]
    sa = coadd["amp_err"]
    sign = "bump" if a > 0 else "dip"
    out["feature_sign"] = sign
    if a > 0:
        frac = a / (1.0 + a)
        dfrac = sa / (1.0 + a) ** 2
        dist_db = 10.0 * math.log10((1.0 + a) / a)
        ddist = 10.0 / math.log(10.0) * sa / (a * (1.0 + a))
        out["spin_coupled_floor_fraction_note"] = (
            "emission bump: fraction of the ON-RESONANCE noise power that is "
            "spin-generated excess, a/(1+a)")
    else:
        depth = abs(a)
        frac = depth
        dfrac = sa
        dist_db = -10.0 * math.log10(min(depth, 1.0))
        ddist = 10.0 / math.log(10.0) * sa / depth
        out["spin_coupled_floor_fraction_note"] = (
            "Gueron dip: dip depth = f_c*lambda_r/lambda_tot = spin-coupled "
            "fraction of the floor at uniform temperature (a lower bound on "
            "the circuit coupling f_c)")
    out["spin_coupled_floor_fraction"] = frac
    out["spin_coupled_floor_fraction_err"] = dfrac
    out["distance_from_ceiling_db"] = dist_db
    out["distance_from_ceiling_db_stat_err"] = ddist
    # systematic envelope from receiver-gain linearity
    if ladder.get("available"):
        rg_pow = ladder["max_abs_power_deviation"]
        out["assumptions"].append(
            "Receiver-gain linearity measured on this bundle's RG ladder: "
            "max power deviation %.1f%%." % (100 * rg_pow))
    else:
        rg_pow = RG_POWER_ENVELOPE_UNTESTED
        out["assumptions"].append(
            "RG ladder unavailable: receiver-gain linearity UNTESTED; the "
            "2020 envelope of +/-20% in power is assumed and folded into "
            "the systematic uncertainty.")
    out["distance_from_ceiling_db_sys_err"] = 10.0 * math.log10(1.0 + rg_pow)
    out["rg_linearity_power_envelope"] = rg_pow
    return out


# ============================================================================
# QA
# ============================================================================

def parse_local(s):
    try:
        return datetime.datetime.strptime(s.strip()[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def qa_flags(bundle, meta, noise_res, validation_msgs):
    env = meta.get("environment", {})
    sw = meta.get("software", {})
    flags = []

    def add(level, name, detail):
        flags.append({"level": level, "check": name, "detail": detail})

    # sweep / lock state as recorded. The BSMS sweep flag is a Bruker
    # dialog answer; other vendors record the operator's field-state
    # notes in instrument.<vendor> instead.
    inst = (meta.get("instrument") or {}).get(bundle.vendor) or {}
    if env.get("lock_sweep_confirmed_off") is True:
        add("OK", "BSMS field sweep",
            "operator confirmed the field sweep was OFF")
    elif bundle.vendor != "bruker" and inst.get("field_state_notes"):
        add("WARN", "field sweep / field state",
            "no BSMS sweep flag on vendor '%s'; operator field-state notes: "
            "'%s' -- a running field sweep or lock hunt smears the feature "
            "by kHz, so the notes must rule it out"
            % (bundle.vendor, inst["field_state_notes"]))
    else:
        add("FAIL", "BSMS field sweep",
            "sweep-off NOT confirmed: a running sweep smears the feature by "
            "kHz; treat any line result with suspicion")
    if env.get("locked") is True:
        add("WARN", "deuterium lock",
            "lock recorded ON during the noise block; lock RF can perturb "
            "the noise floor -- interpret with care")
    else:
        add("OK", "deuterium lock", "lock recorded OFF (as recommended)")
    # ADC clipping, by each experiment's own declared element type
    for exp in meta.get("experiments", []):
        st = bundle.raw_int_stats(exp["expno"])
        if st is None:
            add("FAIL" if exp["expno"] in bundle.read_errors else "WARN",
                "ADC check expno %d" % exp["expno"],
                bundle.read_errors.get(exp["expno"],
                                       "no raw data file readable"))
            continue
        if st["fullscale_fraction"] is None:
            origin = {
                "float64": "TopSpin 4 style",
                "float32": "Varian/Agilent fid status S_FLT",
                "int32": ("Varian/Agilent fid status S_32: the ADC word "
                          "width inside the 32-bit container is not "
                          "declared, so no full scale is assumed"),
            }.get(st["dtype"], st["dtype"])
            add("OK", "ADC check expno %d" % exp["expno"],
                "%s data (%s); clipping check not applicable, max |value| "
                "%.3g" % (st["dtype"], origin, st["max_abs"]))
        elif st["fullscale_fraction"] > 0.90:
            add("FAIL", "ADC check expno %d" % exp["expno"],
                "raw %s data reaches %.0f%% of full scale -- clipping "
                "likely" % (st["dtype"], 100 * st["fullscale_fraction"]))
        else:
            add("OK", "ADC check expno %d" % exp["expno"],
                "max |sample| = %.3g (%.2g%% of %s full scale)"
                % (st["max_abs"], 100 * st["fullscale_fraction"],
                   st["dtype"]))
    # raw-data refusals: an experiment whose layout nothing declares is
    # excluded, never guessed -- and the exclusion is a FAIL, not silence
    for expno in sorted(bundle.read_errors):
        role = next((e.get("role") for e in meta.get("experiments", [])
                     if e.get("expno") == expno), "?")
        add("FAIL", "raw data expno %d (%s)" % (expno, role),
            "EXCLUDED from every analysis: %s" % bundle.read_errors[expno])
    # spikes
    if noise_res:
        spikes = [r.get("n_spikes", 0) for r in noise_res.get("per_row", [])]
        tot = int(np.sum(spikes))
        lvl = "OK" if tot < 0.001 * sum(
            r.get("nperseg", 1) for r in noise_res["per_row"]) else "WARN"
        add(lvl, "PSD spikes replaced",
            "%d bins across %d rows (per-row: %s)" % (tot, len(spikes), spikes))
    # timestamps
    exps = meta.get("experiments", [])
    bad_pairs = [e["expno"] for e in exps
                 if (parse_local(e.get("started_local", "")) and
                     parse_local(e.get("finished_local", "")) and
                     parse_local(e["finished_local"]) < parse_local(e["started_local"]))]
    if bad_pairs:
        add("FAIL", "timestamps",
            "finished < started for expno(s) %s" % bad_pairs)
    else:
        add("OK", "timestamps", "finished >= started for every experiment")
    starts = [(e["expno"], parse_local(e.get("started_local", ""))) for e in exps]
    starts = [(n, t) for n, t in starts if t]
    if len(starts) >= 2:
        order = [n for n, _ in sorted(starts, key=lambda p: p[1])]
        listed = [n for n, _ in starts]
        if order != listed:
            add("WARN", "experiment order",
                "start times are not monotonic in listed order (%s); normal "
                "for archival repackages, check provenance otherwise" % order)
    # software provenance -- WARN if either the script version or the
    # run_mode is missing: a bundle without a declared run_mode is
    # schema-valid (run_mode is optional in 1.1, absent in 1.0) but its
    # provenance is incomplete, and the science/software-test gate had to
    # assume 'live'.
    prov_ok = bool(sw.get("script_version")) and bool(sw.get("run_mode"))
    prov_detail = ("script_version=%s run_mode=%s script_sha256=%s" % (
        sw.get("script_version", "MISSING"), sw.get("run_mode", "undeclared"),
        (sw.get("script_sha256", "unavailable") or "")[:23]))
    if not sw.get("run_mode"):
        prov_detail += (" -- run_mode undeclared: cannot verify this bundle "
                        "is not a software test; treated as live data")
    add("OK" if prov_ok else "WARN", "software provenance", prov_detail)
    # validator messages that were warnings
    for m in validation_msgs:
        if m.startswith("WARN"):
            add("WARN", "bundle validator", m[7:].strip())
    return flags


# ============================================================================
# Figures
# ============================================================================

def fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight",
                facecolor="white")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def make_figures(noise_res, refs, ladder, detection):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figs = {}
    C_DATA, C_FIT, C_ALT = "#3b6ea5", "#c44e52", "#55a868"

    cf, cf_key = headline_fit(noise_res)
    if cf_key == "unaligned_fit" and "_stack" in noise_res:
        # the aligned co-add was judged manufactured: show the stack at
        # recorded frequencies, centered on its own fitted line
        grid = noise_res["_stack"]["f"] - cf["center_hz"]
        avg = noise_res["_stack"]["avg"]
        center, title = 0.0, ("Co-added spin-noise line, UNALIGNED stack "
                              "(self-alignment judged SUSPECT; see QA)")
        xlabel = "offset from fitted line center (Hz)"
    elif cf is not None and "_coadd" in noise_res:
        grid = noise_res["_coadd"]["grid"]
        avg = noise_res["_coadd"]["avg"]
        center, title = (cf["center_shift_hz"],
                         "Drift-aligned co-added spin-noise line")
        xlabel = "offset from aligned line center (Hz)"
    else:
        grid = None
    if grid is not None:
        fig, ax = plt.subplots(figsize=(7.2, 4.2))
        ax.plot(grid, avg, color=C_DATA, lw=0.8,
                label="co-added normalized PSD (%d rows)"
                % cf["n_rows_coadded"])
        model = lineshape(grid, cf["amp_norm"], cf["disp_norm"],
                          center, cf["fwhm_hz"], cf["offset"])
        ax.plot(grid, model, color=C_FIT, lw=1.6,
                label="absorptive+dispersive fit")
        ax.axhline(1.0, color="0.5", lw=0.7, ls=":")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("PSD / floor")
        ax.set_xlim(-150, 150)
        ax.legend(frameon=False, fontsize=9)
        ax.set_title(title)
        figs["coadd"] = fig_to_b64(fig)

    if noise_res and noise_res.get("_rows"):
        r0 = noise_res["_rows"][0]
        fig, ax = plt.subplots(figsize=(7.2, 3.4))
        ax.semilogy(r0["f"], r0["psd"], color=C_DATA, lw=0.5,
                    label="row 1 PSD")
        ax.semilogy(r0["f"], r0["base"], color=C_FIT, lw=1.2,
                    label="broad-SG baseline")
        ax.set_xlabel("offset from carrier (Hz%s)"
                      % ("; axis SIGN unverified for Agilent/Varian data"
                         if noise_res.get("axis_sign_unverified") else ""))
        ax.set_ylabel("PSD (counts$^2$/Hz)")
        ax.legend(frameon=False, fontsize=9)
        ax.set_title("Full-band PSD and baseline (first noise row)")
        figs["fullband"] = fig_to_b64(fig)

        fits = [pr["fit"] for pr in noise_res["per_row"] if "fit" in pr]
        if fits:
            fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0))
            idx = np.arange(1, len(fits) + 1)
            axes[0].errorbar(idx, [f_["amp_norm"] for f_ in fits],
                             yerr=[f_["amp_err"] for f_ in fits],
                             fmt="o", color=C_DATA, ms=4)
            axes[0].set_xlabel("noise row")
            axes[0].set_ylabel("peak excess (x floor)")
            axes[1].plot(idx, [f_["center_hz"] for f_ in fits], "o-",
                         color=C_ALT, ms=4, lw=0.8)
            axes[1].set_xlabel("noise row")
            axes[1].set_ylabel("line center (Hz)")
            fig.suptitle("Per-row line amplitude and center (drift track)",
                         fontsize=10)
            fig.tight_layout()
            figs["perrow"] = fig_to_b64(fig)

    if ladder.get("available"):
        fig, ax = plt.subplots(figsize=(5.4, 3.6))
        rg = [r["rg"] for r in ladder["rungs"]]
        am = [r["amplitude_counts"] for r in ladder["rungs"]]
        ax.loglog(rg, am, "o", color=C_DATA, ms=6, label="ladder rung")
        xx = np.geomspace(min(rg), max(rg), 50)
        ax.loglog(xx, ladder["slope_counts_per_rg"] * xx, "-",
                  color=C_FIT, lw=1.2, label="linear (through origin)")
        ax.set_xlabel("receiver gain (RG)")
        ax.set_ylabel("line amplitude (counts)")
        ax.legend(frameon=False, fontsize=9)
        ax.set_title("RG ladder linearity (max dev %.1f%% amp)"
                     % (100 * ladder["max_abs_fractional_deviation"]))
        figs["ladder"] = fig_to_b64(fig)
    return figs


# ============================================================================
# Report rendering
# ============================================================================

CSS = """
:root { --bg:#ffffff; --fg:#1c2430; --muted:#5b6b7d; --card:#f5f7fa;
        --accent:#3b6ea5; --ok:#2e7d32; --warn:#b26a00; --fail:#c62828;
        --border:#d7dee7; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151a21; --fg:#e4e9f0; --muted:#9aa8b8; --card:#1e2530;
          --accent:#7aa7d4; --ok:#7bc67e; --warn:#e0a458; --fail:#ef7b73;
          --border:#33404f; } }
body { background:var(--bg); color:var(--fg);
       font:15px/1.55 -apple-system,'Segoe UI',Helvetica,Arial,sans-serif;
       max-width:960px; margin:2rem auto; padding:0 1.2rem; }
h1 { font-size:1.5rem; margin-bottom:.2rem; }
h2 { font-size:1.15rem; border-bottom:1px solid var(--border);
     padding-bottom:.25rem; margin-top:2rem; }
.small, .muted { color:var(--muted); font-size:.88rem; }
.banner { border:2px solid var(--fail); color:var(--fail); padding:.8rem 1rem;
          border-radius:8px; font-weight:700; margin:1rem 0; }
.banner.info { border-color:var(--warn); color:var(--warn); }
.card { background:var(--card); border:1px solid var(--border);
        border-radius:8px; padding:1rem 1.2rem; margin:.8rem 0; }
table { border-collapse:collapse; width:100%; font-size:.9rem; }
th,td { text-align:left; padding:.3rem .6rem; border-bottom:1px solid var(--border); }
th { color:var(--muted); font-weight:600; }
.ok { color:var(--ok); font-weight:700; } .warn { color:var(--warn); font-weight:700; }
.fail { color:var(--fail); font-weight:700; }
.fig { background:#ffffff; border:1px solid var(--border); border-radius:8px;
       padding:.6rem; margin:.8rem 0; text-align:center; }
.fig img { max-width:100%; height:auto; }
.big { font-size:1.35rem; font-weight:700; }
code { background:var(--card); padding:.05rem .3rem; border-radius:4px; }
ul { margin:.3rem 0 .3rem 1.2rem; }
"""


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def fmt(v, nd=3):
    if v is None:
        return "&mdash;"
    if isinstance(v, float):
        if v == 0:
            return "0"
        if abs(v) >= 1e4 or abs(v) < 1e-3:
            return "%.*g" % (nd, v)
        return "%.*f" % (nd, v)
    return esc(v)


def render_html(ctx):
    """Assemble the single-file HTML report from the context dict."""
    meta = ctx["meta"]
    parts = []
    A = parts.append
    A("<meta charset='utf-8'>")
    A("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    A("<title>Spin-noise facility report — %s</title>"
      % esc(meta.get("facility", {}).get("facility_slug", "unknown")))
    A("<style>%s</style>" % CSS)

    fac = meta.get("facility", {})
    spec = meta.get("spectrometer", {})
    sw = meta.get("software", {})
    A("<h1>Spin-noise network — facility report</h1>")
    nuc_txt = ""
    if spec.get("observe_nucleus") and spec.get("observe_nucleus") != "1H":
        nuc_txt = (" &middot; observed nucleus: %s at %.6g MHz"
                   % (esc(spec["observe_nucleus"]),
                      float(spec.get("observe_freq_mhz", 0) or 0)))
    A("<div class='muted'>%s, %s, %s &middot; %s &middot; %.6g MHz "
      "(1H-equivalent, &approx;%.4g T)%s &middot; probe: %s (%s)</div>"
      % (esc(fac.get("institution", "?")), esc(fac.get("city", "?")),
         esc(fac.get("country", "?")), esc(spec.get("console", "?")),
         float(spec.get("h1_freq_mhz", 0) or 0),
         float(spec.get("field_tesla", 0) or 0), nuc_txt,
         esc(spec.get("probe_string", "?")), esc(spec.get("probe_type", "?"))))
    A("<div class='muted small'>bundle: <code>%s</code> &middot; acquired "
      "run_mode=<code>%s</code> by script v%s &middot; report generator "
      "v%s &middot; generated %s UTC &middot; contact: %s</div>"
      % (esc(os.path.basename(ctx["bundle_path"])),
         esc(sw.get("run_mode", "undeclared")),
         esc(sw.get("script_version", "?")), esc(REPORT_VERSION),
         datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
         esc(CONTACT)))

    if ctx["report_type"] == "software-test":
        A("<div class='banner'>SOFTWARE-TEST REPORT — NOT A SCIENCE RESULT."
          "<br>This bundle declares run_mode='%s': the hardware commands were "
          "mocked and the files contain no measurement. No science numbers "
          "are produced from such bundles, by design.</div>"
          % esc(sw.get("run_mode")))
        A("<h2>Bundle validation</h2><div class='card'><ul>")
        for m in ctx["validation_msgs"]:
            cls = "fail" if m.startswith("ERROR") else (
                "warn" if m.startswith("WARN") else "ok")
            A("<li class='%s'>%s</li>" % (cls, esc(m)))
        A("</ul><p>Validator verdict: <span class='%s'>%s</span></p></div>"
          % ("ok" if ctx["validation_ok"] else "fail",
             "PASS" if ctx["validation_ok"] else "FAIL"))
        A("<h2>Bundle inventory</h2><div class='card'><table>"
          "<tr><th>expno</th><th>role</th><th>pulprog</th><th>TD</th>"
          "<th>rows</th><th>RG</th><th>data files present</th></tr>")
        for e in meta.get("experiments", []):
            present = ", ".join(sorted(
                os.path.basename(n) for n in ctx["names"]
                if n.startswith("data/%d/" % e["expno"]))) or "none"
            A("<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
              "<td>%s</td><td>%s</td></tr>"
              % (e["expno"], esc(e.get("role")), esc(e.get("pulprog")),
                 fmt(e.get("td")), fmt(e.get("td1_rows")), fmt(e.get("rg")),
                 esc(present)))
        A("</table><p class='small'>The plumbing that produced this bundle "
          "(dialog chain, dataset bookkeeping, meta.json, zip, checksums) "
          "was exercised end to end; that is all this report certifies."
          "</p></div>")
        # The clock audit is timestamp plumbing, not spin physics, so it IS
        # analyzed for software-test bundles -- the harness uses exactly
        # this to validate the offset fit against a known injected offset.
        # On a desk machine the recorded wall times are the desk's, so the
        # numbers describe the test host, never a spectrometer.
        A(render_clock_audit_html(ctx["clock"]))
        A(render_rdopt_html(ctx))
        A(render_field_sweep_html(ctx))
        A(render_line_catalog_html(ctx))
        A(_footer())
        return "".join(parts)

    if not sw.get("run_mode"):
        A("<div class='banner info'>PROVENANCE INCOMPLETE — this bundle's "
          "software block declares no run_mode (optional in schema 1.1, "
          "absent in 1.0). It is analyzed as live data, but the "
          "software-test gate could not verify that; see the software-"
          "provenance QA flag.</div>")
    if sw.get("run_mode") == "synthetic-injection":
        A("<div class='banner info'>SYNTHETIC-INJECTION VALIDATION BUNDLE — "
          "the data are numerically generated with a known injected feature. "
          "The numbers below validate the pipeline; they are NOT a "
          "measurement of any instrument.</div>")
    if sw.get("run_mode") == "archival-repackage":
        A("<div class='banner info'>ARCHIVAL REPACKAGE — real spectrometer "
          "data recorded before this network existed, repackaged into the "
          "bundle format. Timestamps and some metadata are reconstructed; "
          "see QA flags.</div>")

    # ---- headline
    hd = ctx["headline"]
    det = ctx["detection"]
    A("<h2>Headline: distance from the fundamental sensitivity ceiling</h2>")
    A("<div class='card'>")
    if det.get("unavailable"):
        A("<p class='fail'>Spin-noise analysis UNAVAILABLE for this bundle: "
          "no noise block could be read.</p><p class='small'>%s</p>"
          "<p class='small'>No feature is claimed and no upper limit is "
          "quoted; the numbers below that depend on the noise block are "
          "absent, not zero.</p>" % esc(det.get("reason", "")))
    elif det.get("detected"):
        A("<p><span class='big'>%.2f &plusmn; %.2f dB</span> "
          "(stat) &plusmn; %.2f dB (sys) above the fully spin-coupled "
          "(fundamental) noise floor at resonance.</p>"
          % (hd["distance_from_ceiling_db"],
             hd["distance_from_ceiling_db_stat_err"],
             hd["distance_from_ceiling_db_sys_err"]))
        A("<p>Spin-coupled floor fraction: <b>%.3f &plusmn; %.3f</b> "
          "<span class='small'>(%s)</span></p>"
          % (hd["spin_coupled_floor_fraction"],
             hd["spin_coupled_floor_fraction_err"],
             esc(hd["spin_coupled_floor_fraction_note"])))
        ac = (ctx["noise"] or {}).get("alignment_check") or {}
        if ac.get("suspect"):
            A("<p class='warn'>%s</p>" % esc(ac["verdict"]))
        if det.get("fit_basis_note"):
            A("<p class='small'>%s</p>" % esc(det["fit_basis_note"]))
        if hd.get("sign_vs_probe_type"):
            A("<p class='%s'>%s</p>"
              % ("warn" if "UNEXPECTED" in hd["sign_vs_probe_type"]
                 else "small", esc(hd["sign_vs_probe_type"])))
    else:
        A("<p><b>No significant spin-noise feature was detected.</b> "
          "95%% upper limit on the feature amplitude: "
          "<span class='big'>%s &times; floor</span>.</p>"
          % fmt(det.get("upper_limit_95_amp")))
        if hd.get("distance_from_ceiling_db_lower_bound") is not None:
            A("<p>The receiver therefore sits <b>&ge; %.1f dB</b> from the "
              "fully spin-coupled ceiling under this sample and tuning "
              "(a lower bound, not a measurement of the receiver alone: a "
              "weakly protonated sample gives the same null &mdash; the 2022 "
              "lesson).</p>" % hd["distance_from_ceiling_db_lower_bound"])
    tc = ctx["temp_contrast"]
    A("<p><b>Temperature-contrast point:</b> ")
    if "requires" in str(tc.get("status", "")):
        A("<span class='warn'>%s</span>" % esc(tc["status"]))
    else:
        A("%s" % esc(tc.get("status", "")))
        if tc.get("lambda_r_solutions_per_s"):
            A("<br><span class='small'>master-formula radiation-damping rate "
              "solutions: %s s<sup>-1</sup> (f_c = %.3f, &lambda;_tot = %.1f "
              "s<sup>-1</sup>)</span>"
              % (", ".join("%.1f" % r for r in tc["lambda_r_solutions_per_s"]),
                 tc.get("f_c", float("nan")),
                 tc.get("lambda_tot_per_s", float("nan"))))
    A("</p>")
    A("<p class='small'><b>Assumptions (all of them):</b></p><ul class='small'>")
    for s in hd.get("assumptions", []):
        A("<li>%s</li>" % esc(s))
    A("</ul></div>")

    # ---- feature
    A("<h2>Spin-noise feature</h2><div class='card'>")
    agg = ctx.get("noise_aggregation")
    if agg and agg.get("note"):
        A("<p class='%s'><b>Noise block:</b> %s</p>"
          % ("small" if agg.get("consistent") else "warn",
             esc(agg["note"])))
    if ctx["noise"] and ctx["noise"].get("skipped_experiments"):
        A("<p class='warn'>noise experiments left out of the block: %s</p>"
          % esc("; ".join("expno %s -- %s" % (s["expno"], s["why"])
                          for s in ctx["noise"]["skipped_experiments"])))
    sign = ctx.get("sign_status") or {}
    if det.get("unavailable"):
        A("<p class='fail'>Not analyzed: %s</p>" % esc(det.get("reason", "")))
    elif det.get("detected"):
        cf, cf_key = headline_fit(ctx["noise"])
        sign_word = ("emission BUMP (net spin emission: the spins run hotter "
                     "than the circuit noise they see)"
                     if cf["amp_norm"] > 0 else
                     "absorption DIP (Gueron dip: net absorption of circuit "
                     "noise by the spins)")
        A("<table><tr><th>quantity</th><th>value</th></tr>")
        A("<tr><td>sign / character</td><td>%s</td></tr>" % sign_word)
        A("<tr><td>fit basis</td><td>%s</td></tr>"
          % ("drift-aligned co-add (coadd_fit)" if cf_key == "coadd_fit"
             else "<span class='warn'>UNALIGNED stack (unaligned_fit) -- "
                  "the self-aligned co-add was judged SUSPECT</span>"))
        A("<tr><td>peak excess (amplitude rel. to floor)</td>"
          "<td>%.3f &plusmn; %.3f</td></tr>" % (cf["amp_norm"], cf["amp_err"]))
        A("<tr><td>FWHM</td><td>%.2f &plusmn; %.2f Hz</td></tr>"
          % (cf["fwhm_hz"], cf["fwhm_err_hz"]))
        ba = cf.get("asymmetry_b_over_a")
        A("<tr><td>dispersive fraction b/a</td><td>%s%s</td></tr>"
          % (fmt(ba), (" &plusmn; %.3f" % cf["asymmetry_err"])
             if cf.get("asymmetry_err") else ""))
        A("<tr><td>line center (mean over rows)</td><td>%.1f Hz from carrier"
          "%s</td></tr>"
          % (det.get("line_center_hz", float("nan")),
             " <span class='warn'>(SIGN unverified)</span>"
             if not sign.get("verified", True) else ""))
        A("<tr><td>combined significance (matched-filter NPE, quadrature)"
          "</td><td>%.1f&sigma;</td></tr>" % det.get("npe_combined", float("nan")))
        A("<tr><td>amplitude significance (%s)</td>"
          "<td>%.1f&sigma;</td></tr>"
          % ("co-added fit" if cf_key == "coadd_fit" else "unaligned fit",
             det.get("amp_significance", float("nan"))))
        A("</table>")
    else:
        A("<p>No feature at &ge;%.0f&sigma;. Upper limit constructed at the "
          "reference line position, profiled over plausible widths:</p>"
          % DETECT_NSIGMA)
        A("<table><tr><th>assumed FWHM (Hz)</th><th>fitted amp</th>"
          "<th>&sigma;</th><th>UL95</th></tr>")
        for d in det.get("upper_limit_details", []):
            A("<tr><td>%.1f</td><td>%.4f</td><td>%.4f</td><td>%.4f</td></tr>"
              % (d["fwhm_hz"], d["amp"], d["amp_err"], d["ul95"]))
        A("</table>")
    if not det.get("unavailable") and not sign.get("verified", True):
        A("<p class='warn small'>%s</p>" % esc(sign.get("note", "")))
    A("</div>")
    for key in ("coadd", "fullband", "perrow"):
        if key in ctx["figs"]:
            A("<div class='fig'><img alt='%s' src='data:image/png;base64,%s'>"
              "</div>" % (key, ctx["figs"][key]))

    # ---- per-row table
    if ctx["noise"] and ctx["noise"].get("per_row"):
        multi = (ctx["noise"].get("n_experiments") or 1) > 1
        A("<h2>Per-row noise analysis</h2><div class='card'>")
        A("<p class='small'>%d rows of %.1f s (%d complex points at "
          "%.2f Hz) from %d experiment(s)%s.</p>"
          % (ctx["noise"].get("n_rows", 0),
             ctx["noise"].get("row_seconds", 0.0),
             ctx["noise"].get("n_points_complex", 0),
             ctx["noise"].get("fs_hz", 0.0),
             ctx["noise"].get("n_experiments", 1),
             "; the source expno and its recorded start time identify "
             "each row for drift and time-series use" if multi else ""))
        A("<table><tr><th>row</th><th>expno</th>%s<th>segments</th>"
          "<th>spikes</th><th>amp</th><th>FWHM (Hz)</th><th>center (Hz)</th>"
          "<th>b/a</th><th>NPE</th></tr>"
          % ("<th>started (local)</th>" if multi else ""))
        for i, pr in enumerate(ctx["noise"]["per_row"]):
            src = "<td>%s</td>%s" % (
                fmt(pr.get("expno")),
                ("<td>%s</td>" % (esc(str(pr.get("started_local")
                                          or "")[11:19]) or "&mdash;"))
                if multi else "")
            if "fit" in pr:
                ft = pr["fit"]
                A("<tr><td>%d</td>%s<td>%d</td><td>%d</td>"
                  "<td>%.2f&plusmn;%.2f</td><td>%.1f</td><td>%.1f</td>"
                  "<td>%.2f</td><td>%.1f</td></tr>"
                  % (i + 1, src, pr["nseg"], pr["n_spikes"], ft["amp_norm"],
                     ft["amp_err"], ft["fwhm_hz"], ft["center_hz"],
                     pr.get("asymmetry_b_over_a") or float("nan"),
                     pr.get("npe_at_line", float("nan"))))
            else:
                A("<tr><td>%d</td>%s<td>%d</td><td>%d</td>"
                  "<td colspan='5' class='warn'>fit failed: %s</td></tr>"
                  % (i + 1, src, pr.get("nseg", 0), pr.get("n_spikes", 0),
                     esc(pr.get("fit_error", "?"))))
        A("</table></div>")

    # ---- RG ladder
    lad = ctx["ladder"]
    A("<h2>Receiver-gain linearity (RG ladder)</h2><div class='card'>")
    if lad.get("available"):
        A("<p>Max amplitude deviation from linearity: <b>%.1f%%</b> "
          "(&asymp;%.1f%% in power) across %d rungs.</p>"
          % (100 * lad["max_abs_fractional_deviation"],
             100 * lad["max_abs_power_deviation"], len(lad["rungs"])))
        A("<table><tr><th>expno</th><th>RG</th><th>amplitude (counts)</th>"
          "<th>deviation</th></tr>")
        for r in lad["rungs"]:
            rg_txt = "%.4g" % r["rg"]
            if r.get("rg_declared_in_ladder") is not None:
                rg_txt += (" <span class='small'>(recorded%s; ladder "
                           "declared %.4g)</span>"
                           % ((", %.0f dB" % r["rx_gain_db_recorded"])
                              if r.get("rx_gain_db_recorded") is not None
                              else "", r["rg_declared_in_ladder"]))
            A("<tr><td>%d</td><td>%s</td><td>%.4g</td><td>%+.2f%%</td></tr>"
              % (r["expno"], rg_txt, r["amplitude_counts"],
                 100 * r.get("fractional_deviation", 0)))
        A("</table>")
        if lad.get("rg_source_note"):
            A("<p class='small'>%s</p>" % esc(lad["rg_source_note"]))
        if lad.get("grpdly_note"):
            A("<p class='small'>%s</p>" % esc(lad["grpdly_note"]))
        if lad.get("unreadable"):
            A("<p class='warn small'>rung(s) excluded as unreadable: %s</p>"
              % esc("; ".join("expno %s -- %s" % (u["expno"], u["why"])
                              for u in lad["unreadable"])))
    else:
        A("<p class='warn'>%s</p>" % esc(lad.get("note", "unavailable")))
    A("<p class='small'>Receiver-gain linearity was the largest UNTESTED "
      "systematic of the 2020 pilot (its calibration bridges RG 184.37 &rarr; "
      "0.96); the network protocol measures it at every facility for exactly "
      "that reason.</p></div>")
    if "ladder" in ctx["figs"]:
        A("<div class='fig'><img alt='ladder' "
          "src='data:image/png;base64,%s'></div>" % ctx["figs"]["ladder"])

    # ---- references
    A("<h2>References and floor calibration</h2><div class='card'>")
    refs = ctx["refs"]
    if refs:
        A("<table><tr><th>expno</th><th>role</th><th>rows</th><th>RG</th>"
          "<th>A0 (counts)</th><th>FWHM_amp (Hz)</th><th>center (Hz)</th>"
          "<th>tail floor (counts&sup2;/Hz)</th></tr>")
        for r in refs:
            if not r.get("readable"):
                A("<tr><td>%d</td><td>%s</td>"
                  "<td colspan='6' class='fail'>EXCLUDED, unreadable: %s"
                  "</td></tr>"
                  % (r["expno"], esc(r["role"]),
                     esc(r.get("why", "raw data unreadable"))))
                continue
            A("<tr><td>%d</td><td>%s</td><td>%d</td><td>%.4g</td><td>%s</td>"
              "<td>%s</td><td>%s</td><td>%s</td></tr>"
              % (r["expno"], esc(r["role"]), r["n_rows"], r["rg"],
                 fmt(r.get("A0_counts"), 5), fmt(r.get("fwhm_amp_hz")),
                 fmt(r.get("line_center_hz"), 5),
                 fmt(r.get("tail_floor_counts2perhz"))))
        A("</table>")
        sign = ctx.get("sign_status") or {}
        if not sign.get("verified", True):
            A("<p class='warn small'>Line centers above: %s</p>"
              % esc(sign.get("note", "")))
        pair = ctx.get("reference_pair") or {}
        if pair and not pair.get("matched"):
            A("<p class='warn small'>%s.</p>" % esc(pair.get("note", "")))
        elif pair:
            A("<p class='small'>References are a matched pair (pw %.4g us "
              "/ tpwr %s dB).</p>"
              % (pair["pulses"][0]["pw_us"],
                 fmt(pair["pulses"][0]["tpwr_db"], 3)))
        cal = ctx["floor_cal"]
        if cal:
            A("<ul class='small'>")
            for k, label in (
                ("line_stability_open_close_hz",
                 "line-position stability open&rarr;close (Hz)"),
                ("a0_ratio_close_over_open", "A0 close/open"),
                ("noise_floor_counts2perhz_at_noise_rg",
                 "noise floor at line, noise RG (counts&sup2;/Hz)"),
                ("noise_floor_bridged_to_ref_rg",
                 "noise floor bridged to reference RG (counts&sup2;/Hz)"),
                ("gain_ratio_amplitude", "gain ratio RG_noise/RG_ref"),
                ("floor_consistency_bridged_over_ref_tail",
                 "floor consistency: bridged noise floor / reference tail floor"),
                ("spin_line_integrated_counts2_at_ref_rg",
                 "integrated spin-line power at reference RG (counts&sup2;)"),
            ):
                if cal.get(k) is not None:
                    A("<li>%s: <b>%s</b></li>" % (label, fmt(cal[k], 4)))
            A("</ul>")
            if cal.get("a0_ratio_caveat"):
                A("<p class='warn small'>%s</p>" % esc(cal["a0_ratio_caveat"]))
            if cal.get("spin_line_fit_basis"):
                A("<p class='small'>integrated spin-line power from the %s."
                  "</p>" % esc(cal["spin_line_fit_basis"]))
            if cal.get("floor_consistency_note"):
                A("<p class='small'>%s</p>" % esc(cal["floor_consistency_note"]))
    else:
        A("<p class='warn'>No readable reference experiments; floor "
          "calibration and line-position anchoring unavailable.</p>")
    A("</div>")

    # ---- raw-data read path
    rr = ctx.get("raw_read") or {}
    A("<h2>Raw-data read path</h2><div class='card'>")
    A("<p class='small'>vendor <code>%s</code> &middot; formats read: %s "
      "&middot; element types: %s &middot; %d experiment(s) read, %d "
      "refused</p>"
      % (esc(rr.get("vendor", "?")),
         esc(", ".join(rr.get("formats") or []) or "none"),
         esc(", ".join(rr.get("dtypes") or []) or "none"),
         len(rr.get("by_expno") or {}), len(rr.get("refused") or {})))
    if rr.get("agilent_note"):
        A("<p class='small'>%s</p>" % esc(rr["agilent_note"]))
    if rr.get("refused"):
        A("<p class='fail'>Refused (excluded from every analysis; layout "
          "not declared by the bundle, nothing guessed):</p><ul class='small'>")
        for k, why in rr["refused"].items():
            A("<li>expno %s: %s</li>" % (esc(k), esc(why)))
        A("</ul>")
    by = rr.get("by_expno") or {}
    if by:
        A("<table><tr><th>expno</th><th>format</th><th>element type</th>"
          "<th>rows</th><th>complex points</th><th>note</th></tr>")
        for k, v in by.items():
            A("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
              "<td>%s</td></tr>"
              % (esc(k), esc(v.get("format")), esc(v.get("dtype")),
                 fmt(v.get("n_rows")), fmt(v.get("n_points_complex")),
                 esc(v.get("note", ""))))
        A("</table>")
    A("</div>")

    # ---- clock audit
    A(render_clock_audit_html(ctx["clock"]))

    # ---- optional features (empty fragments when absent)
    A(render_rdopt_html(ctx))
    A(render_field_sweep_html(ctx))
    A(render_line_catalog_html(ctx))
    A(render_axion_exclusion_html(ctx))

    # ---- QA
    A("<h2>QA flags</h2><div class='card'><table>"
      "<tr><th>status</th><th>check</th><th>detail</th></tr>")
    for fl in ctx["qa"]:
        A("<tr><td class='%s'>%s</td><td>%s</td><td>%s</td></tr>"
          % (fl["level"].lower(), fl["level"], esc(fl["check"]),
             esc(fl["detail"])))
    A("</table></div>")

    # ---- what was and was not determined
    A("<h2>What this report does and does not establish</h2><div class='card'>"
      "<ul class='small'>")
    for line in ctx["honesty"]:
        A("<li>%s</li>" % esc(line))
    A("</ul></div>")
    A(_footer())
    return "".join(parts)


def _footer():
    return ("<p class='muted small' style='margin-top:2rem'>Spin-noise "
            "network per-facility report &middot; generator v%s &middot; "
            "methodology: 2020 EPFL pilot pipeline (Welch power averaging, "
            "spike replacement, SG baseline, absorptive+dispersive fit, "
            "gain bridging) &middot; Blanchard, Ebadi, Claude (Anthropic) "
            "&middot; contact %s</p>" % (esc(REPORT_VERSION), esc(CONTACT)))


# ============================================================================
# main
# ============================================================================

def strip_private(obj):
    """Drop numpy arrays / private keys for the JSON output."""
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items()
                if not k.startswith("_")}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return None
    return obj


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("bundle", help="path to spinnoise_*.zip")
    ap.add_argument("--out", default=None,
                    help="output directory (default: <bundle_stem>_report "
                         "next to the bundle)")
    ap.add_argument("--prior-reports", nargs="+", default=None,
                    metavar="PATH",
                    help="report.json files (or report directories) of "
                         "EARLIER sessions of the SAME facility: the new "
                         "report then carries science.axion_exclusion"
                         ".site_combined, the site's running worst-case "
                         "exclusion (analysis/site_exclusion.py) --- a prior "
                         "report from another facility_slug is refused")
    args = ap.parse_args(argv)

    bundle_path = os.path.abspath(args.bundle)
    if not os.path.isfile(bundle_path):
        print("ERROR: no such file: %s" % bundle_path)
        return 2
    out_dir = args.out or os.path.join(
        os.path.dirname(bundle_path),
        os.path.splitext(os.path.basename(bundle_path))[0] + "_report")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    # ---- 1. validation via the uploader's own selftest logic
    schema_path = os.path.join(REPO, "schema", "meta.schema.json")
    val_ok, val_msgs = upload_bundle.verify_bundle(bundle_path, schema_path)
    for m in val_msgs:
        print(m)
    if not val_ok:
        print("ERROR: bundle failed validation; no report produced. Fix the "
              "problems above (or re-create the bundle).")
        return 1

    bundle = Bundle(bundle_path)
    meta = bundle.meta
    sw = meta.get("software", {}) if isinstance(meta.get("software"), dict) else {}
    run_mode = sw.get("run_mode", "undeclared")

    prior_reports = []
    if args.prior_reports:
        site_mod = site_exclusion_module()
        slug = (meta.get("facility") or {}).get("facility_slug")
        for p in args.prior_reports:
            try:
                label, prior = site_mod.load_report(p)
            except (IOError, OSError, ValueError) as exc:
                print("ERROR: cannot read prior report %s: %s" % (p, exc))
                return 2
            if prior.get("facility_slug") != slug:
                print("ERROR: prior report %s is from facility '%s' but this "
                      "bundle is from '%s' --- --prior-reports combines ONE "
                      "site's sessions, never across sites --- no report "
                      "produced" % (label, prior.get("facility_slug"), slug))
                return 2
            prior_reports.append((label, prior))

    report = {
        "report_version": REPORT_VERSION,
        "generated_utc": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bundle": os.path.basename(bundle_path),
        "bundle_sha256": upload_bundle.sha256_of_file(bundle_path),
        "facility_slug": meta.get("facility", {}).get("facility_slug"),
        "run_mode": run_mode,
        "software": sw,
        "validation": {"ok": val_ok, "messages": val_msgs},
    }

    # ---- clock audit (both report types: it is timestamp plumbing, not
    # spin physics, and the harness validates the fit on desktest bundles)
    clock = analyze_clock_audit(meta, bundle)
    report["clock_audit"] = strip_private(clock)

    # ---- 2. run-mode gate
    if run_mode in SOFTWARE_TEST_MODES:
        report["report_type"] = "software-test"
        report["science"] = None
        report["note"] = ("run_mode '%s': plumbing test, not data; science "
                          "analysis refused by design" % run_mode)
        ctx = {"report_type": "software-test", "meta": meta,
               "bundle_path": bundle_path, "validation_msgs": val_msgs,
               "validation_ok": val_ok, "names": sorted(bundle.names),
               "clock": clock}
        html = render_html(ctx)
        _write(out_dir, html, report)
        print("SOFTWARE-TEST report written to %s" % out_dir)
        return 0

    # 'external-acquisition' is the packer's run_mode for real data taken
    # with the facility's own vendor software (the Agilent/JEOL/Magritek
    # path): science, not a synthetic validation
    report["report_type"] = "science" if run_mode in (
        "live", "undeclared", "archival-repackage", "external-acquisition") \
        else "synthetic-validation"

    # ---- collect experiments by role
    exps = meta.get("experiments", [])
    by_role = {}
    for e in exps:
        by_role.setdefault(e.get("role"), []).append(e)
    fs_default = float(exps[0].get("sw_hz", 6900.0)) if exps else 6900.0

    # ---- 5. references first (they anchor the line-position guess)
    refs = []
    for role in ("reference_open", "reference_close"):
        for e in by_role.get(role, []):
            refs.append(analyze_reference_exp(bundle, e, fs_default))
    readable_refs = [r for r in refs if r.get("readable") and
                     r.get("line_center_hz") is not None]
    if readable_refs:
        f0_guess = float(np.mean([r["line_center_hz"] for r in readable_refs]))
        w_ref = float(np.mean([r["fwhm_amp_hz"] for r in readable_refs
                               if r.get("fwhm_amp_hz")]) or 12.0)
    else:
        f0_guess, w_ref = 0.0, 12.0

    # ---- 3. noise blocks: a single 'noise' block, or the field-stepped
    # 'noise_sweep' ladder. Every sweep step is its own axion mass point
    # (mass coordinate = measured carrier + step offset). Each step's
    # line is fitted AT ITS OWN SHIFTED POSITION (fit_line searches only
    # +/-LINE_SEARCH_HZ around its seed, so seeding at the baseline
    # would fit noise for every shifted step); the sign of the shift on
    # this PSD axis is resolved empirically by fitting both candidates
    # and keeping the more significant one. Steps whose offset was never
    # MEASURED are not line-fitted at all -- an unverified target is not
    # a position. The measured step nearest baseline doubles as the
    # headline science block.
    sweep_meta = meta.get("field_sweep") or {}
    sweep_steps_meta = sweep_meta.get("steps") or []

    def _sweep_step_of(expno):
        for s in sweep_steps_meta:
            if s.get("noise_expno") == expno:
                return s
        return None

    def _sweep_measured_of(expno):
        s = _sweep_step_of(expno)
        return s.get("measured_offset_hz") if s else None

    def _fit_significance(r):
        tot = 0.0
        for pr in r["per_row"]:
            ft = pr.get("fit")
            if ft and ft.get("amp_err"):
                tot += min(abs(ft["amp_norm"]) / ft["amp_err"], 10.0)
        return tot

    def _analyze_sweep_step(e, off):
        """Fit at both sign candidates of the measured offset; keep the
        candidate whose per-row line fits are more significant (a real
        line beats a noise fit decisively). Returns (result, seed).

        v0.6 carrier-follow bundles: the receiver window moved WITH the
        step, so the line sits near its baseline LOCAL position and the
        seed is the small residual deviation (measured - target), not
        the full offset -- the sign candidates are the deviation's."""
        step = _sweep_step_of(e.get("expno"))
        if step and step.get("carrier_o1_hz") is not None:
            # Prefer the RECORDED local deviation: when the orchestrator
            # substituted measured = target (sign-unresolved exception),
            # off - target is 0 while the line really sits at the
            # deviation -- seeding at 0 would let a noise fit pass as a
            # measured step (review finding F1, 2026-09-03). Also lets
            # unverified steps (off None) fit at their known local seed.
            dev = step.get("local_deviation_hz")
            if dev is None and off is not None:
                dev = off - (step.get("target_offset_hz") or 0.0)
            dev = dev or 0.0
            cands = [dev, -dev] if dev else [0.0]
        else:
            cands = [off, -off] if off else [0.0]
        best, best_seed, best_score = None, None, -1.0
        for c in cands:
            r = analyze_noise_block(bundle, e, f0_guess + c, fs_default)
            if r is None:
                continue
            score = _fit_significance(r)
            if score > best_score:
                best, best_seed, best_score = r, c, score
        return best, best_seed

    # Standard noise-role experiments form same-parameter groups (sw, td,
    # rg): a Bruker pseudo-2D noise expno is a group of one whose rows
    # are the block; a Tier-1 Agilent session's N single-row noise
    # experiments are ONE block of N rows in meta order, so the co-added
    # periodogram uses the whole session. The largest group is the
    # headline block; any other group is analyzed on its own, listed,
    # and flagged as an inconsistent acquisition set.
    noise_exps = list(by_role.get("noise", []))
    noise_groups = group_noise_experiments(noise_exps)
    noise_heads = [g["exps"][0] for g in noise_groups]
    group_of_head = {g["exps"][0].get("expno"): g for g in noise_groups}
    sweep_exps = sorted(by_role.get("noise_sweep", []),
                        key=lambda e: e.get("expno", 0))
    cf_mode = bool(sweep_meta.get("carrier_follow"))
    frame_indeterminate = []
    analyzed = {}         # expno -> (result_or_None, seed_offset_or_None)
    for g in noise_groups:
        analyzed[g["exps"][0].get("expno")] = (analyze_noise_block(
            bundle, g["exps"], f0_guess, fs_default), 0.0)
    noise_aggregation = None
    if noise_exps:
        noise_aggregation = {
            "n_noise_experiments": len(noise_exps),
            "n_parameter_groups": len(noise_groups),
            "groups": [{"expnos": [e.get("expno") for e in g["exps"]],
                        "sw_hz": g["key"][0], "td": g["key"][1],
                        "rg": g["key"][2],
                        "rows": sum(int(e.get("td1_rows") or 1)
                                    for e in g["exps"]),
                        "readable": analyzed[g["exps"][0].get("expno")][0]
                        is not None}
                       for g in noise_groups],
            "headline_expnos": [e.get("expno")
                                for e in noise_groups[0]["exps"]],
        }
        if len(noise_groups) > 1:
            noise_aggregation["consistent"] = False
            noise_aggregation["note"] = (
                "the %d noise-role experiments do NOT share one parameter "
                "set (sw_hz/td/rg): %d groups found. The largest consistent "
                "group (%d experiment(s), expnos %s) is analyzed as the "
                "headline noise block; the other group(s) are analyzed "
                "separately and listed, but their rows are not co-added "
                "with the headline block"
                % (len(noise_exps), len(noise_groups),
                   len(noise_groups[0]["exps"]),
                   noise_aggregation["headline_expnos"]))
        elif len(noise_exps) > 1:
            noise_aggregation["consistent"] = True
            noise_aggregation["note"] = (
                "%d single-row noise experiments with identical sw_hz/td/rg "
                "aggregated as ONE noise block of %d rows, in meta order "
                "(the operator's acquisition order); per-row records carry "
                "the source expno and its start time"
                % (len(noise_exps), noise_aggregation["groups"][0]["rows"]))
        else:
            noise_aggregation["consistent"] = True
            noise_aggregation["note"] = (
                "one noise experiment (expno %s, %s row(s))"
                % (noise_exps[0].get("expno"),
                   noise_exps[0].get("td1_rows", 1)))
        starts = [parse_local(e.get("started_local", "") or "")
                  for e in noise_groups[0]["exps"]]
        if len(starts) > 1 and all(starts) \
                and any(b < a for a, b in zip(starts, starts[1:])):
            noise_aggregation["time_order_note"] = (
                "the headline block's rows are in meta order but their "
                "recorded start times are not monotonic: drift and "
                "time-series views should be read against each row's "
                "started_local, not its row index")
        if not noise_aggregation["groups"][0]["readable"]:
            noise_aggregation["note"] += (
                " -- but NO row of the headline block could be read (see "
                "the raw-data refusals); the block is not analyzed")
    for e in sweep_exps:
        expno = e.get("expno")
        off = _sweep_measured_of(expno)
        step = _sweep_step_of(expno)
        has_carrier = bool(step and step.get("carrier_o1_hz") is not None)
        if cf_mode and not has_carrier:
            # the sweep declares carrier-follow but this step does not
            # say where its window was: no frame to fit in (review F2)
            analyzed[expno] = (None, None)
            frame_indeterminate.append(expno)
        elif off is None and not has_carrier:
            analyzed[expno] = (None, None)   # v0.5: unverified, no fit
        else:
            # carrier-follow steps fit at their known LOCAL seed even
            # when the step offset was never verified (review F4): the
            # window position is commanded digitally, so the local
            # frame is exact regardless of field verification.
            analyzed[expno] = _analyze_sweep_step(e, off)

    headline_order = noise_heads + sorted(
        [e for e in sweep_exps
         if _sweep_measured_of(e.get("expno")) is not None],
        key=lambda e: abs(_sweep_measured_of(e.get("expno"))))
    noise_res, f0_detect = None, f0_guess
    for e in headline_order:
        res, seed = analyzed.get(e.get("expno"), (None, None))
        if res is not None:
            noise_res = res
            f0_detect = f0_guess + (seed or 0.0)
            break
    sign_status = frequency_axis_sign_status(bundle, meta, f0_guess)

    sweep_analysis = None
    if sweep_exps:
        if cf_mode:
            note = ("carrier-follow sweep (v0.6): the receiver window "
                    "tracked each step, so per-step line centers are in "
                    "the LOCAL moved-window frame (expected near the "
                    "baseline position) and the physical step offset is "
                    "measured_offset_hz in the baseline-field frame; "
                    "the sign convention was resolved once at baseline "
                    "by the carrier-displacement calibration; steps "
                    "whose window position is unknown are not fitted; "
                    "the headline science analysis uses the measured "
                    "step nearest the baseline field")
        else:
            note = ("field-stepped sweep: every step is a distinct "
                    "axion mass point, line-fitted at its own measured "
                    "offset (sign resolved empirically per step); "
                    "steps without a measured offset are listed but "
                    "not fitted; the headline science analysis uses "
                    "the measured step nearest the baseline field")
        sweep_analysis = {
            "headline_expno": (noise_res or {}).get("expno"),
            "carrier_follow": cf_mode,
            "sign_convention_basis":
                sweep_meta.get("sign_convention_basis"),
            "baseline_line_offset_hz":
                sweep_meta.get("baseline_line_offset_hz"),
            "restored_offset_hz": sweep_meta.get("restored_offset_hz"),
            "field_restored": sweep_meta.get("field_restored"),
            "note": note,
            "steps": []}
        if frame_indeterminate:
            sweep_analysis["frame_indeterminate_expnos"] = \
                frame_indeterminate
        unsigned_basis = (
            sweep_meta.get("sign_convention_basis") == "unresolved")
        for e in sweep_exps:
            expno = e.get("expno")
            r, seed = analyzed.get(expno, (None, None))
            off = _sweep_measured_of(expno)
            basis = "unverified"
            if off is not None:
                basis = "measured"
                if unsigned_basis:
                    # the documented v0.6 exception: measured == target,
                    # confirmed only by an unsigned deviation
                    basis = "target_confirmed_unsigned"
            entry = {"expno": expno, "measured_offset_hz": off,
                     "offset_basis": basis,
                     "rows": e.get("td1_rows"), "readable": bool(r)}
            for s in sweep_steps_meta:
                if s.get("noise_expno") == expno:
                    entry["target_offset_hz"] = s.get("target_offset_hz")
                    if s.get("local_deviation_hz") is not None:
                        entry["local_deviation_hz"] = s.get(
                            "local_deviation_hz")
                    if s.get("note"):
                        entry["orchestrator_note"] = s.get("note")
                    if s.get("carrier_o1_hz") is not None:
                        entry["carrier_o1_hz"] = s.get("carrier_o1_hz")
                        entry["lock_shift_target_ppm"] = s.get(
                            "lock_shift_target_ppm")
            if expno in frame_indeterminate:
                entry["note"] = ("sweep declares carrier_follow but this "
                                 "step carries no carrier_o1_hz: window "
                                 "frame unknown, no line fit (QA WARN)")
            elif off is None and entry.get("carrier_o1_hz") is not None:
                entry["note"] = ("step offset never verified -- no "
                                 "mass-point position is claimed; the "
                                 "line is still fitted in the (exactly "
                                 "known) local window frame so the "
                                 "block serves the line catalog")
            elif off is None:
                entry["note"] = ("offset never measured (verification "
                                 "failed or was skipped): no line fit -- "
                                 "an unverified target is not a position")
            if r:
                entry["fit_seed_offset_hz"] = seed
                # line position: mean of per-row fit centers (the coadd
                # fit is in the self-aligned frame and carries only a
                # residual shift)
                centers = [pr["fit"]["center_hz"] for pr in r["per_row"]
                           if "fit" in pr]
                if centers:
                    entry["line_center_hz"] = float(np.mean(centers))
                    entry["line_center_spread_hz"] = float(
                        max(centers) - min(centers))
                cf, cf_key = headline_fit(r)
                if cf is not None:
                    entry["fwhm_hz"] = cf.get("fwhm_hz")
                    entry["amp_norm"] = cf.get("amp_norm")
                    entry["amp_err"] = cf.get("amp_err")
                    entry["fit_basis"] = cf_key
            sweep_analysis["steps"].append(entry)

    detection = {"detected": False}
    if noise_res is None:
        # no readable noise block: say so, claim nothing -- a null result
        # computed from nothing (or from misread bytes) is exactly the
        # silent failure this report must never produce
        why = []
        for e in noise_exps + sweep_exps:
            expno = e.get("expno")
            if expno in bundle.read_errors:
                why.append("expno %s: %s" % (expno, bundle.read_errors[expno]))
        if not noise_exps and not sweep_exps:
            why.append("the bundle lists no noise-role experiment")
        detection.update({
            "unavailable": True,
            "reason": "; ".join(why) or "no noise block could be analyzed"})
    # every feature number below comes from ONE fit of the headline
    # block: the aligned co-add, or the unaligned stack when the
    # alignment cross-check judged the co-add manufactured
    coadd, coadd_key = headline_fit(noise_res)
    if coadd is not None:
        cf = coadd
        amp_sig = abs(cf["amp_norm"]) / cf["amp_err"] if cf["amp_err"] else 0.0
        npes = [pr.get("npe_at_line", 0.0) for pr in noise_res["per_row"]
                if "fit" in pr]
        npe_comb = float(math.sqrt(sum(v * v for v in npes)))
        centers = [pr["fit"]["center_hz"] for pr in noise_res["per_row"]
                   if "fit" in pr]
        detection.update({
            "amp_significance": amp_sig, "npe_combined": npe_comb,
            "line_center_hz": float(np.mean(centers)) if centers else None,
            "line_center_drift_hz": (float(max(centers) - min(centers))
                                     if centers else None),
            "line_center_drift_note": (
                "spread (max - min) of the per-row fitted line centers -- a "
                "per-row fit-scatter measure that grows when rows are "
                "noise-dominated; NOT the open-to-close reference drift, "
                "which is references[].line_stability_hz"),
            "fit_basis": coadd_key,
            "fit_amp_norm": cf["amp_norm"], "fit_amp_err": cf["amp_err"],
            "fit_fwhm_hz": cf["fwhm_hz"],
        })
        if coadd_key == "unaligned_fit":
            detection["fit_basis_note"] = (
                "detection and headline numbers use the UNALIGNED stack's "
                "fit (noise.unaligned_fit): the self-aligned co-add "
                "(noise.coadd_fit, amp %.3f) was judged SUSPECT by the "
                "alignment cross-check and is kept for reference only"
                % noise_res["coadd_fit"]["amp_norm"])
        if amp_sig >= DETECT_NSIGMA:
            detection["detected"] = True
    if noise_res and not detection["detected"]:
        # calibrated upper limit at the reference-anchored position
        # (shifted by the headline sweep step's measured offset when the
        # headline block is a field-stepped one)
        r0 = noise_res["_rows"][0]
        # co-add unaligned (no feature to align on)
        stack = np.mean([rr["pnorm"] for rr in noise_res["_rows"]], axis=0)
        ul, ul_details = upper_limit_at(
            r0["f"], stack, f0_detect,
            [max(0.5 * w_ref, 2.0), w_ref, 2.0 * w_ref])
        detection["upper_limit_95_amp"] = ul
        detection["upper_limit_details"] = ul_details
        detection["upper_limit_note"] = (
            "95%% one-sided limit on |feature amplitude| relative to the "
            "floor, at the reference-anchored line position%s, profiled "
            "over widths 0.5/1/2 x the reference linewidth (%.1f Hz)"
            % ((" shifted %+.1f Hz for the headline sweep step"
                % (f0_detect - f0_guess))
               if abs(f0_detect - f0_guess) > 1e-9 else "", w_ref))

    # ---- v0.6 riders: persistent-line catalog, sub-virial pass, and
    # the axion-mass / axial-vector bookkeeping.
    catalog_blocks = []
    carrier_shifts = {}
    base_o1 = sweep_meta.get("baseline_carrier_o1_hz")
    for e in noise_heads + sweep_exps:
        expno = e.get("expno")
        r, seed = analyzed.get(expno, (None, None))
        if not r:
            continue
        step = _sweep_step_of(expno)
        shift = 0.0
        if step and step.get("carrier_o1_hz") is not None:
            # exact commanded window shift; fall back to the target for
            # writers that record carrier_o1_hz without the baseline
            if base_o1 is not None:
                shift = float(step["carrier_o1_hz"]) - float(base_o1)
            else:
                shift = float(step.get("target_offset_hz") or 0.0)
        # spin tag anchored on the FITTED line position when available:
        # a mis-seeded block must not let the spin line masquerade as a
        # window-fixed spur (review F4)
        f0_loc = f0_guess + (seed or 0.0)
        centers = [pr["fit"]["center_hz"] for pr in r["per_row"]
                   if "fit" in pr]
        if centers:
            f0_loc = float(np.mean(centers))
        carrier_shifts[expno] = shift
        catalog_blocks.append({"expno": expno, "res": r,
                               "carrier_shift_hz": shift,
                               "f0_local": f0_loc})
    line_catalog = (persistent_line_catalog(catalog_blocks, w_ref)
                    if catalog_blocks else None)
    subvirial = None
    if noise_res:
        head_exps = None
        for e in noise_heads + sweep_exps:
            if e.get("expno") == noise_res.get("expno"):
                g = group_of_head.get(e.get("expno"))
                head_exps = g["exps"] if g else [e]
                break
        if head_exps is not None:
            try:
                subvirial = subvirial_pass(bundle, head_exps, f0_detect,
                                           w_ref, fs_default)
            except Exception as exc:
                subvirial = {"error": str(exc)}
    mass_book = axion_mass_bookkeeping(meta, sign_status)

    # ---- 4. RG ladder
    ladder = analyze_rg_ladder(bundle, meta, f0_guess, fs_default)

    # ---- floor calibration / reference-noise consistency
    ref_pair = reference_pair_check(refs)
    floor_cal = {}
    if noise_res and readable_refs:
        opens = [r for r in readable_refs if r["role"] == "reference_open"]
        closes = [r for r in readable_refs if r["role"] == "reference_close"]
        if opens and closes:
            floor_cal["line_stability_open_close_hz"] = float(
                closes[0]["line_center_hz"] - opens[0]["line_center_hz"])
            if opens[0].get("A0_counts") and closes[0].get("A0_counts"):
                floor_cal["a0_ratio_close_over_open"] = float(
                    closes[0]["A0_counts"] / opens[0]["A0_counts"])
        rg_noise = noise_res.get("rg") or 1.0
        rg_ref = readable_refs[0].get("rg") or 1.0
        gain = rg_noise / rg_ref if rg_ref else None
        floors = [pr.get("baseline_psd_at_line") for pr in noise_res["per_row"]
                  if pr.get("baseline_psd_at_line")]
        if floors and gain:
            fl = float(np.mean(floors))
            floor_cal["noise_floor_counts2perhz_at_noise_rg"] = fl
            floor_cal["gain_ratio_amplitude"] = gain
            floor_cal["noise_floor_bridged_to_ref_rg"] = fl / gain ** 2
            tails = [r.get("tail_floor_counts2perhz") for r in readable_refs
                     if r.get("tail_floor_counts2perhz")]
            if tails:
                floor_cal["floor_consistency_bridged_over_ref_tail"] = float(
                    (fl / gain ** 2) / np.mean(tails))
                floor_cal["floor_consistency_note"] = (
                    "reference tail floor uses the last 30% of each reference "
                    "row and may retain residual signal for slowly decaying "
                    "FIDs. A ratio near 1 confirms the gain bridge; a ratio "
                    "far BELOW 1 with a low-RG reference usually means the "
                    "reference floor is digitizer/backend dominated (front-"
                    "end noise attenuated below the ADC floor at low gain) "
                    "-- expected, and why the 2020 calibration bridged gains "
                    "on the pulsed SIGNAL amplitude, not on the floor. Other "
                    "departures from 1 flag RG nonlinearity or a gain-chain "
                    "change between blocks.")
            if detection.get("detected"):
                floor_cal["spin_line_integrated_counts2_at_ref_rg"] = float(
                    coadd["amp_norm"] * math.pi * coadd["fwhm_hz"] / 2.0 * fl
                    / gain ** 2)
                floor_cal["spin_line_fit_basis"] = coadd_key
        if ref_pair and not ref_pair.get("matched") \
                and "a0_ratio_close_over_open" in floor_cal:
            floor_cal["a0_ratio_caveat"] = ref_pair["note"]

    # ---- 6. headline numbers
    headline_notes = []
    temp_contrast = temperature_contrast_point(meta, coadd, headline_notes)
    headline = headline_numbers(meta, noise_res, coadd, detection, ladder,
                                headline_notes)
    if detection.get("detected") and coadd:
        expected = {"RT": "dip", "N2-cryo": "bump", "He-cryo": "bump"}.get(
            meta.get("spectrometer", {}).get("probe_type"))
        got = "bump" if coadd["amp_norm"] > 0 else "dip"
        headline["sign_vs_probe_type"] = (
            "consistent (%s probe, %s observed)" % (
                meta["spectrometer"]["probe_type"], got)
            if expected == got else
            "UNEXPECTED: %s probe but %s observed -- check tuning state and "
            "temperatures%s" % (
                meta["spectrometer"].get("probe_type"), got,
                (" (a positive or strongly dispersive RT lineshape is "
                 "consistent with a probe tuned away from the spin-noise "
                 "tuning optimum, which the literature places off the "
                 "pulse-response tuning optimum; the absorptive sign itself "
                 "is set by the noise temperature the spins see -- coil "
                 "plus amplifier back-emission -- relative to their own, "
                 "which this bundle does not determine; dispersive fraction "
                 "b/a here %.2f)"
                 % (coadd.get("asymmetry_b_over_a") or 0.0))
                if got == "bump" and expected == "dip" else ""))

    # ---- 7. QA
    qa = qa_flags(bundle, meta, noise_res, val_msgs)
    if detection.get("detected") and "UNEXPECTED" in str(
            headline.get("sign_vs_probe_type", "")):
        qa.append({"level": "WARN", "check": "feature sign vs probe type",
                   "detail": headline["sign_vs_probe_type"]})
    if frame_indeterminate:
        qa.append({"level": "WARN",
                   "check": "carrier-follow frame consistency",
                   "detail": ("field_sweep declares carrier_follow but "
                              "steps %s carry no carrier_o1_hz -- their "
                              "window frame is unknown and they were "
                              "not line-fitted"
                              % frame_indeterminate)})
    if noise_aggregation and not noise_aggregation.get("consistent"):
        qa.append({"level": "WARN", "check": "noise block consistency",
                   "detail": noise_aggregation["note"]})
    if noise_res and noise_res.get("skipped_experiments"):
        qa.append({"level": "WARN", "check": "noise block completeness",
                   "detail": "noise experiments left out of the headline "
                             "block: %s" % "; ".join(
                                 "expno %s -- %s" % (s["expno"], s["why"])
                                 for s in noise_res["skipped_experiments"])})
    if detection.get("unavailable"):
        qa.append({"level": "FAIL", "check": "spin-noise analysis",
                   "detail": "UNAVAILABLE: %s" % detection["reason"]})
    if noise_res and (noise_res.get("alignment_check") or {}).get("suspect"):
        qa.append({"level": "WARN", "check": "co-add alignment",
                   "detail": noise_res["alignment_check"]["verdict"]})
    if ref_pair and not ref_pair.get("matched"):
        qa.append({"level": "WARN", "check": "reference pair",
                   "detail": ref_pair["note"]})
    if noise_aggregation and noise_aggregation.get("time_order_note"):
        qa.append({"level": "WARN", "check": "noise row time order",
                   "detail": noise_aggregation["time_order_note"]})
    if not sign_status.get("verified"):
        qa.append({"level": "WARN", "check": "frequency-axis sign",
                   "detail": sign_status["note"]})

    # ---- raw-data read path (what was read, by which format, and what
    # was refused) -- stated so a misread can never hide behind numbers
    raw_read = {"vendor": bundle.vendor,
                "by_expno": {str(k): v for k, v in
                             sorted(bundle.read_log.items())},
                "refused": {str(k): v for k, v in
                            sorted(bundle.read_errors.items())}}
    formats = sorted(set(v["format"] for v in bundle.read_log.values()))
    dtypes = sorted(set(v["dtype"] for v in bundle.read_log.values()))
    raw_read["formats"] = formats
    raw_read["dtypes"] = dtypes
    if "agilent" in formats:
        raw_read["agilent_note"] = (
            "Agilent/Varian fid: 32-byte big-endian file header verified "
            "against the file size, samples read as %s from the file's "
            "status bits (never a default), np = total re+im points as "
            "Bruker TD, one row per block/trace; each block's 28-byte "
            "header is checked (element-type bits must match the file "
            "header, scale must be 0 -- a scaled block is refused, not "
            "rescaled) and never read as samples. The per-row complex mean "
            "is recorded (dc_offset_max_abs) but NOT subtracted: the Welch "
            "PSD detrends every segment and the reference/ladder spectra "
            "remove their own mean, so a Varian DC offset reaches no "
            "analysed spectrum, while the time-domain A0 back-extrapolation "
            "sees the raw samples exactly as it does for Bruker data. No "
            "digital-filter group delay is applied (GRPDLY 0); receiver "
            "gain rg = 10^(gain/20) from procpar; integer S_32 samples "
            "carry no assumed full scale for the clipping check."
            % "/".join(dtypes))

    # ---- axion-coupling exclusion (worst-case, this session), then the
    # site combination over --prior-reports
    excl = axion_exclusion(meta, noise_res, refs, detection, floor_cal,
                           ladder, f0_guess, f0_detect, sign_status,
                           bool(sweep_exps),
                           carrier_shifts.get((noise_res or {}).get("expno"),
                                              0.0))
    if prior_reports:
        current = {"facility_slug": report["facility_slug"],
                   "bundle": report["bundle"],
                   "bundle_sha256": report["bundle_sha256"],
                   "generated_utc": report["generated_utc"],
                   "report_version": REPORT_VERSION,
                   "report_type": report["report_type"],
                   "science": {"axion_exclusion": excl}}
        # this report first: an earlier report of the same bundle among the
        # priors is then the duplicate that is skipped, never this one
        excl["site_combined"] = site_exclusion_module().combine_reports(
            [(os.path.join(out_dir, "report.json"), current)] + prior_reports,
            per_session_columns=False)

    # ---- honesty section
    honesty = [
        "Determined: the receiver's measured noise spectrum around the "
        "observed line, the feature contrast (or its upper limit), "
        "linewidth, "
        "dispersive admixture, floor calibration against the small-flip "
        "references, and the QA state of the acquisition.",
        "The distance-from-ceiling number is contrast-based and holds for "
        "THIS sample, tuning state, and temperature; it is not a universal "
        "property of the spectrometer.",
    ]
    if not ladder.get("available"):
        honesty.append("NOT determined: receiver-gain linearity (no usable "
                       "RG ladder in this bundle) -- the 2020 pilot's "
                       "largest untested systematic; the +/-20% power "
                       "envelope is assumed instead.")
    if "requires" in str(temp_contrast.get("status", "")):
        honesty.append("NOT determined: the temperature-contrast point "
                       "(coil/preamp temperatures absent from meta.json); "
                       "declaring them in a future run upgrades this report "
                       "for free.")
    if not detection.get("detected"):
        honesty.append("A null here does not distinguish a weakly coupled "
                       "receiver from a weakly protonated sample (2022 "
                       "lesson); the recorded H2O fraction is %s%%."
                       % meta.get("sample", {}).get("h2o_fraction_pct"))
    else:
        honesty.append("The feature contrast and the spin-coupled floor "
                       "fraction hold for the recorded sample only (H2O "
                       "fraction %s%%): the same probe with a differently "
                       "protonated tube gives a different a/(1+a)."
                       % meta.get("sample", {}).get("h2o_fraction_pct"))
    if run_mode == "archival-repackage":
        honesty.append("Archival repackage: acquisition predates the network "
                       "protocol; RG ladder and declared temperatures were "
                       "not part of the original session.")
    if (sweep_analysis
            and sweep_meta.get("sign_convention_basis") == "unresolved"):
        honesty.append("Sweep sign convention UNRESOLVED for this session: "
                       "per-step offsets labeled 'target_confirmed_"
                       "unsigned' are the commanded targets, confirmed "
                       "only by an unsigned deviation within the "
                       "substitution cap -- they are not signed "
                       "measurements (the documented v0.6 exception to "
                       "the measured-not-target rule).")
    if detection.get("unavailable"):
        honesty.insert(0, "NOT determined: the spin-noise feature -- no "
                          "noise block could be read (%s). Nothing above "
                          "about the feature is a measurement."
                       % detection["reason"])
    if bundle.read_errors:
        honesty.append("NOT read: expno(s) %s were excluded because nothing "
                       "in the bundle declares their raw-data layout; see "
                       "the QA FAIL rows -- no dtype or byte order was "
                       "guessed for them."
                       % sorted(bundle.read_errors))
    if not sign_status.get("verified"):
        honesty.append("Frequency-axis SIGN unverified (Agilent/Varian, "
                       "vendor checklist item 2): every line offset in this "
                       "report is known up to sign only%s; contrast, widths "
                       "and dip depth are unaffected."
                       % ((" (+/-%.2f ppm two-way ambiguity)"
                           % sign_status["two_way_ambiguity_ppm"])
                          if sign_status.get("two_way_ambiguity_ppm")
                          else ""))
    if noise_aggregation and noise_aggregation.get(
            "n_noise_experiments", 0) > 1:
        honesty.append("Noise block composition: %s."
                       % noise_aggregation["note"])
    if noise_res and (noise_res.get("alignment_check") or {}).get("suspect"):
        honesty.append("Co-add alignment %s"
                       % noise_res["alignment_check"]["verdict"])
    if ref_pair and not ref_pair.get("matched"):
        honesty.append("Reference pair: %s; the open/close A0 ratio and "
                       "line-position stability are NOT a drift bracket "
                       "for this session." % ref_pair["note"])
    if excl.get("available"):
        honesty.append(
            "Axion-coupling exclusion: a WORST-CASE construction "
            "(g_ap < %.3g GeV^-1 at %.6f ueV, %.1e times above the SN1987A "
            "bound), unpublished --- its card lists every input and "
            "caveat, and the limit does NOT improve with more measurement "
            "time in this construction."
            % (excl["result"]["g90_worst_best_gev_inv"],
               excl["result"]["m_a_at_best_uev"],
               excl["result"]["ratio_to_sn1987a_bound"]))
    else:
        honesty.append("NOT determined: the axion-coupling exclusion (%s)."
                       % excl.get("reason", "?"))

    figs = make_figures(noise_res, refs, ladder, detection)
    ctx = {"report_type": report["report_type"], "meta": meta,
           "bundle_path": bundle_path, "validation_msgs": val_msgs,
           "validation_ok": val_ok, "noise": noise_res, "refs": refs,
           "ladder": ladder, "detection": detection, "headline": headline,
           "temp_contrast": temp_contrast, "floor_cal": floor_cal,
           "qa": qa, "figs": figs, "honesty": honesty,
           "names": sorted(bundle.names), "clock": clock,
           "field_sweep": sweep_analysis,
           "line_catalog": line_catalog, "subvirial": subvirial,
           "mass_book": mass_book,
           "sign_status": sign_status, "raw_read": raw_read,
           "noise_aggregation": noise_aggregation,
           "reference_pair": ref_pair,
           "axion_exclusion": excl,
           "rd_optimize": (meta.get("calibration") or {}).get("rd_optimize")}
    html = render_html(ctx)

    report["science"] = strip_private({
        "line_position_guess_hz": f0_guess,
        "reference_linewidth_hz": w_ref,
        "reference_linewidth_note": (
            "magnitude-spectrum FWHM of the pulsed reference line (fit on "
            "|S(f)|); the noise-line FWHM under detection/noise is a "
            "POWER-spectrum width. For one Lorentzian the magnitude width "
            "is sqrt(3) times the power width, so compare noise widths "
            "with reference widths divided by 1.73 (plus lineshape "
            "asymmetry)."),
        "frequency_axis_sign": sign_status,
        "raw_data_read": raw_read,
        "noise_aggregation": noise_aggregation,
        "noise": noise_res, "references": refs,
        "reference_pair": ref_pair, "rg_ladder": ladder,
        "detection": detection, "headline": headline,
        "temperature_contrast": temp_contrast, "floor_calibration": floor_cal,
        "field_sweep": sweep_analysis,
        "line_catalog": line_catalog,
        "subvirial_pass": subvirial,
        "axion_mass_bookkeeping": mass_book,
        "axion_exclusion": excl,
        "rd_optimize": (meta.get("calibration") or {}).get("rd_optimize"),
        "qa_flags": qa, "honesty": honesty,
    })
    _write(out_dir, html, report)
    print("report written to %s" % out_dir)
    return 0


def _write(out_dir, html, report):
    with open(os.path.join(out_dir, "report.html"), "w") as fh:
        fh.write(html)
    with open(os.path.join(out_dir, "report.json"), "w") as fh:
        json.dump(strip_private(report), fh, indent=1, default=float)
        fh.write("\n")


if __name__ == "__main__":
    sys.exit(main())
