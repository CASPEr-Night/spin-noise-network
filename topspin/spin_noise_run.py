# -*- coding: utf-8 -*-
# ============================================================================
# spin_noise_run.py -- community spin-noise acquisition orchestrator
# ============================================================================
#
# PROJECT : "spin-noise network"
#           One-command water spin-noise protocol for Bruker/TopSpin.
#           Maps the McCoy-Ernst/Hoult spin-noise feature (RT probes:
#           Gueron absorption dip; cryoprobes: emission bump) across the
#           community probe fleet, and banks SPN-limited noise records.
#
# OPERATOR: 1. Fill a 5 mm tube with water (tap / distilled / D2O-doped
#              -- whatever it is, it gets RECORDED, not judged).
#           2. Insert the sample, open ANY existing 1H dataset
#              (a PROTON demo set is fine -- it is only used as a
#              parameter template).
#           3. Type:   xpy spin_noise_run
#           4. Answer the dialogs, walk away (~45 min default).
#
# HOW TO RUN THIS SCRIPT:
#           Put this file in  <TSHOME>/exp/stan/nmr/py/user/
#           and the pulse program "zgnoise2d" in
#           <TSHOME>/exp/stan/nmr/lists/pp/user/
#           (the script will offer to install the pulse program for you).
#
# COMPATIBILITY / STYLE NOTES (read me before editing):
#   * Written for TopSpin's EMBEDDED JYTHON interpreter (TopSpin 2.x had
#     Jython ~2.2, TopSpin 3.x/4.x ship Jython 2.7).  Therefore this file
#     deliberately avoids: "with" statements, ternary expressions,
#     str.format(), decorators, sets, and any module that might not exist
#     in old Jython (json, hashlib, zipfile).  JSON, SHA-256 and ZIP are
#     implemented via hand-rolled code / java.* classes, which are always
#     present under Jython.
#   * EVERY TopSpin call that might not exist on a given installation is
#     wrapped so that a missing command degrades to an operator dialog
#     instead of crashing the run.
#   * API facts verified against Bruker "Python Programming in TopSpin"
#     (Bruker BioSpin, distributed with TopSpin; also at pascal-man.com):
#       - INPUT_DIALOG(title, header, items, values, comments, types,
#                      buttons, shortcuts, columns=30) -> list | None
#       - CONFIRM(title, message) -> 1 (OK) / 0 (Cancel)
#       - SELECT(title, message, buttons, mnemonics) -> button index,
#         negative if ESC / window closed
#       - MSG(message, title)          blocking
#       - ERRMSG(message, ...)         non-modal by default
#       - VIEWTEXT(title, header, text, modal)
#       - SHOW_STATUS(message)         non-blocking status line
#       - GETPAR(name, axis=0); axis may be encoded in the name
#         ("1 TD" = F1 TD).  Status params: GETPAR("2s SI") or
#         GETPARSTAT(name, axis).
#       - PUTPAR(name, value)  -- same name encoding ("1 TD", "status SI",
#         array params as "P 1", "D 1", "PLdB 1").
#       - XCMD(cmd, wait=WAIT_TILL_DONE, arg=None) -> CmdThread; by
#         default XCMD WAITS UNTIL THE COMMAND IS FINISHED; for
#         processing commands ct.getResult() is -1 on failure.
#       - ZG(), FT(), EFP()... exist as functions; XCMD covers the rest.
#       - CURDATA() -> [name, expno, procno, dir, user]  (5 elements on
#         TopSpin <= 3.1;  4 elements WITHOUT the user field, with dir
#         being the full data directory, on newer TopSpin).  Both shapes
#         are handled below (see ds_path()).
#       - RE(dataset_list, show), WR(dataset_list, override="y"),
#         RE_PATH(path).
#       - EXIT() terminates the script; SLEEP(s) pauses.
#     NOT in the official manual: NEWDATASET.  Its signature varies
#     between releases, so dataset creation below PRIMARILY uses the
#     documented WR()/RE() copy-from-template pattern, with NEWDATASET
#     attempted only as a guarded bonus path.
#
# ============================================================================

# ----------------------------------------------------------------------------
# GLOBAL SWITCHES -- edit here
# ----------------------------------------------------------------------------
SIMULATE = False          # True: skip all spectrometer commands (zg, rga,
                          # atma, topshim, pulsecal) AND the setup blocks
                          # that lead to them.  Dataset bookkeeping, dialogs,
                          # meta.json and the bundle zip still run, so the
                          # whole flow can be desk-tested.
                          # Can also be enabled at runtime:
                          #     xpy spin_noise_run simulate

DESKTEST = False          # True: Tier-0 desk test.  Like SIMULATE, but it
                          # exercises the REAL TopSpin API surface that is
                          # safe without hardware -- the INPUT_DIALOG chain,
                          # GETPAR/PUTPAR, WR/RE dataset creation, meta.json
                          # writing, zip bundling -- and mocks ONLY the
                          # hardware commands (atma, topshim, pulsecal, zg,
                          # rga) inside safe_hw_cmd().  Requires a running
                          # TopSpin (a free processing-only license is
                          # enough) with any 1H dataset open.  See
                          # testing/tier0_desktest.md.  Runtime:
                          #     xpy spin_noise_run desktest

# Single source of truth for the script version.  KEEP IN SYNC with the
# repository VERSION file (testing/static_check.py enforces the match).
SCRIPT_VERSION  = "0.3.0-dev"
PROGRAM_VERSION = SCRIPT_VERSION  # alias kept for meta.json 'program_version'
# This TopSpin orchestrator still writes schema 1.2 bundles (the last
# Bruker-only schema).  The repository schema is 2.0 (vendor-neutral:
# vendor enum + instrument blocks, written by packer/pack_bundle.py);
# 1.2 bundles remain fully valid there -- an absent vendor field means
# 'bruker' by definition.  static_check.py verifies this SCHEMA_VERSION
# stays within the uploader's supported set.
SCHEMA_VERSION  = "1.2"
PP_NAME         = "zgnoise2d"     # pulse program used for the noise block

# Experiment numbers (see PROTOCOL.md).  The RG ladder starts at 10; its
# extra rungs live at 14/15/16 because 11/12/13 are reserved for the
# reference / noise / reference experiments.
EXP_SETUP     = 1
EXP_LADDER    = [10, 14, 15, 16]  # rungs: RG = 1, 8, 64, max_safe
EXP_REF_OPEN  = 11
EXP_NOISE     = 12
EXP_REF_CLOSE = 13

# Acquisition geometry.
# TD per row is kept at a conservative 256k points: safe on every console
# generation from AV II to Neo (old digitizers/RCUs choke far above this;
# 4.x allows much more, but there is no benefit for this protocol).
TD_ROW      = 262144        # complex-pair points per row (TD, F2)
SWH_HZ      = 6900.0        # -> AQ = TD/(2*SWH) ~ 19.0 s per row
TD_LADDER   = 16384         # quick 1D ladder acquisitions (~1.2 s)
REF_ROWS    = 8             # rows in each reference pseudo-2D
D1_NOISE_S  = 0.05          # loop delay in zgnoise2d (also precedes wr)
D1_REF_S    = 2.0           # relaxation delay for the small-flip references
ROW_OVERHEAD_S = 1.0        # empirical per-row disk/housekeeping allowance
SMALL_FLIP_EXTRA_DB = 39.08 # 20*log10(90): attenuate calibrated 90-deg
                            # power by this much -> ~1 degree tip at P1=P90

DURATION_CHOICES = ["30 min (default)", "60 min", "180 min",
                    "Overnight (10 h)"]
DURATION_SECS    = [1800, 3600, 10800, 36000]

# ----------------------------------------------------------------------------
# Imports.  TopCmds provides the TopSpin API when running under xpy.
# Outside TopSpin (desk syntax-testing) we install console stubs and force
# SIMULATE on, so "python2 spin_noise_run.py" walks the whole flow.
# ----------------------------------------------------------------------------
import sys
import os
import time
import traceback

IN_TOPSPIN = 1
try:
    from TopCmds import *          # noqa -- standard TopSpin idiom
