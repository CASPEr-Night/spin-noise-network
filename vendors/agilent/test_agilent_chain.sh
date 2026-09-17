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
#   9. v0.7.1 extended session (--ladder-levels-db 0..40 x 3 rungs in
#      random order, --tuning-ladder 5, --signcal): roles noise_tune and
#      sweep_signcal pack and pass --selftest; the 27 repeated rungs
#      survive verbatim (acquisition order, duplicates, unsorted, ~6 s
#      apart) and read LINEAR: every rung's DFT amplitude per unit gain
#      agrees with the others to 1.5%; every tuning object survives
#      into meta; the signcal's o1_hz sits +200 Hz above the
#      references', its procpar sfrq is displaced with it (VnmrJ) and
#      does NOT move the bundle's h1_freq_mhz or raise the disagreement
#      WARN, and its synthetic line sits 200 Hz LOWER in the window
#      (pure-Python DFT: the physical convention the report's sign
#      check relies on)
#  10. role inference: an answers.json listing only the tuning entries
#      (and no rg_ladder) packs to the same roles/tuning, with the ladder
#      built from the rungs' stored gains; an answers.json listing no
#      experiments at all gets tuning.setting_index from the _sn_tune_k
#      names
#  11. schema-invalid tuning objects (missing setting_index, unknown key,
#      non-integer index) are REJECTED by the packer naming the key
#  12. a noise_tune save without an index and without a tuning entry
#      packs with a WARN; an answers.json role that contradicts the save
#      name wins, with a WARN
#  13. (when numpy is importable) the extended session repacked as
#      synthetic-injection runs through analysis/facility_report.py: the
#      randomized ladder fits the drift_compression model with an
#      amplitude compression envelope under 1% (the fixture is linear
#      and drift-free, so a linear fixture must read linear), no
#      significant drift, and the axis sign resolves to +1 (physical)
#  14. an answered calibration.rg_ladder is cross-checked: rungs naming
#      expnos the session lacks (a template ladder) and a rung whose rg
#      contradicts the stored gain each get a WARN; the list is still
#      recorded verbatim
#  15. save-name rule: the role word may be followed by anything that is
#      not a letter (12_sn_noise2, 17_sn_noise-1 are noise blocks;
#      12_sn_noisetest is not recognised); a renamed session packs from
#      names alone
#  16. a tuning reading that is not finite (1e400 -> inf, or the literal
#      NaN Python's json accepts) is rejected naming the key, so
#      meta.json never carries a non-standard JSON token
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

# v0.7.1 additions on one extended session: the randomized repeated gain
# ladder (9 levels x 3 rungs, seed 7), the spin-noise tuning ladder (5
# settings x 2 noise_tune blocks) and the axis-sign check (tof +200 Hz)
EXT=$(python3 "$REPO/vendors/agilent/make_synthetic_agilent_data.py" \
    --out-dir "$WORK/ext" --tuning-ladder 5 --signcal \
    --ladder-levels-db "0,5,10,15,20,25,30,35,40" --ladder-repeats 3 \
    --ladder-random-seed 7)
