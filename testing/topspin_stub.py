# -*- coding: utf-8 -*-
# ============================================================================
# testing/topspin_stub.py -- Jython 2.7 stand-in for TopSpin's TopCmds API
# ============================================================================
#
# Used by testing/jython_entry.py, which registers this module as
# sys.modules["TopCmds"] BEFORE execfile()'ing topspin/spin_noise_run.py.
# The script's `from TopCmds import *` then succeeds, so it runs with
# IN_TOPSPIN = 1 -- i.e. the REAL java.util.zip / java.security.MessageDigest
# / jarray code paths execute under a real Jython interpreter.  Only the
# TopSpin API surface is stubbed; nothing else is mocked.
#
# Behavioral-fidelity notes (each mirrors real TopSpin):
#   * Every string handed to the script (dialog answers, GETPAR values,
#     CURDATA elements) is UNICODE, because Jython coerces java.lang.String
#     to unicode.  This is exactly what the embedded interpreter returns,
#     and it is what plain-str desk stubs can never catch (str() on a
#     non-ASCII unicode raises UnicodeEncodeError under Jython/py2).
#   * WR() writes a copy of the CURRENT dataset to the target name/expno
#     (TopSpin semantics: `wr` saves the current data), files and
#     parameters both.  RE() switches the current dataset and raises if
#     the target does not exist.  CURDATA() returns the new 4-element
#     shape [name, expno, procno, data_dir].
#   * GETPAR/PUTPAR operate on the CURRENT dataset's parameter set, keyed
#     by the same name encoding the script uses ("TD", "1 TD", "P 1", ...).
#   * INPUT_DIALOG / SELECT / CONFIRM answers come from per-run fixture
#     dicts keyed by dialog title (exact match preferred, then longest
#     substring).  Any dialog without a scripted answer is recorded in
#     UNSCRIPTED -- the harness fails the run on that, because in a clean
#     SIMULATE/DESKTEST run every dialog is known and no degradation path
#     should fire.
#   * XCMD() and ZG() must NEVER be reached in SIMULATE/DESKTEST (the
#     script's hw_skip() guard mocks hardware first); if they are reached
#     the stub records a GUARD BREACH and the harness fails.
#
# Jython 2.7 only.  Do not import this under Python 3.
# ============================================================================

import os

# Names exported to the script via `from TopCmds import *` (and injected
# into __builtin__ by jython_entry.py).  Exactly the documented TopSpin
# API surface spin_noise_run.py touches, plus close neighbours it names --
# plus the two HARNESS_* clock hooks, which are NOT TopSpin API: they are
# the harness's virtual wall clock (see the clock-audit section below).
# In production TopSpin they do not exist and the script's NameError
# guards skip them.
__all__ = [
    "MSG", "ERRMSG", "CONFIRM", "SELECT", "INPUT_DIALOG", "VIEWTEXT",
    "SHOW_STATUS", "XCMD", "WAIT_TILL_DONE", "GETPAR", "GETPARSTAT",
    "PUTPAR", "GETACQUDIM", "CURDATA", "RE", "WR", "RE_PATH", "EXIT",
    "SLEEP", "ZG",
    "HARNESS_WALL_MS", "HARNESS_ADVANCE_S",
]

WAIT_TILL_DONE = 0            # sentinel; value irrelevant, only the name

# ---------------------------------------------------------------------------
# Harness state (inspected by jython_entry.py after the run)
# ---------------------------------------------------------------------------
LOG = []          # every API call, in order: (api, summary_string)
UNSCRIPTED = []   # dialogs that had no fixture answer (harness: FAIL)
BREACHES = []     # hardware-guard breaches: XCMD/ZG reached (harness: FAIL)
ERRMSGS = []      # every ERRMSG (a crash dialog in a clean run: FAIL)
MSGS = []         # every MSG (title, message)
PUTPAR_FAILURES = []   # (name, value, message) per PUTPAR the stub rejected