except ImportError:
    IN_TOPSPIN = 0
    SIMULATE = True

    def MSG(message="", title=None):
        print "[MSG] %s | %s" % (title, message)

    def ERRMSG(message="", title=None, details=None, modal=0):
        print "[ERRMSG] %s | %s" % (title, message)

    def CONFIRM(title=None, message=""):
        print "[CONFIRM->OK] %s | %s" % (title, message)
        return 1

    def SELECT(title=None, message="", buttons=None, mnemonics=None):
        print "[SELECT->0] %s | %s | %s" % (title, message, buttons)
        return 0

    def INPUT_DIALOG(title=None, header=None, items=None, values=None,
                     comments=None, types=None, buttons=None,
                     shortcuts=None, columns=30):
        print "[INPUT_DIALOG->defaults] %s : %s = %s" % (title, items, values)
        return values

    def VIEWTEXT(title="", header="", text="", modal=1):
        print "[VIEWTEXT] %s\n%s" % (title, text)

    def SHOW_STATUS(message=""):
        print "[STATUS] %s" % message

    def XCMD(cmd, wait=None, arg=None):
        print "[XCMD skipped] %s" % cmd
        return None

    WAIT_TILL_DONE = None

    def GETPAR(name, axis=0):
        return ""

    def PUTPAR(name, value):
        print "[PUTPAR skipped] %s = %s" % (name, value)

    _STUB_CUR = [None]      # desk-test state: the "current dataset"

    def CURDATA(cmdthread=None):
        return _STUB_CUR[0]

    def RE(dataset=None, show="y"):
        print "[RE] %s" % dataset
        _STUB_CUR[0] = list(dataset)

    def WR(dataset=None, override="y"):
        # desk-test: materialize the expno dir so later steps can copy it
        print "[WR] %s" % dataset
        d = os.path.join(str(dataset[3]), str(dataset[0]), str(dataset[1]))
        if not os.path.isdir(d):
            os.makedirs(d)
        f = open(os.path.join(d, "acqus"), "w")
        f.write("##TITLE= simulated acqus\n")
        f.close()

    def EXIT():
        sys.exit(0)

    def SLEEP(seconds):
        pass

# Java classes: always available under Jython, used for SHA-256 / ZIP /
# timezone (the corresponding Python stdlib modules are NOT reliable on
# old TopSpin Jython builds).
if IN_TOPSPIN:
    import java.lang.System
    import java.util.TimeZone
    import java.security.MessageDigest
    import java.io.FileInputStream
    import java.io.FileOutputStream
    import java.util.zip.ZipOutputStream
    import java.util.zip.ZipEntry
    import jarray
else:
    java = None

# Runtime mode flags from the command line:
#   "xpy spin_noise_run simulate"   -> SIMULATE
#   "xpy spin_noise_run desktest"   -> DESKTEST (Tier-0 desk test)
try:
    for _a in sys.argv[1:]:
        _al = str(_a).lower().strip()
        if _al in ("simulate", "--simulate", "-s"):
            SIMULATE = True
        if _al in ("desktest", "--desktest", "-d"):
            DESKTEST = True
except Exception:
    pass


# ============================================================================
# Small utilities
# ============================================================================

def say(msg):
    """Non-blocking progress announcement (status line + stdout)."""
    try:
        SHOW_STATUS("spin_noise_run: " + msg)
    except Exception:
        pass
    try:
        print "spin_noise_run: " + msg
    except Exception:
        pass


