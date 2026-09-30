#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_report_qa_flags.py -- validate the QA flags analysis/facility_report.py
raises for the TopSpin parameter dialect (software.param_api) and for the
rows a bundle's data files hold against the rows meta.json declares.

    python3 testing/test_report_qa_flags.py [--out-dir DIR] [--meta-074 PATH]
                                            [--skip-e2e]

Background (Oulu, TopSpin 3.7.0, 2026-09-25): the console accepted the
script's switch to 2D but did not create the F1 parameter file acqu2, and
while it was missing every parameter write into the dataset was lost
silently -- the v0.7.4 desktest bundle's pseudo-2D blocks carried the 1D
template's TD/RG/PULPROG under a meta.json declaring 8 / 89 / 8 rows. A
live run there would have produced a 1D fid (or a ser of the wrong
length) under a meta.json declaring pulse-free rows. The script (0.7.5)
now creates the file and reads TD/RG back; the report says what the
console did (param_api lines) and compares what the data file holds with
what meta.json declares (rows declared vs read).

  1. param_api lines -- qa_flags() called directly on a minimal bundle:
     * a v0.7.4 meta (param_api WITHOUT the 0.7.5 keys) -> NO 'TopSpin
       parameter writes' / 'TopSpin F1 parameter file' line. By default a
       built-in copy of the param_api block of Anne's Oulu desktest meta
       is used; that meta itself (meta-20260925.json, kept outside the
       repository, read-only) only when --meta-074 names it;
     * a 0.7.5 meta with f1_files_created 0, f1_fnmode_edits 0 and
       acq_write_mismatch 0 -> the two OK lines, no FnMODE note;
     * f1_files_source 'operator' -> WARN 'TopSpin F1 parameter file';
       acq_write_mismatch > 0 -> WARN 'TopSpin parameter writes';
     * f1_files_source 'par:COSYGPSW' with f1_fnmode_edits 1 and
       f1_fnmode_copied '6' -> the OK line names the source and the one
       F1 file set to FnMODE 0 (which carried '6');
     * f1_fnmode_edits 1 with no copy (the file was inherited from a 2D
       template) -> the OK line still says the script copied nothing and
       mentions the one file set to FnMODE 0.
  2. rows declared vs read -- the Bruker reader + qa_flags on tiny
     synthetic bundles (both the streaming iter_rows path and read_rows):
     * a ser holding 2 of the 4 rows meta.json declares -> read_log note
       'ser holds 2 of the 4 rows ...' and a WARN 'rows declared vs read';
     * a 1D fid in an expno whose meta declares 8 rows -> read_log note
       'a 1D fid (1 row) was found ...' and a WARN naming exactly that;
     * every row present -> the OK line and no WARN of that family;
     * the reader's numbers do not change: the rows present are read.
  3. end to end (unless --skip-e2e): make_physics_bundle -> the noise
     expno's ser truncated to half its rows (checksum updated) ->
     facility_report -> report.json's science.qa_flags carries the WARN
     for expno 12; the untouched bundle's report carries the OK line.
  4. raw data all zeros (0.7.7; Oulu, Avance III HD 500, 2026-09-30: the
     receiver unit refused every pseudo-2D block, TopSpin pre-allocated
     the full-size ser, the first row held data and rows 2..N zeros --
     the 0.7.6 report co-added 88 zero rows as data):
     * a ser of the declared size with data in row 0 and zeros after it
       -> n_rows 1, n_rows_zero N-1, only the data row yielded, a WARN
       'rows declared vs read' with the zeros wording and a FAIL 'raw data
       all zeros' naming the block (both reader paths);
     * a ser that is zeros throughout -> refused (EXCLUDED FAIL);
     * a 1D fid of zeros -> refused;
     * a SOFTWARE-TEST bundle (run_mode desktest -- what the Jython
       harness produces, whose mocked acquisitions leave real-size files
       and whose legacy-dru flavors leave Oulu's shape) -> the report
       completes with science None and carries the same raw-data content
       check in report.json raw_data_check: the FAIL 'raw data all zeros'
       for a ser of one data row and zeros, the EXCLUDED FAIL 'raw data
       expno N (role)' for a ser of zeros only, the WARN 'rows declared
       vs read'; before 0.7.7 a software-test report carried no QA at all
       (unless --skip-e2e);
     * end to end (unless --skip-e2e): the physics bundle's noise ser with
       rows 2..N zeroed -> the report completes and carries the FAIL;
     * the report's pulse-program parser resolves the shipped sequences'
       'd11 wr' line as a D[11] term (the timing model of v0.7.7).

Exit 0 iff every check passes. Python 3 standard library only in this
file; the report itself needs numpy (and matplotlib for its figures).
"""

from __future__ import print_function

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
REPORT_PY = os.path.join(REPO, "analysis", "facility_report.py")

# Anne's Oulu desktest meta (v0.7.4, TopSpin 3.7.0) is a read-only fixture
# that lives OUTSIDE the repository and is never copied into it; the test
# reads it only when --meta-074 names it.  The default must not point
# outside the checkout (a path beside the repository is one machine's
# layout, not the test's), so it is the built-in copy of that meta's
# param_api block below.
META_074_DEFAULT = None

# The param_api block that meta wrote (keys of the 0.7.3/0.7.4 script; no
# f1_files_* / acq_write_* keys), so the same case runs where the file is
# not available.
PARAM_API_074 = {
    "reload_ok": 1, "f1_td_readback_mismatch": "", "putpar_failures": 0,
    "last_putpar_error": "", "parmode_unverified": 0, "failed_forms": [],
    "acqudim_readback": 2, "reload_failed": 0, "f1_td_form": "1 TD",
    "parmode_form": "name", "f1_td_readback_source": "",
    "parmode_readback": "1", "parmode_already": 8, "f1_td_verified": 0,
    "f1_readback_unreliable": 0, "dim_readback_unreliable": 0,
    "f1_td_already": 0,
}

PARAM_API_075 = dict(PARAM_API_074)
PARAM_API_075.update({
    "f1_td_verified": 1, "f1_td_readback_source": "getpar",
    "f1_files_created": 0, "f1_files_source": "", "f1_fnmode_edits": 0,
    "f1_fnmode_copied": "",
    "acq_write_mismatch": 0, "last_acq_write_mismatch": "",
})

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
# Fixtures
# ---------------------------------------------------------------------------

def base_meta(param_api, experiments):
    """A minimal meta.json in the orchestrator's shape: what qa_flags reads
    (environment, software, experiments) and what the Bundle reader needs
    (experiments' td / td1_rows)."""
    return {
        "schema_version": "1.2",
        "program_version": "0.7.5",
        "created_utc": "2026-09-25T10:00:00Z",
        "facility": {"institution": "QA flags test", "facility_slug":
                     "qa-flags-test"},
        "environment": {"locked": False, "lock_sweep_confirmed_off": True},
        "software": {"script_version": "0.7.5", "schema_version": "1.2",
                     "script_sha256": "unavailable", "run_mode": "live",
                     "param_api": copy.deepcopy(param_api)},
        "calibration": {"rg_ladder": []},
        "experiments": experiments,
        "checksums": {},
    }


def acqus_text(td):
    return ("##TITLE= Parameter file, QA flags test\n"
            "##$PULPROG= <zgnoise2d>\n"
            "##$TD= %d\n"
            "##$BYTORDA= 0\n"
            "##$DTYPA= 0\n"
            "##$NS= 1\n"
            "##END=\n" % td).encode("ascii")


def int32_rows(n_rows, td, seed):
    """n_rows rows of td little-endian int32 (re/im interleaved), each row
    exactly td*4 bytes -- td is chosen so that is a whole 1024-byte block
    and the stride question does not arise."""
    out = bytearray()
    v = seed
    for _r in range(n_rows):
        for _i in range(td):
            v = (v * 1103515245 + 12345) & 0x7fffffff
            out += struct.pack("<i", (v % 20001) - 10000)
    return bytes(out)


def write_bundle(path, meta, files):
    """Zip meta.json + data files, with sha256 checksums filled in the way
    the orchestrator does (so the bundle's own validator is content)."""
    meta = copy.deepcopy(meta)
    for arc, payload in files:
        meta["checksums"][arc] = "sha256:" + hashlib.sha256(payload).hexdigest()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta, indent=1) + "\n")
        for arc, payload in files:
            zf.writestr(arc, payload)
    return meta