echo "extended session: $EXT" >&2
BUNDLE8=$(cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$EXT" \
    --answers "$EXT/answers_packer.json" --vendor agilent \
    --out-dir "$WORK/ext" 2>"$WORK/pack8.log" | tail -1)
cat "$WORK/pack8.log" >&2
echo "bundle (extended session): $BUNDLE8" >&2
if grep -q "reconstructed from file modification time\|without a 'tuning' object\|overrides the\|calibration.rg_ladder" "$WORK/pack8.log"; then
    echo "FAIL: unexpected WARN packing the extended session" >&2
    exit 1
fi
if grep -q "disagree on h1_freq_mhz" "$WORK/pack8.log"; then
    echo "FAIL: the signcal's displaced sfrq must not vote on h1_freq_mhz" >&2
    exit 1
fi
python3 "$REPO/uploader/upload_bundle.py" "$BUNDLE8" --selftest
python3 - "$BUNDLE8" "$EXT" "$REPO" <<'EOF'
import calendar, glob, importlib.util, json, math, os, struct, sys, time, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf:
    meta = json.loads(zf.read("meta.json").decode("utf-8"))
session, repo = sys.argv[2], sys.argv[3]
with open(os.path.join(session, "answers_packer.json")) as fh:
    answers = json.load(fh)
by_role = {}
for e in meta["experiments"]:
    by_role.setdefault(e["role"], []).append(e)
counts = dict((r, len(v)) for r, v in by_role.items())
assert counts == {"rg_ladder": 27, "reference_open": 1, "noise": 3,
                  "reference_close": 1, "noise_tune": 10,
                  "sweep_signcal": 1}, counts

# repeated rungs: verbatim, acquisition order, duplicates, unsorted
ladder = meta["calibration"]["rg_ladder"]
assert ladder == answers["calibration"]["rg_ladder"], "ladder not verbatim"
rgs = [r["rg"] for r in ladder]
assert len(rgs) == 27 and len(set(rgs)) == 9, rgs
assert rgs != sorted(rgs) and rgs != sorted(rgs, reverse=True), (
    "the randomized ladder came out monotonic: %r" % rgs)
rungs = by_role["rg_ladder"]
assert [r["expno"] for r in ladder] == [e["expno"] for e in rungs] \
    == list(range(1000, 1027))
for r, e in zip(ladder, rungs):
    assert abs(r["rg"] - e["rg"]) < 1e-9, (r, e["rg"])
    db = 20.0 * math.log10(e["rg"])
    assert abs(db - round(db)) < 1e-9, db          # integer-dB procpar gain
def epoch(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%S"))
starts = [epoch(e["started_local"]) for e in rungs]
assert all(b - a == 6 for a, b in zip(starts, starts[1:])), starts[:5]
assert meta["instrument"]["agilent"]["receiver_gain_db"] == 40.0, (
    meta["instrument"]["agilent"]["receiver_gain_db"])   # ladder maximum
assert all(abs(e["rg"] - 100.0) < 1e-9 for e in by_role["noise"])

# tuning objects survive verbatim; the main noise blocks carry none
want = dict((a["expno"], a["tuning"]) for a in answers["experiments"]
            if "tuning" in a)
assert len(want) == 10
tunes = by_role["noise_tune"]
for e in tunes:
    assert e["tuning"] == want[e["expno"]], (e["expno"], e.get("tuning"))
    assert e["td"] == 16384 and e["pulprog"] == "s2pul", e
assert sorted(e["tuning"]["setting_index"] for e in tunes) \
    == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]
assert [e["expno"] for e in tunes] == [2000, 2001, 2010, 2011, 2020, 2021,
                                       2030, 2031, 2040, 2041]
assert all("tuning" not in e for e in by_role["noise"])

# the sign check: one more reference with the carrier moved +200 Hz
(sc,) = by_role["sweep_signcal"]
(ro,) = by_role["reference_open"]
(rc,) = by_role["reference_close"]
assert sc["expno"] == 3000
assert abs(sc["o1_hz"] - ro["o1_hz"] - 200.0) < 1e-9, (sc["o1_hz"], ro["o1_hz"])
for k in ("rg", "td", "aq_s_per_row", "sw_hz", "ns"):
    assert sc[k] == ro[k] == rc[k], (k, sc[k], ro[k], rc[k])
# on VnmrJ sfrq tracks tof: the signcal's procpar carries the displaced
# carrier, and the bundle's h1_freq_mhz is still the references'
spec = importlib.util.spec_from_file_location(
    "ar", os.path.join(repo, "vendors", "agilent", "agilent_reader.py"))
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)
pp_sc = ar.parse_procpar(os.path.join(session, "3000_sn_signcal.fid", "procpar"))
pp_ro = ar.parse_procpar(os.path.join(session, "11_sn_ref_open.fid", "procpar"))
assert abs(pp_sc["sfrq"] - pp_ro["sfrq"] - 200e-6) < 1e-9, (pp_sc["sfrq"], pp_ro["sfrq"])
assert abs(meta["spectrometer"]["h1_freq_mhz"] - pp_ro["sfrq"]) < 1e-9, (
    meta["spectrometer"]["h1_freq_mhz"], pp_ro["sfrq"])
