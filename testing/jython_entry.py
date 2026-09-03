# -*- coding: utf-8 -*-
# ============================================================================
# testing/jython_entry.py -- run spin_noise_run.py UNMODIFIED under real
# Jython 2.7 with a stubbed TopSpin API (see testing/topspin_stub.py).
# ============================================================================
#
# Usage (normally via testing/run_jython_harness.sh):
#
#   jython -Dpython.path=<repo>/testing testing/jython_entry.py simulate
#   jython -Dpython.path=<repo>/testing testing/jython_entry.py desktest
#
# What it does:
#   1. builds a throwaway TopSpin-like world in $HARNESS_WORKDIR (or a
#      fresh temp dir): a template 1H dataset (acqus, fid, uxnmr.info,
#      pdata/...) and a fake <TSHOME>/exp/stan/nmr/lists/pp tree;
#   2. registers testing/topspin_stub.py as the module "TopCmds" and also
#      injects its API into __builtin__, then execfile()'s the REAL
#      topspin/spin_noise_run.py with sys.argv = ["spin_noise_run", MODE]
#      -- exactly how `xpy spin_noise_run simulate` hands the mode over;
#   3. after the run, verifies: no unscripted dialogs, no hardware-guard
#      breaches, no ERRMSG/abort, the full expno tree, meta.json (twice,
#      with run_mode == MODE and a real sha256 self-fingerprint), the
#      installed pulse program, a java-zip-readable bundle, and the
#      schema-1.2 clock_audit object (8 blocks whose wall durations track
#      the stub's virtual clock, which carries a deliberate 3e-7 injected
#      fractional offset for the offline fit to recover -- see
#      topspin_stub.INJECTED_CLOCK_OFFSET and run_jython_harness.sh).
#
# The template parameter values are plausible reals taken from the 2020
# archival 600 MHz cryoprobe dataset that motivated this project
# (SFO1 600.1337058 MHz, O1 +3705.8 Hz, SW 12019.23 Hz, RG 184.37).
#
# Exit code 0 = harness PASS.  Prints "BUNDLE: <path>" for the wrapper.
# ============================================================================

import os
import re
import sys
import time
import traceback

TESTING_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTING_DIR)
SCRIPT = os.path.join(REPO, "topspin", "spin_noise_run.py")

if TESTING_DIR not in sys.path:
    sys.path.insert(0, TESTING_DIR)

import topspin_stub


def build_world(workdir):
    """Create the template dataset and a fake TSHOME pp tree."""
    datadir = os.path.join(workdir, "nmrdata")
    template_dir = os.path.join(datadir, "WATERTEST", "1")
    pdata_dir = os.path.join(template_dir, "pdata", "1")
    os.makedirs(pdata_dir)

    f = open(os.path.join(template_dir, "acqus"), "w")
    f.write("##TITLE= Parameter file, TopSpin 4.1.4\n"
            "##JCAMPDX= 5.0\n"
            "##DATATYPE= Parameter Values\n"
            "##ORIGIN= Bruker BioSpin GmbH\n"
            "##$BF1= 600.13\n"
            "##$SFO1= 600.1337058\n"
            "##$SW_h= 12019.2307692308\n"
            "##$TD= 65536\n"
            "##$RG= 184.37\n"
            "##$NS= 1\n"
            "##$PULPROG= <zg30>\n"
            "##END=\n")
    f.close()

    # uxnmr.info: exercises parse_console() (looks for avance/cabinet).
    f = open(os.path.join(template_dir, "uxnmr.info"), "w")
    f.write("CONFIGURATION INFORMATION\n"
            "=========================\n"
            "Description : CABINET 600 HD X\n"
            "This system is an AVANCE III HD 600 console.\n")
    f.close()

    # A small binary fid: proves binary-safe copy + java SHA-256 + zip.
    f = open(os.path.join(template_dir, "fid"), "wb")
    blob = []
    i = 0
    while i < 4096:
        blob.append(chr((i * 37 + 11) % 256))
        i = i + 1
    f.write("".join(blob))
    f.close()

    f = open(os.path.join(pdata_dir, "procs"), "w")
    f.write("##TITLE= Parameter file, TopSpin 4.1.4\n##$SI= 65536\n##END=\n")
    f.close()
    f = open(os.path.join(pdata_dir, "title"), "w")
    f.write("1H template for the Jython harness\n")
    f.close()

    # Fake TSHOME: find_pp_user_dir() requires .../lists/pp to exist and
    # creates .../pp/user itself; TOPSPIN_HOME is one of its env probes.
    tshome = os.path.join(workdir, "tshome")
    os.makedirs(os.path.join(tshome, "exp", "stan", "nmr", "lists", "pp"))
    os.environ["TOPSPIN_HOME"] = tshome

    return datadir, tshome


