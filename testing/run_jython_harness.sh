#!/bin/bash
# ============================================================================
# testing/run_jython_harness.sh -- Tier -1: execute topspin/spin_noise_run.py
# END TO END under a real Jython 2.7 interpreter with a stubbed TopSpin API
# (no TopSpin installation needed).
#
#   ./testing/run_jython_harness.sh
#
# Requires: jython (2.7.x) and python3 on PATH.
#
# For each mode (simulate, desktest) it:
#   1. runs jython_entry.py, which execfile()'s the REAL script unmodified
#      with topspin_stub.py registered as the TopCmds module -- so the
#      script runs its IN_TOPSPIN=1 paths: real java.util.zip bundling,
#      real java.security SHA-256, real jarray buffers;
#   2. validates the produced bundle with the repository's own validator:
#      python3 uploader/upload_bundle.py <bundle> --selftest
#   3. generates the facility report on the bundle and asserts the
#      clock-audit fit recovered the stub's injected 3e-7 fractional
#      offset within its stated 1-sigma uncertainty, with the audit
#      marked conclusive (REALISM check -- the ~1 h virtual session
#      cannot resolve 3e-7, so this is a coverage assertion).
# Then a POWERED clock-recovery matrix on synthetic physics bundles
# (testing/make_physics_bundle.py), where coverage alone would not catch
# a fit that always returns 0:
#   * --clock-offset 1e-3 (~9 sigma vs the ~1.1e-4 fit precision):
#     require recovery within 3 sigma AND a >= 5-sigma detection;
#   * --clock-offset 0 (null): require a fit consistent with zero (3 sigma).
# And finishes with python3 testing/static_check.py.
# Exit code 0 iff every step passed.
#
# The report/recovery steps need numpy + matplotlib in python3
# (analysis/facility_report.py's only dependencies).
# ============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TESTING="$REPO/testing"

command -v jython >/dev/null || { echo "ERROR: jython not on PATH"; exit 2; }
command -v python3 >/dev/null || { echo "ERROR: python3 not on PATH"; exit 2; }

# Three end-to-end variants: the two plain modes, plus desktest with the
# optional rdopt + sweep features on (structure/dialog/meta coverage --
# mock modes exercise the flow, not the physics).
for RUN in "simulate" "desktest" "desktest rdopt sweep autostep"; do
    set -- $RUN
    MODE="$1"; shift
    FEATURES="$*"
    TAG="$(echo "$RUN" | tr ' ' '_')"
    WORK="$(mktemp -d "${TMPDIR:-/tmp}/spin_noise_harness_${TAG}.XXXXXX")"
    LOGF="$WORK/harness_${TAG}.log"
    echo ""
    echo "=== Jython harness: $RUN (workdir $WORK) ==="
    HARNESS_WORKDIR="$WORK" jython -Dpython.path="$TESTING" \
        "$TESTING/jython_entry.py" $RUN 2>&1 | tee "$LOGF"

    BUNDLE="$(grep '^BUNDLE: ' "$LOGF" | tail -1 | sed 's/^BUNDLE: //')"
    if [ -z "$BUNDLE" ] || [ ! -f "$BUNDLE" ]; then
        echo "ERROR: $MODE run produced no bundle zip"
        exit 1
    fi
    echo ""
    echo "--- uploader --selftest on the $MODE bundle ---"
    python3 "$REPO/uploader/upload_bundle.py" "$BUNDLE" --selftest

    # Clock-audit REALISM check: the stub's virtual clock carries a
    # deliberate injected fractional offset (printed by jython_entry.py);
    # the offline fit must recover it within its stated uncertainty.
    INJECTED="$(grep '^INJECTED_CLOCK_OFFSET: ' "$LOGF" | tail -1 \
        | sed 's/^INJECTED_CLOCK_OFFSET: //')"
    if [ -z "$INJECTED" ]; then
        echo "ERROR: $MODE log carries no INJECTED_CLOCK_OFFSET line"
        exit 1
    fi
    echo ""
    echo "--- facility report + clock-offset recovery ($MODE bundle) ---"
    python3 "$REPO/analysis/facility_report.py" "$BUNDLE" \
        --out "$WORK/report"
    python3 "$TESTING/check_clock_recovery.py" "$WORK/report/report.json" \
        --injected "$INJECTED" --within-nsigma 1 --require-conclusive

    # Packer round-trip on the desktest bundle (the real-Jython product):
    # unpack its expno tree, re-pack with packer/pack_bundle.py's Bruker
    # reader, require bit-identical data payload + a valid schema-2.0
    # meta.json.  See testing/test_pack_roundtrip.py.  Plain desktest
    # only: the feature variant's expno layout is a schema-1.2 script
    # product, not a packer input contract.
    if [ "$MODE" = "desktest" ] && [ -z "$FEATURES" ]; then
        echo ""
        echo "--- packer round-trip (desktest bundle) ---"
        python3 "$TESTING/test_pack_roundtrip.py" "$BUNDLE"
    fi
