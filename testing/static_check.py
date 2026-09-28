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
  5. PULSE PROGRAMS (v0.7.6): the texts embedded in the script equal the
     shipped topspin/pp/ files byte for byte, neither project pulse
     program computes a delay (Bruker's zg2d does -- "DELTA=d20-..." --
     and failed at Torino), the installer writes both files, every
     reference block names PP_REF_NAME (zgref2d), and the timing model
     carries zgref2d's fixed 30 ms per row explicitly; the harness, the
     report parser and the physics fixture are pinned to the same model.

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
check("version: no PROGRAM_VERSION alias (TopCmds exports a function of that "
      "name; meta.json takes SCRIPT_VERSION directly)",
      re.search(r"^PROGRAM_VERSION\s*=", SRC, re.M) is None
      and '"program_version": SCRIPT_VERSION' in SRC)

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
# FnMODE may be read AND written only inside the F1-file helpers -- the
# copy path (ensure_f1_files), the inherited-file path
# (ensure_f1_fnmode_undefined), the shared edit (_set_fnmode_undefined),
# its recorder (_record_fnmode_edit) and make_2d, which orders them --
# and there only as a plain text edit of the acqu2 file: a library set or
# a 2D template carries the mode of ITS experiment, and Bruker's rule is
# that 'undefined' (0) must be used when the pulse program has no mc
# statement, true of zgref2d and zgnoise2d.  Never through the parameter API:
# TopSpin 4.4.0 rejected PUTPAR("1 FnMODE", ...) (Torino, 2026-09-18) and
# v0.7.3's rule 'never PUTPAR FnMODE' stands.
_FN_OWNERS = {"ensure_f1_files", "ensure_f1_fnmode_undefined",
              "_set_fnmode_undefined", "_record_fnmode_edit", "make_2d"}
bad = find_offenders("FnMODE", _FN_OWNERS)
_fn_api = [ln for own in _FN_OWNERS for ln in function_body(own)[0]
           if "FnMODE" in ln and not is_comment(ln)
           and re.search(r"(putpar|PUTPAR|getpar|GETPAR)\s*\(", ln)]
_fn_body = "\n".join(function_body("_set_fnmode_undefined")[0])
check("guard: FnMODE read/written only inside the F1-file helpers and "
      "make_2d, never through the parameter API (Bruker requires "
      "'undefined' without an mc statement; v0.7.3: never PUTPAR FnMODE); "
      "the edit is binary in and out so line endings survive a Windows "
      "console, and reports whether it wrote anything",
      not bad and not _fn_api and 'tag = "##$FnMODE="' in _fn_body
      and 'tag + " 0\\n"' in _fn_body
      and 'open(acqu2_path, "rb")' in _fn_body
      and 'open(acqu2_path, "wb")' in _fn_body
      and 'open(acqu2_path, "r")' not in _fn_body
      and 'open(acqu2_path, "w")' not in _fn_body
      and 'if seen and was == "0":' in _fn_body
      and "return was, 0" in _fn_body and "return was, 1" in _fn_body
      and re.search(r"\b(putpar|PUTPAR|getpar|GETPAR)\s*\(", _fn_body) is None,
      "; ".join(bad + _fn_api))
_ef0 = "\n".join(function_body("ensure_f1_files")[0])
_rec = "\n".join(function_body("_record_fnmode_edit")[0])
check("guard: ensure_f1_files sets FnMODE undefined in the copy BEFORE the "
      "dataset is re-read (RE) and records the edit (f1_fnmode_edits; "
      "f1_fnmode_copied keeps the first file's value)",
      0 < _ef0.find("_set_fnmode_undefined(acqu2)")
      < _ef0.find("_record_fnmode_edit(was)")
      < _ef0.find("reload_current_dataset()")
      and 'PARAM_API["f1_fnmode_edits"]' in _rec
      and 'PARAM_API["f1_fnmode_copied"]' in _rec
      and 'if PARAM_API["f1_fnmode_edits"] == 1:' in _rec)
_efn = "\n".join(function_body("ensure_f1_fnmode_undefined")[0])
check("guard: ensure_f1_fnmode_undefined edits an INHERITED acqu2 that says "
      "anything but 0 (absent = undefined, left alone), never the "
      "operator's template dataset, by the same file edit BEFORE one RE, "
      "and reloads nothing when nothing changed; no dialog",
      0 < _efn.find("SESSION_TEMPLATE[0]")
      < _efn.find('jcamp_value(acqu2, "FnMODE")')
      < _efn.find('if was is None or was == "0":')
      < _efn.find("_set_fnmode_undefined(acqu2)")
      < _efn.find("if not changed:")
      < _efn.find("_record_fnmode_edit(was)")
      < _efn.find("reload_current_dataset()")
      and "CONFIRM(" not in _efn and "MSG(" not in _efn)
