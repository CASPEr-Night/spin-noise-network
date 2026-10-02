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
    "HARNESS_WALL_MS", "HARNESS_ADVANCE_S", "HARNESS_MOCK_ACQ",
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
STRAY_DIALOGS = []     # console dialogs the API popped on its own (text as
                       # TopSpin 3.7.0 shows it); the harness fails on any
DRU_REFUSALS = []      # (expno_dir, td, rows, d11_s) per pseudo-2D block the
                       # modelled receiver unit refused (legacy-dru flavors)

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
#   "legacy-noacqu2": the name under which TopSpin 3.7.0 as observed at
#                   Oulu (Avance III HD 500, 2026-09-25, v0.7.4 simulate
#                   + desktest) was first modelled: PUTPAR("PARMODE",
#                   "2D") accepted and read back, but NO F1 parameter
#                   file acqu2 created (the operator's template was 1D;
#                   Oulu's bundle has no acqu2 in any expno), the
#                   console's own dialog once per run -- "The requested
#                   format file is invalid: .../12/acqu2: getpar: No such
#                   file or directory" -- and EVERY parameter write into
#                   the dataset silently lost (the pseudo-2D blocks read
#                   back the TD/RG/PULPROG inherited from the 1D setup
#                   expno with putpar_failures 0).  Torino's first LIVE
#                   run (Avance Neo 400, TopSpin 4.4.0, 2026-09-22,
#                   v0.7.3, 1D template; evidence read 2026-09-25) showed
#                   the SAME mechanism on 4.4.0: PARMODE 2D accepted and
#                   in the audit trail, no acqu2, the F1 PUTPAR("1 TD")
#                   popping that dialog (Java: Cmd.putPar -> PeParams
#                   .setParameterValue -> ... initializeParameters ->
#                   MfrException) with NO exception reaching Jython, the
#                   audit trail empty after the PARMODE change while
#                   TD/RG/PULPROG were written (lost on disk, not a stale
#                   readback), and zg refusing each block ("inconsistent
#                   PARMODE 2D: Parameter set acqu2 ... Unable to open
#                   file .../11/acqu2").  Since then this is the model
#                   for EVERY flavor (below), and legacy-noacqu2 differs
#                   from legacy only in having GETACQUDIM (Oulu:
#                   acqudim_readback 2).  The v0.7.4 script fails every
#                   1D-template flavor: stray dialogs, wrong TD/RG/PULPROG
#                   in the pseudo-2D blocks, no acqu2 in the bundle.
#   "legacy-dru"  : legacy-noacqu2 (TopSpin 3.7.0, GETACQUDIM present) on
#                   an Avance III HD whose AQS DRU refuses a pseudo-2D row
#                   that asks for more than DRU_LAN_BPS over the transfer
#                   delay (TD*4 bytes / d11) -- Oulu, Avance III HD 500,
#                   DRU-E Z102520/04001, 2026-09-30, first live run: every
#                   1 MB row written 50 ms (zgnoise2d) or 30 ms (zgref2d)
#                   after its go was refused at zg with "Exception in
#                   DRUCONTR 1: Your pulse program produces too much data
#                   for the LAN capacity. ->Experiment aborted by DRU1!",
#                   while the 64 kB rungs passed.  The console acquired
#                   the FIRST row of every refused block, aborted 12-18 s
#                   into the second, and left the full-size ser TopSpin
#                   3.x pre-allocates (8,388,608 B / 93,323,264 B) with
#                   zeros in rows 2..N; acqu2s TD said 1.  The stub's
#                   HARNESS_MOCK_ACQ reproduces exactly that shape and
#                   wall time; the script's row probe must walk its ladder
#                   past the first entry (1 MB / 1.0 s = 1.05 MB/s is over
#                   the modelled 0.6 MB/s) to the second (1 MB / 3.0 s),
#                   and acquire the three blocks with it -- no dialog.
#   "legacy-dru-refused": the same console with a LAN limit below every
#                   ladder entry: every pseudo-2D block is refused whatever
#                   TD / d11.  The script must WARN (never dialog), record
#                   the failure, continue with the first setting, retry the
#                   noise block once, and still produce a bundle.
#   "legacy-nocouple": legacy-noacqu2 (TopSpin 3.x) on a console that does
#                   NOT couple DSPFIRM to DIGMOD: the DIGMOD digital write
#                   is accepted and DSPFIRM stays rectangle (v0.7.8).  Not
#                   observed on any console -- Oulu's 3.7.0 coupled -- but
#                   the case the DSPFIRM name ladder exists for: the
#                   script must then try "sharp(standard)" first (accepted
#                   here, as the 3.x parameter editor spells it), never
#                   reach "sharp", record dspfirm_form "sharp(standard)",
#                   and raise no dialog.
#   In "legacy" and "legacy-2dtemplate", GETACQUDIM does not exist (old
#   TopSpin), so the GETPAR("PARMODE") ordinal path is what gets
#   exercised there.
# Acquisition mode (v0.7.7, Oulu's 3.7.0 modelled faithfully since
# v0.7.8).  DIGMOD and DSPFIRM are enumerated like PARMODE: written by
# NAME (analog / digital / homodecoupling-digital / baseopt; sharp /
# smooth / medium / user_defined / rectangle), read back as the ORDINAL,
# an ordinal written is rejected with the GetEnuOrd text.  Bruker couples
# the two -- DSPFIRM rectangle selects DIGMOD baseopt and vice versa --
# and the stub does the same (PUTPAR-COUPLED log entries) on every flavor
# but legacy-nocouple.  The harness template carries Oulu's baseopt /
# rectangle (every Oulu acquisition of 2026-09-30 ran DIGMOD 3 / DSPFIRM
# 4, the mode in which the DRU aborted the 262144-point rows; Torino's
# Neo ran digital / sharp and acquired them), so every flavor exercises
# the switch; under "ts44-strict" the names are rejected like PARMODE's,
# and the script must record that once and go on without them.
# What Oulu's TopSpin 3.7.0 did on 2026-10-02 with the v0.7.7 script, now
# the model for every legacy flavor: PUTPAR("DIGMOD", "digital") accepted
# and DSPFIRM moved to sharp (ordinal 0) by the console itself;
# PUTPAR("DSPFIRM", "sharp") -- the Acquisition Reference's spelling --
# answered with the console's OWN dialog, "GetEnuOrd[DSPFIRM]:
# enumeration name sharp not found", the write dropped and NO exception
# raised into Jython (meta.json: putpar_failures 0, failed_forms []),
# once per acquired expno.  The enum name 3.x accepts is taken to be the
# parameter editor's "sharp(standard)" (not console-confirmed: the
# v0.7.8 script never needs it on a coupling console, so a live bundle
# with dspfirm_form "coupled" settles nothing about it; one with
# dspfirm_form "sharp(standard)" or "sharp" would).  The ts44 flavors
# keep accepting "sharp" as in v0.7.7: Torino's 4.4.0 ran digital /
# sharp from its template and the script never wrote DSPFIRM there, so
# its behaviour for either spelling is UNKNOWN; "ts44-strict" rejects
# both names, as it rejects every enum name the script writes.
# F1 parameter FILES -- the observed consoles (3.7.0 Oulu, 4.4.0 Torino)
# are the model for ALL flavors: no console creates acqu2 on a scripted
# PARMODE write.  While a dataset says 2D and has no acqu2, a GETPAR or
# PUTPAR with the F1 prefix (or axis=1) records a STRAY_DIALOG and
# returns/raises nothing, and EVERY PUTPAR into that dataset (PARMODE
# itself excepted: the transition took on both consoles) is dropped
# without an exception.  Once acqu2 exists -- WR copied it along from a
# dataset that had one, the script created it (its v0.7.5 fallback), or
# the operator's own parmode made it (the 'make dataset 2D' CONFIRM side
# effect, as TopSpin's parmode does) -- the dataset behaves as its flavor
# says, the ts44-* F1 quirks included, and an accepted PUTPAR "1 TD"
# keeps acqu2's TD in step the way TopSpin's putpar writes the file.
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
_DIGMOD_TO_ORDINAL = {u"analog": u"0", u"digital": u"1",
                      u"homodecoupling-digital": u"2", u"baseopt": u"3"}
