#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_pack_roundtrip.py -- prove packer/pack_bundle.py's Bruker reader can
round-trip an existing bundle: unpack a bundle's expno tree, derive an
answers.json from its meta.json (the operator would answer the same
questions), re-pack with the standalone packer, and require:

  1. the repacked zip has EXACTLY the same data/ file set with EXACTLY
     the same sha256 per file (bit-identical payload);
  2. the repacked meta.json (schema 2.0) preserves every science-relevant
     field of the original (facility, sample, calibration, experiments,
     spectrometer common core), with the Bruker-specific fields correctly
     relocated into instrument.bruker (+ the deprecated aliases mirrored);
  3. the repacked bundle PASSES uploader/upload_bundle.py --selftest.

Plus a self-contained unit check of the Bruker JCAMP-DX parameter parser
against a synthetic acqus/acqu2s fixture with known values (angle-bracket
strings, exponent floats, F1 row count).

Usage:
    python3 testing/test_pack_roundtrip.py <bundle.zip>
    python3 testing/test_pack_roundtrip.py            # builds a synthetic
                                                      # desktest bundle first

Run by testing/run_jython_harness.sh on the harness DESKTEST bundle (the
real-Jython end-to-end product), and standalone/CI on a synthetic bundle.
Exit 0 iff every check passed.  Python 3 stdlib only.
"""

from __future__ import print_function

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import zipfile

TESTING = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(TESTING, ".."))
PACKER = os.path.join(REPO, "packer", "pack_bundle.py")
UPLOADER = os.path.join(REPO, "uploader", "upload_bundle.py")

FAILURES = []


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    line = "%s : %s" % (tag, name)
    if detail and not ok:
        line += "\n       %s" % detail
    print(line)
    if not ok:
        FAILURES.append(name)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def zip_data_hashes(path):
    """{arcname: sha256hex} for every data/ file in a bundle zip."""
    out = {}
    with zipfile.ZipFile(path, "r") as zf:
        for n in zf.namelist():
            if n.startswith("data/") and not n.endswith("/"):
                h = hashlib.sha256()
                with zf.open(n, "r") as fh:
                    while True:
                        block = fh.read(1024 * 1024)
                        if not block:
                            break
                        h.update(block)
                out[n] = h.hexdigest()
    return out


def read_meta(path):
    with zipfile.ZipFile(path, "r") as zf:
        return json.loads(zf.read("meta.json").decode("utf-8"))


# ---------------------------------------------------------------------------
# 0. Bruker JCAMP parser unit check (fixed synthetic fixture, known values)
# ---------------------------------------------------------------------------

def parser_unit_checks(pack_mod, tmp):
    fixture = os.path.join(tmp, "jcamp_fixture")
    os.makedirs(fixture)
    with open(os.path.join(fixture, "acqus"), "w") as fh:
        fh.write("##TITLE= Parameter file, TopSpin 4.1.4\n"
                 "##JCAMPDX= 5.0\n"
                 "##$BF1= 600.13\n"
                 "##$SFO1= 600.133705802\n"
                 "##$SW_h= 12019.2307692308\n"
                 "##$TD= 65536\n"
                 "##$O1= 3705.8\n"
                 "##$RG= 184.37\n"
                 "##$NS= 1\n"
                 "##$DE= 6.5\n"
                 "##$GRPDLY= 67.9841613769531\n"
                 "##$PULPROG= <zg30>\n"
                 "##END=\n")
    with open(os.path.join(fixture, "acqu2s"), "w") as fh:
        fh.write("##TITLE= Parameter file F1\n##$TD= 7\n##END=\n")
    # a ser file so td1_rows comes from acqu2s
    with open(os.path.join(fixture, "ser"), "wb") as fh:
        fh.write(b"\x00" * 64)

    reader = pack_mod.BrukerReader()
    p = reader.parse_jcamp(os.path.join(fixture, "acqus"))
    check("jcamp: PULPROG angle brackets stripped", p.get("PULPROG") == "zg30",
          repr(p.get("PULPROG")))
    check("jcamp: SW_h high-precision float text preserved",
          p.get("SW_h") == "12019.2307692308", repr(p.get("SW_h")))
    found = reader.read_experiment(fixture)
    check("bruker reader: td=65536", found.get("td") == 65536, repr(found))
    check("bruker reader: sw_hz=12019.23...",
          abs(found.get("sw_hz", 0) - 12019.2307692308) < 1e-9)
    check("bruker reader: o1_hz=3705.8",
          abs(found.get("o1_hz", 0) - 3705.8) < 1e-9)
    check("bruker reader: rg=184.37",
          abs(found.get("rg", 0) - 184.37) < 1e-9)
    check("bruker reader: h1_freq_mhz from BF1",
          abs(found.get("h1_freq_mhz", 0) - 600.13) < 1e-9)
    check("bruker reader: td1_rows=7 from acqu2s (ser present)",
          found.get("td1_rows") == 7)
    check("bruker reader: aq_s_per_row = TD/(2*SW_h)",
          abs(found.get("aq_s_per_row", 0) - 65536 / (2 * 12019.2307692308))
          < 1e-9)
    check("bruker reader: TopSpin version parsed from TITLE",
          found.get("_topspin_version_guess") == "4.1.4",
          repr(found.get("_topspin_version_guess")))


# ---------------------------------------------------------------------------
# answers.json derivation (what an operator would answer for this bundle)
# ---------------------------------------------------------------------------

def answers_from_meta(meta):
    spec = meta.get("spectrometer", {})
    env = meta.get("environment", {})
    sw = meta.get("software", {})
    answers = {
        "vendor": meta.get("vendor", "bruker"),
        "run_mode": sw.get("run_mode", "external-acquisition"),
        "local_timezone_offset_min": meta.get("local_timezone_offset_min"),
        "facility": dict(meta.get("facility", {})),
        "sample": dict(meta.get("sample", {})),
        "environment": {
            "locked": env.get("locked"),
            "field_sweep_confirmed_off": env.get(
                "lock_sweep_confirmed_off",
                meta.get("instrument", {}).get("bruker", {}).get(
                    "bsms_field_sweep_confirmed_off")),
            "operator_notes": env.get("operator_notes", ""),
        },
        "spectrometer": {
            "probe_type": spec.get("probe_type"),
            "console": spec.get("console", ""),
            "probe_string": spec.get("probe_string", ""),
            "coil_temp_k": spec.get("coil_temp_k"),
            "preamp_temp_k": spec.get("preamp_temp_k"),
            "h1_freq_mhz": spec.get("h1_freq_mhz"),
            "field_tesla": spec.get("field_tesla"),
        },
        "instrument": {
            "topspin_version": spec.get(
                "topspin_version",
                meta.get("instrument", {}).get("bruker", {}).get(
                    "topspin_version")),
        },
        "calibration": dict(meta.get("calibration", {})),
        "experiments": [dict(e) for e in meta.get("experiments", [])],
    }
    return answers


CORE_SPEC_FIELDS = ("h1_freq_mhz", "field_tesla", "console", "probe_string",
                    "probe_type", "coil_temp_k", "preamp_temp_k")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    tmp = tempfile.mkdtemp(prefix="spin_noise_roundtrip_")
    print("workdir: %s" % tmp)

    pack_mod = load_module("snn_packer", PACKER)
    parser_unit_checks(pack_mod, tmp)

    if argv:
        bundle = argv[0]
    else:
        print("no bundle given -- building a synthetic desktest bundle")
        out = subprocess.run(
            [sys.executable, os.path.join(TESTING, "make_synthetic_bundle.py"),
             "--out-dir", tmp], stdout=subprocess.PIPE, check=True)
        bundle = out.stdout.decode().strip().splitlines()[-1]
    check("input bundle exists", os.path.isfile(bundle), bundle)
    if not os.path.isfile(bundle):
        print("\nROUNDTRIP: FAIL (no input bundle)")
        return 1
    print("input bundle: %s" % bundle)

    # ---- 1. unpack ---------------------------------------------------------
    src_meta = read_meta(bundle)
    src_hashes = zip_data_hashes(bundle)
    tree = os.path.join(tmp, "tree")
    with zipfile.ZipFile(bundle, "r") as zf:
        for n in zf.namelist():
            if n.startswith("data/") and not n.endswith("/"):
                dest = os.path.join(tree, *n.split("/")[1:])
                d = os.path.dirname(dest)
                if not os.path.isdir(d):
                    os.makedirs(d)
                with zf.open(n) as fi, open(dest, "wb") as fo:
                    fo.write(fi.read())
    check("unpacked %d data files" % len(src_hashes), len(src_hashes) > 0)

    # ---- 2. derive answers.json, re-pack ------------------------------------
    answers_path = os.path.join(tmp, "answers.json")
    with open(answers_path, "w") as fh:
        json.dump(answers_from_meta(src_meta), fh, indent=2)

    out_dir = os.path.join(tmp, "repacked")
    rc = pack_mod.main([tree, "--answers", answers_path,
                        "--vendor", "bruker", "--out-dir", out_dir])
    check("packer exit code 0", rc == 0, "rc=%s" % rc)
    repacked = None
    if os.path.isdir(out_dir):
        zips = [os.path.join(out_dir, n) for n in sorted(os.listdir(out_dir))
                if n.endswith(".zip")]
        if len(zips) == 1:
            repacked = zips[0]
    check("exactly one repacked bundle produced", repacked is not None)
    if repacked is None:
        print("\nROUNDTRIP: FAIL")
        return 1
    print("repacked bundle: %s" % repacked)

    # ---- 3. data payload identical -------------------------------------------
    dst_hashes = zip_data_hashes(repacked)
    check("data/ file SET identical (%d files)" % len(src_hashes),
          set(src_hashes) == set(dst_hashes),
          "only in src: %s | only in repack: %s"
          % (sorted(set(src_hashes) - set(dst_hashes))[:5],
             sorted(set(dst_hashes) - set(src_hashes))[:5]))
    mismatched = [n for n in src_hashes
                  if dst_hashes.get(n) != src_hashes[n]]
    check("every data file bit-identical (sha256)", not mismatched,
          "; ".join(mismatched[:5]))

    # ---- 4. meta.json field preservation --------------------------------------
    dst_meta = read_meta(repacked)
    check("repacked schema_version == 2.0",
          dst_meta.get("schema_version") == "2.0")
    check("repacked vendor == bruker", dst_meta.get("vendor") == "bruker")
    for section in ("facility", "sample", "experiments", "calibration"):
        check("meta.%s preserved exactly" % section,
              dst_meta.get(section) == src_meta.get(section),
              "src=%r\n       dst=%r" % (src_meta.get(section),
                                         dst_meta.get(section)))
    spec_ok = all(dst_meta.get("spectrometer", {}).get(f)
                  == src_meta.get("spectrometer", {}).get(f)
                  for f in CORE_SPEC_FIELDS)
    check("spectrometer common core preserved", spec_ok,
          "src=%r\n       dst=%r" % (src_meta.get("spectrometer"),
                                     dst_meta.get("spectrometer")))
    ib = dst_meta.get("instrument", {}).get("bruker", {})
    check("topspin_version relocated into instrument.bruker",
          ib.get("topspin_version")
          == src_meta.get("spectrometer", {}).get("topspin_version"),
          repr(ib))
    check("BSMS sweep state relocated into instrument.bruker",
          ib.get("bsms_field_sweep_confirmed_off")
          == src_meta.get("environment", {}).get("lock_sweep_confirmed_off"),
          repr(ib))
    check("deprecated aliases mirrored (old tooling keeps working)",
          dst_meta.get("spectrometer", {}).get("topspin_version")
          == src_meta.get("spectrometer", {}).get("topspin_version")
          and dst_meta.get("environment", {}).get("lock_sweep_confirmed_off")
          == src_meta.get("environment", {}).get("lock_sweep_confirmed_off"))
    check("environment.locked / operator_notes preserved",
          dst_meta.get("environment", {}).get("locked")
          == src_meta.get("environment", {}).get("locked")
          and dst_meta.get("environment", {}).get("operator_notes")
          == src_meta.get("environment", {}).get("operator_notes"))
    check("run_mode preserved (test bundle can never pass as data)",
          dst_meta.get("software", {}).get("run_mode")
          == src_meta.get("software", {}).get("run_mode"),
          repr(dst_meta.get("software")))
    check("checksums map matches the recomputed data hashes",
          dst_meta.get("checksums")
          == dict((n, "sha256:" + h) for n, h in dst_hashes.items()))

    # ---- 5. uploader --selftest on the repacked bundle -------------------------
    st = subprocess.run([sys.executable, UPLOADER, repacked, "--selftest"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    tail = st.stdout.decode(errors="replace")
    print(tail)
    check("uploader --selftest on the repacked bundle: PASS",
          st.returncode == 0 and "RESULT: PASS" in tail)

    print("")
    if FAILURES:
        print("ROUNDTRIP: FAIL (%d check(s) failed)" % len(FAILURES))
        return 1
    print("ROUNDTRIP: PASS (packed bundle is payload-identical and "
          "schema-2.0 valid)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