def exp(expno, role, td, rows):
    return {"expno": expno, "role": role, "pulprog":
            "zgnoise2d" if role == "noise" else "zgref2d",
            "td": td, "td1_rows": rows, "sw_hz": 5000.0, "o1_hz": 0.0,
            "rg": 101.0, "ns": 1, "aq_s_per_row": 0.05}


def flags_named(flags, name):
    return [f for f in flags if f["check"] == name]


# ---------------------------------------------------------------------------
# 1. param_api lines
# ---------------------------------------------------------------------------

def param_api_cases(fr, work, meta_074_path):
    param_lines = ("TopSpin parameter writes", "TopSpin F1 parameter file")

    def run(tag, meta):
        p = os.path.join(work, "pa_%s.zip" % tag)
        write_bundle(p, meta, [])
        b = fr.Bundle(p)
        return fr.qa_flags(b, meta, None, [])

    # (a) v0.7.4 meta: no 0.7.5 keys -> no param_api line at all
    src = "built-in copy of its param_api block"
    meta_074 = base_meta(PARAM_API_074, [])
    if meta_074_path and os.path.isfile(meta_074_path):
        with open(meta_074_path, "r") as fh:
            real = json.load(fh)
        pa = (real.get("software") or {}).get("param_api")
        if isinstance(pa, dict):
            meta_074 = copy.deepcopy(real)
            src = "Anne's desktest meta %s" % os.path.basename(meta_074_path)
            check("fixture: the v0.7.4 meta carries none of the 0.7.5 "
                  "param_api keys (%s)" % src,
                  not any(k in pa for k in ("f1_files_created",
                                            "acq_write_mismatch")),
                  sorted(pa))
    fl = run("074", meta_074)
    got = [f for f in fl if f["check"] in param_lines]
    check("v0.7.4 meta (%s): no 'TopSpin parameter writes' / 'TopSpin F1 "
          "parameter file' line" % src, not got,
          [(f["level"], f["check"]) for f in got])

    # (b) 0.7.5 meta, clean console: the two OK lines
    fl = run("075", base_meta(PARAM_API_075, []))
    w = flags_named(fl, "TopSpin parameter writes")
    f1 = flags_named(fl, "TopSpin F1 parameter file")
    check("0.7.5 meta, clean: OK 'TopSpin parameter writes' (TD and RG read "
          "back as written)", len(w) == 1 and w[0]["level"] == "OK"
          and "read back as written" in w[0]["detail"], w)
    check("0.7.5 meta, clean: OK 'TopSpin F1 parameter file' (created by "
          "the console itself / inherited; no FnMODE note when nothing was "
          "edited)", len(f1) == 1 and f1[0]["level"] == "OK"
          and "console itself" in f1[0]["detail"]
          and "FnMODE" not in f1[0]["detail"], f1)

    # (c) operator source -> WARN; mismatches -> WARN
    pa = dict(PARAM_API_075)
    pa.update({"f1_files_source": "operator", "f1_files_created": 1,
               "f1_td_verified": 0})
    fl = run("operator", base_meta(pa, []))
    f1 = flags_named(fl, "TopSpin F1 parameter file")
    check("f1_files_source 'operator': WARN 'TopSpin F1 parameter file' "
          "naming the operator's parmode and the unverified row count",
          len(f1) == 1 and f1[0]["level"] == "WARN"
          and "operator" in f1[0]["detail"]
          and "NOT verified" in f1[0]["detail"], f1)
    pa = dict(PARAM_API_075)
    pa.update({"acq_write_mismatch": 2,
               "last_acq_write_mismatch": "expno 12 RG: wrote 101 read 1.0"})
    fl = run("mismatch", base_meta(pa, []))
    w = flags_named(fl, "TopSpin parameter writes")
    check("acq_write_mismatch 2: WARN 'TopSpin parameter writes' quoting "
          "the count and the last mismatch",
          len(w) == 1 and w[0]["level"] == "WARN"
          and w[0]["detail"].startswith("2 TD/RG write(s)")
          and "expno 12 RG: wrote 101 read 1.0" in w[0]["detail"], w)

    # (d) library copy: OK line names the source and the FnMODE edit
    pa = dict(PARAM_API_075)
    pa.update({"f1_files_source": "par:COSYGPSW", "f1_files_created": 1,
               "f1_fnmode_edits": 1, "f1_fnmode_copied": "6"})
    fl = run("parcopy", base_meta(pa, []))
    f1 = flags_named(fl, "TopSpin F1 parameter file")
    check("f1_files_source 'par:COSYGPSW', f1_fnmode_edits 1: OK line names "
          "the source and the one F1 file set to FnMODE 0 (which carried "
          "'6')",
          len(f1) == 1 and f1[0]["level"] == "OK"
          and "par:COSYGPSW" in f1[0]["detail"]
          and "by file edit in 1 F1 file(s)" in f1[0]["detail"]
          and "'6'" in f1[0]["detail"], f1)

    # (e) nothing copied but an inherited acqu2 (2D template) normalised:
    # the OK line keeps saying the script copied nothing, and adds the
    # edit count and the value the file carried
    pa = dict(PARAM_API_075)
    pa.update({"f1_fnmode_edits": 1, "f1_fnmode_copied": "5"})
    fl = run("inherited", base_meta(pa, []))
    f1 = flags_named(fl, "TopSpin F1 parameter file")
    check("f1_fnmode_edits 1 with no copy (inherited acqu2): OK line "
          "'console itself' mentions the one file set to FnMODE 0 (which "
          "carried '5')",
          len(f1) == 1 and f1[0]["level"] == "OK"
          and "console itself" in f1[0]["detail"]
          and "by file edit in 1 F1 file(s)" in f1[0]["detail"]
          and "'5'" in f1[0]["detail"], f1)


