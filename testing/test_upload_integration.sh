#!/usr/bin/env bash
# test_upload_integration.sh -- end-to-end test of the ingest path against a
# REAL local Worker (wrangler dev, local mode: workerd + local R2 simulation).
#
#   ./testing/test_upload_integration.sh
#
# What it proves, in order:
#   1. single-shot path : small bundle -> POST /ingest -> RECEIPT
#   2. chunked path     : ~160 MiB bundle -> create/part/complete -> RECEIPT
#      including RESUME : the uploader is kill -9'd after ~2 parts, rerun,
#      and must continue from the checkpoint (never re-sending done parts)
#   3. /list shows both objects; GET /object returns the large bundle
#      byte-exactly (cmp against the original)
#   4. --abort cleans up a deliberately orphaned multipart upload
#   5. unknown upload_id on /upload/part answers 404 JSON
#   6. the facility-registry suite (testing/test_registry.sh: its own
#      wrangler dev on port 8788 with both tokens; POST /registry, the
#      idempotent re-POST, 401/503 paths, /registry/list, /list + /stats
#      exclusion, and analysis/registry_report.py against the live endpoint)
#
# Requirements: node >= 18 (for npx wrangler), python3, curl. No Cloudflare
# account or login needed -- everything runs locally. The test token is
# generated fresh per run and never leaves this machine.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${SPIN_NOISE_TEST_PORT:-8787}"
BASE="http://localhost:${PORT}"
WORK="${REPO}/testing/synthetic_bundles/integration_$(date +%Y%m%d_%H%M%S)"
DEVVARS="${REPO}/server/.dev.vars"
TOKEN="test-only-$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
BIG_MIB="${SPIN_NOISE_TEST_BIG_MIB:-160}"   # 160 MiB -> 4 parts of <= 50 MiB

PASS=0
step()  { printf '\n=== %s\n' "$*"; }
ok()    { printf 'OK   : %s\n' "$*"; }
fail()  { printf 'FAIL : %s\n' "$*"; exit 1; }

WRANGLER_PID=""
DEVVARS_BACKUP=""
cleanup() {
  set +e
  if [ -n "${WRANGLER_PID}" ]; then
    kill "${WRANGLER_PID}" 2>/dev/null
    sleep 1
    pkill -P "${WRANGLER_PID}" 2>/dev/null
  fi
  # workerd occasionally lingers on the port
  lsof -ti "tcp:${PORT}" 2>/dev/null | xargs kill 2>/dev/null
  if [ -n "${DEVVARS_BACKUP}" ]; then
    mv "${DEVVARS_BACKUP}" "${DEVVARS}"
  else
    rm -f "${DEVVARS}"
  fi
  if [ "${PASS}" = "1" ]; then
    rm -rf "${WORK}"
  else
    echo "artifacts kept for inspection: ${WORK}"
  fi
}
trap cleanup EXIT

command -v node >/dev/null || fail "node not found (needed for npx wrangler)"
command -v curl >/dev/null || fail "curl not found"
if lsof -ti "tcp:${PORT}" >/dev/null 2>&1; then
  fail "port ${PORT} already in use (set SPIN_NOISE_TEST_PORT to override)"
fi

mkdir -p "${WORK}"

start_server() {
  ( cd "${REPO}/server" && \
    WRANGLER_SEND_METRICS=false CI=1 \
    npx wrangler dev --local --port "${PORT}" \
        --persist-to "${WORK}/wrangler-state" \
        >> "${WORK}/wrangler.log" 2>&1 ) &
  WRANGLER_PID=$!
  disown "${WRANGLER_PID}" 2>/dev/null || true  # no job-control noise on kill
  for i in $(seq 1 120); do
    if curl -sf "${BASE}/health" >/dev/null 2>&1; then return 0; fi
    if ! kill -0 "${WRANGLER_PID}" 2>/dev/null; then
      tail -20 "${WORK}/wrangler.log"; fail "wrangler dev exited early"
    fi
    sleep 1
  done
  tail -20 "${WORK}/wrangler.log"; fail "server never became healthy"
}

# wrangler dev (dev mode only) dies -- sometimes a few seconds later -- with
# "Uncaught Error: Network connection lost" when a client is kill -9'd
# mid-stream; production Workers isolate requests and shrug this off. The
# local R2 state (including in-progress multipart uploads) lives in
# --persist-to, so after each deliberate kill we restart the dev server
# unconditionally and continue -- which doubles as proof that a resumed
# upload survives a server restart, not just a client crash.
restart_server() {
  kill "${WRANGLER_PID}" 2>/dev/null || true
  sleep 1
  pkill -P "${WRANGLER_PID}" 2>/dev/null || true
  lsof -ti "tcp:${PORT}" 2>/dev/null | xargs kill 2>/dev/null || true
  for i in $(seq 1 30); do
    lsof -ti "tcp:${PORT}" >/dev/null 2>&1 || break
    sleep 1
  done
  start_server
}

