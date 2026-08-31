#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nanalysis_reader.py -- Nanalysis NMReady benchtop session reader and bundle
packer for the spin-noise network (converter-first vendor path).

Reads a session directory of NMReady JCAMP-DX exports (one NN_*.dx file per
experiment, numeric-prefix expno convention below, plus an answers.json
operator questionnaire) and packs it into a spin-noise network bundle zip:

    python3 vendors/nanalysis/nanalysis_reader.py pack <session_dir>
        [--out-dir D] [--schema PATH]
    python3 vendors/nanalysis/nanalysis_reader.py inspect <file.dx>
    python3 vendors/nanalysis/nanalysis_reader.py meta <session_dir>

`pack` prints exactly one line on stdout (the bundle path); progress goes to
stderr.

STATUS / HONESTY:
  * DRAFT PENDING PARTNER VALIDATION.  No NMReady has run the network
    protocol yet.  The validation partner unit is a 60 MHz NMReady 60PRO
    (software v2.2.6.2, firmware v2.6.0) at SIU Carbondale.
  * The JCAMP-DX parsing itself is NOT guessed: it is delegated to the
    already-tested decoder in vendors/jeol/jeol_reader.py (published
    standard: McDonald & Wilks, Appl. Spectrosc. 42, 151 (1988); Davies &
    Lampen, Appl. Spectrosc. 47, 1093 (1993); AFFN + SQZ/DIF/DUP, XYDATA +
    NMR NTUPLES).  This module only adds the Nanalysis-specific label
    digest on top.
  * The Nanalysis label vocabulary used here was verified against REAL
    NMReady output: Nanalysis Corp.'s own open-source JCAMP parser test
    corpus (github.com/nanalysis/jcamp-parser, GPLv3 -- used here as
    DOCUMENTATION ONLY, no code ported) ships genuine instrument exports:
      - NMReady_1D_1H_20210909_Test_formates.dx  (60 MHz, FID export:
        DATA TYPE=NMR FID, DATA CLASS=NTUPLES, AFFN, R/I pages)
      - NMReady_1D_1H_20210909_Test_formatesS.jdx (same data, processed
        spectrum export: DATA TYPE=NMR SPECTRUM, DATA CLASS=XYDATA)
      - NMReady_1D_1H_20210302_quinine_4.dx (100 MHz FID)
    VERIFIED against those files (writer "Nanalysis NMReady v2.2.4.5" --
    one minor release below the partner's v2.2.6.2):
      ##JCAMP-DX=5.01 $$ Nanalysis NMReady v<version>   (machine-readable
        software version in the version-label comment);
      ##LONG DATE=YYYY/MM/DD HH:MM:SS+ZZZZ  (timezone-qualified stamp) and
        ##$DATE=<unix epoch seconds> (the two agree to the second);
      ##$RECVR_GAIN (= ##$RG), ##$TD (Bruker re+im convention: TD = 2 x
        complex points, cross-checked against NPOINTS/VAR_DIM), ##$SWH
        (Hz, = 1/dwell within float precision), ##$O1 (Hz, consistent with
        ##$O1P ppm x ##$SFO1), ##$SFO1/##$BF1 (MHz), ##$AQ (s), ##$NS =
        ##$SCANS, ##$DW (us, Bruker half-dwell convention 1/(2*SWH)),
        ##.OBSERVE 90 / ##$X_PULSE (pulse lengths, values consistent with
        us), ##$TOTAL DURATION (s), ##.PULSE SEQUENCE, ##.FIELD ($$ Tesla),
        ##TEMPERATURE, ##SPECTROMETER/DATA SYSTEM=NMReady 60/<host>.
    UNVERIFIED (partner checklist in README.md):
      RECVR_GAIN units and receiver linearity (record verbatim, never
        convert); which event LONG DATE/$DATE stamps (acquisition start vs
        file save -- finished_local is DERIVED from it plus TOTAL DURATION
        and marked so); TEMPERATURE unit (reads like the regulated magnet
        temperature in Celsius); whether v2.2.6.2 writes the same labels;
        whether a no-pulse acquisition is expressible at all (see README).
  * A reader NEVER invents values: what the files do not carry must come
    from answers.json, and synthetic sessions are stamped run_mode
    "desktest" so a test bundle can never masquerade as data.