_DSPFIRM_TO_ORDINAL = {u"sharp": u"0", u"sharp(standard)": u"0",
                       u"smooth": u"1", u"medium": u"2",
                       u"user_defined": u"3", u"rectangle": u"4"}
# DSPFIRM enum names a PUTPAR takes, per console generation (see the
# acquisition-mode note above): TopSpin 3.x refused "sharp" with a
# dialog (Oulu, 2026-10-02) and is taken to spell it "sharp(standard)";
# 4.4.0 is unknown for DSPFIRM and keeps the v0.7.7 model.
_DSPFIRM_NAMES_TS3 = (u"sharp(standard)", u"smooth", u"medium",
                      u"user_defined", u"rectangle")
_DSPFIRM_NAMES_TS44 = (u"sharp", u"smooth", u"medium",
                       u"user_defined", u"rectangle")
_GETENUORD_DSPFIRM = u"GetEnuOrd[DSPFIRM]: enumeration name %s not found"
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
    del PUTPAR_FAILURES[:], STRAY_DIALOGS[:], DRU_REFUSALS[:]
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


def _is_2d(params):
    return params.get("PARMODE") in (u"1", u"2D")


def _has_acqu2(dsdir):
    return os.path.isfile(os.path.join(dsdir, "acqu2"))


def _existing_fnmode(acqu2_path, default=u"0"):
    """FnMODE as an existing acqu2 states it, or default when the file or
    the line is absent."""
    if not os.path.isfile(acqu2_path):
        return default
    v = default
    f = open(acqu2_path, "r")
    try:
        for ln in f.readlines():
            if ln.startswith("##$FnMODE="):
                v = _u(ln[len("##$FnMODE="):].strip())
    finally:
        f.close()
    return v