step "start wrangler dev --local (workerd + local R2) on port ${PORT}"
if [ -f "${DEVVARS}" ]; then
  DEVVARS_BACKUP="${DEVVARS}.integration-backup"
  mv "${DEVVARS}" "${DEVVARS_BACKUP}"
fi
printf 'INGEST_TOKEN=%s\n' "${TOKEN}" > "${DEVVARS}"

start_server
ok "server healthy: $(curl -s "${BASE}/health" | tr -d '\n ')"

cat > "${WORK}/config.json" <<EOF
{
  "endpoint_url": "${BASE}/ingest",
  "token": "${TOKEN}",
  "facility_slug": "ci-selftest",
  "maintainer_email": "jwbquantum@gmail.com"
}
EOF

UPLOADER="${REPO}/uploader/upload_bundle.py"
CFG="--config ${WORK}/config.json"

step "generate test bundles (small + ${BIG_MIB} MiB random)"
SMALL_ZIP="$(python3 "${REPO}/testing/make_synthetic_bundle.py" --out-dir "${WORK}" 2>/dev/null)"
sleep 1  # distinct timestamp -> distinct bundle name
BIG_ZIP="$(python3 "${REPO}/testing/make_synthetic_bundle.py" --out-dir "${WORK}" --ser-mib "${BIG_MIB}" 2>/dev/null)"
ok "small: $(basename "${SMALL_ZIP}") ($(du -m "${SMALL_ZIP}" | cut -f1) MB)"
ok "big  : $(basename "${BIG_ZIP}") ($(du -m "${BIG_ZIP}" | cut -f1) MB)"

step "1. single-shot path (<= 50 MiB -> POST /ingest)"
echo "--- doctor preflight against the mock server ---"
python3 "${UPLOADER}" --doctor ${CFG} || fail "doctor reported problems against the mock server"

OUT="$(python3 "${UPLOADER}" "${SMALL_ZIP}" ${CFG} --allow-test-bundle)" || { echo "${OUT}"; fail "small upload failed"; }
echo "${OUT}" | grep -q "UPLOAD OK"  || { echo "${OUT}"; fail "no UPLOAD OK for small bundle"; }
echo "${OUT}" | grep -q "RECEIPT: "  || { echo "${OUT}"; fail "no RECEIPT for small bundle"; }
ok "$(echo "${OUT}" | grep 'RECEIPT: ')"

step "2. chunked path: start, kill -9 after ~2 parts, resume"
STATE="${BIG_ZIP}.upload-state.json"
rm -f "${STATE}"
python3 "${UPLOADER}" "${BIG_ZIP}" ${CFG} --allow-test-bundle > "${WORK}/big_attempt1.log" 2>&1 &
UP_PID=$!
# Poll (10 ms) for the part-2 checkpoint, then kill -9 mid-transfer.
KILLED=0
for i in $(seq 1 6000); do
  if [ -f "${STATE}" ] && grep -q '"2":' "${STATE}" 2>/dev/null; then
    kill -9 "${UP_PID}" 2>/dev/null && KILLED=1
    break
  fi
  if ! kill -0 "${UP_PID}" 2>/dev/null; then break; fi
  sleep 0.01
done
wait "${UP_PID}" 2>/dev/null || true
[ "${KILLED}" = "1" ] || { cat "${WORK}/big_attempt1.log"; fail "could not kill uploader mid-transfer (finished too fast?)"; }
[ -f "${STATE}" ] || fail "no resume state file after kill -9"
DONE_PARTS="$(python3 -c "import json;print(len(json.load(open('${STATE}'))['parts']))")"
ok "killed -9 with ${DONE_PARTS} part(s) checkpointed in $(basename "${STATE}")"
restart_server
ok "dev server restarted against the same persisted R2 state"

OUT="$(python3 "${UPLOADER}" "${BIG_ZIP}" ${CFG} --allow-test-bundle)" || { echo "${OUT}"; fail "resumed upload failed"; }
echo "${OUT}" | grep -q "RESUME: found" || { echo "${OUT}"; fail "rerun did not resume from state file"; }
echo "${OUT}" | grep -q "UPLOAD OK"     || { echo "${OUT}"; fail "no UPLOAD OK after resume"; }
echo "${OUT}" | grep -q "RECEIPT: "     || { echo "${OUT}"; fail "no RECEIPT after resume"; }
for p in $(seq 1 "${DONE_PARTS}"); do
  if echo "${OUT}" | grep -q "part ${p}/"; then
    echo "${OUT}"; fail "resume RE-uploaded already-done part ${p}"
  fi
