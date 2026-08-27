# Deploying the spin-noise network repository (Cloudflare Worker + R2)

This directory contains the entire server side of the spin-noise network:
one Cloudflare Worker (`worker.js`) that accepts bundle uploads and stores
them in an R2 object-storage bucket. There is no database, no VM, and no
recurring maintenance beyond occasionally rotating a token.

Uploads are **automatic and size-unlimited in practice**: bundles up to
50 MB go up in a single request; anything larger (overnight runs) goes up
in 50 MiB chunks with resume-after-interruption, up to a 5 GiB ceiling —
all on the **free plan**, because each chunk is its own request and stays
under Cloudflare's 100 MB per-request cap. The uploader picks the path by
itself; facilities never think about any of this.

You need: a free Cloudflare account, Node.js (for the `wrangler` CLI), and
about ten minutes.

---

## 1. One-time setup

```sh
# Install the Cloudflare CLI (or use `npx wrangler ...` everywhere below)
npm i -g wrangler

# Log in (opens a browser window)
npx wrangler login

# Create the R2 bucket that will hold the bundles
npx wrangler r2 bucket create spin-noise-network
```

## 2. Set the ingest token (the shared secret facilities will use)

Generate a long random token and store it as a Worker secret. The Worker
refuses all requests until this secret exists. **Mint a fresh token at
deploy time** — never reuse one that has appeared in any earlier email,
document, or repository state.

```sh
# Generate a token (keep a copy — you will paste it into each facility's
# uploader/config.json)
openssl rand -hex 32

# Store it as the Worker secret (run from this server/ directory;
# wrangler prompts you to paste the value)
npx wrangler secret put INGEST_TOKEN
```

## 2b. Set the registry token (the facility sign-up forwarder's secret)

The `POST /registry` route (Google-Form sign-ups, forwarded by
`docs/forms_forwarder.gs`) is gated by a **separate** secret, so the form
infrastructure never holds the bundle-upload token. Same recipe, different
name — and until this secret exists the route answers 503 (everything else
is unaffected):

```sh
openssl rand -hex 32                      # mint a fresh, SEPARATE token
npx wrangler secret put REGISTRY_TOKEN    # paste it
npx wrangler deploy                       # redeploy so the secret takes effect
```

The value goes into exactly one consumer: the CONFIG block of the Apps
Script pasted into the Form (setup walkthrough: `docs/REGISTRY.md`).
`GET /registry/list` accepts either token — the coordinator holds both.

## 3. Deploy

```sh
# From this server/ directory (wrangler reads wrangler.jsonc)
npx wrangler deploy
```

Wrangler prints the Worker URL, something like:

```
https://spin-noise-ingest.<your-subdomain>.workers.dev
```

Verify it is alive (no auth needed for /health):

```sh
curl https://spin-noise-ingest.<your-subdomain>.workers.dev/health
# -> {"ok": true, "bucket": "spin-noise-network"}
```

## 4. Hand out the config to facilities

Each participating facility fills in `uploader/config.json` (copy from
`uploader/config.example.json`):

```json
{
  "endpoint_url": "https://spin-noise-ingest.<your-subdomain>.workers.dev/ingest",
  "token": "<the INGEST_TOKEN value>",
  "facility_slug": "their_facility_slug"
}
```

That is the entire onboarding step — `upload_bundle.py` does the rest,
including choosing single-shot vs. chunked and resuming interrupted
transfers. (`endpoint_url` may be the `/ingest` URL or the bare Worker
origin; the uploader derives all routes from it.)

### Optional: custom domain

The default `*.workers.dev` URL works fine. If you prefer a stable vanity
URL (e.g. `ingest.spin-noise.org`), add a domain to your Cloudflare account
and attach it to the Worker: dashboard → Workers & Pages → spin-noise-ingest
→ Settings → Domains & Routes → Add custom domain. Nothing in the Worker
code changes; just update `endpoint_url` in the facility configs.

---

## The upload API (what the Worker serves)

All routes except `/health` require `Authorization: Bearer <INGEST_TOKEN>`,
except the two registry routes: `POST /registry` takes `REGISTRY_TOKEN`
only, and `GET /registry/list` takes either token.

