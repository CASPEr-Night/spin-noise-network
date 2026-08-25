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
# and finishes with python3 testing/static_check.py.
# Exit code 0 iff every step passed.
# ============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TESTING="$REPO/testing"

command -v jython >/dev/null || { echo "ERROR: jython not on PATH"; exit 2; }
command -v python3 >/dev/null || { echo "ERROR: python3 not on PATH"; exit 2; }

for MODE in simulate desktest; do
    WORK="$(mktemp -d "${TMPDIR:-/tmp}/spin_noise_harness_${MODE}.XXXXXX")"
    LOGF="$WORK/harness_${MODE}.log"
    echo ""
    echo "=== Jython harness: $MODE (workdir $WORK) ==="
    HARNESS_WORKDIR="$WORK" jython -Dpython.path="$TESTING" \
        "$TESTING/jython_entry.py" "$MODE" 2>&1 | tee "$LOGF"

    BUNDLE="$(grep '^BUNDLE: ' "$LOGF" | tail -1 | sed 's/^BUNDLE: //')"
    if [ -z "$BUNDLE" ] || [ ! -f "$BUNDLE" ]; then
        echo "ERROR: $MODE run produced no bundle zip"
        exit 1
    fi
    echo ""
    echo "--- uploader --selftest on the $MODE bundle ---"
    python3 "$REPO/uploader/upload_bundle.py" "$BUNDLE" --selftest
done

echo ""
echo "--- static checks ---"
python3 "$TESTING/static_check.py"

echo ""
echo "JYTHON HARNESS: ALL PASS (simulate + desktest + selftest + static)"