# Console flavor the stub models (HARNESS_TS_FLAVOR).  Common to ALL
# flavors, per Bruker's documentation and public TopSpin scripts: PARMODE
# is written by enum NAME only (1D..8D; the ordinal "1" that the v0.7.2
# script wrote is rejected with TopSpin 4.4.0's exact text -- and no
# version is documented to accept it), GETPAR("PARMODE") returns the
# ORDINAL ("1" = 2D), and GETACQUDIM() returns the dimensionality.
#   "legacy"      : otherwise permissive (2.x/3.x model).
#   "ts44"        : TopSpin 4.4.0 as observed at Torino on 2026-09-18 --
#                   "1 FnMODE" absent from the F1 parameter map ("1 FnMODE:
#                   parameter not found in map") while "1 TD" goes through.
#   "ts44-stale"  : the leading explanation of that error -- the F1 map is
#                   unavailable after a PARMODE change until the dataset
#                   is RE()-loaded again; F1 writes raise, F1 reads are
#                   empty, until the script reloads.
#   "ts44-strict" : the enum NAME is rejected too (models 'the 4.4.0 name
#                   is not what we think'): the script must fall back to
#                   the operator exactly once, at the attended probe, and
#                   later datasets must inherit 2D via WR().  Answering
#                   the 'make dataset 2D' CONFIRM performs the operator's
#                   parmode (sets PARMODE) as a fixture side effect.
#   "ts44-f1echo" : GETPAR with the "1 " prefix echoes the DIRECT TD (a
#                   console ignoring the axis prefix on reads); the script
#                   must distrust that readback and continue unverified,
#                   without any dialog.
#   "ts44-dimlie" : the dimensionality readback lies (GETPAR("PARMODE")
#                   empty, GETACQUDIM() always 1) although the write was
#                   accepted; the script must ask the operator at most twice
#                   (at the probe) and then trust its writes silently.
#   "ts44-f1route": PUTPAR with the "1 " prefix is routed to the DIRECT TD;
#                   the script must detect and undo that, ask the operator
#                   (the fixture answer sets F1 TD as the operator would),
#                   and warn at the probe that the step will recur.
#   "ts44-f1mismatch": GETPAR("1 TD") returns rows-1 after an accepted
#                   write; bounded operator loop (two confirmations), then
#                   F1 TD writes are trusted silently.
#   In "legacy", GETACQUDIM does not exist (old TopSpin), so the
#   GETPAR("PARMODE") ordinal path is what gets exercised there.
# A rejected PUTPAR raises a Java exception, as the console does
# (bruker.bio.root.except.MfrException), and is recorded in
# PUTPAR_FAILURES: on the console each one is a stray error dialog,
# possibly modal in an unattended run.  The acceptance of PUTPAR("PARMODE",
# "2D") on 4.4.0 itself is documented but not yet console-confirmed; the
# first v0.7.3 bundle from Torino (meta.json software.param_api) settles it.
FLAVOR = ["legacy"]
_TS44_PARMODE_NAMES = (u"1D", u"2D", u"3D", u"4D", u"5D", u"6D", u"7D", u"8D")
_TS44_F1_MAP = (u"TD", u"SW", u"SWH", u"SFO1", u"BF1", u"O1", u"NUC1",
                u"IN_F", u"ND0", u"FnTYPE")     # FnMODE deliberately absent
_PARMODE_TO_ORDINAL = {u"1D": u"0", u"2D": u"1", u"3D": u"2"}
_F1_FRESH = {}    # dsdir -> 1 once RE()-loaded after its last PARMODE write

_CUR = [None]             # current dataset, CURDATA()-shaped list
_PARAMS = {}              # dataset dir -> {param name: unicode value}
_TEMPLATE_PARAMS = {}     # seed for datasets with no parameter set yet
_DIALOG_ANSWERS = {}      # INPUT_DIALOG fixture: title -> [answers]
_SELECT_ANSWERS = {}      # SELECT fixture: title -> int
_CONFIRM_ANSWERS = {}     # CONFIRM fixture: title -> int

_MISS = ("no", "fixture", "match")   # unique sentinel


def configure(current_dataset, template_params, dialog_answers,
              select_answers, confirm_answers=None, flavor="legacy"):
    """Install the per-run fixture and reset all logs."""
    del LOG[:], UNSCRIPTED[:], BREACHES[:], ERRMSGS[:], MSGS[:]
    del PUTPAR_FAILURES[:]
    _F1_FRESH.clear()
    FLAVOR[0] = flavor
    _PARAMS.clear()
    _TEMPLATE_PARAMS.clear()
    _DIALOG_ANSWERS.clear()
    _SELECT_ANSWERS.clear()
    _CONFIRM_ANSWERS.clear()
    _CUR[0] = [_u(x) for x in current_dataset]
    for k, v in template_params.items():
        _TEMPLATE_PARAMS[k] = _u(v)
    for k, v in dialog_answers.items():
        _DIALOG_ANSWERS[_u(k)] = v
    for k, v in select_answers.items():
        _SELECT_ANSWERS[_u(k)] = v
    if confirm_answers:
        for k, v in confirm_answers.items():
            _CONFIRM_ANSWERS[_u(k)] = v


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _u(v):
    """Coerce to unicode, the type Jython gives every java.lang.String."""
    if isinstance(v, unicode):
        return v
    if isinstance(v, str):
        return v.decode("utf-8", "replace")
    return unicode(str(v))


