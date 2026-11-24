#!/usr/bin/env bash
# test_shared_access.sh -- end-to-end test of the collaboration read access
# (DATA_POLICY.md v1.1) against a REAL local Worker (wrangler dev, local
# mode: workerd + local R2 simulation).
#
#   ./testing/test_shared_access.sh
#
# What it proves, in order:
#   1. COLLAB_TOKENS unset   : /shared/list and /shared/object answer 503
#   2. keys set, no consent  : SHARED_SLUGS unset -> a member sees nothing
#                              and can fetch nothing (fail closed)
#   3. consent list set      : a member lists and downloads the bundles of
#                              the listed facilities, byte for byte, and
#                              nothing of a facility that is not listed --
#                              whose bundle answers exactly like a missing one
#   4. named keys            : the response says which member; a second
#                              member's key works; a removed member's does not
#   5. read only, and apart  : a member key cannot upload, list everything,
#                              fetch through /object, or read the registry;
#                              the facilities' INGEST token, a wrong key, a
#                              too-short key and no key do not open /shared/*
#   6. analysis/fetch_shared.py lists and downloads through the same routes
#      and verifies the sha256
#
# Requirements: node >= 18 (for npx wrangler), python3, curl. No Cloudflare
# account or login needed. All test keys are generated fresh per run.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${SPIN_NOISE_SHARED_TEST_PORT:-8789}"
BASE="http://localhost:${PORT}"
WORK="${REPO}/testing/synthetic_bundles/shared_$(date +%Y%m%d_%H%M%S)"
DEVVARS="${REPO}/server/.dev.vars"
rnd() { python3 -c 'import secrets; print(secrets.token_hex(24))'; }
INGEST_TOKEN="test-only-$(rnd)"
REGISTRY_TOKEN="test-only-$(rnd)"
KEY_ALICE="test-only-$(rnd)"
KEY_BOB="test-only-$(rnd)"

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
  fail "port ${PORT} already in use (set SPIN_NOISE_SHARED_TEST_PORT to override)"
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

# write_vars <collab-json or ''> <shared-slugs or ''>
write_vars() {
  {
    printf 'INGEST_TOKEN=%s\n' "${INGEST_TOKEN}"
    printf 'REGISTRY_TOKEN=%s\n' "${REGISTRY_TOKEN}"
    if [ -n "$1" ]; then printf "COLLAB_TOKENS='%s'\n" "$1"; fi
    if [ -n "$2" ]; then printf 'SHARED_SLUGS=%s\n' "$2"; fi
  } > "${DEVVARS}"
}

# code <bearer or ''> <url> [out]  -> prints the HTTP status
code() {
  local out="${3:-/dev/null}"
  if [ -n "$1" ]; then
    curl -s -o "${out}" -w '%{http_code}' -H "Authorization: Bearer $1" "$2"
  else
    curl -s -o "${out}" -w '%{http_code}' "$2"
  fi
}

# Three small synthetic "bundles" (any bytes do; the Worker stores, it does
# not open): two facilities that will consent, one that will not.
B_OULU="spinnoise_uni-oulu_20261005_081748Z_aaaa.zip"
B_TORINO="spinnoise_unito_20261001_085736Z_bbbb.zip"
B_OTHER="spinnoise_other-lab_20261101_120000Z_cccc.zip"
for b in "${B_OULU}" "${B_TORINO}" "${B_OTHER}"; do
  python3 -c "import os,sys; open(sys.argv[1],'wb').write(b'PK'+os.urandom(4096))" "${WORK}/${b}"
done
sha() { shasum -a 256 "$1" | cut -d' ' -f1; }

step "1. COLLAB_TOKENS unset -> /shared/* answers 503"
if [ -f "${DEVVARS}" ]; then
  DEVVARS_BACKUP="${DEVVARS}.shared-backup"
  mv "${DEVVARS}" "${DEVVARS_BACKUP}"
fi
write_vars "" ""
start_server
for b in "${B_OULU}" "${B_TORINO}" "${B_OTHER}"; do
  C="$(curl -s -o "${WORK}/up.json" -w '%{http_code}' -X POST \
        -H "Authorization: Bearer ${INGEST_TOKEN}" -H "X-Bundle-Name: ${b}" \
        -H "X-Content-SHA256: $(sha "${WORK}/${b}")" \
        -H "Content-Type: application/zip" --data-binary @"${WORK}/${b}" "${BASE}/ingest")"
  case "${C}" in 200|201) : ;; *) cat "${WORK}/up.json"; fail "could not seed ${b} (${C})" ;; esac