# Only the parameter files travel with the copy: a status file (acqu2s /
# proc2s) describes acquired data -- zg writes it -- and a copied one would
# describe the library set's experiment.
check("guard: the F1-file copy carries acqu2 and proc2 only, never the "
      "status files acqu2s / proc2s",
      re.search(r'^F1_ACQ_FILES\s*=\s*\("acqu2",\)', SRC, re.M) is not None
      and re.search(r'^F1_PROC_FILES\s*=\s*\("proc2",\)', SRC, re.M) is not None
      and "acqu2s" not in "\n".join(function_body("_copy_f1_files")[0])
      and "proc2s" not in "\n".join(function_body("_copy_f1_files")[0]))
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
# Oulu, TopSpin 3.7.0, 2026-09-25: PARMODE 2D accepted, acqu2 never
# created; the console popped its own dialog once per run (trigger not
# established -- an F1 GETPAR is one candidate) and every parameter write
# into the dataset was lost silently.  make_2d must make
# the file exist between the switch and the row count, the F1 readback
# must never GETPAR while it is absent, and TD/RG must be read back after
# writing (RG only through set_rg, tolerant of the console's gain ladder).
_fr = "\n".join(function_body("f1_td_readback")[0])
_ef = "\n".join(function_body("ensure_f1_files")[0])
_sc = "\n".join(function_body("set_common_acq")[0])
check("guard: make_2d calls ensure_f1_files, then ensure_f1_fnmode_undefined, "
      "between the PARMODE switch and set_f1_td (the F1 parameter file must "
      "exist, and say FnMODE undefined, before any F1 access)",
      0 < _m2.find("parmode_operator_dialog(2)") < _m2.find("ensure_f1_files(")
      < _m2.find("ensure_f1_fnmode_undefined()")
      < _m2.find("set_f1_td(rows)"))
check("guard: f1_td_readback skips GETPAR while acqu2 is absent (a "
      "candidate trigger of the console dialog Oulu saw; TopSpin 3.7.0)",
      0 < _fr.find("if d and not f1_files_present(d):")
      < _fr.find("raw = GETPAR(name)"))
check("guard: ensure_f1_files copies acqu2 from template / session expno / "
      "the console's parameter library, reloads (RE), records the source, "
      "and falls back to the operator's parmode ONCE per session (a later "
      "make_2d runs unattended: no dialog there)",
      "_f1_source_candidates(" in _ef and "reload_current_dataset()" in _ef
      and 'PARAM_API["f1_files_source"]' in _ef
      and 'PARAM_API["f1_files_created"]' in _ef
      and "parmode_operator_dialog(" in _ef
      and 0 < _ef.find('if PARAM_API["f1_files_source"] == "operator":')
      < _ef.find("parmode_operator_dialog(")
      and '"template"' in SRC and '"expno:%d"' in SRC and '"par:"' in SRC
      and "find_tshome_candidates()" in "\n".join(
          function_body("_f1_source_candidates")[0]))
check("guard: find_pp_user_dir shares TSHOME discovery with the parameter-"
      "library lookup (find_tshome_candidates)",
      "find_tshome_candidates()" in "\n".join(function_body("find_pp_user_dir")[0])
      and 'getProperty("XWINNMRHOME")' in "\n".join(
          function_body("find_tshome_candidates")[0]))
bad = find_offenders('putpar("RG"', {"set_rg"})
check("guard: RG written only through set_rg (readback-verified within one "
      "console gain step)", not bad, "; ".join(bad))
check("guard: set_common_acq verifies TD by readback (verify_acq_write) and "
      "the mocked rga writes RG like the real one",
      'verify_acq_write("TD", td, "int")' in _sc and "set_rg(1)" in _sc
      and "set_rg(101.0)" in "\n".join(function_body("run_rga")[0]))
check("guard: verify_acq_write reloads and rewrites once, then counts "
      "(acq_write_mismatch / last_acq_write_mismatch), never dialogues",
      'PARAM_API["acq_write_mismatch"]' in "\n".join(
          function_body("verify_acq_write")[0])
      and 'PARAM_API["last_acq_write_mismatch"]' in "\n".join(
          function_body("verify_acq_write")[0])
      and "reload_current_dataset()" in "\n".join(
          function_body("verify_acq_write")[0])
      and "CONFIRM(" not in "\n".join(function_body("verify_acq_write")[0]))
