#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
spin_noise_driver.py -- spin-noise session driver for Agilent/Varian VnmrJ.

Runs ON THE SPECTROMETER WORKSTATION. Written for VnmrJ-era consoles, which
typically have Python 2.6 and nothing newer, so this is Python 2.6 STANDARD
LIBRARY ONLY. Developed and validated on a 400 MHz Agilent DD2 running
VnmrJ 3.2 Revision A under RHEL 6.1 (SIU Carbondale).
Python 2.6 STANDARD LIBRARY ONLY -- no f-strings, no dict/set comprehensions,
no argparse, no str.format() auto-numbering.

WHAT IT DOES
  1. Interviews the operator (remembers the last sample; only asks what changed).
  2. Makes a NEW session folder under casper/ for every run.
  3. Writes answers.json FIRST, so a crashed session is still packable.
  4. Derives the record length from the real np<=524288 cap and the block
     count from a target duration -- never a hardcoded 10 s / fixed count.
  5. Drives VnmrJ through listenon/send2Vnmr, one block at a time, waiting
     for each .fid to land before submitting the next.

WHAT IT DOES NOT DO
  Touch the probe, tune, match, shim, or calibrate. Do all that yourself and
  leave the console on a good 1H setup before running this.

PRECONDITIONS
  * In VnmrJ, join the experiment you set up, then type:  listenon
    (that writes $vnmruser/.talk, which is how this script talks to VnmrJ)
  * SHIM ON THE TUBE YOU ARE ABOUT TO RUN, and CHECK THE LINESHAPE. Take one
    short pulsed reference and look at the water linewidth before committing
    to a long session. This is not boilerplate: a shim set optimised on a
    different sample can leave the line several times broader, and nothing in
    the procpar shows it except the shim values themselves. We lost a 10 h
    run to exactly that -- the line was 27 Hz where the same tube had given
    9 Hz two days earlier, and the spin-noise feature came out 4x smaller and
    3x broader in proportion.
    Beware also that a concentrated (near-neat H2O) sample CANNOT diagnose a
    shim: radiation damping dominates its linewidth, so the line barely
    responds to the shims. Judge the shim on a dilute or doped tube, or on
    the lock level -- never on the water line of a near-neat sample.
  * Optionally save the result so the session can reload it:  svs('<name>')
    A saved shim set belongs to the sample it was optimised on. This script
    offers to reload one but NEVER defaults to yes, and tells you how old the
    file is.
  * Lock as you want it -- the state is recorded either way.
  * pw90 calibrated -- you will be asked for it.

USAGE
    python spin_noise_driver.py --dry-run   # prints every command, sends nothing
    python spin_noise_driver.py             # for real

    --bracket            split the gain ladder across the session (half at the
                         start, half at the end) so receiver compression and
                         session drift can be separated
    --ladder-visits N    N visits per gain level; N>1 randomizes the order.
                         N=3 randomized is preferred over a plain up/down
                         bracket -- repeats make drift identifiable and
                         randomizing handles NON-linear drift too.
    --tuning-ladder K    after the standard session, K tuning settings x 2
                         no-pulse blocks, role "noise_tune". YOU retune by hand
                         between settings; the script waits. Needs v0.7.1 or
                         later (that is where the role and the tuning block
                         entered the schema).
    --grace S            extra wait after a block timeout (default 120 s)
    --retries N          extra attempts per block before giving up (default 2)
    --continue-on-fail   skip a failed noise block instead of ending the run
    --tof-test           two references at tof and tof+shift (checklist item 2)

LONG RUNS
    An 8 h session is ~960 blocks and takes ~10 h of wall clock (there is
    roughly 8 s of overhead per block on top of `at`). For anything of that
    length use:

        screen -S spinnoise          # so a dropped ssh does not kill the run
        python spin_noise_driver.py --bracket --continue-on-fail

    answers.json is written BEFORE acquisition and PRUNED afterwards to the
    experiments that actually landed, so a partial session packs as-is with no
    hand-editing.

ALWAYS --dry-run FIRST on a new console: it prints the exact command stream
without sending anything, which is the only way to inspect it without a live
VnmrJ. The wexp/au chaining itself is exercised (66/66 and 70/70 blocks on a
VnmrJ 3.2 DD2), but if a block does not save on a new console that is still
the first place to look.

WHAT HAS AND HAS NOT RUN ON HARDWARE
  Exercised: the default session, and one bracketed ladder (SIU DD2,
  VnmrJ 3.2, sessions 2-3), plus a 966-block 10 h run with zero failures.
  NOT yet exercised on a console: the randomized ladder (--ladder-visits > 1),
  the tuning ladder, --tof-test, and the retry / timeout / disarm paths.
