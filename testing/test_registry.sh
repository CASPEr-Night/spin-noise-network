#!/usr/bin/env bash
# test_registry.sh -- end-to-end test of the facility-registry path against a
# REAL local Worker (wrangler dev, local mode: workerd + local R2 simulation).
#
#   ./testing/test_registry.sh          # standalone
#   (also invoked as the final step of test_upload_integration.sh)
#
# What it proves, in order:
#   1. REGISTRY_TOKEN unset  : POST /registry answers 503 with a setup hint
#   2. both tokens set       : POST a synthetic form JSON -> 201 ok + key
#   3. idempotency           : byte-identical re-POST -> ok with the SAME key
#   4. auth                  : wrong token 401, missing token 401, and the
#                              INGEST token does NOT authorize the write path
#   5. /registry/list shows the entry (registry token AND ingest token);
#      /list and /stats do NOT contain registry/ keys
#   6. analysis/registry_report.py renders the table + facilities JSON from
#      the local endpoint (best-effort coverage line included when numpy is
#      present -- the synthetic facility's city resolves in the gazetteer)
#
# Requirements: node >= 18 (for npx wrangler), python3, curl. No Cloudflare
# account or login needed -- everything runs locally. Both test tokens are
# generated fresh per run and never leave this machine.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${SPIN_NOISE_REGISTRY_TEST_PORT:-8788}"
BASE="http://localhost:${PORT}"
WORK="${REPO}/testing/synthetic_bundles/registry_$(date +%Y%m%d_%H%M%S)"
DEVVARS="${REPO}/server/.dev.vars"
INGEST_TOKEN="test-only-$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
REGISTRY_TOKEN="test-only-$(python3 -c 'import secrets; print(secrets.token_hex(16))')"

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
  fail "port ${PORT} already in use (set SPIN_NOISE_REGISTRY_TEST_PORT to override)"
fi

mkdir -p "${WORK}"

start_server() {
  ( cd "${REPO}/server" && \
    WRANGLER_SEND_METRICS=false CI=1 \
    npx wrangler dev --local --port "${PORT}" \
        --persist-to "${WORK}/wrangler-state" \
        >> "${WORK}/wrangler.log" 2>&1 ) &
  WRANGLER_PID=$!
  disown "${WRANGLER_PID}" 2>/dev/null || true
  for i in $(seq 1 120); do
    if curl -sf "${BASE}/health" >/dev/null 2>&1; then return 0; fi
    if ! kill -0 "${WRANGLER_PID}" 2>/dev/null; then
      tail -20 "${WORK}/wrangler.log"; fail "wrangler dev exited early"
    fi
    sleep 1
  done
  tail -20 "${WORK}/wrangler.log"; fail "server never became healthy"
}

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

# Small helper: POST a JSON file to /registry; prints the HTTP code.
# (macOS ships bash 3.2, where empty arrays trip set -u — hence the if/else.)
post_registry() {  # $1 = bearer token ('' = no auth header), $2 = json file, $3 = out file
  if [ -n "$1" ]; then
    curl -s -o "$3" -w '%{http_code}' -X POST \
      -H "Authorization: Bearer $1" \
      -H "Content-Type: application/json" --data-binary @"$2" "${BASE}/registry"
  else
    curl -s -o "$3" -w '%{http_code}' -X POST \
      -H "Content-Type: application/json" --data-binary @"$2" "${BASE}/registry"
  fi
}

# The synthetic sign-up (what forms_forwarder.gs would send). Carbondale is
# deliberately a gazetteer city so the report's coverage path exercises.
cat > "${WORK}/signup.json" <<'EOF'
{
  "submitted_at": "2026-08-27T14:02:11.000Z",
  "institution": "Synthetic State University",
  "contact_name": "Test Facility Contact",
  "contact_email": "facility@example.edu",
  "spectrometers": "600 MHz / Avance III HD / TopSpin 3.6.2",
  "probes": "Prodigy BBO, N2-cooled, ATM yes, sample changer no",
  "city": "Carbondale",
  "country": "USA",
  "city_country_raw": "Carbondale, USA",
  "heard_via": "integration test",
  "consent_contact": true,
  "consent_map": true,
  "forwarder": "forms_forwarder.gs"
}
EOF