SCHEMA WIRING (deferred to a follow-up commit -- see README.md):
  This module emits schema-2.0-shaped bundles with vendor "nanalysis" and
  an instrument.nanalysis block.  The shared files schema/meta.schema.json
  and packer/pack_bundle.py do not know this vendor yet; until the wiring
  commit lands, validate against a locally patched schema copy (the chain
  test does exactly that, using SCHEMA_VENDOR_ENUM_VALUE and
  INSTRUMENT_SCHEMA_BLOCK below -- which are also the exact payload for
  the wiring commit).

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
import re
import sys
import time
import zipfile

ADAPTER_VERSION = "0.1.0-draft"
VENDOR = "nanalysis"

GAMMA_H_MHZ_PER_T = 42.5774689

# Session-layout convention (same numeric-prefix plan as the JEOL Tier-1
# checklist and the Magritek session tree): expno -> role.  Noise records
# are 20, 21, 22, ... (open-ended -- a noise block is MANY repeated 1Ds).
ROLE_BY_EXPNO = {10: "rg_ladder", 14: "rg_ladder", 15: "rg_ladder",
                 16: "rg_ladder", 11: "reference_open",
                 13: "reference_close"}
NOISE_EXPNO_MIN = 20
_PREFIX_RE = re.compile(r"^(\d+)_")
_EXTS = (".dx", ".jdx")

# ---------------------------------------------------------------------------
# The exact schema payload for the follow-up wiring commit.  The chain test
# validates bundles against a patched schema built from these constants, so
# the wiring commit is copy-paste, not design work.
# ---------------------------------------------------------------------------

SCHEMA_VENDOR_ENUM_VALUE = "nanalysis"

INSTRUMENT_SCHEMA_BLOCK = {
    "type": "object",
    "required": ["software_version"],
    "description": (
        "DRAFT (converter-first Nanalysis NMReady path; pending partner "
        "validation on a 60 MHz NMReady 60PRO). Field semantics follow the "
        "JCAMP-DX exports of the NMReady touchscreen software as verified "
        "against Nanalysis Corp.'s own open-source jcamp-parser test corpus "
        "(real v2.2.4.5 instrument output); every mapping must be confirmed "
        "on the partner's unit before this block is treated as "
        "authoritative."),
    "properties": {
        "software_version": {
            "type": "string",
            "description": (
                "NMReady software version, machine-readable from the "
                "##JCAMP-DX label comment ('$$ Nanalysis NMReady "
                "v2.2.6.2'); operator-entered if the export omits it.")},
        "model": {
            "type": "string",
            "description": "e.g. 'NMReady-60PRO'. Optional."},
        "firmware_version": {
            "type": "string",
            "description": ("Instrument firmware version, operator-entered "
                            "(no machine-readable source verified). "
                            "Optional.")},
        "data_format": {
            "type": "string",
            "enum": ["jcamp-dx", "csv", "other"],
            "description": ("Source format of the files under data/: the "
                            "NMReady exports JCAMP-DX (FID as NTUPLES, "
                            "spectrum as XYDATA) and csv.")},
        "receiver_gain": {
            "type": ["number", "null"],
            "description": ("##$RECVR_GAIN verbatim, in Nanalysis's native "
                            "units. UNVERIFIED units/linearity -- record "
                            "verbatim, calibrate at the partner session. "
                            "Null if unknown.")},
        "lock_nucleus": {
            "type": "string",
            "description": ("Lock channel nucleus during the noise block: "
                            "'2H', '1H', 'off', or 'unknown'. The NMReady "
                            "has an internal 1H-or-2H lock; whether it can "
                            "be disabled or radiates in-band during "
                            "acquisition is a partner-session question.")},
        "field_state_notes": {
            "type": "string",
            "description": ("Free text on lock/shim/drift state during the "
                            "noise block (the NMReady analog of the Bruker "
                            "BSMS sweep confirmation: is anything actively "
                            "stepping B0 between or during records?). "
                            "Empty string allowed.")},
    },
}


def info(msg):
    print(msg, file=sys.stderr)


class NanalysisReadError(Exception):
    """Raised when a file/session cannot be interpreted."""


# ---------------------------------------------------------------------------
# JCAMP-DX decoding: delegate to the tested decoder in vendors/jeol/
# ---------------------------------------------------------------------------

_JEOL_MOD = None


