/**
 * spin-noise-ingest — Cloudflare Worker for the spin-noise network central repository.
 *
 * Receives zipped Bruker experiment bundles from community NMR facilities
 * (uploaded by uploader/upload_bundle.py) and stores them in an R2 bucket.
 *
 * Routes:
 *   POST /ingest             store one bundle in a single request (auth required)
 *                            — the fast path for bundles <= 50 MB; larger bodies
 *                            hit Cloudflare's per-plan edge cap (100 MB on Free)
 *   POST /upload/create      start a chunked multipart upload (auth required)
 *   PUT  /upload/part        upload one part (auth required)
 *   POST /upload/complete    finish a multipart upload (auth required)
 *   POST /upload/abort       abandon a multipart upload (auth required)
 *   GET  /object?key=...     retrieve one stored object (auth required;
 *                            coordinator/test use — facilities never need it)
 *   GET  /health             liveness check (no auth)
 *   GET  /list?limit=50      recent bundle keys + sizes (auth required)
 *   GET  /stats              bundle count + total bytes (auth required)
 *
 * Design notes (kept deliberately boring — this must run unattended for years):
 *   - No external dependencies. Plain ES-module Worker, one file.
 *   - Auth is a single shared bearer token stored as the INGEST_TOKEN secret.
 *     Facilities are trusted collaborators; the token only gates spam/abuse.
 *     The token value is never logged.
 *   - Bodies are streamed straight into R2 (request.body is a ReadableStream)
 *     — the Worker never buffers a bundle or a part in memory.
 *   - Integrity, single-shot path: the uploader sends X-Content-SHA256 and R2
 *     verifies the received bytes against it during the write (put() `sha256`
 *     option), rejecting the put on mismatch.
 *   - Integrity, chunked path: R2 cannot hash across parts, so the declared
 *     whole-file sha256 is recorded in customMetadata at create time and in a
 *     receipts/<key>.json sidecar at complete time. Each part is protected by
 *     TLS + TCP in transit and by its returned etag; the coordinator can (and
 *     the integration test does) verify the whole-object sha256 after download.
 *   - Privacy: we record the uploader's COUNTRY (request.cf.country) in
 *     customMetadata, never the connecting IP address.
 *
 * Size limits (documented in DEPLOY.md too):
 *   - Single-shot /ingest: intended for <= 50 MB. Cloudflare's edge enforces a
 *     per-plan request-body cap (Free/Pro 100 MB, Business 200 MB, Enterprise
 *     500 MB) BEFORE this Worker runs; the Worker's own formal ceiling is
 *     2 GiB for hand-rolled clients on big plans.
 *   - Chunked path: bundles up to 5 GiB, parts of 5–95 MiB (final part may be
 *     smaller). The uploader sends 50 MiB parts, comfortably under every
 *     plan's edge cap — so size-unlimited-in-practice uploads work on the
 *     FREE plan (each part is its own request).
 */

// ---------------------------------------------------------------------------
// Tunables
// ---------------------------------------------------------------------------

const MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024;      // /ingest formal ceiling (2 GiB)
const MAX_MULTIPART_BYTES = 5 * 1024 * 1024 * 1024;   // chunked-path ceiling (5 GiB)
const MIN_PART_BYTES = 5 * 1024 * 1024;               // R2 multipart minimum (non-final)
const MAX_PART_BYTES = 95 * 1024 * 1024;              // stay under the 100 MB edge cap
const MAX_PARTS = 10000;                              // R2 multipart maximum
const MAX_KEY_LENGTH = 200;                           // generous; real keys ~55 chars
const DEFAULT_LIST_LIMIT = 50;
const MAX_LIST_LIMIT = 500;
const RECEIPT_PREFIX = "receipts/";                   // sidecar keys — outside the
                                                      // spinnoise_ list/stats prefix