def _write_f1_files(dsdir, params):
    """Write acqu2 (+ pdata/1/proc2) the way TopSpin's OWN parmode does
    (the operator's step: a CONFIRM side effect), or bring acqu2's TD in
    step after an accepted PUTPAR "1 TD" on a dataset that has the file
    (TopSpin's putpar writes the file).  NEVER called for a scripted
    PARMODE write: no console creates the file there -- TopSpin 3.7.0
    (Oulu, 2026-09-25) and 4.4.0 (Torino, live 2026-09-22) both left the
    dataset 2D without it.

    An acqu2 that already exists keeps its FnMODE: putpar edits the one
    parameter it was given, and parmode does not regenerate an F1 set the
    dataset already has -- so the mode a 2D template carried survives WR,
    the PARMODE switch and the '1 TD' write, exactly as on the console,
    and the script has to normalise it itself (the harness asserts that
    on legacy-2dtemplate).  A file the console creates from nothing says
    0, as TopSpin's own defaults do."""
    if not os.path.isdir(dsdir):
        return
    td1 = params.get("1 TD", u"256")
    acqu2 = os.path.join(dsdir, "acqu2")
    fnmode = _existing_fnmode(acqu2)
    f = open(acqu2, "w")
    f.write("##TITLE= Parameter file, TopSpin (harness stub)\n"
            "##JCAMPDX= 5.0\n"
            "##DATATYPE= Parameter Values\n"
            "##ORIGIN= Bruker BioSpin GmbH\n"
            "##OWNER= harness\n"
            "##$TD= %s\n"
            "##$FnMODE= %s\n"
            "##$SW_h= 5000\n"
            "##$NUC1= <1H>\n"
            "##END=\n" % (_b(td1), _b(fnmode)))
    f.close()
    pdir = os.path.join(dsdir, "pdata", "1")
    if not os.path.isdir(pdir):
        os.makedirs(pdir)
    if not os.path.isfile(os.path.join(pdir, "proc2")):
        f = open(os.path.join(pdir, "proc2"), "w")
        f.write("##TITLE= Parameter file, TopSpin (harness stub)\n"
                "##JCAMPDX= 5.0\n##DATATYPE= Parameter Values\n"
                "##ORIGIN= Bruker BioSpin GmbH\n##OWNER= harness\n"
                "##$SI= 256\n##$MC2= 0\n##END=\n")
        f.close()
    LOG.append(("F1FILES", u"%s (TD %s)" % (_u(dsdir), _u(td1))))