done

# Powered clock-recovery matrix: synthetic physics bundles with (a) an
# offset ~9 sigma above the fit precision -- must be RESOLVED, not just
# covered -- and (b) a zero-offset null. See the header for why the
# realism check alone has no statistical power against a dead fit.
# Both cases also require the acqus-DE expectation refinement to have
# engaged on the 3 long usable blocks (the 4 short RG-ladder blocks are
# excluded by the 5% wall/OCXO consistency gate: the fixture's realistic
# 0.18 s per-block overhead is ~5.7% of a 3.2 s ladder block -- true on
# real hardware too, where the offset information lives in long blocks).
echo ""
echo "=== Powered clock-offset recovery (make_physics_bundle) ==="
CLOCKWORK="$(mktemp -d "${TMPDIR:-/tmp}/spin_noise_harness_clock.XXXXXX")"
for CASE in "1e-3 --within-nsigma 3 --detect-nsigma 5 --expect-refined 3" \
            "0 --within-nsigma 3 --expect-refined 3"; do
    set -- $CASE
    OFFSET="$1"; shift
    echo ""
    echo "--- injected offset $OFFSET ---"
    PBUNDLE="$(python3 "$TESTING/make_physics_bundle.py" --feature none \
        --clock-offset "$OFFSET" --out-dir "$CLOCKWORK")"
    python3 "$REPO/analysis/facility_report.py" "$PBUNDLE" \
        --out "$CLOCKWORK/report_$OFFSET"
    python3 "$TESTING/check_clock_recovery.py" \
        "$CLOCKWORK/report_$OFFSET/report.json" --injected "$OFFSET" "$@"
done

# Discrimination case: a deliberately huge DE (20 ms; stock is 6.5 us)
# on top of the fixture's structural shortfalls (the zg2d references'
# second d1 per row, which the recorded expectations omit). The
# headline (pulse-program-derived) fit must still recover the zero
# injected offset, while the recorded-model comparison fit must sit
# > 5 sigma away -- proving the refinement engaged AND did real work,
# not merely that both fits agree when the shortfalls are negligible.
echo ""
echo "--- DE discrimination (de_us = 20000, injected offset 0) ---"
PBUNDLE="$(python3 "$TESTING/make_physics_bundle.py" --feature none \
    --clock-offset 0 --de-us 20000 --out-dir "$CLOCKWORK")"
python3 "$REPO/analysis/facility_report.py" "$PBUNDLE" \
    --out "$CLOCKWORK/report_de"
python3 "$TESTING/check_clock_recovery.py" \
    "$CLOCKWORK/report_de/report.json" --injected 0 --within-nsigma 3 \
    --expect-refined 3 --recorded-model-biased-nsigma 5

# Field-sweep science path: fabricated sweep bundle -- per-step fits at
# the measured (shifted) positions, unverified steps listed-not-fitted,
# headline = measured step nearest baseline. See test_sweep_report.py.
echo ""
echo "--- field-sweep report path (test_sweep_report) ---"
python3 "$TESTING/test_sweep_report.py" --out-dir "$CLOCKWORK/sweepreport"

echo ""
echo "--- static checks ---"
python3 "$TESTING/static_check.py"

echo ""
echo "JYTHON HARNESS: ALL PASS (simulate + desktest + selftest"
echo "                + packer round-trip"
echo "                + clock-offset recovery: realism, powered, null,"
echo "                  DE discrimination"
echo "                + static)"