# Template parameters: plausible values from the 2020 600 MHz cryoprobe
# dataset (see module docstring).  Unicode on purpose -- real TopSpin
# GETPAR returns java.lang.String, which Jython coerces to unicode.
TEMPLATE_PARAMS = {
    "TE": u"298.0",
    "P 1": u"8.5",
    "PLdB 1": u"-11.79",
    "PL 1": u"",
    "RG": u"184.37",
    "PROBHD": u"5 mm CryoProbe Prodigy BBO BB-H&F/D Z-GRD",
    "BF1": u"600.13",
    "SFO1": u"600.1337058",
    "TD": u"65536",
    "SWH": u"12019.23",
    "O1": u"3705.8",
    "NS": u"1",
    "DS": u"0",
    "D 1": u"2.0",
    "PULPROG": u"<zg30>",
    "PARMODE": u"0",
}

# Scripted operator answers.  The non-ASCII city (Testköping) and the
# em-dash in the notes are DELIBERATE: real operators type non-ASCII, and
# Jython dialogs return unicode -- this is the input class plain-str desk
# stubs cannot exercise.
DIALOG_ANSWERS = {
    "spin-noise network 1/5: your facility":
        [u"Harness Test Facility", u"Testköping", u"Testland",
         u"harness@example.org"],
    "spin-noise network 2/5: facility slug": [u"harness-lab"],
    "spin-noise network 3/5: the sample":
        [u"distilled water", u"100", u"0", u"none", u"5", u"550"],
    "spin-noise network 4/5: temperature": [u"298"],
    "spin-noise run: hardware check":
        [u"4.1.4", u"AVANCE III HD",
         u"5 mm CryoProbe Prodigy BBO BB-H&F/D Z-GRD"],
    "spin-noise run: probe temperatures (optional)": [u"80", u"298"],
    "spin-noise run: 90-degree pulse": [u"8.5", u"-11.79"],
    # Must NOT fire (harness answers 298 K, in range); scripted so a
    # regression shows as a wrong outcome, not a stuck harness.
    "spin-noise network 4/5: temperature (please check)": [u"298"],
    "spin-noise run: notes":
        [u"harness run — synthetic operator input"],
    # Fallback dialogs that must NOT fire in a clean run; scripted anyway
    # so a failure shows up as a wrong outcome, not a stuck harness.
    "spin_noise_run: pulse program directory": [u""],
    "spin_noise_run: receiver gain": [u"101"],
    # Optional-feature dialogs (rdopt / sweep variant): 3 tuning offsets
    # and a 3-step +/-1200 Hz sweep keep that variant fast.
    "spin-noise rd-optimize: offsets": [u"0, -60, 60"],
    # Must NOT fire in mock modes (chosen offset is always 0 there);
    # scripted so a regression shows as a wrong outcome, not a hang.
    "spin-noise rd-optimize: P90 at chosen tuning": [u"8.5"],
    "spin-noise sweep: plan": [u"3", u"1200"],
}

SELECT_ANSWERS = {
    "spin-noise network": 0,                            # greeting: Start
    "spin-noise network: contact consent": 0,           # yes
    # 60 min, not the 30-min default: the clock audit needs a session
    # span over 1 h to escape the report's 'inconclusive (short session)'
    # flag, and the harness clock is virtual so this costs nothing.
    "spin-noise network 5/5: noise-block duration": 1,
    "spin-noise run: lock": 0,                          # lock OFF
    # Sweep-off is now the SECOND button (safe default flipped after
    # the 2022-incident review): index 1 = "Yes -- I checked just now".
    "spin-noise run: BSMS FIELD SWEEP -- IMPORTANT": 1, # sweep OFF
    "spin-noise run: probe type": 1,                    # N2-cryo (Prodigy)
    # Field-sweep operator steps (SELECT dialogs; 0 = proceed). The
    # off-target adjudication never fires in mock modes (no measured
    # shift exists) -- scripted so a regression surfaces as a wrong
    # outcome instead of an unscripted dialog.
    "spin-noise sweep: baseline": 0,
    "spin-noise sweep: set field step": 0,
    "spin-noise sweep: restore field": 0,
    "spin-noise sweep: off target": 0,
}