# ---------------------------------------------------------------------------
# 2. rows declared vs read
# ---------------------------------------------------------------------------

TD = 256            # 256 int32 = 1024 bytes per row: one padding block
ROW = TD * 4


def rows_cases(fr, work):
    fam = "rows declared vs read"
    # Bundle A: expno 11 (reference_open) declares 4 rows, ser holds 2;
    #           expno 12 (noise) declares 8 rows, holds a 1D fid.
    exps_a = [exp(11, "reference_open", TD, 4), exp(12, "noise", TD, 8)]
    files_a = [("data/11/acqus", acqus_text(TD)),
               ("data/11/ser", int32_rows(2, TD, 7)),
               ("data/12/acqus", acqus_text(TD)),
               ("data/12/fid", int32_rows(1, TD, 11))]
    # Bundle B: both complete.
    exps_b = [exp(11, "reference_open", TD, 4), exp(12, "noise", TD, 8)]
    files_b = [("data/11/acqus", acqus_text(TD)),
               ("data/11/ser", int32_rows(4, TD, 7)),
               ("data/12/acqus", acqus_text(TD)),
               ("data/12/ser", int32_rows(8, TD, 11))]
    pa = os.path.join(work, "rows_short.zip")
    pb = os.path.join(work, "rows_full.zip")
    meta_a = write_bundle(pa, base_meta(PARAM_API_075, exps_a), files_a)
    meta_b = write_bundle(pb, base_meta(PARAM_API_075, exps_b), files_b)

    for path, meta, label, streaming in (
            (pa, meta_a, "short", True), (pa, meta_a, "short", False),
            (pb, meta_b, "full", True), (pb, meta_b, "full", False)):
        b = fr.Bundle(path)
        n_read = {}
        for e in meta["experiments"]:
            if streaming:
                got = list(b.iter_rows(e["expno"], e))
                n_read[e["expno"]] = len(got)
            else:
                rows, _acq = b.read_rows(e["expno"], e)
                n_read[e["expno"]] = 0 if rows is None else int(rows.shape[0])
        path_tag = "iter_rows" if streaming else "read_rows"
        fl = fr.qa_flags(b, meta, None, [])
        fam_fl = flags_named(fl, fam)
        if label == "short":
            check("%s bundle, %s: the rows present are read (2 of 11, 1 of "
                  "12) -- the numbers do not change" % (label, path_tag),
                  n_read == {11: 2, 12: 1}, n_read)
            n11 = b.read_log.get(11, {})
            n12 = b.read_log.get(12, {})
            check("%s bundle, %s: read_log note for expno 11 says 'ser holds "
                  "2 of the 4 rows meta.json declares'" % (label, path_tag),
                  n11.get("n_rows") == 2 and "ser holds 2 of the 4 rows "
                  "meta.json declares" in n11.get("note", ""), n11)
            check("%s bundle, %s: read_log note for expno 12 says 'a 1D fid "
                  "(1 row) was found where meta.json declares 8 rows'"
                  % (label, path_tag),
                  n12.get("n_rows") == 1 and "a 1D fid (1 row) was found "
                  "where meta.json declares 8 rows" in n12.get("note", ""),
                  n12)
            check("%s bundle, %s: rows_declared_vs_read records both "
                  "(11: ser 2/4, 12: fid 1/8)" % (label, path_tag),
                  b.rows_declared_vs_read == {
                      11: {"declared": 4, "read": 2, "file": "ser"},
                      12: {"declared": 8, "read": 1, "file": "fid"}},
                  b.rows_declared_vs_read)
            warns = [f for f in fam_fl if f["level"] == "WARN"]
            check("%s bundle, %s: two WARN '%s' lines and no OK line"
                  % (label, path_tag, fam),
                  len(warns) == 2 and len(fam_fl) == 2, fam_fl)
            d11 = [f["detail"] for f in warns if "expno 11" in f["detail"]]
            d12 = [f["detail"] for f in warns if "expno 12" in f["detail"]]
            check("%s bundle, %s: WARN for expno 11 names the declared 4 "
                  "rows and the 2 the ser holds" % (label, path_tag),
                  len(d11) == 1 and d11[0].startswith(
                      "meta.json declares 4 rows for expno 11")
                  and "ser holds 2 row(s)" in d11[0], d11)
            check("%s bundle, %s: WARN for expno 12 reads 'meta.json declares "
                  "8 rows for expno 12 (noise), a 1D fid (1 row) was found'"
                  % (label, path_tag),
                  len(d12) == 1 and d12[0].startswith(
                      "meta.json declares 8 rows for expno 12 (noise), "
                      "a 1D fid (1 row) was found"), d12)
        else:
            check("%s bundle, %s: every row read (4 of 11, 8 of 12)"
                  % (label, path_tag), n_read == {11: 4, 12: 8}, n_read)
            check("%s bundle, %s: no read_log note, nothing in "
                  "rows_declared_vs_read" % (label, path_tag),
                  "note" not in b.read_log.get(11, {"note": 1})
                  and "note" not in b.read_log.get(12, {"note": 1})
                  and b.rows_declared_vs_read == {},
                  (b.read_log, b.rows_declared_vs_read))
            check("%s bundle, %s: exactly one OK '%s' line (2 expnos "
                  "compared), no WARN" % (label, path_tag, fam),
                  len(fam_fl) == 1 and fam_fl[0]["level"] == "OK"
                  and "2 expno(s) compared" in fam_fl[0]["detail"], fam_fl)


