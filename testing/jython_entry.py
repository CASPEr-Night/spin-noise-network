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
#   3. after the run, verifies: no unscripted dialogs, no stray console
#      dialogs or silently lost parameter writes (the stub models both,
#      per console flavor), no hardware-guard
#      breaches, no ERRMSG/abort, the full expno tree, meta.json (twice,
#      with run_mode == MODE and a real sha256 self-fingerprint), the
#      installed pulse program, a java-zip-readable bundle, and the
#      schema-1.2 clock_audit object (9 blocks whose wall durations track
#      the stub's virtual clock, which carries a deliberate 3e-7 injected
#      fractional offset for the offline fit to recover -- see
#      topspin_stub.INJECTED_CLOCK_OFFSET and run_jython_harness.sh);
#      since v0.7.7 also the row probe (expno 17), the raw-data files the
#      mocked acquisitions leave, the data-content check of every
#      pseudo-2D block and, under the legacy-dru flavors, the script's
#      handling of a receiver unit that refuses blocks; since v0.7.8 the
#      acquisition-mode writes per flavor (DIGMOD once per session, no
#      DSPFIRM write on a coupling console, the name ladder on
#      legacy-nocouple) and zero GetEnuOrd[DSPFIRM] dialogs; since v0.8.2
#      the nutation curve (expnos 80..89, role nutation, section 8c of the
#      script): the 1.0 x P90 step first with rga on it alone, every step
#      at the calibrated power with its physics FID from the stub, the
#      amplitudes fitted back to the stub's P90_true (3% off the declared
#      value), the RG-1 fallback under legacy-rgafail and the recorded
#      skip under the nonutation flag.
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
import json as _jsonmod   # a later function-local `import json` would shadow the plain name
import __builtin__
import java.lang as _jl
CATCHABLE = (__builtin__.Exception, _jl.Exception)   # harness runs under plain Jython
# What the execfile() catcher turns into a FAIL line: any Python exception
# or ANY Java throwable -- Throwable, not Exception, so a java.lang.Error
# (StackOverflowError, OutOfMemoryError) escaping the script still yields
# the readable FAIL instead of killing the harness with a raw Java trace.
SCRIPT_ESCAPES = (__builtin__.Exception, _jl.Throwable)

TESTING_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTING_DIR)
SCRIPT = os.path.join(REPO, "topspin", "spin_noise_run.py")

if TESTING_DIR not in sys.path:
    sys.path.insert(0, TESTING_DIR)

import topspin_stub

# Console flavors the stub models (see topspin_stub.py, FLAVOR).
FLAVORS = ("legacy", "ts44", "ts44-stale", "ts44-strict", "ts44-f1echo",
           "ts44-dimlie", "ts44-f1route", "ts44-f1mismatch",
           "legacy-2dtemplate", "legacy-noacqu2",
           "legacy-dru", "legacy-dru-refused", "legacy-nocouple",
           "legacy-rgafail")