check("meta: schema documents the v0.7.5 param_api keys",
      all(k in json.dumps(schema["properties"]["software"]["properties"]
                          ["param_api"])
          for k in ("f1_files_created", "f1_files_source",
                    "f1_fnmode_edits", "f1_fnmode_copied",
                    "acq_write_mismatch", "last_acq_write_mismatch")))
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
check("guard: the setup expno and every ladder rung are made 1D whatever the "
      "template was (make_1d after their open_expno)",
      SRC.count("    clear_raw_data(setup_dir)\n    make_1d()\n") == 1
      and "        cd = open_expno(template, dsname, expno)\n        make_1d()" in SRC)
check("guard: meta program_version takes SCRIPT_VERSION directly (TopCmds "
      "exports a PROGRAM_VERSION function that clobbers an alias)",
      '"program_version": SCRIPT_VERSION' in SRC
      and re.search(r"^PROGRAM_VERSION\s*=", SRC, re.M) is None)
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
check("guard: the stub models the observed consoles (3.7.0 Oulu, 4.4.0 "
      "Torino) on EVERY flavor -- PARMODE 2D accepted, no acqu2 from a "
      "scripted write, the console's dialog on F1 access, every write into "
      "the dataset dropped -- the F1 files only the operator's parmode "
      "creates, and an existing acqu2 keeping its FnMODE",
      '"legacy-noacqu2"' in STUB_SRC and "STRAY_DIALOGS" in STUB_SRC
      and "The requested format file is invalid" in STUB_SRC
      and "PUTPAR-DROPPED" in STUB_SRC and "def _write_f1_files" in STUB_SRC
      and "return _is_2d(params) and not _has_acqu2(dsdir)" in STUB_SRC
      and 'FLAVOR[0] == "legacy-noacqu2" and _is_2d' not in STUB_SRC
      and "def _existing_fnmode" in STUB_SRC
      and '_stray_dialog(dsdir, "PUTPAR", _u(name))' in STUB_SRC)
check("guard: the harness runs all ten console flavors (the Oulu model in "
      "both modes), the feature variant also under the strict flavor",
      all(f in SH_SRC for f in ('"legacy simulate"', '"legacy desktest"',
                                '"ts44 desktest"', '"ts44-stale desktest"',
                                '"ts44-strict desktest"',
                                '"ts44-f1echo desktest"',
                                '"ts44-dimlie desktest"',
                                '"ts44-f1route desktest"',
                                '"ts44-f1mismatch desktest"',
                                '"legacy-noacqu2 simulate"',
                                '"legacy-noacqu2 desktest"',
                                '"ts44-strict desktest rdopt sweep autostep"'))
      and "HARNESS_TS_FLAVOR" in SH_SRC and "HARNESS_TS_FLAVOR" in ENTRY_SRC,
      "testing/run_jython_harness.sh or jython_entry.py no longer run the flavors")
check("guard: the harness fails on stray console dialogs and lost writes, "
      "checks acqu2 in the bundle and the recorded td/rg/pulprog per role",
      "STRAY_DIALOGS" in ENTRY_SRC and "PUTPAR-DROPPED" in ENTRY_SRC
      and '"data/%d/acqu2" % _e' in ENTRY_SRC
      and "recorded acquisition parameters match" in ENTRY_SRC
      and '"legacy-noacqu2"' in ENTRY_SRC)
check("guard: the harness runs the 2D-template flavor, checks per-expno "
      "dimensionality, and asserts the FnMODE normalisation per flavor "
      "(f1_fnmode_edits, FnMODE 0 on disk, the template file untouched)",
      '"legacy-2dtemplate desktest"' in SH_SRC
      and "whatever the template was" in ENTRY_SRC
      and "f1_fnmode_edits" in ENTRY_SRC
      and "template acqu2 is untouched" in ENTRY_SRC
      and "TEMPLATE_ACQU2" in ENTRY_SRC)
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
# 5. Pulse programs (v0.7.6).  Torino, Avance Neo 400, TopSpin 4.4.0,
#    2026-09-25, first live run: both reference blocks failed at zg because
#    Bruker's library zg2d computes "DELTA=d20-((d1+aq)*(ns+ds))-30m" and
#    spends it per row, while the run script never sets d20 -- "Cannot load
#    line: duration is negative (-21166512.000000 us) In 'zg2d': line 24".
#    The references now use the project's own zgref2d (zg2d without that
#    line).  Pinned here: the embedded texts equal the shipped files byte
#    for byte, neither project pulse program computes a delay (a quoted
#    name=expression other than acqt0, which is the receiver's time-origin
#    constant, not a delay), the installer writes both files, every
#    reference block names PP_REF_NAME, the timing model carries zgref2d's
#    fixed 30 ms per row explicitly, and the harness, the report's
#    pulse-program parser and the physics fixture agree with it.
# --------------------------------------------------------------------------