# ---------------------------------------------------------------------------
# 3. end to end on a physics bundle
# ---------------------------------------------------------------------------

def truncate_noise_ser(src, dst):
    """Copy the bundle with expno 12's ser cut to half its declared rows
    (whole padded rows), the checksum updated so the validator is content
    and the report reads it -- the aborted-run shape."""
    zin = zipfile.ZipFile(src, "r")
    meta = json.loads(zin.read("meta.json").decode("utf-8"))
    e12 = [e for e in meta["experiments"] if e["expno"] == 12][0]
    rows = int(e12["td1_rows"])
    row_bytes = int(e12["td"]) * 4
    padded = ((row_bytes + 1023) // 1024) * 1024
    keep = rows // 2
    ser = zin.read("data/12/ser")[:keep * padded]
    meta["checksums"]["data/12/ser"] = "sha256:" + hashlib.sha256(ser).hexdigest()
    zout = zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED)
    for info in zin.infolist():
        if info.filename == "meta.json":
            zout.writestr("meta.json", json.dumps(meta, indent=1) + "\n")
        elif info.filename == "data/12/ser":
            zout.writestr("data/12/ser", ser)
        else:
            zout.writestr(info, zin.read(info.filename))
    zout.close()
    zin.close()
    return rows, keep


def e2e(work):
    fam = "rows declared vs read"
    base = subprocess.check_output(
        [sys.executable, os.path.join(REPO, "testing", "make_physics_bundle.py"),
         "--feature", "none", "--clock-offset", "0", "--out-dir", work],
        stderr=subprocess.DEVNULL).decode().strip().splitlines()[-1]
    short = os.path.join(work, "short_" + os.path.basename(base))
    rows, keep = truncate_noise_ser(base, short)

    def qa_of(bundle, out):
        subprocess.check_call(
            [sys.executable, REPORT_PY, bundle, "--out", out],
            stdout=subprocess.DEVNULL)
        with open(os.path.join(out, "report.json"), "r") as fh:
            rep = json.load(fh)
        return rep, flags_named((rep.get("science") or {}).get("qa_flags")
                                or [], fam)

    rep, fl = qa_of(short, os.path.join(work, "report_short"))
    warns = [f for f in fl if f["level"] == "WARN"]
    check("e2e: truncated noise ser (%d of %d rows) -> report.json carries "
          "a WARN '%s' for expno 12 and no OK line" % (keep, rows, fam),
          len(warns) == 1 and len(fl) == 1
          and warns[0]["detail"].startswith(
              "meta.json declares %d rows for expno 12 (noise)" % rows)
          and "ser holds %d row(s)" % keep in warns[0]["detail"], fl)
    rl = ((rep.get("science") or {}).get("raw_data_read") or {})
    check("e2e: report.json's raw-data read log carries the reader's note "
          "for expno 12", "ser holds %d of the %d rows" % (keep, rows)
          in json.dumps(rl), sorted(rl)[:6])
    rep, fl = qa_of(base, os.path.join(work, "report_full"))
    check("e2e: untouched physics bundle -> exactly one OK '%s' line "
          "(3 expnos compared)" % fam,
          len(fl) == 1 and fl[0]["level"] == "OK"
          and "3 expno(s) compared" in fl[0]["detail"], fl)
    other = [f for f in (rep.get("science") or {}).get("qa_flags") or []
             if f["check"] in ("TopSpin parameter writes",
                               "TopSpin F1 parameter file")]
    check("e2e: a synthetic bundle without param_api gets no param_api line",
          not other, other)