done
ok "three bundles stored through /ingest"
C="$(code "${KEY_ALICE}" "${BASE}/shared/list" "${WORK}/r.json")"
[ "${C}" = "503" ] || { cat "${WORK}/r.json"; fail "/shared/list without COLLAB_TOKENS: expected 503, got ${C}"; }
grep -q "COLLAB_TOKENS" "${WORK}/r.json" || fail "503 body lacks the setup hint"
C="$(code "${KEY_ALICE}" "${BASE}/shared/object?key=${B_OULU}")"
[ "${C}" = "503" ] || fail "/shared/object without COLLAB_TOKENS: expected 503, got ${C}"
ok "503 with a setup hint while COLLAB_TOKENS is unset"

step "2. member keys set, SHARED_SLUGS unset -> nothing is shared"
write_vars "{\"alice\":\"${KEY_ALICE}\",\"bob\":\"${KEY_BOB}\",\"shorty\":\"abc123\"}" ""
restart_server
C="$(code "${KEY_ALICE}" "${BASE}/shared/list" "${WORK}/r.json")"
[ "${C}" = "200" ] || { cat "${WORK}/r.json"; fail "expected 200, got ${C}"; }
python3 - "${WORK}/r.json" <<'EOF' || fail "a member sees bundles although no facility is on the list"
import json, sys
d = json.load(open(sys.argv[1]))
assert d["ok"] and d["count"] == 0 and d["bundles"] == [] and d["facilities"] == [], d
EOF
C="$(code "${KEY_ALICE}" "${BASE}/shared/object?key=${B_OULU}")"
[ "${C}" = "404" ] || fail "download with an empty consent list: expected 404, got ${C}"
ok "empty list, 404 on every bundle (fail closed)"

step "3. consent list set -> listed facilities only, byte for byte"
write_vars "{\"alice\":\"${KEY_ALICE}\",\"bob\":\"${KEY_BOB}\",\"shorty\":\"abc123\"}" "uni-oulu,unito"
restart_server
C="$(code "${KEY_ALICE}" "${BASE}/shared/list" "${WORK}/list.json")"
[ "${C}" = "200" ] || { cat "${WORK}/list.json"; fail "expected 200, got ${C}"; }
python3 - "${WORK}/list.json" "${B_OULU}" "${B_TORINO}" "${B_OTHER}" "$(sha "${WORK}/${B_OULU}")" <<'EOF' || fail "/shared/list content wrong"
import json, sys
d = json.load(open(sys.argv[1]))
names = sorted(b["name"] for b in d["bundles"])
assert names == sorted([sys.argv[2], sys.argv[3]]), names
assert sys.argv[4] not in json.dumps(d), "a non-consenting facility's bundle is listed"
assert d["facilities"] == ["uni-oulu", "unito"], d["facilities"]
assert d["member"] == "alice", d["member"]
by = {b["name"]: b for b in d["bundles"]}
assert by[sys.argv[2]]["sha256"] == sys.argv[5] and by[sys.argv[2]]["facility_slug"] == "uni-oulu"
assert "other-lab" not in json.dumps(d)
EOF
ok "/shared/list shows the two consenting facilities' bundles and names the member"
for b in "${B_OULU}" "${B_TORINO}"; do
  C="$(code "${KEY_ALICE}" "${BASE}/shared/object?key=${b}" "${WORK}/dl_${b}")"
  [ "${C}" = "200" ] || fail "download ${b}: expected 200, got ${C}"
  cmp -s "${WORK}/${b}" "${WORK}/dl_${b}" || fail "downloaded ${b} differs from what was stored"
done
ok "both shared bundles download byte-identical"
C1="$(code "${KEY_ALICE}" "${BASE}/shared/object?key=${B_OTHER}" "${WORK}/no1.json")"
C2="$(code "${KEY_ALICE}" "${BASE}/shared/object?key=spinnoise_other-lab_20261101_120000Z_zzzz.zip" "${WORK}/no2.json")"
[ "${C1}" = "404" ] && [ "${C2}" = "404" ] || fail "unshared/missing bundle: expected 404/404, got ${C1}/${C2}"
python3 - "${WORK}/no1.json" "${WORK}/no2.json" <<'EOF' || fail "an unshared bundle answers differently from a missing one"
import json, sys
a, b = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
a.pop("key"); b.pop("key")
assert a == b, (a, b)
EOF
C="$(code "${KEY_ALICE}" "${BASE}/shared/object?key=receipts/${B_OULU}.json")"
[ "${C}" = "404" ] || [ "${C}" = "400" ] || fail "receipt through /shared/object: expected 400/404, got ${C}"
ok "a non-consenting facility's bundle answers exactly like a missing one; no receipts"

