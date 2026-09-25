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
            "zgnoise2d" if role == "noise" else "zg2d",
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