PP_DIR = os.path.join(REPO, "topspin", "pp")
REPORT_PATH = os.path.join(REPO, "analysis", "facility_report.py")
PHYS_PATH = os.path.join(REPO, "testing", "make_physics_bundle.py")


def embedded_pp(varname):
    m = re.search(r'^%s = """(.*?)"""' % varname, SRC, re.M | re.S)
    return m.group(1) if m else None


def pp_code_lines(text):
    """The statements of a pulse program: non-blank lines that are not
    ';' comments, stripped."""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s and not s.startswith(";"):
            out.append(s)
    return out


_PP_DEFINE = re.compile(r'^"\s*([A-Za-z_]\w*)\s*=')
_PP_FILES = {}
for var, name in (("PP_TEXT", "zgnoise2d"), ("PP_REF_TEXT", "zgref2d")):
    with open(os.path.join(PP_DIR, name), "rb") as fh:
        pp_bytes = fh.read()
    _PP_FILES[name] = pp_bytes.decode("ascii", "replace")
    emb = embedded_pp(var)
    check("pulse program: embedded %s equals topspin/pp/%s byte for byte"
          % (var, name),
          emb is not None and emb.encode("utf-8") == pp_bytes,
          "embedded %d bytes vs file %d bytes" % (len(emb or ""), len(pp_bytes)))
    check("pulse program: topspin/pp/%s is ASCII with LF line endings and a "
          "final newline" % name,
          all(b < 128 for b in pp_bytes) and b"\r" not in pp_bytes
          and pp_bytes.endswith(b"\n"))
    code = pp_code_lines(_PP_FILES[name])
    computed = [ln for ln in code
                if _PP_DEFINE.match(ln) and _PP_DEFINE.match(ln).group(1) != "acqt0"]
    check("pulse program: %s computes no delay (no quoted name=expression "
          "other than acqt0 -- zg2d's DELTA is what failed at Torino)" % name,
          not computed, "; ".join(computed))
    check("pulse program: %s has no d20 / DELTA dependence and no include "
          "beyond Avance.incl" % name,
          re.search(r"\b(d20|DELTA)\b", "\n".join(code)) is None
          and [ln for ln in code if ln.startswith("#")] == ["#include <Avance.incl>"])

check("pulse program: zgref2d is Bruker's zg2d minus the DELTA line -- "
      "1 ze / 30m / 2 d1 / p1 ph1 / go=2 ph31 / 30m wr #0 if #0 ze / "
      "lo to 2 times td1 / exit, ph1=0, ph31=0, acqt0 as in zg/zg2d",
      pp_code_lines(_PP_FILES["zgref2d"]) == [
          "#include <Avance.incl>", '"acqt0=-p1*2/3.1416"', "1 ze", "30m",
          "2 d1", "p1 ph1", "go=2 ph31", "30m wr #0 if #0 ze",
          "lo to 2 times td1", "exit", "ph1=0", "ph31=0"],
      repr(pp_code_lines(_PP_FILES["zgref2d"])))
check("pulse program: zgnoise2d is unchanged -- 1 ze / 2 d1 / go=2 ph31 / "
      "d1 wr #0 if #0 ze / lo to 2 times td1 / exit, ph31=0, no pulse",
      pp_code_lines(_PP_FILES["zgnoise2d"]) == [
          "#include <Avance.incl>", "1 ze", "2 d1", "go=2 ph31",
          "d1 wr #0 if #0 ze", "lo to 2 times td1", "exit", "ph31=0"],
      repr(pp_code_lines(_PP_FILES["zgnoise2d"])))