def now_local():
    """Local timestamp, ISO-ish, second resolution."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def utc_stamp_compact():
    return time.strftime("%Y%m%d_%H%M%SZ", time.gmtime())


def tz_offset_min():
    """Local timezone offset from UTC in minutes (java, DST-aware)."""
    try:
        if IN_TOPSPIN:
            tz = java.util.TimeZone.getDefault()
            return tz.getOffset(java.lang.System.currentTimeMillis()) / 60000
    except Exception:
        pass
    try:
        if time.daylight and time.localtime().tm_isdst:
            return int(-time.altzone / 60)
        return int(-time.timezone / 60)
    except Exception:
        return 0


# ----------------------------------------------------------------------------
# Clock audit (schema 1.2).
#
# WHY: stock spectrometers have no GPS discipline -- the console master
# clock is an OCXO (absolute accuracy typically 1e-8 to 1e-7 depending on
# aging and calibration history; chemistry never needed better, because
# chemical shifts are internally-referenced ratios).  Cross-site work on
# absolute frequencies does need better, and there is a free, software-only
# measurement of the offset: acquisition durations are OCXO-derived, while
# the workstation wall clock is normally NTP-disciplined.  Recording
# wall-clock milliseconds immediately before and after each acquisition
# block, together with the OCXO-implied expected duration computed from
# that block's parameters, lets the analysis fit the fractional clock
# offset across the session (precision ~ NTP timestamp jitter / time span;
# an 8 h session reaches ~3e-7).
#
# CONSTRAINT: TopSpin's script layer cannot see individual rows during a
# pseudo-2D acquisition (the console runs autonomously; the script waits),
# so the audit works at BLOCK level: one wall-clock pair per acquisition
# expno, many blocks per session, joint fit offline.
# ----------------------------------------------------------------------------

CLOCK_BLOCKS = []   # filled by clock_block_begin/_end; emitted into meta.json


def wall_clock_ms():
    """Workstation wall-clock time in milliseconds.

    Under TopSpin this is java.lang.System.currentTimeMillis() (the
    NTP-disciplined OS clock).  The Jython test harness injects a
    HARNESS_WALL_MS builtin (a virtual clock with a known fractional
    offset) so the audit math can be validated without hardware; the
    NameError path below means production runs never notice the seam.
    """
    try:
        return int(HARNESS_WALL_MS())      # test seam (harness only)
    except NameError:
        pass
    except Exception:
        pass
    try:
        if IN_TOPSPIN:
            return int(java.lang.System.currentTimeMillis())
    except Exception:
        pass
    try:
        return int(time.time() * 1000)
    except Exception:
        return 0


def harness_clock_advance(seconds):
    """Test seam: in the Jython harness, mocked acquisitions advance the
    virtual wall clock by their OCXO-implied duration (times one plus the
    harness's injected fractional offset).  A no-op everywhere else."""
    if seconds is None:
        return
    try:
        HARNESS_ADVANCE_S(seconds)         # injected by testing/jython_entry
    except NameError:
        pass
    except Exception:
        pass


def ocxo_expected_s(td, swh, ns, rows, d1_s, d1_per_row):
    """OCXO-implied duration of one acquisition block, in seconds, from the
    acquisition parameters alone: rows * ns * (AQ + d1_per_row * d1).
    AQ = TD/(2*SWH).  zg/zg2d spend one d1 per transient; zgnoise2d spends
    two (one before go, one before wr).  Disk/housekeeping overhead is NOT
    included -- it is not OCXO-derived, and the offline fit's intercept
    absorbs it."""
    if not swh:
        return None
    aq = td / (2.0 * swh)
    return rows * ns * (aq + d1_per_row * d1_s)


def clock_block_begin(expno, role, expected_s):
    """Open a clock-audit block: record the wall clock immediately before
    the acquisition starts.  expected_s may be None for blocks whose
    duration is not OCXO-predictable (the setup expno: tune/shim/pulsecal
    plus operator dialogs); such blocks are recorded for the session
    timeline but excluded from the offset fit."""
    entry = {
        "expno": expno,
        "role": role,
        "wall_start_ms": wall_clock_ms(),
        "wall_end_ms": None,
        "ocxo_expected_s": expected_s,
    }
    CLOCK_BLOCKS.append(entry)
    return entry


def clock_block_end(entry):
    """Close a clock-audit block immediately after the acquisition ends."""
    entry["wall_end_ms"] = wall_clock_ms()


def _shell_capture(cmd):
    """Run a shell command defensively; return its output as text, or None.
    os.popen exists on every Jython/Python 2 build TopSpin ever shipped."""
    try:
        p = os.popen(cmd)
        out = p.read()
        rc = None
        try:
            rc = p.close()
        except Exception:
            rc = None
        out = to_text(out).strip()
        if out != "" and (rc is None or rc == 0):
            return out[:2000]
    except Exception:
        pass
    return None


def capture_ntp_status():
    """Best-effort snapshot of the workstation's time-sync state.

    Tries the common tools in order (chrony, ntpd, systemd-timesyncd,
    Windows w32time); the first that answers is recorded verbatim.  Every
    call is wrapped -- a locked-down workstation records 'unavailable'
    and the run continues.  Returns (raw_string, source_label)."""
    probes = [
        ("chronyc tracking", "chrony"),
        ("ntpq -pn", "ntpd (ntpq)"),
        ("timedatectl", "systemd-timesyncd (timedatectl)"),
        ("w32tm /query /status", "w32time"),
    ]
    for cmd, label in probes:
        out = _shell_capture(cmd)
        if out is not None:
            return (out, label)
    return ("unavailable", "unknown")


def rand4hex():
    """4 hex chars for bundle-name uniqueness (no 'random' dependency)."""
    try:
        n = int(time.time() * 1000)
    except Exception:
        n = 0
    n = (n ^ (n >> 16)) & 0xFFFF
    return "%04x" % n


def to_float(s, default=None):
    try:
        return float(str(s).strip())
    except Exception:
        return default


def to_int(s, default=None):
    try:
        return int(float(str(s).strip()))
    except Exception:
        return default


def to_text(v):
    """String coercion that never raises UnicodeEncodeError.

    TopSpin's Jython hands scripts java.lang.Strings, which arrive as
    unicode -- and str() on a non-ASCII unicode raises under Jython /
    Python 2 (an operator typing an umlaut in a dialog used to crash
    slugify this way).  Strings pass through unchanged; anything else
    goes through str(), with repr() as the last resort.
    """
    if isinstance(v, basestring):
        return v
    try:
        return str(v)
    except Exception:
        try:
            return repr(v)
        except Exception:
            return "(unprintable)"


def slugify(text):
    """Lowercase alnum + hyphens; used to auto-suggest the facility slug.

    Dialog answers are unicode under Jython, so no str() here (it raises
    UnicodeEncodeError on non-ASCII input); non-alnum characters --
    including all non-ASCII -- become hyphens anyway."""
    out = []
    prev_dash = 0
    for ch in to_text(text).lower():
        if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            out.append(ch)
            prev_dash = 0
        else:
            if not prev_dash and out:
                out.append("-")
                prev_dash = 1
    s = "".join(out)
    while s.endswith("-"):
        s = s[:-1]
    return s[:40]


def abort(msg):
    """Announce and stop the script cleanly."""
    try:
        MSG(msg + "\n\nThe run was cancelled. Nothing was uploaded.",
            "spin_noise_run: cancelled")
    except Exception:
        pass
    EXIT()


# ----------------------------------------------------------------------------
# JSON writer (hand-rolled: the 'json' module is absent on old Jython)
# ----------------------------------------------------------------------------

def _json_escape(s):
    out = []
    for ch in s:
        c = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif c < 0x20 or c > 0x7E:
            out.append("\\u%04x" % c)
        else:
            out.append(ch)
    return "".join(out)


def json_dumps(obj, indent=0):
    """Minimal JSON serializer: dict / list / str / int / float / bool /
    None.  Dict key order is whatever the dict yields (fine for us)."""
    pad = "  " * indent
    pad2 = "  " * (indent + 1)
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    t = type(obj)
    if t is int or t is long:
        return str(obj)
    if t is float:
        # repr() keeps precision; JSON forbids NaN/Inf -> null them.
        if obj != obj:
            return "null"
        return repr(obj)
    if t is dict:
        if not obj:
            return "{}"
        parts = []
        for k in obj.keys():
            parts.append('%s"%s": %s'
                         % (pad2, _json_escape(to_text(k)),
                            json_dumps(obj[k], indent + 1)))
        return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
    if t is list or t is tuple:
        if not obj:
            return "[]"
        parts = []
        for v in obj:
            parts.append(pad2 + json_dumps(v, indent + 1))
        return "[\n" + ",\n".join(parts) + "\n" + pad + "]"
    # everything else -> string.  to_text, NOT str: dialog answers are
    # unicode under Jython and str() raises on non-ASCII operator input.
    return '"' + _json_escape(to_text(obj)) + '"'


# ----------------------------------------------------------------------------
# SHA-256 + ZIP via java.* (guaranteed under Jython; Python hashlib/zipfile
# are not).  Outside TopSpin these degrade gracefully.
# ----------------------------------------------------------------------------

def sha256_file(path):
    if not IN_TOPSPIN:
        return "sha256:simulated"
    md = java.security.MessageDigest.getInstance("SHA-256")
    fis = java.io.FileInputStream(path)
    buf = jarray.zeros(65536, "b")
    try:
        while 1:
            n = fis.read(buf)
            if n <= 0:
                break
            md.update(buf, 0, n)
    finally:
        fis.close()
    dig = md.digest()
    hx = []
    for b in dig:
        hx.append("%02x" % (b & 0xFF))
    return "sha256:" + "".join(hx)


def script_self_sha256():
    """SHA-256 of THIS script file, computed at runtime.

    Gives every bundle a fingerprint of the exact code that produced it,
    without requiring git on the facility machine.  Under TopSpin the java
    digest is used; on a plain-Python desk run, hashlib.  Any failure
    (no __file__, unreadable path, missing digest class) degrades to the
    string 'unavailable' -- it must never break a run.
    """
    try:
        path = None
        try:
            path = __file__
        except NameError:
            path = None
        if path is None or not os.path.isfile(path):
            return "unavailable"
        if IN_TOPSPIN:
            return sha256_file(path)
        import hashlib
        h = hashlib.sha256()
        f = open(path, "rb")
        try:
            h.update(f.read())
        finally:
            f.close()
        return "sha256:" + h.hexdigest()
    except Exception:
        return "unavailable"


def _zip_add_file(zos, fs_path, arc_name):
    entry = java.util.zip.ZipEntry(arc_name)
    zos.putNextEntry(entry)
    fis = java.io.FileInputStream(fs_path)
    buf = jarray.zeros(65536, "b")
    try:
        while 1:
            n = fis.read(buf)
            if n <= 0:
                break
            zos.write(buf, 0, n)
    finally:
        fis.close()
    zos.closeEntry()


def zip_directory(src_dir, zip_path):
    """Zip src_dir contents (relative paths, forward slashes) -> zip_path."""
    if not IN_TOPSPIN:
        f = open(zip_path, "w")
        f.write("simulated bundle placeholder\n")
        f.close()
        return
    fos = java.io.FileOutputStream(zip_path)
    zos = java.util.zip.ZipOutputStream(fos)
    try:
        stack = [""]
        while stack:
            rel = stack.pop()
            if rel:
                cur = os.path.join(src_dir, rel)
            else:
                cur = src_dir
            names = os.listdir(cur)
            names.sort()
            for name in names:
                fs = os.path.join(cur, name)
                if rel:
                    arc = rel + "/" + name
                else:
                    arc = name
                if os.path.isdir(fs):
                    stack.append(arc)
                else:
                    _zip_add_file(zos, fs, arc)
    finally:
        zos.close()


def copy_file(src, dst):
    fi = open(src, "rb")
    data = fi.read()
    fi.close()
    fo = open(dst, "wb")
    fo.write(data)
    fo.close()


def copy_tree(src, dst):
    """Recursive dir copy via os.listdir (os.walk absent in old Jython)."""
    if not os.path.isdir(dst):
        os.makedirs(dst)
    names = os.listdir(src)
    for name in names:
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.isdir(s):
            copy_tree(s, d)
        else:
            try:
                copy_file(s, d)
            except Exception:
                # unreadable file (permissions, live lock file): skip,
                # but tell the terminal so it can be diagnosed later.
                print "spin_noise_run: WARNING could not copy %s" % s


# ============================================================================
# Defensive TopSpin wrappers
# ============================================================================

def safe_xcmd(cmd, describe=None):
    """Run a TopSpin command; return (ok, result).

    XCMD's documented default is wait=WAIT_TILL_DONE (the call blocks until
    the command is finished).  If the command is unknown / the call raises,
    we return ok=0 so the caller can degrade to an operator dialog.

    NOTE: hardware commands must NOT call this directly -- they go through
    safe_hw_cmd() below, which mocks them in SIMULATE / DESKTEST mode.
    """
    if describe is None:
        describe = cmd
    say("running: %s" % describe)
    try:
        ct = XCMD(cmd)               # blocks (WAIT_TILL_DONE default)
        res = None
        try:
            if ct is not None:
                res = ct.getResult()
        except Exception:
            res = None
        return (1, res)
    except Exception:
        print "spin_noise_run: hardware command '%s' failed:" % cmd
        try:
            traceback.print_exc()
        except Exception:
            pass
        return (0, None)


# The spectrometer-hardware commands this script can issue.  Everything
# else it does (dialogs, GETPAR/PUTPAR, WR/RE, file IO) is safe without
# hardware and runs for real in DESKTEST mode.
HW_COMMANDS = ("atma", "topshim", "pulsecal", "rga", "zg")


def hw_skip():
    """1 when hardware commands must be mocked instead of issued
    (SIMULATE or DESKTEST mode), 0 for a live run."""
    if SIMULATE:
        return 1
    if DESKTEST:
        return 1
    return 0


def hw_mode_name():
    if SIMULATE:
        return "SIMULATE"
    if DESKTEST:
        return "DESKTEST"
    return "LIVE"


def safe_hw_cmd(cmd, describe=None):
    """THE guarded wrapper for spectrometer-hardware commands.

    Every hardware command (see HW_COMMANDS) MUST be issued through this
    function -- testing/static_check.py enforces it.  In SIMULATE and
    DESKTEST the command is mocked: announced on the terminal, not issued,
    and 'success' is returned so the calling flow proceeds.
    """
    if hw_skip():
        print "spin_noise_run: %s -> mocked '%s'" % (hw_mode_name(), cmd)
        return (1, None)
    return safe_xcmd(cmd, describe)


def xcmd_or_dialog(cmd, dialog_title, dialog_text):
    """Try a HARDWARE command; if it fails, ask the operator to do it by
    hand.  Delegates to safe_hw_cmd, so SIMULATE/DESKTEST mock it.

    Returns 1 if the command ran, 0 if the operator did it manually,
    aborts the script if the operator cancels.
    """
    ok, _res = safe_hw_cmd(cmd)
    if ok:
        return 1
    ans = CONFIRM(dialog_title,
                  dialog_text
                  + "\n\nThe automatic command '" + cmd + "' is not "
                  "available on this system.\nPlease perform the step "
                  "MANUALLY now, then press OK.\n(Cancel aborts the whole "
                  "run.)")
    if ans != 1:
        abort("Cancelled at manual step: " + dialog_title)
    return 0


def getpar(name, default=""):
    try:
        v = GETPAR(name)
        if v is None or str(v).strip() == "":
            return default
        return str(v)
    except Exception:
        return default


def putpar(name, value):
    """PUTPAR with logging; returns 1 on success."""
    try:
        PUTPAR(name, str(value))
        return 1
    except Exception:
        print "spin_noise_run: PUTPAR %s=%s failed" % (name, value)
        return 0


def ask_fields(title, header, items, defaults, comments=None):
    """INPUT_DIALOG wrapper; abort on Cancel; returns list of strings."""
    if comments is None:
        comments = [""] * len(items)
    types = ["1"] * len(items)
    res = INPUT_DIALOG(title, header, items, defaults, comments, types)
    if res is None:
        abort("Cancelled in dialog: " + title)
    return res


def ask_select(title, message, buttons, default_abort=1):
    """SELECT wrapper; negative (ESC/close) aborts."""
    v = SELECT(title, message, buttons)
    try:
        v = int(v)
    except Exception:
        v = -1
    if v < 0:
        if default_abort:
            abort("Cancelled in dialog: " + title)
        v = 0
    return v


# ============================================================================
# Dataset plumbing
# ============================================================================

def ds_path(curd):
    """Filesystem path of the EXPNO directory for a CURDATA()-style list.

    Handles both documented shapes:
      old (<= TS 3.1): [name, expno, procno, topspin_dir, user]
                       -> dir/data/user/nmr/name/expno
      new (>= TS 3.5): [name, expno, procno, data_dir]
                       -> data_dir/name/expno
    """
    if curd is None:
        return None
    # to_text, not str: CURDATA() elements are java.lang.Strings (unicode
    # in Jython) and a non-ASCII data path or user name would make str()
    # raise UnicodeEncodeError.
    if len(curd) >= 5:
        return os.path.join(to_text(curd[3]), "data", to_text(curd[4]),
                            "nmr", to_text(curd[0]), to_text(curd[1]))
    return os.path.join(to_text(curd[3]), to_text(curd[0]), to_text(curd[1]))


def open_expno(template_curd, name, expno):
    """Create/open dataset <name>/<expno> and make it current.

    Primary path (fully documented): WR() a copy of the template dataset
    under the new name/expno, then RE() it.  This works on every TopSpin
    that can run xpy at all.  NEWDATASET is deliberately NOT relied upon
    (undocumented signature drift across releases).
    """
    target = list(template_curd)
    target[0] = name
    target[1] = str(expno)
    target[2] = "1"
    say("creating dataset %s expno %s" % (name, expno))
    made = 0
    try:
        WR(target, "y")   # silent overwrite if it already exists
        made = 1
    except Exception:
        print "spin_noise_run: WR() to %s failed" % str(target)
        try:
            traceback.print_exc()
        except Exception:
            pass
    if not made:
        # Last resort: ask the operator to create it interactively.
        MSG("Could not create dataset\n  NAME  = %s\n  EXPNO = %s\n\n"
            "Please create it now yourself:\n"
            "  1. type 'new' in TopSpin\n"
            "  2. NAME = %s, EXPNO = %s, PROCNO = 1\n"
            "  3. use your current dataset as the template\n"
            "  4. click OK there, then close this message."
            % (name, expno, name, expno),
            "spin_noise_run: manual dataset creation")
    try:
        RE(target, "y")
    except Exception:
        abort("Could not open dataset %s/%s -- cannot continue."
              % (name, expno))
    cd = CURDATA()
    if cd is None or str(cd[0]) != str(name) or str(cd[1]) != str(expno):
        abort("Dataset switch verification failed for %s/%s."
              % (name, expno))
    return cd


def make_2d(rows):
    """Turn the current (1D template) dataset into a pseudo-2D.

    PARMODE: 0 = 1D, 1 = 2D.  Setting it from a script is normally silent
    (the interactive 'files will be deleted' prompt belongs to the GUI
    flow), but we VERIFY the result and fall back to an operator dialog.
    F1 is set to QF (no frequency encoding -- it is just a row counter).
    """
    putpar("PARMODE", "1")
    pm = getpar("PARMODE")
    if pm.strip() not in ("1", "2D"):
        ans = CONFIRM("spin_noise_run: make dataset 2D",
                      "The script could not switch this dataset to 2D "
                      "automatically.\n\nPlease type 'parmode' in TopSpin, "
                      "select 2D, confirm any\n'delete files' question, "
                      "then press OK here.")
        if ans != 1:
            abort("Dataset could not be made 2D.")
    # F1 ("indirect") dimension: TD1 rows, QF mode.
    putpar("1 TD", str(rows))
    if not putpar("1 FnMODE", "QF"):
        putpar("FnMODE", "QF")   # older syntax fallback; harmless if no-op


# ============================================================================
# Parameter helpers for the individual experiments
# ============================================================================

def read_power_db():
    """Current f1-channel hard-pulse power in dB. TS3/4: PLdB 1; TS2: PL 1."""
    v = to_float(getpar("PLdB 1"))
    if v is not None:
        return (v, "PLdB 1")
    v = to_float(getpar("PL 1"))
    if v is not None:
        return (v, "PL 1")
    return (None, None)


def set_small_flip(p90_us, p90_db, db_parname):
    """P1 = calibrated P90, power attenuated by ~39 dB -> ~1 degree tip."""
    putpar("P 1", "%.2f" % p90_us)
    if p90_db is not None and db_parname is not None:
        new_db = p90_db + SMALL_FLIP_EXTRA_DB
        # Bruker attenuator ranges top out around 120 dB -- clamp politely.
        if new_db > 120.0:
            new_db = 120.0
        putpar(db_parname, "%.2f" % new_db)
    else:
        MSG("Could not read the pulse power parameter (PLdB 1 / PL 1).\n"
            "Please set channel-1 power to a value ~39 dB LOWER (i.e. "
            "weaker)\nthan your calibrated 90-degree power, using 'eda', "
            "then close this\nmessage.  This keeps the flip angle near 1 "
            "degree.",
            "spin_noise_run: set small-flip power manually")


def set_common_acq(o1_hz, td, swh, ns, d1_s):
    putpar("TD", str(td))
    putpar("SWH", "%.2f" % swh)   # SWH in Hz sets SW consistently
    putpar("O1", "%.2f" % o1_hz)
    putpar("NS", str(ns))
    putpar("DS", "0")
    putpar("D 1", "%.4f" % d1_s)
    putpar("RG", "1")


def run_zg_and_wait(expno_dir, what, ocxo_s=None):
    """Start acquisition and wait.  Both the per-command function ZG and
    the XCMD fallback block per the manual (default wait=WAIT_TILL_DONE).
    As belt-and-braces we verify a raw data file appeared; if not, the
    operator adjudicates.  SIMULATE/DESKTEST mock the acquisition (the
    hw_skip() guard below is what testing/static_check.py verifies).
    ocxo_s is the block's OCXO-implied duration; the mocked path hands it
    to the harness's virtual clock so the clock audit can be tested."""
    if hw_skip():
        say("%s: zg mocked (%s)" % (hw_mode_name(), what))
        harness_clock_advance(ocxo_s)
        return
    ok = 0
    try:
        ZG()          # documented per-command function; blocks
        ok = 1
    except Exception:
        ok = 0
    if not ok:
        ok, _r = safe_hw_cmd("zg", "zg (%s)" % what)
    # verify data landed on disk
    got = 0
    if expno_dir:
        for fn in ("ser", "fid"):
            if os.path.exists(os.path.join(expno_dir, fn)):
                got = 1
    if not got:
        ans = CONFIRM("spin_noise_run: acquisition check",
                      "The script cannot see a raw-data file (ser/fid) for\n"
                      "the experiment it just started:\n  %s\n\n"
                      "If the acquisition is still running, wait for it to\n"
                      "finish, then press OK.  Press Cancel to abort the "
                      "run." % what)
        if ans != 1:
            abort("Acquisition did not complete: " + what)


def run_rga():
    """Run receiver gain optimization; return resulting RG (float) or None.
    SIMULATE/DESKTEST return a fixed mock value (no hardware touched)."""
    if hw_skip():
        print "spin_noise_run: %s -> mocked 'rga' (RG=101)" % hw_mode_name()
        return 101.0
    ok, _r = safe_hw_cmd("rga", "rga (receiver gain optimization)")
    if not ok:
        res = ask_fields(
            "spin_noise_run: receiver gain",
            "Automatic 'rga' is not available.\n"
            "Please run rga (or set RG) manually on the CURRENT experiment\n"
            "and enter the final RG value below.",
            ["RG"], [getpar("RG", "64")])
        return to_float(res[0], 64.0)
    return to_float(getpar("RG"), None)


def record_experiment(meta, expno, role, started, finished, rows):
    """Append one entry to meta['experiments'] from current status params."""
    td = to_int(getpar("TD"), 0)
    swh = to_float(getpar("SWH"), 0.0)
    aq = 0.0
    if swh:
        aq = td / (2.0 * swh)
    entry = {
        "expno": expno,
        "role": role,
        "pulprog": getpar("PULPROG").strip(" <>"),
        "td": td,
        "td1_rows": rows,
        "sw_hz": swh,
        "o1_hz": to_float(getpar("O1"), 0.0),
        "rg": to_float(getpar("RG"), 0.0),
        "ns": to_int(getpar("NS"), 1),
        "aq_s_per_row": aq,
        "started_local": started,
        "finished_local": finished,
    }
    meta["experiments"].append(entry)
    return entry


# ============================================================================
# Pulse program installation
# ============================================================================

PP_TEXT = """;zgnoise2d
;spin-noise network -- pseudo-2D pure-noise acquisition
;
;Bruker zg2d with the excitation pulse line DELETED: the receiver
;simply opens td1 times and records thermal + spin noise.  There is
;NO rf pulse statement anywhere in this sequence (and none hidden in
;an include), so the sample magnetization is never touched.
;
;Plain syntax only -- compiles on TopSpin 2.x through 4.x.
;If <Avance.incl> is missing on a very old system, comment it out:
;this sequence uses none of its macros.

#include <Avance.incl>

;d1 : loop delay, keep short (e.g. 50 ms) -- there is nothing to relax
;ns : MUST be 1 (one transient per row; averaging would defeat the
;     noise-statistics analysis)
;td1: number of rows = number of noise records

1 ze
2 d1
  go=2 ph31
  d1 wr #0 if #0 ze
  lo to 2 times td1
exit

ph31=0

;aq per row = td/(2*swh); the run script sizes td1 so that
;td1*(aq+2*d1) fills the requested wall-clock duration.
"""


def find_pp_user_dir():
    """Locate <TSHOME>/exp/stan/nmr/lists/pp/user.  TSHOME discovery is
    heuristic (java property, env vars); on failure the operator is asked
    once for the path.  Returns dir path or None."""
    candidates = []
    try:
        if IN_TOPSPIN:
            p = java.lang.System.getProperty("XWINNMRHOME")
            if p:
                candidates.append(p)
    except Exception:
        pass
    for env in ("XWINNMRHOME", "TOPSPIN_HOME", "TS_HOME"):
        try:
            p = os.environ.get(env)
            if p:
                candidates.append(p)
        except Exception:
            pass
    for c in candidates:
        d = os.path.join(c, "exp", "stan", "nmr", "lists", "pp", "user")
        if os.path.isdir(os.path.join(c, "exp", "stan", "nmr", "lists",
                                      "pp")):
            if not os.path.isdir(d):
                try:
                    os.makedirs(d)
                except Exception:
                    pass
            if os.path.isdir(d):
                return d
    return None


def install_pulse_program():
    """Write zgnoise2d into the user pp directory (or verify it exists)."""
    ppdir = find_pp_user_dir()
    if ppdir is None:
        res = INPUT_DIALOG(
            "spin_noise_run: pulse program directory",
            "The TopSpin installation directory could not be determined\n"
            "automatically.  Enter the full path of your USER pulse-program\n"
            "directory (usually <TopSpin>/exp/stan/nmr/lists/pp/user),\n"
            "or leave blank if you have ALREADY copied 'zgnoise2d' there\n"
            "by hand (see INSTALL.md).",
            ["pp/user path"], [""], [""], ["1"])
        if res is None:
            abort("Cancelled at pulse-program installation.")
        p = res[0].strip()
        if p == "":
            return "operator states zgnoise2d pre-installed"
        ppdir = p
    target = os.path.join(ppdir, PP_NAME)
    try:
        f = open(target, "w")
        f.write(PP_TEXT)
        f.close()
        say("pulse program installed: %s" % target)
        return target
    except Exception:
        MSG("Could not write the pulse program to:\n  %s\n\n"
            "Please copy the file 'zgnoise2d' from the distribution's\n"
            "topspin/pp/ folder into that directory by hand, then close\n"
            "this message." % target,
            "spin_noise_run: manual pulse-program install")
        return "manual install requested"


# ============================================================================
# uxnmr.info parsing (console identification)
# ============================================================================

def parse_console(expno_dir):
    """Best-effort console name from uxnmr.info in an expno directory."""
    if not expno_dir:
        return None
    p = os.path.join(expno_dir, "uxnmr.info")
    if not os.path.exists(p):
        return None
    try:
        f = open(p, "r")
        lines = f.readlines()
        f.close()
    except Exception:
        return None
    hit = None
    for ln in lines:
        low = ln.lower()
        if ("avance" in low) or ("cabinet" in low) or ("fourier" in low):
            hit = ln.strip()
            break
    return hit


# ============================================================================
# MAIN
# ============================================================================

def main():
    t_run_start = now_local()

    # ---------------------------------------------------------------- 0
    # Greeting.
    mode_notice = ""
    if SIMULATE:
        mode_notice = ("\n*** SIMULATE MODE: no spectrometer commands "
                       "will be issued ***\n")
    elif DESKTEST:
        mode_notice = ("\n*** DESKTEST MODE: hardware commands are mocked; "
                       "dialogs,\n    parameters, datasets and the bundle "
                       "zip are real.\n    Do NOT upload the resulting "
                       "bundle -- it is a plumbing test. ***\n")
    v = ask_select(
        "spin-noise network",
        "Welcome to the community spin-noise run (v%s).\n\n"
        "This will:\n"
        "  * ask you ~6 questions about your facility and sample\n"
        "  * tune/shim, calibrate the 1H 90-degree pulse\n"
        "  * record an RG ladder, two small-flip references, and a long\n"
        "    pulse-free noise block (default ~45 min total)\n"
        "  * write meta.json and pack everything into one zip for upload\n\n"
        "Before continuing:\n"
        "  * a 5 mm tube of water is in the magnet\n"
        "  * ANY existing 1H dataset is open (used only as a parameter\n"
        "    template)\n%s"
        % (SCRIPT_VERSION, mode_notice),
        ["Start", "Cancel"])
    if v != 0:
        abort("Operator chose Cancel at greeting.")

    template = CURDATA()
    if template is None and not SIMULATE:
        abort("No dataset is open.  Open any 1H dataset (e.g. a PROTON\n"
              "demo experiment) so it can be used as a parameter template,\n"
              "then start again with:  xpy spin_noise_run")
    if template is None:
        template = ["SIMTEMPLATE", "1", "1",
                    os.path.join(os.getcwd(), "sim_data")]
        try:
            os.makedirs(ds_path(template))
        except Exception:
            pass

    # ---------------------------------------------------------------- 1
    # Operator Q&A: facility.
    say("operator questionnaire")
    f1 = ask_fields(
        "spin-noise network 1/5: your facility",
        "Who is contributing this dataset?",
        ["Institution", "City", "Country", "Contact e-mail"],
        ["", "", "", ""])
    institution, city, country, email = \
        f1[0].strip(), f1[1].strip(), f1[2].strip(), f1[3].strip()

    slug_suggest = slugify(institution + " " + city)
    if slug_suggest == "":
        slug_suggest = "facility"
    f2 = ask_fields(
        "spin-noise network 2/5: facility slug",
        "Short machine-readable ID for your facility.\n"
        "Used in bundle file names -- lowercase letters, digits, hyphens.",
        ["Facility slug"], [slug_suggest])
    facility_slug = slugify(f2[0])
    if facility_slug == "":
        facility_slug = slug_suggest

    consent = ask_select(
        "spin-noise network: contact consent",
        "May the project maintainers contact you at\n  %s\n"
        "about this data (questions, results, acknowledgements)?" % email,
        ["Yes, you may contact me", "No"])
    contact_consent = (consent == 0)

    # ---------------------------------------------------------------- 2
    # Operator Q&A: sample.
    f3 = ask_fields(
        "spin-noise network 3/5: the sample",
        "Describe the water sample EXACTLY as it is.\n"
        "Tap water is fine -- we just need to know.",
        ["Sample description (e.g. 'tap water', 'distilled + 10% D2O')",
         "H2O fraction (%)", "D2O fraction (%)",
         "Additives (e.g. 'CuSO4 1 mM', or 'none')",
         "Tube outer diameter (mm)", "Sample volume (uL)"],
        ["distilled water", "100", "0", "none", "5", "550"])
    sample_desc = f3[0].strip()
    h2o_pct = to_float(f3[1], 100.0)
    d2o_pct = to_float(f3[2], 0.0)
    additives = f3[3].strip()
    tube_od = to_float(f3[4], 5.0)
    sample_ul = to_float(f3[5], 550.0)

    f4 = ask_fields(
        "spin-noise network 4/5: temperature",
        "VT setpoint in Kelvin (the value the VT unit is regulating to;\n"
        "enter 298 if VT is off and the bore is at room temperature).",
        ["VT setpoint (K)"], [getpar("TE", "298")])
    vt_k = to_float(f4[0], 298.0)

    # ---------------------------------------------------------------- 3
    # Duration.
    dsel = ask_select(
        "spin-noise network 5/5: noise-block duration",
        "How long should the pulse-free noise block run?\n"
        "(Setup + references add ~15 min on top.)",
        DURATION_CHOICES)
    noise_secs = DURATION_SECS[dsel]

    # ---------------------------------------------------------------- 4
    # CRITICAL housekeeping dialogs (recorded, never silent).
    # Lock state.
    lsel = ask_select(
        "spin-noise run: lock",
        "For clean noise records the LOCK should be OFF for the noise\n"
        "block (lock rf leaks into the 1H channel on some systems).\n\n"
        "Turn the lock OFF now if you can (command 'lock off' /\n"
        "BSMS keyboard LOCK button).\n\nWhat is the lock state?",
        ["Lock is OFF", "Lock is ON (leave it on)"])
    locked = (lsel == 1)

    # BSMS field sweep -- THE 2022 lesson: a sweeping B0 shows up as
    # spurious structure in long noise records.  No portable command
    # exists to switch it, so this is operator-confirmed, always.
    ssel = ask_select(
        "spin-noise run: BSMS FIELD SWEEP -- IMPORTANT",
        "The BSMS FIELD SWEEP MUST BE OFF during the noise block.\n"
        "(In 2022 a sweeping field quietly contaminated weeks of noise\n"
        "records -- this dialog exists so that never happens again.)\n\n"
        "Open the BSMS display ('bsmsdisp') and verify SWEEP is OFF.\n\n"
        "Is the field sweep confirmed OFF?",
        ["Yes -- SWEEP is OFF", "No / cannot verify"])
    sweep_off = (ssel == 0)
    if not sweep_off:
        v2 = ask_select(
            "spin-noise run: proceed without sweep confirmation?",
            "The run can continue, but the noise block will be flagged\n"
            "as 'sweep unconfirmed' in meta.json and may be excluded\n"
            "from the axion-relevant analysis.",
            ["Proceed anyway (flagged)", "Abort the run"])
        if v2 == 1:
            abort("Aborted: BSMS field sweep not confirmed off.")

    # ---------------------------------------------------------------- 5
    # Hardware capture.
    say("capturing hardware information")
    probe = getpar("PROBHD").strip()
    bf1 = to_float(getpar("BF1"))
    if bf1 is None:
        bf1 = to_float(getpar("SFO1"), 400.0)
    field_t = bf1 / 42.5774806     # 1H gyromagnetic ratio, MHz/T

    # TopSpin version: heuristic guess from install path, confirmed by
    # the operator (there is no documented version API in old releases).
    ts_guess = ""
    try:
        if IN_TOPSPIN:
            home = java.lang.System.getProperty("XWINNMRHOME")
            if home:
                ts_guess = os.path.basename(str(home))
    except Exception:
        pass
    console_guess = parse_console(ds_path(template))
    if console_guess is None:
        console_guess = ""
    fh = ask_fields(
        "spin-noise run: hardware check",
        "Please verify / complete (auto-detected where possible):",
        ["TopSpin version (e.g. 4.1.4)", "Console (e.g. AVANCE NEO)",
         "Probe (from PROBHD)"],
        [ts_guess, console_guess, probe])
    ts_version = fh[0].strip()
    console = fh[1].strip()
    probe = fh[2].strip()

    ptype_guess = 0   # RT
    plow = probe.lower()
    if ("prodigy" in plow) or ("n2" in plow):
        ptype_guess = 1
    if ("cryo" in plow) or ("tci" in plow) or ("tcp" in plow) \
            or ("qci" in plow):
        if "prodigy" not in plow:
            ptype_guess = 2
    ptype_names = ["RT", "N2-cryo", "He-cryo", "unknown"]
    psel = ask_select(
        "spin-noise run: probe type",
        "What kind of probe is this?\n(auto-guess from the probe string: "
        "%s)" % ptype_names[ptype_guess],
        ["Room temperature (RT)", "Nitrogen-cooled (Prodigy etc.)",
         "Helium cryoprobe", "Unknown"])
    probe_type = ptype_names[psel]

    ft = ask_fields(
        "spin-noise run: probe temperatures (optional)",
        "If you know them (edte / cryopanel), enter coil and preamp\n"
        "temperatures in K; leave blank if unknown.",
        ["Coil temperature (K, blank=unknown)",
         "Preamp temperature (K, blank=unknown)"],
        ["", ""])
    coil_k = to_float(ft[0])
    preamp_k = to_float(ft[1])

    # ---------------------------------------------------------------- 6
    # Install pulse program, create the dataset.
    pp_where = install_pulse_program()

    # Workstation time-sync snapshot for the clock audit (once per
    # session; every probe is wrapped, worst case records 'unavailable').
    say("checking workstation time-sync (NTP) status")
    ntp_raw, ntp_source = capture_ntp_status()

    dsname = "SPINNOISE_" + time.strftime("%Y%m%d", time.localtime())
    meta = {
        "schema_version": SCHEMA_VERSION,
        "program_version": PROGRAM_VERSION,
        "software": {
            "script_version": SCRIPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "script_sha256": script_self_sha256(),
            "run_mode": hw_mode_name().lower(),
        },
        "created_utc": now_utc(),
        "local_timezone_offset_min": tz_offset_min(),
        "facility": {
            "institution": institution,
            "city": city,
            "country": country,
            "facility_slug": facility_slug,
            "contact_email": email,
            "contact_consent": contact_consent,
        },
        "spectrometer": {
            "topspin_version": ts_version,
            "h1_freq_mhz": bf1,
            "field_tesla": field_t,
            "console": console,
            "probe_string": probe,
            "probe_type": probe_type,
            "coil_temp_k": coil_k,
            "preamp_temp_k": preamp_k,
        },
        "sample": {
            "description": sample_desc,
            "h2o_fraction_pct": h2o_pct,
            "d2o_pct": d2o_pct,
            "additives": additives,
            "tube_od_mm": tube_od,
            "sample_volume_ul": sample_ul,
            "vt_setpoint_k": vt_k,
        },
        "environment": {
            "locked": locked,
            "lock_sweep_confirmed_off": sweep_off,
            "operator_notes": "",
        },
        "calibration": {
            "p90_us": None,
            "p90_power_db_or_w": None,
            "rg_ladder": [],
            "topshim_ok": False,
        },
        "experiments": [],
        # Clock audit (schema 1.2): blocks is the SAME list object as
        # CLOCK_BLOCKS, so entries appended during the run land here.
        "clock_audit": {
            "blocks": CLOCK_BLOCKS,
            "ntp_status_raw": ntp_raw,
            "workstation_time_source": ntp_source,
        },
        "checksums": {},
    }

    o1_hz = 4.7 * bf1    # 4.7 ppm (water) in Hz for a bf1-MHz spectrometer

    # ---------------------------------------------------------------- 7
    # EXPNO 1: setup -- tune/match, shim, pulse calibration.
    say("expno %d: setup (tune, shim, calibrate)" % EXP_SETUP)
    cd = open_expno(template, dsname, EXP_SETUP)
    setup_dir = ds_path(cd)
    putpar("PULPROG", "zg")
    set_common_acq(o1_hz, TD_LADDER, SWH_HZ, 1, D1_REF_S)
    t0 = now_local()
    # Clock-audit block for setup: wall times only.  The setup expno's
    # duration (tune, shim, pulsecal, operator dialogs) is not
    # OCXO-predictable, so ocxo_expected_s is null and the offline fit
    # skips this block; the timestamps still anchor the session timeline.
    cb = clock_block_begin(EXP_SETUP, "setup", None)

    if not SIMULATE:
        # Tune & match: atma if fitted, else operator wobb.
        xcmd_or_dialog(
            "atma",
            "spin_noise_run: tune & match",
            "Tune and match the 1H channel on the water sample\n"
            "(type 'wobb', adjust, then 'stop').")
        # Shim.
        ok_shim, _r = safe_hw_cmd("topshim", "topshim")
        if not ok_shim:
            xcmd_or_dialog(
                "topshim 1d",
                "spin_noise_run: shimming",
                "Shim the sample by your usual method (gs / simplex / "
                "manual).")
            meta["calibration"]["topshim_ok"] = False
        else:
            meta["calibration"]["topshim_ok"] = True
    else:
        meta["calibration"]["topshim_ok"] = True

    # P90 calibration: pulsecal if present; the RESULT is always
    # operator-confirmed because pulsecal's output location is not
    # readable in a version-portable way.  Fallback chain:
    # pulsecal -> paropt/popt by hand -> operator-entered value.
    p90_default = getpar("P 1", "10.0")
    if not SIMULATE:
        ok_pc, _r = safe_hw_cmd("pulsecal", "pulsecal (auto P90)")
        if not ok_pc:
            MSG("Automatic 'pulsecal' is not available on this system.\n\n"
                "Determine the 1H 90-degree pulse your usual way\n"
                "(popt/paropt on this water sample, or use the value from\n"
                "your last probe calibration -- water is forgiving), then\n"
                "enter it in the next dialog.",
                "spin_noise_run: manual P90")
        else:
            p90_default = getpar("P 1", p90_default)
    db_now = read_power_db()[0]
    if db_now is None:
        db_prefill = ""
    else:
        db_prefill = str(db_now)
    fp = ask_fields(
        "spin-noise run: 90-degree pulse",
        "Confirm the calibrated 1H 90-degree pulse for this probe\n"
        "(pre-filled from pulsecal / current P1).",
        ["P90 (us)", "P90 power (dB, from PLdB1/PL1)"],
        [p90_default, db_prefill])
    p90_us = to_float(fp[0], 10.0)
    p90_db = to_float(fp[1])
    db_par = read_power_db()[1]
    meta["calibration"]["p90_us"] = p90_us
    # Schema types this field number/string, never null: a legacy console
    # with unreadable PLdB1/PL1 plus a blank dialog answer must not
    # produce a bundle that fails --selftest after a completed run.
    if p90_db is None:
        meta["calibration"]["p90_power_db_or_w"] = "unknown"
    else:
        meta["calibration"]["p90_power_db_or_w"] = p90_db
    clock_block_end(cb)
    record_experiment(meta, EXP_SETUP, "setup", t0, now_local(), 1)

    # ---------------------------------------------------------------- 8
    # RG ladder: quick 1D small-flip acquisitions at RG = 1, 8, 64, max.
    say("RG ladder (4 quick 1D acquisitions)")
    max_rg = None
    ladder_rgs = [1.0, 8.0, 64.0, None]   # None -> use rga result
    i = 0
    while i < len(EXP_LADDER):
        expno = EXP_LADDER[i]
        cd = open_expno(template, dsname, expno)
        putpar("PULPROG", "zg")
        set_common_acq(o1_hz, TD_LADDER, SWH_HZ, 1, D1_REF_S)
        set_small_flip(p90_us, p90_db, db_par)
        rung = ladder_rgs[i]
        if rung is None:
            max_rg = run_rga()
            if max_rg is None:
                max_rg = 64.0
            rung = max_rg
        else:
            putpar("RG", str(rung))
        t0 = now_local()
        ocxo_s = ocxo_expected_s(TD_LADDER, SWH_HZ, 1, 1, D1_REF_S, 1)
        cb = clock_block_begin(expno, "rg_ladder", ocxo_s)
        run_zg_and_wait(ds_path(cd), "RG ladder rung %d (RG=%s)"
                        % (i + 1, rung), ocxo_s)
        clock_block_end(cb)
        record_experiment(meta, expno, "rg_ladder", t0, now_local(), 1)
        meta["calibration"]["rg_ladder"].append(
            {"expno": expno, "rg": rung, "tip_deg": 1.0})
        i = i + 1
    if max_rg is None:
        max_rg = 64.0

    # ---------------------------------------------------------------- 9
    # Reference (open): pseudo-2D small-flip, 8 rows x ~19 s, moderate RG.
    aq_row = TD_ROW / (2.0 * SWH_HZ)
    say("expno %d: reference_open (%d rows x %.0f s)"
        % (EXP_REF_OPEN, REF_ROWS, aq_row))
    moderate_rg = max_rg / 4.0
    if moderate_rg < 1.0:
        moderate_rg = 1.0
    cd = open_expno(template, dsname, EXP_REF_OPEN)
    putpar("PULPROG", "zg2d")     # any standard small-flip 2D works; the
    # rows are stored serially exactly like the noise block.
    make_2d(REF_ROWS)
    set_common_acq(o1_hz, TD_ROW, SWH_HZ, 1, D1_REF_S)
    set_small_flip(p90_us, p90_db, db_par)
    putpar("RG", str(moderate_rg))
    t0 = now_local()
    ocxo_s = ocxo_expected_s(TD_ROW, SWH_HZ, 1, REF_ROWS, D1_REF_S, 1)
    cb = clock_block_begin(EXP_REF_OPEN, "reference_open", ocxo_s)
    run_zg_and_wait(ds_path(cd), "reference_open", ocxo_s)
    clock_block_end(cb)
    record_experiment(meta, EXP_REF_OPEN, "reference_open",
                      t0, now_local(), REF_ROWS)

    # ---------------------------------------------------------------- 10
    # Noise block: zgnoise2d, NO pulse, NS=1/row, RG = max stable,
    # rows sized to the requested duration.
    row_secs = aq_row + 2.0 * D1_NOISE_S + ROW_OVERHEAD_S
    n_rows = int(noise_secs / row_secs)
    if n_rows < 4:
        n_rows = 4
    say("expno %d: NOISE block, %d rows x %.0f s (~%.0f min)"
        % (EXP_NOISE, n_rows, row_secs, n_rows * row_secs / 60.0))
    cd = open_expno(template, dsname, EXP_NOISE)
    putpar("PULPROG", PP_NAME)
    make_2d(n_rows)
    set_common_acq(o1_hz, TD_ROW, SWH_HZ, 1, D1_NOISE_S)
    # RG for the noise block: rga on this (pulse-free) experiment finds
    # the maximum stable gain, then it stays FIXED for the whole block.
    noise_rg = run_rga()
    if noise_rg is None:
        noise_rg = max_rg
    putpar("RG", str(noise_rg))
    t0 = now_local()
    MSG("The pulse-free noise block starts when you close this message.\n\n"
        "  rows      : %d\n"
        "  per row   : %.0f s\n"
        "  total     : ~%.0f min\n"
        "  RG        : %s\n\n"
        "You can walk away now.  A final dialog will appear when the\n"
        "bundle zip is ready." % (n_rows, row_secs,
                                  n_rows * row_secs / 60.0, noise_rg),
        "spin-noise run: noise block starting")
    # zgnoise2d spends TWO d1 delays per row (before go, before wr).
    ocxo_s = ocxo_expected_s(TD_ROW, SWH_HZ, 1, n_rows, D1_NOISE_S, 2)
    cb = clock_block_begin(EXP_NOISE, "noise", ocxo_s)
    run_zg_and_wait(ds_path(cd), "noise block (%d rows)" % n_rows, ocxo_s)
    clock_block_end(cb)
    record_experiment(meta, EXP_NOISE, "noise", t0, now_local(), n_rows)

    # ---------------------------------------------------------------- 11
    # Reference (close): identical to reference_open.
    say("expno %d: reference_close" % EXP_REF_CLOSE)
    cd = open_expno(template, dsname, EXP_REF_CLOSE)
    putpar("PULPROG", "zg2d")
    make_2d(REF_ROWS)
    set_common_acq(o1_hz, TD_ROW, SWH_HZ, 1, D1_REF_S)
    set_small_flip(p90_us, p90_db, db_par)
    putpar("RG", str(moderate_rg))
    t0 = now_local()
    ocxo_s = ocxo_expected_s(TD_ROW, SWH_HZ, 1, REF_ROWS, D1_REF_S, 1)
    cb = clock_block_begin(EXP_REF_CLOSE, "reference_close", ocxo_s)
    run_zg_and_wait(ds_path(cd), "reference_close", ocxo_s)
    clock_block_end(cb)
    record_experiment(meta, EXP_REF_CLOSE, "reference_close",
                      t0, now_local(), REF_ROWS)

    # ---------------------------------------------------------------- 12
    # Final operator notes.
    fn = INPUT_DIALOG(
        "spin-noise run: notes",
        "Anything unusual during the run?\n(construction nearby, elevator, "
        "He fill, lock left on, ...)\nLeave blank if all was quiet.",
        ["Notes"], [""], [""], ["1"])
    if fn is not None:
        meta["environment"]["operator_notes"] = fn[0].strip()

    # ---------------------------------------------------------------- 13
    # Bundle: staging dir -> checksums -> meta.json -> zip.
    say("packing the bundle")
    # dataset NAME directory = parent of any expno directory
    name_dir = os.path.dirname(ds_path(cd))
    stage = os.path.join(name_dir, "bundle_stage")
    data_stage = os.path.join(stage, "data")
    if not os.path.isdir(data_stage):
        os.makedirs(data_stage)

    all_expnos = [EXP_SETUP] + EXP_LADDER \
        + [EXP_REF_OPEN, EXP_NOISE, EXP_REF_CLOSE]
    for expno in all_expnos:
        src = os.path.join(name_dir, str(expno))
        if os.path.isdir(src):
            say("copying expno %d into bundle" % expno)
            copy_tree(src, os.path.join(data_stage, str(expno)))
        else:
            print "spin_noise_run: WARNING expno dir missing: %s" % src

    # Checksums over everything under data/ (relative, forward slashes).
    say("computing SHA-256 checksums")
    meta["checksums"] = {}

    def _walk2(cur, rel):
        names = os.listdir(cur)
        names.sort()
        for nm in names:
            fs = os.path.join(cur, nm)
            if rel:
                r2 = rel + "/" + nm
            else:
                r2 = nm
            if os.path.isdir(fs):
                _walk2(fs, r2)
            else:
                meta["checksums"]["data/" + r2] = sha256_file(fs)

    _walk2(data_stage, "")

    meta_text = json_dumps(meta) + "\n"
    # meta.json goes BOTH into the staging root (bundled) and into the
    # dataset directory itself (local provenance).
    f = open(os.path.join(stage, "meta.json"), "w")
    f.write(meta_text)
    f.close()
    f = open(os.path.join(name_dir, "meta.json"), "w")
    f.write(meta_text)
    f.close()

    bundle_name = "spinnoise_%s_%s_%s.zip" \
        % (facility_slug, utc_stamp_compact(), rand4hex())
    bundle_path = os.path.join(name_dir, bundle_name)
    say("zipping -> %s" % bundle_name)
    zip_directory(stage, bundle_path)

    # ---------------------------------------------------------------- 14
    # Done.
    MSG("Done!  Started %s, finished %s.\n\n"
        "Bundle written to:\n  %s\n\n"
        "To upload it, run on any computer with Python 3:\n\n"
        "  python3 uploader/upload_bundle.py %s\n\n"
        "(uploader/ is in the spin-noise-network distribution you got\n"
        "this script from; see its config.example.json for the endpoint\n"
        "and token.)  If upload is impossible, KEEP THE ZIP and e-mail\n"
        "the maintainers.\n\nThank you for contributing your probe's "
        "noise!" % (t_run_start, now_local(), bundle_path, bundle_name),
        "spin-noise run: complete")


# ----------------------------------------------------------------------------
# Entry point with a last-ditch catcher: anything unexpected surfaces as a
# readable dialog instead of a silent Jython stack trace in the console.
# ----------------------------------------------------------------------------
try:
    main()
except SystemExit:
    pass
except Exception:
    _tb = ""
    try:
        _tb = traceback.format_exc()
    except Exception:
        _tb = "(traceback unavailable)"
    try:
        ERRMSG("spin_noise_run crashed unexpectedly.\n\n"
               "No hardware is left running: any experiment already\n"
               "started will finish on its own and the data stays in\n"
               "the SPINNOISE_* dataset.\n\nDetails:\n" + _tb,
               "spin_noise_run: error", None, 1)
    except Exception:
        print _tb