done
[ ! -f "${STATE}" ] || fail "state file not deleted after successful upload"
ok "resumed at part $((DONE_PARTS + 1)), completed, state file removed"
ok "$(echo "${OUT}" | grep 'RESUME: found')"

step "3. /list shows both objects; /object returns the big one byte-exactly"
LIST="$(curl -s -H "Authorization: Bearer ${TOKEN}" "${BASE}/list?limit=100")"
echo "${LIST}" | grep -q "$(basename "${SMALL_ZIP}")" || { echo "${LIST}"; fail "small bundle missing from /list"; }
echo "${LIST}" | grep -q "$(basename "${BIG_ZIP}")"   || { echo "${LIST}"; fail "big bundle missing from /list"; }
ok "/list contains both bundles"

curl -sf -H "Authorization: Bearer ${TOKEN}" \
  "${BASE}/object?key=$(basename "${BIG_ZIP}")" -o "${WORK}/roundtrip.zip" \
  || fail "GET /object failed for the big bundle"
cmp "${BIG_ZIP}" "${WORK}/roundtrip.zip" || fail "downloaded big bundle differs from original"
ok "GET /object roundtrip is byte-exact ($(du -m "${WORK}/roundtrip.zip" | cut -f1) MB; sha256 $(shasum -a 256 "${WORK}/roundtrip.zip" | cut -c1-12)...)"

step "4. --abort cleans up a deliberately orphaned upload"
sleep 1
ORPHAN_ZIP="${WORK}/$(basename "${BIG_ZIP}" .zip | sed 's/_[0-9a-f]\{4\}$//')_ab0a.zip"
cp "${BIG_ZIP}" "${ORPHAN_ZIP}"
python3 "${UPLOADER}" "${ORPHAN_ZIP}" ${CFG} --allow-test-bundle > "${WORK}/orphan.log" 2>&1 &
UP_PID=$!
OSTATE="${ORPHAN_ZIP}.upload-state.json"
KILLED=0
for i in $(seq 1 6000); do
  if [ -f "${OSTATE}" ] && grep -q '"1":' "${OSTATE}" 2>/dev/null; then
    kill -9 "${UP_PID}" 2>/dev/null && KILLED=1
    break
  fi
  if ! kill -0 "${UP_PID}" 2>/dev/null; then break; fi
  sleep 0.01
done
wait "${UP_PID}" 2>/dev/null || true
[ "${KILLED}" = "1" ] || { cat "${WORK}/orphan.log"; fail "could not orphan an upload"; }
restart_server
OUT="$(python3 "${UPLOADER}" "${ORPHAN_ZIP}" ${CFG} --abort)" || { echo "${OUT}"; fail "--abort failed"; }
echo "${OUT}" | grep -q "aborted on the server" || { echo "${OUT}"; fail "--abort did not confirm server abort"; }
[ ! -f "${OSTATE}" ] || fail "--abort left the state file behind"
LIST="$(curl -s -H "Authorization: Bearer ${TOKEN}" "${BASE}/list?limit=100")"
echo "${LIST}" | grep -q "$(basename "${ORPHAN_ZIP}")" && fail "aborted upload appeared in /list"
ok "orphaned upload aborted; no object stored"

step "5. unknown upload_id -> graceful 404 JSON"
CODE="$(printf 'x' | curl -s -o "${WORK}/bogus.json" -w '%{http_code}' -X PUT \
  -H "Authorization: Bearer ${TOKEN}" -H "Content-Length: 1" \
  --data-binary @- \
  "${BASE}/upload/part?key=$(basename "${BIG_ZIP}")&upload_id=bogus-id-000&part=1&final=1")"
[ "${CODE}" = "404" ] || { cat "${WORK}/bogus.json"; fail "expected 404 for bogus upload_id, got ${CODE}"; }
grep -q '"ok": false' "${WORK}/bogus.json" || fail "404 body is not the expected JSON"
ok "bogus upload_id answered 404 JSON"

step "6. facility-registry suite (testing/test_registry.sh, own server on its own port)"
"${REPO}/testing/test_registry.sh" || fail "registry suite failed"
ok "registry suite passed"

PASS=1
printf '\nALL INTEGRATION TESTS PASSED\n'
