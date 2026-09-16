#!/usr/bin/env bash
# test_agilent_chain.sh -- prove the Agilent adapter + packer + uploader
# selftest chain works today, without hardware.
#
#   bash vendors/agilent/test_agilent_chain.sh [workdir]
#
# Steps:
#   1. build a synthetic VnmrJ session (make_synthetic_agilent_data.py)
#   2. pack it (packer/pack_bundle.py --vendor agilent, schema 2.0) and
#      require ZERO "reconstructed from file modification time" warnings
#      (procpar time_run/time_complete carry the acquisition times)
#   3. validate the bundle with uploader/upload_bundle.py --selftest
#   4. sanity-assert key meta.json fields (vendor, run_mode, mapping,
#      started_local == procpar time_run for every experiment)
#   5. sanity-check the standalone inspector on the synthetic files
#   6. pack a second session whose console clock runs 3 h fast and whose
#      questionnaire omits instrument.vnmrj_version: require the
#      ahead-clock WARN and the vnmrrev auto-fill; re-pack it with
#      answers that declare the console's zone 3 h east and require the
#      WARN to go away (the zone answer is what the WARN text advises)
#   7. blank one experiment's time_run: require the started_local
#      reconstruction WARN and NO finished-before-started WARN
#   8. an unusable vnmrrev copy without an answered version must abort
#      naming the file and the reason (not "no vnmrrev file"); a valid
#      vnmrrev.txt beside it must still be used; a mistyped (non-string)
#      answer must abort as a type problem, not fall back to vnmrrev
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
    --out-dir "$WORK" 2>"$WORK/pack1.log" | tail -1)
cat "$WORK/pack1.log" >&2
echo "bundle (central packer): $BUNDLE" >&2
if grep -q "reconstructed from file modification time" "$WORK/pack1.log"; then
    echo "FAIL: packer fell back to file mtimes although procpar carries time_run/time_complete" >&2
    exit 1
fi
if grep -q "acquisition clock appears ahead" "$WORK/pack1.log"; then
    echo "FAIL: ahead-clock WARN on a session with a fixed past base time" >&2
    exit 1
fi
python3 "$REPO/uploader/upload_bundle.py" "$BUNDLE" --selftest

python3 - "$BUNDLE" "$SESSION" "$REPO" <<'EOF'
import glob, importlib.util, json, os, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf:
    meta = json.loads(zf.read("meta.json").decode("utf-8"))
    names = set(zf.namelist())
session, repo = sys.argv[2], sys.argv[3]

assert meta["schema_version"] == "2.0", meta["schema_version"]
assert meta["vendor"] == "agilent"
assert meta["software"]["run_mode"] == "desktest", (
    "synthetic bundle MUST be stamped desktest, got %r"
    % meta["software"]["run_mode"])
assert meta["software"]["writer"] == "packer/pack_bundle.py"
inst = meta["instrument"]["agilent"]
assert inst["data_format"] == "varian-fid"
assert inst["vnmrj_version"].startswith("synthetic")   # answers win
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

# acquisition times come from procpar, not from file mtimes
spec = importlib.util.spec_from_file_location(
    "ar", os.path.join(repo, "vendors", "agilent", "agilent_reader.py"))
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)
for e in meta["experiments"]:
    (d,) = glob.glob(os.path.join(session, "%d_*.fid" % e["expno"]))
    pp = ar.parse_procpar(os.path.join(d, "procpar"))
    run = ar.vnmrj_time_iso(pp["time_run"])
    complete = ar.vnmrj_time_iso(pp["time_complete"])
    assert run and complete, (e["expno"], pp.get("time_run"))
    assert e["started_local"] == run, (e["expno"], e["started_local"], run)
    assert e["finished_local"] == complete, (e["expno"],
                                             e["finished_local"], complete)
    assert e["finished_local"] > e["started_local"], e
assert meta["experiments"][0]["started_local"] == "2026-03-01T09:00:04", (
    meta["experiments"][0]["started_local"])   # default --base-time

# every data file present and checksummed; svf sidecars packed too
for arc in meta["checksums"]:
    assert arc in names, arc
assert "data/12/fid" in meta["checksums"]
assert "data/12/procpar" in meta["checksums"]
assert "data/12/text" in meta["checksums"]
assert not any(n.endswith("/vnmrrev") for n in names), (
    "vnmrrev sits at the session top, not inside an experiment")

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
assert pp["time_processed"] == "", repr(pp.get("time_processed"))
assert "_unparsed" not in pp, pp.get("_unparsed")
assert ar.vnmrj_time_iso("20260914T181359") == "2026-09-14T18:13:59"
for bad in ("", "20260914", "20261314T181359", "20260914T181360",
            "Sep 14 2026", None, 3.0):
    assert ar.vnmrj_time_iso(bad) is None, bad
