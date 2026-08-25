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
#      installed pulse program, and a java-zip-readable bundle.
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
    "spin-noise run: notes":
        [u"harness run — synthetic operator input"],
    # Fallback dialogs that must NOT fire in a clean run; scripted anyway
    # so a failure shows up as a wrong outcome, not a stuck harness.
    "spin_noise_run: pulse program directory": [u""],
    "spin_noise_run: receiver gain": [u"101"],
}

SELECT_ANSWERS = {
    "spin-noise network": 0,                            # greeting: Start
    "spin-noise network: contact consent": 0,           # yes
    "spin-noise network 5/5: noise-block duration": 0,  # 30 min
    "spin-noise run: lock": 0,                          # lock OFF
    "spin-noise run: BSMS FIELD SWEEP -- IMPORTANT": 0, # sweep OFF
    "spin-noise run: probe type": 1,                    # N2-cryo (Prodigy)
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("simulate", "desktest"):
        print "usage: jython jython_entry.py {simulate|desktest}"
        return 2
    mode = sys.argv[1]

    workdir = os.environ.get("HARNESS_WORKDIR")
    if not workdir:
        import tempfile
        workdir = tempfile.mkdtemp(prefix="spin_noise_harness_")
    if not os.path.isdir(workdir):
        os.makedirs(workdir)

    print "harness: mode=%s workdir=%s" % (mode, workdir)
    datadir, tshome = build_world(workdir)

    template = [u"WATERTEST", u"1", u"1", datadir.decode("utf-8")]
    topspin_stub.configure(template, TEMPLATE_PARAMS,
                           DIALOG_ANSWERS, SELECT_ANSWERS)

    # Register the stub as TopCmds (so `from TopCmds import *` succeeds
    # and IN_TOPSPIN=1 -> real java zip/digest paths) AND inject the API
    # into __builtin__, mirroring how TopSpin pre-loads its commands.
    sys.modules["TopCmds"] = topspin_stub
    import __builtin__
    for name in topspin_stub.__all__:
        setattr(__builtin__, name, getattr(topspin_stub, name))

    # Run the real script, unmodified, the way xpy would.
    sys.argv = ["spin_noise_run", mode]
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

    dsname = "SPINNOISE_" + time.strftime("%Y%m%d", time.localtime())
    name_dir = os.path.join(datadir, dsname)
    for expno in (1, 10, 14, 15, 16, 11, 12, 13):
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
    check("meta.json schema_version == '1.1'",
          '"schema_version": "1.1"' in meta_text)
    check("meta.json script_sha256 is a real java-computed digest",
          re.search(r'"script_sha256": "sha256:[0-9a-f]{64}"',
                    meta_text) is not None)
    check("meta.json carries the non-ASCII city, json-escaped",
          '"city": "Testk\\u00f6ping"' in meta_text)
    check("meta.json carries the non-ASCII operator note, json-escaped",
          'harness run \\u2014 synthetic operator input' in meta_text)
    check("meta.json facility_slug from dialog answer",
          '"facility_slug": "harness-lab"' in meta_text)

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