def _b(v):
    """Byte-string for filesystem use (fixture paths are ASCII)."""
    try:
        return str(v)
    except UnicodeError:
        return v.encode("utf-8")


def _say(line):
    try:
        print "[stub] " + line
    except UnicodeError:
        print "[stub] " + line.encode("utf-8", "replace")


def _match(table, title):
    """Fixture lookup: exact title match, else longest substring key."""
    t = _u(title)
    if t in table:
        return table[t]
    best, best_len = _MISS, -1
    for k in table.keys():
        if k in t and len(k) > best_len:
            best, best_len = table[k], len(k)
    return best


def _dspath(ds):
    """EXPNO directory for a CURDATA-shaped list (both documented shapes)."""
    if len(ds) >= 5:
        return os.path.join(_b(ds[3]), "data", _b(ds[4]), "nmr",
                            _b(ds[0]), _b(ds[1]))
    return os.path.join(_b(ds[3]), _b(ds[0]), _b(ds[1]))


def _params_for(dsdir):
    if dsdir not in _PARAMS:
        _PARAMS[dsdir] = dict(_TEMPLATE_PARAMS)
    return _PARAMS[dsdir]


def _copy_tree(src, dst):
    if not os.path.isdir(dst):
        os.makedirs(dst)
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.isdir(s):
            _copy_tree(s, d)
        else:
            fi = open(s, "rb")
            data = fi.read()
            fi.close()
            fo = open(d, "wb")
            fo.write(data)
            fo.close()


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

def MSG(message="", title=None):
    MSGS.append((_u(title), _u(message)))
    LOG.append(("MSG", _u(title)))
    _say("MSG [%s]\n%s" % (title, message))


def ERRMSG(message="", title=None, details=None, modal=0):
    ERRMSGS.append((_u(title), _u(message)))
    LOG.append(("ERRMSG", _u(title)))
    _say("ERRMSG [%s]\n%s" % (title, message))


def CONFIRM(title=None, message=""):
    ans = _match(_CONFIRM_ANSWERS, title)
    if ans is _MISS:
        UNSCRIPTED.append(("CONFIRM", _u(title)))
        _say("CONFIRM UNSCRIPTED [%s] -> 1 (OK)" % title)
        return 1
    LOG.append(("CONFIRM", _u(title)))
    _say("CONFIRM [%s] -> %s" % (title, ans))
    # Fixture side effects: the operator does what the dialog asks.
    if ans == 1 and _CUR[0] is not None:
        t = _u(title)
        if FLAVOR[0] in ("ts44-strict", "ts44-dimlie") \
                and u"make dataset 2D" in t:
            _params_for(_dspath(_CUR[0]))["PARMODE"] = u"2D"
            LOG.append(("OPERATOR", u"parmode -> 2D"))
        if FLAVOR[0] == "ts44-f1route" and u"set F1 TD" in t:
            import re as _re
            m = _re.search(r"to (\d+)\.", _u(message))
            if m:
                _params_for(_dspath(_CUR[0]))["1 TD"] = _u(m.group(1))
                LOG.append(("OPERATOR", u"1 td -> %s" % m.group(1)))
    return ans


def SELECT(title=None, message="", buttons=None, mnemonics=None):
    ans = _match(_SELECT_ANSWERS, title)
    if ans is _MISS:
        UNSCRIPTED.append(("SELECT", _u(title)))
        _say("SELECT UNSCRIPTED [%s] -> 0" % title)
        return 0
    LOG.append(("SELECT", _u(title)))
    _say("SELECT [%s] -> %s" % (title, ans))
    return ans


def INPUT_DIALOG(title=None, header=None, items=None, values=None,
                 comments=None, types=None, buttons=None, shortcuts=None,
                 columns=30):
    ans = _match(_DIALOG_ANSWERS, title)
    if ans is _MISS:
        UNSCRIPTED.append(("INPUT_DIALOG", _u(title)))
        _say("INPUT_DIALOG UNSCRIPTED [%s] -> defaults %s" % (title, values))
        return values
    if items is not None and len(ans) != len(items):
        UNSCRIPTED.append(("INPUT_DIALOG length mismatch: %d answers for "
                           "%d items" % (len(ans), len(items)), _u(title)))
    LOG.append(("INPUT_DIALOG", _u(title)))
    _say("INPUT_DIALOG [%s] -> %s" % (title, ans))
    return [_u(a) for a in ans]