check("pulse program: PP_REF_NAME = \"zgref2d\"; the references never name "
      "Bruker's zg2d (probe, section 9 and section 11 all write PP_REF_NAME; "
      "the noise blocks write PP_NAME)",
      re.search(r'^PP_REF_NAME\s*=\s*"zgref2d"', SRC, re.M) is not None
      and 'putpar("PULPROG", "zg2d")' not in SRC
      and SRC.count('putpar("PULPROG", PP_REF_NAME)') == 3
      and SRC.count('putpar("PULPROG", PP_NAME)') == 2)
_ip = "\n".join(function_body("install_pulse_program")[0])
check("pulse program: install_pulse_program writes BOTH files (or the "
      "operator confirms both are in place)",
      "((PP_NAME, PP_TEXT), (PP_REF_NAME, PP_REF_TEXT))" in _ip
      and "zgnoise2d and zgref2d pre-installed" in _ip
      and "BOTH 'zgnoise2d'" in _ip)
_oe = "\n".join(function_body("ocxo_expected_s")[0])
check("timing: ocxo_expected_s takes fixed_s_per_row; the references pass "
      "REF_ROW_FIXED_S (0.03, zgref2d's '30m wr'), the noise blocks and the "
      "1D rungs pass 0 -- every one of the six call sites",
      "def ocxo_expected_s(td, swh, ns, rows, d1_s, d1_per_row, fixed_s_per_row):"
      in SRC
      and "return rows * (ns * (aq + d1_per_row * d1_s) + fixed_s_per_row)" in _oe
      and re.search(r"^REF_ROW_FIXED_S\s*=\s*0\.03\b", SRC, re.M) is not None
      and SRC.count("ocxo_expected_s(TD_ROW, SWH_HZ, 1, REF_ROWS, D1_REF_S, 1,\n"
                    "                             REF_ROW_FIXED_S)") == 2
      and SRC.count("ocxo_expected_s(TD_ROW, SWH_HZ, 1, n_rows, D1_NOISE_S, 2, 0.0)") == 2
      and SRC.count("ocxo_expected_s(TD_LADDER, SWH_HZ, 1, 1, D1_REF_S, 1, 0.0)") == 2
      and len(re.findall(r"ocxo_expected_s\(", SRC)) == 7)
check("harness: the references are expected as zgref2d, both pulse programs "
      "are checked in the fake TSHOME pp/user, and the recorded expectations "
      "are checked against the per-role timing model",
      '"reference_open": "zgref2d", "reference_close": "zgref2d"' in ENTRY_SRC
      and '("zgnoise2d", ";zgnoise2d"),' in ENTRY_SRC
      and '("zgref2d", ";zgref2d")):' in ENTRY_SRC
      and "follow the per-role timing model" in ENTRY_SRC)
with open(REPORT_PATH, "r", encoding="utf-8") as fh:
    REPORT_SRC = fh.read()
check("report: the pulse-program parser skips the acqt0 definition (a "
      "time-origin constant, no duration) and refuses any other computed "
      "delay, so zgref2d and Bruker's zg are modelable and zg2d is not",
      'if m.group(1) == "acqt0":' in REPORT_SRC
      and "computed delay definition" in REPORT_SRC)
check("report: the pulse-program text is read from pulseprogram OR the "
      "TopSpin 4.x pulseprogram.precomp (Torino's bundle has only the "
      "latter), and the fixture can write either layout",
      '_PP_FILES = ("pulseprogram", "pulseprogram.precomp")' in REPORT_SRC
      and "def pulseprogram_source" in REPORT_SRC
      and '"--pp-layout", choices=("topspin3", "topspin4")'
      in open(PHYS_PATH, encoding="utf-8").read()
      and "--pp-layout topspin4" in open(
          os.path.join(REPO, "testing", "run_jython_harness.sh"),
          encoding="utf-8").read())
with open(PHYS_PATH, "r", encoding="utf-8") as fh:
    PHYS_SRC = fh.read()
check("fixture: make_physics_bundle writes the SHIPPED zgref2d/zgnoise2d "
      "texts (read from topspin/pp/) and models one d1 + 30 ms per "
      "reference row, never zg2d",
      '_project_pp("zgref2d")' in PHYS_SRC and '_project_pp("zgnoise2d")' in PHYS_SRC
      and '"zg2d"' not in PHYS_SRC and "REF_ROW_FIXED_S" in PHYS_SRC)


# --------------------------------------------------------------------------
print("")
if FAILURES:
    print("%d CHECK(S) FAILED" % len(FAILURES))
    sys.exit(1)
print("ALL CHECKS PASSED")
sys.exit(0)