step "1. REGISTRY_TOKEN unset -> POST /registry answers 503"
if [ -f "${DEVVARS}" ]; then
  DEVVARS_BACKUP="${DEVVARS}.registry-backup"
  mv "${DEVVARS}" "${DEVVARS_BACKUP}"
fi
printf 'INGEST_TOKEN=%s\n' "${INGEST_TOKEN}" > "${DEVVARS}"
start_server
ok "server healthy (INGEST_TOKEN only): $(curl -s "${BASE}/health" | tr -d '\n ')"

CODE="$(post_registry "${REGISTRY_TOKEN}" "${WORK}/signup.json" "${WORK}/unset.json")"
[ "${CODE}" = "503" ] || { cat "${WORK}/unset.json"; fail "expected 503 without REGISTRY_TOKEN, got ${CODE}"; }
grep -q "REGISTRY_TOKEN" "${WORK}/unset.json" || { cat "${WORK}/unset.json"; fail "503 body lacks the setup hint"; }
ok "503 with setup hint while REGISTRY_TOKEN is unset"

step "2. both tokens set -> synthetic sign-up stores (201)"
printf 'INGEST_TOKEN=%s\nREGISTRY_TOKEN=%s\n' "${INGEST_TOKEN}" "${REGISTRY_TOKEN}" > "${DEVVARS}"
restart_server
ok "server restarted with both tokens"

