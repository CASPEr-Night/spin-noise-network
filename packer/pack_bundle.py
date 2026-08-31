#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pack_bundle.py -- standalone, vendor-neutral spin-noise bundle packer.

    python3 packer/pack_bundle.py <data_dir> --answers answers.json \\
        [--vendor bruker] [--out-dir DIR] [--schema PATH] [--no-validate]

Input : a directory of vendor data files (Bruker: one numeric expno
        subdirectory per experiment, each containing acqus [+ acqu2s,
        fid/ser]) plus an answers.json -- the operator questionnaire,
        carrying the SAME information the TopSpin dialogs collect
        (facility, sample, environment, calibration, per-experiment
        roles).  See packer/answers.example.json.
Output: a validated bundle zip identical in layout to what
        topspin/spin_noise_run.py produces -- meta.json (schema 2.0) at
        the zip root, the data tree under data/<expno>/, sha256
        checksums, filename spinnoise_<slug>_<YYYYMMDD_HHMMSSZ>_<hex>.zip
        -- ready for uploader/upload_bundle.py.

Why this exists: the TopSpin Jython orchestrator only runs on Bruker.
This packer decouples the bundle format from the acquisition path, so a
facility (or partner package) can acquire with the vendor's own tooling
and still produce a contract-conforming bundle.  Vendor support is
pluggable through reader adapters (see VendorReader below):

    bruker   : IMPLEMENTED.  Parses the JCAMP-DX acqus/acqu2s parameter
               files (##$TD=, ##$SW_h=, ##$SFO1=, ...).  Can round-trip
               an existing bundle's expno tree back into an equivalent
               bundle -- testing/test_pack_roundtrip.py tests exactly
               that against a harness desktest bundle.
    jeol     : IMPLEMENTED as a DRAFT pending partner-facility validation
               (work package C).  Delegates format parsing to
               vendors/jeol/jeol_reader.py (native .jdf, verified against
               the public jeol-data-test corpus, plus the JCAMP-DX export
               fallback); see JeolReader and vendors/jeol/README.md
               (operator protocol + validation checklist).
    magritek : IMPLEMENTED as a DRAFT pending bench validation (work
               package B).  Delegates format parsing to
               vendors/magritek/magritek_reader.py; see MagritekReader
               and vendors/magritek/README.md (validation checklist).
    agilent  : IMPLEMENTED as a DRAFT pending partner-facility validation
               (Agilent/Varian VnmrJ + OpenVnmrJ lineage).  Delegates
               format parsing to vendors/agilent/agilent_reader.py
               (binary fid + procpar per nmrglue's varian reader); see
               AgilentReader and vendors/agilent/README.md (operator
               protocol + validation checklist).

Same portability rules as the uploader: Python 3 STANDARD LIBRARY ONLY,
nothing newer than 3.6 (facility workstations).  The packer never
modifies the input data directory.

Validation checklist for the partner-facility sessions (anti-fabrication
rule: anything below that could not be verified against real vendor
documentation carries an UNVERIFIED marker in code and must be confirmed
on real hardware/software before the corresponding reader is trusted):
  * JEOL: .jdf header layout and parameter names -- port/verify against
    the MIT-licensed cheminfo jeolconverter parser, v1.0.1
    (https://www.npmjs.com/package/jeolconverter) with attribution, then
    confirm against files exported by the partner's Delta version.
  * JEOL: machine-readable source of the Delta software version and
    receiver-gain units (instrument.jeol.receiver_gain).
  * Magritek: acqu.par parameter names/units (rx gain, sweep, points)
    and the data.1d binary layout -- nmrglue's spinsolve reader
    (https://nmrglue.readthedocs.io/en/latest/reference/spinsolve.html)
    documents the file family (acqu.par, data.1d/fid.1d, proc.par under
    SpinsolveExpert); exact fields must be confirmed on a bench.
  * Agilent/Varian: fid binary layout and procpar text format follow
    nmrglue's varian reader (nmrglue/fileio/varian.py, BSD-3-Clause,
    https://github.com/jjhelmus/nmrglue) -- but unlike the JEOL path, NO
    public corpus of raw .fid directories was found to verify against,
    so the whole format implementation awaits real VnmrJ 3.2 output
    (vendors/agilent/README.md checklist).  Receiver-gain ('gain', dB)
    range/step per console model, 'tof' offset convention, and procpar
    timestamp semantics are UNVERIFIED.
  * Bruker: nothing pending -- acqus parameter names used here (TD,
    SW_h, SFO1, BF1, O1, RG, NS, PULPROG, BYTORDA, DTYPA, GRPDLY) are
    standard JCAMP-DX labels already exercised by this repository's
    harness and archival repackaging against real 2020 files.
"""

from __future__ import print_function

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import zipfile

# Kept in sync with the repository VERSION file (a literal, because this
# script may be copied standalone); testing/static_check.py enforces it.
PACKER_VERSION = "0.5.0"
SCHEMA_VERSION = "2.0"

GAMMA_1H_MHZ_PER_T = 42.5774806   # same constant spin_noise_run.py uses

ROLES = ("setup", "rg_ladder", "reference_open", "noise", "reference_close")
RUN_MODES = ("live", "simulate", "desktest", "archival-repackage",
             "synthetic-injection", "external-acquisition")

CHUNK = 1024 * 1024


def info(msg):
    print(msg, file=sys.stderr)


class PackError(Exception):
    """Fatal packing problem with an operator-readable message."""


# ---------------------------------------------------------------------------
# Vendor adapter interface
# ---------------------------------------------------------------------------

class VendorReader(object):
    """Contract every vendor adapter implements (packages B and C: this is
    the interface your reader plugs into -- register it in VENDOR_READERS).

    A reader NEVER invents values: anything it cannot determine from the
    vendor files it leaves out of its returned dicts, and the packer then
    requires the answers.json to supply it (missing required values abort
    with a message listing exactly what to add).
    """

    #: registry key and meta.json vendor enum value
    name = None

    def discover_experiments(self, data_dir):
        """Return a sorted list of (expno:int, dirpath:str) for every
        experiment found under data_dir.  Raise PackError with a helpful
        message if the directory does not look like this vendor's data."""
        raise NotImplementedError

    def read_experiment(self, dirpath):
        """Return a dict of parameters discovered from the vendor files in
        one experiment directory.  Recognized keys (all optional -- omit
        what the files do not carry): td, td1_rows, sw_hz, o1_hz, rg, ns,
        pulprog, h1_freq_mhz, aq_s_per_row.  Extra vendor-specific keys
        are allowed and end up in the instrument block via
        instrument_block()."""
        raise NotImplementedError

    def instrument_block(self, answers, discovered):
        """Return (block_dict, warnings:list-of-str) for
        meta['instrument'][self.name], built from the vendor-specific part
        of answers.json (answers.get('instrument', {})) plus whatever
        read_experiment() discovered.  Must satisfy the schema's
        instrument.<vendor> requireds or raise PackError naming the
        missing answers."""
        raise NotImplementedError


class BrukerReader(VendorReader):
    """Bruker TopSpin expno trees (fully implemented).

    Parameter files (acqus, acqu2s) are JCAMP-DX text with lines like
    '##$SW_h= 12019.2307692308' and '##$PULPROG= <zg30>'.  The parameter
    names used here are the standard Bruker acquisition-status labels,
    the same ones this repository already reads and writes elsewhere
    (testing/jython_entry.py template, testing/repackage_epfl.py against
    the real 2020 EPFL files) and that open-source readers (e.g.
    nmrglue's bruker module) parse identically.
    """

    name = "bruker"

    _JCAMP_RE = re.compile(r"^##\$?([A-Za-z_0-9]+)=\s*(.*)$")

    @classmethod
    def parse_jcamp(cls, path):
        """Parse a Bruker JCAMP-DX parameter file into {name: string}.
        Values in <angle brackets> are unwrapped; array blocks ('(0..31)'
        headers) are joined onto one line.  Text only -- no numeric
        coercion here."""
        params = {}
        try:
            with open(path, "r", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError as exc:
            raise PackError("cannot read %s: %s" % (path, exc))
        key = None
        buf = []
        for line in lines:
            m = cls._JCAMP_RE.match(line)
            if m:
                if key is not None:
                    params[key] = " ".join(buf).strip()
                key = m.group(1)
                buf = [m.group(2).strip()]
            elif key is not None and not line.startswith("##"):
                buf.append(line.strip())
        if key is not None:
            params[key] = " ".join(buf).strip()
        # unwrap <...> string convention
        for k, v in list(params.items()):
            if v.startswith("<") and v.endswith(">"):
                params[k] = v[1:-1]
        return params

    @staticmethod
    def _num(params, name):
        v = params.get(name)
        if v is None or v == "":
            return None
        try:
            return float(v)
        except ValueError:
            return None

    def discover_experiments(self, data_dir):
        out = []
        try:
            names = sorted(os.listdir(data_dir))
        except OSError as exc:
            raise PackError("cannot list data directory %s: %s"
                            % (data_dir, exc))
        for name in names:
            d = os.path.join(data_dir, name)
            if name.isdigit() and os.path.isdir(d) \
                    and os.path.isfile(os.path.join(d, "acqus")):
                out.append((int(name), d))
        if not out:
            raise PackError(
                "no Bruker experiments found under %s -- expected numeric "
                "expno subdirectories each containing an 'acqus' file "
                "(point me at the dataset NAME directory, e.g. "
                ".../SPINNOISE_20260826, or at an unpacked bundle's data/ "
                "directory)" % data_dir)
        out.sort()
        return out

    def read_experiment(self, dirpath):
        acqus = self.parse_jcamp(os.path.join(dirpath, "acqus"))
        found = {}
        td = self._num(acqus, "TD")
        if td is not None and td > 0:
            found["td"] = int(td)
        sw = self._num(acqus, "SW_h")
        if sw is not None and sw > 0:
            found["sw_hz"] = sw
        o1 = self._num(acqus, "O1")
        if o1 is not None:
            found["o1_hz"] = o1
        rg = self._num(acqus, "RG")
        if rg is not None and rg > 0:
            found["rg"] = rg
        ns = self._num(acqus, "NS")
        if ns is not None and ns >= 1:
            found["ns"] = int(ns)
        if acqus.get("PULPROG"):
            found["pulprog"] = acqus["PULPROG"]
        bf1 = self._num(acqus, "BF1")
        if bf1 is None:
            bf1 = self._num(acqus, "SFO1")
        if bf1 is not None and bf1 > 0:
            found["h1_freq_mhz"] = bf1
        # rows: acqu2s F1 TD when a ser file exists; plain 1D otherwise.
        acqu2s_path = os.path.join(dirpath, "acqu2s")
        if os.path.isfile(acqu2s_path):
            acqu2s = self.parse_jcamp(acqu2s_path)
            rows = self._num(acqu2s, "TD")
            if rows is not None and rows >= 1:
                found["td1_rows"] = int(rows)
        elif os.path.isfile(os.path.join(dirpath, "fid")):
            found["td1_rows"] = 1
        if "td" in found and "sw_hz" in found:
            found["aq_s_per_row"] = found["td"] / (2.0 * found["sw_hz"])
        # provenance extras for the instrument block
        title = acqus.get("TITLE", "")
        m = re.search(r"TopSpin\s+([0-9][0-9a-zA-Z().]*)", title)
        if m:
            found["_topspin_version_guess"] = m.group(1)
        return found

    def instrument_block(self, answers, discovered):
        warnings = []
        ans_inst = answers.get("instrument", {})
        ts_version = ans_inst.get("topspin_version")
        if not ts_version:
            guesses = set(d.get("_topspin_version_guess")
                          for d in discovered.values()
                          if d.get("_topspin_version_guess"))
            if len(guesses) == 1:
                ts_version = guesses.pop()
                warnings.append("topspin_version %r taken from the acqus "
                                "TITLE line (not answered in answers.json)"
                                % ts_version)
        if not ts_version:
            raise PackError(
                "answers.json is missing instrument.topspin_version and it "
                "could not be read from the acqus TITLE lines -- add e.g. "
                '"instrument": {"topspin_version": "4.1.4"}')
        sweep = answers.get("environment", {}).get("field_sweep_confirmed_off")
        if not isinstance(sweep, bool):
            raise PackError(
                "answers.json must answer environment."
                "field_sweep_confirmed_off (true/false) -- the same BSMS "
                "sweep confirmation the TopSpin dialog asks; a running "
                "sweep ruins the noise record")
        block = {
            "topspin_version": ts_version,
            "bsms_field_sweep_confirmed_off": sweep,
        }
        if ans_inst.get("console_raw"):
            block["console_raw"] = ans_inst["console_raw"]
        if "rga_used" in ans_inst:
            block["rga_used"] = ans_inst["rga_used"]
        return block, warnings


class JeolReader(VendorReader):
    """JEOL Delta datasets: a flat directory of per-experiment files --
    native .jdf, or Delta JCAMP-DX exports (.jdx/.dx) as the fallback
    path -- one file per experiment, named per the Tier-1 operator
    checklist (vendors/jeol/README.md): a numeric prefix maps each file
    onto the Bruker expno plan, e.g. 11_sn_ref_open.jdf, 12_sn_noise.jdf.

    DRAFT PENDING HARDWARE VALIDATION.  Parsing is delegated to
    vendors/jeol/jeol_reader.py; see its provenance block: the .jdf
    layout is ported from the MIT-licensed cheminfo/jeolconverter parser
    (https://www.npmjs.com/package/jeolconverter) and was verified
    against 38 real Delta files from the public jeol-data-test corpus;
    the JCAMP-DX path follows the published standard (McDonald & Wilks
    1988; Davies & Lampen 1993).  Still UNVERIFIED and awaiting the
    partner-facility session (vendors/jeol/README.md checklist):
    receiver-gain units/linearity, timestamp timezone, no-pulse
    expressibility in Delta, and whether a live ECZ/ECZL console writes
    anything the corpus did not contain.
    """

    name = "jeol"

    _EXTS = (".jdf", ".jdx", ".dx")
    _PREFIX_RE = re.compile(r"^(\d+)_")

    def __init__(self):
        self._mod = None
        self._warnings = []

    def _reader(self):
        """Lazy-import vendors/jeol/jeol_reader.py (stdlib importlib)."""
        if self._mod is None:
            import importlib.util
            here = os.path.dirname(os.path.abspath(__file__))
            path = os.path.abspath(os.path.join(
                here, "..", "vendors", "jeol", "jeol_reader.py"))
            if not os.path.isfile(path):
                raise PackError("vendors/jeol/jeol_reader.py not found "
                                "(looked at %s) -- the JEOL adapter needs "
                                "the repository checkout, not a standalone "
                                "packer copy" % path)
            spec = importlib.util.spec_from_file_location("snn_jeol", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._mod = mod
        return self._mod

    def discover_experiments(self, data_dir):
        try:
            names = sorted(os.listdir(data_dir))
        except OSError as exc:
            raise PackError("cannot list data directory %s: %s"
                            % (data_dir, exc))
        candidates = [n for n in names
                      if os.path.isfile(os.path.join(data_dir, n))
                      and os.path.splitext(n)[1].lower() in self._EXTS]
        if not candidates:
            raise PackError(
                "no JEOL data files (.jdf/.jdx/.dx) found under %s -- "
                "expected one file per experiment named per the Tier-1 "
                "checklist (e.g. 11_sn_ref_open.jdf); see "
                "vendors/jeol/README.md" % data_dir)
        prefixed = {}
        unprefixed = []
        for n in candidates:
            m = self._PREFIX_RE.match(n)
            if m:
                prefixed.setdefault(int(m.group(1)), []).append(n)
            else:
                unprefixed.append(n)
        out = []
        if prefixed and not unprefixed:
            for expno in sorted(prefixed):
                group = prefixed[expno]
                if len(group) > 1:
                    # same expno in both formats: prefer the native .jdf
                    jdfs = [g for g in group if g.lower().endswith(".jdf")]
                    if len(jdfs) == 1:
                        self._warnings.append(
                            "expno %d present as multiple files (%s); "
                            "using the native %s"
                            % (expno, ", ".join(group), jdfs[0]))
                        group = jdfs
                    else:
                        raise PackError(
                            "expno prefix %d is claimed by multiple files "
                            "(%s) -- the Tier-1 naming convention requires "
                            "one experiment per prefix"
                            % (expno, ", ".join(group)))
                out.append((expno, os.path.join(data_dir, group[0])))
        else:
            # no (consistent) numeric prefixes: positional expnos, with a
            # warning -- answers.json roles must then be written against
            # this 1..N numbering
            self._warnings.append(
                "JEOL files lack the NN_ expno prefix of the Tier-1 "
                "checklist; assigning positional expnos 1..%d in sorted "
                "filename order" % len(candidates))
            for i, n in enumerate(sorted(candidates), start=1):
                out.append((i, os.path.join(data_dir, n)))
        return out

    def read_experiment(self, dirpath):
        mod = self._reader()
        try:
            result = mod.read_any(dirpath)
        except (mod.JeolReadError, OSError, ValueError) as exc:
            raise PackError("cannot parse JEOL file %s: %s" % (dirpath, exc))
        info = result["info"]
        found = {}
        if info["format"] == "jdf":
            npts = info.get("x_points") or (info.get("points") or [None])[0]
            complex_data = "im" in result["data"]
            if isinstance(npts, int) and npts > 0:
                found["td"] = npts * (2 if complex_data else 1)
            found["td1_rows"] = 1     # Tier-1 protocol: repeated 1Ds
            sw = info.get("sweep_hz")
            if isinstance(sw, (int, float)) and sw > 0:
                found["sw_hz"] = float(sw)
            ns = info.get("scans")
            if isinstance(ns, int) and ns >= 1:
                found["ns"] = ns
            if info.get("experiment"):
                found["pulprog"] = str(info["experiment"])
            rg = info.get("recvr_gain_raw")
            if isinstance(rg, (int, float)) and rg > 0:
                # verbatim JEOL units -- semantics UNVERIFIED (partner
                # checklist item 1); the schema documents rg as
                # vendor-native units
                found["rg"] = float(rg)
            aq = info.get("acq_time_s")
            if isinstance(aq, (int, float)) and aq > 0:
                found["aq_s_per_row"] = float(aq)
            freq = info.get("spectrometer_freq_hz")
            if isinstance(freq, (int, float)) and freq > 0 \
                    and info.get("nucleus") in ("Proton", "1H", "H1"):
                found["h1_freq_mhz"] = freq / 1e6
            # o1 analog: x_offset. UNVERIFIED that JEOL x_offset is the
            # exact analog of Bruker O1 (sign/reference convention;
            # partner checklist item 5); convert only when the unit is
            # unambiguous, else leave it to answers.json.
            off = info.get("offset")
            unit = info.get("offset_unit")
            if isinstance(off, (int, float)):
                if unit == "Hertz":
                    found["o1_hz"] = float(off)
                elif unit == "Ppm" and isinstance(freq, (int, float)):
                    found["o1_hz"] = float(off) * freq / 1e6
            if info.get("delta_version_guess"):
                found["_delta_version_guess"] = info["delta_version_guess"]
            if info.get("actual_start_time_iso"):
                found["_jdf_start_iso"] = info["actual_start_time_iso"]
            found["_format"] = "jdf"
        else:
            npts = int(info.get("npoints") or info.get("npoints_data") or 0)
            complex_data = "im" in result["data"]
            if npts > 0:
                found["td"] = npts * (2 if complex_data else 1)
            found["td1_rows"] = 1
            found["ns"] = 1
            dwell = info.get("x_factor")
            if isinstance(dwell, (int, float)) and dwell > 0:
                found["sw_hz"] = 1.0 / dwell
                if npts:
                    found["aq_s_per_row"] = npts * dwell
            found["pulprog"] = str(info.get("data_type") or "JCAMP-DX")
            freq = info.get("spectrometer_freq_mhz")
            nuc = (info.get("nucleus") or "").upper()
            if isinstance(freq, (int, float)) and freq > 0 \
                    and nuc in ("^1H", "1H", "H1", "PROTON"):
                found["h1_freq_mhz"] = float(freq)
            # generic JCAMP-DX exports carry NO receiver gain and no o1:
            # answers.json must supply rg (and o1_hz) per experiment
            found["_format"] = "jcamp-dx"
        return found

    def instrument_block(self, answers, discovered):
        warnings = list(self._warnings)
        ans_inst = answers.get("instrument", {})
        delta_version = ans_inst.get("delta_version")
        if not delta_version:
            guesses = set(d.get("_delta_version_guess")
                          for d in discovered.values()
                          if d.get("_delta_version_guess"))
            if len(guesses) == 1:
                delta_version = guesses.pop()
                warnings.append(
                    "delta_version %r taken from the .jdf 'version' "
                    "parameter (16-char format truncation applies; "
                    "authoritativeness across Delta releases UNVERIFIED "
                    "-- answer instrument.delta_version to override)"
                    % delta_version)
        if not delta_version:
            raise PackError(
                "answers.json is missing instrument.delta_version and no "
                ".jdf file carried a 'version' parameter -- add e.g. "
                '"instrument": {"delta_version": "5.3.2"}')
        formats = set(d.get("_format") for d in discovered.values()
                      if d.get("_format"))
        if formats == set(["jdf"]):
            data_format = "jdf"
        elif formats == set(["jcamp-dx"]):
            data_format = "jcamp-dx"
        else:
            data_format = "other"
            warnings.append("mixed/unknown JEOL source formats %s recorded "
                            "as data_format 'other'" % sorted(formats))
        rg = None
        for d in discovered.values():
            if d.get("_format") and d.get("rg") is not None:
                rg = d["rg"]        # verbatim; last one wins (the noise
                                    # block runs at the highest, most
                                    # relevant gain)
        notes = ans_inst.get("field_state_notes", "")
        if not notes:
            warnings.append(
                "instrument.field_state_notes not answered -- please "
                "describe the lock/field state during the noise block "
                "(the JEOL analog of the Bruker BSMS sweep confirmation)")
        block = {
            "delta_version": str(delta_version),
            "data_format": data_format,
            "receiver_gain": rg,
            "field_state_notes": notes,
        }
        if ans_inst.get("spectrometer_model"):
            block["spectrometer_model"] = ans_inst["spectrometer_model"]
        return block, warnings


class MagritekReader(VendorReader):
    """Magritek Spinsolve/Prospa spin-noise sessions: per-experiment
    NUMERIC directories (the layout vendors/magritek/
    spin_noise_run_spinsolve.mac writes), each holding acqu.par
    (acquisition parameters) + data.1d (raw FID), as documented by
    nmrglue's spinsolve reader
    (https://nmrglue.readthedocs.io/en/latest/reference/spinsolve.html).

    DRAFT PENDING BENCH VALIDATION (work package B).  Format parsing is
    delegated to vendors/magritek/magritek_reader.py -- the single source
    of truth, also usable standalone; see its docstring and
    vendors/magritek/README.md for the full UNVERIFIED list and the
    bench-session checklist.  Still UNVERIFIED: acqu.par parameter
    spelling/units as written by a REAL Spinsolve (b1Freq MHz, dwellTime
    us, nrPnts, nrScans, rxGain dB are the Prospa-ecosystem names, parsed
    leniently) and the .1d header magic / dataType meanings (the reader
    checks payload structure, not magic values).
    """

    name = "magritek"

    def __init__(self):
        self._mod = None

    def _reader(self):
        """Lazy-import vendors/magritek/magritek_reader.py (stdlib)."""
        if self._mod is None:
            import importlib.util
            here = os.path.dirname(os.path.abspath(__file__))
            path = os.path.abspath(os.path.join(
                here, "..", "vendors", "magritek", "magritek_reader.py"))
            if not os.path.isfile(path):
                raise PackError("vendors/magritek/magritek_reader.py not "
                                "found (looked at %s) -- the Magritek "
                                "adapter needs the repository checkout, "
                                "not a standalone packer copy" % path)
            spec = importlib.util.spec_from_file_location("snn_magritek",
                                                          path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._mod = mod
        return self._mod

    def discover_experiments(self, data_dir):
        try:
            names = sorted(os.listdir(data_dir))
        except OSError as exc:
            raise PackError("cannot list data directory %s: %s"
                            % (data_dir, exc))
        found = []
        for entry in names:
            full = os.path.join(data_dir, entry)
            if not (os.path.isdir(full) and entry.isdigit()):
                continue
            if (os.path.isfile(os.path.join(full, "acqu.par"))
                    or os.path.isfile(os.path.join(full, "data.1d"))):
                found.append((int(entry), full))
        if not found:
            raise PackError(
                "%s does not look like a Spinsolve spin-noise session: no "
                "numeric experiment directories containing acqu.par or "
                "data.1d (expected layout: the session tree written by "
                "vendors/magritek/spin_noise_run_spinsolve.mac; see "
                "vendors/magritek/README.md)" % data_dir)
        return sorted(found)

    def read_experiment(self, dirpath):
        mod = self._reader()
        disc = {"td1_rows": 1, "o1_hz": 0.0}
        acqu_path = os.path.join(dirpath, "acqu.par")
        acqu = mod.parse_acqu_par(acqu_path) if os.path.isfile(acqu_path) \
            else {}
        nr_pnts = acqu.get("nrPnts")
        dwell_us = acqu.get("dwellTime")   # UNVERIFIED unit: microseconds
        rx_gain = acqu.get("rxGain")       # UNVERIFIED unit/range: dB
        ns = acqu.get("nrScans")
        b1 = acqu.get("b1Freq")            # UNVERIFIED unit: MHz
        if isinstance(nr_pnts, int) and nr_pnts > 0:
            # meta.json 'td' follows the Bruker convention (re+im points);
            # nrPnts is complex points, hence the factor 2 (mapping table
            # in vendors/magritek/README.md).
            disc["td"] = 2 * nr_pnts
            if isinstance(dwell_us, (int, float)) and dwell_us > 0:
                disc["aq_s_per_row"] = nr_pnts * float(dwell_us) * 1.0e-6
        if isinstance(dwell_us, (int, float)) and dwell_us > 0:
            disc["sw_hz"] = 1.0e6 / float(dwell_us)
        if isinstance(rx_gain, (int, float)) \
                and not isinstance(rx_gain, bool):
            # meta.json 'rg' is Bruker-comparable LINEAR amplitude gain;
            # rxGain is dB, so rg = 10^(dB/20).  Raw dB is kept for the
            # instrument block.
            disc["rg"] = 10.0 ** (float(rx_gain) / 20.0)
            disc["rx_gain_db"] = float(rx_gain)
        if isinstance(ns, int) and not isinstance(ns, bool) and ns >= 1:
            disc["ns"] = ns
        if isinstance(b1, (int, float)) and not isinstance(b1, bool) \
                and b1 > 0:
            disc["h1_freq_mhz"] = float(b1)
        pulprog = acqu.get("experiment")
        if isinstance(pulprog, str) and pulprog:
            disc["pulprog"] = pulprog
        data_path = os.path.join(dirpath, "data.1d")
        if os.path.isfile(data_path):
            one_d = mod.read_1d(data_path)
            disc["data1d_structure_ok"] = bool(one_d.get("structure_ok"))
        return disc

    def instrument_block(self, answers, discovered):
        warnings = []
        inst_ans = answers.get("instrument", {})
        if not isinstance(inst_ans, dict):
            inst_ans = {}
        sw = inst_ans.get("spinsolve_software_version")
        if not isinstance(sw, str) or not sw:
            raise PackError(
                "missing answer: instrument.spinsolve_software_version "
                "(the Spinsolve/SpinsolveExpert software version; its "
                "machine-readable source is UNVERIFIED pending the bench "
                "session, so enter it by hand)")
        rx_gain = inst_ans.get("rx_gain")
        if not isinstance(rx_gain, (int, float)) \
                or isinstance(rx_gain, bool):
            gains = [d["rx_gain_db"] for d in discovered.values()
                     if isinstance(d.get("rx_gain_db"), float)]
            rx_gain = max(gains) if gains else None
            if rx_gain is not None:
                warnings.append("instrument.rx_gain taken as the highest "
                                "rxGain in the acqu.par files (%.1f dB, "
                                "assumed to be the noise-block gain)"
                                % rx_gain)
        bad_1d = sorted(e for e, d in discovered.items()
                        if d.get("data1d_structure_ok") is False)
        if bad_1d:
            warnings.append("data.1d in expno(s) %s did not match the "
                            "expected 3*xDim float32 layout (UNVERIFIED "
                            "format variant?) -- packed verbatim; verify "
                            "at the bench session" % bad_1d)
        block = {
            "spinsolve_software_version": sw,
            "model": inst_ans.get("model") or "unknown",
            "expert_mode": inst_ans.get("expert_mode")
                           if isinstance(inst_ans.get("expert_mode"), bool)
                           else None,
            "rx_gain": rx_gain,
            "data_format": "prospa-1d",
        }
        pv = inst_ans.get("prospa_version")
        if isinstance(pv, str) and pv:
            block["prospa_version"] = pv
        return block, warnings


class AgilentReader(VendorReader):
    """Agilent/Varian (VnmrJ / OpenVnmrJ) datasets: one VnmrJ save
    directory per experiment -- <name>.fid/ holding procpar + fid (svf()
    writes procpar, text, log, and fid; the packer packs all four) --
    named per the Tier-1 operator checklist (vendors/agilent/README.md):
    a numeric prefix maps each directory onto the Bruker expno plan,
    e.g. 11_sn_ref_open.fid, 12_sn_noise.fid.

    DRAFT PENDING PARTNER-FACILITY VALIDATION (Boyd Goodson, SIU
    Carbondale: 400 MHz Agilent DD2, VnmrJ 3.2).  Parsing is delegated
    to vendors/agilent/agilent_reader.py; the fid/procpar layout follows
    nmrglue's varian reader (BSD-3-Clause) but -- unlike the JEOL path --
    has NOT yet been checked against real instrument output (no public
    corpus of raw .fid directories was found).  Still UNVERIFIED and
    awaiting the partner session (vendors/agilent/README.md checklist):
    'gain' units/range/linearity on the DD2, 'tof' offset convention,
    transmitter silence at pw=0, procpar timestamp semantics, and
    whatever a live VnmrJ 3.2 writes that the docs did not teach us.

    Parameter mapping (README.md table):
      td   = np                (Varian np is TOTAL points, re+im
                                interleaved -- the same counting
                                convention as Bruker TD; at = np/(2*sw))
      td1_rows = fid nblocks   (arrayed acquisitions land as blocks; 1
                                for the Tier-1 protocol's plain 1Ds)
      sw_hz = sw               (Hz)
      rg   = 10^(gain/20)      (gain is dB; meta 'rg' is the Bruker-
                                comparable LINEAR amplitude convention,
                                same mapping the Magritek adapter uses;
                                raw dB kept in instrument.agilent)
      o1_hz = tof              (transmitter offset, Hz assumed --
                                sign/reference convention UNVERIFIED)
      ns   = nt
      aq_s_per_row = at        (s; falls back to np/(2*sw))
      h1_freq_mhz = sfrq       (MHz) when tn is the proton channel
    """

    name = "agilent"

    _PREFIX_RE = re.compile(r"^(\d+)_")

    def __init__(self):
        self._mod = None
        self._warnings = []

    def _reader(self):
        """Lazy-import vendors/agilent/agilent_reader.py (stdlib)."""
        if self._mod is None:
            import importlib.util
            here = os.path.dirname(os.path.abspath(__file__))
            path = os.path.abspath(os.path.join(
                here, "..", "vendors", "agilent", "agilent_reader.py"))
            if not os.path.isfile(path):
                raise PackError("vendors/agilent/agilent_reader.py not "
                                "found (looked at %s) -- the Agilent "
                                "adapter needs the repository checkout, "
                                "not a standalone packer copy" % path)
            spec = importlib.util.spec_from_file_location("snn_agilent",
                                                          path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._mod = mod
        return self._mod

    def discover_experiments(self, data_dir):
        try:
            names = sorted(os.listdir(data_dir))
        except OSError as exc:
            raise PackError("cannot list data directory %s: %s"
                            % (data_dir, exc))
        candidates = []
        for n in names:
            d = os.path.join(data_dir, n)
            if not os.path.isdir(d):
                continue
            if not n.lower().endswith(".fid"):
                continue
            if os.path.isfile(os.path.join(d, "procpar")) \
                    or os.path.isfile(os.path.join(d, "fid")):
                candidates.append(n)
        if not candidates:
            raise PackError(
                "no Agilent/Varian experiments found under %s -- expected "
                "one <name>.fid directory per experiment (each holding "
                "procpar + fid), named per the Tier-1 checklist (e.g. "
                "11_sn_ref_open.fid); see vendors/agilent/README.md"
                % data_dir)
        prefixed = {}
        unprefixed = []
        for n in candidates:
            m = self._PREFIX_RE.match(n)
            if m:
                prefixed.setdefault(int(m.group(1)), []).append(n)
            else:
                unprefixed.append(n)
        out = []
        if prefixed and not unprefixed:
            for expno in sorted(prefixed):
                group = prefixed[expno]
                if len(group) > 1:
                    raise PackError(
                        "expno prefix %d is claimed by multiple "
                        "directories (%s) -- the Tier-1 naming convention "
                        "requires one experiment per prefix"
                        % (expno, ", ".join(group)))
                out.append((expno, os.path.join(data_dir, group[0])))
        else:
            self._warnings.append(
                "Agilent .fid directories lack the NN_ expno prefix of "
                "the Tier-1 checklist; assigning positional expnos 1..%d "
                "in sorted directory-name order" % len(candidates))
            for i, n in enumerate(sorted(candidates), start=1):
                out.append((i, os.path.join(data_dir, n)))
        return out

    def read_experiment(self, dirpath):
        mod = self._reader()
        try:
            result = mod.read_experiment_dir(dirpath)
        except (mod.AgilentReadError, OSError, ValueError) as exc:
            raise PackError("cannot parse Agilent experiment %s: %s"
                            % (dirpath, exc))
        self._warnings.extend(result["warnings"])
        pp = result["procpar"]
        fid = result["fid"]
        sc = mod.scalar
        found = {}
        np_pts = sc(pp, "np")
        if np_pts is None and fid and fid.get("np_total_points"):
            np_pts = fid["np_total_points"]
        if isinstance(np_pts, (int, float)) and np_pts >= 1:
            # np counts real+imag points -- same convention as Bruker TD
            found["td"] = int(np_pts)
        if fid and isinstance(fid.get("nblocks"), int) \
                and fid["nblocks"] >= 1:
            found["td1_rows"] = fid["nblocks"]
        else:
            found["td1_rows"] = 1
        sw = sc(pp, "sw")
        if isinstance(sw, (int, float)) and sw > 0:
            found["sw_hz"] = float(sw)
        at = sc(pp, "at")
        if isinstance(at, (int, float)) and at > 0:
            found["aq_s_per_row"] = float(at)
        elif "td" in found and "sw_hz" in found:
            found["aq_s_per_row"] = found["td"] / (2.0 * found["sw_hz"])
        gain = sc(pp, "gain")
        if isinstance(gain, (int, float)):
            # 'gain' is receiver gain in dB (VnmrJ parameter docs); the
            # meta 'rg' convention is Bruker-comparable LINEAR amplitude,
            # so rg = 10^(dB/20) (same mapping as the Magritek adapter).
            # Range/step/linearity on a given console are UNVERIFIED
            # (partner checklist item 1); raw dB goes to the instrument
            # block.
            found["rg"] = 10.0 ** (float(gain) / 20.0)
            found["rx_gain_db"] = float(gain)
        nt = sc(pp, "nt")
        if isinstance(nt, (int, float)) and nt >= 1:
            found["ns"] = int(nt)
        seqfil = sc(pp, "seqfil") or sc(pp, "pslabel")
        if isinstance(seqfil, str) and seqfil:
            found["pulprog"] = seqfil
        sfrq = sc(pp, "sfrq")
        tn = sc(pp, "tn")
        if isinstance(sfrq, (int, float)) and sfrq > 0 \
                and isinstance(tn, str) and tn.upper() in ("H1", "1H",
                                                           "PROTON"):
            found["h1_freq_mhz"] = float(sfrq)
        # o1 analog: tof (transmitter offset). UNVERIFIED(2) that tof is
        # the exact analog of Bruker O1 (sign and reference convention);
        # recorded verbatim in Hz, override in answers.json if wrong.
        tof = sc(pp, "tof")
        if isinstance(tof, (int, float)):
            found["o1_hz"] = float(tof)
        if fid is not None:
            found["fid_structure_ok"] = bool(fid.get("structure_ok"))
        found["_format"] = "varian-fid"
        return found

    def instrument_block(self, answers, discovered):
        warnings = list(self._warnings)
        self._warnings = []
        ans_inst = answers.get("instrument", {})
        if not isinstance(ans_inst, dict):
            ans_inst = {}
        vnmrj_version = ans_inst.get("vnmrj_version")
        if not isinstance(vnmrj_version, str) or not vnmrj_version:
            raise PackError(
                "answers.json is missing instrument.vnmrj_version (the "
                "VnmrJ/OpenVnmrJ software version, e.g. \"3.2\"; no "
                "machine-readable source in procpar is verified yet, so "
                "enter it by hand)")
        gains = [d["rx_gain_db"] for d in discovered.values()
                 if isinstance(d.get("rx_gain_db"), float)]
        # verbatim dB; the noise block runs at the highest, most relevant
        # gain (same assumption the Magritek adapter documents)
        rg = max(gains) if gains else None
        bad_fids = sorted(e for e, d in discovered.items()
                          if d.get("fid_structure_ok") is False)
        if bad_fids:
            warnings.append("fid in expno(s) %s did not match the "
                            "nmrglue-documented header arithmetic "
                            "(UNVERIFIED format variant?) -- packed "
                            "verbatim; verify at the partner session"
                            % bad_fids)
        notes = ans_inst.get("field_state_notes", "")
        if not notes:
            warnings.append(
                "instrument.field_state_notes not answered -- please "
                "describe the lock/z0 state during the noise block "
                "(the Agilent analog of the Bruker BSMS sweep "
                "confirmation)")
        block = {
            "vnmrj_version": str(vnmrj_version),
            "data_format": "varian-fid",
            "receiver_gain_db": rg,
            "field_state_notes": notes,
        }
        if ans_inst.get("spectrometer_model"):
            block["spectrometer_model"] = ans_inst["spectrometer_model"]
        return block, warnings



class NanalysisReader(VendorReader):
    """Delegates to vendors/nanalysis/nanalysis_reader.py (see there)."""
    name = "nanalysis"

    def __init__(self):
        self._impl = None

    def _get(self):
        if self._impl is None:
            import importlib.util
            here = os.path.dirname(os.path.abspath(__file__))
            path = os.path.abspath(os.path.join(
                here, "..", "vendors", "nanalysis", "nanalysis_reader.py"))
            if not os.path.isfile(path):
                raise PackError("vendors/nanalysis/nanalysis_reader.py "
                                "not found (looked at %s)" % path)
            spec = importlib.util.spec_from_file_location("snn_nanalysis", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._impl = mod.NanalysisReader(pack_error=PackError)
        return self._impl

    def discover_experiments(self, data_dir):
        return self._get().discover_experiments(data_dir)

    def read_experiment(self, dirpath):
        return self._get().read_experiment(dirpath)

    def instrument_block(self, answers, discovered):
        return self._get().instrument_block(answers, discovered)

VENDOR_READERS = {
    "bruker": BrukerReader,
    "jeol": JeolReader,
    "magritek": MagritekReader,
    "agilent": AgilentReader,
    "nanalysis": NanalysisReader,
}


# ---------------------------------------------------------------------------
# answers.json -> meta.json assembly
# ---------------------------------------------------------------------------

def _require(dct, dotted, typ, problems, coerce=None):
    """Fetch answers value at 'a.b' path; record a problem if missing or
    mistyped; return the value (or None)."""
    cur = dct
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            problems.append("missing answer: %s" % dotted)
            return None
        cur = cur[part]
    if coerce is not None:
        try:
            cur = coerce(cur)
        except (TypeError, ValueError):
            problems.append("answer %s: cannot interpret %r" % (dotted, cur))
            return None
    if typ is not None and not isinstance(cur, typ):
        problems.append("answer %s: expected %s, got %r"
                        % (dotted, getattr(typ, "__name__", typ), cur))
        return None
    if typ is not bool and isinstance(cur, bool):
        problems.append("answer %s: expected %s, got a boolean"
                        % (dotted, getattr(typ, "__name__", typ)))
        return None
    return cur


def _num_or_none(v):
    if v is None:
        return None
    return float(v)


def load_answers(path):
    try:
        with open(path, "r") as fh:
            answers = json.load(fh)
    except OSError as exc:
        raise PackError("cannot read answers file %s: %s" % (path, exc))
    except ValueError as exc:
        raise PackError("answers file %s is not valid JSON: %s" % (path, exc))
    if not isinstance(answers, dict):
        raise PackError("answers file %s must contain a JSON object" % path)
    return answers


def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def utc_stamp_compact():
    return time.strftime("%Y%m%d_%H%M%SZ", time.gmtime())


def local_tz_offset_min():
    if time.daylight and time.localtime().tm_isdst:
        return int(-time.altzone / 60)
    return int(-time.timezone / 60)


def script_self_sha256():
    try:
        h = hashlib.sha256()
        with open(os.path.abspath(__file__), "rb") as fh:
            h.update(fh.read())
        return "sha256:" + h.hexdigest()
    except OSError:
        return "unavailable"


def mtime_iso_local(path):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S",
                             time.localtime(os.path.getmtime(path)))
    except OSError:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def infer_role(pulprog):
    """Fallback role inference when answers.json does not name one."""
    if pulprog and "noise" in pulprog.lower():
        return "noise"
    return None


EXP_FIELDS_NUM = ("sw_hz", "o1_hz", "rg", "aq_s_per_row")
EXP_FIELDS_INT = ("td", "td1_rows", "ns")


def build_experiments(reader, experiments_found, answers, problems, warnings):
    """Merge discovered per-experiment parameters with answers.json
    overrides into schema-conforming meta['experiments'] entries."""
    overrides = {}
    answer_order = {}
    for entry in answers.get("experiments", []):
        if isinstance(entry, dict) and isinstance(entry.get("expno"), int):
            overrides[entry["expno"]] = entry
            answer_order[entry["expno"]] = len(answer_order)
    unknown = set(overrides) - set(e for e, _d in experiments_found)
    for e in sorted(unknown):
        warnings.append("answers.json describes expno %d but the data "
                        "directory has no such experiment; ignored" % e)

    # Emit experiments in the answers.json order (the operator's session
    # order -- what the TopSpin orchestrator records), with any expnos not
    # listed there appended in numeric order.
    ordered = sorted(experiments_found,
                     key=lambda ed: (answer_order.get(ed[0], len(answer_order)),
                                     ed[0]))

    out = []
    for expno, dirpath in ordered:
        disc = reader.read_experiment(dirpath)
        ov = overrides.get(expno, {})
        entry = {"expno": expno}
        role = ov.get("role") or infer_role(
            ov.get("pulprog") or disc.get("pulprog"))
        if role not in ROLES:
            problems.append(
                "experiment %d: no valid role -- add {\"expno\": %d, "
                "\"role\": one of %s} to answers.json 'experiments'"
                % (expno, expno, list(ROLES)))
        entry["role"] = role
        entry["pulprog"] = ov.get("pulprog") or disc.get("pulprog") or ""
        for name in EXP_FIELDS_INT:
            v = ov.get(name, disc.get(name))
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                problems.append("experiment %d: missing/invalid '%s' -- not "
                                "in the vendor files; add it to this "
                                "experiment's entry in answers.json"
                                % (expno, name))
                v = None
            entry[name] = v
        for name in EXP_FIELDS_NUM:
            v = ov.get(name, disc.get(name))
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                if name == "aq_s_per_row" and entry.get("td") \
                        and entry.get("sw_hz"):
                    v = entry["td"] / (2.0 * entry["sw_hz"])
                else:
                    problems.append("experiment %d: missing/invalid '%s' -- "
                                    "add it to answers.json" % (expno, name))
                    v = None
            entry[name] = v
        for name in ("started_local", "finished_local"):
            v = ov.get(name)
            if not isinstance(v, str) or not v:
                raw = None
                if os.path.isfile(dirpath):    # single-file experiment
                    raw = mtime_iso_local(dirpath)
                for fn in ("ser", "fid", "acqus"):
                    if raw:
                        break
                    p = os.path.join(dirpath, fn)
                    if os.path.isfile(p):
                        raw = mtime_iso_local(p)
                        break
                v = raw or time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
                warnings.append("experiment %d: %s reconstructed from file "
                                "modification time (%s); supply it in "
                                "answers.json if known better"
                                % (expno, name, v))
            entry[name] = v
        out.append((entry, disc))
    return out


def build_meta(vendor, reader, data_dir, answers):
    """Assemble the full schema-2.0 meta dict.  Raises PackError listing
    every missing answer at once (operators fix one file, not one field
    per run)."""
    problems = []
    warnings = []

    experiments_found = reader.discover_experiments(data_dir)
    exp_entries = build_experiments(reader, experiments_found, answers,
                                    problems, warnings)
    discovered = dict((e["expno"], d) for e, d in exp_entries)

    # --- facility (same six questions as TopSpin dialogs 1/5 + 2/5) -----
    fac = {
        "institution": _require(answers, "facility.institution", str, problems),
        "city": _require(answers, "facility.city", str, problems),
        "country": _require(answers, "facility.country", str, problems),
        "facility_slug": _require(answers, "facility.facility_slug", str,
                                  problems),
        "contact_email": answers.get("facility", {}).get("contact_email", ""),
        "contact_consent": bool(answers.get("facility", {})
                                .get("contact_consent", False)),
    }
    slug = fac["facility_slug"] or ""
    if not re.match(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$", slug):
        problems.append("facility.facility_slug %r must be 3-64 chars of "
                        "lowercase letters, digits and hyphens" % slug)
    if not fac["contact_consent"]:
        fac["contact_email"] = ""

    # --- sample (dialog 3/5 + 4/5) ---------------------------------------
    samp = {
        "description": _require(answers, "sample.description", str, problems),
        "h2o_fraction_pct": _require(answers, "sample.h2o_fraction_pct",
                                     (int, float), problems),
        "d2o_pct": _require(answers, "sample.d2o_pct", (int, float), problems),
        "additives": answers.get("sample", {}).get("additives", "none"),
        "tube_od_mm": _require(answers, "sample.tube_od_mm", (int, float),
                               problems),
        "sample_volume_ul": _require(answers, "sample.sample_volume_ul",
                                     (int, float), problems),
        "vt_setpoint_k": _require(answers, "sample.vt_setpoint_k",
                                  (int, float), problems),
    }

    # --- environment ------------------------------------------------------
    env_ans = answers.get("environment", {})
    locked = env_ans.get("locked")
    if not isinstance(locked, bool):
        problems.append("missing answer: environment.locked (true/false)")
    env = {
        "locked": bool(locked),
        "operator_notes": env_ans.get("operator_notes", ""),
    }

    # --- spectrometer common core ------------------------------------------
    spec_ans = answers.get("spectrometer", {})
    h1 = spec_ans.get("h1_freq_mhz")
    if not isinstance(h1, (int, float)) or isinstance(h1, bool) or h1 <= 0:
        h1s = set(d.get("h1_freq_mhz") for d in discovered.values()
                  if d.get("h1_freq_mhz"))
        if len(h1s) == 1:
            h1 = h1s.pop()
        elif len(h1s) > 1:
            h1 = sorted(h1s)[0]
            warnings.append("experiments disagree on h1_freq_mhz (%s); "
                            "using %.6f -- override in answers.json "
                            "spectrometer.h1_freq_mhz if wrong"
                            % (sorted(h1s), h1))
        else:
            problems.append("spectrometer.h1_freq_mhz not in the vendor "
                            "files -- add it to answers.json")
            h1 = None
    field_t = spec_ans.get("field_tesla")
    if not isinstance(field_t, (int, float)) or isinstance(field_t, bool):
        field_t = (h1 / GAMMA_1H_MHZ_PER_T) if h1 else None
    probe_type = spec_ans.get("probe_type")
    ptypes = ("RT", "N2-cryo", "He-cryo", "permanent-magnet-benchtop",
              "unknown")
    if probe_type not in ptypes:
        problems.append("missing answer: spectrometer.probe_type (one of %s)"
                        % list(ptypes))
    spec = {
        "h1_freq_mhz": h1,
        "field_tesla": field_t,
        "console": spec_ans.get("console", ""),
        "probe_string": spec_ans.get("probe_string", ""),
        "probe_type": probe_type,
        "coil_temp_k": _num_or_none(spec_ans.get("coil_temp_k")),
        "preamp_temp_k": _num_or_none(spec_ans.get("preamp_temp_k")),
    }

    # --- calibration ---------------------------------------------------------
    cal_ans = answers.get("calibration", {})
    p90 = cal_ans.get("p90_us")
    if not isinstance(p90, (int, float)) or isinstance(p90, bool) or p90 <= 0:
        problems.append("missing answer: calibration.p90_us (microseconds; "
                        "the calibrated or nominal 1H 90-degree pulse)")
    p90_pw = cal_ans.get("p90_power_db_or_w", "unknown")
    if not isinstance(p90_pw, (int, float, str)) or isinstance(p90_pw, bool):
        p90_pw = "unknown"
    ladder = cal_ans.get("rg_ladder")
    if not (isinstance(ladder, list) and ladder):
        # synthesize a single-entry ladder from a reference experiment,
        # exactly the honest fallback repackage_epfl.py uses
        ladder = []
        for e, _d in exp_entries:
            if e.get("role") in ("rg_ladder", "reference_open",
                                 "reference_close") and e.get("rg"):
                ladder = [{"expno": e["expno"], "rg": e["rg"],
                           "tip_deg": cal_ans.get("reference_tip_deg", 1.0)}]
                warnings.append("no calibration.rg_ladder answered; recorded "
                                "a single-entry ladder from expno %d (rg=%s) "
                                "-- receiver-linearity checks will be "
                                "limited" % (e["expno"], e["rg"]))
                break
        if not ladder:
            problems.append("missing answer: calibration.rg_ladder (list of "
                            "{expno, rg, tip_deg}) and no reference "
                            "experiment to synthesize one from")
    cal = {
        "p90_us": p90,
        "p90_power_db_or_w": p90_pw,
        "rg_ladder": ladder,
        "topshim_ok": bool(cal_ans.get("topshim_ok", False)),
    }

    # --- software provenance ---------------------------------------------------
    run_mode = answers.get("run_mode", "external-acquisition")
    if run_mode not in RUN_MODES:
        problems.append("run_mode %r not one of %s" % (run_mode,
                                                       list(RUN_MODES)))

    # --- instrument block (vendor adapter; may raise PackError itself) ------
    inst_block = None
    try:
        inst_block, inst_warnings = reader.instrument_block(answers,
                                                            discovered)
        warnings.extend(inst_warnings)
    except PackError as exc:
        problems.append(str(exc))

    if problems:
        raise PackError(
            "answers.json / data directory incomplete -- %d problem(s):\n  - "
            "%s\n(see packer/answers.example.json for the full questionnaire)"
            % (len(problems), "\n  - ".join(problems)))

    tz_min = answers.get("local_timezone_offset_min")
    if not isinstance(tz_min, int) or isinstance(tz_min, bool):
        tz_min = local_tz_offset_min()

    meta = {
        "schema_version": SCHEMA_VERSION,
        "program_version": PACKER_VERSION,
        "software": {
            "script_version": PACKER_VERSION,
            "schema_version": SCHEMA_VERSION,
            "script_sha256": script_self_sha256(),
            "writer": "packer/pack_bundle.py",
            "run_mode": run_mode,
        },
        "created_utc": now_utc(),
        "local_timezone_offset_min": tz_min,
        "vendor": vendor,
        "facility": fac,
        "spectrometer": spec,
        "instrument": {vendor: inst_block},
        "sample": samp,
        "environment": env,
        "calibration": cal,
        "experiments": [e for e, _d in exp_entries],
        "checksums": {},
    }
    # Bruker continuity aliases: old tooling reads these 1.x locations.
    if vendor == "bruker":
        meta["spectrometer"]["topspin_version"] = inst_block["topspin_version"]
        meta["environment"]["lock_sweep_confirmed_off"] = \
            inst_block["bsms_field_sweep_confirmed_off"]
    return meta, warnings


# ---------------------------------------------------------------------------
# Zip assembly
# ---------------------------------------------------------------------------

def collect_data_files(experiments_found):
    """[(arcname, fs_path)] for every file under every experiment dir,
    sorted, forward slashes -- the exact layout the Jython script zips.
    Single-FILE experiments (the JEOL adapter: one .jdf/.jdx per
    experiment) land as data/<expno>/<filename>."""
    out = []
    for expno, dirpath in experiments_found:
        if os.path.isfile(dirpath):
            out.append(("data/%d/%s" % (expno, os.path.basename(dirpath)),
                        dirpath))
            continue
        for root, dirs, files in os.walk(dirpath):
            dirs.sort()
            for fn in sorted(files):
                fs = os.path.join(root, fn)
                rel = os.path.relpath(fs, dirpath).replace(os.sep, "/")
                out.append(("data/%d/%s" % (expno, rel), fs))
    return out


def sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            h.update(block)
    return "sha256:" + h.hexdigest()


def write_bundle(meta, data_files, out_dir, slug):
    bundle_name = "spinnoise_%s_%s_%04x.zip" % (
        slug, utc_stamp_compact(), random.randint(0, 0xFFFF))
    bundle_path = os.path.join(out_dir, bundle_name)
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta, indent=2) + "\n")
        for arcname, fs_path in data_files:
            zf.write(fs_path, arcname)
    return bundle_path


# ---------------------------------------------------------------------------
# Self-validation via the repository's own validator
# ---------------------------------------------------------------------------

def repo_paths():
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.abspath(os.path.join(here, ".."))
    return (repo,
            os.path.join(repo, "uploader", "upload_bundle.py"),
            os.path.join(repo, "schema", "meta.schema.json"))


def load_uploader_module(uploader_path):
    """Import uploader/upload_bundle.py as a module (single source of
    validation truth -- the packer never re-implements the validator)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("snn_uploader",
                                                  uploader_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def validate_bundle(bundle_path, schema_path, uploader_path):
    """Returns (ok, msgs) using the uploader's verify_bundle; (None, [..])
    if the uploader is not alongside (standalone copy of the packer)."""
    if not os.path.isfile(uploader_path):
        return None, ["WARN : uploader not found at %s -- skipping "
                      "validation; run --selftest yourself before "
                      "uploading" % uploader_path]
    up = load_uploader_module(uploader_path)
    return up.verify_bundle(bundle_path, schema_path)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Pack a directory of vendor NMR data + answers.json "
                    "into a validated spin-noise bundle zip.")
    parser.add_argument("data_dir",
                        help="directory containing the vendor experiment "
                             "data (Bruker: numeric expno subdirectories)")
    parser.add_argument("--answers", required=True,
                        help="answers.json -- the operator questionnaire "
                             "(see packer/answers.example.json)")
    parser.add_argument("--vendor", default=None,
                        choices=sorted(VENDOR_READERS),
                        help="instrument vendor (default: taken from "
                             "answers.json 'vendor', else 'bruker')")
    parser.add_argument("--out-dir", default=None,
                        help="directory for the bundle zip (default: the "
                             "current working directory)")
    parser.add_argument("--schema", default=None,
                        help="override path to meta.schema.json for the "
                             "final validation")
    parser.add_argument("--no-validate", action="store_true",
                        help="skip the final validation pass (NOT "
                             "recommended; the uploader will validate "
                             "anyway before sending)")
    args = parser.parse_args(argv)

    repo, uploader_path, default_schema = repo_paths()
    schema_path = args.schema or default_schema

    try:
        answers = load_answers(args.answers)
        vendor = args.vendor or answers.get("vendor") or "bruker"
        if vendor not in VENDOR_READERS:
            raise PackError("unknown vendor %r (known: %s)"
                            % (vendor, ", ".join(sorted(VENDOR_READERS))))
        reader = VENDOR_READERS[vendor]()
        if not os.path.isdir(args.data_dir):
            raise PackError("no such data directory: %s" % args.data_dir)

        info("vendor  : %s" % vendor)
        experiments_found = reader.discover_experiments(args.data_dir)
        info("found   : %d experiment(s): %s"
             % (len(experiments_found),
                ", ".join(str(e) for e, _d in experiments_found)))

        meta, warnings = build_meta(vendor, reader, args.data_dir, answers)
        for w in warnings:
            info("WARN : %s" % w)

        data_files = collect_data_files(experiments_found)
        info("hashing %d data file(s)" % len(data_files))
        for arcname, fs_path in data_files:
            meta["checksums"][arcname] = sha256_of_file(fs_path)

        out_dir = args.out_dir or os.getcwd()
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        bundle_path = write_bundle(
            meta, data_files, out_dir,
            meta["facility"]["facility_slug"])
        info("bundle  : %s (%.1f MiB)"
             % (bundle_path, os.path.getsize(bundle_path) / 1048576.0))
    except PackError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1

    if not args.no_validate:
        ok, msgs = validate_bundle(bundle_path, schema_path, uploader_path)
        for m in msgs:
            info(m)
        if ok is False:
            info("ERROR: the packed bundle FAILED validation -- it was NOT "
                 "deleted (inspect it), but do not upload it. Fix the "
                 "problems above and re-pack.")
            return 1
        if ok:
            info("validation: PASS")

    # exactly one line on stdout: the bundle path (script-friendly, same
    # convention as testing/make_synthetic_bundle.py)
    print(bundle_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