def _f1_broken(dsdir, params):
    """A dataset that says 2D but has no acqu2 -- on EVERY flavor: TopSpin
    3.7.0 (Oulu) and 4.4.0 (Torino) both left it so after a scripted
    PARMODE write, and both lost every write into it."""
    return _is_2d(params) and not _has_acqu2(dsdir)


def _stray_dialog(dsdir, api, name, text=None):
    """A dialog the console pops on its own, with NO exception reaching
    the script: the acqu2 'format file' dialog (default text; Oulu,
    2026-09-25) or whatever text the caller passes (the GetEnuOrd[DSPFIRM]
    dialog of 2026-10-02)."""
    if text is None:
        text = (u"The requested format file is invalid: %s/acqu2: getpar: "
                u"No such file or directory" % _u(dsdir))
    STRAY_DIALOGS.append((api, _u(name), text))
    LOG.append(("STRAY-DIALOG", u"%s %s" % (api, _u(name))))
    _say("STRAY CONSOLE DIALOG (%s %s): %s" % (api, name, text))


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
            _dsd = _dspath(_CUR[0])
            _params_for(_dsd)["PARMODE"] = u"2D"
            LOG.append(("OPERATOR", u"parmode -> 2D"))
            _write_f1_files(_dsd, _params_for(_dsd))   # parmode makes them
        if FLAVOR[0] == "ts44-f1route" and u"set F1 TD" in t:
            import re as _re
            m = _re.search(r"to (\d+)\.", _u(message))
            if m:
                _dsd = _dspath(_CUR[0])
                _params_for(_dsd)["1 TD"] = _u(m.group(1))
                LOG.append(("OPERATOR", u"1 td -> %s" % m.group(1)))
                if _has_acqu2(_dsd):
                    _write_f1_files(_dsd, _params_for(_dsd))
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
    if (n.startswith(u"1 ") or axis == 1) and _f1_broken(dsdir, params):
        # TopSpin 3.7.0 reads F1 parameters from the acqu2 FILE; with the
        # file missing it shows its own error dialog and yields nothing.
        _stray_dialog(dsdir, "GETPAR", n)
        LOG.append(("GETPAR", u"%s = " % n))
        return u""
    v = params.get(name, u"")
    if n == u"PARMODE":
        v = _PARMODE_TO_ORDINAL.get(v, v)     # consoles report the ORDINAL
        if FLAVOR[0] == "ts44-dimlie":
            v = u""                           # unrecognisable readback
    elif n == u"DIGMOD":
        v = _DIGMOD_TO_ORDINAL.get(v, v)      # the ORDINAL, like PARMODE
    elif n == u"DSPFIRM":
        v = _DSPFIRM_TO_ORDINAL.get(v, v)
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
    if FLAVOR[0] in ("legacy", "legacy-2dtemplate"):
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
    elif n in (u"DIGMOD", u"DSPFIRM"):
        names_ok = tuple(_DIGMOD_TO_ORDINAL)
        if n == u"DSPFIRM":
            names_ok = _DSPFIRM_NAMES_TS44
            if FLAVOR[0].startswith("legacy"):
                names_ok = _DSPFIRM_NAMES_TS3   # a name outside it never
                                                # gets here: PUTPAR drops
                                                # it with the 3.7.0 dialog
        if FLAVOR[0] == "ts44-strict":
            names_ok = ()                       # rejected like PARMODE's
        if v not in names_ok:
            msg = (u"exception 'Could not convert '%s' into enum:\n"
                   u"GetEnuOrd[%s]: enumeration name %s not found\n"
                   u"' in validateParameterOfFamily())" % (v, n, v))
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