def VIEWTEXT(title="", header="", text="", modal=1):
    LOG.append(("VIEWTEXT", _u(title)))
    _say("VIEWTEXT [%s]" % title)


def SHOW_STATUS(message=""):
    LOG.append(("SHOW_STATUS", _u(message)))


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

def GETPAR(name, axis=0):
    if _CUR[0] is None:
        return u""
    dsdir = _dspath(_CUR[0])
    params = _params_for(dsdir)
    n = _u(name)
    v = params.get(name, u"")
    if n == u"PARMODE":
        v = _PARMODE_TO_ORDINAL.get(v, v)     # consoles report the ORDINAL
        if FLAVOR[0] == "ts44-dimlie":
            v = u""                           # unrecognisable readback
    elif n.startswith(u"1 "):
        if FLAVOR[0] == "ts44-f1echo":
            v = params.get("TD", u"")         # prefix ignored: F2's TD
        elif FLAVOR[0] == "ts44-stale" and not _F1_FRESH.get(dsdir):
            v = u""                           # F1 map not loaded yet
        elif FLAVOR[0] == "ts44-f1mismatch" and n == u"1 TD":
            try:
                v = u"%d" % (int(v) - 1)      # off by one, consistently
            except ValueError:
                pass
    LOG.append(("GETPAR", u"%s = %s" % (n, v)))
    return v


def GETACQUDIM():
    """Acquisition dimensionality of the current dataset (documented API)."""
    if FLAVOR[0] == "legacy":
        raise NameError("GETACQUDIM")           # old TopSpin: no such command
    if FLAVOR[0] == "ts44-dimlie":
        LOG.append(("GETACQUDIM", u"1"))
        return 1                                # lies: raw-data-derived
    if _CUR[0] is None:
        return 0
    v = _params_for(_dspath(_CUR[0])).get("PARMODE", u"0")
    v = _PARMODE_TO_ORDINAL.get(v, v)
    try:
        d = int(v) + 1
    except ValueError:
        d = 0
    LOG.append(("GETACQUDIM", u"%d" % d))
    return d


def GETPARSTAT(name, axis=0):
    return GETPAR(name, axis)


def _validate_putpar(name, value, dsdir):
    """Raise the way the console does for the forms it rejects (message
    texts copied from Torino's Error_2.txt / Error_3.txt)."""
    import java.lang
    n = _u(name)
    v = _u(value)
    msg = None
    if n == u"PARMODE":
        names_ok = _TS44_PARMODE_NAMES
        if FLAVOR[0] == "ts44-strict":
            names_ok = ()                       # nothing we write is accepted
        if v not in names_ok:
            msg = (u"exception 'Could not convert '%s' into enum:\n"
                   u"GetEnuOrd[PARMODE]: enumeration name %s not found\n"
                   u"' in validateParameterOfFamily())" % (v, v))
    elif n.startswith(u"1 "):
        if FLAVOR[0] == "ts44-stale" and not _F1_FRESH.get(dsdir):
            msg = u"%s: parameter not found in map" % n
        elif FLAVOR[0].startswith("ts44") and n[2:] not in _TS44_F1_MAP:
            msg = u"%s: parameter not found in map" % n
    if msg is not None:
        PUTPAR_FAILURES.append((n, v, msg))
        LOG.append(("PUTPAR-REJECTED", u"%s = %s" % (n, v)))
        _say("PUTPAR REJECTED (%s) [%s = %s]" % (FLAVOR[0], n, v))
        raise java.lang.RuntimeException(msg)


def PUTPAR(name, value):
    if _CUR[0] is None:
        raise RuntimeError("PUTPAR with no current dataset")
    dsdir = _dspath(_CUR[0])
    _validate_putpar(name, value, dsdir)
    params = _params_for(dsdir)
    if FLAVOR[0] == "ts44-f1route" and _u(name).startswith(u"1 "):
        params[_u(name)[2:]] = _u(value)      # prefix ignored on WRITE
        LOG.append(("PUTPAR", u"%s = %s (routed to %s)"
                    % (_u(name), _u(value), _u(name)[2:])))
        return
    params[name] = _u(value)
    if _u(name) == u"PARMODE" and dsdir in _F1_FRESH:
        del _F1_FRESH[dsdir]                    # F1 map stale until RE()
    LOG.append(("PUTPAR", u"%s = %s" % (_u(name), _u(value))))


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------