| Route | Purpose |
|---|---|
| `POST /ingest` | single-request upload — the fast path for bundles ≤ 50 MB; R2 verifies the `X-Content-SHA256` during the write |
| `POST /upload/create` | start a chunked upload: `{bundle_name, total_bytes, sha256, n_parts, part_bytes}` → `{key, upload_id}` |
| `PUT /upload/part?key=..&upload_id=..&part=N` | one raw chunk (5–95 MiB; final part may be smaller, flagged `&final=1`) → `{part, etag}` |
| `POST /upload/complete` | `{key, upload_id, parts:[{part,etag}...], sha256}` → `{receipt_id}`; also writes a `receipts/<key>.json` sidecar |
| `POST /upload/abort` | `{key, upload_id}` — abandon a stalled upload, freeing its stored parts |
| `GET /object?key=..` | stream one stored bundle (or its receipt sidecar) back — coordinator/analysis use |
| `GET /health` | liveness (no auth) |
| `GET /list?limit=50` | recent bundles |
| `GET /stats` | count + total bytes |
| `POST /registry` | store one facility sign-up (JSON ≤ 1 MB with `institution` + `city` + `country`; idempotent on the body sha256) — `REGISTRY_TOKEN` auth; 503 until that secret is set |
| `GET /registry/list` | sign-ups with keys + parsed bodies — `REGISTRY_TOKEN` **or** `INGEST_TOKEN`; consumed by `analysis/registry_report.py` |

Size rules: single-shot formally caps at 2 GiB but Cloudflare's edge cuts
bodies at 100 MB (Free/Pro) first — hence the 50 MB guidance; the chunked
path caps at **5 GiB** per bundle with parts of 5–95 MiB (the uploader sends
50 MiB parts). Duplicate bundle names get **409** on both paths; the
original is never overwritten.

Integrity: on the single-shot path R2 hashes the received bytes against the
declared sha256 and rejects mismatches during the write. On the chunked path
R2 cannot hash across parts, so the declared whole-file sha256 is stored in
the object's customMetadata and in the receipt sidecar; verify after
download (`shasum -a 256`) — the integration test does exactly this.

---

## Costs

For this project's expected volume the answer is: **free, or nearly so.**

- **R2 free tier**: 10 GB-month of storage, 1 million Class A (write) and
  10 million Class B (read) operations per month, and **zero egress fees**
  (downloading your data out costs nothing). Beyond that, storage is
  $0.015/GB-month. (developers.cloudflare.com/r2/pricing/)
- **Workers free tier**: 100,000 requests/day. Requests are per-part on the
  chunked path, so even a 5 GiB bundle is only ~100 part requests + 2 — the
  network will use a few hundred requests per day at most.
- A community of ~30 probes uploading 50 MB–2 GB bundles lands at a few to
  tens of GB; storage is the only line item that can eventually leave the
  free tier, at $0.015/GB-month.

---

## Downloading bundles (maintainer)

### Quick look and one-off downloads via the Worker

```sh
# List recent uploads (auth required)
curl -H "Authorization: Bearer <INGEST_TOKEN>" \
  "https://spin-noise-ingest.<your-subdomain>.workers.dev/list?limit=50"

# Totals
curl -H "Authorization: Bearer <INGEST_TOKEN>" \
  "https://spin-noise-ingest.<your-subdomain>.workers.dev/stats"

# Fetch one bundle straight through the Worker (R2 egress is free)
curl -H "Authorization: Bearer <INGEST_TOKEN>" \
  "https://spin-noise-ingest.<your-subdomain>.workers.dev/object?key=spinnoise_epfl_lausanne_20260817_231502Z_9f3a.zip" \
  -o spinnoise_epfl_lausanne_20260817_231502Z_9f3a.zip

# Or with wrangler, straight from R2
npx wrangler r2 object get \
  spin-noise-network/spinnoise_epfl_lausanne_20260817_231502Z_9f3a.zip \
  --file ./spinnoise_epfl_lausanne_20260817_231502Z_9f3a.zip --remote
```

### Bulk sync with rclone (recommended for analysis)

R2 speaks the S3 API. Create an R2 API token in the dashboard
(R2 → Manage R2 API Tokens → Create, "Object Read only" is enough), note
your account ID, then configure rclone (`~/.config/rclone/rclone.conf`):

```ini
[spinnoise]
type = s3
provider = Cloudflare
access_key_id = <R2 access key id>
secret_access_key = <R2 secret access key>
endpoint = https://<ACCOUNT_ID>.r2.cloudflarestorage.com
```

```sh
# Mirror everything to a local analysis directory (incremental, resumable)
rclone sync spinnoise:spin-noise-network ./bundles/

# Or just list
rclone ls spinnoise:spin-noise-network
```

---

## Maintenance

### Rotating the ingest token

Rotate if the token leaks, when a facility leaves the collaboration, or
annually on principle:

```sh
openssl rand -hex 32              # new token
npx wrangler secret put INGEST_TOKEN   # paste it; takes effect immediately
```