def _couple_acq_mode(name, params):
    """Bruker's documented coupling: DSPFIRM rectangle selects DIGMOD
    baseopt and DIGMOD baseopt selects DSPFIRM rectangle; leaving either
    leaves the other (digital / sharp, the defaults).  Oulu's 3.7.0 did
    exactly this on 2026-10-02 (DIGMOD digital written, DSPFIRM read back
    0).  legacy-nocouple is the console that does not."""
    if FLAVOR[0] == "legacy-nocouple":
        return
    n = _u(name)
    if n == u"DIGMOD":
        if params.get("DIGMOD") == u"baseopt" \
                and params.get("DSPFIRM") != u"rectangle":
            params["DSPFIRM"] = u"rectangle"
            LOG.append(("PUTPAR-COUPLED", u"DSPFIRM = rectangle"))
        elif params.get("DIGMOD") != u"baseopt" \
                and params.get("DSPFIRM") == u"rectangle":
            params["DSPFIRM"] = u"sharp"
            LOG.append(("PUTPAR-COUPLED", u"DSPFIRM = sharp"))
    elif n == u"DSPFIRM":
        if params.get("DSPFIRM") == u"rectangle" \
                and params.get("DIGMOD") != u"baseopt":
            params["DIGMOD"] = u"baseopt"
            LOG.append(("PUTPAR-COUPLED", u"DIGMOD = baseopt"))
        elif params.get("DSPFIRM") != u"rectangle" \
                and params.get("DIGMOD") == u"baseopt":
            params["DIGMOD"] = u"digital"
            LOG.append(("PUTPAR-COUPLED", u"DIGMOD = digital"))


def PUTPAR(name, value):
    if _CUR[0] is None:
        raise RuntimeError("PUTPAR with no current dataset")
    dsdir = _dspath(_CUR[0])
    params = _params_for(dsdir)
    if _u(name) != u"PARMODE" and _f1_broken(dsdir, params):
        # Oulu 2026-09-25 (3.7.0) and Torino 2026-09-22 (4.4.0): on a 2D
        # dataset without acqu2 every write is lost without an exception
        # -- TD, RG, PULPROG and "1 TD" all read back the inherited values
        # while putpar_failures stayed 0 (Torino's audit trail has no
        # entry after the PARMODE change) -- and the F1 write is what
        # popped the console's dialog at Torino (Cmd.putPar -> PeParams
        # .setParameterValue -> ... initializeParametersForFamilyIfNeeded).
        # Only the PARMODE transition itself had taken.  Checked BEFORE the
        # flavor quirks: those describe F1 access on a dataset that HAS
        # its file.
        if _u(name).startswith(u"1 "):
            _stray_dialog(dsdir, "PUTPAR", _u(name))
        LOG.append(("PUTPAR-DROPPED", u"%s = %s" % (_u(name), _u(value))))
        _say("PUTPAR silently LOST (%s, no acqu2) [%s = %s]"
             % (FLAVOR[0], name, value))
        return
    if _u(name) == u"DSPFIRM" and FLAVOR[0].startswith("legacy") \
            and _u(value) not in _DSPFIRM_NAMES_TS3:
        # Oulu, TopSpin 3.7.0, 2026-10-02: PUTPAR("DSPFIRM", "sharp")
        # popped the console's own dialog -- "GetEnuOrd[DSPFIRM]:
        # enumeration name sharp not found" -- and dropped the write, with
        # NO exception into Jython (the v0.7.7 desktest's meta.json says
        # putpar_failures 0, failed_forms []), once per acquired expno.
        # A stray dialog, like the acqu2 one: the harness fails on any.
        _stray_dialog(dsdir, "PUTPAR", _u(name),
                      _GETENUORD_DSPFIRM % _u(value))
        LOG.append(("PUTPAR-DROPPED", u"%s = %s" % (_u(name), _u(value))))
        _say("PUTPAR dropped with a console dialog (%s) [%s = %s]"
             % (FLAVOR[0], name, value))
        return
    _validate_putpar(name, value, dsdir)
    if FLAVOR[0] == "ts44-f1route" and _u(name).startswith(u"1 "):
        params[_u(name)[2:]] = _u(value)      # prefix ignored on WRITE
        LOG.append(("PUTPAR", u"%s = %s (routed to %s)"
                    % (_u(name), _u(value), _u(name)[2:])))
        return
    params[name] = _u(value)
    if _u(name) == u"PARMODE" and dsdir in _F1_FRESH:
        del _F1_FRESH[dsdir]                    # F1 map stale until RE()
    LOG.append(("PUTPAR", u"%s = %s" % (_u(name), _u(value))))
    _couple_acq_mode(name, params)
    # A scripted PARMODE write creates NO F1 parameter file on any console
    # observed (3.7.0 Oulu, 4.4.0 Torino): the transition takes, the file
    # does not appear.  Only the operator's parmode (CONFIRM side effect)
    # writes it; putpar "1 TD" updates a file that exists.
    if _u(name) == u"1 TD" and _has_acqu2(dsdir):
        _write_f1_files(dsdir, params)          # putpar writes the file


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


