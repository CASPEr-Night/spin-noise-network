#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
static_check.py -- Tier-0 static checks for topspin/spin_noise_run.py.

The run script executes only inside TopSpin's embedded Jython, so it can
never be imported or unit-tested here. What CAN be checked from plain
Python 3, and is checked, in order:

  1. SYNTAX: the script compiles. Jython-compat note: the script targets
     Jython 2.x, and the only Python-2-only construct it uses is the
     print STATEMENT (verified by inspection; no `except X, e`, no
     backticks, no octal 0NNN literals). We mechanically rewrite
     `print <args>` -> `print(<args>)` before handing the source to
     compile(), so a pass means "valid Python apart from py2 prints" --
     the closest available proxy for Jython syntax without a Jython.
  2. VERSION SYNC: SCRIPT_VERSION in the script equals the repository
     VERSION file, UPLOADER_VERSION in uploader/upload_bundle.py and
     PACKER_VERSION in packer/pack_bundle.py equal the VERSION file.
     SCHEMA SYNC: the shipped schema const is the current schema (2.0,
     vendor-neutral, written by the packer); the TopSpin orchestrator
     still writes 1.2 (the last Bruker-only schema), so its
     SCHEMA_VERSION must be a member of the uploader's
     SUPPORTED_SCHEMA_VERSIONS, and the packer's SCHEMA_VERSION must
     equal the schema const.
  3. HARDWARE GUARDING: every spectrometer-hardware command goes through
     the guarded wrapper (safe_hw_cmd / the hw_skip()-guarded ZG() in
     run_zg_and_wait), so SIMULATE and DESKTEST can never touch hardware:
       - XCMD( appears only inside safe_xcmd (and the desk-test stub);
       - safe_xcmd( is called only from safe_hw_cmd;
       - safe_hw_cmd checks hw_skip() before delegating;
       - ZG() appears only inside run_zg_and_wait, after an
         `if hw_skip():` guard that returns;
       - every command named in HW_COMMANDS has at least one callsite
         routed via safe_hw_cmd(...) or xcmd_or_dialog(...).
  4. META STAMPING: the meta.json writer emits the schema-1.1 "software"
     object with script_version/schema_version/script_sha256, and the
     schema-1.2 "clock_audit" object (blocks + NTP status), with every
     audited acquisition wrapped in clock_block_begin/_end.

Usage:  python3 testing/static_check.py     (exit 0 iff all green)
"""

import json
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SCRIPT_PATH = os.path.join(REPO, "topspin", "spin_noise_run.py")
VERSION_PATH = os.path.join(REPO, "VERSION")
SCHEMA_PATH = os.path.join(REPO, "schema", "meta.schema.json")
UPLOADER_PATH = os.path.join(REPO, "uploader", "upload_bundle.py")
PACKER_PATH = os.path.join(REPO, "packer", "pack_bundle.py")
ENTRY_PATH = os.path.join(REPO, "testing", "jython_entry.py")
STUB_PATH = os.path.join(REPO, "testing", "topspin_stub.py")
HARNESS_SH_PATH = os.path.join(REPO, "testing", "run_jython_harness.sh")

FAILURES = []


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    line = "%s : %s" % (tag, name)
    if detail and not ok:
        line += "\n       %s" % detail
    print(line)
    if not ok:
        FAILURES.append(name)


# --------------------------------------------------------------------------
# Load the source
# --------------------------------------------------------------------------

with open(SCRIPT_PATH, "r", encoding="utf-8") as fh:
    SRC = fh.read()
LINES = SRC.splitlines()


def is_comment(line):
    return line.lstrip().startswith("#")


# --------------------------------------------------------------------------
# 1. Syntax via compile(), after py2-print rewrite
# --------------------------------------------------------------------------

def rewrite_prints(lines):
    """Rewrite py2 `print <args>` statements (incl. backslash
    continuations) into `print(<args>)` calls, purely for compile()."""
    out = []
    i = 0
    pat = re.compile(r"^(\s*)print\s+(?!\()(.*)$")
    while i < len(lines):
        line = lines[i]
        m = pat.match(line)
        if m and not is_comment(line):
            indent, payload = m.group(1), m.group(2)
            parts = [payload]
            while parts[-1].rstrip().endswith("\\"):
                parts[-1] = parts[-1].rstrip()[:-1]
                i += 1
                parts.append(lines[i].strip())
                out.append("")  # keep the line count stable
            out.append("%sprint(%s)" % (indent, " ".join(p.strip() for p in parts)))
        else:
            out.append(line)
        i += 1
    return "\n".join(out) + "\n"


syntax_ok, syntax_err = True, ""
try:
    compile(rewrite_prints(LINES), SCRIPT_PATH, "exec")
except SyntaxError as exc:
    syntax_ok, syntax_err = False, "line %s: %s" % (exc.lineno, exc.msg)
check("syntax: compiles after py2-print rewrite (Jython-compat proxy)",
      syntax_ok, syntax_err)

# Jython 2.2 grammar guards that a Python-3 compile() cannot catch: a
# conditional expression (PEP 308, Python 2.5+) bricks the whole module
# at load on TopSpin 2.x consoles -- the review of 2026-09-03 caught
# exactly this in a fresh edit. AST-based: comments/strings never
# false-positive.
_tree = None
try:
    import ast
    _tree = ast.parse(rewrite_prints(LINES))
    _ternaries = [n.lineno for n in ast.walk(_tree)
                  if isinstance(n, ast.IfExp)]
    _withs = [n.lineno for n in ast.walk(_tree)
              if isinstance(n, (ast.With, ast.GeneratorExp, ast.SetComp,
                                ast.DictComp))]
    check("jython 2.2: no conditional expressions (PEP 308) in the "
          "orchestrator", not _ternaries, "lines %s" % _ternaries)
    check("jython 2.2: no with/genexp/set-comp/dict-comp in the "
          "orchestrator", not _withs, "lines %s" % _withs)
except SyntaxError:
    pass          # already reported by the compile check above

# Gyromagnetic-ratio table consistency: h1_freq_mhz (the cross-site
# field coordinate) is computed from this table, so its entries must
# stay on the IUPAC frequency-ratio system to ~1e-5 -- a mixed-
# provenance value puts a 19F session's coordinate ~3e-4 off its own
# magnet's 1H sessions (review finding, 2026-09-03).
m = re.search(r"NUC_GAMMA_MHZ_T\s*=\s*\{(.*?)\}", SRC, re.S)
gamma_ok, gamma_msg = False, "table not found"
if m:
    entries = dict(re.findall(r'"([^"]+)":\s*(-?[0-9.]+)', m.group(1)))
    iupac_xi = {"1H": 100.000000, "19F": 94.094011, "2H": 15.350609,
                "13C": 25.145020, "31P": 40.480742, "15N": -10.136767,
                "7Li": 38.863797, "23Na": 26.451900}
    bad = []
    for k, xi in iupac_xi.items():
        if k in entries:
            want = xi / 100.0 * 42.5774806
            got = float(entries[k])
            if abs(got - want) > 1e-4 * abs(want):
                bad.append("%s: %.6f vs IUPAC %.6f" % (k, got, want))
    gamma_ok, gamma_msg = not bad, "; ".join(bad)
check("gamma table: NUC_GAMMA_MHZ_T consistent with IUPAC frequency "
      "ratios (1e-4)", gamma_ok, gamma_msg)


# --------------------------------------------------------------------------
# 2. Version sync
# --------------------------------------------------------------------------

with open(VERSION_PATH, "r", encoding="utf-8") as fh:
    version_file = fh.read().strip()

m = re.search(r'^SCRIPT_VERSION\s*=\s*"([^"]+)"', SRC, re.M)
script_version = m.group(1) if m else None
check("version: SCRIPT_VERSION (%r) == VERSION file (%r)"
      % (script_version, version_file),
      script_version == version_file and script_version is not None)

m = re.search(r'^PROGRAM_VERSION\s*=\s*SCRIPT_VERSION', SRC, re.M)
check("version: PROGRAM_VERSION aliases SCRIPT_VERSION (single source)",
      m is not None)

with open(UPLOADER_PATH, "r", encoding="utf-8") as fh:
    UPLOADER_SRC = fh.read()
m = re.search(r'^UPLOADER_VERSION\s*=\s*"([^"]+)"', UPLOADER_SRC, re.M)
uploader_version = m.group(1) if m else None
check("version: uploader UPLOADER_VERSION (%r) == VERSION file (%r)"
      % (uploader_version, version_file),
      uploader_version == version_file and uploader_version is not None)

with open(PACKER_PATH, "r", encoding="utf-8") as fh:
    PACKER_SRC = fh.read()
m = re.search(r'^PACKER_VERSION\s*=\s*"([^"]+)"', PACKER_SRC, re.M)
packer_version = m.group(1) if m else None
check("version: packer PACKER_VERSION (%r) == VERSION file (%r)"
      % (packer_version, version_file),
      packer_version == version_file and packer_version is not None)

with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
    schema = json.load(fh)
schema_const = schema.get("properties", {}).get("schema_version", {}).get("const")
check("schema: shipped schema const is the current schema ('2.0', got %r)"
      % schema_const, schema_const == "2.0")

m = re.search(r'^SCHEMA_VERSION\s*=\s*"([^"]+)"', PACKER_SRC, re.M)
packer_schema = m.group(1) if m else None
check("schema: packer SCHEMA_VERSION (%r) == schema const (%r)"
      % (packer_schema, schema_const),
      packer_schema == schema_const and packer_schema is not None)

# The TopSpin orchestrator deliberately still writes 1.2 (the last
# Bruker-only schema; a vendor-less bundle IS a Bruker bundle) -- it must
# stay within the uploader's supported set so its bundles keep validating.
m = re.search(r'^SUPPORTED_SCHEMA_VERSIONS\s*=\s*\(([^)]*)\)', UPLOADER_SRC, re.M)
supported = re.findall(r'"([^"]+)"', m.group(1)) if m else []
check("schema: uploader supports the current schema (%r in %r)"
      % (schema_const, supported), schema_const in supported)

m = re.search(r'^SCHEMA_VERSION\s*=\s*"([^"]+)"', SRC, re.M)
script_schema = m.group(1) if m else None
check("schema: orchestrator SCHEMA_VERSION (%r) is uploader-supported (%r)"
      % (script_schema, supported),
      script_schema in supported and script_schema is not None)

check("schema: vendor enum required with bruker/jeol/magritek/agilent/nanalysis (v2.0)",
      "vendor" in schema.get("required", [])
      and schema.get("properties", {}).get("vendor", {}).get("enum")
      == ["bruker", "jeol", "magritek", "agilent", "nanalysis"]
      and "instrument" in schema.get("required", []))

check("schema: every vendor enum value has an instrument.<vendor> block",
      set(schema.get("properties", {}).get("vendor", {}).get("enum") or [])
      <= set(schema.get("properties", {}).get("instrument", {})
             .get("properties", {})))


# --------------------------------------------------------------------------
# 3. Hardware guarding
# --------------------------------------------------------------------------

def owner_map(lines):
    """Map line index -> name of the top-level function owning it (or
    None at module level). Indentation-based; good enough for this file,
    which defines only flat module-level functions."""
    owners, current = [], None
    for line in lines:
        if re.match(r"^def\s+(\w+)", line):
            current = re.match(r"^def\s+(\w+)", line).group(1)
        elif line.strip() and not line[0].isspace() and not is_comment(line):
            current = None
        owners.append(current)
    return owners


OWNERS = owner_map(LINES)


def find_offenders(token, allowed_owners, skip_def_of=None):
    """Non-comment lines containing token whose owning function is not in
    allowed_owners. Lines defining skip_def_of are ignored."""
    bad = []
    for idx, line in enumerate(LINES):
        if token not in line or is_comment(line):
            continue
        if skip_def_of and re.match(r"^\s*def\s+%s\s*\(" % skip_def_of, line):
            continue
        if OWNERS[idx] not in allowed_owners:
            bad.append("line %d (in %s): %s"
                       % (idx + 1, OWNERS[idx], line.strip()))
    return bad


bad = find_offenders("XCMD(", {"safe_xcmd", "XCMD"}, skip_def_of="XCMD")
check("guard: XCMD( only inside safe_xcmd (+ desk stub)",
      not bad, "; ".join(bad))

bad = find_offenders("safe_xcmd(", {"safe_hw_cmd"}, skip_def_of="safe_xcmd")
check("guard: safe_xcmd( called only from safe_hw_cmd",
      not bad, "; ".join(bad))

bad = find_offenders("ZG()", {"run_zg_and_wait"})
check("guard: ZG() only inside run_zg_and_wait", not bad, "; ".join(bad))


def function_body(name):
    idxs = [i for i, o in enumerate(OWNERS) if o == name]
    return [LINES[i] for i in idxs], idxs


body, idxs = function_body("safe_hw_cmd")
text = "\n".join(body)
check("guard: safe_hw_cmd checks hw_skip() before delegating",
      "if hw_skip():" in text and text.find("if hw_skip():") < text.find("safe_xcmd("))

body, idxs = function_body("run_zg_and_wait")
guard_line = zg_line = ret_line = None
for j, line in enumerate(body):
    if "if hw_skip():" in line and guard_line is None:
        guard_line = j
    if guard_line is not None and ret_line is None and re.match(r"^\s+return\b", line):
        ret_line = j
    if "ZG()" in line and not is_comment(line) and zg_line is None:
        zg_line = j
check("guard: run_zg_and_wait has `if hw_skip(): ... return` before ZG()",
      guard_line is not None and ret_line is not None and zg_line is not None
      and guard_line < ret_line < zg_line)

m = re.search(r'^HW_COMMANDS\s*=\s*\(([^)]*)\)', SRC, re.M)
hw_commands = re.findall(r'"([^"]+)"', m.group(1)) if m else []
check("guard: HW_COMMANDS tuple declared", bool(hw_commands),
      "HW_COMMANDS not found")

# TopSpin's Jython namespace shadows the bare name Exception with
# java.lang.Exception, so an except clause naming bare Exception misses
# every Python exception on a real console (Torino, TopSpin 4.4.0,
# 2026-09-17).  Catch-alls must use the module's CATCHABLE tuple, which
# names both worlds.  AST-based, so comments and strings never
# false-positive, and so the tuple form `except (X, Exception):`,
# `raise Exception(...)` and `isinstance(e, Exception)` are all caught:
# EVERY bare-Name use of `Exception` in code is an offender (the tuple
# itself uses the attribute forms exceptions.Exception and
# java.lang.Exception).  A bare `except:` is refused too: it swallows
# SystemExit, i.e. abort()/EXIT().
if _tree is not None:
    _bare_exception = sorted(set(
        n.lineno for n in ast.walk(_tree)
        if isinstance(n, ast.Name) and n.id == "Exception"))
    _bare_except = sorted(set(
        h.lineno for n in ast.walk(_tree) if isinstance(n, ast.Try)
        for h in n.handlers if h.type is None))
    check("guard: no bare-name 'Exception' anywhere in the TopSpin script "
          "code (use CATCHABLE)", not _bare_exception,
          "lines %s -- TopSpin shadows Exception with java.lang.Exception"
          % _bare_exception)
    check("guard: no bare 'except:' in the TopSpin script (swallows SystemExit)",
          not _bare_except, "lines %s" % _bare_except)
check("guard: CATCHABLE = (exceptions.Exception, java.lang.Exception) declared, "
      "with the no-java fallback (exceptions module, not __builtin__)",
      re.search(r"^import exceptions\s*$", SRC, re.M) is not None
      and re.search(r"CATCHABLE\s*=\s*\(exceptions\.Exception,"
                    r"\s*java\.lang\.Exception\)", SRC) is not None
      and re.search(r"CATCHABLE\s*=\s*\(exceptions\.Exception,\)", SRC)
      is not None,
      "CATCHABLE tuple, its fallback, or 'import exceptions' not found")
check("guard: no '__builtin__.Exception' in the TopSpin script (a console "
      "may install java.lang names into __builtin__)",
      "__builtin__.Exception" not in SRC)
# The self-test must be at module level and fatal (EXIT() inside its block).
_st = [i for i, ln in enumerate(LINES)
       if ln.startswith("if not _catchable_selftest():")]
_st_fatal = False
if _st:
    j = _st[0] + 1
    while j < len(LINES) and (LINES[j].startswith((" ", "\t"))
                              or LINES[j].strip() == ""):
        if LINES[j].strip() == "EXIT()":
            _st_fatal = True
        j += 1
check("guard: import-time CATCHABLE self-test present at module level and "
      "fatal (EXIT())",
      "def _catchable_selftest():" in SRC and bool(_st) and _st_fatal)

# The harness can only see this bug class because it reproduces TopSpin's
# namespace and answers the temperature dialog blank; pin both so a later
# cleanup of jython_entry.py cannot silently revert the reproduction.
with open(ENTRY_PATH, "r", encoding="utf-8") as fh:
    ENTRY_SRC = fh.read()
check("guard: the Jython harness shadows Exception in the script's globals "
      "(reproduces the TopSpin namespace)",
      re.search(r'script_globals\["Exception"\]\s*=\s*java\.lang\.Exception',
                ENTRY_SRC) is not None,
      "testing/jython_entry.py no longer injects java.lang.Exception")
check("guard: the Jython harness answers the probe-temperature dialog blank "
      "(the Torino input)",
      re.search(r'"spin-noise run: probe temperatures \(optional\)":\s*'
                r'\[u"",\s*u""\]', ENTRY_SRC) is not None,
      "testing/jython_entry.py no longer leaves the temperature fields blank")
# TopSpin validates enumerated parameters written from Python by NAME
# (Torino, TopSpin 4.4.0, 2026-09-18: PUTPAR("PARMODE", "1") raised; the
# same console rejected "1 FnMODE"), and every rejected PUTPAR pops the
# console's own error dialog.  PARMODE and the F1 parameters may therefore
# be set only through the dialect-aware helpers (name only, readback-
# verified, reload after the switch, each rejected form probed once, the
# first switch while the operator is present), FnMODE is never written
# (Bruker: 'undefined' is mandatory without an mc statement), and what
# the console accepted is stamped into meta.json.
bad = find_offenders('putpar("PARMODE"', {"set_parmode"})
check("guard: putpar(\"PARMODE\" only inside set_parmode", not bad,
      "; ".join(bad))
bad = find_offenders("PUTPAR(", {"putpar"}, skip_def_of="PUTPAR")
check("guard: PUTPAR( only inside putpar (+ desk stub)", not bad,
      "; ".join(bad))
bad = find_offenders('"1 TD"', {None, "set_f1_td", "f1_td_readback",
                                "f1_td_form_rejected"})
check("guard: F1 TD addressed only via set_f1_td / f1_td_readback "
      "(verified readback, bounded operator fallback)", not bad,
      "; ".join(bad))
bad = find_offenders("FnMODE", set())
check("guard: FnMODE never written or read by the script (Bruker requires "
      "'undefined' without an mc statement)", not bad, "; ".join(bad))
bad = find_offenders("GETACQUDIM(", {"acqu_dim_readback"})
check("guard: GETACQUDIM( only inside acqu_dim_readback (wrapped: absent "
      "on old TopSpin)", not bad, "; ".join(bad))
_re_bad = []
for _idx, _line in enumerate(LINES):
    if is_comment(_line) or not re.search(r"(?<![A-Za-z0-9_])RE\(", _line):
        continue
    if re.match(r"^\s*def\s+RE\s*\(", _line):
        continue
    if OWNERS[_idx] not in (None, "open_expno", "reopen_expno",
                            "reload_current_dataset"):
        _re_bad.append("line %d (in %s): %s"
                       % (_idx + 1, OWNERS[_idx], _line.strip()))
check("guard: RE( only inside open_expno / reopen_expno / "
      "reload_current_dataset (+ desk stub)", not _re_bad, "; ".join(_re_bad))
_m2 = "\n".join(function_body("make_2d")[0])
_m1 = "\n".join(function_body("make_1d")[0])
_sp = "\n".join(function_body("set_parmode")[0])
_sf = "\n".join(function_body("set_f1_td")[0])
_po = "\n".join(function_body("parmode_operator_dialog")[0])
check("guard: make_2d/make_1d route PARMODE through set_parmode + "
      "parmode_operator_dialog and make_2d verifies F1 TD",
      "set_parmode(2)" in _m2 and "parmode_operator_dialog(2)" in _m2
      and "set_f1_td(rows)" in _m2
      and "set_parmode(1)" in _m1 and "parmode_operator_dialog(1)" in _m1)
check("guard: set_parmode recognises an already-2D dataset, writes the enum "
      "NAME, reloads (RE) and reads back; never re-probes a rejected form",
      "if acqu_dim_readback() == ndim:" in _sp
      and "PARMODE_NAME[ndim]" in _sp and "reload_current_dataset()" in _sp
      and "_form_has_failed(" in _sp and '"ordinal"' not in _sp)
_ar = "\n".join(function_body("acqu_dim_readback")[0])
check("guard: dimensionality readback consults GETPAR PARMODE (console-"
      "confirmed ordinal) before GETACQUDIM, and honours the unreliable flag",
      0 < _ar.find('pm = getpar("PARMODE")') < _ar.find("d = to_int(GETACQUDIM(), None)")
      and 'if PARAM_API["dim_readback_unreliable"]:' in _ar)
check("guard: a readback that contradicts the operator twice is flagged "
      "unreliable (no repeated dialogs later)",
      'PARAM_API["dim_readback_unreliable"] = 1' in _po
      and 'PARAM_API["f1_readback_unreliable"] = 1' in _sf
      and 'if PARAM_API["f1_readback_unreliable"]:'
      in "\n".join(function_body("f1_td_readback")[0]))
check("guard: set_f1_td recognises an F1 TD that already reads rows "
      "(inherited / probed) before writing",
      _sf.find("rb, src = f1_td_readback(td_direct)") < _sf.find('putpar("1 TD"'))
check("guard: every pseudo-2D / quick-1D creation WR()s from a template of "
      "the right dimensionality (no PARMODE write per dataset)",
      "ensure_template_dim(template, dsname, 1)"
      in "\n".join(function_body("acquire_quick_1d")[0])
      and "ensure_template_dim(template, dsname, 2)"
      in "\n".join(function_body("run_field_sweep")[0])
      and SRC.count("ensure_template_dim(template, dsname, 2)") >= 3)
check("guard: the probe clears the template's raw data before the switch "
      "and warns the operator when their help was needed",
      SRC.find("clear_raw_data(ds_path(cd_probe))") < SRC.find("make_2d(REF_ROWS)")
      and "dataset setup on this console" in SRC)
check("guard: operator fallbacks are bounded (attempts >= 2) in set_f1_td "
      "and parmode_operator_dialog",
      "attempts >= 2" in _sf and "attempts >= 2" in _po)
check("guard: F1 TD readback distrusts a value equal to the direct TD "
      "(prefix-ignoring console) and never traps the operator",
      "str(v) != str(td_direct)" in "\n".join(function_body("f1_td_readback")[0]))
_i_probe = SRC.find("dialect probe: creating expno")
_i_p90 = SRC.find('"spin-noise run: 90-degree pulse"')
check("guard: the first dimensionality switch (dialect probe) happens "
      "BEFORE the 90-degree dialog, while the operator is present",
      0 < _i_probe < _i_p90)
check("guard: the opening reference is created once (at the probe) and "
      "RE-opened, not WR-overwritten, in section 9",
      SRC.count("= open_expno(template, dsname, EXP_REF_OPEN)") == 1
      and "cd = reopen_expno(template, dsname, EXP_REF_OPEN)" in SRC)
check("meta: software.param_api stamped (what this console accepted)",
      '"param_api": PARAM_API' in SRC)
check("meta: schema documents software.param_api (optional)",
      "param_api" in schema.get("properties", {}).get("software", {})
      .get("properties", {})
      and "param_api" not in schema["properties"]["software"].get("required", []))
# Pin the harness reproduction of the 4.4 console, as for the namespace bug.
with open(STUB_PATH, "r", encoding="utf-8") as fh:
    STUB_SRC = fh.read()
with open(HARNESS_SH_PATH, "r", encoding="utf-8") as fh:
    SH_SRC = fh.read()
check("guard: the stub models the console -- PARMODE by name only "
      "(GetEnuOrd), ordinal readback, GETACQUDIM, the missing/stale F1 map, "
      "the strict and echo variants",
      "GetEnuOrd[PARMODE]" in STUB_SRC
      and "parameter not found in map" in STUB_SRC
      and "FnMODE deliberately absent" in STUB_SRC
      and "_PARMODE_TO_ORDINAL" in STUB_SRC and "def GETACQUDIM" in STUB_SRC
      and "_F1_FRESH" in STUB_SRC and '"ts44-strict"' in STUB_SRC
      and '"ts44-f1echo"' in STUB_SRC and '"ts44-dimlie"' in STUB_SRC
      and '"ts44-f1route"' in STUB_SRC and '"ts44-f1mismatch"' in STUB_SRC
      and 'raise NameError("GETACQUDIM")' in STUB_SRC)
check("guard: the harness runs all eight console flavors, the feature "
      "variant also under the strict flavor",
      all(f in SH_SRC for f in ('"legacy simulate"', '"legacy desktest"',
                                '"ts44 desktest"', '"ts44-stale desktest"',
                                '"ts44-strict desktest"',
                                '"ts44-f1echo desktest"',
                                '"ts44-dimlie desktest"',
                                '"ts44-f1route desktest"',
                                '"ts44-f1mismatch desktest"',
                                '"ts44-strict desktest rdopt sweep autostep"'))
      and "HARNESS_TS_FLAVOR" in SH_SRC and "HARNESS_TS_FLAVOR" in ENTRY_SRC,
      "testing/run_jython_harness.sh or jython_entry.py no longer run the flavors")
check("guard: the harness asserts probed-once, an actual readback, the "
      "attended operator steps and the unreliable-readback flags",
      "no stray dialog repeats" in ENTRY_SRC
      and "readback actually happened" in ENTRY_SRC
      and "operator steps happen BEFORE the 90-degree" in ENTRY_SRC
      and "flagged unreliable after two" in ENTRY_SRC)
for cmd in hw_commands:
    routed = re.search(
        r'(safe_hw_cmd|xcmd_or_dialog)\(\s*\n?\s*"%s' % re.escape(cmd), SRC)
    check("guard: '%s' issued via safe_hw_cmd/xcmd_or_dialog" % cmd,
          routed is not None)

check("guard: DESKTEST flag exists and hw_skip() covers SIMULATE and DESKTEST",
      re.search(r"^DESKTEST\s*=", SRC, re.M) is not None
      and "if SIMULATE:" in "\n".join(function_body("hw_skip")[0])
      and "if DESKTEST:" in "\n".join(function_body("hw_skip")[0]))


# --------------------------------------------------------------------------
# 4. Meta stamping
# --------------------------------------------------------------------------

check("meta: software object emitted with script_version/schema_version/sha256",
      '"software": {' in SRC
      and '"script_version": SCRIPT_VERSION' in SRC
      and '"schema_version": SCHEMA_VERSION' in SRC
      and '"script_sha256": script_self_sha256()' in SRC)

check("meta: schema requires the software object (v1.1)",
      "software" in schema.get("required", []))

check("meta: clock_audit object emitted with blocks + NTP status (v1.2)",
      '"clock_audit": {' in SRC
      and '"blocks": CLOCK_BLOCKS' in SRC
      and '"ntp_status_raw": ntp_raw' in SRC
      and '"workstation_time_source": ntp_source' in SRC)

check("meta: schema keeps clock_audit OPTIONAL (backward compatible)",
      "clock_audit" in schema.get("properties", {})
      and "clock_audit" not in schema.get("required", []))

n_begin = len(re.findall(r"clock_block_begin\(", SRC))
n_end = len(re.findall(r"clock_block_end\(", SRC))
# 5 call sites for each (setup, ladder loop, ref_open, noise, ref_close)
# plus the two function definitions themselves.
check("clock audit: every audited block has a begin AND an end "
      "(%d/%d call sites)" % (n_begin - 1, n_end - 1),
      n_begin == n_end and n_begin >= 6)

check("clock audit: mocked acquisitions feed the harness clock "
      "(harness_clock_advance wired into run_zg_and_wait)",
      "harness_clock_advance(ocxo_s)"
      in "\n".join(function_body("run_zg_and_wait")[0]))


# --------------------------------------------------------------------------
print("")
if FAILURES:
    print("%d CHECK(S) FAILED" % len(FAILURES))
    sys.exit(1)
print("ALL CHECKS PASSED")
sys.exit(0)
