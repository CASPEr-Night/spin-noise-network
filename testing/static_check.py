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
     VERSION file, UPLOADER_VERSION in uploader/upload_bundle.py equals
     the VERSION file, and SCHEMA_VERSION equals the const in
     schema/meta.schema.json.
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
     object with script_version/schema_version/script_sha256.

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

m = re.search(r'^SCHEMA_VERSION\s*=\s*"([^"]+)"', SRC, re.M)
script_schema = m.group(1) if m else None
with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
    schema = json.load(fh)
schema_const = schema.get("properties", {}).get("schema_version", {}).get("const")
check("version: SCHEMA_VERSION (%r) == schema const (%r)"
      % (script_schema, schema_const),
      script_schema == schema_const and script_schema is not None)


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


# --------------------------------------------------------------------------
print("")
if FAILURES:
    print("%d CHECK(S) FAILED" % len(FAILURES))
    sys.exit(1)
print("ALL CHECKS PASSED")
sys.exit(0)
