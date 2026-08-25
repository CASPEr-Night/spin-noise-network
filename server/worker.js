/**
 * spin-noise-ingest — Cloudflare Worker for the spin-noise network central repository.
 *
 * Receives zipped Bruker experiment bundles from community NMR facilities
 * (uploaded by uploader/upload_bundle.py) and stores them in an R2 bucket.
 *
 * Routes:
 *   POST /ingest          store one bundle (auth required)
 *   GET  /health          liveness check (no auth)
 *   GET  /list?limit=50   recent bundle keys + sizes (auth required)
 *   GET  /stats           bundle count + total bytes (auth required)
 *
 * Design notes (kept deliberately boring — this must run unattended for years):
 *   - No external dependencies. Plain ES-module Worker, one file.
 *   - Auth is a single shared bearer token stored as the INGEST_TOKEN secret.
 *     Facilities are trusted collaborators; the token only gates spam/abuse.
 *   - The request body is streamed straight into R2 (request.body is a
 *     ReadableStream) — the Worker never buffers the zip in memory.
 *   - Integrity: the uploader sends X-Content-SHA256. We hand that digest to
 *     R2's put() `sha256` option; R2 computes the hash of the received bytes
 *     and REJECTS the put on mismatch, so a corrupted transfer never lands.
 *     (R2PutOptions.sha256 accepts a hex string — see
 *     developers.cloudflare.com/r2/api/workers/workers-api-reference/)
 *   - Privacy: we record the uploader's COUNTRY (request.cf.country) in
 *     customMetadata, never the connecting IP address.
 *
 * Practical size limits (important, documented in DEPLOY.md too):
 *   - We formally reject anything over 2 GiB.
 *   - Cloudflare's edge enforces a per-plan request-body cap FIRST:
 *     Free/Pro 100 MB, Business 200 MB, Enterprise 500 MB (413 at the edge,
 *     this Worker never sees the request). A typical bundle (a few noise
 *     pseudo-2D ser files) is tens to a few hundred MB — overnight runs can
 *     exceed 100 MB, in which case the facility uses the manual-upload
 *     fallback printed by upload_bundle.py (or the maintainer upgrades plans).
 */

// ---------------------------------------------------------------------------
// Tunables
// ---------------------------------------------------------------------------

const MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024; // 2 GiB hard ceiling
const MAX_KEY_LENGTH = 200;                      // generous; real keys ~55 chars
const DEFAULT_LIST_LIMIT = 50;
const MAX_LIST_LIMIT = 500;

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
 * Sanitize + validate the bundle name from X-Bundle-Name into an R2 key.
 * Returns the key string, or null if the name is unacceptable.
 * We are strict on purpose: the key namespace is flat and shared by every
 * facility, so nothing that could smuggle a path or odd character gets in.
 */
function sanitizeBundleName(raw) {
  if (!raw) return null;
  // Strip any path components someone might have left in (defensive; the
  // uploader sends a bare basename).
  const base = raw.split("/").pop().split("\\").pop().trim();
  if (base.length === 0 || base.length > MAX_KEY_LENGTH) return null;
  if (!KEY_PATTERN.test(base)) return null;
  return base;
}

// ---------------------------------------------------------------------------
// Route handlers
// ---------------------------------------------------------------------------

/** POST /ingest — store one bundle. */
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
    return json({ ok: false, error: "Content-Length required (chunked uploads not accepted)" }, 411);
  }
  if (contentLength > MAX_BUNDLE_BYTES) {
    return json({ ok: false, error: `bundle exceeds ${MAX_BUNDLE_BYTES} bytes (2 GiB) limit` }, 413);
  }

  // Integrity header. The uploader always sends the zip's SHA-256 as lowercase
  // hex; accept an optional "sha256:" prefix for hand-rolled curl uploads.
  let sha256hex = (request.headers.get("X-Content-SHA256") || "").trim().toLowerCase();
  if (sha256hex.startsWith("sha256:")) sha256hex = sha256hex.slice(7);
  if (!/^[0-9a-f]{64}$/.test(sha256hex)) {
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
  });
}

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

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    try {
      if (request.method === "POST" && path === "/ingest") return await handleIngest(request, env);
      if (request.method === "GET" && path === "/health") return await handleHealth(env);
      if (request.method === "GET" && path === "/list") return await handleList(request, env);
      if (request.method === "GET" && path === "/stats") return await handleStats(request, env);
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
        routes: ["POST /ingest", "GET /health", "GET /list?limit=50", "GET /stats"],
      },
      405
    );
  },
};