# ---------------------------------------------------------------------------
# The receiver unit (clock-audit fixture's sibling): HARNESS_MOCK_ACQ
# ---------------------------------------------------------------------------
# The script's mocked zg (mock_acquisition) offers this seam before it
# advances the clock and writes its own pseudo-random raw-data file.  For
# every flavor but the two legacy-dru ones it returns 0 (the DRU accepts
# the block: the script writes the file and advances the clock itself).
# Under legacy-dru a pseudo-2D block whose per-row volume over the
# transfer delay exceeds DRU_LAN_BPS is REFUSED the way Oulu's was: the
# file of the declared size is created, the first row holds data, every
# later row is zeros, and the wall clock advances by one row plus the
# abort (12-18 s at Oulu).  legacy-dru-refused refuses every pseudo-2D
# block.  1D rungs always pass (64 kB in 30 ms did).

DRU_LAN_BPS = 600000.0   # bytes per second the modelled AQS LAN accepts:
                         # 1 MB / 1.0 s (the ladder's first entry) fails,
                         # 1 MB / 3.0 s and 0.5 MB / 1.0 s pass
DRU_ABORT_S = 12.0       # the DRU aborted 12-18 s into the second row
DRU_TEXT = (u"Exception in DRUCONTR 1: Your pulse program produces too "
            u"much data for the LAN capacity. ->Experiment aborted by DRU1!")


def _random_row_bytes(td, seed):
    """One row of td little-endian int32 in [-2e6, 2e6) as a Java byte[]."""
    import java.util.Random
    import java.nio.ByteBuffer
    import java.nio.ByteOrder
    rnd = java.util.Random(long(seed))
    arr = rnd.ints(long(td), -2000000, 2000000).toArray()
    bb = java.nio.ByteBuffer.allocate(4 * td)
    bb.order(java.nio.ByteOrder.LITTLE_ENDIAN)
    bb.asIntBuffer().put(arr)
    return bb.array()


def HARNESS_MOCK_ACQ(expno_dir, td, rows, d11_s, ocxo_s):
    """Model of the console's receiver unit for a mocked zg.  Returns 0
    when the block is accepted (the script then writes its own mocked
    raw data and advances the clock), 1 when this function handled the
    step as a REFUSAL (file of the refused shape written, clock advanced
    by one row plus the abort, refusal logged)."""
    if FLAVOR[0] not in ("legacy-dru", "legacy-dru-refused"):
        return 0
    if rows is None or rows <= 1:
        return 0                     # the 1D rungs passed at Oulu
    refused = 0
    if FLAVOR[0] == "legacy-dru-refused":
        refused = 1
    else:
        try:
            bps = td * 4.0 / max(float(d11_s), 1e-3)
        except (TypeError, ValueError):
            bps = 1e12
        if bps > DRU_LAN_BPS:
            refused = 1
    if not refused:
        return 0
    import java.io.RandomAccessFile
    path = os.path.join(_b(expno_dir), "ser")
    for fn in ("ser", "fid"):
        p = os.path.join(_b(expno_dir), fn)
        if os.path.isfile(p):
            os.remove(p)
    raf = java.io.RandomAccessFile(path, "rw")
    try:
        raf.write(_random_row_bytes(td, 20260930 + td))   # the first row
        raf.setLength(long(rows) * long(td) * 4L)          # zeros: rows 2..N
    finally:
        raf.close()
    per_row = 0.0
    if ocxo_s:
        per_row = float(ocxo_s) / rows
    HARNESS_ADVANCE_S(per_row + DRU_ABORT_S)
    DRU_REFUSALS.append((_u(expno_dir), td, rows, d11_s))
    LOG.append(("DRU-REFUSED", u"%s TD %d rows %d d11 %s"
                % (_u(expno_dir), td, rows, d11_s)))
    _say("DRU REFUSED (%s) [TD %d, %d rows, d11 %s s -> %.2f MB/s]: %s"
         % (FLAVOR[0], td, rows, d11_s, td * 4.0 / max(float(d11_s), 1e-3)
            / 1e6, DRU_TEXT))
    return 1


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
