#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
upload_bundle.py -- ship a spin-noise bundle zip to the central repository.

    python3 upload_bundle.py spinnoise_myfacility_20260818_140322Z_a1b2.zip
    python3 upload_bundle.py <bundle.zip> --dry-run       # show what would be sent
    python3 upload_bundle.py <bundle.zip> --verify-only   # validate + sha256, no network
    python3 upload_bundle.py <bundle.zip> --selftest      # validate zip structure + meta.json
                                                          # against schema/meta.schema.json
    python3 upload_bundle.py <bundle.zip> --abort         # abandon a stalled chunked upload
    python3 upload_bundle.py <bundle.zip> --config /path/to/config.json

Upload paths (chosen automatically by size):
  * <= 50 MiB : one POST to /ingest (single request, server verifies sha256).
  * >  50 MiB : chunked multipart upload in 50 MiB parts via /upload/create,
    /upload/part, /upload/complete — works up to 5 GiB on any Cloudflare plan.
    RESUMABLE: progress is checkpointed to <bundle>.upload-state.json after
    every part; rerunning the same command after a crash / network loss / kill
    picks up where it left off. The state file is deleted on success. A stalled
    upload can be abandoned with --abort (frees the server-side parts).

Design constraints (do not "improve" these away):
  * Python 3 STANDARD LIBRARY ONLY. This runs on ancient CentOS boxes that sit
    next to spectrometers and have no pip, no internet package access, and
    often Python 3.6. No f-string '=' specifiers, no walrus operator, no
    dataclasses features beyond 3.6, no external jsonschema package.
  * NEVER delete or move the bundle. If anything at all goes wrong, the zip
    stays exactly where it is and we print instructions for manual upload.
  * Every failure mode gets a human-readable message aimed at busy NMR staff.

Config file (default: config.json next to this script; see config.example.json):
    {
      "endpoint_url":     "https://spin-noise-ingest.<subdomain>.workers.dev/ingest",
      "token":            "<bearer token from the maintainer>",
      "facility_slug":    "your-facility-slug",
      "maintainer_email": "spin-noise-maintainer@example.org"   (optional)
    }
"""

from __future__ import print_function  # harmless on py3; belt-and-braces

import argparse
import copy
import hashlib
import json
import os
import re
import ssl
import sys
import time
import zipfile

try:
    # Standard library on every Python 3.
    from urllib import request as urlrequest
    from urllib import error as urlerror
    from urllib.parse import quote as urlquote
except ImportError:  # pragma: no cover - cannot happen on py3, but stay polite
    print("ERROR: this script requires Python 3 (found %s)." % sys.version.split()[0])
    sys.exit(2)

# Kept in sync with the repository VERSION file (a literal, not a file
# read, because this script is copied standalone to facility machines);
# testing/static_check.py enforces the sync.
UPLOADER_VERSION = "0.4.0"

# Metadata schema versions this uploader understands.  The shipped
# schema/meta.schema.json describes the CURRENT version (2.0, which made
# the contract vendor-neutral: required 'vendor' enum + vendor-namespaced
# 'instrument' blocks; 1.2 added the optional 'clock_audit' object; 1.1
# added the required 'software' provenance object); bundles declaring
# 1.2, 1.1 or 1.0 are still accepted -- see adapt_schema_for_version().
# A 1.x bundle carries no 'vendor' field: it is treated as 'bruker'
# (every 1.x writer was the TopSpin orchestrator).
SUPPORTED_SCHEMA_VERSIONS = ("1.0", "1.1", "1.2", "2.0")

# Bundle filename convention (see project spec):
#   spinnoise_<facility_slug>_<YYYYMMDD_HHMMSSZ>_<4hex>.zip
BUNDLE_NAME_RE = re.compile(
    r"^spinnoise_[a-z0-9][a-z0-9-]*_[0-9]{8}_[0-9]{6}Z_[0-9a-f]{4}\.zip$"
)

# Retry schedule for transient failures (network unreachable, 5xx):
# initial attempt, then 3 retries after these sleeps. Applied PER PART on the
# chunked path, so one flaky part never restarts the whole transfer.
RETRY_SLEEPS_S = [2, 8, 30]

DEFAULT_MAINTAINER_EMAIL = "spin-noise-maintainer@example.org"

CHUNK = 1024 * 1024  # 1 MiB read chunks for hashing / part streaming

# Automatic path selection: bundles at or under this size go through the
# single-request POST /ingest; larger ones use the chunked multipart path.
SINGLE_SHOT_MAX_BYTES = 50 * 1024 * 1024

# Chunked path geometry. 50 MiB parts sit comfortably under Cloudflare's
# 100 MB free-plan request cap and over R2's 5 MiB multipart minimum.
PART_BYTES = 50 * 1024 * 1024
MULTIPART_MAX_BYTES = 5 * 1024 * 1024 * 1024  # server-enforced 5 GiB ceiling

UPLOAD_TIMEOUT_S = 600  # per request (one bundle or one part)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def default_config_path():
    return os.path.join(script_dir(), "config.json")


def default_schema_path():
    # schema/ lives one level up from uploader/ in the repo layout.
    return os.path.abspath(os.path.join(script_dir(), "..", "schema", "meta.schema.json"))


def sha256_of_file(path):
    """Stream the file through sha256; bundles can be gigabytes, never slurp."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def load_config(path):
    """Return (config_dict, None) or (None, error_message)."""
    if not os.path.isfile(path):
        return None, "config file not found: %s" % path
    try:
        with open(path, "r") as fh:
            cfg = json.load(fh)
    except ValueError as exc:
        return None, "config file %s is not valid JSON: %s" % (path, exc)
    missing = [k for k in ("endpoint_url", "token", "facility_slug") if not cfg.get(k)]
    if missing:
        return None, "config file %s is missing required key(s): %s" % (path, ", ".join(missing))
    return cfg, None