The change is immediate — old-token uploads start failing with 401. Then
email the new token to the participating facilities so they update
`uploader/config.json`. (There is a single shared token by design: the
collaborators are trusted, and per-facility tokens are not worth a database.
The facility identity comes from the bundle name and meta.json, not the
token.)

### Abandoned multipart uploads

An interrupted chunked upload that is never resumed keeps its parts stored
until aborted. The uploader's `--abort` flag cleans up its own stalled
uploads; as maintainer you can set an R2 lifecycle rule (dashboard → R2 →
spin-noise-network → Settings → Object lifecycle rules → "Abort incomplete
multipart uploads" after e.g. 7 days) so orphans never accumulate.

### Watching activity

- `GET /list` and `GET /stats` (curl commands above) are the day-to-day view.
- `npx wrangler tail spin-noise-ingest` streams live request logs (useful
  while a facility debugs their first upload).
- Dashboard → R2 → spin-noise-network shows objects, sizes, and metrics.
  Completed chunked uploads also leave a small `receipts/<key>.json`
  sidecar with the declared sha256, part count, and completion time.

### Duplicate and integrity behavior (what facilities may ask about)

- A re-sent bundle with the same name gets **409 Conflict** — the original
  is never overwritten. Re-zipping produces a new random suffix, so true
  re-uploads always succeed. (One exception, deliberately: re-running
  `/upload/complete` for an object that already landed with the same sha256
  returns success again, so a lost response never strands a finished upload.)
- Single-shot uploads carry `X-Content-SHA256`; R2 verifies the received
  bytes against it **during** the write and rejects on mismatch (HTTP 400,
  nothing stored). A stored object is therefore always bit-exact.
- Chunked uploads record the declared sha256 in metadata + receipt; verify
  after download. Each part transfer is additionally protected by TLS.

### Manual upload fallback (facility has the zip, uploader failed)

For a bundle ≤ 50 MB, any HTTP client works; this is exactly what
`upload_bundle.py` does on its single-shot path:

```sh
Z=spinnoise_myfacility_20260817_231502Z_9f3a.zip
curl -X POST "https://spin-noise-ingest.<your-subdomain>.workers.dev/ingest" \
  -H "Authorization: Bearer <INGEST_TOKEN>" \
  -H "Content-Type: application/zip" \
  -H "X-Bundle-Name: $Z" \
  -H "X-Content-SHA256: $(shasum -a 256 "$Z" | cut -d' ' -f1)" \
  --data-binary @"$Z"
```

Larger bundles: rerun `upload_bundle.py` (it resumes on its own), or the
facility emails/file-transfers the zip to the maintainer, who drops it in
with `npx wrangler r2 object put spin-noise-network/$Z --file $Z --remote`.

---

## Testing the whole path locally (no Cloudflare account needed)

```sh
./testing/test_upload_integration.sh
```

runs the real Worker under `wrangler dev --local` (workerd + a local R2
simulation), uploads a small bundle through `/ingest` and a ~160 MiB bundle
through the chunked path with the real uploader, kill -9's the uploader
mid-transfer and proves the resume, verifies byte-exact retrieval via
`GET /object`, and exercises `--abort`. Its final step runs the
facility-registry suite (`testing/test_registry.sh`, also runnable on its
own): the 503-until-configured path, a synthetic sign-up, the idempotent
re-POST, the 401 paths, `/registry/list`, the `/list`+`/stats` exclusion,
and `analysis/registry_report.py` against the live local endpoint. Run it
after any change to `worker.js` or `upload_bundle.py`.

---

## Worst case: zero-infrastructure fallback (Zenodo instead)

If you would rather run **no** server at all, the network still works —
the protocol and uploader are deliberately independent of this Worker:

1. Create a **Zenodo community** (zenodo.org → Communities → New) named
   e.g. `spin-noise-network`. Zenodo is CERN-hosted, free, permanent, and
   gives every upload a DOI.
2. Tell facilities to leave `uploader/config.json` **absent or empty**.
   `upload_bundle.py` then takes its fallback path: it keeps the zip on
   the spectrometer workstation and prints instructions.
3. Facilities upload their zips at zenodo.org/uploads (web form: drag the
   zip in, choose the community, paste the contents of `meta.json` into the
   description field, publish). Curation happens in the community queue.
4. The maintainer harvests via the Zenodo REST API or just downloads from
   the community page; `meta.json` inside each zip carries everything the
   analysis needs.

Trade-offs: no automatic upload (a human clicks), no duplicate check, and
records are public by default (fine — this is open data). Gains: literally
nothing to deploy, rotate, or pay for, and DOIs for citing the raw data.
You can also run both: the Worker for day-to-day automation, plus periodic
curated Zenodo snapshots of the full bundle set for citability.