# lock referencing in every procpar: reffrq fixed by the lock, rfl
# following the VnmrJ identity reffrq = sfrq - sw/2 + rfl - rfp even in
# the signcal whose sfrq moved with tof; water at 4.75 ppm then sits at
# the line's +537.5 Hz physical offset in the references
for pp in (pp_ro, pp_sc):
    for k in ("reffrq", "rfl", "rfp"):
        assert isinstance(pp.get(k), float), (k, pp.get(k))
    resid = pp["reffrq"] * 1e6 - (pp["sfrq"] * 1e6 - pp["sw"] / 2.0 + pp["rfl"] - pp["rfp"])
    assert abs(resid) < 0.01, ("referencing identity broken by %.4f Hz" % resid, pp["rfl"])
assert abs(pp_sc["reffrq"] - pp_ro["reffrq"]) < 1e-12, (pp_sc["reffrq"], pp_ro["reffrq"])
assert abs(pp_sc["rfl"] - pp_ro["rfl"] + 200.0) < 1e-6, (pp_sc["rfl"], pp_ro["rfl"])
pred = pp_ro["reffrq"] * 1e6 * (1.0 + 4.75e-6) - pp_ro["sfrq"] * 1e6
assert abs(pred - 537.5) < 0.01, pred

# physical convention: the line's apparent offset DROPS by the
# displacement (re + i*im, positive offset = exp(+2 pi i f t))
def dft_amp(path, freqs, sw=10000.0):
    with open(path, "rb") as fh:
        raw = fh.read()
    npts = struct.unpack(">6ihhi", raw[:32])[2]
    v = struct.unpack(">%di" % npts, raw[60:60 + 4 * npts])
    re, im = v[0::2], v[1::2]
    out = {}
    for f in freqs:
        sr = si = 0.0
        for k in range(len(re)):
            ph = -2.0 * math.pi * f * k / sw
            c, s = math.cos(ph), math.sin(ph)
            sr += re[k] * c - im[k] * s
            si += re[k] * s + im[k] * c
        out[f] = math.hypot(sr, si) / len(re)
    return out
probe = (137.5, 337.5, 537.5, 737.5)
ref = dft_amp(os.path.join(session, "11_sn_ref_open.fid", "fid"), probe)
sig = dft_amp(os.path.join(session, "3000_sn_signcal.fid", "fid"), probe)
assert max(ref, key=ref.get) == 537.5 and ref[537.5] > 10 * ref[337.5], ref
assert max(sig, key=sig.get) == 337.5 and sig[337.5] > 10 * sig[537.5], sig
noise = dft_amp(os.path.join(session, "12_sn_noise.fid", "fid"), probe)
assert max(noise.values()) < 0.05 * ref[537.5], noise   # no line in noise

# the fixture is generated linear and drift-free, so it must READ
# linear: per-rung amplitude per unit gain within 1.5% of the median
# (the line's matched-filter SNR is ~440 per rung, 0.23% scatter)
per_rg = []
for e in rungs:
    (d,) = glob.glob(os.path.join(session, "%d_sn_ladder_*.fid" % e["expno"]))
    per_rg.append(dft_amp(os.path.join(d, "fid"), (537.5,))[537.5] / e["rg"])
