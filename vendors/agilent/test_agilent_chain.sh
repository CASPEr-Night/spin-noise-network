#!/usr/bin/env bash
# test_agilent_chain.sh -- prove the Agilent adapter + packer + uploader
# selftest chain works today, without hardware.
#
#   bash vendors/agilent/test_agilent_chain.sh [workdir]
#
# Steps:
#   1. build a synthetic VnmrJ session (make_synthetic_agilent_data.py)
#   2. pack it (packer/pack_bundle.py --vendor agilent, schema 2.0)
#   3. validate the bundle with uploader/upload_bundle.py --selftest
#   4. sanity-assert key meta.json fields (vendor, run_mode, mapping)
#   5. sanity-check the standalone inspector on the synthetic files
#
# Exits non-zero on any failure.  Same shape as
# vendors/magritek/test_magritek_chain.sh.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${1:-$(mktemp -d)}"
mkdir -p "$WORK"

echo "workdir: $WORK" >&2

SESSION=$(python3 "$REPO/vendors/agilent/make_synthetic_agilent_data.py" --out-dir "$WORK")
echo "session: $SESSION" >&2

BUNDLE=$(cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$SESSION" \
    --answers "$SESSION/answers_packer.json" --vendor agilent \
    --out-dir "$WORK" | tail -1)
echo "bundle (central packer): $BUNDLE" >&2
python3 "$REPO/uploader/upload_bundle.py" "$BUNDLE" --selftest

python3 - "$BUNDLE" <<'EOF'
import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf:
    meta = json.loads(zf.read("meta.json").decode("utf-8"))
    names = set(zf.namelist())

assert meta["schema_version"] == "2.0", meta["schema_version"]
assert meta["vendor"] == "agilent"
assert meta["software"]["run_mode"] == "desktest", (
    "synthetic bundle MUST be stamped desktest, got %r"
    % meta["software"]["run_mode"])
assert meta["software"]["writer"] == "packer/pack_bundle.py"
inst = meta["instrument"]["agilent"]
assert inst["data_format"] == "varian-fid"
assert inst["vnmrj_version"].startswith("synthetic")
assert inst["receiver_gain_db"] == 60.0, inst["receiver_gain_db"]

roles = [e["role"] for e in meta["experiments"]]
assert roles == (["rg_ladder"] * 4 + ["reference_open"]
                 + ["noise"] * 3 + ["reference_close"]), roles

# mapping spot-checks against the generator's constants
noise = [e for e in meta["experiments"] if e["role"] == "noise"][0]
assert noise["expno"] == 12, noise["expno"]
assert noise["td"] == 16384, noise["td"]          # np verbatim (re+im)
assert noise["td1_rows"] == 1, noise["td1_rows"]  # fid nblocks
assert abs(noise["sw_hz"] - 10000.0) < 1e-9, noise["sw_hz"]
assert abs(noise["rg"] - 10.0 ** (60.0 / 20.0)) < 1e-9, noise["rg"]
assert abs(noise["aq_s_per_row"] - 16384 / (2.0 * 10000.0)) < 1e-9
assert noise["pulprog"] == "s2pul", noise["pulprog"]
assert abs(meta["spectrometer"]["h1_freq_mhz"] - 399.945) < 1e-9
assert abs(meta["spectrometer"]["field_tesla"]
           - 399.945 / 42.5774806) < 1e-6
assert meta["spectrometer"]["probe_type"] == "RT"

ladder = meta["calibration"]["rg_ladder"]
assert [r["expno"] for r in ladder] == [10, 14, 15, 16]

# every data file present and checksummed; svf sidecars packed too
for arc in meta["checksums"]:
    assert arc in names, arc
assert "data/12/fid" in meta["checksums"]
assert "data/12/procpar" in meta["checksums"]
assert "data/12/text" in meta["checksums"]

print("meta.json assertions: all OK")
EOF

# standalone inspector smoke test on one synthetic experiment
NOISE_DIR=$(ls -d "$SESSION"/12_*.fid)
python3 "$REPO/vendors/agilent/agilent_reader.py" inspect "$NOISE_DIR" >/dev/null
python3 - "$NOISE_DIR" "$REPO" <<'EOF'
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location(
    "ar", os.path.join(sys.argv[2], "vendors", "agilent",
                       "agilent_reader.py"))
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)
fid = ar.read_fid(os.path.join(sys.argv[1], "fid"))
assert fid["structure_ok"], fid
assert fid["dtype"] == "int32", fid["dtype"]
assert fid["np_total_points"] == 16384
pp = ar.parse_procpar(os.path.join(sys.argv[1], "procpar"))
assert pp["tn"] == "H1" and pp["seqfil"] == "s2pul"
assert pp["np"] == 16384.0 and pp["gain"] == 60.0
assert "_unparsed" not in pp, pp.get("_unparsed")
print("inspector assertions: all OK")
EOF

echo "AGILENT CHAIN TEST: PASS" >&2