step "4. named keys: a second member, and a removed one"
C="$(code "${KEY_BOB}" "${BASE}/shared/list" "${WORK}/bob.json")"
[ "${C}" = "200" ] || fail "second member: expected 200, got ${C}"
grep -q '"member": "bob"' "${WORK}/bob.json" || fail "response does not name the second member"
write_vars "{\"alice\":\"${KEY_ALICE}\",\"shorty\":\"abc123\"}" "uni-oulu,unito"
restart_server
C="$(code "${KEY_BOB}" "${BASE}/shared/list")"
[ "${C}" = "401" ] || fail "removed member: expected 401, got ${C}"
C="$(code "${KEY_ALICE}" "${BASE}/shared/list")"
[ "${C}" = "200" ] || fail "remaining member after the removal: expected 200, got ${C}"
ok "each key is one person; removing an entry ends that person's access"

step "5. read only, and apart from every other key"
for u in "/shared/list" "/shared/object?key=${B_OULU}"; do
  for t in "" "wrong-key-000000000000000000000000000000000000" "abc123" "${INGEST_TOKEN}" "${REGISTRY_TOKEN}"; do
    C="$(code "${t}" "${BASE}${u}")"
    [ "${C}" = "401" ] || fail "${u} with a non-member key: expected 401, got ${C}"
  done
done
ok "/shared/* refuses no key, a wrong key, a too-short key, the INGEST and the REGISTRY tokens"
for u in "/list" "/stats" "/object?key=${B_OULU}" "/object?key=${B_OTHER}" "/registry/list"; do
  C="$(code "${KEY_ALICE}" "${BASE}${u}")"
  [ "${C}" = "401" ] || fail "member key on ${u}: expected 401, got ${C}"
done
C="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer ${KEY_ALICE}" \
      -H "X-Bundle-Name: spinnoise_uni-oulu_20261201_000000Z_dddd.zip" \
      -H "X-Content-SHA256: $(sha "${WORK}/${B_OULU}")" --data-binary @"${WORK}/${B_OULU}" "${BASE}/ingest")"
[ "${C}" = "401" ] || fail "member key on POST /ingest: expected 401, got ${C}"
C="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer ${KEY_ALICE}" \
      -H "Content-Type: application/json" --data '{"institution":"x","city":"y","country":"z"}' "${BASE}/registry")"
[ "${C}" = "401" ] || fail "member key on POST /registry: expected 401, got ${C}"
for m in POST PUT DELETE; do
  C="$(curl -s -o /dev/null -w '%{http_code}' -X "${m}" -H "Authorization: Bearer ${KEY_ALICE}" "${BASE}/shared/object?key=${B_OULU}")"
  [ "${C}" = "405" ] || fail "${m} /shared/object: expected 405, got ${C}"
done
ok "a member key lists and downloads shared bundles and does nothing else"

step "6. analysis/fetch_shared.py through the same routes"
SPIN_NOISE_SHARED_KEY="${KEY_ALICE}" python3 "${REPO}/analysis/fetch_shared.py" \
  --endpoint "${BASE}" list > "${WORK}/tool_list.txt" 2>&1 || { cat "${WORK}/tool_list.txt"; fail "fetch_shared.py list failed"; }
grep -q "${B_OULU}" "${WORK}/tool_list.txt" && grep -q "${B_TORINO}" "${WORK}/tool_list.txt" \
  || { cat "${WORK}/tool_list.txt"; fail "tool list misses a shared bundle"; }
grep -q "${B_OTHER}" "${WORK}/tool_list.txt" && fail "tool list shows an unshared bundle"
grep -q "${KEY_ALICE}" "${WORK}/tool_list.txt" && fail "the tool printed the key"
mkdir -p "${WORK}/got"
SPIN_NOISE_SHARED_KEY="${KEY_ALICE}" python3 "${REPO}/analysis/fetch_shared.py" \
  --endpoint "${BASE}" get uni-oulu --out "${WORK}/got" > "${WORK}/tool_get.txt" 2>&1 \
  || { cat "${WORK}/tool_get.txt"; fail "fetch_shared.py get failed"; }
cmp -s "${WORK}/${B_OULU}" "${WORK}/got/${B_OULU}" || fail "tool download differs from what was stored"
[ ! -e "${WORK}/got/${B_TORINO}" ] || fail "tool fetched a bundle the substring did not select"
grep -q "sha256 verified" "${WORK}/tool_get.txt" || { cat "${WORK}/tool_get.txt"; fail "tool did not verify the sha256"; }
ok "fetch_shared.py lists, downloads and verifies; never prints the key"

PASS=1
printf '\nALL SHARED-ACCESS TESTS PASSED\n'
