#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_synthetic_bundle.py -- construct a synthetic spin-noise bundle zip for
testing uploader/upload_bundle.py --selftest without a spectrometer.

    python3 testing/make_synthetic_bundle.py                     # v1.2 bundle
    python3 testing/make_synthetic_bundle.py --schema-version 1.1
    python3 testing/make_synthetic_bundle.py --schema-version 1.0
    python3 testing/make_synthetic_bundle.py --out-dir /tmp

Prints exactly one line on stdout: the path of the created bundle. All
progress goes to stderr, so CI (and CONTRIBUTING.md) can do:

    python3 uploader/upload_bundle.py \\
        "$(python3 testing/make_synthetic_bundle.py)" --selftest

The bundle mimics what topspin/spin_noise_run.py produces -- meta.json at
the zip root (valid against schema/meta.schema.json for the requested
schema version), fake Bruker experiment files under data/<expno>/, correct
sha256 checksums, and a filename following the
spinnoise_<slug>_<YYYYMMDD_HHMMSSZ>_<4hex>.zip convention. It contains no
measurement data; run_mode is stamped 'desktest' so it can never be
mistaken for a real record.

Same portability rules as the uploader: Python 3 standard library only,
nothing newer than 3.6.
"""

from __future__ import print_function

import argparse
import hashlib
import json
import os
import random
import sys
import time
import zipfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

FACILITY_SLUG = "ci-selftest"


def info(msg):
    print(msg, file=sys.stderr)


def repo_version():
    with open(os.path.join(REPO, "VERSION"), "r") as fh:
        return fh.read().strip()


def build_meta(schema_version, version):
    """A minimal-but-complete meta.json in the shape spin_noise_run.py writes."""
    utc = time.gmtime()
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", utc)
    started = time.strftime("%Y-%m-%dT%H:%M:%S", utc)
    meta = {
        "schema_version": schema_version,
        "program_version": version,
        "created_utc": created,
        "local_timezone_offset_min": 0,
        "facility": {
            "institution": "CI selftest (synthetic)",
            "city": "Nowhere",
            "country": "n/a",
            "facility_slug": FACILITY_SLUG,
            "contact_email": "",
            "contact_consent": False,
        },
        "spectrometer": {
            "topspin_version": "4.1.4",
            "h1_freq_mhz": 600.13,
            "field_tesla": 14.095,
            "console": "synthetic",
            "probe_string": "synthetic 5 mm probe",
            "probe_type": "unknown",
            "coil_temp_k": None,
            "preamp_temp_k": None,
        },
        "sample": {
            "description": "synthetic water (no sample exists)",
            "h2o_fraction_pct": 100.0,
            "d2o_pct": 0.0,
            "additives": "none",
            "tube_od_mm": 5.0,
            "sample_volume_ul": 550.0,
            "vt_setpoint_k": 298.0,
        },
        "environment": {
            "locked": False,
            "lock_sweep_confirmed_off": True,
            "operator_notes": "synthetic CI bundle; not data; never upload",
        },
        "calibration": {
            "p90_us": 10.0,
            "p90_power_db_or_w": "unknown",
            "rg_ladder": [
                {"expno": 10, "rg": 1.0, "tip_deg": 1.0},
                {"expno": 14, "rg": 8.0, "tip_deg": 1.0},
            ],
            "topshim_ok": False,
        },
        "experiments": [
            {
                "expno": 11,
                "role": "reference_open",
                "pulprog": "zg2d",
                "td": 32768,
                "td1_rows": 8,
                "sw_hz": 12019.23,
                "o1_hz": 3705.8,
                "rg": 25.0,
                "ns": 1,
                "aq_s_per_row": 1.36,
                "started_local": started,
                "finished_local": started,
            },
            {
                "expno": 12,
                "role": "noise",
                "pulprog": "zgnoise2d",
                "td": 32768,
                "td1_rows": 4,
                "sw_hz": 12019.23,
                "o1_hz": 3705.8,
                "rg": 101.0,
                "ns": 1,
                "aq_s_per_row": 1.36,
                "started_local": started,
                "finished_local": started,
            },
        ],
        "checksums": {},  # filled in below
    }
    if schema_version in ("1.1", "1.2"):
        meta["software"] = {
            "script_version": version,
            "schema_version": schema_version,
            "script_sha256": "unavailable",
            "run_mode": "desktest",
        }
    if schema_version == "1.2":
        # Minimal clock_audit (the 1.2 addition): two blocks bracketing
        # the two experiments above, wall == OCXO (zero offset), plus the
        # NTP-status fields. Exercises the uploader's 1.2 validation path.
        meta["clock_audit"] = {
            "blocks": [
                {"expno": 11, "role": "reference_open",
                 "wall_start_ms": 1787000000000,
                 "wall_end_ms": 1787000010880,
                 "ocxo_expected_s": 8 * 1.36},
                {"expno": 12, "role": "noise",
                 "wall_start_ms": 1787000013000,
                 "wall_end_ms": 1787000018440,
                 "ocxo_expected_s": 4 * 1.36},
            ],
            "ntp_status_raw": "synthetic CI bundle (no NTP daemon queried)",
            "workstation_time_source": "synthetic",
        }
    return meta


def fake_data_files(ser_mib=None):
    """(in-zip path, bytes) pairs standing in for Bruker experiment dirs.
    With ser_mib set, the 'ser' payload is that many MiB of incompressible
    random bytes -- used by testing/test_upload_integration.sh to exercise
    the uploader's chunked multipart path with a realistically large bundle."""
    acqus = (
        "##TITLE= Parameter file, synthetic\n"
        "##$PULPROG= <zgnoise2d>\n"
        "##$TD= 32768\n"
        "##$NS= 1\n"
        "##END=\n"
    ).encode("ascii")
    if ser_mib:
        # os.urandom is incompressible, so the zip lands at ~ser_mib MiB.
        ser = b"".join(os.urandom(1024 * 1024) for _ in range(ser_mib))
    else:
        # Deterministic pseudo-noise 'ser' payload (content is irrelevant; the
        # selftest only checks that the sha256 in meta.json matches the bytes).
        rng = random.Random(20260825)
        ser = bytes(bytearray(rng.getrandbits(8) for _ in range(4096)))
    return [
        ("data/11/acqus", acqus),
        ("data/12/acqus", acqus),
        ("data/12/ser", ser),
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a synthetic spin-noise bundle zip for selftest.")
    parser.add_argument("--schema-version", choices=("1.0", "1.1", "1.2"),
                        default="1.2",
                        help="schema_version the bundle declares (default 1.2; "
                             "1.1 omits the 'clock_audit' object and 1.0 "
                             "additionally omits 'software', as real bundles "
                             "of those vintages did)")
    parser.add_argument("--out-dir", default=None,
                        help="directory for the zip (default: a fresh "
                             "'synthetic_bundles' dir under testing/)")
    parser.add_argument("--ser-mib", type=int, default=None,
                        help="size of the synthetic ser payload in MiB "
                             "(random, incompressible) -- for exercising the "
                             "uploader's chunked path; default: tiny")
    args = parser.parse_args(argv)

    out_dir = args.out_dir or os.path.join(REPO, "testing", "synthetic_bundles")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    version = repo_version()
    meta = build_meta(args.schema_version, version)

    files = fake_data_files(args.ser_mib)
    for arc_name, payload in files:
        meta["checksums"][arc_name] = "sha256:" + hashlib.sha256(payload).hexdigest()

    stamp = time.strftime("%Y%m%d_%H%M%SZ", time.gmtime())
    suffix = "%04x" % random.randint(0, 0xFFFF)
    bundle_name = "spinnoise_%s_%s_%s.zip" % (FACILITY_SLUG, stamp, suffix)
    bundle_path = os.path.join(out_dir, bundle_name)

    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(meta, indent=2) + "\n")
        for arc_name, payload in files:
            zf.writestr(arc_name, payload)

    info("synthetic bundle (schema %s, program %s): %s"
         % (args.schema_version, version, bundle_path))
    print(bundle_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