session = os.path.dirname(sys.argv[1])
rev = ar.parse_vnmrrev(os.path.join(session, "vnmrrev"))
assert rev["vnmrj_version"] == "3.2 Revision A", rev
assert rev["system"] == "vnmrsdd2", rev
# a copy saved through Windows Notepad (UTF-8 BOM) parses the same
with open(os.path.join(session, "vnmrrev"), "rb") as fh:
    raw = fh.read()
bom_path = os.path.join(session, "vnmrrev_bom_copy.txt")
with open(bom_path, "wb") as fh:
    fh.write(b"\xef\xbb\xbf" + raw)
assert ar.parse_vnmrrev(bom_path) == rev, ar.parse_vnmrrev(bom_path)
os.remove(bom_path)
print("inspector assertions: all OK")
EOF

# a console clock 3 h fast (the state SIU found on its console before the
# 2026-09-14 sessions) must be called out once per experiment, and a
# questionnaire without vnmrj_version must pick up the vnmrrev copy
SKEWED=$(python3 "$REPO/vendors/agilent/make_synthetic_agilent_data.py" \
    --out-dir "$WORK/skewed" --base-time now --clock-skew-s 10800)
echo "skewed session: $SKEWED" >&2
python3 - "$SKEWED" "$REPO" <<'EOF'
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location(
    "pk", os.path.join(sys.argv[2], "packer", "pack_bundle.py"))
pk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pk)
p = os.path.join(sys.argv[1], "answers_packer.json")
with open(p) as fh:
    answers = json.load(fh)
del answers["instrument"]["vnmrj_version"]
with open(os.path.join(sys.argv[1], "answers_noversion.json"), "w") as fh:
    json.dump(answers, fh, indent=2)
# the same stamps declared as a console zone 3 h EAST of this machine:
# then they are not in the future at all
answers["local_timezone_offset_min"] = pk.local_tz_offset_min() + 180
with open(os.path.join(sys.argv[1], "answers_east.json"), "w") as fh:
    json.dump(answers, fh, indent=2)