# ---------------------------------------------------------------------------
# 4. raw data all zeros (0.7.7)
# ---------------------------------------------------------------------------

def zero_rows_cases(fr, work):
    fam = "rows declared vs read"
    zfam = "raw data all zeros"
    row0 = int32_rows(1, TD, 31)
    # expno 11: 4 rows declared, row 0 data, rows 1-3 zeros (Oulu's shape)
    # expno 12: 8 rows declared, all zeros
    # expno 13: reference_close declares 2 rows, a 1D fid of zeros
    exps = [exp(11, "reference_open", TD, 4), exp(12, "noise", TD, 8),
            exp(13, "reference_close", TD, 2)]
    files = [("data/11/acqus", acqus_text(TD)),
             ("data/11/ser", row0 + b"\x00" * (3 * ROW)),
             ("data/11/acqu2s", b"##TITLE= status F1\n##$TD= 1\n##END=\n"),
             ("data/12/acqus", acqus_text(TD)),
             ("data/12/ser", b"\x00" * (8 * ROW)),
             ("data/13/acqus", acqus_text(TD)),
             ("data/13/fid", b"\x00" * ROW)]
    pz = os.path.join(work, "rows_zeros.zip")
    meta = write_bundle(pz, base_meta(PARAM_API_075, exps), files)
    for streaming in (True, False):
        b = fr.Bundle(pz)
        n_read = {}
        for e in meta["experiments"]:
            if streaming:
                got = list(b.iter_rows(e["expno"], e))
                n_read[e["expno"]] = len(got)
            else:
                rows, _acq = b.read_rows(e["expno"], e)
                n_read[e["expno"]] = 0 if rows is None else int(rows.shape[0])
        tag = "iter_rows" if streaming else "read_rows"
        check("zeros bundle, %s: only the data row of expno 11 is yielded "
              "(1), nothing from the all-zero ser (12) or fid (13)" % tag,
              n_read == {11: 1, 12: 0, 13: 0}, n_read)
        n11 = b.read_log.get(11, {})
        check("zeros bundle, %s: read_log for expno 11 says n_rows 1, "
              "n_rows_zero 3 and notes the zeros" % tag,
              n11.get("n_rows") == 1 and n11.get("n_rows_zero") == 3
              and "3 row(s) are all zeros" in n11.get("note", ""), n11)
        check("zeros bundle, %s: rows_declared_vs_read for expno 11 records "
              "declared 4, read 1, zero_rows 3" % tag,
              b.rows_declared_vs_read.get(11) == {
                  "declared": 4, "read": 1, "file": "ser", "zero_rows": 3},
              b.rows_declared_vs_read)
        check("zeros bundle, %s: the all-zero ser (12) and fid (13) are "
              "refused with the 'holds only zeros' reason, the acqu2s-less "
              "refusal without a status note" % tag,
              "holds only zeros in all 8 row(s)" in b.read_errors.get(12, "")
              and "holds only zeros in all 1 row(s)" in b.read_errors.get(13, "")
              and "acqu2s" not in b.read_errors.get(12, ""),
              b.read_errors)
        fl = fr.qa_flags(b, meta, None, [])
        fam_fl = flags_named(fl, fam)
        check("zeros bundle, %s: one WARN '%s' (expno 11) with the zeros "
              "wording, no OK line" % (tag, fam),
              len(fam_fl) == 1 and fam_fl[0]["level"] == "WARN"
              and "expno 11" in fam_fl[0]["detail"]
              and "3 of its rows are all zeros" in fam_fl[0]["detail"]
              and "1 row(s) with data were analysed" in fam_fl[0]["detail"],
              fam_fl)
        zf = flags_named(fl, zfam)
        check("zeros bundle, %s: one FAIL '%s' naming expno 11 (3 of 4 rows), "
              "quoting the acqu2s status TD=1" % (tag, zfam),
              len(zf) == 1 and zf[0]["level"] == "FAIL"
              and zf[0]["detail"].startswith("expno 11 (reference_open): 3 of "
                                             "4 row(s)")
              and "acqu2s says TD=1" in zf[0]["detail"], zf)
        ex = [f for f in fl if f["check"].startswith("raw data expno")]
        check("zeros bundle, %s: expnos 12 and 13 are EXCLUDED FAILs "
              "(raw data expno ...)" % tag,
              sorted(f["check"] for f in ex) == ["raw data expno 12 (noise)",
                                                  "raw data expno 13 "
                                                  "(reference_close)"]
              and all(f["level"] == "FAIL" and "only zeros" in f["detail"]
                      for f in ex), ex)
    # the report's pulse-program timing model resolves d11 (any dN) from
    # the acqus D array: the shipped sequences' row is d1 + [p1] + go + d11
    for name, want_row in (("zgnoise2d", [("d", 1), ("go",), ("d", 11)]),
                           ("zgref2d", [("d", 1), ("p", 1), ("go",),
                                        ("d", 11)])):
        text = open(os.path.join(REPO, "topspin", "pp", name), "rb").read()
        pre, row, why = fr.pp_timing_model(text.decode("ascii"), 8)
        check("pp_timing_model(%s): row terms %r (the 'd11 wr' line is one "
              "D[11] delay), no error" % (name, want_row),
              why is None and row == want_row, (pre, row, why))
    text = open(os.path.join(REPO, "topspin", "pp", "zgref2d"), "rb").read()
    pre, row, why = fr.pp_timing_model(text.decode("ascii"), 8)
    check("pp_timing_model(zgref2d): the 30m before the loop is the one "
          "pre-loop term", pre == [("lit", 0.03)], pre)