def CURDATA(cmdthread=None):
    if _CUR[0] is None:
        return None
    return [_u(x) for x in _CUR[0]]


def WR(dataset=None, override="y"):
    """TopSpin semantics: write a copy of the CURRENT dataset to target."""
    if _CUR[0] is None:
        raise RuntimeError("WR with no current dataset")
    src = _dspath(_CUR[0])
    dst = _dspath(dataset)
    LOG.append(("WR", _u(dst)))
    _say("WR -> %s" % dst)
    _copy_tree(src, dst)
    _PARAMS[dst] = dict(_params_for(src))


def RE(dataset=None, show="y"):
    dst = _dspath(dataset)
    if not os.path.isdir(dst):
        raise RuntimeError("RE: no such dataset: %s" % dst)
    _CUR[0] = [_u(x) for x in dataset]
    _F1_FRESH[dst] = 1                          # parameter model reloaded
    LOG.append(("RE", _u(dst)))
    _say("RE -> %s" % dst)


def RE_PATH(path):
    LOG.append(("RE_PATH", _u(path)))
    _say("RE_PATH %s (no-op)" % path)


# ---------------------------------------------------------------------------
# Commands / control flow
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Virtual wall clock (clock-audit test fixture)
# ---------------------------------------------------------------------------
# The script's clock audit compares OCXO-implied acquisition durations
# against workstation wall-clock timestamps.  Under the harness no real
# time passes (acquisitions are mocked), so the stub provides a virtual
# clock with a DELIBERATE injected fractional offset:
#   * HARNESS_WALL_MS()      -> current virtual time in ms; each read also
#     advances the clock a few ms (code between timestamps takes time);
#   * HARNESS_ADVANCE_S(s)   -> a mocked acquisition of OCXO-implied
#     duration s advances the wall clock by s*(1+INJECTED_CLOCK_OFFSET)
#     plus a small per-block overhead (disk writes etc.), plus
#     deterministic ms-scale jitter (NTP timestamp granularity).
# The offline fit in analysis/facility_report.py must recover
# INJECTED_CLOCK_OFFSET within its stated uncertainty -- the harness
# wrapper (run_jython_harness.sh) enforces that.

INJECTED_CLOCK_OFFSET = 3.0e-7   # deliberate console-clock error to recover
_VCLOCK_MS = [1787000000000L]    # virtual epoch (arbitrary, 2026-ish)
_LCG = [20260826L]               # deterministic jitter source


def _jitter_ms(spread):
    """Deterministic pseudo-random integer in [-spread, +spread]."""
    _LCG[0] = (_LCG[0] * 6364136223846793005L + 1442695040888963407L) \
        & 0xFFFFFFFFFFFFFFFFL
    return int((_LCG[0] >> 33) % (2 * spread + 1)) - spread


def HARNESS_WALL_MS():
    """Virtual System.currentTimeMillis(); reading it costs a few ms."""
    t = _VCLOCK_MS[0]
    _VCLOCK_MS[0] = _VCLOCK_MS[0] + 5 + _jitter_ms(3)
    return t


def HARNESS_ADVANCE_S(seconds):
    """Advance the virtual clock across a mocked acquisition: OCXO-implied
    duration scaled by the injected offset, plus per-block overhead."""
    LOG.append(("HARNESS_ADVANCE_S", _u(seconds)))
    ms = seconds * 1000.0 * (1.0 + INJECTED_CLOCK_OFFSET)
    _VCLOCK_MS[0] = _VCLOCK_MS[0] + long(round(ms)) + 200 + _jitter_ms(8)


class _CmdThread:
    def getResult(self):
        return 0


def XCMD(cmd, wait=None, arg=None):
    # In SIMULATE/DESKTEST the script's hw_skip() guard must mock every
    # hardware command before XCMD is reached.  Reaching here = breach.
    BREACHES.append("XCMD reached: %s" % _u(cmd))
    _say("GUARD BREACH: XCMD(%r)" % cmd)
    return _CmdThread()


def ZG():
    BREACHES.append("ZG() reached")
    _say("GUARD BREACH: ZG()")


def EXIT():
    LOG.append(("EXIT", u""))
    _say("EXIT()")
    raise SystemExit(0)


def SLEEP(seconds):
    LOG.append(("SLEEP", _u(seconds)))