"""

import os
import sys

if sys.version_info[0] != 2:
    sys.stderr.write(
        "This driver targets the Python 2.6 that VnmrJ-era consoles ship.\n"
        "Run it with that interpreter, e.g. /usr/bin/python2.6 %s\n"
        % (sys.argv[0] if sys.argv else "spin_noise_driver.py"))
    raise SystemExit(2)

import json
import time
import math
import random
import re
import subprocess

# Session directory. Defaults to <vnmruser>/casper, which is wherever VnmrJ
# keeps this operator's tree -- so the script works on any console without
# editing. CASPER_DIR overrides it (also used by the test suite).
def _default_casper_dir():
    base = os.environ.get("vnmruser", "")
    if not base:
        base = os.path.expanduser("~/vnmrsys")
    return os.path.join(base, "casper")


CASPER_DIR = os.environ.get("CASPER_DIR", _default_casper_dir())
SHIM_FILE = os.path.join(CASPER_DIR, "caspershims")
SEND2VNMR = "/vnmr/bin/send2Vnmr"
RUN_PROFILE = os.path.join(CASPER_DIR, "last_run.json")
LEGACY_PROFILE = os.path.join(CASPER_DIR, "last_sample.json")
NP_MAX = 524288            # procpar-declared maximum for np on this console
DEF_SW = 6410.25641026
VNMRREV = "/vnmr/vnmrrev"  # copied into each session so vnmrj_version auto-fills

# A 50 ns pulse delivers only ~76% of nominal on this console, so the default
# reference pulse is four times longer and 12 dB down -- same 1 deg tip, far
# less dependent on pulse rise/fall.
DEF_REF_PW = 0.2           # us
REF_ATTEN_DB = 12.0        # dB below the pw90 calibration power

DRY = ("--dry-run" in sys.argv) or ("-n" in sys.argv)

# --bracket repeats the gain ladder in DESCENDING order at the end of the
# session. Comparing within one ladder measures receiver compression;
# comparing the opening and closing ladders at the same gain measures drift
# over the whole session. A single ascending ladder cannot separate the two --
# it runs low-to-high in time order, so compression and monotonic drift are
# degenerate. Costs ~8 records, about 30 s.
BRACKET = "--bracket" in sys.argv


def _argval(flag, default):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            try:
                return int(sys.argv[i + 1])
            except ValueError:
                pass
    return default


# Long unattended runs: an 8 h session is ~960 blocks, and "stop on the first
# hiccup" is the wrong failure mode when block 700 of 960 times out at 3 a.m.
RETRIES = _argval("--retries", 2)          # extra attempts per block
KEEP_GOING = "--continue-on-fail" in sys.argv

# Maintainer's preference (2026-09-16): randomized order with three visits per
# level, rather than a plain up/down bracket. Repeats are what make drift
# identifiable, and randomizing handles NON-linear drift, which up/down cannot.
# --ladder-visits 1 keeps the original single ascending ladder.
LADDER_VISITS = _argval("--ladder-visits", 1)

# Tuning ladder: schema role "noise_tune" plus the per-experiment tuning
# block, both present from v0.7.1. Against an older schema these sessions
# acquire fine but will not pack.
TUNING_SETTINGS = _argval("--tuning-ladder", 0)
TUNE_BLOCKS_PER_SETTING = 2

# console parameters captured at startup, restored by disarm()
SAVED_STATE = None

# extra wait after a block timeout, before concluding it is dead
GRACE_S = _argval("--grace", 120)


# ----------------------------------------------------------------- prompting
def ask(prompt, default=None, cast=str):
    while True:
        if default is None:
            s = raw_input("%s: " % prompt).strip()
        else:
            s = raw_input("%s [%s]: " % (prompt, default)).strip()
            if s == "":
                s = str(default)
        if s == "":
            print("  -> required.")
            continue
        try:
            return cast(s)
        except ValueError:
            print("  -> not a valid %s." % cast.__name__)


def ask_num(prompt):
    """Optional number. Blank / none / unknown -> None, because the schema
    types these ["number","null"] and a string fails validation."""
    while True:
        t = raw_input("%s: " % prompt).strip()
        if t == "" or t.lower() in ("none", "unknown", "n/a", "na"):
            return None
        try:
            return float(t)
        except ValueError:
            print("  -> enter a number, or leave blank for none.")


def ask_yn(prompt, default=True):
    if default:
        d = "Y/n"
    else:
        d = "y/N"
    while True:
        s = raw_input("%s [%s]: " % (prompt, d)).strip().lower()
        if s == "":
            return default
        if s in ("y", "yes"):
            return True
        if s in ("n", "no"):
            return False


# ----------------------------------------------------------------- VnmrJ I/O
def talkfile():
    cands = [os.path.expanduser("~/vnmrsys/.talk"),
             os.path.expanduser("~/.talk")]
    vu = os.environ.get("vnmruser", "")
    if vu:                       # else this yields a RELATIVE ".talk"
        cands.append(os.path.join(vu, ".talk"))
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def vnmr(cmd):
    """Send one command string to the running VnmrJ."""
    if DRY:
        print("    [dry-run] %s" % cmd)
        return True
    tf = talkfile()
    if tf is None:
        print("\nERROR: no .talk file. In VnmrJ, type 'listenon', then rerun.")
        sys.exit(1)
    rc = subprocess.call([SEND2VNMR, tf, cmd])
    if rc != 0:
        print("    WARNING: send2Vnmr returned %d for: %s" % (rc, cmd))
    return rc == 0


def procpar_get(fid_dir, key):
    """Read one numeric/string parameter out of a saved .fid's procpar.
    Used to record what the console ACTUALLY applied, which is not always
    what we asked for -- gain is rounded to integer dB, for instance."""
    try:
        f = open(os.path.join(fid_dir, "procpar"))
        lines = f.read().splitlines()
        f.close()
    except Exception:
        return None
    for i in range(len(lines) - 1):
        if lines[i].split(" ")[0] == key:
            parts = lines[i + 1].split(" ", 1)
            if len(parts) > 1:
                return parts[1].split()[0].strip('"')
    return None


def read_live(names, path):
    """Ask the RUNNING VnmrJ for parameter values and read them back.
    Never parse curpar: VnmrJ flushes it lazily and it can be minutes stale."""
    if DRY:
        print("    [dry-run] would read live: %s" % ", ".join(names))
        return None
    fmt = " ".join(["%s=%%g" % n for n in names])
    vnmr("write('reset','%s') write('file','%s','%s',%s)"
         % (path, path, fmt, ",".join(names)))
    for _ in range(10):
        time.sleep(1)
        try:
            f = open(path)
            txt = f.read().strip()
            f.close()
            if txt:
                out = {}
                for tok in txt.split():
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        try:
                            out[k] = float(v)
                        except ValueError:
                            pass
                return out or None
        except IOError:
            pass
    print("    WARNING: no readback from VnmrJ for %s" % ", ".join(names))
    return None


def disarm(saved=None):
    """Leave the joined experiment safe to use.

    Every block sets wexp='svf(<session>/<name>)'. If that is left armed, the
    NEXT au anyone runs in this experiment saves into a finished session
    folder, onto a name that already exists -- svf collision behaviour is
    unverified and the packer reads such a folder verbatim. Clearing wexp is
    one command and removes the hazard entirely.
    """
    if talkfile() is None and not DRY:
        return                       # listenoff already run; nothing to send
    vnmr("wexp=''")
    if saved:
        pairs = []
        for k in ("pw", "tpwr", "gain", "at"):
            if k in saved:
                pairs.append("%s=%.10g" % (k, saved[k]))
        if pairs:
            vnmr(" ".join(pairs))
    print("  experiment disarmed (wexp cleared%s)"
          % (", parameters restored" if saved else ""))


def prune_answers(session, answers):
    """Drop experiments from answers.json that never actually acquired.

    answers.json is written BEFORE acquisition so a crashed session is still
    packable. The cost is that after a partial run it over-claims: it lists
    every planned expno, including ones that never happened. An answers.json
    describing experiments the bundle does not contain is precisely the kind of
    quiet inconsistency that passes --selftest and is still wrong -- and on a
    960-block run, hand-editing it is not a plan.

    Returns the number of experiments dropped. Mutates answers in place.
    """
    try:
        names = os.listdir(session)
    except OSError:
        return 0
    landed = {}
    for nm in names:
        if not nm.endswith(".fid"):
            continue
        if not os.path.exists(os.path.join(session, nm, "fid")):
            continue        # directory exists but the data never arrived
        head = nm.split("_")[0]
        try:
            landed[int(head)] = True
        except ValueError:
            continue

    present = [e for e in answers["experiments"] if e["expno"] in landed]
    dropped = len(answers["experiments"]) - len(present)
    if dropped:
        answers["experiments"] = present
        answers["calibration"]["rg_ladder"] = [
            r for r in answers["calibration"]["rg_ladder"]
            if r["expno"] in landed]
    return dropped


def wait_for(path, timeout_s, label):
    """Block until <path>.fid has appeared and stopped growing."""
    target = path + ".fid"
    fidfile = os.path.join(target, "fid")
    if DRY:
        print("    [dry-run] would wait up to %ds for %s" % (timeout_s, target))
        return True
    t0 = time.time()
    last = -1
    stable = 0
    while time.time() - t0 < timeout_s:
        time.sleep(2)
        if (os.path.isdir(target) and os.path.exists(fidfile)
                and os.path.exists(os.path.join(target, "procpar"))):
            sz = os.path.getsize(fidfile)
            if sz <= 32:            # header only, or truncated -- not landed
                last = sz
                continue
            if sz == last and sz > 0:
                stable = stable + 1
                if stable >= 2:
                    print("    saved %s (%d bytes, %.0f s)"
                          % (label, sz, time.time() - t0))
                    return True
            else:
                stable = 0
            last = sz
    print("    TIMEOUT after %ds waiting for %s" % (timeout_s, target))
    print("    Acquisition may still be running. Check VnmrJ before continuing.")
    return False


# ----------------------------------------------------------------- interview
def load_profile():
    """Return {'sample':..,'acq':..,'env':..}. Falls back to the older
    sample-only last_sample.json so an existing install keeps working."""
    try:
        p = json.load(open(RUN_PROFILE))
        if "sample" in p:
            if "acq" not in p:
                p["acq"] = {}
            if "env" not in p:
                p["env"] = {}
            if "facility" not in p:
                p["facility"] = {}
            return p
    except Exception:
        pass
    try:
        return {"sample": json.load(open(LEGACY_PROFILE)), "acq": {},
                "env": {}, "facility": {}}
    except Exception:
        return None


def show_profile(p):
    s = p.get("sample", {})
    a = p.get("acq", {})
    e = p.get("env", {})
    print("\nLast run used:")
    print("  sample      : %s" % s.get("description"))
    print("  H2O / D2O   : %s%% / %s%%" % (s.get("h2o_fraction_pct"),
                                           s.get("d2o_pct")))
    print("  additives   : %s" % s.get("additives"))
    print("  volume / VT : %s uL / %s K" % (s.get("sample_volume_ul"),
                                            s.get("vt_setpoint_k")))
    if a:
        print("  pw90        : %s us at %s dB"
              % (a.get("p90_us"), a.get("p90_tpwr", a.get("tpwr"))))
        print("  ref pulse   : %s us at %s dB"
              % (a.get("ref_pw", DEF_REF_PW), a.get("ref_tpwr", "(derived)")))
        print("  max gain    : %s dB" % a.get("maxgain"))
        print("  sw / record : %s Hz / %s s" % (a.get("sw"), a.get("want_at")))
        print("  target      : %s min of noise" % a.get("target"))
    if e:
        if e.get("locked"):
            lk = "ON"
        else:
            lk = "OFF"
        print("  lock        : %s (%s)" % (lk, e.get("field_state_notes")))


def profile_interview():
    """Returns (sample, acq_defaults, env_defaults, reuse_everything)."""
    prev = load_profile()
    if prev is None:
        print("\nNo stored profile yet; collecting sample details.")
        return (new_sample(), {}, {}, False)

    show_profile(prev)
    if ask_yn("\nSame sample as that?", True):
        acq = prev.get("acq", {})
        env = prev.get("env", {})
        if acq and ask_yn("Reuse the same acquisition settings too?", True):
            return (prev["sample"], acq, env, True)
        # same sample, but re-ask settings -- with last run's values as defaults
        return (prev["sample"], acq, env, False)

    print("\nNew sample -- collecting its details.")
    print("(acquisition settings will still default to the last run's)")
    return (new_sample(), prev.get("acq", {}), prev.get("env", {}), False)


def read_vnmrrev():
    """VnmrJ version straight from /vnmr/vnmrrev, so it is never guessed."""
    try:
        f = open(VNMRREV)
        first = f.readline().strip()
        f.close()
        return first or None
    except Exception:
        return None


def facility_interview(stored):
    """Facility and instrument identity. Asked once, then remembered -- these
    do not change between sessions, but they DO change between facilities, so
    nothing here may be hardcoded."""
    if stored:
        print("\nFacility on file:")
        print("  %s, %s, %s  (slug %s)"
              % (stored.get("institution"), stored.get("city"),
                 stored.get("country"), stored.get("facility_slug")))
        print("  contact  : %s" % stored.get("contact_email"))
        print("  console  : %s   probe: %s (%s)"
              % (stored.get("console"), stored.get("probe_string"),
                 stored.get("probe_type")))
        if ask_yn("Still correct?", True):
            return stored
        print("\nRe-entering facility details.")

    f = {}
    print("\nFacility (asked once, then remembered):")
    f["institution"] = ask("  Institution")
    f["city"] = ask("  City")
    f["country"] = ask("  Country")
    f["facility_slug"] = ask("  Facility slug (from the maintainer)")
    f["contact_email"] = ask("  Contact email")
    print("  contact_consent puts that address on the public facility")
    print("  registry -- it is the contact's call, not a default.")
    f["contact_consent"] = ask_yn("  Consent to publish the contact email?", False)
    print("\nInstrument:")
    f["console"] = ask("  Console (e.g. Agilent DD2 400)")
    f["probe_string"] = ask("  Probe (e.g. 5 mm HCN)")
    f["probe_type"] = ask("  Probe type [RT | N2-cryo | He-cryo | unknown]", "RT")
    rev = read_vnmrrev()
    if rev:
        print("  VnmrJ version, read from %s: %s" % (VNMRREV, rev))
        f["vnmrj_version"] = rev
    else:
        f["vnmrj_version"] = ask("  VnmrJ version")
    return f


def new_sample():
    s = {}
    s["description"] = ask("  Sample description")
    s["h2o_fraction_pct"] = ask("  H2O fraction (%)", None, float)
    s["d2o_pct"] = ask("  D2O fraction (%)", 100.0 - s["h2o_fraction_pct"], float)
    s["additives"] = ask("  Additives (or 'none')", "none")
    s["tube_od_mm"] = ask("  Tube OD (mm)", 5, float)
    s["sample_volume_ul"] = ask("  Sample volume (uL)", 550, float)
    s["vt_setpoint_k"] = ask("  Temperature / VT setpoint (K)", 298, float)
    return s


# ----------------------------------------------------------------- main
def main():
    print("=" * 66)
    print("  spin-noise session driver -- Agilent/Varian VnmrJ")
    if DRY:
        print("  *** DRY RUN -- no commands will be sent to VnmrJ ***")
    print("=" * 66)
    print("\nThis drives ACQUISITION. Tune, match, shim, lock and calibrate")
    print("pw90 yourself first, and type 'listenon' in VnmrJ.\n")

    if not DRY and talkfile() is None:
        print("ERROR: no .talk file found. In VnmrJ type 'listenon', then rerun.")
        sys.exit(1)

    # ---- session folder: a NEW one every run
    default_name = time.strftime("session_%Y%m%d_%H%M%S")
    name = ask("Session folder name", default_name)
    if not re.match(r"^[A-Za-z0-9_.-]+$", name):
        print("ERROR: session name must be [A-Za-z0-9_.-] only -- it is")
        print("       interpolated into a MAGICAL command string.")
        sys.exit(1)
    session = os.path.join(CASPER_DIR, name)
    if os.path.exists(session):
        print("ERROR: %s already exists. Pick another name." % session)
        sys.exit(1)

    fac = facility_interview((load_profile() or {}).get("facility"))

    sample, acq, env, reuse_all = profile_interview()

    # Back-compat: older profiles stored a single "tpwr" meaning the power the
    # pw90 was calibrated at, and always used a 0.05 us reference pulse.
    if "p90_tpwr" not in acq and "tpwr" in acq:
        acq["p90_tpwr"] = acq["tpwr"]

    if reuse_all:
        p90 = acq["p90_us"]
        p90_tpwr = acq["p90_tpwr"]
        ref_pw = acq.get("ref_pw", DEF_REF_PW)
        ref_tpwr = acq.get("ref_tpwr", p90_tpwr - REF_ATTEN_DB)
        maxgain = acq["maxgain"]
        sw = acq["sw"]
        want_at = acq["want_at"]
        target = acq["target"]
        locked = env.get("locked", False)
        fieldnt = env.get("field_state_notes", "")
        print("\nReusing stored settings (pw90 %g us at %g dB; reference pulse"
              % (p90, p90_tpwr))
        print("  %g us at %g dB; max gain %g dB; sw %g Hz; %g s records;"
              % (ref_pw, ref_tpwr, maxgain, sw, want_at))
        print("  %g min target; lock %s)" % (target, locked and "ON" or "OFF"))
        coil_k = env.get("coil_temp_k")
        preamp_k = env.get("preamp_temp_k")
        # notes genuinely differ run to run, so always ask
        opnotes = ask("\n  Operator notes for THIS run",
                      env.get("operator_notes", "none"))
    else:
        print("\nAcquisition settings:")
        p90 = ask("  Calibrated pw90 (us)", acq.get("p90_us"), float)
        p90_tpwr = ask("  tpwr the pw90 was calibrated at (dB)",
                       acq.get("p90_tpwr", 56), float)
        print("  -- reference/ladder pulse: a LONGER pulse at LOWER power gives")
        print("     the same tip more accurately; a 50 ns pulse delivers only")
        print("     about 76% of nominal on this console.")
        ref_pw = ask("  Reference/ladder pw (us)",
                     acq.get("ref_pw", DEF_REF_PW), float)
        ref_tpwr = ask("  Reference/ladder tpwr (dB)",
                       acq.get("ref_tpwr", p90_tpwr - REF_ATTEN_DB), float)
        maxgain = ask("  Max receiver gain that does NOT overflow (dB)",
                      acq.get("maxgain", 30), float)
        sw = ask("  Spectral width sw (Hz)", acq.get("sw", DEF_SW), float)
        want_at = ask("  Desired noise record length (s)",
                      acq.get("want_at", 30.0), float)
        target = ask("  Target total noise time (minutes)",
                     acq.get("target", 30.0), float)

        print("\nEnvironment:")
        locked = ask_yn("  Was the lock ON during the noise blocks?",
                        env.get("locked", False))

        # The field-state note must agree with the lock answer just given.
        # Carrying forward a stored note that contradicts it would put a
        # self-inconsistent bundle on the server -- locked=true alongside
        # "lock off, z0 stable" -- which nothing downstream would catch.
        if locked:
            implied = "lock on, z0 stable"
        else:
            implied = "lock off, z0 stable"
        stored = env.get("field_state_notes", "")
        low = stored.lower()
        if (not stored) or ("lock on" in low and not locked) \
                or ("lock off" in low and locked):
            default_fs = implied
        else:
            default_fs = stored
        fieldnt = ask("  Field/lock state notes", default_fs)

        # MEASURED temperatures, not assumed. Without these the analysis
        # cannot compute the temperature-contrast point at all. For a
        # room-temperature probe these are physical ambient readings: the coil
        # at probe-body temperature, the preamp at its own ambient -- which is
        # not necessarily the room, if it lives in a console cabinet.
        print("  Temperatures -- MEASURE these, do not assume 298:")
        coil_k = ask("    Coil / probe-body temperature (K)",
                     env.get("coil_temp_k", 298.15), float)
        preamp_k = ask("    Preamp ambient temperature (K)",
                       env.get("preamp_temp_k", coil_k), float)
        opnotes = ask("  Operator notes", "none")

    # Shims. A saved shim set belongs to the SAMPLE it was optimised on.
    # Reloading one that was optimised on a different tube is a quiet way to
    # start a long session on a line several times broader than it should be,
    # with nothing in the procpar to show for it except the shim values
    # themselves. So: never silently, never by default, and always say how old
    # the file is.
    reshim = False
    if os.path.exists(SHIM_FILE):
        age_s = time.time() - os.path.getmtime(SHIM_FILE)
        print("\nSaved shim set : %s" % SHIM_FILE)
        print("  last written : %s  (%.1f days ago)"
              % (time.strftime("%Y-%m-%d %H:%M",
                               time.localtime(os.path.getmtime(SHIM_FILE))),
                 age_s / 86400.0))
        print("  A shim set belongs to the sample it was optimised on. If it")
        print("  was saved on a different tube, do NOT reload it here.")
        reshim = ask_yn("  Reload it?", False)
    else:
        print("\nNo saved shim set at %s" % SHIM_FILE)
        print("  Using whatever shims are currently loaded on the console.")
        print("  To save one for next time: shim on THIS sample, then in")
        print("  VnmrJ run  svs('%s')" % SHIM_FILE)

    # ---- derive record length from the REAL cap, block count from duration
    at_max = NP_MAX / (2.0 * sw)
    at = min(want_at, at_max)
    np_pts = int(2 * sw * at) & ~1                  # even
    nblocks = int(math.ceil(target * 60.0 / at))

    # Tip angle scales with pulse LENGTH and with amplitude, and amplitude goes
    # as 10^(dB/20). The reference pulse need not be at the pw90's power, so
    # both factors matter -- a 0.2 us pulse 12 dB down is the same 1 deg tip as
    # 0.05 us at full power.
    tip_deg = 90.0 * (ref_pw / p90) * (10.0 ** ((ref_tpwr - p90_tpwr) / 20.0))

    # Ladder: four ascending gains ending at the operator's safe maximum.
    # INTEGER dB -- the console rounds gain to whole dB, so asking for 13.3
    # silently gets you 14 and the declared rg is then wrong by ~8%.
    gains = [float(int(round(maxgain * f)))
             for f in (0.0, 1.0 / 3, 2.0 / 3, 1.0)]

    # expno plan: ladder 10/14/15/16, ref_open 11, noise 12 then 17..,
    # ref_close 13, then any extra ladder visits and tuning blocks past
    # the noise range. Time order is read from procpar, not from expno
    # ordering -- confirmed by the maintainer.
    noise_no = [12] + range(17, 17 + nblocks - 1)
    # Must clear BOTH the noise block and the fixed expnos 10-16. With a
    # short smoke-test session (nblocks<=1) max(noise_no) is 12, and spare
    # would land on 13 -- the closing reference -- so an extra rung or the
    # first tune block would be saved beside 13_sn_ref_close.fid and the
    # packer would reject the whole session.
    spare = max([13, 16] + noise_no) + 1

    # Build the ladder schedule: LADDER_VISITS visits of each gain level.
    # One visit -> the classic ascending ladder. More than one -> randomized,
    # which is what makes non-linear drift separable from compression.
    def _group(nvisits):
        g = []
        for v in range(nvisits):
            for x in gains:
                g.append(x)
        if nvisits > 1 or LADDER_VISITS > 1:
            random.shuffle(g)
        return g

    if BRACKET:
        # EVERY level must appear in BOTH halves, or drift is not identifiable
        # and the bracket achieves nothing. An earlier revision split a single
        # pass down the middle, which gave each level exactly once, at one end
        # of the session or the other -- worse than no bracket at all, because
        # compression and drift are then fully confounded AND there is no
        # repeat to separate them. Build each half independently instead.
        per_half = max(1, (max(1, LADDER_VISITS) + 1) // 2)
        open_gains = _group(per_half)
        close_gains = _group(per_half)
    else:
        open_gains = _group(max(1, LADDER_VISITS))
        close_gains = []

    # canonical expnos for the first four opening rungs, then spares
    canon = [10, 14, 15, 16]
    ladder_no = []
    for i in range(len(open_gains)):
        if i < len(canon):
            ladder_no.append(canon[i])
        else:
            ladder_no.append(spare)
            spare = spare + 1
    close_no = []
    for i in range(len(close_gains)):
        close_no.append(spare)
        spare = spare + 1

    print("\n" + "-" * 66)
    print("  np cap %d  ->  at_max = %.2f s at sw = %.2f Hz" % (NP_MAX, at_max, sw))
    print("  record length    : %.3f s  (np = %d)" % (at, np_pts))
    print("  noise blocks     : %d  ->  %.1f min total"
          % (nblocks, nblocks * at / 60.0))
    print("  ladder gains     : %s dB (integer -- console rounds)"
          % (", ".join([("%g" % g) for g in gains])))
    if BRACKET:
        print("  closing ladder   : same gains descending, expno %s"
              % (", ".join([str(n) for n in close_no])))
    print("  reference pulse  : %g us at %g dB  ->  tip %.3f deg"
          % (ref_pw, ref_tpwr, tip_deg))
    print("                     (pw90 %g us at %g dB)" % (p90, p90_tpwr))
    print("  session folder   : %s" % session)
    print("-" * 66)
    if at < want_at:
        print("  NOTE: %.1f s requested but capped to %.2f s by np<=%d."
              % (want_at, at, NP_MAX))
        print("        Narrow sw for longer records (at_max scales as 1/sw).")
    if not ask_yn("\nProceed?", True):
        print("Aborted; nothing created.")
        sys.exit(0)

    # ---- create folder + answers.json BEFORE acquiring (crash-safe)
    if not DRY:
        os.makedirs(session)
    print("\ncreated %s" % session)

    # numbering: ladder 10/14/15/16, ref_open 11, noise 12 then 17.., ref_close 13
    experiments = []
    for n in ladder_no:
        experiments.append({"expno": n, "role": "rg_ladder"})
    experiments.append({"expno": 11, "role": "reference_open"})
    for n in noise_no:
        experiments.append({"expno": n, "role": "noise"})
    experiments.append({"expno": 13, "role": "reference_close"})
    for n in close_no:
        experiments.append({"expno": n, "role": "rg_ladder"})
    experiments.sort(key=lambda e: e["expno"])

    rg_ladder = []
    for i in range(len(ladder_no)):
        rg_ladder.append({"expno": ladder_no[i],
                          "rg": round(10.0 ** (open_gains[i] / 20.0), 6),
                          "tip_deg": round(tip_deg, 4)})
    for i in range(len(close_no)):
        rg_ladder.append({"expno": close_no[i],
                          "rg": round(10.0 ** (close_gains[i] / 20.0), 6),
                          "tip_deg": round(tip_deg, 4)})

    answers = {
        "_comment": "Written by casper_run.py before acquisition; safe to pack even if the session aborted.",
        "vendor": "agilent",
        "run_mode": "external-acquisition",
        "facility": {"institution": fac["institution"],
                     "city": fac["city"],
                     "country": fac["country"],
                     "facility_slug": fac["facility_slug"],
                     "contact_email": fac["contact_email"],
                     "contact_consent": fac["contact_consent"]},
        "sample": sample,
        "environment": {"locked": locked, "operator_notes": opnotes},
        "spectrometer": {"probe_type": fac["probe_type"],
                         "console": fac["console"],
                         "probe_string": fac["probe_string"],
                         "coil_temp_k": coil_k,
                         "preamp_temp_k": preamp_k},
        "instrument": {"vnmrj_version": fac["vnmrj_version"],
                       "spectrometer_model": fac["console"],
                       "field_state_notes": fieldnt},
        "calibration": {"p90_us": p90,
                        "p90_power_db_or_w": "%g dB (tpwr)" % p90_tpwr,
                        "topshim_ok": False,
                        "rg_ladder": rg_ladder},
        "experiments": experiments,
    }
    if not DRY:
        json.dump(answers, open(os.path.join(session, "answers.json"), "w"), indent=2)
        # /vnmr/vnmrrev into the session so the packer can auto-fill
        # instrument.vnmrj_version instead of trusting an operator-typed string
        try:
            src = open(VNMRREV).read()
            dst = open(os.path.join(session, "vnmrrev"), "w")
            dst.write(src)
            dst.close()
            print("copied %s into the session" % VNMRREV)
        except Exception, e:                                    # noqa (py2)
            print("NOTE: could not copy %s (%s)" % (VNMRREV, e))
        # remember the WHOLE run so a repeat is just Enter-Enter
        json.dump({"sample": sample,
                   "acq": {"p90_us": p90, "p90_tpwr": p90_tpwr,
                           "ref_pw": ref_pw, "ref_tpwr": ref_tpwr,
                           "maxgain": maxgain,
                           "sw": sw, "want_at": want_at, "target": target},
                   "env": {"locked": locked, "field_state_notes": fieldnt,
                           "operator_notes": opnotes,
                           "coil_temp_k": coil_k, "preamp_temp_k": preamp_k},
                   "facility": fac,
                   "_saved": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "_session": name},
                  open(RUN_PROFILE, "w"), indent=2)
    print("wrote answers.json (%d experiments) and saved the run profile\n"
          % len(experiments))

    log = []

    def block(label, expno, cmd, seconds):
        path = os.path.join(session, "%d_%s" % (expno, label))
        tag = "%d_%s" % (expno, label)
        print("  [%s] expno %d" % (label, expno))
        ok = False
        for attempt in range(RETRIES + 1):
            if attempt:
                # Only retry if NOTHING landed. If a .fid directory exists we
                # leave it alone: svf collision behaviour on 3.2 is unverified
                # (checklist item 7), so a second svf to the same name could
                # make a mess rather than a clean overwrite.
                if os.path.isdir(path + ".fid"):
                    print("    %s.fid exists but looked incomplete -- not "
                          "retrying (svf collision behaviour unverified)" % tag)
                    break
                print("    retry %d of %d" % (attempt, RETRIES))
            vnmr("%s wexp='svf(\\'%s\\')' au" % (cmd, path))
            ok = wait_for(path, int(seconds * 1.5) + 120, tag)
            if ok:
                break

            # A timeout does NOT mean the block is dead -- it may just be late
            # (console busy, queued, paused). The experiment still has
            # wexp='svf(<this path>)' armed, so a late completion would save
            # correctly. But if we submit ANOTHER au now, that same late
            # acquisition can complete against the NEW wexp and be saved under
            # the next block's name; wait_for would accept it and the real next
            # block would collide or be lost.
            #
            # So: grace period first, and if it is still absent, clear wexp so
            # nothing can be saved under a stale or future name.
            print("    grace period: %ds more before giving up on %s"
                  % (GRACE_S, tag))
            if wait_for(path, GRACE_S, tag):
                ok = True
                break
            print("    still nothing; clearing wexp so a late acquisition")
            print("    cannot save under the wrong name")
            vnmr("wexp=''")
        log.append((expno, label, ok, time.strftime("%Y-%m-%dT%H:%M:%S")))
        return ok

    # ---- preamble: cryo_noisetest idiom -- autoshim/autolock off, no spin
    print("Preamble")
    if reshim:
        vnmr("rts('%s')" % SHIM_FILE)
    vnmr("wshim='n' alock='n' in='n' spin='n'")

    at_ref = 2.0
    ok = True

    print("\nGain ladder (%d blocks, tiny flip)%s"
          % (len(ladder_no), " -- RANDOMIZED order" if LADDER_VISITS > 1 else ""))
    open_lbls = []
    for i in range(len(ladder_no)):
        lbl = "sn_ladder_%02d" % i
        open_lbls.append(lbl)
        ok = block(lbl, ladder_no[i],
                   "pw=%g tpwr=%g nt=1 ss=0 pad=0 gain=%g at=%g"
                   % (ref_pw, ref_tpwr, open_gains[i], at_ref), at_ref) and ok
        if not ok:
            break

    # Record what the console ACTUALLY applied. It rounds gain to integer dB
    # and may clamp pw, so the target values we asked for are not necessarily
    # what ran -- and a wrong rg makes the linearity fit wrong downstream.
    def readback(expnos, asked, offset, labels):
        changed = []
        for i in range(len(expnos)):
            d = os.path.join(session, "%d_%s" % (expnos[i], labels[i]) + ".fid")
            g = procpar_get(d, "gain")
            w = procpar_get(d, "pw")
            if g is None:
                continue
            g = float(g)
            e = rg_ladder[offset + i]
            e["rg"] = round(10.0 ** (g / 20.0), 6)
            e["gain_db_actual"] = g
            if w is not None:
                e["tip_deg"] = round(
                    90.0 * (float(w) / p90)
                    * (10.0 ** ((ref_tpwr - p90_tpwr) / 20.0)), 4)
            if abs(g - asked[i]) > 1e-9:
                changed.append("expno %d: asked %g dB, console applied %g dB"
                               % (expnos[i], asked[i], g))
        if changed:
            print("  NOTE: console adjusted the ladder --")
            for c in changed:
                print("        %s" % c)
            print("        answers.json records the ACTUAL values.")
        json.dump(answers, open(os.path.join(session, "answers.json"), "w"),
                  indent=2)

    if ok and not DRY:
        readback(ladder_no, open_gains, 0, open_lbls)

    if ok:
        print("\nOpening reference")
        ok = block("sn_ref_open", 11,
                   "pw=%g tpwr=%g nt=1 ss=0 pad=0 gain=%g at=%g"
                   % (ref_pw, ref_tpwr, max(gains), at_ref), at_ref)

    if ok:
        print("\nNoise blocks (%d x %.1f s = %.1f min) -- no pulse"
              % (nblocks, at, nblocks * at / 60.0))
        if KEEP_GOING:
            print("  --continue-on-fail: a failed block is skipped, not fatal")
        t_noise = time.time()
        failures = 0
        for i in range(len(noise_no)):
            # progress + ETA matter when this runs for ten hours unattended
            if i:
                rate = (time.time() - t_noise) / i
                eta = rate * (len(noise_no) - i)
                print("  block %d/%d   elapsed %.0f min, ETA %.0f min"
                      % (i + 1, nblocks, (time.time() - t_noise) / 60.0,
                         eta / 60.0))
            else:
                print("  block 1/%d" % nblocks)
            ok = block("sn_noise", noise_no[i],
                       "pw=0 tpwr=-16 nt=1 ss=0 pad=0 gain=%g at=%g wshim='n' alock='n'"
                       % (max(gains), at), at)
            if not ok:
                failures = failures + 1
                if KEEP_GOING:
                    print("  block %d FAILED -- continuing (%d failed so far)"
                          % (noise_no[i], failures))
                    ok = True
                else:
                    print("  stopping noise loop; %d of %d blocks saved"
                          % (i, nblocks))
                    print("  (rerun with --continue-on-fail to skip bad blocks)")
                    break
        if failures:
            print("  %d noise block(s) failed out of %d" % (failures, nblocks))

    if ok:
        print("\nClosing reference (identical to the opening one)")
        # Deliberately identical to expno 11. An earlier revision of the
        # operator quickstart specified pw=0.11 / tpwr=57 here, contradicting
        # "identical to step 2" and producing mismatched references
        # (1.0 deg vs 2.25 deg) in the first validation session.
        ok = block("sn_ref_close", 13,
                   "pw=%g tpwr=%g nt=1 ss=0 pad=0 gain=%g at=%g"
                   % (ref_pw, ref_tpwr, max(gains), at_ref), at_ref)

    if ok and BRACKET:
        print("\nClosing gain ladder (descending) -- brackets the session so")
        print("receiver compression and session-long drift can be separated")
        close_lbls = []
        for i in range(len(close_no)):
            lbl = "sn_ladder_close_%02d" % i
            close_lbls.append(lbl)
            ok = block(lbl, close_no[i],
                       "pw=%g tpwr=%g nt=1 ss=0 pad=0 gain=%g at=%g"
                       % (ref_pw, ref_tpwr, close_gains[i], at_ref),
                       at_ref) and ok
            if not ok:
                break
        if ok and not DRY:
            readback(close_no, close_gains, len(ladder_no), close_lbls)

    if not DRY:
        dropped = prune_answers(session, answers)
        if dropped:
            print("\n  pruned answers.json: dropped %d experiment(s) that never"
                  " acquired" % dropped)
            print("  it now describes exactly what is on disk -- packable as is")
        json.dump(answers, open(os.path.join(session, "answers.json"), "w"),
                  indent=2)

    # ---- tuning ladder (schema role "noise_tune", spec fixed 2026-09-16)
    #
    # Two noise blocks at each of several tuning settings around the operator's
    # normal one. The probe has no autotune, so RETUNING IS PHYSICAL and the
    # operator does it -- we stop and wait at each setting. The standard
    # session above stays in the same bundle, at normal tuning, and provides
    # the headline co-add; these blocks are deliberately kept out of it.
    if ok and TUNING_SETTINGS > 0:
        print("\n" + "=" * 66)
        print("  TUNING LADDER -- %d settings x %d noise blocks"
              % (TUNING_SETTINGS, TUNE_BLOCKS_PER_SETTING))
        print("=" * 66)
        print("\n  You will retune the probe BY HAND between settings.")
        print("  Work around your normal tuning, and record the dial/meter")
        print("  readings as you go -- they are what make the scan analysable.")
        print("\n  NOTE: role 'noise_tune' is NOT in the v0.6.0 schema. This")
        print("  session will NOT pack until v0.7.1 is released. Acquire now,")
        print("  pack later; the raw data is what matters.\n")

        tune_no = []
        for k in range(TUNING_SETTINGS):
            print("-" * 66)
            print("  SETTING %d of %d" % (k + 1, TUNING_SETTINGS))
            print("-" * 66)
            label = ask("  Label for this setting (e.g. 'normal', '-2 turns')")
            # schema types these ["number","null"] with additionalProperties
            # false -- a string here makes the whole session unpackable
            tune_r = ask_num("  Tune reading (number, or blank for none)")
            match_r = ask_num("  Match reading (number, or blank for none)")
            units = ask("  Units of those readings", "arb")
            note = ask("  Note (optional)", "none")
            raw_input("\n  Set the probe to this tuning, then press Enter... ")

            for j in range(TUNE_BLOCKS_PER_SETTING):
                expno = spare
                spare = spare + 1
                lbl = "sn_tune_%d" % k
                print("  setting %d, block %d/%d"
                      % (k + 1, j + 1, TUNE_BLOCKS_PER_SETTING))
                good = block(lbl, expno,
                             "pw=0 tpwr=-16 nt=1 ss=0 pad=0 gain=%g at=%g "
                             "wshim='n' alock='n'" % (max(gains), at), at)
                tune_no.append(expno)
                entry = {"expno": expno, "role": "noise_tune",
                         "tuning": {"setting_index": k,
                                    "label": label,
                                    "tune_reading": tune_r,
                                    "match_reading": match_r,
                                    "units": units,
                                    "note": note}}
                answers["experiments"].append(entry)
                if not good and not KEEP_GOING:
                    print("  block failed; stopping the tuning ladder")
                    break
            answers["experiments"].sort(key=lambda e: e["expno"])
            if not DRY:
                json.dump(answers,
                          open(os.path.join(session, "answers.json"), "w"),
                          indent=2)

        print("\n  Tuning ladder finished.")
        print("  *** RESTORE THE PROBE TO YOUR NORMAL TUNING before the next")
        print("      session -- the probe is left at setting %d. ***"
              % TUNING_SETTINGS)

    done = len([r for r in log if r[2]])
    print("\n" + "=" * 66)
    print("  %d / %d blocks saved into %s" % (done, len(log), session))
    if done < len(log):
        print("  INCOMPLETE -- but answers.json has been pruned to match,")
        print("  so the session can be packed without hand-editing.")
    print("=" * 66)
    if not DRY:
        f = open(os.path.join(session, "casper_run_log.txt"), "w")
        for rec in log:
            f.write("%s\t%d_%s\t%s\n"
                    % (rec[3], rec[0], rec[1], rec[2] and "OK" or "FAILED"))
        f.close()
    print("\nNext: copy %s to the packing machine, then" % session)
    print("  python3 packer/pack_bundle.py <dir> --answers <dir>/answers.json --vendor agilent")


def tof_test():
    """Vendor checklist item 2: which way does the line move when tof rises?

    Two 1D references, one at the current tof and one at tof+SHIFT, saved side
    by side so the sign is settled from the data rather than by eye. Fully
    automatic -- tof is a console parameter, nothing physical is touched.
    """
    print("=" * 66)
    print("  tof sign test -- vendor checklist item 2")
    if DRY:
        print("  *** DRY RUN -- nothing sent to VnmrJ ***")
    print("=" * 66)
    print("\nTwo short references, at tof and tof+shift. The transmitter is")
    print("pulsed at the ordinary tiny reference flip; nothing else changes.")
    print("tof is restored afterwards.\n")

    if not DRY and talkfile() is None:
        print("ERROR: no .talk file. In VnmrJ type 'listenon', then rerun.")
        sys.exit(1)

    prev = load_profile() or {}
    acq = prev.get("acq", {})
    if "p90_tpwr" not in acq and "tpwr" in acq:
        acq["p90_tpwr"] = acq["tpwr"]

    name = ask("Session folder name",
               time.strftime("toftest_%Y%m%d_%H%M%S"))
    session = os.path.join(CASPER_DIR, name)
    if os.path.exists(session):
        print("ERROR: %s already exists." % session)
        sys.exit(1)

    ref_pw = ask("  Reference pw (us)", acq.get("ref_pw", DEF_REF_PW), float)
    ref_tpwr = ask("  Reference tpwr (dB)",
                   acq.get("ref_tpwr", acq.get("p90_tpwr", 56) - REF_ATTEN_DB),
                   float)
    gain = ask("  Receiver gain (dB, integer)", int(acq.get("maxgain", 30)), int)
    at_ref = ask("  Acquisition time (s)", 2.0, float)
    shift = ask("  tof shift (Hz)", 200.0, float)

    print("\n  session: %s" % session)
    if not ask_yn("Proceed?", True):
        print("Aborted; nothing created.")
        sys.exit(0)
    if not DRY:
        os.makedirs(session)

    base = "pw=%g tpwr=%g nt=1 ss=0 pad=0 gain=%d at=%g" % (
        ref_pw, ref_tpwr, gain, at_ref)

    def shot(label):
        path = os.path.join(session, label)
        vnmr("%s wexp='svf(\\'%s\\')' au" % (base, path))
        return wait_for(path, int(at_ref * 1.5) + 120, label)

    # Capture the LIVE tof before touching anything, so the restore can be
    # absolute. A relative "tof=tof-shift" is wrong whenever the +shift never
    # landed: it would leave the console `shift` Hz BELOW where the operator
    # set it, while printing "restoring tof".
    live = read_live(["tof"], os.path.join(session, "tof0.txt"))
    tof0 = live.get("tof") if live else None
    if tof0 is None:
        if not DRY:
            print("\nERROR: could not read the live tof. Refusing to shift it,")
            print("       because the restore could not then be verified.")
            sys.exit(1)
        tof0 = 0.0          # dry-run stand-in so the arithmetic is printable
        print("\n  live tof before the test: (dry-run, using 0.0 as a stand-in)")
    else:
        print("\n  live tof before the test: %.4f Hz" % tof0)

    shifted = False
    try:
        print("\n1/2  at the current tof")
        ok = shot("tof_base")

        if ok:
            print("\n2/2  at tof + %g Hz" % shift)
            if vnmr("tof=%.10g" % (tof0 + shift)):
                shifted = True
                ok = shot("tof_plus")
            else:
                print("    tof shift was not accepted; not acquiring")
                ok = False
    finally:
        # restore ONLY if we actually moved it, and to an ABSOLUTE value
        if shifted:
            print("\nrestoring tof to %.4f" % tof0)
            vnmr("tof=%.10g" % tof0)
            back = read_live(["tof"], os.path.join(session, "tof1.txt"))
            if back is not None:
                got = back.get("tof")
                if got is not None and abs(got - tof0) < 0.01:
                    print("  VERIFIED: tof is back at %.4f" % got)
                else:
                    print("  *** WARNING: tof reads %s, expected %.4f ***"
                          % (got, tof0))
                    print("  *** Set it by hand in VnmrJ before acquiring. ***")
        else:
            print("\ntof was never shifted; nothing to restore")

    if not DRY and ok:
        t1 = procpar_get(os.path.join(session, "tof_base.fid"), "tof")
        t2 = procpar_get(os.path.join(session, "tof_plus.fid"), "tof")
        print("\n  tof recorded in procpar: base %s -> shifted %s" % (t1, t2))
        f = open(os.path.join(session, "tof_test_notes.txt"), "w")
        f.write("tof sign test (vendor checklist item 2)\n")
        f.write("requested shift : +%g Hz\n" % shift)
        f.write("tof base        : %s\n" % t1)
        f.write("tof shifted     : %s\n" % t2)
        f.write("live tof before : %s\n" % tof0)
        f.write("live tof after  : %s\n"
                % (back.get("tof") if ("back" in dir() and back) else "unread"))
        f.write("pw=%g tpwr=%g gain=%d at=%g\n" % (ref_pw, ref_tpwr, gain, at_ref))
        f.write("\nAnalyse: FFT both, compare the water line position. If the\n")
        f.write("line moves in the SAME direction as tof, the frequency axis\n")
        f.write("follows the Bruker o1 convention; if it moves the other way,\n")
        f.write("the adapter needs a sign flip on o1_hz.\n")
        f.close()

    print("\n%s -- copy %s off and send it for analysis."
          % ("done" if ok else "INCOMPLETE", session))


if __name__ == "__main__":
    try:
        try:
            if "--tof-test" in sys.argv:
                tof_test()
            else:
                main()
        finally:
            # runs on success, on error and on Ctrl-C
            disarm(SAVED_STATE)
    except KeyboardInterrupt:
        print("\n\nInterrupted. VnmrJ may still be running a block -- check the console.")
        sys.exit(1)