def zero_noise_ser(src, dst):
    """Copy the bundle with expno 12's ser rows 2..N zeroed (Oulu's refused
    shape: first row acquired, the rest never came), checksum updated."""
    zin = zipfile.ZipFile(src, "r")
    meta = json.loads(zin.read("meta.json").decode("utf-8"))
    e12 = [e for e in meta["experiments"] if e["expno"] == 12][0]
    rows = int(e12["td1_rows"])
    row_bytes = int(e12["td"]) * 4
    padded = ((row_bytes + 1023) // 1024) * 1024
    ser = zin.read("data/12/ser")
    ser = ser[:padded] + b"\x00" * (len(ser) - padded)
    meta["checksums"]["data/12/ser"] = "sha256:" + hashlib.sha256(ser).hexdigest()
    zout = zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED)
    for info in zin.infolist():
        if info.filename == "meta.json":
            zout.writestr("meta.json", json.dumps(meta, indent=1) + "\n")
        elif info.filename == "data/12/ser":
            zout.writestr("data/12/ser", ser)
        else:
            zout.writestr(info, zin.read(info.filename))
    zout.close()
    zin.close()
    return rows


def rewrite_bundle(src, dst, run_mode=None, zero_rows_of=None):
    """Copy a bundle, optionally with software.run_mode replaced and, per
    expno in zero_rows_of ({expno: rows_to_keep}), the ser's rows after
    the first rows_to_keep zeroed (0 keeps none: a ser of zeros only, the
    fully refused shape; 1 keeps the first row: Oulu's shape).  Checksums
    updated; meta.json is not checksummed."""
    zero_rows_of = zero_rows_of or {}
    zin = zipfile.ZipFile(src, "r")
    meta = json.loads(zin.read("meta.json").decode("utf-8"))
    if run_mode is not None:
        meta["software"]["run_mode"] = run_mode
    payloads = {}
    for expno, keep in zero_rows_of.items():
        e = [x for x in meta["experiments"] if x["expno"] == expno][0]
        row_bytes = int(e["td"]) * 4
        padded = ((row_bytes + 1023) // 1024) * 1024
        arc = "data/%d/ser" % expno
        ser = zin.read(arc)
        cut = padded * int(keep)
        ser = ser[:cut] + b"\x00" * (len(ser) - cut)
        meta["checksums"][arc] = "sha256:" + hashlib.sha256(ser).hexdigest()
        payloads[arc] = ser
    zout = zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED)
    for info in zin.infolist():
        if info.filename == "meta.json":
            zout.writestr("meta.json", json.dumps(meta, indent=1) + "\n")
        elif info.filename in payloads:
            zout.writestr(info.filename, payloads[info.filename])
        else:
            zout.writestr(info, zin.read(info.filename))
    zout.close()
    zin.close()
    return meta


