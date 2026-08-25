# Deploying the spin-noise network repository (Cloudflare Worker + R2)

This directory contains the entire server side of the spin-noise network:
one Cloudflare Worker (`worker.js`) that accepts bundle uploads and stores
them in an R2 object-storage bucket. There is no database, no VM, and no
recurring maintenance beyond occasionally rotating a token.

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
refuses all requests until this secret exists.

```sh
# Generate a token (keep a copy — you will paste it into each facility's
# uploader/config.json)
openssl rand -hex 32

# Store it as the Worker secret (run from this server/ directory;
# wrangler prompts you to paste the value)
npx wrangler secret put INGEST_TOKEN
```

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

That is the entire onboarding step — `upload_bundle.py` does the rest.

### Optional: custom domain

The default `*.workers.dev` URL works fine. If you prefer a stable vanity
URL (e.g. `ingest.spin-noise.org`), add a domain to your Cloudflare account
and attach it to the Worker: dashboard → Workers & Pages → spin-noise-ingest
→ Settings → Domains & Routes → Add custom domain. Nothing in the Worker
code changes; just update `endpoint_url` in the facility configs.

---

## Costs

For this project's expected volume the answer is: **free, or nearly so.**

- **R2 free tier**: 10 GB-month of storage, 1 million Class A (write) and
  10 million Class B (read) operations per month, and **zero egress fees**
  (downloading your data out costs nothing). Beyond that, storage is
  $0.015/GB-month. (developers.cloudflare.com/r2/pricing/)
- **Workers free tier**: 100,000 requests/day — the network will use a few
  dozen.
- A community of ~30 probes uploading ~50–200 MB bundles lands at a few GB;
  you likely stay inside the free tier for a long while.

### Upload size caveat (read this once)

The Worker formally rejects bundles over 2 GiB, but Cloudflare's edge
enforces a smaller **per-plan request body limit first**: 100 MB on Free and
Pro plans, 200 MB Business, 500 MB Enterprise
(developers.cloudflare.com/workers/platform/limits/). A standard 30–60 min
run compresses well under 100 MB; **overnight runs may exceed it**. If a
facility hits an HTTP 413, their options are (a) keep the zip and use the
manual fallback below, or (b) you upgrade the zone plan. The uploader
already prints these instructions on failure.

---

## Downloading bundles (maintainer)

### Quick look and one-off downloads with wrangler

```sh
# List recent uploads via the Worker (auth required)
curl -H "Authorization: Bearer <INGEST_TOKEN>" \
  "https://spin-noise-ingest.<your-subdomain>.workers.dev/list?limit=50"

# Totals
curl -H "Authorization: Bearer <INGEST_TOKEN>" \
  "https://spin-noise-ingest.<your-subdomain>.workers.dev/stats"

# Fetch one bundle straight from R2
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
npx wrangler secret put INGEST_TOKEN   # paste it; takes effect on next deploy-less update
```

The change is immediate — old-token uploads start failing with 401. Then
email the new token to the participating facilities so they update
`uploader/config.json`. (There is a single shared token by design: the
collaborators are trusted, and per-facility tokens are not worth a database.
The facility identity comes from the bundle name and meta.json, not the
token.)

### Watching activity

- `GET /list` and `GET /stats` (curl commands above) are the day-to-day view.
- `npx wrangler tail spin-noise-ingest` streams live request logs (useful
  while a facility debugs their first upload).
- Dashboard → R2 → spin-noise-network shows objects, sizes, and metrics.

### Duplicate and integrity behavior (what facilities may ask about)

- A re-sent bundle with the same name gets **409 Conflict** — the original
  is never overwritten. Re-zipping produces a new random suffix, so true
  re-uploads always succeed.
- Every upload carries an `X-Content-SHA256`; R2 verifies the received bytes
  against it **during** the write and rejects on mismatch (HTTP 400,
  nothing stored). A stored object is therefore always bit-exact.

### Manual upload fallback (facility has the zip, uploader failed)

Any HTTP client works; this is exactly what `upload_bundle.py` does:

```sh
Z=spinnoise_myfacility_20260817_231502Z_9f3a.zip
curl -X POST "https://spin-noise-ingest.<your-subdomain>.workers.dev/ingest" \
  -H "Authorization: Bearer <INGEST_TOKEN>" \
  -H "Content-Type: application/zip" \
  -H "X-Bundle-Name: $Z" \
  -H "X-Content-SHA256: $(shasum -a 256 "$Z" | cut -d' ' -f1)" \
  --data-binary @"$Z"
```

Or the facility simply emails the zip to the maintainer, who drops it in
with `npx wrangler r2 object put spin-noise-network/$Z --file $Z --remote`.

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
