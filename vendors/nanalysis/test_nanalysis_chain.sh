#!/usr/bin/env bash
# test_nanalysis_chain.sh -- prove the Nanalysis converter chain works today,
# without hardware and WITHOUT the shared-file wiring (the vendor enum /
# instrument.nanalysis schema block / VENDOR_READERS entry are a follow-up
# commit; this test builds a locally patched schema copy from the constants
# exported by nanalysis_reader.py -- the exact payload of that commit).
#
#   bash vendors/nanalysis/test_nanalysis_chain.sh [workdir]
#
# Steps:
#   1. build a synthetic NMReady session (make_synthetic_nanalysis.py)
#   2. direct-import reader test: label digest + exact FID recovery
#   3. pack it (nanalysis_reader.py pack, schema-2.0-shaped)
#   4. validate the bundle with uploader/upload_bundle.py --selftest against
#      the patched schema copy
#   5. sanity-assert key meta.json fields (vendor, run_mode, mapping)
#   6. duck-typed VendorReader adapter smoke test (discover/read/instrument)
#
# Exits non-zero on any failure.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${1:-$(mktemp -d)}"
mkdir -p "$WORK"

echo "workdir: $WORK" >&2

SESSION=$(python3 "$REPO/vendors/nanalysis/make_synthetic_nanalysis.py" --out-dir "$WORK")
echo "session: $SESSION" >&2

# --- step 2: direct module import (no shared-file wiring needed) -----------
python3 - "$REPO" "$SESSION" <<'EOF'
import importlib.util, os, sys
repo, session = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location(
    "nan", os.path.join(repo, "vendors", "nanalysis", "nanalysis_reader.py"))
nan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nan)
spec2 = importlib.util.spec_from_file_location(
    "synth", os.path.join(repo, "vendors", "nanalysis",
                          "make_synthetic_nanalysis.py"))
synth = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(synth)

# one noise record: digest + exact numeric recovery vs the generator
path = os.path.join(session, "20_sn_noise.dx")
r = nan.read_nanalysis_dx(path)
d = r["nan"]
assert d["software_version"] == synth.SOFTWARE_VERSION, d["software_version"]
assert d["is_fid"] is True
assert d["td"] == 2 * synth.COMPLEX_POINTS, d["td"]
assert abs(d["sw_hz"] - synth.SWH_HZ) < 1e-6, d["sw_hz"]
assert abs(d["o1_hz"] - synth.O1_HZ) < 1e-6
assert abs(d["sfo1_mhz"] - synth.SFO1_MHZ) < 1e-9
assert d["recvr_gain_raw"] == float(synth.NOISE_GAIN)
assert d["ns"] == 1
assert abs(d["aq_s"] - synth.AQ_S) < 1e-9
assert d["tz_offset_min"] == 60, d["tz_offset_min"]
assert d["started_local_iso"] and "T" in d["started_local_iso"]

re_exp, im_exp = synth._fid(20, synth.COMPLEX_POINTS, 0.0)
re_got, im_got = r["data"]["re"], r["data"]["im"]
assert len(re_got) == len(re_exp) == synth.COMPLEX_POINTS
scale = max(max(abs(v) for v in re_exp), max(abs(v) for v in im_exp))
for got, exp in ((re_got, re_exp), (im_got, im_exp)):
    worst = max(abs(a - b) for a, b in zip(got, exp))
    assert worst < 2e-5 * scale, "FID recovery worst error %g" % worst
print("direct-import reader test: exact FID recovery + digest OK")
EOF

# --- step 3: pack ------------------------------------------------------------
BUNDLE=$(python3 "$REPO/vendors/nanalysis/nanalysis_reader.py" pack "$SESSION" --out-dir "$WORK")
echo "bundle: $BUNDLE" >&2

# --- step 4: patched-schema validation (the deferred wiring, applied to a
#     local COPY of the schema only -- shared files stay untouched) ----------
PATCHED="$WORK/meta.schema.nanalysis-patched.json"
python3 - "$REPO" "$PATCHED" <<'EOF'
import importlib.util, json, os, sys
repo, out = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location(
    "nan", os.path.join(repo, "vendors", "nanalysis", "nanalysis_reader.py"))
nan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nan)
with open(os.path.join(repo, "schema", "meta.schema.json")) as fh:
    schema = json.load(fh)