med = sorted(per_rg)[len(per_rg) // 2]
worst = max(abs(v / med - 1.0) for v in per_rg)
assert worst < 0.015, ("ladder fixture not linear: worst deviation %.3f%%"
                       % (100 * worst), per_rg)
print("extended-session assertions: all OK (ladder linear to %.2f%%)"
      % (100 * worst))
EOF

# role inference from the Tier-1 save names, ladder built from the
# rungs' stored gains, tuning.setting_index from the _sn_tune_k names
python3 - "$EXT" <<'EOF'
import json, os, sys
p = os.path.join(sys.argv[1], "answers_packer.json")
with open(p) as fh:
    answers = json.load(fh)
answers["experiments"] = [a for a in answers["experiments"] if "tuning" in a]
del answers["calibration"]["rg_ladder"]
with open(os.path.join(sys.argv[1], "answers_inferred.json"), "w") as fh:
    json.dump(answers, fh, indent=2)
answers["experiments"] = []
with open(os.path.join(sys.argv[1], "answers_names_only.json"), "w") as fh:
    json.dump(answers, fh, indent=2)
EOF
BUNDLE9=$(cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$EXT" \
    --answers "$EXT/answers_inferred.json" --vendor agilent \
    --out-dir "$WORK/ext" 2>"$WORK/pack9.log" | tail -1)
cat "$WORK/pack9.log" >&2
if ! grep -q "built a 27-rung ladder from the rg_ladder experiments" "$WORK/pack9.log"; then
    echo "FAIL: rg_ladder omitted from answers.json must be built from the rg_ladder experiments" >&2
    exit 1
fi
python3 "$REPO/uploader/upload_bundle.py" "$BUNDLE9" --selftest
BUNDLE10=$(cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$EXT" \
    --answers "$EXT/answers_names_only.json" --vendor agilent \
    --out-dir "$WORK/ext" 2>"$WORK/pack10.log" | tail -1)
cat "$WORK/pack10.log" >&2
if grep -q "without a 'tuning' object" "$WORK/pack10.log"; then
    echo "FAIL: _sn_tune_k names must supply tuning.setting_index" >&2
    exit 1
fi
python3 "$REPO/uploader/upload_bundle.py" "$BUNDLE10" --selftest
python3 - "$BUNDLE8" "$BUNDLE9" "$BUNDLE10" <<'EOF'
import json, sys, zipfile
def load(p):
    with zipfile.ZipFile(p) as zf:
        return json.loads(zf.read("meta.json").decode("utf-8"))
full, inferred, names = [load(p) for p in sys.argv[1:4]]
def by_expno(meta):
    return dict((e["expno"], e) for e in meta["experiments"])
f, i, n = by_expno(full), by_expno(inferred), by_expno(names)
assert set(f) == set(i) == set(n) and len(f) == 43
for x in f:
    assert f[x]["role"] == i[x]["role"] == n[x]["role"], (x, f[x]["role"],
                                                          i[x]["role"],
                                                          n[x]["role"])
    assert f[x].get("tuning") == i[x].get("tuning"), x
    if f[x]["role"] == "noise_tune":
        assert n[x]["tuning"] == {"setting_index": f[x]["tuning"]["setting_index"]}, n[x]
    else:
        assert "tuning" not in n[x], x
lad_f, lad_i = full["calibration"]["rg_ladder"], inferred["calibration"]["rg_ladder"]
assert [r["expno"] for r in lad_i] == [r["expno"] for r in lad_f]
assert all(abs(a["rg"] - b["rg"]) < 1e-9 and a["tip_deg"] == b["tip_deg"]
           for a, b in zip(lad_f, lad_i)), (lad_f[:2], lad_i[:2])
assert lad_i == names["calibration"]["rg_ladder"]
print("role-inference assertions: all OK")
EOF

# schema-invalid tuning objects must be rejected by the packer with a
# message naming the experiment and the key
python3 - "$EXT" <<'EOF'
import copy, json, os, sys
p = os.path.join(sys.argv[1], "answers_packer.json")
with open(p) as fh:
    answers = json.load(fh)
first = [a for a in answers["experiments"] if a["expno"] == 2000][0]
bad = {
    "answers_bad_missing.json": {"label": "no index"},
    "answers_bad_unknown.json": {"setting_index": 0, "turns": 2},
    "answers_bad_type.json": {"setting_index": "0"},
}
for name, tuning in bad.items():
    a = copy.deepcopy(answers)
    [x for x in a["experiments"] if x["expno"] == 2000][0]["tuning"] = tuning
    with open(os.path.join(sys.argv[1], name), "w") as fh:
        json.dump(a, fh, indent=2)
EOF
for case in "missing|experiment 2000: tuning.setting_index missing" \
            "unknown|experiment 2000: tuning has unknown key(s) \['turns'\]" \
            "type|experiment 2000: tuning.setting_index must be an integer >= 0, got '0'"; do
    NAME="${case%%|*}"
    EXPECT="${case#*|}"
    if (cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$EXT" \
            --answers "$EXT/answers_bad_$NAME.json" --vendor agilent \
            --out-dir "$WORK/ext" >/dev/null 2>"$WORK/pack_bad_$NAME.log"); then
        echo "FAIL: a tuning object with a $NAME setting_index problem was packed" >&2
        exit 1
    fi
    cat "$WORK/pack_bad_$NAME.log" >&2
    if ! grep -q "$EXPECT" "$WORK/pack_bad_$NAME.log"; then
        echo "FAIL: rejection of the $NAME tuning object must say: $EXPECT" >&2
        exit 1
    fi
done

# a noise_tune save without an index and without a tuning entry: packs,
# with a WARN; an answers.json role contradicting the save name wins,
# with a WARN
NOIDX="$WORK/noidx/$(basename "$EXT")"
mkdir -p "$WORK/noidx"
cp -R "$EXT" "$NOIDX"
mv "$NOIDX/2000_sn_tune_0.fid" "$NOIDX/2000_sn_tune.fid"
python3 - "$NOIDX" <<'EOF'
import json, os, sys
p = os.path.join(sys.argv[1], "answers_names_only.json")
with open(p) as fh:
    answers = json.load(fh)
answers["experiments"] = [{"expno": 12, "role": "reference_open"}]
with open(os.path.join(sys.argv[1], "answers_override.json"), "w") as fh:
    json.dump(answers, fh, indent=2)
EOF
BUNDLE11=$(cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$NOIDX" \
    --answers "$NOIDX/answers_override.json" --vendor agilent \
    --out-dir "$WORK/noidx" 2>"$WORK/pack11.log" | tail -1)
cat "$WORK/pack11.log" >&2
if ! grep -q "experiment 2000: role noise_tune without a 'tuning' object" "$WORK/pack11.log" \
        || ! grep -q "experiment 12: answers.json role 'reference_open' overrides the role 'noise' implied by the save name" "$WORK/pack11.log"; then
    echo "FAIL: expected the missing-tuning WARN for expno 2000 and the role-override WARN for expno 12" >&2
    exit 1
fi
python3 "$REPO/uploader/upload_bundle.py" "$BUNDLE11" --selftest
python3 - "$BUNDLE11" <<'EOF'
import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf:
    meta = json.loads(zf.read("meta.json").decode("utf-8"))
by = dict((e["expno"], e) for e in meta["experiments"])
assert by[2000]["role"] == "noise_tune" and "tuning" not in by[2000], by[2000]
assert by[2001]["tuning"] == {"setting_index": 0}, by[2001]
assert by[12]["role"] == "reference_open", by[12]
print("WARN-path assertions: all OK")
EOF

# the report's own reading of the linear fixture (needs numpy; the
# chain itself is stdlib-only, so this step is skipped without it)
if python3 -c "import numpy" 2>/dev/null; then
    python3 - "$EXT" <<'EOF'
import json, os, sys
p = os.path.join(sys.argv[1], "answers_packer.json")
with open(p) as fh:
    answers = json.load(fh)
answers["run_mode"] = "synthetic-injection"   # the report withholds science numbers from desktest
with open(os.path.join(sys.argv[1], "answers_injection.json"), "w") as fh:
    json.dump(answers, fh, indent=2)
EOF
    BUNDLE12=$(cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$EXT" \
        --answers "$EXT/answers_injection.json" --vendor agilent \
        --out-dir "$WORK/ext" 2>"$WORK/pack12.log" | tail -1)
    python3 "$REPO/analysis/facility_report.py" "$BUNDLE12" --out "$WORK/ext/report" >/dev/null 2>"$WORK/report12.log" \
        || { cat "$WORK/report12.log" >&2; echo "FAIL: facility_report failed on the extended session" >&2; exit 1; }
    python3 - "$WORK/ext/report/report.json" <<'EOF'
import json, sys
with open(sys.argv[1]) as fh:
    sci = json.load(fh)["science"]
lad = sci["rg_ladder"]
assert lad.get("model") == "drift_compression", lad.get("model")
assert lad.get("time_order") == "randomized", lad.get("time_order")
assert lad["n_levels"] == 9 and lad["n_visits"] == 27, (lad["n_levels"], lad["n_visits"])
env = lad["max_abs_fractional_deviation"]
assert env < 0.01, ("a linear, drift-free fixture read a %.2f%% amplitude "
                    "compression envelope" % (100 * env),
                    [(lv["rg_db"], lv["compression"]) for lv in lad["levels"]])
assert abs(lad["drift"]["significance"]) < 3.0, lad["drift"]
fas = sci["frequency_axis_sign"]
assert fas.get("verified") is True and fas.get("sign") == 1, fas
assert fas.get("basis") == "carrier_displacement_calibration", fas.get("basis")
assert fas["calibration"]["verdict"] == "physical", fas["calibration"]
# the lock-referencing cross-check reads the generator's reffrq/rfl/rfp
# and agrees with the signcal pair: physical, +1, identity to the hertz
rc = fas["referencing_check"]
assert rc["verdict"] == "physical" and rc["sign"] == 1, rc
assert abs(rc["identity_residual_hz"]) < 2.0, rc["identity_residual_hz"]
assert abs(rc["predicted_offset_hz"] - 537.5) < 1.0, rc["predicted_offset_hz"]
assert abs(rc["measured_offset_hz"] - 537.5) < 5.0, rc["measured_offset_hz"]
assert rc["agreement"].startswith("agrees"), rc["agreement"]
for k in ("reffrq_mhz", "rfl_hz", "rfp_hz", "sw_hz", "sfrq_mhz", "tolerance_hz"):
    assert isinstance(rc.get(k), float), (k, rc.get(k))
assert any(q["check"] == "frequency-axis sign" and q["level"] == "OK" for q in sci["qa_flags"]), sci["qa_flags"]
print("report assertions: all OK (envelope %.2f%% in amplitude, sign +1, "
      "referencing check %s)" % (100 * env, rc["verdict"]))
EOF
else
    echo "NOTE: numpy not importable -- report-level envelope check skipped" >&2
fi

# an answered ladder is cross-checked against the session: the
# pre-v0.7.1 template ladder (expnos 10/14/15/16) against the extended
# session, and one rung whose rg is the requested 13.3 dB where the
# console stored 15 dB
python3 - "$EXT" <<'EOF'
import copy, json, os, sys
p = os.path.join(sys.argv[1], "answers_packer.json")
with open(p) as fh:
    answers = json.load(fh)
stale = copy.deepcopy(answers)
stale["calibration"]["rg_ladder"] = [
    {"expno": 10, "rg": 1.0, "tip_deg": 1.0}, {"expno": 14, "rg": 10.0, "tip_deg": 1.0},
    {"expno": 15, "rg": 100.0, "tip_deg": 1.0}, {"expno": 16, "rg": 1000.0, "tip_deg": 1.0}]
with open(os.path.join(sys.argv[1], "answers_ladder_stale.json"), "w") as fh:
    json.dump(stale, fh, indent=2)
mism = copy.deepcopy(answers)
rung = [r for r in mism["calibration"]["rg_ladder"] if abs(r["rg"] - 10.0 ** 0.75) < 1e-6][0]
rung["rg"] = 4.62381     # 13.3 dB, the SIU session-2 request
with open(os.path.join(sys.argv[1], "answers_ladder_mismatch.json"), "w") as fh:
    json.dump(mism, fh, indent=2)
print(rung["expno"])
EOF
BUNDLE13=$(cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$EXT" \
    --answers "$EXT/answers_ladder_stale.json" --vendor agilent \
    --out-dir "$WORK/ext" 2>"$WORK/pack13.log" | tail -1)
cat "$WORK/pack13.log" >&2
N_STALE=$(grep -c "calibration.rg_ladder names expno .* but the data directory has no such experiment" "$WORK/pack13.log" || true)
if [ "$N_STALE" != "4" ]; then
    echo "FAIL: expected one WARN per template rung the session lacks (4), got $N_STALE" >&2
    exit 1
fi
python3 "$REPO/uploader/upload_bundle.py" "$BUNDLE13" --selftest
BUNDLE14=$(cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$EXT" \
    --answers "$EXT/answers_ladder_mismatch.json" --vendor agilent \
    --out-dir "$WORK/ext" 2>"$WORK/pack14.log" | tail -1)
cat "$WORK/pack14.log" >&2
if ! grep -q "calibration.rg_ladder expno [0-9]*: answered rg 4.62381 (13.3 dB) but the vendor files store rg 5.62341 (15.0 dB)" "$WORK/pack14.log" \
        || [ "$(grep -c 'calibration.rg_ladder' "$WORK/pack14.log")" != "1" ]; then
    echo "FAIL: expected exactly one rg-mismatch WARN for the 13.3 dB rung" >&2
    exit 1
fi
python3 - "$BUNDLE13" "$BUNDLE14" "$EXT" <<'EOF'
import json, os, sys, zipfile
def load(p):
    with zipfile.ZipFile(p) as zf:
        return json.loads(zf.read("meta.json").decode("utf-8"))
stale, mism = load(sys.argv[1]), load(sys.argv[2])
assert [r["expno"] for r in stale["calibration"]["rg_ladder"]] == [10, 14, 15, 16]
with open(os.path.join(sys.argv[3], "answers_ladder_mismatch.json")) as fh:
    want = json.load(fh)["calibration"]["rg_ladder"]
assert mism["calibration"]["rg_ladder"] == want, "mismatching ladder not recorded verbatim"
print("ladder cross-check assertions: all OK")
EOF

# save-name rule: whatever follows the role word is free unless it runs
# straight into letters
NAMES="$WORK/names/$(basename "$SESSION")"
mkdir -p "$WORK/names"
cp -R "$SESSION" "$NAMES"
mv "$NAMES/17_sn_noise_1.fid" "$NAMES/17_sn_noise2.fid"
mv "$NAMES/18_sn_noise_2.fid" "$NAMES/18_sn_noise-1.fid"
mv "$NAMES/14_sn_ladder_b.fid" "$NAMES/14_SN_LADDER_20DB.fid"
python3 - "$NAMES" "$REPO" <<'EOF'
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location(
    "pk", os.path.join(sys.argv[2], "packer", "pack_bundle.py"))
pk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pk)
cases = {
    "12_sn_noise.fid": ("noise", None), "12_sn_noise2.fid": ("noise", None),
    "17_sn_noise-1": ("noise", None), "12_sn_noise_b.fid": ("noise", None),
    "12_sn_noisetest.fid": (None, None), "12_sn_noise_tune": ("noise", None),
    "2010_sn_tune_1.fid": ("noise_tune", 1), "2010_sn_tune_12": ("noise_tune", 12),
    "2000_sn_tune.fid": ("noise_tune", None), "2010_sn_tune1": ("noise_tune", None),
    "1004_sn_ladder_15db.fid": ("rg_ladder", None), "11_SN_REF_OPEN.fid": ("reference_open", None),
    "13_sn_ref_close": ("reference_close", None), "3000_sn_signcal.fid": ("sweep_signcal", None),
    "01_sn_setup": ("setup", None), "water_ref.fid": (None, None),
}
for name, want in cases.items():
    got = pk.infer_role_from_name(name)
    assert got == want, (name, got, want)
p = os.path.join(sys.argv[1], "answers_packer.json")
with open(p) as fh:
    answers = json.load(fh)
answers["experiments"] = []
del answers["calibration"]["rg_ladder"]
with open(os.path.join(sys.argv[1], "answers_names.json"), "w") as fh:
    json.dump(answers, fh, indent=2)
EOF
BUNDLE15=$(cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$NAMES" \
    --answers "$NAMES/answers_names.json" --vendor agilent \
    --out-dir "$WORK/names" 2>"$WORK/pack15.log" | tail -1)
cat "$WORK/pack15.log" >&2
python3 "$REPO/uploader/upload_bundle.py" "$BUNDLE15" --selftest
python3 - "$BUNDLE15" <<'EOF'
import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as zf:
    meta = json.loads(zf.read("meta.json").decode("utf-8"))
roles = dict((e["expno"], e["role"]) for e in meta["experiments"])
assert roles == {10: "rg_ladder", 14: "rg_ladder", 15: "rg_ladder", 16: "rg_ladder",
                 11: "reference_open", 12: "noise", 17: "noise", 18: "noise",
                 13: "reference_close"}, roles
assert [r["expno"] for r in meta["calibration"]["rg_ladder"]] == [10, 14, 15, 16]
print("save-name assertions: all OK")
EOF
mv "$NAMES/17_sn_noise2.fid" "$NAMES/17_sn_noisetest.fid"
if (cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$NAMES" \
        --answers "$NAMES/answers_names.json" --vendor agilent \
        --out-dir "$WORK/names" >/dev/null 2>"$WORK/pack16.log"); then
    echo "FAIL: 17_sn_noisetest must not be read as a noise block" >&2
    exit 1
fi
if ! grep -q "experiment 17: no valid role" "$WORK/pack16.log"; then
    cat "$WORK/pack16.log" >&2
    echo "FAIL: the unrecognised save name must abort naming experiment 17" >&2
    exit 1
fi

# non-finite tuning readings: valid JSON that Python reads as inf/nan
# must be rejected before it can reach meta.json
python3 - "$EXT" <<'EOF'
import json, os, sys
p = os.path.join(sys.argv[1], "answers_packer.json")
with open(p) as fh:
    text = fh.read()
answers = json.loads(text)
first = [a for a in answers["experiments"] if a["expno"] == 2000][0]
first["tuning"]["tune_reading"] = "@@INF@@"
with open(os.path.join(sys.argv[1], "answers_bad_inf.json"), "w") as fh:
    fh.write(json.dumps(answers, indent=2).replace('"@@INF@@"', "1e400"))
first["tuning"]["tune_reading"] = None
first["tuning"]["match_reading"] = "@@NAN@@"
with open(os.path.join(sys.argv[1], "answers_bad_nan.json"), "w") as fh:
    fh.write(json.dumps(answers, indent=2).replace('"@@NAN@@"', "NaN"))
EOF
for case in "inf|experiment 2000: tuning.tune_reading must be a finite number or null, got inf" \
            "nan|experiment 2000: tuning.match_reading must be a finite number or null, got nan"; do
    NAME="${case%%|*}"
    EXPECT="${case#*|}"
    if (cd "$WORK" && python3 "$REPO/packer/pack_bundle.py" "$EXT" \
            --answers "$EXT/answers_bad_$NAME.json" --vendor agilent \
            --out-dir "$WORK/ext" >/dev/null 2>"$WORK/pack_bad_$NAME.log"); then
        echo "FAIL: a non-finite ($NAME) tuning reading was packed" >&2
        exit 1
    fi
    cat "$WORK/pack_bad_$NAME.log" >&2
    if ! grep -q "$EXPECT" "$WORK/pack_bad_$NAME.log"; then
        echo "FAIL: rejection of the $NAME reading must say: $EXPECT" >&2
        exit 1
    fi
done

echo "AGILENT CHAIN TEST: PASS" >&2