CODE="$(post_registry "${REGISTRY_TOKEN}" "${WORK}/signup.json" "${WORK}/post1.json")"
[ "${CODE}" = "201" ] || { cat "${WORK}/post1.json"; fail "expected 201, got ${CODE}"; }
KEY1="$(python3 -c "import json;print(json.load(open('${WORK}/post1.json'))['key'])")"
case "${KEY1}" in registry/*.json) : ;; *) fail "unexpected key format: ${KEY1}" ;; esac
ok "stored as ${KEY1}"

step "3. byte-identical re-POST -> ok with the SAME key (idempotent)"
CODE="$(post_registry "${REGISTRY_TOKEN}" "${WORK}/signup.json" "${WORK}/post2.json")"
[ "${CODE}" = "200" ] || { cat "${WORK}/post2.json"; fail "expected 200 on duplicate, got ${CODE}"; }
KEY2="$(python3 -c "import json;print(json.load(open('${WORK}/post2.json'))['key'])")"
[ "${KEY1}" = "${KEY2}" ] || fail "duplicate got a NEW key (${KEY2} != ${KEY1})"
python3 -c "import json,sys; d=json.load(open('${WORK}/post2.json')); sys.exit(0 if d.get('duplicate') else 1)" \
  || fail "duplicate response missing the duplicate flag"
ok "re-POST answered the existing key with duplicate=true"

step "4. auth: wrong token 401, missing token 401, ingest token cannot WRITE"
CODE="$(post_registry "wrong-token-000" "${WORK}/signup.json" "${WORK}/wrong.json")"
[ "${CODE}" = "401" ] || fail "wrong token: expected 401, got ${CODE}"
CODE="$(post_registry "" "${WORK}/signup.json" "${WORK}/noauth.json")"
[ "${CODE}" = "401" ] || fail "missing token: expected 401, got ${CODE}"
CODE="$(post_registry "${INGEST_TOKEN}" "${WORK}/signup.json" "${WORK}/ingestwrite.json")"
[ "${CODE}" = "401" ] || fail "ingest token on the write path: expected 401, got ${CODE}"
ok "write path rejects wrong/missing/ingest tokens with 401"

step "5. /registry/list shows the entry; /list and /stats exclude registry/"
LIST_R="$(curl -s -H "Authorization: Bearer ${REGISTRY_TOKEN}" "${BASE}/registry/list")"
echo "${LIST_R}" | grep -q "${KEY1}" || { echo "${LIST_R}"; fail "entry missing from /registry/list (registry token)"; }
echo "${LIST_R}" | grep -q "Synthetic State University" || { echo "${LIST_R}"; fail "submission body missing from /registry/list"; }
LIST_I="$(curl -s -H "Authorization: Bearer ${INGEST_TOKEN}" "${BASE}/registry/list")"
echo "${LIST_I}" | grep -q "${KEY1}" || { echo "${LIST_I}"; fail "/registry/list refused the ingest token (coordinator holds both)"; }
CODE="$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/registry/list")"
[ "${CODE}" = "401" ] || fail "/registry/list without auth: expected 401, got ${CODE}"
ok "/registry/list works with either token, 401 without"

BUNDLE_LIST="$(curl -s -H "Authorization: Bearer ${INGEST_TOKEN}" "${BASE}/list?limit=500")"
echo "${BUNDLE_LIST}" | grep -q "registry/" && { echo "${BUNDLE_LIST}"; fail "/list leaked a registry/ key"; }
N_BUNDLES="$(python3 -c "import json;print(json.load(open('/dev/stdin'))['count'])" <<< "${BUNDLE_LIST}")"
[ "${N_BUNDLES}" = "0" ] || fail "/list counts registry entries as bundles (count=${N_BUNDLES})"
STATS="$(curl -s -H "Authorization: Bearer ${INGEST_TOKEN}" "${BASE}/stats")"
N_STATS="$(python3 -c "import json;print(json.load(open('/dev/stdin'))['count'])" <<< "${STATS}")"
[ "${N_STATS}" = "0" ] || fail "/stats counts registry entries as bundles (count=${N_STATS})"
ok "/list and /stats report zero bundles (registry/ invisible to the bundle side)"

step "6. registry_report.py renders the table from the local endpoint"
REPORT_OUT="${WORK}/report"
mkdir -p "${REPORT_OUT}"
SPIN_NOISE_ENDPOINT="${BASE}" SPIN_NOISE_REGISTRY_TOKEN="${REGISTRY_TOKEN}" \
  python3 "${REPO}/analysis/registry_report.py" --out "${REPORT_OUT}" \
  > "${WORK}/report.txt" 2>&1 || { cat "${WORK}/report.txt"; fail "registry_report.py failed"; }
grep -q "Synthetic State University" "${WORK}/report.txt" || { cat "${WORK}/report.txt"; fail "table missing the facility"; }
grep -q "1 sign-up(s) stored" "${WORK}/report.txt" || { cat "${WORK}/report.txt"; fail "unexpected sign-up count"; }
[ -f "${REPORT_OUT}/registry_facilities.json" ] || fail "registry_facilities.json not written"
python3 - "${REPORT_OUT}/registry_facilities.json" <<'EOF' || fail "facilities JSON malformed"
import json, sys
d = json.load(open(sys.argv[1]))
assert d["count"] == 1, d["count"]
assert d["facilities"][0]["institution"] == "Synthetic State University"
assert d["facilities"][0]["_registry_key"].startswith("registry/")
EOF
if python3 -c "import numpy" 2>/dev/null; then
  grep -q "Sidereal-phase coverage" "${WORK}/report.txt" \
    || { cat "${WORK}/report.txt"; fail "numpy present but coverage section missing (Carbondale is in the gazetteer)"; }
  ok "coverage section rendered (numpy present, city resolved)"
else
  grep -q "coverage skipped" "${WORK}/report.txt" || fail "numpy absent but no skip note"
  ok "coverage gracefully skipped (no numpy)"
fi
ok "report table + registry_facilities.json verified"

PASS=1
printf '\nALL REGISTRY TESTS PASSED\n'