def fallback_instructions(bundle_path, maintainer_email):
    """The message we print whenever the upload cannot proceed."""
    lines = [
        "",
        "----------------------------------------------------------------------",
        "  YOUR DATA IS SAFE. Nothing was deleted.",
        "",
        "  The bundle is still here:",
        "      %s" % os.path.abspath(bundle_path),
        "",
        "  Manual upload options:",
        "    1. Keep this zip somewhere safe (it is the complete record).",
        "    2. Email the maintainer to arrange transfer (the zip may be too",
        "       large to attach -- a link or shared drive is fine):",
        "           %s" % maintainer_email,
        "    3. Or retry later from any machine with this script and the zip:",
        "           python3 upload_bundle.py %s" % os.path.basename(bundle_path),
        "----------------------------------------------------------------------",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Minimal JSON-Schema validator
# --------------------------------------------------------------------------
# We deliberately do NOT depend on the 'jsonschema' package (not installed on
# spectrometer workstations). This implements exactly the subset of draft-07
# that schema/meta.schema.json uses:
#     type (incl. lists of types), required, properties, patternProperties,
#     additionalProperties (boolean form), items, enum, const, pattern,
#     minimum, maximum, exclusiveMinimum, minItems, minProperties
# Unknown keywords are ignored (which is what a full validator does anyway
# for annotations). No $ref support -- the schema avoids $ref on purpose.

_TYPE_MAP = {
    "string": str,
    "object": dict,
    "array": list,
    "boolean": bool,
    "null": type(None),
}


def _type_ok(value, tname):
    if tname == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if tname == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    pytype = _TYPE_MAP.get(tname)
    if pytype is None:
        return True  # unknown type name: don't fail the operator over it
    return isinstance(value, pytype)


def validate_against_schema(instance, schema, path="$"):
    """Return a list of human-readable error strings (empty list = valid)."""
    errors = []

    # -- type -------------------------------------------------------------
    stype = schema.get("type")
    if stype is not None:
        allowed = stype if isinstance(stype, list) else [stype]
        if not any(_type_ok(instance, t) for t in allowed):
            errors.append("%s: expected type %s, got %s"
                          % (path, "/".join(allowed), type(instance).__name__))
            return errors  # no point checking further constraints on wrong type

    # -- const / enum -----------------------------------------------------
    if "const" in schema and instance != schema["const"]:
        errors.append("%s: must equal %r (got %r)" % (path, schema["const"], instance))
    if "enum" in schema and instance not in schema["enum"]:
        errors.append("%s: %r is not one of %r" % (path, instance, schema["enum"]))

    # -- string constraints -------------------------------------------------
    if isinstance(instance, str) and "pattern" in schema:
        if re.search(schema["pattern"], instance) is None:
            errors.append("%s: %r does not match pattern %r"
                          % (path, instance, schema["pattern"]))

    # -- numeric constraints ------------------------------------------------
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append("%s: %r is below minimum %r" % (path, instance, schema["minimum"]))
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append("%s: %r is above maximum %r" % (path, instance, schema["maximum"]))
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append("%s: %r must be > %r" % (path, instance, schema["exclusiveMinimum"]))

    # -- object constraints ---------------------------------------------------
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append("%s: missing required key '%s'" % (path, key))
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            errors.append("%s: needs at least %d entries (has %d)"
                          % (path, schema["minProperties"], len(instance)))
        props = schema.get("properties", {})
        pattern_props = schema.get("patternProperties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            subpath = "%s.%s" % (path, key)
            matched = False
            if key in props:
                matched = True
                errors.extend(validate_against_schema(value, props[key], subpath))
            for pat, subschema in pattern_props.items():
                if re.search(pat, key) is not None:
                    matched = True
                    errors.extend(validate_against_schema(value, subschema, subpath))
            if not matched and additional is False:
                errors.append("%s: unexpected key '%s' (additionalProperties is false)"
                              % (path, key))

    # -- array constraints ----------------------------------------------------
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append("%s: needs at least %d items (has %d)"
                          % (path, schema["minItems"], len(instance)))
        items = schema.get("items")
        if isinstance(items, dict):
            for i, element in enumerate(instance):
                errors.extend(validate_against_schema(element, items, "%s[%d]" % (path, i)))

    return errors


def adapt_schema_for_version(schema, declared_version):
    """Return a copy of the shipped (current, 2.0) schema adjusted to the
    schema_version a bundle declares.

    v2.0 bundles: schema used as-is ('vendor' + 'instrument' required;
    'clock_audit' optional -- it was never required in any version).
    v1.x bundles: written before the vendor split -- the schema_version
    const becomes the declared version, 'vendor' and 'instrument' drop out
    of the required list (absent vendor = 'bruker'), and the two fields
    that MOVED into instrument.bruker in 2.0 are re-tightened back to
    their 1.x homes: 'topspin_version' required in spectrometer,
    'lock_sweep_confirmed_off' required in environment.  So old bundles
    keep exactly their original validation.
    v1.0 bundles: additionally drop 'software' from the required list
    (it arrived in 1.1).
    """
    if declared_version == "2.0":
        return schema
    adapted = copy.deepcopy(schema)
    props = adapted.get("properties", {})
    if isinstance(props.get("schema_version"), dict):
        props["schema_version"]["const"] = declared_version
    adapted["required"] = [k for k in adapted.get("required", [])
                           if k not in ("vendor", "instrument")]
    spec = props.get("spectrometer", {})
    if isinstance(spec, dict):
        req = list(spec.get("required", []))
        if "topspin_version" not in req:
            req.insert(0, "topspin_version")
        spec["required"] = req
    env = props.get("environment", {})
    if isinstance(env, dict):
        req = list(env.get("required", []))
        if "lock_sweep_confirmed_off" not in req:
            req.insert(1, "lock_sweep_confirmed_off")
        env["required"] = req
    if declared_version == "1.0":
        adapted["required"] = [k for k in adapted.get("required", [])
                               if k != "software"]
    return adapted


# --------------------------------------------------------------------------
# Bundle structure validation (used by --selftest and --verify-only)
# --------------------------------------------------------------------------

def verify_bundle(bundle_path, schema_path, check_data_hashes=True):
    """
    Validate the bundle zip. Returns (ok_bool, list_of_messages).
    Messages prefixed 'ERROR' are fatal; 'WARN' are advisory only.
    """
    msgs = []
    fatal = False

    basename = os.path.basename(bundle_path)
    if BUNDLE_NAME_RE.match(basename) is None:
        msgs.append("WARN : filename '%s' does not follow the convention "
                    "spinnoise_<facility>_<YYYYMMDD_HHMMSSZ>_<4hex>.zip "
                    "(the server may reject it)" % basename)

    if not os.path.isfile(bundle_path):
        return False, ["ERROR: no such file: %s" % bundle_path]

    try:
        zf = zipfile.ZipFile(bundle_path, "r")
    except zipfile.BadZipFile as exc:
        return False, ["ERROR: not a readable zip file (%s). Was the copy "
                       "interrupted? Re-copy from the spectrometer." % exc]

    with zf:
        names = zf.namelist()

        # CRC sweep -- catches truncated transfers cheaply.
        bad = zf.testzip()
        if bad is not None:
            msgs.append("ERROR: zip CRC check failed at '%s' -- file is corrupt, "
                        "re-copy the bundle from the acquisition machine." % bad)
            fatal = True

        # meta.json must sit at the zip root.
        if "meta.json" not in names:
            msgs.append("ERROR: meta.json missing from zip root. This zip was not "
                        "produced by spin_noise_run.py, or was re-zipped from "
                        "inside a folder (zip the CONTENTS, not the folder).")
            return False, msgs

        try:
            meta = json.loads(zf.read("meta.json").decode("utf-8"))
        except ValueError as exc:
            msgs.append("ERROR: meta.json is not valid JSON: %s" % exc)
            return False, msgs

        # Validate against the schema, if we can find it.
        if os.path.isfile(schema_path):
            try:
                with open(schema_path, "r") as fh:
                    schema = json.load(fh)
            except ValueError as exc:
                msgs.append("WARN : could not parse schema %s (%s); skipping "
                            "schema validation." % (schema_path, exc))
                schema = None
            if schema is not None:
                declared = None
                if isinstance(meta, dict):
                    declared = meta.get("schema_version")
                if declared not in SUPPORTED_SCHEMA_VERSIONS:
                    fatal = True
                    msgs.append("ERROR: meta.json declares schema_version %r; "
                                "this uploader understands: %s. Update the "
                                "uploader (or fix meta.json)."
                                % (declared,
                                   ", ".join(SUPPORTED_SCHEMA_VERSIONS)))
                else:
                    errs = validate_against_schema(
                        meta, adapt_schema_for_version(schema, declared))
                    if errs:
                        fatal = True
                        msgs.append("ERROR: meta.json fails schema validation "
                                    "(%d problem(s)):" % len(errs))
                        for e in errs[:25]:
                            msgs.append("       - %s" % e)
                        if len(errs) > 25:
                            msgs.append("       ... and %d more"
                                        % (len(errs) - 25))
                    else:
                        msgs.append("OK   : meta.json (schema_version %s) "
                                    "validates against %s"
                                    % (declared,
                                       os.path.basename(schema_path)))
        else:
            msgs.append("WARN : schema file not found at %s; skipping schema "
                        "validation (structure checks still ran)." % schema_path)

        # Vendor / instrument consistency (schema 2.0).  The minimal
        # validator subset cannot express "instrument must contain the
        # block named by vendor", so that rule lives here in code.  1.x
        # bundles carry no vendor field and are 'bruker' by construction
        # (every 1.x writer was the TopSpin orchestrator).
        if isinstance(meta, dict):
            vendor = meta.get("vendor")
            if meta.get("schema_version") == "2.0" and isinstance(vendor, str):
                inst = meta.get("instrument")
                if not (isinstance(inst, dict)
                        and isinstance(inst.get(vendor), dict)):
                    msgs.append("ERROR: meta.json declares vendor '%s' but "
                                "instrument carries no '%s' block -- the "
                                "vendor-specific fields are missing." %
                                (vendor, vendor))
                    fatal = True
                else:
                    msgs.append("OK   : vendor '%s' with a matching "
                                "instrument.%s block." % (vendor, vendor))
            elif vendor is None and isinstance(
                    meta.get("schema_version"), str) \
                    and meta.get("schema_version").startswith("1."):
                msgs.append("OK   : pre-2.0 bundle without a vendor field -- "
                            "treated as vendor 'bruker'.")

        # There must be at least one data/<expno>/ entry.
        data_files = [n for n in names if n.startswith("data/") and not n.endswith("/")]
        if not data_files:
            msgs.append("ERROR: no files under data/ in the zip -- the Bruker "
                        "experiment directories are missing.")
            fatal = True

        # Cross-check checksums map <-> zip contents.
        checksums = meta.get("checksums", {}) if isinstance(meta, dict) else {}
        if isinstance(checksums, dict) and checksums:
            listed = set(checksums.keys())
            present = set(data_files)
            for missing in sorted(listed - present):
                msgs.append("ERROR: checksums lists '%s' but it is not in the zip." % missing)
                fatal = True
            for extra in sorted(present - listed):
                msgs.append("WARN : '%s' is in the zip but has no checksum entry." % extra)
            if check_data_hashes:
                n_checked = 0
                for name in sorted(listed & present):
                    want = checksums[name]
                    h = hashlib.sha256()
                    with zf.open(name, "r") as fh:
                        while True:
                            block = fh.read(CHUNK)
                            if not block:
                                break
                            h.update(block)
                    got = "sha256:" + h.hexdigest()
                    if got != want:
                        msgs.append("ERROR: checksum mismatch for '%s' "
                                    "(meta.json says %s..., file is %s...)"
                                    % (name, want[:15], got[:15]))
                        fatal = True
                    n_checked += 1
                if not fatal:
                    msgs.append("OK   : verified sha256 of %d data file(s)." % n_checked)

        # Friendly reminder if the noise experiment looks absent.
        if isinstance(meta, dict):
            roles = [e.get("role") for e in meta.get("experiments", [])
                     if isinstance(e, dict)]
            if "noise" not in roles:
                msgs.append("WARN : no experiment with role 'noise' in meta.json -- "
                            "is this bundle complete?")

    return (not fatal), msgs


# --------------------------------------------------------------------------
# Upload — shared plumbing
# --------------------------------------------------------------------------

def endpoint_base(url):
    """config endpoint_url may be the full /ingest URL (historic configs) or
    the bare Worker origin; return the bare origin either way."""
    u = url.rstrip("/")
    if u.endswith("/ingest"):
        u = u[: -len("/ingest")]
    return u


def ingest_url(cfg):
    return endpoint_base(cfg["endpoint_url"]) + "/ingest"


def state_file_path(bundle_path):
    return os.path.abspath(bundle_path) + ".upload-state.json"


def load_upload_state(bundle_path, size, digest_hex):
    """Return the saved resume state if it matches this bundle, else None."""
    path = state_file_path(bundle_path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as fh:
            state = json.load(fh)
    except (ValueError, OSError) as exc:
        print("WARN : resume state file %s unreadable (%s); starting fresh." % (path, exc))
        return None
    if (state.get("total_bytes") != size or state.get("sha256") != digest_hex
            or not state.get("key") or not state.get("upload_id")
            or not isinstance(state.get("parts"), dict)):
        print("WARN : resume state file does not match this bundle (file changed")
        print("       since the interrupted upload?); starting a fresh upload.")
        return None
    return state


def save_upload_state(bundle_path, state):
    """Atomic write so a crash mid-save never corrupts the resume state."""
    path = state_file_path(bundle_path)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)  # atomic on POSIX and Windows (py3.3+)


def remove_upload_state(bundle_path):
    try:
        os.remove(state_file_path(bundle_path))
    except OSError:
        pass


class _FilePartReader(object):
    """File-like view of one part of an already-open file: read() streams at
    most `length` bytes starting at `offset`. urllib sends file-like bodies in
    small blocks, so a 50 MiB part goes over the wire without this script ever
    holding the part (let alone the whole bundle) in memory."""

    def __init__(self, fh, offset, length):
        self._fh = fh
        self._remaining = length
        fh.seek(offset)

    def read(self, n=-1):
        if self._remaining <= 0:
            return b""
        if n is None or n < 0 or n > self._remaining:
            n = self._remaining
        block = self._fh.read(min(n, CHUNK))
        self._remaining -= len(block)
        return block


def http_json_call(url, method, payload, token):
    """Send a small JSON request; return (status_code, parsed_dict).
    Transport-level failures (unreachable, TLS, timeout) raise OSError /
    urlerror.URLError for the caller's retry loop; HTTP error statuses are
    RETURNED, not raised."""
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": "Bearer %s" % token,
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "User-Agent": "spin-noise-uploader/%s" % UPLOADER_VERSION,
    }
    tls_ctx = ssl.create_default_context()
    req = urlrequest.Request(url, data=body, headers=headers, method=method)
    try:
        resp = urlrequest.urlopen(req, context=tls_ctx, timeout=UPLOAD_TIMEOUT_S)
        raw = resp.read().decode("utf-8", "replace")
        code = resp.getcode()
    except urlerror.HTTPError as exc:
        code = exc.code
        try:
            raw = exc.read().decode("utf-8", "replace")
        except Exception:
            raw = ""
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            parsed = {"raw": raw.strip()[:300]}
    except ValueError:
        parsed = {"raw": raw.strip()[:300]}
    return code, parsed


def json_call_with_retries(url, method, payload, token, what):
    """http_json_call + the standard transient-retry schedule.
    Returns (code, parsed) once a non-5xx HTTP answer arrives, or
    (None, None) after all attempts failed at transport level / with 5xx."""
    attempts = 1 + len(RETRY_SLEEPS_S)
    for attempt in range(attempts):
        if attempt > 0:
            sleep_s = RETRY_SLEEPS_S[attempt - 1]
            print("Retrying %s in %d s (attempt %d of %d)..."
                  % (what, sleep_s, attempt + 1, attempts))
            time.sleep(sleep_s)
        try:
            code, parsed = http_json_call(url, method, payload, token)
        except (urlerror.URLError, ssl.SSLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            print("WARN : could not reach server for %s (%s)." % (what, reason))
            continue
        if 500 <= code < 600:
            print("WARN : HTTP %d from server on %s -- transient, will retry."
                  % (code, what))
            continue
        return code, parsed
    return None, None


def explain_duplicate_409(bundle_name):
    print("ERROR: HTTP 409 Conflict -- a bundle with this exact name already")
    print("       exists on the server. If this is a genuine re-run, rename the")
    print("       zip with a fresh 4-hex suffix and upload again, e.g.:")
    stem = bundle_name[:-4]
    new_name = re.sub(r"_[0-9a-f]{4}$", "_%04x" % (int(time.time()) & 0xFFFF), stem) + ".zip"
    print("           mv %s %s" % (bundle_name, new_name))
    print("       If you already uploaded this bundle, you are done -- the 409")
    print("       simply means the server has it.")


# --------------------------------------------------------------------------
# Upload — single-shot path (bundles <= 50 MiB, one POST /ingest)
# --------------------------------------------------------------------------

def do_upload(bundle_path, cfg, digest_hex, dry_run):
    """
    POST the bundle in one request. Returns process exit code (0 on success).
    Retries transient failures per RETRY_SLEEPS_S; terminal HTTP errors
    (401/409/413/other 4xx) are explained and NOT retried.
    """
    url = ingest_url(cfg)
    maintainer = cfg.get("maintainer_email", DEFAULT_MAINTAINER_EMAIL)
    bundle_name = os.path.basename(bundle_path)
    size = os.path.getsize(bundle_path)

    # Sanity: the slug in the filename should match the configured facility.
    m = re.match(r"^spinnoise_([a-z0-9-]+)_[0-9]{8}_", bundle_name)
    if m and m.group(1) != cfg["facility_slug"]:
        print("WARN : bundle filename says facility '%s' but config.json says '%s'."
              % (m.group(1), cfg["facility_slug"]))
        print("       Continuing anyway -- but check you have the right config.")

    headers = {
        "Authorization": "Bearer %s" % cfg["token"],
        "Content-Type": "application/zip",
        "X-Bundle-Name": bundle_name,
        "X-Content-SHA256": digest_hex,
        "Content-Length": str(size),
        "User-Agent": "spin-noise-uploader/%s" % UPLOADER_VERSION,
    }

    if dry_run:
        print("DRY RUN -- nothing sent. Would POST:")
        print("  URL           : %s" % url)
        print("  Bundle        : %s (%.1f MiB)" % (bundle_name, size / 1048576.0))
        print("  X-Content-SHA256: %s" % digest_hex)
        print("  Authorization : Bearer %s...(redacted)" % cfg["token"][:4])
        return 0

    # Explicit TLS context: default (secure) settings, system CA store.
    tls_ctx = ssl.create_default_context()

    attempts = 1 + len(RETRY_SLEEPS_S)
    for attempt in range(attempts):
        if attempt > 0:
            sleep_s = RETRY_SLEEPS_S[attempt - 1]
            print("Retrying in %d s (attempt %d of %d)..." % (sleep_s, attempt + 1, attempts))
            time.sleep(sleep_s)
        try:
            # Stream the file; Content-Length is set explicitly above so
            # urllib does not need to know len() of the body (py3.6-safe).
            body = open(bundle_path, "rb")
            try:
                req = urlrequest.Request(url, data=body, headers=headers, method="POST")
                print("Uploading %s (%.1f MiB) to %s ..." % (bundle_name, size / 1048576.0, url))
                resp = urlrequest.urlopen(req, context=tls_ctx, timeout=600)
                raw = resp.read().decode("utf-8", "replace")
            finally:
                body.close()

            # Success path.
            receipt_id = None
            try:
                receipt_id = json.loads(raw).get("receipt_id")
            except ValueError:
                pass
            print("")
            print("UPLOAD OK (HTTP %d)." % resp.getcode())
            if receipt_id:
                print("RECEIPT: %s" % receipt_id)
                print("Note this receipt in your facility log. Thank you!")
            else:
                print("Server response: %s" % raw.strip()[:200])
            print("You may keep or archive the local zip; the repository has a copy.")
            return 0

        except urlerror.HTTPError as exc:
            code = exc.code
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace").strip()[:200]
            except Exception:
                pass
            if code == 401:
                print("ERROR: HTTP 401 Unauthorized -- the server rejected the token.")
                print("       Check the 'token' value in your config.json against the one")
                print("       the maintainer sent you (no extra spaces or line breaks).")
                print(fallback_instructions(bundle_path, maintainer))
                return 1
            if code == 413:
                print("ERROR: HTTP 413 Payload Too Large -- the bundle exceeds the server's")
                print("       2 GiB limit (%.2f GiB sent). This usually means an overnight" % (size / 1073741824.0))
                print("       run with very large TD. Contact the maintainer to arrange an")
                print("       alternative transfer; do NOT edit the zip contents yourself.")
                print(fallback_instructions(bundle_path, maintainer))
                return 1
            if code == 409:
                explain_duplicate_409(bundle_name)
                return 1
            if 400 <= code < 500:
                print("ERROR: HTTP %d from server: %s" % (code, detail or exc.reason))
                print("       This is not a transient error; retrying will not help.")
                print(fallback_instructions(bundle_path, maintainer))
                return 1
            # 5xx: transient, fall through to retry.
            print("WARN : HTTP %d from server (%s) -- transient, will retry." % (code, detail or exc.reason))

        except urlerror.URLError as exc:
            print("WARN : could not reach %s (%s)." % (url, exc.reason))
        except (ssl.SSLError, OSError) as exc:
            print("WARN : network/TLS error: %s" % exc)

    # All attempts exhausted.
    print("")
    print("ERROR: upload failed after %d attempts. The network or server may be" % attempts)
    print("       down; this happens and is not your fault.")
    print(fallback_instructions(bundle_path, maintainer))
    return 1


# --------------------------------------------------------------------------
# Upload — chunked resumable path (bundles > 50 MiB, up to 5 GiB)
# --------------------------------------------------------------------------

def _chunk_plan(size):
    """Return (n_parts, last_part_bytes) for the fixed PART_BYTES chunking."""
    n_parts = (size + PART_BYTES - 1) // PART_BYTES
    last = size - (n_parts - 1) * PART_BYTES
    return n_parts, last


def _upload_one_part(base, token, key, upload_id, fh, part, n_parts, size):
    """Upload part `part` (1-based) with the standard retry schedule.
    Returns the part's etag string, or (None, http_code) style tuple —
    concretely: (etag, None) on success, (None, code_or_None) on failure."""
    offset = (part - 1) * PART_BYTES
    length = min(PART_BYTES, size - offset)
    final = "&final=1" if part == n_parts else ""
    url = ("%s/upload/part?key=%s&upload_id=%s&part=%d%s"
           % (base, urlquote(key, safe=""), urlquote(upload_id, safe=""), part, final))
    headers = {
        "Authorization": "Bearer %s" % token,
        "Content-Type": "application/octet-stream",
        "Content-Length": str(length),
        "User-Agent": "spin-noise-uploader/%s" % UPLOADER_VERSION,
    }
    tls_ctx = ssl.create_default_context()

    attempts = 1 + len(RETRY_SLEEPS_S)
    for attempt in range(attempts):
        if attempt > 0:
            sleep_s = RETRY_SLEEPS_S[attempt - 1]
            print("Retrying part %d in %d s (attempt %d of %d)..."
                  % (part, sleep_s, attempt + 1, attempts))
            time.sleep(sleep_s)
        try:
            body = _FilePartReader(fh, offset, length)
            req = urlrequest.Request(url, data=body, headers=headers, method="PUT")
            resp = urlrequest.urlopen(req, context=tls_ctx, timeout=UPLOAD_TIMEOUT_S)
            raw = resp.read().decode("utf-8", "replace")
            try:
                etag = json.loads(raw).get("etag")
            except ValueError:
                etag = None
            if not etag:
                print("WARN : part %d stored but no etag in response -- will retry." % part)
                continue
            return etag, None
        except urlerror.HTTPError as exc:
            code = exc.code
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace").strip()[:200]
            except Exception:
                pass
            if 500 <= code < 600:
                print("WARN : HTTP %d on part %d (%s) -- transient, will retry."
                      % (code, part, detail or exc.reason))
                continue
            return None, code  # 4xx: terminal, caller explains
        except (urlerror.URLError, ssl.SSLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            print("WARN : network error on part %d (%s)." % (part, reason))
    return None, None


def do_upload_chunked(bundle_path, cfg, digest_hex, dry_run):
    """Chunked, resumable upload. Returns process exit code (0 on success)."""
    base = endpoint_base(cfg["endpoint_url"])
    token = cfg["token"]
    maintainer = cfg.get("maintainer_email", DEFAULT_MAINTAINER_EMAIL)
    bundle_name = os.path.basename(bundle_path)
    size = os.path.getsize(bundle_path)
    n_parts, last_part = _chunk_plan(size)

    if size > MULTIPART_MAX_BYTES:
        print("ERROR: bundle is %.2f GiB; the server accepts at most 5 GiB."
              % (size / 1073741824.0))
        print("       Contact the maintainer to arrange an alternative transfer.")
        print(fallback_instructions(bundle_path, maintainer))
        return 1

    if dry_run:
        print("DRY RUN -- nothing sent. Chunked upload plan:")
        print("  Bundle        : %s (%.1f MiB)" % (bundle_name, size / 1048576.0))
        print("  sha256        : %s" % digest_hex)
        print("  Endpoint base : %s" % base)
        print("  Parts         : %d x %.0f MiB (last part %.1f MiB)"
              % (n_parts, PART_BYTES / 1048576.0, last_part / 1048576.0))
        print("  Flow          : POST /upload/create -> PUT /upload/part x%d" % n_parts)
        print("                  -> POST /upload/complete")
        print("  Resume state  : %s" % state_file_path(bundle_path))
        print("  Authorization : Bearer %s...(redacted)" % token[:4])
        state = load_upload_state(bundle_path, size, digest_hex)
        if state:
            print("  NOTE          : resume state found -- %d of %d parts already done."
                  % (len(state["parts"]), n_parts))
        return 0

    # ---- create (or resume) the multipart upload ---------------------------
    state = load_upload_state(bundle_path, size, digest_hex)
    if state is not None:
        print("RESUME: found %s" % os.path.basename(state_file_path(bundle_path)))
        print("        %d of %d parts already uploaded -- continuing."
              % (len(state["parts"]), n_parts))
    else:
        print("Starting chunked upload: %s (%.1f MiB in %d parts of <= %.0f MiB)"
              % (bundle_name, size / 1048576.0, n_parts, PART_BYTES / 1048576.0))
        code, resp = json_call_with_retries(
            base + "/upload/create", "POST",
            {"bundle_name": bundle_name, "total_bytes": size,
             "sha256": digest_hex, "n_parts": n_parts, "part_bytes": PART_BYTES},
            token, "upload/create")
        if code is None:
            print("")
            print("ERROR: could not start the upload; the network or server may be down.")
            print(fallback_instructions(bundle_path, maintainer))
            return 1
        if code == 401:
            print("ERROR: HTTP 401 Unauthorized -- the server rejected the token.")
            print("       Check the 'token' value in your config.json against the one")
            print("       the maintainer sent you (no extra spaces or line breaks).")
            print(fallback_instructions(bundle_path, maintainer))
            return 1
        if code == 409:
            explain_duplicate_409(bundle_name)
            return 1
        if code != 200 or not resp.get("upload_id"):
            print("ERROR: HTTP %s starting upload: %s"
                  % (code, resp.get("error", resp.get("raw", ""))))
            print(fallback_instructions(bundle_path, maintainer))
            return 1
        state = {
            "state_version": 1,
            "bundle": bundle_name,
            "endpoint_base": base,
            "key": resp["key"],
            "upload_id": resp["upload_id"],
            "total_bytes": size,
            "part_bytes": PART_BYTES,
            "n_parts": n_parts,
            "sha256": digest_hex,
            "parts": {},  # "1": "<etag>", filled as parts land
        }
        save_upload_state(bundle_path, state)

    key = state["key"]
    upload_id = state["upload_id"]

    # ---- upload the missing parts ------------------------------------------
    with open(bundle_path, "rb") as fh:
        for part in range(1, n_parts + 1):
            if str(part) in state["parts"]:
                continue
            etag, err_code = _upload_one_part(
                base, token, key, upload_id, fh, part, n_parts, size)
            if etag is None:
                print("")
                if err_code == 404:
                    print("ERROR: the server no longer knows this upload (uploads that sit")
                    print("       incomplete for days are cleaned up). Removing the stale")
                    print("       resume file -- rerun the same command to start over:")
                    print("           python3 upload_bundle.py %s" % bundle_name)
                    remove_upload_state(bundle_path)
                elif err_code == 401:
                    print("ERROR: HTTP 401 Unauthorized -- the server rejected the token.")
                    print("       Check the 'token' value in your config.json.")
                elif err_code is not None:
                    print("ERROR: HTTP %d on part %d -- not transient; retrying will not help."
                          % (err_code, part))
                else:
                    print("ERROR: part %d failed after all retries. The network or server" % part)
                    print("       may be down; this happens and is not your fault.")
                    print("       Your progress is saved -- rerun the SAME command later and")
                    print("       the upload resumes from part %d:" % part)
                    print("           python3 upload_bundle.py %s" % bundle_name)
                print(fallback_instructions(bundle_path, maintainer))
                return 1
            state["parts"][str(part)] = etag
            save_upload_state(bundle_path, state)
            done_bytes = min(part * PART_BYTES, size)
            print("part %d/%d uploaded (%.1f / %.1f MiB)"
                  % (part, n_parts, done_bytes / 1048576.0, size / 1048576.0))

    # ---- complete ------------------------------------------------------------
    parts_list = [{"part": int(k), "etag": v}
                  for k, v in sorted(state["parts"].items(), key=lambda kv: int(kv[0]))]
    code, resp = json_call_with_retries(
        base + "/upload/complete", "POST",
        {"key": key, "upload_id": upload_id, "parts": parts_list, "sha256": digest_hex},
        token, "upload/complete")
    if code is None:
        print("")
        print("ERROR: all parts are uploaded but the final 'complete' call did not")
        print("       get through. Nothing is lost -- rerun the SAME command later;")
        print("       it will retry just this step.")
        print(fallback_instructions(bundle_path, maintainer))
        return 1
    if code == 404:
        print("ERROR: the server no longer knows this upload. Removing the stale")
        print("       resume file -- rerun the same command to start over.")
        remove_upload_state(bundle_path)
        print(fallback_instructions(bundle_path, maintainer))
        return 1
    if code == 409:
        explain_duplicate_409(bundle_name)
        return 1
    if code != 200 or not resp.get("ok"):
        print("ERROR: HTTP %s completing upload: %s"
              % (code, resp.get("error", resp.get("raw", ""))))
        print(fallback_instructions(bundle_path, maintainer))
        return 1

    remove_upload_state(bundle_path)
    print("")
    print("UPLOAD OK (HTTP %d, %d parts)." % (code, len(parts_list)))
    receipt_id = resp.get("receipt_id")
    if receipt_id:
        print("RECEIPT: %s" % receipt_id)
        print("Note this receipt in your facility log. Thank you!")
    print("You may keep or archive the local zip; the repository has a copy.")
    return 0


def do_abort(bundle_path, cfg):
    """Abandon a stalled chunked upload recorded in <bundle>.upload-state.json.
    Frees the parts stored on the server; the local zip is untouched."""
    path = state_file_path(bundle_path)
    if not os.path.isfile(path):
        print("Nothing to abort: no resume state file at %s" % path)
        return 0
    try:
        with open(path, "r") as fh:
            state = json.load(fh)
    except (ValueError, OSError) as exc:
        print("WARN : resume state file unreadable (%s); deleting it." % exc)
        remove_upload_state(bundle_path)
        return 0
    key = state.get("key")
    upload_id = state.get("upload_id")
    if not key or not upload_id:
        print("WARN : resume state file incomplete; deleting it.")
        remove_upload_state(bundle_path)
        return 0
    base = endpoint_base(cfg["endpoint_url"])
    code, resp = json_call_with_retries(
        base + "/upload/abort", "POST",
        {"key": key, "upload_id": upload_id}, cfg["token"], "upload/abort")
    if code is None:
        print("ERROR: could not reach the server to abort. The resume file is kept;")
        print("       try again later (the server also expires stale uploads on its own).")
        return 1
    if code != 200:
        print("WARN : HTTP %s on abort: %s" % (code, resp.get("error", resp.get("raw", ""))))
        print("       Deleting the local resume file anyway (the server expires stale")
        print("       uploads on its own).")
    else:
        print("Upload aborted on the server.")
    remove_upload_state(bundle_path)
    print("Local resume state removed. The zip itself is untouched:")
    print("    %s" % os.path.abspath(bundle_path))
    return 0


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Upload a spin-noise bundle zip to the central repository.")
    parser.add_argument("bundle", help="path to spinnoise_*.zip")
    parser.add_argument("--config", default=None,
                        help="path to config.json (default: next to this script)")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute checksum and show the request, but send nothing")
    parser.add_argument("--verify-only", action="store_true",
                        help="validate the bundle and print its sha256; no network")
    parser.add_argument("--selftest", action="store_true",
                        help="validate zip structure and meta.json against "
                             "schema/meta.schema.json; no network")
    parser.add_argument("--abort", action="store_true",
                        help="abandon the stalled chunked upload recorded in "
                             "<bundle>.upload-state.json (frees server-side "
                             "parts; the zip is untouched)")
    parser.add_argument("--schema", default=None,
                        help="override path to meta.schema.json (used by "
                             "--selftest/--verify-only)")
    args = parser.parse_args(argv)

    bundle_path = args.bundle
    schema_path = args.schema or default_schema_path()

    if not os.path.isfile(bundle_path):
        print("ERROR: no such file: %s" % bundle_path)
        return 2

    # ---- offline modes ----------------------------------------------------
    if args.selftest or args.verify_only:
        print("Validating bundle: %s" % bundle_path)
        ok, msgs = verify_bundle(bundle_path, schema_path)
        for m in msgs:
            print(m)
        if args.verify_only:
            print("sha256: %s" % sha256_of_file(bundle_path))
        print("RESULT: %s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 1

    # ---- upload / abort paths (need config) --------------------------------
    cfg_path = args.config or default_config_path()
    cfg, err = load_config(cfg_path)
    if cfg is None:
        print("ERROR: %s" % err)
        print("")
        print("To set up: copy config.example.json to config.json (same folder as")
        print("this script) and fill in the endpoint URL and token that the network")
        print("maintainer sent to your facility.")
        print(fallback_instructions(bundle_path, DEFAULT_MAINTAINER_EMAIL))
        return 2

    if args.abort:
        return do_abort(bundle_path, cfg)

    # Quick structural sanity check before burning upload bandwidth.
    # Hash verification of every data file is skipped here for speed; run
    # --selftest for the full check.
    ok, msgs = verify_bundle(bundle_path, schema_path, check_data_hashes=False)
    for m in msgs:
        if m.startswith("ERROR") or m.startswith("WARN"):
            print(m)
    if not ok:
        print("ERROR: bundle failed validation; not uploading. Fix the problems")
        print("       above (or re-create the bundle) and try again.")
        print(fallback_instructions(bundle_path, cfg.get("maintainer_email",
                                                         DEFAULT_MAINTAINER_EMAIL)))
        return 1

    print("Computing sha256 (may take a minute for large bundles)...")
    digest_hex = sha256_of_file(bundle_path)
    print("sha256: %s" % digest_hex)

    # Automatic path selection: small bundles go in one request, big ones
    # take the chunked resumable path (works on every Cloudflare plan).
    size = os.path.getsize(bundle_path)
    if size <= SINGLE_SHOT_MAX_BYTES:
        return do_upload(bundle_path, cfg, digest_hex, args.dry_run)
    return do_upload_chunked(bundle_path, cfg, digest_hex, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