def e2e_software_test(work):
    """A run_mode desktest bundle of Oulu's shape: expno 12's ser one data
    row and zeros, expno 13's ser zeros only.  The report must complete,
    keep science None (the gate) and name both blocks as FAILs in
    report.json raw_data_check -- what the Jython harness asserts on its
    legacy-dru-refused desktest bundle."""
    base = subprocess.check_output(
        [sys.executable, os.path.join(REPO, "testing", "make_physics_bundle.py"),
         "--feature", "none", "--clock-offset", "0", "--out-dir",
         os.path.join(work, "swtest")],
        stderr=subprocess.DEVNULL).decode().strip().splitlines()[-1]
    dst = os.path.join(work, "desktest_" + os.path.basename(base))
    meta = rewrite_bundle(base, dst, run_mode="desktest",
                          zero_rows_of={12: 1, 13: 0})
    rows12 = int([e for e in meta["experiments"] if e["expno"] == 12][0]
                 ["td1_rows"])
    out = os.path.join(work, "report_desktest")
    rc = subprocess.call([sys.executable, REPORT_PY, dst, "--out", out],
                         stdout=subprocess.DEVNULL)
    check("e2e software-test: the report completes on a run_mode desktest "
          "bundle whose noise ser is one data row and zeros and whose "
          "closing-reference ser is zeros only",
          rc == 0 and os.path.isfile(os.path.join(out, "report.json")),
          "rc=%s" % rc)
    if rc != 0:
        return
    with open(os.path.join(out, "report.json"), "r") as fh:
        rep = json.load(fh)
    check("e2e software-test: report_type 'software-test', science None (the "
          "gate holds), raw_data_check present",
          rep.get("report_type") == "software-test"
          and rep.get("science") is None
          and isinstance(rep.get("raw_data_check"), dict),
          (rep.get("report_type"), rep.get("science") is None,
           type(rep.get("raw_data_check"))))
    rcq = (rep.get("raw_data_check") or {}).get("qa_flags") or []
    zf = flags_named(rcq, "raw data all zeros")
    check("e2e software-test: raw_data_check carries the FAIL 'raw data all "
          "zeros' for expno 12 (%d of %d rows)" % (rows12 - 1, rows12),
          len(zf) == 1 and zf[0]["level"] == "FAIL"
          and zf[0]["detail"].startswith(
              "expno 12 (noise): %d of %d row(s)" % (rows12 - 1, rows12)), zf)
    ex = [f for f in rcq if f["check"].startswith("raw data expno")]
    check("e2e software-test: expno 13 is the EXCLUDED FAIL 'raw data expno "
          "13 (reference_close)' with the 'only zeros' reason, and no other "
          "expno is excluded",
          [f["check"] for f in ex] == ["raw data expno 13 (reference_close)"]
          and ex[0]["level"] == "FAIL" and "only zeros" in ex[0]["detail"],
          ex)
    warns = [f for f in flags_named(rcq, "rows declared vs read")
             if f["level"] == "WARN"]
    check("e2e software-test: one WARN 'rows declared vs read' (expno 12, "
          "the zeros wording); the excluded expno 13 is not compared",
          len(warns) == 1 and "expno 12" in warns[0]["detail"]
          and "are all zeros" in warns[0]["detail"], warns)
    rl = ((rep.get("raw_data_check") or {}).get("raw_data_read") or {})
    check("e2e software-test: raw_data_check.raw_data_read says expno 12 "
          "n_rows 1 / n_rows_zero %d and lists expno 13 as refused"
          % (rows12 - 1),
          ((rl.get("by_expno") or {}).get("12") or {}).get("n_rows") == 1
          and ((rl.get("by_expno") or {}).get("12") or {}).get("n_rows_zero")
          == rows12 - 1 and "13" in (rl.get("refused") or {}),
          (rl.get("by_expno", {}).get("12"), sorted(rl.get("refused", {}))))
    with open(os.path.join(out, "report.html"), "r", encoding="utf-8") as fh:
        html = fh.read()
    check("e2e software-test: report.html carries the SOFTWARE-TEST banner "
          "and the raw-data content card naming expno 12",
          "SOFTWARE-TEST REPORT" in html
          and "Raw-data content (plumbing check)" in html
          and "expno 12 (noise)" in html, len(html))