DRU_FLAVORS = ("legacy-dru", "legacy-dru-refused")
# The nutation fixture (v0.8.2): the fixture answers 8.5 us in the
# 90-degree dialog (DIALOG_ANSWERS below), the stub's probe is 3% longer.
# The script scales its steps from the declared value; the FIDs follow
# the truth; the harness fits them back and must see the difference.
NUTATION_P90_TRUE_US = 8.5 * 1.03
NUTATION_EXPNOS = range(80, 90)
NUTATION_MULTS = [1.0, 0.25, 0.5, 0.75, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
# The dense receiver-gain ladder (v0.8.2 addendum), re-derived here
# independently of the script: max_rg is the mocked rga's 101, so the
# levels are 2^(k/2) rounded to 0.01 up to 90.51, plus the reference gain
# 25.25 (= 101/4) and the noise gain 101 as levels of their own -- 16
# levels x 2 visits = 32 rungs in expnos 300..331, visit 1 in the seeded
# permutation (seed = the session date YYYYMMDD; _lcg_permutation below
# is the documented algorithm), visit 2 in its reverse.
DENSE_LEVELS = [1.0, 1.41, 2.0, 2.83, 4.0, 5.66, 8.0, 11.31, 16.0, 22.63,
                25.25, 32.0, 45.25, 64.0, 90.51, 101.0]
DENSE_EXPNOS = range(300, 300 + 2 * len(DENSE_LEVELS))


def _lcg_permutation(n, seed):
    """Fisher-Yates over range(n) with v = v*1103515245 + 12345 mod 2^31,
    j = (v >> 8) % (i + 1), from v = seed & 0x7fffffff -- the script's
    seeded_permutation, written out again so a change there is seen."""
    order = list(range(n))
    v = long(seed) & 0x7fffffff
    i = n - 1
    while i > 0:
        v = (v * 1103515245 + 12345) & 0x7fffffff
        j = int((v >> 8) % (i + 1))
        order[i], order[j] = order[j], order[i]
        i = i - 1
    return order
# legacy-rgafail (v0.8.2): legacy-noacqu2 on which rga fails on the
# nutation block's full-power 90-degree step (and nowhere else): the
# curve is acquired at the RG-1 fallback, recorded, no dialog; every
# other expectation is legacy-noacqu2's.
# legacy-dru / legacy-dru-refused (v0.7.7): legacy-noacqu2 on an Avance III
# HD whose AQS DRU refuses a pseudo-2D block (Oulu, 2026-09-30: "too much
# data for the LAN capacity"; first row acquired, the rest zeros, full-size
# ser).  legacy-dru refuses 1 MB rows written in 1.0 s and accepts them
# in 3.0 s, so the row probe must pass at its SECOND attempt and the
# blocks must be acquired at (262144, 3.0); legacy-dru-refused refuses
# every pseudo-2D block: the probe fails five times, every block is
# recorded as not acquired, the noise block is retried once, only
# WARNINGs are said and a bundle is still produced.  No dialog in either.
# legacy-2dtemplate: the operator's template dataset is 2D (Torino's first
# desktest, 2026-09-21) -- the setup expno and the ladder rungs must be
# switched to 1D by the script, the probe to 2D; no dialogs anywhere.
# legacy-noacqu2: the name under which TopSpin 3.7.0 as seen at Oulu
# (2026-09-25) was first modelled -- PARMODE 2D accepted but the console
# never creates the F1 parameter file acqu2; until it exists every
# parameter write is silently lost and the console pops its own dialog.
# Torino's first live run (TopSpin 4.4.0, 2026-09-22) showed the same on
# 4.4.0, so since then EVERY flavor models it (see topspin_stub.py) and
# legacy-noacqu2 differs from legacy only by having GETACQUDIM.  On every
# 1D-template flavor the script must create acqu2 itself (from the
# console's own parameter library, exp/stan/nmr/par) at the attended
# probe, BEFORE any F1 access, and later pseudo-2D datasets inherit the
# file via WR(); no dialog anywhere, every write verified.
# legacy-nocouple (v0.7.8): legacy-noacqu2 on a 3.x console that does NOT
# move DSPFIRM to sharp when DIGMOD is set to digital (Oulu's 3.7.0 did,
# 2026-10-02; this is the hypothetical the DSPFIRM name ladder exists
# for).  Every other expectation is legacy-noacqu2's; the acquisition-mode
# check wants the ladder's first name, "sharp(standard)", written once
# and accepted, "sharp" never reached, no dialog.


# The F1 parameter file the fake TopSpin home's standard parameter library
# carries (exp/stan/nmr/par/COSYGPSW/acqu2 -- a 2D set every install has).
# Small but well-formed JCAMP-DX: the script reads TD (and logs FnMODE)
# from the copy it makes.  Its TD is deliberately NOT a row count the
# session uses (8 / 89 / 179), so an F1 TD that merely came along with the
# file can never pass as a verified write (8 / 85 / 171 at the default
# geometry; 2 for the row probe).
PAR_ACQU2 = ("##TITLE= Parameter file, TopSpin 3.7.0\n"
             "##JCAMPDX= 5.0\n"
             "##DATATYPE= Parameter Values\n"
             "##ORIGIN= Bruker BioSpin GmbH\n"
             "##OWNER= nmr\n"
             "##$TD= 256\n"
             "##$FnMODE= 6\n"
             "##$SW_h= 5000\n"
             "##$NUC1= <1H>\n"
             "##END=\n")
PAR_PROC2 = ("##TITLE= Parameter file, TopSpin 3.7.0\n"
             "##JCAMPDX= 5.0\n"
             "##DATATYPE= Parameter Values\n"
             "##ORIGIN= Bruker BioSpin GmbH\n"
             "##OWNER= nmr\n"
             "##$SI= 1024\n"
             "##$MC2= 3\n"
             "##END=\n")
# The operator's template acqu2 (legacy-2dtemplate): the same shape with a
# DIFFERENT non-zero FnMODE -- 5 (States-TPPI) against the library set's
# 6 (echo-antiecho) -- so a value the script records can be traced to the
# file it came from.  Every pseudo-2D expno of that flavor inherits this
# file through WR (no copy is ever needed) and the script must set it to
# 0 (undefined) in the session's expnos, while the operator's own dataset
# keeps its 5.
TEMPLATE_ACQU2 = PAR_ACQU2.replace("##$FnMODE= 6\n", "##$FnMODE= 5\n")


def _fnmode_in(path):
    """FnMODE as an acqu2 on disk states it: None when the line is absent,
    "missing" when there is no file, "unreadable" when it cannot be read."""
    if not os.path.isfile(path):
        return "missing"
    v = None
    try:
        fh = open(path, "r")
        try:
            for ln in fh.readlines():
                if ln.startswith("##$FnMODE="):
                    v = ln[len("##$FnMODE="):].strip()
        finally:
            fh.close()
    except CATCHABLE:
        return "unreadable"
    return v


def build_world(workdir, flavor="legacy"):
    """Create the template dataset and a fake TSHOME (pp tree + the
    standard parameter library)."""
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

    if flavor == "legacy-2dtemplate":
        # A 2D dataset carries its F1 parameter files (Torino's template,
        # 2026-09-21: acqu2/proc2 in every expno of that bundle).
        f = open(os.path.join(template_dir, "acqu2"), "w")
        f.write(TEMPLATE_ACQU2)                 # FnMODE 5: must not survive
        f.close()                               # into the session's expnos
        f = open(os.path.join(pdata_dir, "proc2"), "w")
        f.write(PAR_PROC2)
        f.close()

    # Fake TSHOME: find_pp_user_dir() requires .../lists/pp to exist and
    # creates .../pp/user itself; TOPSPIN_HOME and XWINNMRHOME are two of
    # its env probes (real consoles set XWINNMRHOME; both name the same
    # directory here).  The standard parameter library exp/stan/nmr/par
    # is part of every TopSpin install: the script's F1-file fallback
    # copies acqu2 from a 2D set there when the console did not create
    # it -- which no console does on a scripted PARMODE write (Oulu
    # 3.7.0, Torino 4.4.0), so every 1D-template flavor whose switch is
    # accepted uses it exactly once; the flavors that go through the
    # operator's parmode, and the 2D template, must leave it UNUSED (the
    # harness asserts f1_files_created per flavor).
    tshome = os.path.join(workdir, "tshome")
    os.makedirs(os.path.join(tshome, "exp", "stan", "nmr", "lists", "pp"))
    pardir = os.path.join(tshome, "exp", "stan", "nmr", "par", "COSYGPSW")
    os.makedirs(pardir)
    f = open(os.path.join(pardir, "acqu2"), "w")
    f.write(PAR_ACQU2)
    f.close()
    f = open(os.path.join(pardir, "proc2"), "w")
    f.write(PAR_PROC2)
    f.close()
    os.environ["TOPSPIN_HOME"] = tshome
    os.environ["XWINNMRHOME"] = tshome

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
    # Oulu's acquisition mode (2026-09-30): every expno of the first live
    # run carried DIGMOD 3 (baseopt) / DSPFIRM 4 (rectangle) from the
    # operator's parameter set -- the mode in which the DRU aborted the
    # 262144-point rows.  The script must switch every dataset it
    # acquires with to digital / sharp (v0.7.7).
    "DIGMOD": u"baseopt",
    "DSPFIRM": u"rectangle",
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
    # Left BLANK on purpose: the dialog says "blank=unknown", and a blank
    # answer is what a room-temperature facility types.  The first live run
    # died here (float("") behind a catch-all TopSpin's namespace defeated).
    "spin-noise run: probe temperatures (optional)": [u"", u""],
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
# The strict flavor rejects every scripted PARMODE write, so the operator
# fallback is EXPECTED there -- exactly once, at the attended probe (the
# stub performs the operator's parmode as a side effect of answering).
CONFIRM_ANSWERS_STRICT = {
    "spin_noise_run: make dataset 1D": 1,
    "spin_noise_run: make dataset 2D": 1,
}
# The F1-TD fault flavors expect the rows dialog (the fixture performs the
# operator's '1 td' in ts44-f1route; in ts44-f1mismatch nothing can satisfy
# the lying readback, so the bounded loop must give up after two).
CONFIRM_ANSWERS_F1 = {
    "spin_noise_run: make dataset 1D": 1,
    "spin_noise_run: set F1 TD (number of rows)": 1,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("simulate", "desktest"):
        print "usage: jython jython_entry.py {simulate|desktest} " \
              "[rdopt] [sweep] [autostep] [nonutation] [sparseladder]"
        return 2
    mode = sys.argv[1]
    features = []
    for a in sys.argv[2:]:
        if a in ("rdopt", "sweep", "autostep", "nonutation", "sparseladder"):
            features.append(a)
    rdopt_on = "rdopt" in features
    sweep_on = "sweep" in features
    autostep_on = "autostep" in features
    nutation_on = "nonutation" not in features
    dense_on = "sparseladder" not in features

    workdir = os.environ.get("HARNESS_WORKDIR")
    if not workdir:
        import tempfile
        workdir = tempfile.mkdtemp(prefix="spin_noise_harness_")
    if not os.path.isdir(workdir):
        os.makedirs(workdir)
    flavor = os.environ.get("HARNESS_TS_FLAVOR", "legacy")
    if flavor not in FLAVORS:
        print "harness: unknown HARNESS_TS_FLAVOR %r (one of %s)" \
            % (flavor, "|".join(FLAVORS))
        return 2

    print "harness: mode=%s features=%s flavor=%s workdir=%s" \
        % (mode, ",".join(features) or "none", flavor, workdir)
    datadir, tshome = build_world(workdir, flavor)

    template = [u"WATERTEST", u"1", u"1", datadir.decode("utf-8")]
    confirm_answers = CONFIRM_ANSWERS
    if flavor in ("ts44-strict", "ts44-dimlie"):
        confirm_answers = CONFIRM_ANSWERS_STRICT
    elif flavor in ("ts44-f1route", "ts44-f1mismatch"):
        confirm_answers = CONFIRM_ANSWERS_F1
    template_params = dict(TEMPLATE_PARAMS)
    if flavor == "legacy-2dtemplate":
        template_params["PARMODE"] = u"1"       # a 2D dataset was open
        template_params["1 TD"] = u"256"        # ... with its F1 TD (acqu2)
    topspin_stub.configure(template, template_params,
                           DIALOG_ANSWERS, SELECT_ANSWERS,
                           confirm_answers, flavor=flavor)
    topspin_stub.NUTATION_P90_TRUE_US[0] = NUTATION_P90_TRUE_US

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
    # TopSpin executes user scripts in a namespace that carries java.lang.*
    # (String, Exception, ...), so the bare name Exception is
    # java.lang.Exception on a real console.  Reproduce that here: a catch-all
    # written as `except Exception:` must fail in the harness exactly as it
    # failed at Torino (TopSpin 4.4.0, 2026-09-17).
    import java.lang
    script_globals["Exception"] = java.lang.Exception
    run_error = None
    try:
        execfile(SCRIPT, script_globals)
    except SystemExit:
        pass          # EXIT() outside main()'s own catcher; treated below
    except SCRIPT_ESCAPES:
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

    # _set_fnmode_undefined edits ONE value of an acqu2 -- the copy the
    # script made or the file a dataset inherited -- and nothing else:
    # binary in and out, every line keeping its own terminator (a Windows
    # console's Jython text mode would otherwise turn a whole LF file into
    # CRLF), the absent line appended before ##END=, a file that already
    # says 0 left alone byte for byte (changed 0: no RE for it).  A pure
    # function of the file, so it is checked on every flavor against four
    # fixtures written and read back as bytes; it returns (was, changed).
    _fn_edit = script_globals.get("_set_fnmode_undefined")
    _fn_cases = (
        ("lf", "##TITLE= t\n##$TD= 4\n##$FnMODE= 6\n##END=\n",
         "##TITLE= t\n##$TD= 4\n##$FnMODE= 0\n##END=\n", ("6", 1)),
        ("crlf", "##TITLE= t\r\n##$TD= 4\r\n##$FnMODE= 6\r\n##END=\r\n",
         "##TITLE= t\r\n##$TD= 4\r\n##$FnMODE= 0\r\n##END=\r\n", ("6", 1)),
        ("absent", "##TITLE= t\n##$TD= 4\n##END=\n",
         "##TITLE= t\n##$TD= 4\n##$FnMODE= 0\n##END=\n", ("", 1)),
        ("already0", "##TITLE= t\r\n##$TD= 4\r\n##$FnMODE= 0\r\n##END=\r\n",
         "##TITLE= t\r\n##$TD= 4\r\n##$FnMODE= 0\r\n##END=\r\n", ("0", 0)),
    )
    _fn_bad = []
    _fn_dir = os.path.join(workdir, "fnmode_edit_check")
    if not os.path.isdir(_fn_dir):
        os.makedirs(_fn_dir)
    for _tag, _src, _want, _was_want in _fn_cases:
        _p = os.path.join(_fn_dir, "acqu2_%s" % _tag)
        _got = None
        _was = None
        try:
            _fh = open(_p, "wb")
            _fh.write(_src)
            _fh.close()
            _was = _fn_edit(_p)
            _fh = open(_p, "rb")
            _got = _fh.read()
            _fh.close()
        except SCRIPT_ESCAPES:
            _fn_bad.append("%s: %s" % (_tag, traceback.format_exc()[-300:]))
            continue
        if _got != _want or _was != _was_want:
            _fn_bad.append("%s: got %r (was %r)" % (_tag, _got, _was))
    check("_set_copied_fnmode_undefined edits only the FnMODE value: LF "
          "and CRLF files keep their line endings byte for byte, an absent "
          "line is added before ##END=, the source value is returned",
          callable(_fn_edit) and not _fn_bad, "; ".join(_fn_bad)[:600])
    # The script's CATCHABLE must catch a Python exception under BOTH
    # conceivable TopSpin shadowing mechanisms: java.lang names installed
    # into the script's globals (modelled above for the whole run) or into
    # __builtin__ (modelled here, briefly, around one float("")).  A tuple
    # built from __builtin__.Exception degenerates to (java.lang.Exception,
    # java.lang.Exception) under the second mechanism; exceptions.Exception
    # does not.
    _cat = script_globals.get("CATCHABLE")
    _robust = 0
    if _cat is not None:
        _real_exc = __builtin__.Exception
        __builtin__.Exception = java.lang.Exception
        try:
            try:
                try:
                    float("")
                except _cat:
                    _robust = 1
            except ValueError:
                _robust = 0
        finally:
            __builtin__.Exception = _real_exc
    check("script CATCHABLE still catches a Python exception with "
          "__builtin__.Exception shadowed by java.lang.Exception",
          _robust, "CATCHABLE = %r" % (_cat,))
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
    # v0.7.7: the row probe (expno 17) sits between the ladder and the
    # opening reference; v0.8.2: the nutation curve (expnos 80..89, the
    # 1.0 x P90 step first) between the ladder and the row probe, unless
    # the nonutation flag skips it.
    expected_expnos += [10, 14, 15, 16]
    expected_roles += ["rg_ladder"] * 4
    # v0.8.2 addendum: the dense two-visit ladder (expnos 300..331, role
    # rg_ladder) between the legacy rungs and the nutation block, unless
    # the sparseladder flag skips it.
    if dense_on:
        expected_expnos += list(DENSE_EXPNOS)
        expected_roles += ["rg_ladder"] * len(DENSE_EXPNOS)
    if nutation_on:
        expected_expnos += list(NUTATION_EXPNOS)
        expected_roles += ["nutation"] * len(NUTATION_EXPNOS)
    expected_expnos += [17, 11]
    expected_roles += ["row_probe", "reference_open"]
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

    # Both project pulse programs go into pp/user (v0.7.6: the references
    # use zgref2d, not Bruker's zg2d, whose d20-computed pacing delay came
    # out negative and refused to compile at Torino, 2026-09-25).
    pp_user = os.path.join(tshome, "exp", "stan", "nmr", "lists", "pp",
                           "user")
    for _ppname, _ppmark in (("zgnoise2d", ";zgnoise2d"),
                             ("zgref2d", ";zgref2d")):
        pp_path = os.path.join(pp_user, _ppname)
        pp_ok = False
        if os.path.isfile(pp_path):
            f = open(pp_path, "r")
            pp_ok = f.read().startswith(_ppmark + "\n")
            f.close()
        check("pulse program %s installed into fake TSHOME pp/user" % _ppname,
              pp_ok, pp_path)

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
    try:
        _spec = _jsonmod.loads(meta_text).get("spectrometer", {})
    except CATCHABLE:
        _spec = {}
    check("meta.json coil_temp_k/preamp_temp_k are null when the optional "
          "temperature fields are left blank",
          _spec.get("coil_temp_k", 0) is None
          and _spec.get("preamp_temp_k", 0) is None,
          repr((_spec.get("coil_temp_k"), _spec.get("preamp_temp_k"))))

    # ---- dataset dimensionality / F1 dialect (v0.7.3).  The ts44*
    # flavors reproduce Torino's TopSpin 4.4.0 (2026-09-18) and its likely
    # variants (see topspin_stub.py).  Invariants: no dialog in a clean run
    # except the scripted operator steps of the fault flavors, all at the
    # attended probe unless the console rejects '1 TD' outright; a form the
    # console rejected is probed once per session (each rejection is a
    # stray console dialog); the row count is actually read back; datasets
    # WR()-copied from a 2D neighbour are recognised as already 2D; a
    # readback that lies never traps the operator and stops being consulted
    # after contradicting them twice.
    try:
        _pa = _jsonmod.loads(meta_text).get("software", {}).get(
            "param_api", {})
    except CATCHABLE:
        _pa = {}
    if not isinstance(_pa, dict):
        _pa = {}
    _failed = _pa.get("failed_forms", [])
    _rej = topspin_stub.PUTPAR_FAILURES
    _log = topspin_stub.LOG
    _n_put_f1 = len([1 for a, s in _log
                     if a == "PUTPAR" and s.startswith(u"1 TD = ")])
    _n_get_f1 = len([1 for a, s in _log
                     if a == "GETPAR" and s.startswith(u"1 TD = ")])
    # A CONFIRM the fixture had no answer for is not in LOG (the stub
    # records it in UNSCRIPTED and answers OK), so it is counted here as
    # well: a flavor's "dialog x0" must mean no dialog AT ALL, not merely
    # none the fixture scripted.
    _unscripted_confirms = [t for a, t in topspin_stub.UNSCRIPTED
                            if a == "CONFIRM"]
    _n_confirm_2d = len([1 for a, s in _log
                         if a == "CONFIRM" and u"make dataset 2D" in s]) \
        + len([1 for t in _unscripted_confirms if u"make dataset 2D" in t])
    _n_confirm_f1 = len([1 for a, s in _log
                         if a == "CONFIRM" and u"set F1 TD" in s]) \
        + len([1 for t in _unscripted_confirms if u"set F1 TD" in t])
    _n_msg_setup = len([1 for t, m in topspin_stub.MSGS
                        if t is not None and u"dataset setup on this console" in t])
    _notice = [s for a, s in _log
               if a == "SHOW_STATUS" and u"NOISE BLOCK starting" in s]
    _notice = _notice and _notice[-1] or u""
    _i_p90 = [k for k, (a, s) in enumerate(_log)
              if a == "INPUT_DIALOG" and u"90-degree pulse" in s]
    _i_conf = [k for k, (a, s) in enumerate(_log)
               if a == "CONFIRM" and (u"make dataset 2D" in s
                                      or u"set F1 TD" in s)]
    check("meta.json software.param_api present", bool(_pa),
          repr(_pa)[:200])
    check("param_api: putpar_failures == rejected PUTPARs seen by the stub "
          "(%d)" % len(_rej), _pa.get("putpar_failures") == len(_rej),
          repr(_pa.get("putpar_failures")))
    # Oulu, 2026-09-25 (TopSpin 3.7.0): the console popped its OWN error
    # dialog once per run ("The requested format file is invalid ...
    # /12/acqu2: getpar: No such file or directory"); the trigger is not
    # established, and the stub models it on any F1 GETPAR/PUTPAR on a 2D
    # dataset without acqu2 -- the candidate the script can avoid.  Like a
    # rejected PUTPAR, each one is a dialog the operator saw and possibly
    # a modal stop in an unattended run: none is acceptable.
    # Since v0.7.8 the stub's legacy flavors also pop Oulu's second
    # dialog, "GetEnuOrd[DSPFIRM]: enumeration name sharp not found", on
    # PUTPAR("DSPFIRM", "sharp") -- the write dropped, no exception (Oulu,
    # 2026-10-02: nine dialogs across the v0.7.7 desktest, putpar_failures
    # 0) -- so this check is also the one that fails the v0.7.7 script.
    _stray = ["%s %s: %s" % (a, n, t[:60])
              for a, n, t in topspin_stub.STRAY_DIALOGS]
    check("no stray console dialogs (the stub's models of Oulu's two "
          "dialogs: F1 GETPAR/PUTPAR on a 2D dataset without acqu2, "
          "2026-09-25, and GetEnuOrd[DSPFIRM] on PUTPAR DSPFIRM sharp, "
          "2026-10-02; TopSpin 3.7.0)",
          not _stray, "; ".join(_stray)[:600])
    _dropped = [s for a, s in _log if a == "PUTPAR-DROPPED"]
    check("no parameter write was dropped by the console without an "
          "exception (PUTPAR on a 2D dataset without acqu2; PUTPAR DSPFIRM "
          "by a name the console's enum table lacks)", not _dropped,
          "; ".join(_dropped)[:400])
    check("param_api: every rejected parameter was probed EXACTLY once "
          "(no stray dialog repeats) and failed_forms has no duplicates",
          len(_rej) == len(set([r[0] for r in _rej]))
          and len(_failed) == len(set(_failed))
          and len(_rej) <= len(_failed),
          "rejected=%r failed_forms=%r" % (_rej, _failed))
    # Once this console's F1 readback has contradicted the operator twice
    # (ts44-f1mismatch: the two bounded confirmations at the dialect
    # probe), set_f1_td trusts its writes without reading back -- by
    # design since v0.7.3 -- so every later dataset adds a PUTPAR '1 TD'
    # without a GETPAR: 12 and 13, and since v0.7.7 the row probe's 17
    # (5 writes, 4 reads).  There the requirement is that the readback
    # was consulted at all before it was given up on.
    _f1_unreliable = _pa.get("f1_readback_unreliable") == 1
    check("param_api: the F1 TD readback actually happened (GETPAR '1 TD' "
          "%d times for %d PUTPAR '1 TD'%s)"
          % (_n_get_f1, _n_put_f1,
             _f1_unreliable and "; readback flagged unreliable" or ""),
          _n_put_f1 >= 1 and _n_get_f1 >= 1
          and (_n_get_f1 >= _n_put_f1 or _f1_unreliable))
    check("param_api: dataset reloaded (RE) after the dimension switch",
          (_pa.get("reload_ok") or 0) >= 1 and not _pa.get("reload_failed"),
          repr((_pa.get("reload_ok"), _pa.get("reload_failed"))))
    _E = {
        # form, f1form, failed, verified, src, already_min, unverified_min,
        # confirm2d, confirmf1, msg_setup, notice_marker, pm_readback, acqudim
        "legacy":      dict(form="name", f1form="1 TD", failed=[], verified=1,
                            src="getpar", already_min=3, unverified_min=0,
                            c2d=0, cf1=0, msg=0, notice=None, pm="1",
                            acqudim=None),
        "ts44":        dict(form="name", f1form="1 TD", failed=[], verified=1,
                            src="getpar", already_min=3, unverified_min=0,
                            c2d=0, cf1=0, msg=0, notice=None, pm="1",
                            acqudim=2),
        "ts44-stale":  dict(form="name", f1form="1 TD", failed=[], verified=1,
                            src="getpar", already_min=3, unverified_min=0,
                            c2d=0, cf1=0, msg=0, notice=None, pm="1",
                            acqudim=2),
        # ts44-strict rejects every enum name the script writes: DIGMOD's
        # at the setup expno (set_common_acq, before any PARMODE write --
        # the 1D template needs none there), PARMODE's at the dialect
        # probe; DSPFIRM is never reached (DIGMOD failed first).
        "ts44-strict": dict(form="operator", f1form="1 TD",
                            failed=["DIGMOD:name", "PARMODE:name"], verified=1,
                            src="getpar", already_min=3, unverified_min=0,
                            c2d=1, cf1=0, msg=1,
                            notice=u"no further step expected",
                            pm="1", acqudim=2),
        # f1echo: GETPAR echoes the direct TD, which the script distrusts;
        # the acqu2 FILE (kept in step with the accepted write by the
        # console, as TopSpin's putpar does) is what verifies the rows.
        "ts44-f1echo": dict(form="name", f1form="1 TD", failed=[], verified=1,
                            src="acqu2", already_min=3, unverified_min=0,
                            c2d=0, cf1=0, msg=0, notice=None, pm="1",
                            acqudim=2),
        "ts44-dimlie": dict(form="operator", f1form="1 TD", failed=[],
                            verified=1, src="getpar", already_min=0,
                            unverified_min=3, c2d=2, cf1=0, msg=1,
                            notice=u"no further step expected", pm="",
                            acqudim=1),
        # f1route: the operator types '1 td' wherever a dataset needs a
        # row count its WR source did not have -- the dialect probe (11,
        # 8 rows), the row probe (17: WR-copied from 11 with 8, wants 2;
        # v0.7.7), the noise block (12: 171) and the closing reference
        # (13: from a 1D rung) = 4; section 9 re-opens 11 already at 8.
        "ts44-f1route": dict(form="name", f1form="operator",
                             failed=["F1 TD:1 TD"], verified=1, src="getpar",
                             already_min=3, unverified_min=0, c2d=0, cf1=4,
                             msg=1, notice=u"'1 td' typed by hand", pm="1",
                             acqudim=2),
        "ts44-f1mismatch": dict(form="name", f1form="operator", failed=[],
                                verified=0, src="", already_min=3,
                                unverified_min=0, c2d=0, cf1=2, msg=1,
                                notice=u"no further step expected", pm="1",
                                acqudim=2),
        "legacy-2dtemplate": dict(form="name", f1form="1 TD", failed=[],
                                  verified=1, src="getpar", already_min=3,
                                  unverified_min=0, c2d=0, cf1=0, msg=0,
                                  notice=None, pm="1", acqudim=None),
        # Oulu: GETACQUDIM exists on 3.7.0 (acqudim_readback 2 in the
        # bundle); the F1 files come from the parameter library once, at
        # the probe; everything else must look like the legacy console.
        "legacy-noacqu2": dict(form="name", f1form="1 TD", failed=[],
                               verified=1, src="getpar", already_min=3,
                               unverified_min=0, c2d=0, cf1=0, msg=0,
                               notice=None, pm="1", acqudim=2),
        # The same console with a refusing receiver unit: the dataset
        # dialect is Oulu's, so every expectation here is legacy-noacqu2's
        # (the refusals show in calibration.row_probe, checked below).
        "legacy-dru": dict(form="name", f1form="1 TD", failed=[],
                           verified=1, src="getpar", already_min=3,
                           unverified_min=0, c2d=0, cf1=0, msg=0,
                           notice=None, pm="1", acqudim=2),
        "legacy-dru-refused": dict(form="name", f1form="1 TD", failed=[],
                                   verified=1, src="getpar", already_min=3,
                                   unverified_min=0, c2d=0, cf1=0, msg=0,
                                   notice=None, pm="1", acqudim=2),
        # The non-coupling 3.x console (v0.7.8): Oulu's dialect again; the
        # DSPFIRM name ladder is what differs (checked with the mode).
        "legacy-nocouple": dict(form="name", f1form="1 TD", failed=[],
                                verified=1, src="getpar", already_min=3,
                                unverified_min=0, c2d=0, cf1=0, msg=0,
                                notice=None, pm="1", acqudim=2),
        # The failing rga of the nutation block (v0.8.2): Oulu's dialect
        # again; the RG-1 fallback is checked with the nutation block.
        "legacy-rgafail": dict(form="name", f1form="1 TD", failed=[],
                               verified=1, src="getpar", already_min=3,
                               unverified_min=0, c2d=0, cf1=0, msg=0,
                               notice=None, pm="1", acqudim=2),
    }[flavor]
    # F1 parameter files (v0.7.5).  No console is known to create acqu2 on
    # a scripted PARMODE write -- TopSpin 3.7.0 (Oulu, 2026-09-25) and
    # 4.4.0 (Torino, live run 2026-09-22) both left the dataset 2D without
    # it and dropped every write into it -- and the stub models every
    # flavor that way.  So every 1D-template flavor whose scripted switch
    # is ACCEPTED copies the file from the library exactly once, at the
    # probe (expno 11; 12/13 inherit it via WR); the two flavors whose
    # switch needs the operator (ts44-strict rejects the enum name,
    # ts44-dimlie's readback lies) find the file TopSpin's OWN parmode
    # made (the 'make dataset 2D' fixture side effect) and copy nothing;
    # legacy-2dtemplate inherits the template's file everywhere.
    _E["f1_created"] = 1
    _E["f1_source"] = "par:COSYGPSW"
    if flavor in ("ts44-strict", "ts44-dimlie", "legacy-2dtemplate"):
        _E["f1_created"] = 0
        _E["f1_source"] = ""
    # FnMODE normalisation: every F1 file a pseudo-2D expno acquires with
    # must say 0 (undefined -- Bruker's rule for a pulse program without
    # an mc statement), set by file edit, never by PUTPAR.  Which files
    # needed the edit follows from where the file came from:
    #   * a library copy (the accepted-switch flavors): the fixture set
    #     carries 6 -> one edit, value "6"; 12/13 inherit the edited file;
    #   * TopSpin's own parmode (ts44-strict, ts44-dimlie): the console's
    #     default file says 0 -> nothing to edit, value "";
    #   * legacy-2dtemplate: expno 11 inherits the operator's template
    #     acqu2 (FnMODE 5) via WR and is edited at the probe; 12/13 are
    #     WR-copied from 11 after that -> exactly one edit, value "5".
    _E["fn_edits"] = 1
    _E["fn_copied"] = "6"
    if flavor in ("ts44-strict", "ts44-dimlie"):
        _E["fn_edits"] = 0
        _E["fn_copied"] = ""
    elif flavor == "legacy-2dtemplate":
        _E["fn_copied"] = "5"
    # RE reloads (param_api reload_ok), derived per flavor -- one per
    # PARMODE write that took, one per operator step, one per F1 file the
    # script copied or edited (a copy is normalised before its one RE):
    #   legacy, ts44, ts44-stale, ts44-f1echo, legacy-noacqu2: the probe's
    #     PARMODE switch + the library copy = 2 (the rdopt/sweep features
    #     add only datasets that WR() already gives the right dimension);
    #   ts44-strict: the rejected write reloads nothing, the operator's
    #     parmode dialog reloads once = 1;
    #   ts44-dimlie: the probe's write 1 + two operator confirmations 2;
    #     the readback is unreliable from then on, so every later
    #     set_parmode writes and reloads -- 4 ladder rungs -> 1D, the row
    #     probe's expno 17 (v0.7.7), the re-opened 11, 12 and 13 -> 2D --
    #     8 more = 11 (10 before the row probe existed);
    #   ts44-f1route: 2 + one operator '1 td' at each of 11 (the dialect
    #     probe), 17 (v0.7.7: the WR copy of 11 carries 8 rows, the row
    #     probe wants 2), 12 and 13 = 6 (5 before the row probe existed;
    #     section 9 re-opens 11 already at 8 rows, no dialog there);
    #   ts44-f1mismatch: 2 + the two bounded confirmations at the probe = 4
    #     (F1 TD writes are trusted silently afterwards, expno 17 included);
    #   legacy-2dtemplate: setup -> 1D, probe -> 2D, the FnMODE edit of the
    #     inherited acqu2 = 3;
    #   legacy-dru, legacy-dru-refused: as legacy-noacqu2 -- expno 17 and
    #     the noise retry rewrite F1 TD / TD / D11 on datasets that are
    #     already 2D with acqu2, which reloads nothing = 2.
    # Before the Torino evidence the accepted-switch flavors reloaded once
    # (the console 'created' the file, so nothing was copied).
    _E["reloads"] = {"legacy": 2, "ts44": 2, "ts44-stale": 2,
                     "ts44-f1echo": 2, "legacy-noacqu2": 2,
                     "ts44-strict": 1, "ts44-dimlie": 11,
                     "ts44-f1route": 6, "ts44-f1mismatch": 4,
                     "legacy-2dtemplate": 3,
                     "legacy-dru": 2, "legacy-dru-refused": 2,
                     "legacy-nocouple": 2, "legacy-rgafail": 2}[flavor]
    # ts44-dimlie (v0.8.2): the ten nutation steps are 1D datasets made
    # by WR from the last rung, and with the readback unreliable each
    # make_1d writes PARMODE and reloads once more, like the rungs -> +10.
    if flavor == "ts44-dimlie" and nutation_on:
        _E["reloads"] = _E["reloads"] + len(NUTATION_EXPNOS)
    if flavor == "ts44-dimlie" and dense_on:    # the 32 dense rungs likewise
        _E["reloads"] = _E["reloads"] + len(DENSE_EXPNOS)
    # Every dataset must have the dimensionality its role needs at
    # acquisition time, whatever the template was: the stub's parameter
    # store is inspected per expno (PARMODE ordinal "0"/"1" or name).
    _one_d_roles = ("setup", "rg_ladder", "rdopt_scan", "sweep_verify",
                    "sweep_signcal", "nutation")
    _bad_dims = []
    for _e, _role in zip(expected_expnos, expected_roles):
        _pm = topspin_stub._PARAMS.get(os.path.join(name_dir, str(_e)),
                                        {}).get("PARMODE")
        _is1d = _pm in (u"0", u"1D")
        if (_role in _one_d_roles) != _is1d:
            _bad_dims.append("expno %d (%s): PARMODE=%r" % (_e, _role, _pm))
    check("dimensionality: 1D roles are 1D and pseudo-2D roles are 2D at "
          "acquisition, whatever the template was", not _bad_dims,
          "; ".join(_bad_dims))
    # ---- acquisition mode (v0.7.7; read before written since v0.7.8).
    # Oulu, 2026-09-30: every expno ran DIGMOD baseopt / DSPFIRM rectangle
    # from the operator's parameter set and the DRU aborted every
    # 262144-point pseudo-2D row after the first ("too much data for the
    # LAN capacity"); Torino's digital / sharp rows acquired.  Oulu,
    # 2026-10-02: the operator's manual check in digital mode acquired the
    # refused row (the mode is confirmed), and the v0.7.7 desktest on the
    # same console showed the DIGMOD write alone moving DSPFIRM to sharp
    # while PUTPAR DSPFIRM "sharp" popped "GetEnuOrd[DSPFIRM]: enumeration
    # name sharp not found" on every acquired expno WITHOUT raising.  The
    # template here carries Oulu's mode, so every dataset the script
    # acquires with must read digital / sharp at acquisition (compared as
    # the ordinals "1" / "0" through the stub's enum maps, whichever
    # spelling the store holds), and the way there is pinned per flavor:
    #   * coupling consoles (every flavor but the two below): DIGMOD =
    #     digital written EXACTLY ONCE, at the setup expno -- between the
    #     first WR (the setup expno, from the template) and the second
    #     (the first rung); WR carries the mode to every later expno and
    #     the script must see that by READING first -- no DSPFIRM write of
    #     any kind, digmod_form "name", dspfirm_form "coupled", readbacks
    #     "1" / "0", no mismatch, a DIGMOD readback on every dataset;
    #   * legacy-nocouple: the same single DIGMOD write, then the ladder:
    #     "sharp(standard)" written once and accepted, "sharp" never
    #     reached, dspfirm_form "sharp(standard)", failed_forms empty;
    #   * ts44-strict: DIGMOD's name probed once at the setup expno and
    #     rejected (a raised PUTPAR), never again, DSPFIRM never written,
    #     digmod_form "" and dspfirm_form "" with the template's readbacks
    #     "3" / "4" (read before write), DIGMOD:name in failed_forms, the
    #     mode left as the template had it.
    # In every case zero stray dialogs (checked above): the v0.7.7 script
    # fails every legacy flavor there with nine GetEnuOrd[DSPFIRM] dialogs.
    _bad_mode = []
    _want_mode = (u"1", u"0")
    if flavor == "ts44-strict":
        _want_mode = (u"3", u"4")
    for _e, _role in zip(expected_expnos, expected_roles):
        _pp = topspin_stub._PARAMS.get(os.path.join(name_dir, str(_e)), {})
        _got_mode = (topspin_stub._DIGMOD_TO_ORDINAL.get(_pp.get("DIGMOD"),
                                                         _pp.get("DIGMOD")),
                     topspin_stub._DSPFIRM_TO_ORDINAL.get(_pp.get("DSPFIRM"),
                                                          _pp.get("DSPFIRM")))
        if _got_mode != _want_mode:
            _bad_mode.append("expno %d (%s): DIGMOD/DSPFIRM %r (store %r / %r)"
                             % (_e, _role, _got_mode, _pp.get("DIGMOD"),
                                _pp.get("DSPFIRM")))
    check("acquisition mode[%s]: every acquired dataset reads DIGMOD/DSPFIRM "
          "ordinals %r at acquisition (template: baseopt/rectangle, Oulu's)"
          % (flavor, _want_mode), not _bad_mode, "; ".join(_bad_mode)[:500])
    _n_dm = len([1 for a, s in _log
                 if a == "PUTPAR" and s == u"DIGMOD = digital"])
    _n_dm_rej = len([1 for a, s in _log
                     if a == "PUTPAR-REJECTED" and s.startswith(u"DIGMOD = ")])
    _df_any = [s for a, s in _log
               if a in ("PUTPAR", "PUTPAR-REJECTED", "PUTPAR-DROPPED")
               and s.startswith(u"DSPFIRM = ")]
    _df_ok = [s for a, s in _log
              if a == "PUTPAR" and s.startswith(u"DSPFIRM = ")]
    _n_gm = len([1 for a, s in _log
                 if a == "GETPAR" and s.startswith(u"DIGMOD = ")])
    _i_wr = [k for k, (a, s) in enumerate(_log) if a == "WR"]
    _i_dm = [k for k, (a, s) in enumerate(_log)
             if a in ("PUTPAR", "PUTPAR-REJECTED")
             and s.startswith(u"DIGMOD = ")]
    _dm_at_setup = (len(_i_dm) == 1 and len(_i_wr) >= 2
                    and _i_wr[0] < _i_dm[0] < _i_wr[1])
    _mode_state = repr((_n_dm, _n_dm_rej, _df_any, _n_gm, _dm_at_setup,
                        _pa.get("digmod_form"), _pa.get("dspfirm_form"),
                        _pa.get("digmod_readback"),
                        _pa.get("dspfirm_readback"),
                        _pa.get("digmod_mismatch")))
    check("acquisition mode[%s]: DIGMOD read back before any write on every "
          "dataset (GETPAR DIGMOD %d times for %d datasets)"
          % (flavor, _n_gm, len(expected_expnos)),
          _n_gm >= len(expected_expnos), _mode_state)
    if flavor == "ts44-strict":
        check("acquisition mode[ts44-strict]: DIGMOD's enum name probed "
              "exactly once, at the setup expno, and rejected; DSPFIRM never "
              "written; digmod_form '' / dspfirm_form '' with the template's "
              "readbacks '3' / '4'; DIGMOD:name in failed_forms; no mismatch",
              _n_dm == 0 and _n_dm_rej == 1 and _dm_at_setup
              and not _df_any
              and _pa.get("digmod_form") == "" and "DIGMOD:name" in _failed
              and _pa.get("dspfirm_form") == ""
              and _pa.get("digmod_readback") == u"3"
              and _pa.get("dspfirm_readback") == u"4"
              and _pa.get("digmod_mismatch") == 0, _mode_state)
    elif flavor == "legacy-nocouple":
        check("acquisition mode[legacy-nocouple]: DIGMOD = digital written "
              "exactly once at the setup expno, the console did not couple, "
              "the ladder wrote DSPFIRM = sharp(standard) exactly once and it "
              "took ('sharp' never reached, no dialog, no failed form); "
              "digmod_form 'name', dspfirm_form 'sharp(standard)', readbacks "
              "'1' / '0', no mismatch",
              _n_dm == 1 and _dm_at_setup and _n_dm_rej == 0
              and _df_any == [u"DSPFIRM = sharp(standard)"]
              and _df_ok == [u"DSPFIRM = sharp(standard)"]
              and _pa.get("digmod_form") == "name"
              and _pa.get("dspfirm_form") == "sharp(standard)"
              and not [f for f in _failed if f.startswith("DSPFIRM:")]
              and _pa.get("digmod_readback") == u"1"
              and _pa.get("dspfirm_readback") == u"0"
              and _pa.get("digmod_mismatch") == 0, _mode_state)
    else:
        check("acquisition mode[%s]: DIGMOD = digital written exactly once, "
              "at the setup expno (%d write(s) for %d datasets: WR carried "
              "the mode, the script read it), the console coupled DSPFIRM to "
              "sharp by itself and NO DSPFIRM write of any kind was issued "
              "(%d); digmod_form 'name', dspfirm_form 'coupled', readbacks "
              "'1' / '0', no mismatch" % (flavor, _n_dm, len(expected_expnos),
                                          len(_df_any)),
              _n_dm == 1 and _dm_at_setup and _n_dm_rej == 0
              and not _df_any
              and _pa.get("digmod_form") == "name"
              and _pa.get("dspfirm_form") == "coupled"
              and _pa.get("digmod_readback") == u"1"
              and _pa.get("dspfirm_readback") == u"0"
              and _pa.get("digmod_mismatch") == 0, _mode_state)
    # What the pseudo-2D blocks would ACQUIRE with: Oulu's v0.7.4 bundle
    # recorded td 16384 / rg 1.0 / pulprog zg2d for the noise block (the
    # values inherited from the 1D setup expno) although the script had
    # written 262144 / 101 / zgnoise2d -- a live run would have recorded
    # pulsed 1.2 s rows instead of pulse-free 19 s ones.  Every recorded
    # experiment must carry the parameters its role was given, and every
    # pseudo-2D expno must carry acqu2 into the bundle.
    try:
        _mo = _jsonmod.loads(meta_text)
    except CATCHABLE:
        _mo = {}
    _exps = {}
    for _e in (_mo.get("experiments") or []):
        _exps[_e.get("expno")] = _e
    _cks = _mo.get("checksums") or {}
    _two_d_roles = ("reference_open", "noise", "reference_close",
                    "noise_sweep", "row_probe")
    _want_pp = {"setup": "zg", "rg_ladder": "zg", "rdopt_scan": "zg",
                "sweep_verify": "zg", "sweep_signcal": "zg",
                "reference_open": "zgref2d", "reference_close": "zgref2d",
                "noise": "zgnoise2d", "noise_sweep": "zgnoise2d",
                "row_probe": "zgnoise2d", "nutation": "zg"}
    # RG: set_common_acq's 1 (the setup expno and the row probe), the
    # ladder rungs 1/8/64/rga(101 mocked), max_rg/4 = 25.25 for the
    # references, the mocked rga for the noise blocks, RDOPT_RG = 8 for
    # the quick 1Ds of rdopt / sweep; the nutation curve (v0.8.2) its own
    # rga on the 1.0 x P90 step (101 mocked) kept for every step, or the
    # RG-1 fallback where the modelled rga fails (legacy-rgafail).
    _want_rg = {"setup": 1.0, "reference_open": 25.25,
                "reference_close": 25.25, "noise": 101.0,
                "noise_sweep": 101.0, "rdopt_scan": 8.0,
                "sweep_verify": 8.0, "sweep_signcal": 8.0,
                "row_probe": 1.0, "nutation": 101.0}
    if flavor == "legacy-rgafail":
        _want_rg["nutation"] = 1.0
    _ladder_rg = {10: 1.0, 14: 8.0, 15: 64.0, 16: 101.0}
    # the dense rungs (v0.8.2 addendum) were given the level their
    # calibration.rg_ladder entry records; the levels themselves are
    # checked against DENSE_LEVELS below
    for _r in ((_mo.get("calibration") or {}).get("rg_ladder") or []):
        if isinstance(_r, dict) and _r.get("expno") in DENSE_EXPNOS:
            _ladder_rg[_r["expno"]] = _r.get("rg")
    # Row geometry (v0.7.7): TD 262144 everywhere the ladder is walked in
    # this harness -- legacy-dru's winner is its second entry (262144,
    # 3.0), legacy-dru-refused falls back to the first (262144, 1.0) and
    # retries the noise block with the second.  The probe's recorded
    # experiment is its FINAL attempt: (32768, 1.0) when none passed.
    _bad_acq = []
    _no_acqu2 = []
    for _e, _role in zip(expected_expnos, expected_roles):
        _x = _exps.get(_e)
        if _x is None:
            _bad_acq.append("expno %d (%s): not recorded" % (_e, _role))
            continue
        _td_want = 16384
        if _role in _two_d_roles:
            _td_want = 262144
            if _role == "row_probe" and flavor == "legacy-dru-refused":
                _td_want = 32768
            if ("data/%d/acqu2" % _e) not in _cks:
                _no_acqu2.append(_e)
        _rg_want = _want_rg.get(_role)
        if _role == "rg_ladder":
            _rg_want = _ladder_rg.get(_e)
        _problems = []
        if _x.get("td") != _td_want:
            _problems.append("td %r != %d" % (_x.get("td"), _td_want))
        if _x.get("pulprog") != _want_pp.get(_role):
            _problems.append("pulprog %r != %r"
                             % (_x.get("pulprog"), _want_pp.get(_role)))
        try:
            _rg_ok = abs(float(_x.get("rg")) - _rg_want) < 1e-6
        except CATCHABLE:
            _rg_ok = 0
        if not _rg_ok:
            _problems.append("rg %r != %r" % (_x.get("rg"), _rg_want))
        if _problems:
            _bad_acq.append("expno %d (%s): %s"
                            % (_e, _role, ", ".join(_problems)))
    check("recorded acquisition parameters match what each role was given "
          "(td, rg, pulprog per expno -- Oulu's pseudo-2D blocks did not)",
          not _bad_acq, "; ".join(_bad_acq)[:600])
    check("bundle checksums carry data/<expno>/acqu2 for every pseudo-2D "
          "expno (Oulu's bundle had none)", not _no_acqu2,
          "missing for expnos %r" % _no_acqu2)
    _rgl = (_mo.get("calibration") or {}).get("rg_ladder") or []
    _rgl_bad = []
    for _r in _rgl:
        _x = _exps.get(_r.get("expno"))
        try:
            if abs(float(_x.get("rg")) - float(_r.get("rg"))) > 1e-6:
                _rgl_bad.append("expno %s: ladder %r vs recorded %r"
                                % (_r.get("expno"), _r.get("rg"),
                                   _x.get("rg")))
        except CATCHABLE:
            _rgl_bad.append("expno %s: unreadable" % _r.get("expno"))
    check("calibration.rg_ladder agrees with experiments[] RG (the mocked "
          "rga writes RG like the real one)", bool(_rgl) and not _rgl_bad,
          "; ".join(_rgl_bad))
    _n_pm_writes = len([1 for a, s in _log
                        if a == "PUTPAR" and s.startswith(u"PARMODE = ")])
    if flavor in ("legacy", "legacy-noacqu2", "legacy-nocouple",
                  "legacy-rgafail") or flavor in DRU_FLAVORS:
        check("PARMODE written exactly once (the dialect probe's 2D; setup "
              "already 1D; the row probe is WR-copied from expno 11)",
              _n_pm_writes == 1, "writes %d" % _n_pm_writes)
    elif flavor == "legacy-2dtemplate":
        check("PARMODE written exactly twice (setup -> 1D, probe -> 2D)",
              _n_pm_writes == 2, "writes %d" % _n_pm_writes)
    check("param_api[%s]: PARMODE path == %r" % (flavor, _E["form"]),
          _pa.get("parmode_form") == _E["form"], repr(_pa.get("parmode_form")))
    check("param_api[%s]: F1 TD path == %r" % (flavor, _E["f1form"]),
          _pa.get("f1_td_form") == _E["f1form"], repr(_pa.get("f1_td_form")))
    check("param_api[%s]: failed_forms == %r" % (flavor, _E["failed"]),
          list(_failed) == _E["failed"], repr(_failed))
    check("param_api[%s]: F1 TD verified == %d via %r"
          % (flavor, _E["verified"], _E["src"]),
          _pa.get("f1_td_verified") == _E["verified"]
          and _pa.get("f1_td_readback_source") == _E["src"],
          repr((_pa.get("f1_td_verified"), _pa.get("f1_td_readback_source"))))
    check("param_api[%s]: datasets recognised as already 2D >= %d, "
          "unverified writes >= %d" % (flavor, _E["already_min"],
                                       _E["unverified_min"]),
          (_pa.get("parmode_already") or 0) >= _E["already_min"]
          and (_pa.get("parmode_unverified") or 0) >= _E["unverified_min"],
          repr((_pa.get("parmode_already"), _pa.get("parmode_unverified"))))
    check("param_api[%s]: readbacks -- PARMODE %r, GETACQUDIM %r"
          % (flavor, _E["pm"], _E["acqudim"]),
          _pa.get("parmode_readback") == _E["pm"]
          and _pa.get("acqudim_readback") == _E["acqudim"],
          repr((_pa.get("parmode_readback"), _pa.get("acqudim_readback"))))
    check("param_api[%s]: F1 files created by the script == %d, source %r"
          % (flavor, _E["f1_created"], _E["f1_source"]),
          _pa.get("f1_files_created") == _E["f1_created"]
          and _pa.get("f1_files_source") == _E["f1_source"],
          repr((_pa.get("f1_files_created"), _pa.get("f1_files_source"))))
    check("param_api[%s]: f1_fnmode_edits == %d, f1_fnmode_copied == %r "
          "(F1 files set to FnMODE 0 by file edit; the value the first of "
          "them carried)" % (flavor, _E["fn_edits"], _E["fn_copied"]),
          _pa.get("f1_fnmode_edits") == _E["fn_edits"]
          and _pa.get("f1_fnmode_copied") == _E["fn_copied"],
          repr((_pa.get("f1_fnmode_edits"), _pa.get("f1_fnmode_copied"))))
    check("param_api[%s]: dataset reloaded (RE) exactly %d time(s) -- see "
          "the derivation in the expectation table"
          % (flavor, _E["reloads"]),
          _pa.get("reload_ok") == _E["reloads"], repr(_pa.get("reload_ok")))
    check("param_api[%s]: every verified acquisition write took "
          "(acq_write_mismatch 0)" % flavor,
          _pa.get("acq_write_mismatch") == 0
          and _pa.get("last_acq_write_mismatch") == "",
          repr((_pa.get("acq_write_mismatch"),
                _pa.get("last_acq_write_mismatch"))))
    # The F1 parameter file on disk, for EVERY pseudo-2D expno of the
    # session and whatever its source (library copy, TopSpin's own
    # parmode, a 2D template): present; parameter files only -- a status
    # file (acqu2s / proc2s) describes acquired data and a copied one
    # would be the library set's; FnMODE 0 (undefined, Bruker's rule for a
    # pulse program without an mc statement), set by file edit before RE
    # and never through PUTPAR.
    _two_d_expnos = [e for e, r in zip(expected_expnos, expected_roles)
                     if r not in _one_d_roles]
    _no_f1 = [e for e in _two_d_expnos
              if not os.path.isfile(os.path.join(name_dir, str(e),
                                                 "acqu2"))]
    check("acqu2 present on disk in every pseudo-2D expno %r (%s)"
          % (_two_d_expnos, flavor), not _no_f1, "missing in %r" % _no_f1)
    _stray_status = [e for e in _two_d_expnos
                     if os.path.isfile(os.path.join(name_dir, str(e),
                                                    "acqu2s"))
                     or os.path.isfile(os.path.join(
                         name_dir, str(e), "pdata", "1", "proc2s"))]
    check("no status file (acqu2s / proc2s) beside acqu2 in any pseudo-2D "
          "expno (%s)" % flavor, not _stray_status,
          "status files in %r" % _stray_status)
    _fn_bad = []
    for e in _two_d_expnos:
        _v = _fnmode_in(os.path.join(name_dir, str(e), "acqu2"))
        if _v != "0":
            _fn_bad.append("expno %d: FnMODE %r" % (e, _v))
    check("acqu2 reads FnMODE 0 (undefined) in every pseudo-2D expno (%s)"
          % flavor, not _fn_bad, "; ".join(_fn_bad))
    _fn_putpar = [s for a, s in _log
                  if a in ("PUTPAR", "PUTPAR-DROPPED", "PUTPAR-REJECTED")
                  and u"FnMODE" in s]
    check("FnMODE was never written through PUTPAR (file edit only; %s)"
          % flavor, not _fn_putpar, "; ".join(_fn_putpar))
    if _E["f1_created"]:
        # Exactly one copy: the probe's expno 11 gets acqu2 from the
        # library; 12 and 13 are WR()-copied from a dataset that has the
        # file, so the first F1 TD write (probe) must precede WR 12.
        _i_f1 = [k for k, (a, s) in enumerate(_log)
                 if a == "PUTPAR" and s.startswith(u"1 TD = ")]
        _i_wr12 = [k for k, (a, s) in enumerate(_log)
                   if a == "WR" and s.endswith(u"/12")]
        check("param_api[%s]: the first F1 TD write (probe, expno 11) "
              "precedes the WR of expno 12 (12 inherits the file)" % flavor,
              bool(_i_f1) and (not _i_wr12 or _i_f1[0] < _i_wr12[0]),
              "first '1 TD' write at %r, WR 12 at %r"
              % (_i_f1[:1], _i_wr12[:1]))
    if flavor == "legacy-2dtemplate":
        # The template's acqu2 (FnMODE 5) travels into every expno via
        # WR; the probe's expno 11 is the one the script edits (asserted
        # above: one edit, value "5", one RE), 12 and 13 are WR-copied from
        # it afterwards.  The operator's own dataset is never touched.
        _tpl_fn = _fnmode_in(os.path.join(datadir, "WATERTEST", "1",
                                          "acqu2"))
        check("legacy-2dtemplate: the operator's template acqu2 is untouched "
              "(still FnMODE 5)", _tpl_fn == "5", repr(_tpl_fn))
    check("param_api[%s]: 'make dataset 2D' dialog x%d, 'set F1 TD' dialog "
          "x%d" % (flavor, _E["c2d"], _E["cf1"]),
          _n_confirm_2d == _E["c2d"] and _n_confirm_f1 == _E["cf1"],
          "seen %d / %d" % (_n_confirm_2d, _n_confirm_f1))
    check("param_api[%s]: attended 'dataset setup on this console' warning "
          "x%d" % (flavor, _E["msg"]), _n_msg_setup == _E["msg"],
          "seen %d" % _n_msg_setup)
    if sweep_on:
        pass          # the sweep replaces the single noise block (no notice)
    elif _E["notice"] is None:
        check("param_api[%s]: pre-noise-block notice has no manual-step tail"
              % flavor, u"NOTE:" not in _notice, _notice[:160])
    else:
        check("param_api[%s]: pre-noise-block notice says %r"
              % (flavor, _E["notice"]), _E["notice"] in _notice, _notice[:160])
    if _E["c2d"] or _E["cf1"]:
        # Every operator step except a recurring '1 td' happens BEFORE the
        # 90-degree dialog (operator present); the recurring '1 td' case
        # is exactly what the attended warning announces.
        _first_ok = bool(_i_conf) and bool(_i_p90) and _i_conf[0] < _i_p90[0]
        if flavor == "ts44-f1route":
            _all_ok = _first_ok
        else:
            _all_ok = _first_ok and all([k < _i_p90[0] for k in _i_conf])
        check("param_api[%s]: operator steps happen BEFORE the 90-degree "
              "dialog (operator present)%s" % (
                  flavor, flavor == "ts44-f1route"
                  and " -- except the announced recurring '1 td'" or ""),
              _all_ok, "confirms at %r, p90 dialog at %r" % (_i_conf, _i_p90))
    if flavor in ("ts44-dimlie", "ts44-f1mismatch"):
        check("param_api[%s]: lying readback flagged unreliable after two "
              "contradictions" % flavor,
              (_pa.get("dim_readback_unreliable") == 1) if flavor == "ts44-dimlie"
              else (_pa.get("f1_readback_unreliable") == 1
                    and _pa.get("f1_td_readback_mismatch") not in ("", None)),
              repr((_pa.get("dim_readback_unreliable"),
                    _pa.get("f1_readback_unreliable"),
                    _pa.get("f1_td_readback_mismatch"))))

    # ---- row probe and data content (v0.7.7).  Oulu (Avance III HD 500,
    # AQS DRU-E, TopSpin 3.7.0, 2026-09-30): the receiver unit refused
    # every pseudo-2D block at zg, TopSpin pre-allocated the full ser, the
    # first row held data and the rest zeros, and the v0.7.6 script took
    # the file for an acquisition.  The script now writes the transfer
    # delay D11, probes the row geometry at expno 17 by the LAST row's
    # content, checks every block the same way, retries the noise block
    # once, and never dialogs for it.  The mocked acquisition leaves a
    # raw-data file of the block's size; the legacy-dru flavors leave the
    # refused shape.  Model of the rows per role (mirrors the script):
    #   noise / noise_sweep: int(secs / (aq + d1_noise + d11 + 1.0));
    #   references:          round(170 / (aq + 2.0 + d11));
    #   row probe:           max(2, ceil(40 / (aq + d1_noise + d11))).
    import math as _math
    _rp = (_mo.get("calibration") or {}).get("row_probe") or {}
    _rp_att = _rp.get("attempts") or []
    _rp_blocks = _rp.get("blocks") or []
    _rp_by_expno = {}
    for _b in _rp_blocks:
        _rp_by_expno[_b.get("expno")] = _b
    _bwd = _pa.get("blocks_without_data")
    _dru = [s for a, s in _log if a == "DRU-REFUSED"]

    def _aq(td):
        return td / (2.0 * 6900.0)

    def _rows_model(role, td, d11, secs=3600.0):
        if role in ("noise", "noise_sweep"):
            return int(secs / (_aq(td) + 0.05 + d11 + 1.0))
        if role in ("reference_open", "reference_close"):
            return int(round(170.0 / (_aq(td) + 2.0 + d11)))
        if role == "row_probe":
            return max(2, int(_math.ceil(40.0 / (_aq(td) + 0.05 + d11))))
        return None

    check("row probe: calibration.row_probe present for expno 17 with the "
          "ladder as tried, attempts, blocks and the session geometry",
          _rp.get("expno") == 17 and _rp.get("ladder")
          and _rp["ladder"][0] == [262144, 1.0] and len(_rp["ladder"]) == 5
          and isinstance(_rp_att, list) and isinstance(_rp_blocks, list)
          and isinstance(_rp.get("td_row"), int)
          and isinstance(_rp.get("transfer_delay_s"), float),
          repr(_rp)[:300])
    if flavor == "legacy-dru":
        _want_att = [(262144, 1.0, 0), (262144, 3.0, 1)]
        _want_geom = (262144, 3.0)
        _want_passed = (True, 2)
        _want_dru = 1
        _want_bwd = []
    elif flavor == "legacy-dru-refused":
        _want_att = [(262144, 1.0, 0), (262144, 3.0, 0), (131072, 1.0, 0),
                     (65536, 1.0, 0), (32768, 1.0, 0)]
        _want_geom = (262144, 1.0)
        _want_passed = (False, None)
        # 5 probe attempts + reference_open + noise + its retry +
        # reference_close (sweep variant: not run under this flavor)
        _want_dru = 9
        _want_bwd = [17, 11, 12, 13]
    else:
        _want_att = [(262144, 1.0, 1)]
        _want_geom = (262144, 1.0)
        _want_passed = (True, 1)
        _want_dru = 0
        _want_bwd = []
    _got_att = [(a.get("td"), a.get("d11"), a.get("data_last"))
                for a in _rp_att]
    check("row probe[%s]: attempts (td, d11, data_last) == %r -- the ladder "
          "walked exactly as far as the modelled receiver unit requires"
          % (flavor, _want_att), _got_att == _want_att, repr(_got_att))
    check("row probe[%s]: every attempt's FIRST row held data (Oulu's DRU "
          "acquired the first row of every refused block; the stub does "
          "the same) and every attempt has rows >= 2 and a wall_s"
          % flavor,
          bool(_rp_att) and all([a.get("data_first") == 1
                                 and (a.get("rows") or 0) >= 2
                                 and isinstance(a.get("wall_s"), (int, float))
                                 for a in _rp_att]),
          repr(_rp_att)[:400])
    check("row probe[%s]: passed %r at attempt %r; session geometry (td_row, "
          "transfer_delay_s) == %r" % (flavor, _want_passed[0],
                                        _want_passed[1], _want_geom),
          _rp.get("passed") == _want_passed[0]
          and _rp.get("attempt_passed") == _want_passed[1]
          and (_rp.get("td_row"), _rp.get("transfer_delay_s")) == _want_geom,
          repr((_rp.get("passed"), _rp.get("attempt_passed"),
                _rp.get("td_row"), _rp.get("transfer_delay_s"))))
    check("row probe[%s]: the stub refused %d block(s) (DRU-REFUSED log "
          "entries)" % (flavor, _want_dru), len(_dru) == _want_dru,
          "; ".join(_dru)[:400])
    check("row probe[%s]: param_api.blocks_without_data == %r"
          % (flavor, _want_bwd), _bwd == _want_bwd, repr(_bwd))
    # every pseudo-2D block recorded in row_probe.blocks at the session
    # geometry (the refused flavor's noise block at the retry's setting),
    # acquired == (data_last == 1), rows consistent with experiments[]
    # and with the duration model
    _bad_blocks = []
    _two_d_expected = [(e, r) for e, r in zip(expected_expnos, expected_roles)
                       if r in _two_d_roles]
    for _e, _role in _two_d_expected:
        _b = _rp_by_expno.get(_e)
        _x = _exps.get(_e) or {}
        if _b is None:
            _bad_blocks.append("expno %d (%s): no row_probe.blocks entry"
                               % (_e, _role))
            continue
        _g_td, _g_d11 = _want_geom
        if flavor == "legacy-dru-refused":
            if _role in ("noise", "noise_sweep"):
                _g_td, _g_d11 = 262144, 3.0          # the one retry
            elif _role == "row_probe":
                _g_td, _g_d11 = 32768, 1.0           # the final attempt
        _probs = []
        if (_b.get("td"), _b.get("d11")) != (_g_td, _g_d11):
            _probs.append("geometry %r != %r"
                          % ((_b.get("td"), _b.get("d11")), (_g_td, _g_d11)))
        if _b.get("rows") != _x.get("td1_rows"):
            _probs.append("rows %r != experiments td1_rows %r"
                          % (_b.get("rows"), _x.get("td1_rows")))
        _secs = 3600.0
        if _role == "noise_sweep":
            _secs = max(300.0, 3600.0 / 3.0)          # per_secs of 3 steps
        _rm = _rows_model(_role, _g_td, _g_d11, _secs)
        if _rm is not None and _b.get("rows") != _rm:
            _probs.append("rows %r != duration model %r" % (_b.get("rows"), _rm))
        _acq_want = (flavor != "legacy-dru-refused")
        if bool(_b.get("acquired")) != _acq_want \
                or (_b.get("data_last") == 1) != _acq_want:
            _probs.append("acquired %r / data_last %r, expected acquired %r"
                          % (_b.get("acquired"), _b.get("data_last"),
                             _acq_want))
        if _b.get("data_first") != 1:
            _probs.append("data_first %r" % _b.get("data_first"))
        if _probs:
            _bad_blocks.append("expno %d (%s): %s" % (_e, _role,
                                                      ", ".join(_probs)))
    check("row probe[%s]: every pseudo-2D block is in row_probe.blocks at "
          "the session geometry, rows == experiments[].td1_rows == the "
          "duration model, acquired per the modelled receiver unit"
          % flavor, not _bad_blocks, "; ".join(_bad_blocks)[:700])
    _retries = _rp.get("block_retries") or []
    if flavor == "legacy-dru-refused":
        check("row probe[%s]: the noise block was retried exactly once, with "
              "the next ladder setting (262144, 3.0), rows recomputed, and "
              "still came back without data" % flavor,
              len(_retries) == 1 and _retries[0].get("expno") == 12
              and (_retries[0].get("first_try") or {}).get("td") == 262144
              and (_retries[0].get("first_try") or {}).get("d11") == 1.0
              and (_retries[0].get("retry") or {}).get("d11") == 3.0
              and (_retries[0].get("retry") or {}).get("data_last") == 0
              and (_retries[0].get("retry") or {}).get("rows")
              == _rows_model("noise", 262144, 3.0),
              repr(_retries)[:400])
        _warns = [s for a, s in _log if a == "SHOW_STATUS"
                  and u"WARNING" in s]
        check("row probe[%s]: the failures were said as WARNINGs on the "
              "status line -- the probe, each block without data, the noise "
              "retry -- and never as a dialog" % flavor,
              len([s for s in _warns if u"WARNING: row probe" in s]) == 1
              and len([s for s in _warns
                       if u"holds no data" in s]) >= 4,
              "; ".join(_warns)[:600])
    else:
        check("row probe[%s]: no block retry and no WARNING about data on the "
              "status line" % flavor,
              not _retries and not [s for a, s in _log if a == "SHOW_STATUS"
                                    and u"WARNING" in s
                                    and (u"row probe" in s
                                         or u"holds no data" in s)],
              repr(_retries)[:200])
    # the raw-data files the mocked acquisitions left, and their content
    # (what block_data_check read): a fid of 16384 int32 per 1D, a ser of
    # rows x td int32 per pseudo-2D, first row non-zero everywhere, last
    # row non-zero unless the modelled receiver unit refused the block
    _bad_files = []
    for _e, _role in zip(expected_expnos, expected_roles):
        if _role == "setup":
            continue
        _x = _exps.get(_e) or {}
        _d = os.path.join(name_dir, str(_e))
        _fn = "ser" if _role in _two_d_roles else "fid"
        _p = os.path.join(_d, _fn)
        if not os.path.isfile(_p):
            _bad_files.append("expno %d (%s): no %s" % (_e, _role, _fn))
            continue
        _rows = int(_x.get("td1_rows") or 1)
        _td = int(_x.get("td") or 0)
        _size = os.path.getsize(_p)
        if _size != _rows * _td * 4:
            _bad_files.append("expno %d (%s): %s is %d bytes, expected %d x "
                              "%d x 4" % (_e, _role, _fn, _size, _rows, _td))
            continue
        _fh = open(_p, "rb")
        try:
            _first = _fh.read(min(65536, _td * 4))
            _fh.seek((_rows - 1) * _td * 4)
            _last = _fh.read(min(65536, _td * 4))
        finally:
            _fh.close()
        _first_ok = _first.count("\x00") != len(_first)
        _last_ok = _last.count("\x00") != len(_last)
        _b = _rp_by_expno.get(_e)
        _want_last = True
        if _b is not None:
            _want_last = bool(_b.get("acquired"))
        if not _first_ok:
            _bad_files.append("expno %d (%s): first row all zeros" % (_e, _role))
        if _last_ok != _want_last:
            _bad_files.append("expno %d (%s): last row %s, recorded acquired %r"
                              % (_e, _role, "has data" if _last_ok else "zeros",
                                 _want_last))
    check("mocked acquisitions leave raw-data files of rows x TD int32 (fid "
          "for 1Ds, ser for pseudo-2Ds); first rows hold data; last rows hold "
          "data exactly where the block is recorded as acquired (%s)" % flavor,
          not _bad_files, "; ".join(_bad_files)[:600])

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
        # expected*(1 + 3e-7) + ~0.2 s overhead), i.e. within 1 percent
        # or 0.25 s, whichever is larger (v0.8.2: the stub's ~0.2 s
        # per-block overhead is 0.95% of a 21 s nutation step, and its
        # ms-scale jitter would cross a bare 1%) -- except a block the
        # modelled receiver unit refused (recorded as not acquired): its
        # wall time is one row plus the abort, as at Oulu (39.9 s for
        # 168 s expected), which the report's gate drops.
        _rb = _rp_by_expno.get(b.get("expno"))
        if _rb is not None and not _rb.get("acquired"):
            continue
        if exp is not None and exp > 10.0:
            if abs(wall_s - exp) > max(0.01 * exp, 0.25):
                consistent = False
                detail.append("block %s: wall %.3f s vs ocxo %.3f s"
                              % (b.get("expno"), wall_s, exp))
    check("clock_audit wall durations consistent with OCXO predictions "
          "(virtual clock, injected offset %.1e)"
          % topspin_stub.INJECTED_CLOCK_OFFSET,
          consistent, "; ".join(detail))
    # Timing model per role (v0.7.7): the recorded OCXO expectation is what
    # the pulse program spends per row from the parameters the script
    # writes -- zgref2d one d1 (2 s) and the transfer delay d11 per row
    # (the references), zgnoise2d one d1 (50 ms) and d11 (the noise blocks
    # and the row probe; until v0.7.6 zgnoise2d spent a second d1 and
    # zgref2d a fixed 30 ms), zg one d1 (the 1D rungs / quick 1Ds; its
    # two 30m lines are not in the acquisition-side model).  AQ =
    # TD/(2*SWH) with the block's TD (262144 by default, 16384 for 1Ds) at
    # SWH 6900 Hz; TD, d11 and rows from the block's row_probe.blocks
    # entry and experiments[].td1_rows.
    _aq_1d = 16384 / (2.0 * 6900.0)
    _bad_exp = []
    for b in blocks:
        _role = b.get("role")
        _x = _exps.get(b.get("expno")) or {}
        _rows = _x.get("td1_rows") or 0
        _rb = _rp_by_expno.get(b.get("expno")) or {}
        _td_b = _rb.get("td") or 262144
        _d11_b = _rb.get("d11")
        if _d11_b is None:
            _d11_b = 1.0
        _aq_row = _td_b / (2.0 * 6900.0)
        if _role in ("reference_open", "reference_close"):
            _want = _rows * (_aq_row + 2.0 + _d11_b)
        elif _role in ("noise", "noise_sweep", "row_probe"):
            _want = _rows * (_aq_row + 0.05 + _d11_b)
        elif _role in ("rg_ladder", "rdopt_scan", "sweep_verify",
                       "sweep_signcal"):
            _want = _aq_1d + 2.0
        elif _role == "nutation":
            _want = _aq_1d + 20.0        # NUTATION_D1_S (v0.8.2)
        else:
            continue
        _got = b.get("ocxo_expected_s")
        if _got is None or abs(_got - _want) > 1e-6 * _want:
            _bad_exp.append("expno %s (%s): recorded %r, model %r"
                            % (b.get("expno"), _role, _got, _want))
    check("clock_audit expectations follow the per-role timing model "
          "(zgref2d: one d1 + d11 per row; zgnoise2d: one d1 + d11; zg: one "
          "d1) at each block's recorded row geometry",
          not _bad_exp, "; ".join(_bad_exp)[:600])

    # ---- dense receiver-gain ladder (v0.8.2 addendum, section 8d of the
    # script).  The four legacy rungs stay as they were; then 16 levels x
    # 2 visits in expnos 300..331, role rg_ladder, small flip, visit 1 in
    # the seeded permutation of the levels (seed = the session date) and
    # visit 2 in its reverse; the reference gain 25.25 and the noise gain
    # 101 are levels of their own; entries appended to calibration.
    # rg_ladder with "visit"; calibration.rg_ladder_dense once, or the
    # skipped form under the sparseladder flag.
    _rgl_all = (_mo.get("calibration") or {}).get("rg_ladder") or []
    _dense = (_mo.get("calibration") or {}).get("rg_ladder_dense")
    _legacy_rungs = [(r.get("expno"), r.get("rg"), r.get("tip_deg"),
                      "visit" in r) for r in _rgl_all[:4]]
    check("dense ladder: the four legacy rungs lead calibration.rg_ladder "
          "unchanged (10/14/15/16 at RG 1/8/64/101, tip 1.0, no visit key)",
          _legacy_rungs == [(10, 1.0, 1.0, False), (14, 8.0, 1.0, False),
                            (15, 64.0, 1.0, False), (16, 101.0, 1.0, False)],
          repr(_legacy_rungs))
    try:
        _seed_want = int(dsname[10:18])
    except ValueError:
        _seed_want = None
    if not dense_on:
        check("dense ladder[sparseladder]: calibration.rg_ladder_dense is "
              "the skipped form with why 'sparseladder flag'; rg_ladder has "
              "the four legacy rungs only; no expno 300..363 created",
              _dense == {"skipped": True, "why": "sparseladder flag"}
              and len(_rgl_all) == 4
              and not [e for e in range(300, 364)
                       if os.path.isdir(os.path.join(name_dir, str(e)))],
              repr((_dense, len(_rgl_all))))
    else:
        _dense_rungs = [r for r in _rgl_all[4:]]
        _v1 = [r.get("rg") for r in _dense_rungs if r.get("visit") == 1]
        _v2 = [r.get("rg") for r in _dense_rungs if r.get("visit") == 2]
        _perm = _lcg_permutation(len(DENSE_LEVELS), _seed_want or 0)
        _v1_want = [DENSE_LEVELS[k] for k in _perm]
        _v2_want = _v1_want[:]
        _v2_want.reverse()
        check("dense ladder: calibration.rg_ladder_dense is the acquired form "
              "-- expnos 300..331, levels == the 16 re-derived levels "
              "(2^(k/2) to 90.51 plus 25.25 and 101), seed == the session "
              "date %r, visits 2, skipped false" % _seed_want,
              isinstance(_dense, dict) and _dense.get("skipped") is False
              and _dense.get("expnos") == list(DENSE_EXPNOS)
              and _dense.get("levels") == DENSE_LEVELS
              and _dense.get("seed") == _seed_want
              and _dense.get("visits") == 2 and "why" not in _dense,
              repr(_dense)[:400])
        check("dense ladder: 32 entries follow the legacy rungs in "
              "calibration.rg_ladder, expnos 300..331 in acquisition order, "
              "tip 1.0, every level visited exactly once per visit, visit 1 "
              "in the seeded permutation re-derived here and visit 2 in its "
              "exact reverse (every level once early and once late)",
              len(_dense_rungs) == len(DENSE_EXPNOS)
              and [r.get("expno") for r in _dense_rungs] == list(DENSE_EXPNOS)
              and not [r for r in _dense_rungs if r.get("tip_deg") != 1.0]
              and sorted(_v1) == DENSE_LEVELS and sorted(_v2) == DENSE_LEVELS
              and _v1 == _v1_want and _v2 == _v2_want
              and [r.get("visit") for r in _dense_rungs]
              == [1] * len(DENSE_LEVELS) + [2] * len(DENSE_LEVELS),
              "visit1 %r visit2 %r want1 %r" % (_v1, _v2, _v1_want))
        check("dense ladder: the reference gain (max_rg/4 = 25.25) and the "
              "noise gain (max_rg = 101) are levels of their own, each "
              "acquired twice, and every dense rung recorded td 16384 / zg "
              "in experiments[]",
              _v1.count(25.25) == 1 and _v2.count(25.25) == 1
              and _v1.count(101.0) == 1 and _v2.count(101.0) == 1
              and not [e for e in DENSE_EXPNOS
                       if (_exps.get(e) or {}).get("td") != 16384
                       or (_exps.get(e) or {}).get("pulprog") != "zg"],
              repr([(e, (_exps.get(e) or {}).get("rg"))
                    for e in DENSE_EXPNOS])[:400])

    # ---- nutation curve (v0.8.2, section 8c of the script): the pulse
    # calibration kept as data.  Ten quick 1Ds (expnos 80..89) at the
    # CALIBRATED power (the fixture's PLdB 1 -11.79), P1 = multiplier x
    # the declared 8.5 us, the 1.0 step first with rga on it alone (RG
    # 101 mocked; the RG-1 fallback under legacy-rgafail), never the
    # ladder's gain; the stub writes each step's physics FID from its own
    # P90_true (3% above the declared value) and the amplitudes must fit
    # back to that truth; calibration.nutation carries the acquired form,
    # or the skipped form with the reason under the nonutation flag.
    _nut = (_mo.get("calibration") or {}).get("nutation")
    _nut_fids = topspin_stub.NUTATION_FIDS
    _rga_dirs = [s for a, s in _log if a == "RGA"]
    if not nutation_on:
        check("nutation[nonutation]: calibration.nutation is the skipped "
              "form with why 'nonutation flag'",
              _nut == {"skipped": True, "why": "nonutation flag"}, repr(_nut))
        # rga still runs where the headline protocol runs it (the ladder's
        # rung 16, the noise block's expno 12), never on an expno 80..95
        _rga_nut = [s for s in _rga_dirs
                    if [1 for e in range(80, 96) if s.endswith(u"/%d" % e)]]
        check("nutation[nonutation]: no expno 80..95 created, no physics "
              "FID written, no clock block of role nutation, rga never ran "
              "on an expno 80..95 (rung 16 as before)",
              not [e for e in range(80, 96)
                   if os.path.isdir(os.path.join(name_dir, str(e)))]
              and not _nut_fids and "nutation" not in roles
              and not _rga_nut
              and [s for s in _rga_dirs if s.endswith(u"/16")],
              repr((_nut_fids, _rga_dirs)))
    else:
        _nut_rg_want = _want_rg["nutation"]
        _p1_ok = (isinstance(_nut, dict) and isinstance(_nut.get("p1_us"),
                                                        list)
                  and len(_nut["p1_us"]) == len(NUTATION_MULTS)
                  and not [1 for p, m in zip(_nut["p1_us"], NUTATION_MULTS)
                           if abs(p - m * 8.5) > 1e-6])
        check("nutation: calibration.nutation is the acquired form -- expnos "
              "80..89 in acquisition order, multipliers 1.0 first then the "
              "list, p1_us = multiplier x the declared 8.5 us, "
              "p90_us_assumed 8.5, power_db -11.79 on 'PLdB 1', rg %g, d1_s "
              "20, td 16384, skipped false" % _nut_rg_want,
              isinstance(_nut, dict) and _nut.get("skipped") is False
              and _nut.get("expnos") == list(NUTATION_EXPNOS)
              and _nut.get("multipliers") == NUTATION_MULTS and _p1_ok
              and _nut.get("p90_us_assumed") == 8.5
              and _nut.get("power_db") == -11.79
              and _nut.get("power_parname") == "PLdB 1"
              and _nut.get("rg") == _nut_rg_want
              and _nut.get("d1_s") == 20.0 and _nut.get("td") == 16384
              and "why" not in _nut, repr(_nut)[:500])
        _rga_80 = [s for s in _rga_dirs if s.endswith(u"/80")]
        _rga_nut_other = [s for s in _rga_dirs
                          if [1 for e in range(81, 96)
                              if s.endswith(u"/%d" % e)]]
        _rga_16 = [s for s in _rga_dirs if s.endswith(u"/16")]
        check("nutation: rga ran exactly once in the block, on the 1.0 x P90 "
              "step (expno 80), never on another step; the ladder's rga on "
              "rung 16 as before",
              len(_rga_80) == 1 and not _rga_nut_other and len(_rga_16) == 1,
              repr(_rga_dirs))
        _nut_exps = [(_exps.get(e) or {}).get("rg") for e in NUTATION_EXPNOS]
        _ladder_16 = (_exps.get(16) or {}).get("rg")
        if flavor == "legacy-rgafail":
            check("nutation[legacy-rgafail]: the modelled rga failed on "
                  "expno 80 only; the curve fell back to RG 1 -- recorded "
                  "in calibration.nutation.rg and on every step's "
                  "experiments[] entry -- while rung 16 kept the ladder's "
                  "rga RG 101 (never reused); said as a WARNING, no dialog",
                  len(topspin_stub.RGA_FAILURES) == 1
                  and topspin_stub.RGA_FAILURES[0].endswith(u"/80")
                  and _nut_exps == [1.0] * len(NUTATION_EXPNOS)
                  and _ladder_16 == 101.0
                  and len([s for a, s in _log if a == "SHOW_STATUS"
                           and u"rga failed on the 90-degree step" in s]) == 1,
                  repr((topspin_stub.RGA_FAILURES, _nut_exps, _ladder_16)))
        else:
            check("nutation[%s]: the modelled rga succeeded everywhere; the "
                  "block's RG (101) is rga's own result on expno 80, not an "
                  "inherited gain, on every step" % flavor,
                  not topspin_stub.RGA_FAILURES
                  and _nut_exps == [101.0] * len(NUTATION_EXPNOS)
                  and not [s for a, s in _log if a == "SHOW_STATUS"
                           and u"rga failed" in s],
                  repr((topspin_stub.RGA_FAILURES, _nut_exps)))
        _fid_expnos = []
        _fid_bad = []
        for _fd, _p1, _pl, _rg, _tip, _amp in _nut_fids:
            try:
                _fid_expnos.append(int(_fd.rstrip(u"/").split(u"/")[-1]))
            except ValueError:
                _fid_expnos.append(-1)
            if abs(_pl + 11.79) > 1e-6 or abs(_rg - _nut_rg_want) > 1e-6:
                _fid_bad.append("%s: PL %r RG %r" % (_fd, _pl, _rg))
        check("nutation: the stub wrote a full-power physics FID for exactly "
              "the ten nutation expnos (80..89), each at PLdB 1 -11.79 and at "
              "the block's RG %g -- no other 1D of the session ran at full "
              "power" % _nut_rg_want,
              sorted(_fid_expnos) == list(NUTATION_EXPNOS) and not _fid_bad,
              repr((sorted(_fid_expnos), _fid_bad))[:400])
        # amplitudes from the files the script will bundle: the first
        # sample is the amplitude (phase 0), the noise floor +/- 2000
        import struct as _struct
        _amps = {}
        for _e, _m in zip(NUTATION_EXPNOS, NUTATION_MULTS):
            _p = os.path.join(name_dir, str(_e), "fid")
            try:
                _fh = open(_p, "rb")
                _buf = _fh.read(400 * 4)
                _fh.close()
                _vals = _struct.unpack("<%di" % (len(_buf) // 4), _buf)
                _amps[_m] = max([abs(v) for v in _vals])
            except CATCHABLE:
                _amps[_m] = None
        _best = None
        if None not in _amps.values() and len(_amps) == len(NUTATION_MULTS):
            _p90 = 7.0
            while _p90 <= 11.0:
                _ss = 0.0
                _sa = 0.0
                for _m in NUTATION_MULTS:
                    _s = abs(_math.sin(_math.pi / 2.0 * _m * 8.5 / _p90))
                    _ss = _ss + _s * _s
                    _sa = _sa + _amps[_m] * _s
                _a = _ss and (_sa / _ss) or 0.0
                _res = 0.0
                for _m in NUTATION_MULTS:
                    _s = abs(_math.sin(_math.pi / 2.0 * _m * 8.5 / _p90))
                    _res = _res + (_amps[_m] - _a * _s) ** 2
                if _best is None or _res < _best[0]:
                    _best = (_res, _p90, _a)
                _p90 = _p90 + 0.001
        _fit_ok = 0
        if _best is not None:
            _s1 = abs(_math.sin(_math.pi / 2.0 * 8.5 / _best[1]))
            _fit_ok = (abs(_best[1] - NUTATION_P90_TRUE_US) < 0.03
                       and abs(_best[1] - 8.5) > 0.17
                       and _amps[2.0] == min(_amps.values())
                       and abs(_amps[1.0] - _best[2] * _s1) < 0.01 * _best[2]
                       and _amps[1.0] > 0.9 * _best[2])
        check("nutation: the ten FID amplitudes follow A90 |sin(pi/2 P1 / "
              "P90_true)| -- a grid fit recovers the stub's P90_true %.3f us "
              "within 0.03 us, 3%% away from the declared 8.5 us the steps "
              "were scaled from; the 2.0 step is the smallest (the "
              "180-degree null), the 1.0 step within 1%% of the fitted A90"
              % NUTATION_P90_TRUE_US, _fit_ok,
              "fit %r amps %r" % (_best, _amps))
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