CONFIRM_ANSWERS = {
    # Failure-path fallbacks that must NOT fire in a clean run.
    "spin_noise_run: make dataset 1D": 1,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("simulate", "desktest"):
        print "usage: jython jython_entry.py {simulate|desktest} " \
              "[rdopt] [sweep]"
        return 2
    mode = sys.argv[1]
    features = []
    for a in sys.argv[2:]:
        if a in ("rdopt", "sweep", "autostep"):
            features.append(a)
    rdopt_on = "rdopt" in features
    sweep_on = "sweep" in features
    autostep_on = "autostep" in features

    workdir = os.environ.get("HARNESS_WORKDIR")
    if not workdir:
        import tempfile
        workdir = tempfile.mkdtemp(prefix="spin_noise_harness_")
    if not os.path.isdir(workdir):
        os.makedirs(workdir)

    print "harness: mode=%s features=%s workdir=%s" \
        % (mode, ",".join(features) or "none", workdir)
    datadir, tshome = build_world(workdir)

    template = [u"WATERTEST", u"1", u"1", datadir.decode("utf-8")]
    topspin_stub.configure(template, TEMPLATE_PARAMS,
                           DIALOG_ANSWERS, SELECT_ANSWERS,
                           CONFIRM_ANSWERS)

    # Register the stub as TopCmds (so `from TopCmds import *` succeeds
    # and IN_TOPSPIN=1 -> real java zip/digest paths) AND inject the API
    # into __builtin__, mirroring how TopSpin pre-loads its commands.
    sys.modules["TopCmds"] = topspin_stub
    import __builtin__
    for name in topspin_stub.__all__:
        setattr(__builtin__, name, getattr(topspin_stub, name))

    # Run the real script, unmodified, the way xpy would.
    sys.argv = ["spin_noise_run", mode] + features
    script_globals = {"__name__": "__main__", "__file__": SCRIPT}
    run_error = None
    try:
        execfile(SCRIPT, script_globals)
    except SystemExit:
        pass          # EXIT() outside main()'s own catcher; treated below
    except Exception:
        run_error = traceback.format_exc()

    # ------------------------------------------------------------ checks
    failures = []

    def check(name, ok, detail=""):
        tag = "PASS"
        if not ok:
            tag = "FAIL"
            failures.append(name)
        line = "%s : %s" % (tag, name)
        if detail and not ok:
            line = line + "\n       %s" % detail
        print line

    check("script ran to completion without an uncaught exception",
          run_error is None, run_error or "")
    check("no hardware-guard breaches (XCMD/ZG never reached)",
          not topspin_stub.BREACHES, "; ".join(topspin_stub.BREACHES))
    unscripted = ["%s [%s]" % (a, t) for a, t in topspin_stub.UNSCRIPTED]
    check("no unscripted dialogs (every dialog had a fixture answer)",
          not unscripted, "; ".join(unscripted))
    errm = ["%s" % (t,) for t, m in topspin_stub.ERRMSGS]
    check("no ERRMSG (no crash dialog)", not errm, "; ".join(errm))
    aborts = [t for t, m in topspin_stub.MSGS
              if t is not None and "cancelled" in t]
    check("no abort/cancel MSG", not aborts, "; ".join(aborts))
    completes = [t for t, m in topspin_stub.MSGS
                 if t is not None and "complete" in t]
    check("final 'complete' MSG shown", len(completes) == 1)

    # Expected session shape (execution order), parameterized by the
    # optional features: rdopt adds 3 scan 1Ds (fixture offsets 0/-60/60,
    # expnos 20..22) after setup; sweep replaces the single noise block
    # with baseline verify + 3 x (verify + noise) + restore verify
    # (expnos 30..34 and 50..52).
    expected_expnos = [1]
    expected_roles = ["setup"]
    if rdopt_on:
        expected_expnos += [20, 21, 22]
        expected_roles += ["rdopt_scan"] * 3
    expected_expnos += [10, 14, 15, 16, 11]
    expected_roles += ["rg_ladder"] * 4 + ["reference_open"]
    if sweep_on:
        # v0.6: baseline verify (30), then the carrier-displacement
        # sign-calibration 1D (29), then 3 x (verify + noise), then the
        # restore verify.
        expected_expnos += [30, 29, 31, 50, 32, 51, 33, 52, 34]
        expected_roles += ["sweep_verify", "sweep_signcal"]
        for _k in range(3):
            expected_roles += ["sweep_verify", "noise_sweep"]
        expected_roles += ["sweep_verify"]
    else:
        expected_expnos += [12]
        expected_roles += ["noise"]
    expected_expnos += [13]
    expected_roles += ["reference_close"]

    # dsname now carries date AND time (same-day rerun protection), so
    # discover it instead of recomputing the exact minute.
    cand = []
    if os.path.isdir(datadir):
        for nd in os.listdir(datadir):
            if nd.startswith("SPINNOISE_") and \
                    os.path.isdir(os.path.join(datadir, nd)):
                cand.append(nd)
    check("exactly one SPINNOISE_* dataset created", len(cand) == 1,
          str(cand))
    dsname = cand[0] if cand else "SPINNOISE_MISSING"
    name_dir = os.path.join(datadir, dsname)
    for expno in expected_expnos:
        d = os.path.join(name_dir, str(expno))
        check("expno %d dataset dir with acqus" % expno,
              os.path.isfile(os.path.join(d, "acqus")), d)

    pp_path = os.path.join(tshome, "exp", "stan", "nmr", "lists", "pp",
                           "user", "zgnoise2d")
    pp_ok = False
    if os.path.isfile(pp_path):
        f = open(pp_path, "r")
        pp_ok = ";zgnoise2d" in f.read()
        f.close()
    check("pulse program installed into fake TSHOME pp/user", pp_ok, pp_path)

    meta_ds = os.path.join(name_dir, "meta.json")
    meta_stage = os.path.join(name_dir, "bundle_stage", "meta.json")
    check("meta.json written in dataset dir", os.path.isfile(meta_ds))
    check("meta.json written in bundle staging dir",
          os.path.isfile(meta_stage))

    meta_text = ""
    if os.path.isfile(meta_ds):
        f = open(meta_ds, "r")
        meta_text = f.read()
        f.close()
    check("meta.json run_mode == '%s' (bundle cannot pass as data)" % mode,
          '"run_mode": "%s"' % mode in meta_text)
    check("meta.json schema_version == '1.2'",
          '"schema_version": "1.2"' in meta_text)
    check("meta.json script_sha256 is a real java-computed digest",
          re.search(r'"script_sha256": "sha256:[0-9a-f]{64}"',
                    meta_text) is not None)
    check("meta.json carries the non-ASCII city, json-escaped",
          '"city": "Testk\\u00f6ping"' in meta_text)
    check("meta.json carries the non-ASCII operator note, json-escaped",
          'harness run \\u2014 synthetic operator input' in meta_text)
    check("meta.json facility_slug from dialog answer",
          '"facility_slug": "harness-lab"' in meta_text)

    # ---- clock audit (schema 1.2).  Jython 2.7 ships json, so the
    # harness can parse what the script's hand-rolled writer emitted.
    ca = None
    try:
        import json
        ca = json.loads(meta_text).get("clock_audit")
    except Exception:
        ca = None
    check("meta.json carries a clock_audit object", isinstance(ca, dict))
    blocks = []
    if isinstance(ca, dict):
        blocks = ca.get("blocks", [])
        check("clock_audit records the NTP status probe (raw string)",
              isinstance(ca.get("ntp_status_raw"), basestring)
              and len(ca.get("ntp_status_raw")) > 0)
        check("clock_audit names the workstation time source",
              isinstance(ca.get("workstation_time_source"), basestring))
    check("clock_audit has %d blocks for this variant"
          % len(expected_roles),
          len(blocks) == len(expected_roles), "found %d" % len(blocks))
    roles = [b.get("role") for b in blocks]
    check("clock_audit block roles cover the session in order",
          roles == expected_roles, str(roles))

    # Feature meta objects (rdopt/sweep variant only).
    if rdopt_on or sweep_on:
        mo = {}
        try:
            import json as _json
            mo = _json.loads(meta_text)
        except Exception:
            mo = {}
        if rdopt_on:
            ro = (mo.get("calibration") or {}).get("rd_optimize") or {}
            check("meta.calibration.rd_optimize present with 3 scan "
                  "expnos and a mocked note",
                  ro.get("enabled") is True
                  and ro.get("scan_expnos") == [20, 21, 22]
                  and ro.get("chosen_offset_khz") == 0.0
                  and "mocked" in (ro.get("note") or ""), str(ro))
        if sweep_on:
            fs = mo.get("field_sweep") or {}
            steps = fs.get("steps") or []
            check("meta.field_sweep present with 3 unskipped steps and "
                  "the expected expnos",
                  fs.get("enabled") is True and len(steps) == 3
                  and [s.get("noise_expno") for s in steps] == [50, 51, 52]
                  and [s.get("verify_expno") for s in steps] == [31, 32, 33]
                  and not [s for s in steps if s.get("skipped")],
                  str(fs)[:400])
            check("meta.field_sweep targets span the requested +/-1200 Hz",
                  len(steps) == 3
                  and abs(steps[0].get("target_offset_hz", 0) + 1200.0) < 1
                  and abs(steps[1].get("target_offset_hz", 1)) < 1
                  and abs(steps[2].get("target_offset_hz", 0) - 1200.0) < 1,
                  str([s.get("target_offset_hz") for s in steps]))
            if autostep_on:
                # In mock modes autostep must bail out BEFORE any dialog
                # or file access, record why, and leave every step on the
                # operator-dialog basis.
                asx = fs.get("autostep") or {}
                check("autostep: graceful mock-mode fallback recorded",
                      asx.get("requested") is True
                      and asx.get("available") is False
                      and "mock mode" in (asx.get("fallback_reason") or ""),
                      str(asx)[:200])
                check("autostep: steps fell back to operator_dialog basis",
                      bool(steps) and not [
                          s for s in steps
                          if s.get("actuation_basis") != "operator_dialog"],
                      str([s.get("actuation_basis") for s in steps]))
    setup_ok = bool(blocks) and blocks[0].get("ocxo_expected_s") is None
    check("setup block has ocxo_expected_s null (not OCXO-predictable)",
          setup_ok)
    consistent = bool(blocks)
    detail = []
    for b in blocks:
        try:
            wall_s = (b["wall_end_ms"] - b["wall_start_ms"]) / 1000.0
        except Exception:
            consistent = False
            detail.append("block %s: bad wall times" % b.get("expno"))
            continue
        if wall_s < 0:
            consistent = False
            detail.append("block %s: negative duration" % b.get("expno"))
        exp = b.get("ocxo_expected_s")
        # Blocks with a meaningful OCXO prediction must show a wall
        # duration consistent with it (stub advances the virtual clock by
        # expected*(1 + 3e-7) + ~0.2 s overhead), i.e. within 1 percent.
        if exp is not None and exp > 10.0:
            if abs(wall_s / exp - 1.0) > 0.01:
                consistent = False
                detail.append("block %s: wall %.3f s vs ocxo %.3f s"
                              % (b.get("expno"), wall_s, exp))
    check("clock_audit wall durations consistent with OCXO predictions "
          "(virtual clock, injected offset %.1e)"
          % topspin_stub.INJECTED_CLOCK_OFFSET,
          consistent, "; ".join(detail))
    # For the wrapper: the recovery check (facility_report must refit the
    # injected offset within its stated uncertainty) runs in python3.
    print "INJECTED_CLOCK_OFFSET: %.6e" % topspin_stub.INJECTED_CLOCK_OFFSET

    bundles = []
    if os.path.isdir(name_dir):
        for n in os.listdir(name_dir):
            if n.startswith("spinnoise_") and n.endswith(".zip"):
                bundles.append(os.path.join(name_dir, n))
    check("exactly one bundle zip produced", len(bundles) == 1,
          "found: %s" % bundles)

    bundle = None
    if len(bundles) == 1:
        bundle = bundles[0]
        bn = os.path.basename(bundle)
        check("bundle filename follows the upload convention",
              re.match(r"^spinnoise_harness-lab_[0-9]{8}_[0-9]{6}Z_"
                       r"[0-9a-f]{4}\.zip$", bn) is not None, bn)
        # Read the zip back with java (same class family that wrote it).
        try:
            from java.util.zip import ZipFile
            zf = ZipFile(bundle)
            names = []
            en = zf.entries()
            while en.hasMoreElements():
                names.append(en.nextElement().getName())
            zf.close()
            data_entries = [n for n in names if n.startswith("data/")]
            check("bundle zip readable by java.util.zip.ZipFile",
                  True)
            check("bundle has meta.json at zip root", "meta.json" in names)
            check("bundle has data/<expno>/ files (%d found)"
                  % len(data_entries), len(data_entries) >= 8)
        except Exception:
            check("bundle zip readable by java.util.zip.ZipFile", False,
                  traceback.format_exc())

    print ""
    if bundle:
        print "BUNDLE: %s" % bundle
    if failures:
        print "HARNESS %s: FAIL (%d check(s) failed)" % (mode, len(failures))
        return 1
    print "HARNESS %s: PASS" % mode
    return 0


sys.exit(main())