// Bundle names are produced by upload_bundle.py as
//   spinnoise_<facility_slug>_<YYYYMMDD_HHMMSSZ>_<4hex>.zip
// We do not try to parse the internal structure (facilities occasionally
// hand-rename), we only require the prefix, the safe character set, and .zip.
const KEY_PATTERN = /^spinnoise_[A-Za-z0-9._-]+\.zip$/;

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

/** JSON response helper. */
function json(obj, status = 200) {
  return new Response(JSON.stringify(obj, null, 2) + "\n", {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Constant-time(ish) comparison of the presented bearer token against the
 * configured secret. Uses the Workers-native crypto.subtle.timingSafeEqual
 * (developers.cloudflare.com/workers/runtime-apis/web-crypto/#timingsafeequal).
 * timingSafeEqual requires equal-length buffers; when lengths differ we
 * compare a buffer against itself and negate, so we never return early on
 * length (which would leak the secret's length through timing).
 */
function tokensMatch(presented, secret) {
  const enc = new TextEncoder();
  const a = enc.encode(presented);
  const b = enc.encode(secret);
  if (a.byteLength !== b.byteLength) {
    // Burn comparable time, then fail.
    crypto.subtle.timingSafeEqual(b, b);
    return false;
  }
  return crypto.subtle.timingSafeEqual(a, b);
}

/**
 * Check the Authorization header. Returns null when authorized, otherwise a
 * ready-to-return error Response. Fails closed if the secret is unset.
 */
function checkAuth(request, env) {
  if (!env.INGEST_TOKEN) {
    // Deployed without `wrangler secret put INGEST_TOKEN` — refuse everything
    // rather than silently becoming a public drop box.
    return json({ ok: false, error: "server not configured (INGEST_TOKEN secret missing)" }, 503);
  }
  const header = request.headers.get("Authorization") || "";
  const m = header.match(/^Bearer\s+(\S+)$/);
  if (!m || !tokensMatch(m[1], env.INGEST_TOKEN)) {
    return json({ ok: false, error: "unauthorized" }, 401);
  }
  return null;
}

/**
 * Sanitize + validate a bundle name into an R2 key.
 * Returns the key string, or null if the name is unacceptable.
 * We are strict on purpose: the key namespace is flat and shared by every
 * facility, so nothing that could smuggle a path or odd character gets in.
 */
function sanitizeBundleName(raw) {
  if (!raw) return null;
  // Strip any path components someone might have left in (defensive; the
  // uploader sends a bare basename).
  const base = String(raw).split("/").pop().split("\\").pop().trim();
  if (base.length === 0 || base.length > MAX_KEY_LENGTH) return null;
  if (!KEY_PATTERN.test(base)) return null;
  return base;
}

/** Normalize + validate a sha256 hex digest (optional "sha256:" prefix ok). */
function normalizeSha256(raw) {
  let hex = String(raw || "").trim().toLowerCase();
  if (hex.startsWith("sha256:")) hex = hex.slice(7);
  return /^[0-9a-f]{64}$/.test(hex) ? hex : null;
}

/** Short, stable receipt id derived from the bundle's declared sha256. */
function receiptIdFor(sha256hex) {
  return "sn-" + sha256hex.slice(0, 16);
}

/** Parse a small JSON request body; returns [obj, null] or [null, Response]. */
async function readJsonBody(request) {
  let body;
  try {
    body = await request.json();
  } catch (_) {
    return [null, json({ ok: false, error: "request body must be JSON" }, 400)];
  }
  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    return [null, json({ ok: false, error: "request body must be a JSON object" }, 400)];
  }
  return [body, null];
}

/** True when an R2 error looks like "this multipart upload does not exist". */
function isUnknownUploadError(err) {
  const msg = String((err && err.message) || err);
  return /does not exist|no such upload|NoSuchUpload|not found|invalid.*upload/i.test(msg);
}

// ---------------------------------------------------------------------------
// Route handlers — single-shot path
// ---------------------------------------------------------------------------

/** POST /ingest — store one bundle in a single request (<= 50 MB fast path). */
async function handleIngest(request, env) {
  const authError = checkAuth(request, env);
  if (authError) return authError;

  // --- validate headers before touching the body ---

  const key = sanitizeBundleName(request.headers.get("X-Bundle-Name"));
  if (!key) {
    return json(
      {
        ok: false,
        error:
          "missing or invalid X-Bundle-Name header " +
          "(must match spinnoise_<slug>_<stamp>_<4hex>.zip, chars [A-Za-z0-9._-] only)",
      },
      400
    );
  }

  // Size guard. Cloudflare's edge already rejects bodies over the account
  // plan limit (100-500 MB) with a 413 before we run; this check is the
  // formal 2 GiB ceiling and also catches a missing Content-Length.
  const lenHeader = request.headers.get("Content-Length");
  const contentLength = lenHeader === null ? NaN : Number(lenHeader);
  if (!Number.isFinite(contentLength) || contentLength <= 0) {
    return json({ ok: false, error: "Content-Length required (chunked transfer not accepted)" }, 411);
  }
  if (contentLength > MAX_BUNDLE_BYTES) {
    return json({ ok: false, error: `bundle exceeds ${MAX_BUNDLE_BYTES} bytes (2 GiB) limit; use the chunked /upload/* path` }, 413);
  }

  // Integrity header. The uploader always sends the zip's SHA-256 as lowercase
  // hex; accept an optional "sha256:" prefix for hand-rolled curl uploads.
  const sha256hex = normalizeSha256(request.headers.get("X-Content-SHA256"));
  if (!sha256hex) {
    return json(
      { ok: false, error: "missing or malformed X-Content-SHA256 header (need 64 hex chars)" },
      400
    );
  }

  // Duplicate check: bundle names embed a timestamp + 4 random hex chars, so a
  // collision means a genuine re-send. head() is cheap (metadata only).
  const existing = await env.SPIN_NOISE.head(key);
  if (existing !== null) {
    return json(
      {
        ok: false,
        error: "duplicate: an object with this bundle name already exists",
        key,
        existing: { size: existing.size, uploaded: existing.uploaded },
      },
      409
    );
  }

  // --- stream the body into R2 ---
  //
  // put() with the `sha256` option makes R2 hash the received bytes and fail
  // the write on mismatch, so we get end-to-end integrity without buffering
  // anything ourselves. On mismatch put() rejects and we return 400.
  const receivedAt = new Date().toISOString();
  const country = (request.cf && request.cf.country) || "unknown"; // country only — never the IP

  let object;
  try {
    object = await env.SPIN_NOISE.put(key, request.body, {
      sha256: sha256hex,
      httpMetadata: { contentType: "application/zip" },
      customMetadata: {
        sha256: sha256hex,
        receivedAt: receivedAt,
        uploadCountry: country,
      },
    });
  } catch (err) {
    // R2 throws on checksum mismatch (and on transport errors). Either way
    // the object was NOT stored, so the client should retry or re-zip.
    const msg = String((err && err.message) || err);
    const looksLikeChecksum = /checksum|hash|integrity|sha/i.test(msg);
    return json(
      {
        ok: false,
        error: looksLikeChecksum
          ? "sha256 mismatch: received bytes do not match X-Content-SHA256 (nothing stored)"
          : "storage error (nothing stored): " + msg,
      },
      looksLikeChecksum ? 400 : 502
    );
  }

  return json({
    ok: true,
    key,
    size: object.size,
    sha256: sha256hex,
    etag: object.etag,
    receivedAt,
    receipt_id: receiptIdFor(sha256hex),
  });
}

// ---------------------------------------------------------------------------
// Route handlers — chunked multipart path
// ---------------------------------------------------------------------------

/**
 * POST /upload/create — start a chunked upload.
 * Body: {bundle_name, total_bytes, sha256, n_parts, part_bytes}
 * Returns: {ok, key, upload_id}
 */
async function handleUploadCreate(request, env) {
  const authError = checkAuth(request, env);
  if (authError) return authError;

  const [body, bodyError] = await readJsonBody(request);
  if (bodyError) return bodyError;

  const key = sanitizeBundleName(body.bundle_name);
  if (!key) {
    return json(
      {
        ok: false,
        error:
          "missing or invalid bundle_name " +
          "(must match spinnoise_<slug>_<stamp>_<4hex>.zip, chars [A-Za-z0-9._-] only)",
      },
      400
    );
  }

  const totalBytes = Number(body.total_bytes);
  if (!Number.isInteger(totalBytes) || totalBytes <= 0) {
    return json({ ok: false, error: "total_bytes must be a positive integer" }, 400);
  }
  if (totalBytes > MAX_MULTIPART_BYTES) {
    return json({ ok: false, error: `total_bytes exceeds ${MAX_MULTIPART_BYTES} bytes (5 GiB) limit` }, 413);
  }

  const sha256hex = normalizeSha256(body.sha256);
  if (!sha256hex) {
    return json({ ok: false, error: "missing or malformed sha256 (need 64 hex chars)" }, 400);
  }

  const nParts = Number(body.n_parts);
  const partBytes = Number(body.part_bytes);
  if (!Number.isInteger(nParts) || nParts < 1 || nParts > MAX_PARTS) {
    return json({ ok: false, error: `n_parts must be an integer in 1..${MAX_PARTS}` }, 400);
  }
  if (!Number.isInteger(partBytes) || partBytes < 1 || partBytes > MAX_PART_BYTES) {
    return json({ ok: false, error: `part_bytes must be an integer in 1..${MAX_PART_BYTES}` }, 400);
  }
  if (nParts > 1 && partBytes < MIN_PART_BYTES) {
    return json({ ok: false, error: `part_bytes must be >= ${MIN_PART_BYTES} (5 MiB) for multi-part uploads` }, 400);
  }
  // Geometry must be self-consistent: n_parts full-ish parts cover total_bytes.
  if ((nParts - 1) * partBytes >= totalBytes || nParts * partBytes < totalBytes) {
    return json({ ok: false, error: "n_parts/part_bytes do not cover total_bytes" }, 400);
  }

  // Duplicate check, same semantics as /ingest.
  const existing = await env.SPIN_NOISE.head(key);
  if (existing !== null) {
    return json(
      {
        ok: false,
        error: "duplicate: an object with this bundle name already exists",
        key,
        existing: { size: existing.size, uploaded: existing.uploaded },
      },
      409
    );
  }

  const createdAt = new Date().toISOString();
  const country = (request.cf && request.cf.country) || "unknown"; // country only — never the IP

  let upload;
  try {
    // customMetadata rides along at create time and lands on the final object
    // when the upload completes — no post-hoc copy needed.
    upload = await env.SPIN_NOISE.createMultipartUpload(key, {
      httpMetadata: { contentType: "application/zip" },
      customMetadata: {
        sha256: sha256hex,
        receivedAt: createdAt, // stamped at create; completion time goes in the receipt sidecar
        uploadCountry: country,
        declaredParts: String(nParts),
        declaredBytes: String(totalBytes),
      },
    });
  } catch (err) {
    return json({ ok: false, error: "could not create multipart upload: " + String((err && err.message) || err) }, 502);
  }

  return json({ ok: true, key, upload_id: upload.uploadId, part_bytes: partBytes, n_parts: nParts });
}

/**
 * PUT /upload/part?key=..&upload_id=..&part=N[&final=1] — upload one part.
 * Body: the raw part bytes. Returns: {ok, part, etag}
 */
async function handleUploadPart(request, env) {
  const authError = checkAuth(request, env);
  if (authError) return authError;

  const url = new URL(request.url);
  const key = sanitizeBundleName(url.searchParams.get("key"));
  const uploadId = (url.searchParams.get("upload_id") || "").trim();
  const partNum = Number(url.searchParams.get("part"));
  const isFinal = url.searchParams.get("final") === "1";

  if (!key) return json({ ok: false, error: "missing or invalid key parameter" }, 400);
  if (!uploadId) return json({ ok: false, error: "missing upload_id parameter" }, 400);
  if (!Number.isInteger(partNum) || partNum < 1 || partNum > MAX_PARTS) {
    return json({ ok: false, error: `part must be an integer in 1..${MAX_PARTS}` }, 400);
  }

  const lenHeader = request.headers.get("Content-Length");
  const contentLength = lenHeader === null ? NaN : Number(lenHeader);
  if (!Number.isFinite(contentLength) || contentLength <= 0) {
    return json({ ok: false, error: "Content-Length required" }, 411);
  }
  if (contentLength > MAX_PART_BYTES) {
    return json({ ok: false, error: `part exceeds ${MAX_PART_BYTES} bytes (95 MiB) limit` }, 413);
  }
  if (contentLength < MIN_PART_BYTES && !isFinal) {
    return json(
      { ok: false, error: `non-final parts must be >= ${MIN_PART_BYTES} bytes (5 MiB); add &final=1 for the last part` },
      400
    );
  }

  const upload = env.SPIN_NOISE.resumeMultipartUpload(key, uploadId);
  let part;
  try {
    // Streams straight from the request into R2; never buffered here.
    part = await upload.uploadPart(partNum, request.body);
  } catch (err) {
    if (isUnknownUploadError(err)) {
      return json(
        { ok: false, error: "unknown upload_id for this key (upload expired, aborted, or never created) — start over with POST /upload/create" },
        404
      );
    }
    return json({ ok: false, error: "part upload failed: " + String((err && err.message) || err) }, 502);
  }

  return json({ ok: true, part: part.partNumber, etag: part.etag });
}

/**
 * POST /upload/complete — finish a chunked upload.
 * Body: {key, upload_id, parts: [{part, etag}, ...], sha256}
 * Returns: {ok, key, size, receipt_id}
 * Idempotent-ish: if the object already exists with the SAME declared sha256
 * (a retry after a lost success response), returns ok again instead of 409.
 */
async function handleUploadComplete(request, env) {
  const authError = checkAuth(request, env);
  if (authError) return authError;

  const [body, bodyError] = await readJsonBody(request);
  if (bodyError) return bodyError;

  const key = sanitizeBundleName(body.key);
  const uploadId = String(body.upload_id || "").trim();
  const sha256hex = normalizeSha256(body.sha256);
  if (!key) return json({ ok: false, error: "missing or invalid key" }, 400);
  if (!uploadId) return json({ ok: false, error: "missing upload_id" }, 400);
  if (!sha256hex) return json({ ok: false, error: "missing or malformed sha256 (need 64 hex chars)" }, 400);

  if (!Array.isArray(body.parts) || body.parts.length < 1 || body.parts.length > MAX_PARTS) {
    return json({ ok: false, error: `parts must be a non-empty array (max ${MAX_PARTS})` }, 400);
  }
  const parts = [];
  for (const p of body.parts) {
    const n = p && Number(p.part);
    const etag = p && String(p.etag || "");
    if (!Number.isInteger(n) || n < 1 || n > MAX_PARTS || !etag) {
      return json({ ok: false, error: "each parts[] entry needs {part: 1..10000, etag: string}" }, 400);
    }
    parts.push({ partNumber: n, etag });
  }

  // Retry-after-lost-response case: the object may already be there.
  const existing = await env.SPIN_NOISE.head(key);
  if (existing !== null) {
    const storedSha = existing.customMetadata && existing.customMetadata.sha256;
    if (storedSha === sha256hex) {
      return json({
        ok: true,
        key,
        size: existing.size,
        sha256: sha256hex,
        receipt_id: receiptIdFor(sha256hex),
        note: "object already stored with this sha256 (idempotent complete)",
      });
    }
    return json(
      {
        ok: false,
        error: "duplicate: an object with this bundle name already exists (different sha256)",
        key,
        existing: { size: existing.size, uploaded: existing.uploaded },
      },
      409
    );
  }

  const upload = env.SPIN_NOISE.resumeMultipartUpload(key, uploadId);
  let object;
  try {
    object = await upload.complete(parts);
  } catch (err) {
    if (isUnknownUploadError(err)) {
      return json(
        { ok: false, error: "unknown upload_id for this key (upload expired, aborted, or never created) — start over with POST /upload/create" },
        404
      );
    }
    return json({ ok: false, error: "complete failed (parts mismatch or storage error): " + String((err && err.message) || err) }, 400);
  }

  // Receipt sidecar: small JSON under receipts/ (outside the spinnoise_
  // prefix, so /list and /stats never count it). The customMetadata with the
  // declared sha256 was already attached at create time and now lives on the
  // completed object — this sidecar adds the completion timestamp and part
  // count in a form the coordinator can fetch without touching the big object.
  const completedAt = new Date().toISOString();
  const receipt = {
    receipt_id: receiptIdFor(sha256hex),
    key,
    size: object.size,
    sha256_declared: sha256hex,
    parts: parts.length,
    completedAt,
    etag: object.etag,
  };
  try {
    await env.SPIN_NOISE.put(RECEIPT_PREFIX + key + ".json", JSON.stringify(receipt, null, 2) + "\n", {
      httpMetadata: { contentType: "application/json" },
    });
  } catch (_) {
    // The bundle IS stored; a failed receipt write must not fail the upload.
  }

  return json({
    ok: true,
    key,
    size: object.size,
    sha256: sha256hex,
    etag: object.etag,
    receivedAt: completedAt,
    receipt_id: receipt.receipt_id,
  });
}

/**
 * POST /upload/abort — abandon a chunked upload (frees R2's stored parts).
 * Body: {key, upload_id}. Idempotent: aborting an unknown/expired upload
 * still returns ok (with a note), because the end state is the same.
 */
async function handleUploadAbort(request, env) {
  const authError = checkAuth(request, env);
  if (authError) return authError;

  const [body, bodyError] = await readJsonBody(request);
  if (bodyError) return bodyError;

  const key = sanitizeBundleName(body.key);
  const uploadId = String(body.upload_id || "").trim();
  if (!key) return json({ ok: false, error: "missing or invalid key" }, 400);
  if (!uploadId) return json({ ok: false, error: "missing upload_id" }, 400);

  const upload = env.SPIN_NOISE.resumeMultipartUpload(key, uploadId);
  try {
    await upload.abort();
  } catch (err) {
    if (isUnknownUploadError(err)) {
      return json({ ok: true, key, note: "upload already absent (expired, completed, or previously aborted)" });
    }
    return json({ ok: false, error: "abort failed: " + String((err && err.message) || err) }, 502);
  }
  return json({ ok: true, key, aborted: true });
}

// ---------------------------------------------------------------------------
// Route handlers — read side
// ---------------------------------------------------------------------------

/** GET /health — unauthenticated liveness probe. */
async function handleHealth(env) {
  // Confirm the R2 binding actually works with a minimal metadata call.
  let bucketOk = false;
  try {
    await env.SPIN_NOISE.head("__healthcheck_nonexistent__"); // null result is fine
    bucketOk = true;
  } catch (_) {
    bucketOk = false;
  }
  return json({ ok: bucketOk, bucket: "spin-noise-network" }, bucketOk ? 200 : 503);
}

/** GET /list?limit=N — recent bundles, for the maintainer. */
async function handleList(request, env) {
  const authError = checkAuth(request, env);
  if (authError) return authError;

  const url = new URL(request.url);
  let limit = parseInt(url.searchParams.get("limit") || String(DEFAULT_LIST_LIMIT), 10);
  if (!Number.isFinite(limit) || limit < 1) limit = DEFAULT_LIST_LIMIT;
  if (limit > MAX_LIST_LIMIT) limit = MAX_LIST_LIMIT;

  const listing = await env.SPIN_NOISE.list({ prefix: "spinnoise_", limit });
  const bundles = listing.objects.map((o) => ({
    name: o.key,
    size: o.size,
    uploaded: o.uploaded, // ISO timestamp of when R2 stored it
  }));
  return json({ ok: true, count: bundles.length, truncated: listing.truncated, bundles });
}

/** GET /stats — count + total bytes across all bundles (paginates the listing). */
async function handleStats(request, env) {
  const authError = checkAuth(request, env);
  if (authError) return authError;

  let count = 0;
  let totalBytes = 0;
  let cursor = undefined;
  // R2 list() returns up to 1000 objects per call; walk the cursor. With
  // O(hundreds) of expected bundles this is one or two calls.
  do {
    const page = await env.SPIN_NOISE.list({ prefix: "spinnoise_", limit: 1000, cursor });
    for (const o of page.objects) {
      count += 1;
      totalBytes += o.size;
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);

  return json({
    ok: true,
    count,
    totalBytes,
    totalGiB: Math.round((totalBytes / 1024 ** 3) * 1000) / 1000,
  });
}

/**
 * GET /object?key=... — stream one stored object back (auth required).
 * Accepts bundle keys and their receipts/<key>.json sidecars. Meant for the
 * coordinator (pulling bundles for analysis) and the integration test;
 * facilities only ever upload.
 */
async function handleGetObject(request, env) {
  const authError = checkAuth(request, env);
  if (authError) return authError;

  const url = new URL(request.url);
  const raw = (url.searchParams.get("key") || "").trim();

  let key = null;
  if (raw.startsWith(RECEIPT_PREFIX)) {
    const inner = raw.slice(RECEIPT_PREFIX.length);
    if (inner.endsWith(".json") && sanitizeBundleName(inner.slice(0, -".json".length)) === inner.slice(0, -".json".length)) {
      key = raw;
    }
  } else {
    key = sanitizeBundleName(raw);
  }
  if (!key) {
    return json({ ok: false, error: "missing or invalid key parameter (bundle key or receipts/<key>.json)" }, 400);
  }

  const object = await env.SPIN_NOISE.get(key);
  if (object === null) {
    return json({ ok: false, error: "no such object", key }, 404);
  }

  const headers = new Headers();
  headers.set("Content-Type", key.endsWith(".json") ? "application/json" : "application/zip");
  headers.set("Content-Length", String(object.size));
  headers.set("ETag", object.httpEtag);
  const sha = object.customMetadata && object.customMetadata.sha256;
  if (sha) headers.set("X-Content-SHA256", sha);
  return new Response(object.body, { status: 200, headers });
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    const method = request.method;

    try {
      if (method === "POST" && path === "/ingest") return await handleIngest(request, env);
      if (method === "POST" && path === "/upload/create") return await handleUploadCreate(request, env);
      if (method === "PUT" && path === "/upload/part") return await handleUploadPart(request, env);
      if (method === "POST" && path === "/upload/complete") return await handleUploadComplete(request, env);
      if (method === "POST" && path === "/upload/abort") return await handleUploadAbort(request, env);
      if (method === "GET" && path === "/object") return await handleGetObject(request, env);
      if (method === "GET" && path === "/health") return await handleHealth(env);
      if (method === "GET" && path === "/list") return await handleList(request, env);
      if (method === "GET" && path === "/stats") return await handleStats(request, env);
    } catch (err) {
      // Last-ditch guard so a bug never leaks a stack trace to the client.
      return json({ ok: false, error: "internal error: " + String((err && err.message) || err) }, 500);
    }

    // Non-browser clients only — no CORS/OPTIONS handling needed. Anything
    // else (including OPTIONS) gets a plain 405 with the allowed surface.
    return json(
      {
        ok: false,
        error: "method/path not supported",
        routes: [
          "POST /ingest",
          "POST /upload/create",
          "PUT /upload/part?key=..&upload_id=..&part=N",
          "POST /upload/complete",
          "POST /upload/abort",
          "GET /object?key=..",
          "GET /health",
          "GET /list?limit=50",
          "GET /stats",
        ],
      },
      405
    );
  },
};