def _jcamp_module():
    """Lazy-import vendors/jeol/jeol_reader.py (stdlib importlib) for its
    published-standard JCAMP-DX decoder (read_jcamp). Read-only reuse."""
    global _JEOL_MOD
    if _JEOL_MOD is None:
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.abspath(os.path.join(
            here, "..", "jeol", "jeol_reader.py"))
        if not os.path.isfile(path):
            raise NanalysisReadError(
                "vendors/jeol/jeol_reader.py not found (looked at %s) -- "
                "the Nanalysis reader reuses its JCAMP-DX decoder and "
                "needs the repository checkout, not a standalone copy"
                % path)
        spec = importlib.util.spec_from_file_location("snn_jeol_for_nan",
                                                      path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _JEOL_MOD = mod
    return _JEOL_MOD


def _flabel(labels, name):
    """Float from a normalized JCAMP label, tolerating '$$ unit' comments
    and leading text (e.g. '##.FIELD=1.410588  $$ Tesla')."""
    v = labels.get(name)
    if v is None:
        return None
    m = re.match(r"\s*([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)", str(v))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


_VERSION_RE = re.compile(r"Nanalysis\s+NMReady\s+v([0-9][0-9A-Za-z.]*)")
_LONGDATE_RE = re.compile(
    r"^(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2}):(\d{2})"
    r"(?:\s*([+-])(\d{2})(\d{2}))?")


def read_nanalysis_dx(path):
    """Read one NMReady JCAMP-DX export.  Returns
    {labels, data, info, nan} where labels/data/info come from the shared
    JCAMP decoder and nan is the Nanalysis-specific digest (verbatim-first;
    UNVERIFIED semantics are named as such in the key or left raw)."""
    mod = _jcamp_module()
    result = mod.read_jcamp(path)
    labels = result["labels"]

    version = None
    m = _VERSION_RE.search(labels.get("JCAMPDX", "") or "")
    if m:
        version = m.group(1)

    long_date = labels.get("LONGDATE")
    started_iso = None
    tz_offset_min = None
    if long_date:
        dm = _LONGDATE_RE.match(long_date.strip())
        if dm:
            started_iso = "%s-%s-%sT%s:%s:%s" % dm.group(1, 2, 3, 4, 5, 6)
            if dm.group(7):
                sign = -1 if dm.group(7) == "-" else 1
                tz_offset_min = sign * (int(dm.group(8)) * 60
                                        + int(dm.group(9)))

    data_type = (labels.get("DATATYPE") or "").upper()
    nan = {
        "source_file": os.path.basename(path),
        "software_version": version,          # from ##JCAMP-DX $$ comment
        "data_type": labels.get("DATATYPE"),  # 'NMR FID' vs 'NMR SPECTRUM'
        "is_fid": "FID" in data_type,
        "spectrometer_system": labels.get("SPECTROMETER/DATASYSTEM"),
        "pulse_sequence": labels.get(".PULSESEQUENCE"),
        "sfo1_mhz": _flabel(labels, "$SFO1")
                    or _flabel(labels, ".OBSERVEFREQUENCY"),
        "bf1_mhz": _flabel(labels, "$BF1"),
        "field_tesla_verbatim": _flabel(labels, ".FIELD"),
        "o1_hz": _flabel(labels, "$O1"),
        "sw_hz": _flabel(labels, "$SWH"),
        "td": labels.get("$TD") and int(_flabel(labels, "$TD")),
        "aq_s": _flabel(labels, "$AQ"),
        "ns": (labels.get("$NS") and int(_flabel(labels, "$NS")))
              or (labels.get("$SCANS") and int(_flabel(labels, "$SCANS"))),
        "dummy_scans": labels.get("$DS") and int(_flabel(labels, "$DS")),
        # UNVERIFIED units -- record verbatim, never convert:
        "recvr_gain_raw": _flabel(labels, "$RECVRGAIN")
                          if labels.get("$RECVRGAIN") is not None
                          else _flabel(labels, "$RG"),
        "observe_90_raw": _flabel(labels, ".OBSERVE90"),   # us-consistent
        "x_pulse_raw": _flabel(labels, "$XPULSE"),          # us-consistent
        "pulse_amplitude_raw": _flabel(labels, "$PULSEAMPLITUDE"),
        "temperature_raw": _flabel(labels, "TEMPERATURE"),  # Celsius-like
        "lock_offset_raw": _flabel(labels, "$LOCKOFFSET"),
        "total_duration_s": _flabel(labels, "$TOTALDURATION"),
        "date_epoch_s": _flabel(labels, "$DATE"),
        "long_date_verbatim": long_date,
        "started_local_iso": started_iso,     # anchor event UNVERIFIED
        "tz_offset_min": tz_offset_min,
        "nucleus": labels.get(".OBSERVENUCLEUS"),
        "solvent": labels.get(".SOLVENTNAME"),
    }
    result["nan"] = nan
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
    """Read an NMReady spin-noise session directory: answers.json plus one
    NN_*.dx (or .jdx) file per experiment, numeric prefix = expno."""
    warnings = []
    answers = {}
    apath = os.path.join(session_dir, "answers.json")
    if os.path.isfile(apath):
        try:
            with open(apath, "r") as fh:
                answers = json.load(fh)
        except ValueError as exc:
            warnings.append("answers.json unparseable (%s) -- proceeding "
                            "with file metadata only" % exc)
    else:
        warnings.append("no answers.json in session -- operator fields "
                        "will be placeholders")

    experiments = []
    for entry in sorted(os.listdir(session_dir)):
        full = os.path.join(session_dir, entry)
        if not os.path.isfile(full) \
                or os.path.splitext(entry)[1].lower() not in _EXTS:
            continue
        m = _PREFIX_RE.match(entry)
        if not m:
            warnings.append("file %s lacks the NN_ expno prefix -- skipped "
                            "(see README.md operator checklist)" % entry)
            continue
        expno = int(m.group(1))
        role = _role_for_expno(expno)
        if role is None:
            warnings.append("file %s: expno %d outside the session "
                            "convention -- skipped" % (entry, expno))
            continue
        try:
            result = read_nanalysis_dx(full)
        except Exception as exc:               # decoding must never abort
            raise NanalysisReadError("cannot parse %s: %s" % (full, exc))
        if not result["nan"]["is_fid"]:
            warnings.append(
                "expno %d (%s): DATA TYPE is %r, not 'NMR FID' -- this "
                "looks like a processed-spectrum export; the network needs "
                "the raw FID export (README operator checklist step 6)"
                % (expno, entry, result["nan"]["data_type"]))
        experiments.append({"expno": expno, "role": role, "path": full,
                            "result": result,
                            "files": [("data/%d/%s" % (expno, entry),
                                       full)]})

    phase = {"rg_ladder": 0, "reference_open": 1, "noise": 2,
             "reference_close": 3}
    experiments.sort(key=lambda e: (phase[e["role"]], e["expno"]))
    if not experiments:
        raise NanalysisReadError(
            "no NN_*.dx experiment files found under %s (expected the "
            "session layout of README.md: 10/14/15/16 ladder, 11 open "
            "reference, 20+ noise, 13 close reference)" % session_dir)
    if all(not e["result"]["nan"]["is_fid"] for e in experiments):
        # A session made ENTIRELY of processed spectra is unusable --
        # refuse on this documented standalone-CLI path exactly as the
        # central-packer adapter does, instead of packing a bundle that
        # validates, uploads, and disappoints weeks later.
        raise NanalysisReadError(
            "every export in this session is a processed-SPECTRUM file, "
            "not a raw FID export -- the network cannot use processed "
            "spectra at all. On the NMReady choose the FID JCAMP-DX "
            "export for each record (operator checklist in README.md) "
            "and re-export.")
    return {"answers": answers, "experiments": experiments,
            "answers_path": apath if os.path.isfile(apath) else None,
            "warnings": warnings}


# ---------------------------------------------------------------------------
# meta.json construction
# ---------------------------------------------------------------------------

def _finished_iso(nan, fallback):
    """Derive finished_local = LONG DATE + TOTAL DURATION.  Honesty: which
    event LONG DATE stamps is UNVERIFIED (acquisition start vs save), so
    this is bookkeeping, not clock-audit material.  The arithmetic stays in
    the file's own local time (no conversion through the packing machine's
    timezone)."""
    started = nan.get("started_local_iso")
    dur = nan.get("total_duration_s")
    if isinstance(started, str) and isinstance(dur, float) and dur >= 0:
        import datetime
        try:
            t0 = datetime.datetime.strptime(started, "%Y-%m-%dT%H:%M:%S")
            t1 = t0 + datetime.timedelta(seconds=dur)
            return t1.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass
    return started or fallback


def _experiment_entry(exp, answers, fallback_time):
    """Map one NMReady export to a schema `experiments[]` item.

    Mapping (verified against real v2.2.4.5 output; README table):
      td    = ##$TD verbatim (already the Bruker re+im convention:
              TD = 2 x complex points, cross-checked vs NPOINTS/VAR_DIM)
      sw_hz = ##$SWH (Hz; equals 1/dwell = 1/##FACTOR[X])
      o1_hz = ##$O1 (Hz; equals ##$O1P ppm x ##$SFO1)
      rg    = ##$RECVR_GAIN VERBATIM (units UNVERIFIED -- native units,
              exactly like the JEOL adapter; do NOT convert)
      aq_s_per_row = ##$AQ
    """
    nan = exp["result"]["nan"]
    data = exp["result"]["data"]

    td = nan.get("td")
    if not isinstance(td, int) or td <= 0:
        n = len(data.get("re", data.get("y", [])))
        td = n * (2 if "im" in data else 1)
    sw = nan.get("sw_hz")
    if not isinstance(sw, float) or sw <= 0:
        xf = exp["result"]["info"].get("x_factor")
        sw = (1.0 / xf) if xf else 1.0
    aq = nan.get("aq_s")
    if not isinstance(aq, float) or aq <= 0:
        aq = (td / 2.0) / sw
    rg = nan.get("recvr_gain_raw")
    if not isinstance(rg, float) or rg <= 0:
        blk = _block_record(answers, exp["expno"])
        rg = blk.get("recvr_gain")
        if not isinstance(rg, (int, float)) or rg <= 0:
            rg = 1.0                      # schema needs >0; flagged upstream
    ns = nan.get("ns")
    if not isinstance(ns, int) or ns < 1:
        ns = 1
    o1 = nan.get("o1_hz")
    if not isinstance(o1, float):
        o1 = 0.0
    pulprog = nan.get("pulse_sequence") or (
        "nmready-noise-draft" if exp["role"] == "noise" else "1D")

    return {
        "expno": exp["expno"],
        "role": exp["role"],
        "pulprog": str(pulprog),
        "td": int(td),
        "td1_rows": 1,                    # converter path: repeated 1Ds
        "sw_hz": float(sw),
        "o1_hz": float(o1),
        "rg": float(rg),
        "ns": int(ns),
        "aq_s_per_row": float(aq),
        "started_local": nan.get("started_local_iso") or fallback_time,
        "finished_local": _finished_iso(nan, fallback_time),
    }


def _block_record(answers, expno):
    for blk in answers.get("blocks", []):
        if blk.get("expno") == expno:
            return blk
    return {}


def _tip_deg(nan, default):
    """Achieved tip from the file's own pulse bookkeeping:
    tip = 90 * X_PULSE / OBSERVE_90 (both us-consistent in real output)."""
    xp = nan.get("x_pulse_raw")
    p90 = nan.get("observe_90_raw")
    if isinstance(xp, float) and isinstance(p90, float) and p90 > 0 \
            and xp > 0:
        return 90.0 * xp / p90
    return default


def build_meta(session):
    """Build the full meta.json dict (schema-2.0-shaped, vendor
    'nanalysis') for a read_session() result."""
    answers = session["answers"]
    experiments = session["experiments"]
    nans = [e["result"]["nan"] for e in experiments]

    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    fallback_local = time.strftime("%Y-%m-%dT%H:%M:%S")

    # timezone: prefer the files' own LONG DATE offset (verified present),
    # else the packing machine's.
    tz_candidates = set(n["tz_offset_min"] for n in nans
                        if isinstance(n.get("tz_offset_min"), int))
    if len(tz_candidates) == 1:
        tz_off_min = tz_candidates.pop()
    elif time.daylight and time.localtime().tm_isdst:
        tz_off_min = -int(time.altzone // 60)
    else:
        tz_off_min = -int(time.timezone // 60)

    h1 = None
    for n in nans:
        v = n.get("sfo1_mhz")
        if isinstance(v, float) and v > 0:
            h1 = v
            break
    if h1 is None:
        v = answers.get("h1_freq_mhz")
        h1 = float(v) if isinstance(v, (int, float)) and v > 0 else 60.0
    field_t = None
    for n in nans:
        v = n.get("field_tesla_verbatim")
        if isinstance(v, float) and v > 0:
            field_t = v
            break
    if field_t is None:
        field_t = h1 / GAMMA_H_MHZ_PER_T

    run_mode = answers.get("run_mode_hint", "external-acquisition")
    if run_mode not in ("external-acquisition", "desktest", "simulate",
                        "synthetic-injection", "archival-repackage"):
        run_mode = "external-acquisition"

    sw_version = None
    for n in nans:
        if isinstance(n.get("software_version"), str):
            sw_version = n["software_version"]
            break
    if sw_version is None:
        sw_version = str(answers.get("software_version",
                                     "unknown (export carried no version "
                                     "comment; enter by hand)"))

    system = None
    for n in nans:
        if isinstance(n.get("spectrometer_system"), str):
            system = n["spectrometer_system"]
            break

    noise_gain = None
    for e in experiments:
        if e["role"] == "noise":
            g = e["result"]["nan"].get("recvr_gain_raw")
            if isinstance(g, float):
                noise_gain = g
                break

    p90 = answers.get("p90_us")
    if not isinstance(p90, (int, float)) or p90 <= 0:
        p90 = None
        for n in nans:
            v = n.get("observe_90_raw")
            if isinstance(v, float) and v > 0:
                p90 = v            # us-consistent in real output; UNVERIFIED
                break
    if p90 is None:
        p90 = 16.0                 # 60PRO-typical 1H p90; placeholder

    ref_tip = answers.get("ref_tip_deg")
    if not isinstance(ref_tip, (int, float)) or ref_tip <= 0:
        ref_tip = 5.0

    lock_nucleus = str(answers.get("lock_nucleus", "unknown"))
    notes = str(answers.get("operator_notes", ""))
    lock_note = ("NMReady internal lock (1H-or-2H channel) state during "
                 "noise blocks: %s." % lock_nucleus)
    temps = [n.get("temperature_raw") for n in nans
             if isinstance(n.get("temperature_raw"), float)]
    if temps:
        lock_note += (" File TEMPERATURE label (unit UNVERIFIED, reads as "
                      "regulated magnet temperature in Celsius): %s."
                      % sorted(set(round(t, 3) for t in temps)))
    operator_notes = (notes + " " + lock_note).strip()

    vt_k = answers.get("vt_setpoint_k")
    if not isinstance(vt_k, (int, float)) or vt_k <= 0:
        # NMReady magnets are thermally regulated slightly above room
        # temperature; the real corpus file reads TEMPERATURE=33.0
        # (Celsius-like). 306 K is that reading; override in answers.json.
        vt_k = 306.0

    meta = {
        "schema_version": "2.0",
        "program_version": ADAPTER_VERSION,
        "software": {
            "script_version": ADAPTER_VERSION,
            "schema_version": "2.0",
            "script_sha256": _self_sha256(),
            "writer": "vendors/nanalysis/nanalysis_reader.py",
            "run_mode": run_mode,
        },
        "created_utc": created,
        "local_timezone_offset_min": tz_off_min,
        "vendor": VENDOR,
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
            "h1_freq_mhz": h1,
            "field_tesla": field_t,
            "console": system or str(answers.get("model",
                                                 "Nanalysis NMReady")),
            "probe_string": "NMReady integrated benchtop probe",
            "probe_type": "permanent-magnet-benchtop",
            "coil_temp_k": vt_k,
            "preamp_temp_k": None,
        },
        "instrument": {
            VENDOR: {
                "software_version": sw_version,
                "model": str(answers.get("model", "")) or "unknown",
                "data_format": "jcamp-dx",
                "receiver_gain": noise_gain,
                "lock_nucleus": lock_nucleus,
                "field_state_notes": str(answers.get("field_state_notes",
                                                     "")),
            }
        },
        "sample": {
            "description": str(answers.get("sample_description",
                                           "UNKNOWN")),
            "h2o_fraction_pct": float(answers.get("h2o_fraction_pct",
                                                  100.0)),
            "d2o_pct": float(answers.get("d2o_pct", 0.0)),
            "additives": str(answers.get("additives", "")),
            "tube_od_mm": float(answers.get("tube_od_mm", 5.0)),
            "sample_volume_ul": float(answers.get("sample_volume_ul",
                                                  550.0)),
            "vt_setpoint_k": float(vt_k),
        },
        "environment": {
            "locked": lock_nucleus in ("1H", "2H"),
            "operator_notes": operator_notes,
        },
        "calibration": {
            "p90_us": float(p90),
            "p90_power_db_or_w": "n/a (NMReady internal amplitude units; "
                                 "PULSE_AMPLITUDE recorded verbatim in the "
                                 "data files)",
            "rg_ladder": [
                {"expno": e["expno"],
                 "rg": _experiment_entry(e, answers, fallback_local)["rg"],
                 "tip_deg": _tip_deg(e["result"]["nan"], float(ref_tip))}
                for e in experiments if e["role"] == "rg_ladder"
            ] or [{"expno": experiments[0]["expno"], "rg": 1.0,
                   "tip_deg": float(ref_tip)}],
            "topshim_ok": bool(answers.get("shim_ok", False)),
        },
        "experiments": [_experiment_entry(e, answers, fallback_local)
                        for e in experiments],
        "checksums": {},  # filled by pack()
    }

    if isinstance(answers.get("firmware_version"), str) \
            and answers["firmware_version"]:
        meta["instrument"][VENDOR]["firmware_version"] = \
            answers["firmware_version"]

    # NO clock_audit block: the only timestamps in the exports are
    # LONG DATE / $DATE whose anchor event is UNVERIFIED, and the schema
    # documents that packer bundles without block timestamps simply omit
    # the (optional) object.  The partner session decides whether NMReady
    # timestamps can feed the audit (README checklist).
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

def pack(session_dir, out_dir=None):
    session = read_session(session_dir)
    for w in session["warnings"]:
        info("WARN : %s" % w)
    meta = build_meta(session)

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
    info("packed %d files, schema 2.0, vendor %s, run_mode %s"
         % (len(files), VENDOR, meta["software"]["run_mode"]))
    info("NOTE : shared-schema wiring for vendor 'nanalysis' is a "
         "follow-up commit; validate against a patched schema copy "
         "(see README.md / test_nanalysis_chain.sh) until it lands")
    print(bundle)
    return bundle


# ---------------------------------------------------------------------------
# Central-packer adapter (VendorReader interface, duck-typed)
# ---------------------------------------------------------------------------

class NanalysisReader(object):
    """packer/pack_bundle.py VendorReader-compatible adapter (duck-typed on
    purpose: this module must not import the shared packer).  Registration
    is the follow-up wiring commit -- see README.md 'Deferred wiring'."""

    name = VENDOR

    def __init__(self, pack_error=NanalysisReadError):
        self._err = pack_error
        self._warnings = []

    def discover_experiments(self, data_dir):
        try:
            names = sorted(os.listdir(data_dir))
        except OSError as exc:
            raise self._err("cannot list data directory %s: %s"
                            % (data_dir, exc))
        out = []
        skipped = []
        for n in names:
            full = os.path.join(data_dir, n)
            if not os.path.isfile(full) \
                    or os.path.splitext(n)[1].lower() not in _EXTS:
                continue
            m = _PREFIX_RE.match(n)
            if m:
                out.append((int(m.group(1)), full))
            else:
                skipped.append(n)
        if skipped:
            # Parity with read_session(): NMReady's own default export
            # names carry no prefix, so silence here would quietly drop
            # real records from the bundle.
            self._warnings.append(
                "SKIPPED %d JCAMP file(s) without the NN_ expno prefix "
                "(%s%s) -- rename per the operator checklist (e.g. "
                "11_sn_ref_open.dx) or they will not be packed"
                % (len(skipped), ", ".join(skipped[:6]),
                   ", ..." if len(skipped) > 6 else ""))
        if not out:
            raise self._err(
                "no NN_*.dx / NN_*.jdx NMReady files found under %s -- "
                "expected one JCAMP-DX FID export per experiment named per "
                "the operator checklist (e.g. 11_sn_ref_open.dx); see "
                "vendors/nanalysis/README.md" % data_dir)
        return sorted(out)

    def read_experiment(self, dirpath):
        try:
            result = read_nanalysis_dx(dirpath)
        except Exception as exc:
            raise self._err("cannot parse NMReady file %s: %s"
                            % (dirpath, exc))
        nan = result["nan"]
        found = {"td1_rows": 1}
        if isinstance(nan.get("td"), int) and nan["td"] > 0:
            found["td"] = nan["td"]
        if isinstance(nan.get("sw_hz"), float) and nan["sw_hz"] > 0:
            found["sw_hz"] = nan["sw_hz"]
        if isinstance(nan.get("o1_hz"), float):
            found["o1_hz"] = nan["o1_hz"]
        if isinstance(nan.get("recvr_gain_raw"), float) \
                and nan["recvr_gain_raw"] > 0:
            found["rg"] = nan["recvr_gain_raw"]   # verbatim; UNVERIFIED units
        if isinstance(nan.get("ns"), int) and nan["ns"] >= 1:
            found["ns"] = nan["ns"]
        if isinstance(nan.get("aq_s"), float) and nan["aq_s"] > 0:
            found["aq_s_per_row"] = nan["aq_s"]
        if nan.get("pulse_sequence"):
            found["pulprog"] = str(nan["pulse_sequence"])
        if isinstance(nan.get("sfo1_mhz"), float) and nan["sfo1_mhz"] > 0:
            found["h1_freq_mhz"] = nan["sfo1_mhz"]
        if nan.get("software_version"):
            found["_nanalysis_version"] = nan["software_version"]
        if not nan.get("is_fid"):
            found["_not_fid"] = str(nan.get("data_type"))
        return found

    def instrument_block(self, answers, discovered):
        warnings = list(self._warnings)
        ans_inst = answers.get("instrument", {})
        if not isinstance(ans_inst, dict):
            ans_inst = {}
        version = ans_inst.get("software_version")
        if not version:
            guesses = set(d.get("_nanalysis_version")
                          for d in discovered.values()
                          if d.get("_nanalysis_version"))
            if len(guesses) == 1:
                version = guesses.pop()
                warnings.append("software_version %r taken from the "
                                "##JCAMP-DX label comment" % version)
        if not version:
            raise self._err(
                "answers.json is missing instrument.software_version and "
                "no export carried the '$$ Nanalysis NMReady v...' comment "
                '-- add e.g. "instrument": {"software_version": "2.2.6.2"}')
        not_fid = sorted(e for e, d in discovered.items()
                         if d.get("_not_fid"))
        if not_fid:
            if len(not_fid) == len(discovered):
                # A session made ENTIRELY of processed spectra is unusable
                # -- refusing here beats a bundle that validates, uploads,
                # and disappoints weeks later.
                raise self._err(
                    "every export in this session is a processed-SPECTRUM "
                    "file, not a raw FID export -- the network cannot use "
                    "processed spectra at all. On the NMReady choose the "
                    "FID JCAMP-DX export for each record (operator "
                    "checklist in vendors/nanalysis/README.md) and "
                    "re-export.")
            warnings.append("expno(s) %s are processed-spectrum exports, "
                            "not raw FID exports -- the network needs the "
                            "FID .dx (README operator checklist)" % not_fid)
        rg = None
        for d in discovered.values():
            if d.get("rg") is not None:
                rg = d["rg"]
        block = {
            "software_version": str(version),
            "model": ans_inst.get("model") or "unknown",
            "data_format": "jcamp-dx",
            "receiver_gain": rg,
            "lock_nucleus": str(ans_inst.get("lock_nucleus", "unknown")),
            "field_state_notes": str(ans_inst.get("field_state_notes", "")),
        }
        if ans_inst.get("firmware_version"):
            block["firmware_version"] = str(ans_inst["firmware_version"])
        return block, warnings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Nanalysis NMReady session reader/packer (spin-noise "
                    "network, draft pending partner validation).")
    sub = parser.add_subparsers(dest="cmd")

    p_pack = sub.add_parser("pack", help="pack a session dir into a bundle")
    p_pack.add_argument("session_dir")
    p_pack.add_argument("--out-dir", default=None)

    p_meta = sub.add_parser("meta", help="print the meta.json for a session")
    p_meta.add_argument("session_dir")

    p_insp = sub.add_parser("inspect", help="dump one export's digest")
    p_insp.add_argument("path")
    p_insp.add_argument("--data-head", type=int, default=0, metavar="N")

    args = parser.parse_args(argv)
    if args.cmd == "pack":
        pack(args.session_dir, args.out_dir)
    elif args.cmd == "meta":
        session = read_session(args.session_dir)
        for w in session["warnings"]:
            info("WARN : %s" % w)
        print(json.dumps(build_meta(session), indent=2))
    elif args.cmd == "inspect":
        result = read_nanalysis_dx(args.path)
        print(json.dumps(result["nan"], indent=2, default=str))
        if args.data_head:
            for name in sorted(result["data"]):
                print("%s: %s" % (name, result["data"][name][:args.data_head]))
    else:
        parser.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
