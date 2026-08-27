# Facility registry — Google Form → Worker automation

Sign-ups arrive through the CASPEr@night Google Form
(`proposals/comms_setup_checklist.md`, section 2). This document wires those
responses into the ingest Worker automatically, so the coordinator's tools
(`analysis/registry_report.py`) always see the current facility list without
anyone exporting spreadsheets.

The moving parts:

| Piece | File | Runs where |
|---|---|---|
| Registry routes (`POST /registry`, `GET /registry/list`) | `server/worker.js` | Cloudflare Worker |
| Form forwarder (`onFormSubmit`, `runBackfill`) | `docs/forms_forwarder.gs` | the Form's Apps Script editor |
| Coordinator pull tool | `analysis/registry_report.py` | your laptop |

Design points:

- **Separate secret.** `POST /registry` is gated by `REGISTRY_TOKEN`, not
  `INGEST_TOKEN`. The Apps Script infrastructure (Google's servers) therefore
  never holds the bundle-upload token; the registry token can do nothing but
  add sign-ups. `GET /registry/list` accepts either token — the coordinator
  holds both. If `REGISTRY_TOKEN` is unset the route answers **503** with a
  setup hint.
- **Idempotent.** The Worker keys each entry by a UTC stamp plus 8 hex chars
  of the payload's sha256 and recognizes byte-identical re-sends (Apps Script
  retries, `runBackfill` re-runs) — it answers ok with the existing key and
  stores nothing twice.
- **Invisible to the bundle side.** Entries live under the `registry/` key
  prefix; `/list` and `/stats` filter on the `spinnoise_` prefix (same
  mechanism that hides the `receipts/` sidecars), so registry entries never
  appear in bundle listings.
- **No sign-up silently lost.** The forwarder retries once and on repeated
  failure emails the coordinator the full payload for manual replay.

---

## One-time setup (John, ~10 minutes)

### 1. Mint the registry token and redeploy the Worker

```sh
cd server/

# Mint a FRESH token (never reuse one from any email/document/repo state)
openssl rand -hex 32

# Store it as the Worker secret (wrangler prompts for the value)
npx wrangler secret put REGISTRY_TOKEN

# Redeploy so the new routes go live
npx wrangler deploy
```

Verify the gate (this must answer 401, not 503, once the secret is set):

```sh
curl -s -X POST https://spin-noise-ingest.<your-subdomain>.workers.dev/registry \
  -H "Content-Type: application/json" -d '{}'
# -> {"ok": false, "error": "unauthorized"}   (401)
```

Keep the token in your password manager. It goes into exactly one other
place: the Apps Script CONFIG block below.

### 2. Paste the forwarder into the Form

1. Open the sign-up Form (as its owner) → three-dot menu → **Apps Script**
   (older UI: Extensions → Apps Script).
2. Delete the placeholder `myFunction` and paste the entire contents of
   `docs/forms_forwarder.gs`.
3. Fill in the two CONFIG lines at the top: `ENDPOINT` (the Worker origin,
   e.g. `https://spin-noise-ingest.<your-subdomain>.workers.dev`) and `TOKEN`
   (the `REGISTRY_TOKEN` value). **These live only in the script editor —
   never commit them.**
4. Save (Ctrl/Cmd-S), name the project e.g. `registry-forwarder`.

### 3. Install the trigger

1. In the script editor, click the **Triggers** clock icon (left sidebar) →
   **Add Trigger**.
2. Function: `onFormSubmit` · Deployment: Head · Event source: **From form**
   · Event type: **On form submit**. Save.
3. Google shows the authorization consent screen (the script needs form-read,
   external-URL, and send-mail-as-you permissions — that is the retry-failure
   alert). Authorize with the form-owner account. An "unverified app" warning
   is expected for a personal script: Advanced → Go to project.

From this moment every new form submission lands in the Worker within
seconds. Optional smoke test: run `sendTestPing()` from the editor toolbar,
then confirm with the coordinator tool (below) and delete the test entry if
you care (it is clearly labeled `TEST PING`).

### 4. Backfill responses submitted BEFORE the trigger existed

The trigger only fires for new submissions. Anything already sitting in the
Form (e.g. **Boyd Goodson's sign-up**) is recovered by the backfill function
in the same script:

1. In the script editor toolbar, select `runBackfill` in the function
   dropdown → **Run**.
2. Check the execution log: one line per stored response, ending
   `forwarded (HTTP 201)`.

Safe to re-run any time — the server stores byte-identical payloads only
once. Re-running after future form-title edits is also safe: already-stored
entries are recognized, genuinely new ones get added.

---

## Reading the registry (coordinator)

```sh
# Raw listing (either token works)
curl -s -H "Authorization: Bearer <REGISTRY_TOKEN or INGEST_TOKEN>" \
  "https://spin-noise-ingest.<your-subdomain>.workers.dev/registry/list" | python3 -m json.tool

# The nice version: table + registry_facilities.json + best-effort
# sidereal-coverage numbers
python3 analysis/registry_report.py
```

`registry_report.py` reads the endpoint and token from
`uploader/config.json` (add an optional `"registry_token"` key next to the
existing `"token"`) or from the environment
(`SPIN_NOISE_ENDPOINT`, `SPIN_NOISE_REGISTRY_TOKEN`); run with `--help` for
the flags.

## What the Worker stores

`registry/<utc-stamp>_<8-hex-of-body-sha>.json`, body verbatim as the
forwarder sent it:

```json
{
  "submitted_at": "2026-08-27T14:02:11.000Z",
  "institution": "Example University",
  "contact_name": "A. Scientist",
  "contact_email": "scientist@example.edu",
  "spectrometers": "600 MHz / Avance III HD / TopSpin 3.6.2",
  "probes": "Prodigy BBO, N2-cooled, ATM yes, sample changer no",
  "city": "Lausanne",
  "country": "Switzerland",
  "city_country_raw": "Lausanne, Switzerland",
  "heard_via": "colleague",
  "consent_contact": true,
  "consent_map": false,
  "forwarder": "forms_forwarder.gs"
}
```

The Worker is deliberately lenient — any JSON object under 1 MB with
non-empty `institution` + `city` + `country` is accepted — so form edits and
older forwarder versions keep working. Privacy: the Worker records the
submitting country (Cloudflare geo), never an IP address; consent boxes
unchecked mean **no** and are honored downstream (`consent_map` gates the
public coverage map).

## Token hygiene / rotation

Rotate `REGISTRY_TOKEN` independently of the ingest token (leak, annual
principle, or if the Apps Script project is ever shared):

```sh
openssl rand -hex 32
npx wrangler secret put REGISTRY_TOKEN   # takes effect immediately
```

then update the single `TOKEN` line in the Form's script editor. Nothing
else holds it.