enum = schema["properties"]["vendor"]["enum"]
if nan.SCHEMA_VENDOR_ENUM_VALUE not in enum:
    enum.append(nan.SCHEMA_VENDOR_ENUM_VALUE)
schema["properties"]["instrument"]["properties"]["nanalysis"] = \
    nan.INSTRUMENT_SCHEMA_BLOCK
with open(out, "w") as fh:
    json.dump(schema, fh, indent=2)
print("patched schema copy written:", out)
EOF

python3 "$REPO/uploader/upload_bundle.py" "$BUNDLE" --selftest --schema "$PATCHED"

# --- step 5: meta.json assertions -------------------------------------------
python3 - "$BUNDLE" <<'EOF'
import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf:
    meta = json.loads(zf.read("meta.json").decode("utf-8"))
    names = set(zf.namelist())

assert meta["schema_version"] == "2.0"
assert meta["vendor"] == "nanalysis"
inst = meta["instrument"]["nanalysis"]
assert inst["software_version"] == "2.2.6.2", inst
assert inst["data_format"] == "jcamp-dx"
assert inst["receiver_gain"] == 14.0
assert inst["firmware_version"] == "2.6.0"
assert meta["software"]["run_mode"] == "desktest", (
    "synthetic bundle MUST be stamped desktest, got %r"
    % meta["software"]["run_mode"])
assert meta["software"]["writer"] == "vendors/nanalysis/nanalysis_reader.py"

roles = [e["role"] for e in meta["experiments"]]
assert roles == (["rg_ladder"] * 4 + ["reference_open"]
                 + ["noise"] * 3 + ["reference_close"]), roles

noise = [e for e in meta["experiments"] if e["role"] == "noise"][0]
assert noise["td"] == 2 * 2048, noise["td"]
assert abs(noise["sw_hz"] - 735.29413622215111) < 1e-6
assert noise["rg"] == 14.0, "rg must be RECVR_GAIN VERBATIM (native units)"
assert abs(noise["o1_hz"] - 300.28249915091567) < 1e-6
assert abs(noise["aq_s_per_row"] - 2048 / 735.29413622215111) < 1e-6
assert noise["started_local"] < noise["finished_local"]

assert abs(meta["spectrometer"]["h1_freq_mhz"] - 60.05649983018314) < 1e-9
assert abs(meta["spectrometer"]["field_tesla"] - 1.410588) < 1e-9
assert meta["spectrometer"]["probe_type"] == "permanent-magnet-benchtop"
assert meta["local_timezone_offset_min"] == 60

ladder = meta["calibration"]["rg_ladder"]
assert [l["rg"] for l in ladder] == [1.0, 4.0, 8.0, 14.0], ladder
for l in ladder:
    assert abs(l["tip_deg"] - 1.0) < 1e-6, l   # 90*X_PULSE/OBSERVE90

assert "clock_audit" not in meta, (
    "no clock_audit may be fabricated from UNVERIFIED timestamps")

for arc in meta["checksums"]:
    assert arc in names, arc
assert "data/20/20_sn_noise.dx" in meta["checksums"]
assert "data/answers.json" in meta["checksums"]
print("meta.json assertions: all OK")
EOF

# --- step 6: duck-typed VendorReader adapter smoke test ----------------------
python3 - "$REPO" "$SESSION" <<'EOF'
import importlib.util, os, sys
repo, session = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location(
    "nan", os.path.join(repo, "vendors", "nanalysis", "nanalysis_reader.py"))
nan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nan)

reader = nan.NanalysisReader()
found = reader.discover_experiments(session)
assert [e for e, _ in found] == [10, 11, 13, 14, 15, 16, 20, 21, 22], found
discovered = {}
for expno, path in found:
    discovered[expno] = reader.read_experiment(path)
d = discovered[20]
assert d["td"] == 4096 and d["rg"] == 14.0 and d["td1_rows"] == 1
assert abs(d["h1_freq_mhz"] - 60.05649983018314) < 1e-9
block, warns = reader.instrument_block(
    {"instrument": {"lock_nucleus": "unknown",
                    "firmware_version": "2.6.0"}}, discovered)
assert block["software_version"] == "2.2.6.2", block
assert block["receiver_gain"] == 14.0
assert block["firmware_version"] == "2.6.0"
print("VendorReader adapter smoke test: OK (%d warnings)" % len(warns))
EOF

echo "NANALYSIS CHAIN TEST: PASS" >&2
