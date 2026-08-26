#!/usr/bin/env bash
# test_magritek_chain.sh -- prove the Magritek adapter + packer + uploader
# selftest chain works today, without hardware.
#
#   bash vendors/magritek/test_magritek_chain.sh [workdir]
#
# Steps:
#   1. build a synthetic Spinsolve session (make_synthetic_magritek_data.py)
#   2. pack it (magritek_reader.py pack, schema 2.0)
#   3. validate the bundle with uploader/upload_bundle.py --selftest
#   4. same again with the --schema-version 1.2 fallback
#   5. sanity-assert key meta.json fields (vendor, run_mode, mapping)
#
# Exits non-zero on any failure.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${1:-$(mktemp -d)}"
mkdir -p "$WORK"

echo "workdir: $WORK" >&2

SESSION=$(python3 "$REPO/vendors/magritek/make_synthetic_magritek_data.py" --out-dir "$WORK")
echo "session: $SESSION" >&2

BUNDLE=$(python3 "$REPO/vendors/magritek/magritek_reader.py" pack "$SESSION" --out-dir "$WORK")
echo "bundle (2.0): $BUNDLE" >&2
python3 "$REPO/uploader/upload_bundle.py" "$BUNDLE" --selftest

BUNDLE12=$(python3 "$REPO/vendors/magritek/magritek_reader.py" pack "$SESSION" --out-dir "$WORK" --schema-version 1.2)
echo "bundle (1.2 fallback): $BUNDLE12" >&2
python3 "$REPO/uploader/upload_bundle.py" "$BUNDLE12" --selftest

# Central packer path (packer/pack_bundle.py MagritekReader adapter),
# when the central packer is present (it lands with package A).
if [ -f "$REPO/packer/pack_bundle.py" ]; then
    BUNDLEP=$(cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$SESSION" \
        --answers "$SESSION/answers_packer.json" --vendor magritek \
        --out-dir "$WORK" | tail -1)
    echo "bundle (central packer): $BUNDLEP" >&2
    python3 "$REPO/uploader/upload_bundle.py" "$BUNDLEP" --selftest
    python3 - "$BUNDLEP" <<'EOF'
import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf:
    meta = json.loads(zf.read("meta.json").decode("utf-8"))
assert meta["vendor"] == "magritek"
assert meta["software"]["run_mode"] == "desktest"
assert meta["software"]["writer"] == "packer/pack_bundle.py"
assert meta["instrument"]["magritek"]["data_format"] == "prospa-1d"
assert meta["instrument"]["magritek"]["rx_gain"] == 60.0
noise = [e for e in meta["experiments"] if e["role"] == "noise"][0]
assert noise["td"] == 2 * 8192, noise["td"]
assert abs(noise["rg"] - 10.0 ** (60.0 / 20.0)) < 1e-9, noise["rg"]
assert abs(meta["spectrometer"]["h1_freq_mhz"] - 60.0) < 1e-9
print("central-packer meta.json assertions: all OK")
EOF
else
    echo "central packer not present; skipped that path" >&2
fi

python3 - "$BUNDLE" <<'EOF'
import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf:
    meta = json.loads(zf.read("meta.json").decode("utf-8"))
    names = set(zf.namelist())

assert meta["schema_version"] == "2.0", meta["schema_version"]
assert meta["vendor"] == "magritek"
assert meta["instrument"]["magritek"]["data_format"] == "prospa-1d"
assert meta["software"]["run_mode"] == "desktest", (
    "synthetic bundle MUST be stamped desktest, got %r"
    % meta["software"]["run_mode"])
assert meta["software"]["writer"] == "vendors/magritek/magritek_reader.py"

roles = [e["role"] for e in meta["experiments"]]
assert roles == (["rg_ladder"] * 4 + ["reference_open"]
                 + ["noise"] * 3 + ["reference_close"]), roles

# mapping spot-checks against the generator's constants
noise = [e for e in meta["experiments"] if e["role"] == "noise"][0]
assert noise["td"] == 2 * 8192, noise["td"]
assert abs(noise["sw_hz"] - 1.0e6 / 100.0) < 1e-6, noise["sw_hz"]
assert abs(noise["rg"] - 10.0 ** (60.0 / 20.0)) < 1e-9, noise["rg"]
assert abs(noise["aq_s_per_row"] - 8192 * 100.0e-6) < 1e-9
assert abs(meta["spectrometer"]["h1_freq_mhz"] - 60.0) < 1e-9
assert abs(meta["spectrometer"]["field_tesla"] - 60.0 / 42.5774689) < 1e-9
assert meta["spectrometer"]["probe_type"] == "permanent-magnet-benchtop"

# clock audit present and self-consistent
audit = meta["clock_audit"]
assert len(audit["blocks"]) == 9
for blk in audit["blocks"]:
    assert blk["wall_end_ms"] > blk["wall_start_ms"]
    assert blk["ocxo_expected_s"] and blk["ocxo_expected_s"] > 0

# every data file present and checksummed
for arc in meta["checksums"]:
    assert arc in names, arc
assert "data/20/data.1d" in meta["checksums"]
assert "data/answers.json" in meta["checksums"]

print("meta.json assertions: all OK")
EOF

echo "MAGRITEK CHAIN TEST: PASS" >&2