def e2e_zeros(work):
    base = subprocess.check_output(
        [sys.executable, os.path.join(REPO, "testing", "make_physics_bundle.py"),
         "--feature", "none", "--clock-offset", "0", "--out-dir", work],
        stderr=subprocess.DEVNULL).decode().strip().splitlines()[-1]
    zeroed = os.path.join(work, "zeros_" + os.path.basename(base))
    rows = zero_noise_ser(base, zeroed)
    out = os.path.join(work, "report_zeros")
    rc = subprocess.call([sys.executable, REPORT_PY, zeroed, "--out", out],
                         stdout=subprocess.DEVNULL)
    check("e2e zeros: the report completes on a bundle whose noise ser is "
          "one data row and %d zero rows (Oulu's shape)" % (rows - 1),
          rc == 0 and os.path.isfile(os.path.join(out, "report.json")),
          "rc=%s" % rc)
    if rc != 0:
        return
    with open(os.path.join(out, "report.json"), "r") as fh:
        rep = json.load(fh)
    qa = (rep.get("science") or {}).get("qa_flags") or []
    zf = flags_named(qa, "raw data all zeros")
    check("e2e zeros: report.json carries the FAIL 'raw data all zeros' for "
          "expno 12 (%d of %d rows)" % (rows - 1, rows),
          len(zf) == 1 and zf[0]["level"] == "FAIL"
          and zf[0]["detail"].startswith(
              "expno 12 (noise): %d of %d row(s)" % (rows - 1, rows)), zf)
    rl = ((rep.get("science") or {}).get("raw_data_read") or {}).get(
        "by_expno") or {}
    check("e2e zeros: the raw-data read log says expno 12 has n_rows 1 and "
          "n_rows_zero %d" % (rows - 1),
          (rl.get("12") or {}).get("n_rows") == 1
          and (rl.get("12") or {}).get("n_rows_zero") == rows - 1,
          rl.get("12"))
    warns = [f for f in flags_named(qa, "rows declared vs read")
             if f["level"] == "WARN"]
    check("e2e zeros: one WARN 'rows declared vs read' for expno 12 with the "
          "zeros wording", len(warns) == 1
          and "expno 12" in warns[0]["detail"]
          and "are all zeros" in warns[0]["detail"], warns)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--meta-074", default=META_074_DEFAULT,
                    help="a v0.7.4 meta.json to use as the 'no 0.7.5 keys' "
                         "fixture, read-only (e.g. Anne's Oulu desktest "
                         "meta, which lives outside the repository); by "
                         "default the built-in copy of its param_api block "
                         "is used and no file outside the checkout is read")
    ap.add_argument("--skip-e2e", action="store_true")
    args = ap.parse_args(argv)
    work = args.out_dir or tempfile.mkdtemp(prefix="report_qa_flags_")
    if not os.path.isdir(work):
        os.makedirs(work)

    try:
        fr = load_report_module()
    except ImportError as exc:
        print("FAIL: cannot import analysis/facility_report.py (%s); the "
              "report needs numpy" % exc)
        return 1

    print("--- 1. param_api lines ---")
    param_api_cases(fr, work, args.meta_074)
    print("--- 4. raw data all zeros (Bruker reader + qa_flags + pp model) ---")
    zero_rows_cases(fr, work)
    if not args.skip_e2e:
        print("--- 4b. raw data all zeros, end to end ---")
        e2e_zeros(work)
        print("--- 4c. raw data all zeros, software-test report ---")
        e2e_software_test(work)
    print("--- 2. rows declared vs read (Bruker reader + qa_flags) ---")
    rows_cases(fr, work)
    if not args.skip_e2e:
        print("--- 3. end to end (physics bundle, truncated ser) ---")
        e2e(work)

    print("")
    if FAILED:
        print("REPORT QA FLAGS: %d FAILED" % len(FAILED))
        for n in FAILED:
            print("  - %s" % n)
        return 1
    print("REPORT QA FLAGS: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