EOF
BUNDLE2=$(cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$SKEWED" \
    --answers "$SKEWED/answers_noversion.json" --vendor agilent \
    --out-dir "$WORK/skewed" 2>"$WORK/pack2.log" | tail -1)
cat "$WORK/pack2.log" >&2
echo "bundle (skewed clock): $BUNDLE2" >&2
N_AHEAD=$(grep -c "acquisition clock appears ahead of this machine by ~" "$WORK/pack2.log" || true)
if [ "$N_AHEAD" != "9" ]; then
    echo "FAIL: expected one ahead-clock WARN per experiment (9) for procpar stamps 3 h in the future, got $N_AHEAD" >&2
    exit 1
fi
if grep -q "reconstructed from file modification time" "$WORK/pack2.log"; then
    echo "FAIL: skewed session fell back to file mtimes" >&2
    exit 1
fi
if ! grep -q "vnmrj_version '3.2 Revision A' taken from" "$WORK/pack2.log"; then
    echo "FAIL: vnmrj_version not auto-filled from the session's vnmrrev copy" >&2
    exit 1
fi
python3 "$REPO/uploader/upload_bundle.py" "$BUNDLE2" --selftest
python3 - "$BUNDLE2" <<'EOF'
import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf:
    meta = json.loads(zf.read("meta.json").decode("utf-8"))
assert meta["instrument"]["agilent"]["vnmrj_version"] == "3.2 Revision A", (
    meta["instrument"]["agilent"]["vnmrj_version"])
print("skewed-clock assertions: all OK")
EOF

BUNDLE3=$(cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$SKEWED" \
    --answers "$SKEWED/answers_east.json" --vendor agilent \
    --out-dir "$WORK/skewed" 2>"$WORK/pack3.log" | tail -1)
cat "$WORK/pack3.log" >&2
echo "bundle (skewed clock, console zone declared): $BUNDLE3" >&2
if grep -q "acquisition clock appears ahead" "$WORK/pack3.log"; then
    echo "FAIL: ahead-clock WARN although local_timezone_offset_min places the console 3 h east" >&2
    exit 1
fi
if grep -q "is before started_local" "$WORK/pack3.log"; then
    echo "FAIL: finished-before-started WARN on well-ordered procpar stamps" >&2
    exit 1
fi

# one procpar without time_run: started_local is reconstructed (WARN), and
# the mtime-derived start must NOT be compared against the procpar finish
MIXED="$WORK/mixed/$(basename "$SESSION")"
mkdir -p "$WORK/mixed"
cp -R "$SESSION" "$MIXED"
python3 - "$MIXED" <<'EOF'
import glob, os, sys
(d,) = glob.glob(os.path.join(sys.argv[1], "17_*.fid"))
p = os.path.join(d, "procpar")
with open(p) as fh:
    lines = fh.read().splitlines()
idx = [i for i, ln in enumerate(lines) if ln.startswith("time_run ")]
assert len(idx) == 1, idx
lines[idx[0] + 1] = '1 ""'
with open(p, "w") as fh:
    fh.write("\n".join(lines) + "\n")
EOF
BUNDLE4=$(cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$MIXED" \
    --answers "$MIXED/answers_packer.json" --vendor agilent \
    --out-dir "$WORK/mixed" 2>"$WORK/pack4.log" | tail -1)
cat "$WORK/pack4.log" >&2
echo "bundle (one time_run blanked): $BUNDLE4" >&2
N_RECON=$(grep -c "reconstructed from file modification time" "$WORK/pack4.log" || true)
if [ "$N_RECON" != "1" ] || ! grep -q "experiment 17: started_local reconstructed" "$WORK/pack4.log"; then
    echo "FAIL: expected exactly one reconstruction WARN (experiment 17 started_local), got $N_RECON" >&2
    exit 1
fi
if grep -q "is before started_local\|acquisition clock appears ahead" "$WORK/pack4.log"; then
    echo "FAIL: clock WARN raised against an mtime-reconstructed started_local" >&2
    exit 1
fi

# vnmrrev copies that cannot be used, and a mistyped answer
REV=$(python3 "$REPO/vendors/agilent/make_synthetic_agilent_data.py" --out-dir "$WORK/rev")
python3 - "$REV" <<'EOF'
import json, os, sys
p = os.path.join(sys.argv[1], "answers_packer.json")
with open(p) as fh:
    answers = json.load(fh)
del answers["instrument"]["vnmrj_version"]
with open(os.path.join(sys.argv[1], "answers_noversion.json"), "w") as fh:
    json.dump(answers, fh, indent=2)
answers["instrument"]["vnmrj_version"] = 3.2
with open(os.path.join(sys.argv[1], "answers_mistyped.json"), "w") as fh:
    json.dump(answers, fh, indent=2)
EOF
: > "$REV/vnmrrev"     # present but empty
if (cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$REV" \
        --answers "$REV/answers_noversion.json" --vendor agilent \
        --out-dir "$WORK/rev" >/dev/null 2>"$WORK/pack5.log"); then
    echo "FAIL: packed with neither an answered vnmrj_version nor a usable vnmrrev" >&2
    exit 1
fi
cat "$WORK/pack5.log" >&2
if ! grep -q "vnmrrev copy could not be used ($REV/vnmrrev: " "$WORK/pack5.log" \
        || grep -q "has no vnmrrev file" "$WORK/pack5.log"; then
    echo "FAIL: the ERROR must name the rejected vnmrrev copy and why, not claim it is missing" >&2
    exit 1
fi
printf 'VnmrJ VERSION 3.2 REVISION A\nSeptember 21, 2011\nvnmrsdd2\n' > "$REV/vnmrrev.txt"
BUNDLE6=$(cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$REV" \
    --answers "$REV/answers_noversion.json" --vendor agilent \
    --out-dir "$WORK/rev" 2>"$WORK/pack6.log" | tail -1)
cat "$WORK/pack6.log" >&2
if ! grep -q "vnmrrev copy ignored -- $REV/vnmrrev: " "$WORK/pack6.log" \
        || ! grep -q "vnmrj_version '3.2 Revision A' taken from $REV/vnmrrev.txt" "$WORK/pack6.log"; then
    echo "FAIL: a valid vnmrrev.txt beside an empty vnmrrev must be used, with the empty one called out" >&2
    exit 1
fi
if (cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$REV" \
        --answers "$REV/answers_mistyped.json" --vendor agilent \
        --out-dir "$WORK/rev" >/dev/null 2>"$WORK/pack7.log"); then
    echo "FAIL: a non-string instrument.vnmrj_version answer was accepted" >&2
    exit 1
fi
cat "$WORK/pack7.log" >&2
if ! grep -q "answer instrument.vnmrj_version: expected a string, got 3.2" "$WORK/pack7.log" \
        || grep -q "taken from" "$WORK/pack7.log"; then
    echo "FAIL: a mistyped vnmrj_version answer must abort as a type problem, not fall back to vnmrrev" >&2
    exit 1
fi

echo "AGILENT CHAIN TEST: PASS" >&2
