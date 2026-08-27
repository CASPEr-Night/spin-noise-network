/**
 * forms_forwarder.gs — Google Apps Script that forwards each CASPEr@night
 * facility sign-up (Google Form response) to the spin-noise Worker's
 * POST /registry endpoint, so the coordinator's tools see new facilities
 * without anyone exporting spreadsheets.
 *
 * WHERE THIS LIVES: pasted into the Form's own script editor (open the Form
 * as its owner → three-dot menu → Apps Script), NOT committed anywhere with
 * real values filled in. Full setup walkthrough: docs/REGISTRY.md.
 *
 * WHAT IT DOES
 *   onFormSubmit(e)  trigger target — builds a JSON payload from the form's
 *                    named questions, POSTs it to <ENDPOINT>/registry with
 *                    the registry bearer token, retries once, and on repeated
 *                    failure emails the coordinator with the full payload so
 *                    no sign-up is ever silently lost.
 *   runBackfill()    run MANUALLY once from the editor — iterates every
 *                    response already stored in the Form (submitted before
 *                    the trigger existed) and posts each one. The server is
 *                    idempotent on the payload bytes, so re-running is safe.
 *   sendTestPing()   optional smoke test — posts one synthetic sign-up.
 *
 * SECURITY
 *   - TOKEN below is the REGISTRY_TOKEN — a write-only secret that can do
 *     nothing except add sign-ups. It is deliberately NOT the bundle-upload
 *     token; this script must never hold that one.
 *   - DO NOT COMMIT this file anywhere with the real ENDPOINT/TOKEN values
 *     pasted in. The repository copy carries placeholders only.
 *
 * Contact: John W. Blanchard <jwbquantum@gmail.com>
 */

// ===========================================================================
// CONFIG — fill these two in inside the Form's script editor ONLY.
// DO NOT COMMIT REAL VALUES. The repo copy must keep these placeholders.
// ===========================================================================

var ENDPOINT = "https://spin-noise-ingest.YOUR-SUBDOMAIN.workers.dev"; // Worker origin, no trailing slash
var TOKEN = "PASTE-THE-REGISTRY_TOKEN-VALUE-HERE";                     // REGISTRY_TOKEN secret — never the ingest token

var COORDINATOR_EMAIL = "jwbquantum@gmail.com";  // failure alerts go here
var RETRY_DELAY_MS = 5000;                       // pause before the single retry

// Exact question titles on the live form (proposals/comms_setup_checklist.md).
// Matching is normalized (case/punctuation-insensitive, prefix-based), so
// minor title edits keep working.
var CONSENT_CONTACT_TEXT = "You may contact me about my facility's data";
var CONSENT_MAP_TEXT = "My institution may appear on the public coverage map";

// ===========================================================================
// Trigger entry point
// ===========================================================================

/**
 * Installable trigger target. Set up via the script editor: Triggers (clock
 * icon) → Add Trigger → function onFormSubmit → event source "From form" →
 * event type "On form submit".
 */
function onFormSubmit(e) {
  var payload = buildPayload(e.response);
  postWithRetryOrAlert(payload);
}

/**
 * MANUAL backfill for responses submitted BEFORE the trigger existed
 * (e.g. Boyd Goodson's early sign-up). Run once from the editor:
 * select runBackfill in the toolbar dropdown → Run. Safe to re-run —
 * the server recognizes byte-identical payloads and stores nothing twice.
 */
function runBackfill() {
  var responses = FormApp.getActiveForm().getResponses();
  Logger.log("Backfill: %s stored response(s) to forward.", responses.length);
  var okCount = 0;
  for (var i = 0; i < responses.length; i++) {
    var payload = buildPayload(responses[i]);
    var result = postWithRetryOrAlert(payload);
    Logger.log("  [%s/%s] %s — %s", i + 1, responses.length,
               payload.institution || "(no institution)",
               result ? "forwarded (HTTP " + result + ")" : "FAILED (coordinator emailed)");
    if (result) okCount++;
  }
  Logger.log("Backfill done: %s forwarded, %s failed.", okCount, responses.length - okCount);
}

/** Optional smoke test: posts one clearly-labeled synthetic sign-up. */
function sendTestPing() {
  var result = postWithRetryOrAlert({
    submitted_at: new Date().toISOString(),
    institution: "TEST PING — delete me",
    contact_name: "Forwarder self-test",
    contact_email: COORDINATOR_EMAIL,
    spectrometers: "",
    probes: "",
    city: "Testville",
    country: "Testland",
    city_country_raw: "Testville, Testland",
    heard_via: "sendTestPing()",
    consent_contact: false,
    consent_map: false
  });
  Logger.log(result ? "Test ping stored (HTTP " + result + ")." : "Test ping FAILED.");
}

// ===========================================================================
// Payload construction
// ===========================================================================

/** Normalize a question title for matching: lowercase, alphanumerics only. */
function normTitle(title) {
  return String(title || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

/**
 * Build the /registry JSON payload from one FormResponse (live trigger or
 * backfill — both expose the same API).
 */
function buildPayload(formResponse) {
  var answers = {};  // normalized title -> answer (string or array)
  var itemResponses = formResponse.getItemResponses();
  for (var i = 0; i < itemResponses.length; i++) {
    answers[normTitle(itemResponses[i].getItem().getTitle())] =
        itemResponses[i].getResponse();
  }

  // Prefix-based lookup so "Spectrometer(s)" matches "spectrometer".
  function get(prefix) {
    for (var key in answers) {
      if (key.indexOf(normTitle(prefix)) === 0) return answers[key];
    }
    return "";
  }
  function getStr(prefix) {
    var v = get(prefix);
    return (v == null) ? "" : String(v).trim();
  }

  // "City and country" is ONE short-answer field on the form; split on the
  // LAST comma ("Carbondale, Illinois, USA" -> city "Carbondale, Illinois",
  // country "USA"). Without a comma, both fields carry the raw answer and
  // the coordinator's gazetteer sorts it out; the raw string is kept too.
  var cityCountryRaw = getStr("City and country");
  var city = cityCountryRaw, country = cityCountryRaw;
  var lastComma = cityCountryRaw.lastIndexOf(",");
  if (lastComma > 0) {
    city = cityCountryRaw.slice(0, lastComma).trim();
    country = cityCountryRaw.slice(lastComma + 1).trim();
  }

  // Consent is a checkboxes question: the response is an array of the
  // CHECKED option texts. Unchecked means no.
  var consentRaw = get("Consent");
  var checked = [];
  if (consentRaw && typeof consentRaw !== "string") {
    checked = consentRaw;             // array of selected option strings
  } else if (consentRaw) {
    checked = [consentRaw];
  }
  // Same normalization as question titles (case/punctuation-insensitive,
  // prefix-based), so minor wording tweaks to the checkbox options on the
  // live form can never silently turn a given consent into "no".
  function consentGiven(optionPrefix) {
    var want = normTitle(optionPrefix);
    for (var i = 0; i < checked.length; i++) {
      if (normTitle(checked[i]).indexOf(want) === 0) return true;
    }
    return false;
  }

  return {
    submitted_at: formResponse.getTimestamp().toISOString(),
    institution: getStr("Institution"),
    contact_name: getStr("Contact name"),
    contact_email: getStr("Contact email"),
    spectrometers: getStr("Spectrometer(s)"),
    probes: getStr("Probe(s)"),
    city: city,
    country: country,
    city_country_raw: cityCountryRaw,
    heard_via: getStr("How did you hear about the network"),
    consent_contact: consentGiven(CONSENT_CONTACT_TEXT),
    consent_map: consentGiven(CONSENT_MAP_TEXT),
    forwarder: "forms_forwarder.gs"
  };
}

// ===========================================================================
// Delivery with retry + coordinator alert
// ===========================================================================

/**
 * POST the payload to <ENDPOINT>/registry. One retry after RETRY_DELAY_MS;
 * on repeated failure, email the coordinator with the FULL payload JSON so
 * the sign-up can be replayed by hand (or by runBackfill later).
 * Returns the HTTP status code on success (2xx), or null on failure.
 */
function postWithRetryOrAlert(payload) {
  var body = JSON.stringify(payload);
  var lastDetail = "";
  for (var attempt = 1; attempt <= 2; attempt++) {
    try {
      var resp = UrlFetchApp.fetch(ENDPOINT.replace(/\/+$/, "") + "/registry", {
        method: "post",
        contentType: "application/json",
        headers: { Authorization: "Bearer " + TOKEN },
        payload: body,
        muteHttpExceptions: true
      });
      var code = resp.getResponseCode();
      if (code >= 200 && code < 300) return code;
      lastDetail = "HTTP " + code + ": " + resp.getContentText().slice(0, 500);
    } catch (err) {
      lastDetail = "fetch error: " + err;
    }
    if (attempt === 1) Utilities.sleep(RETRY_DELAY_MS);
  }
  // Both attempts failed — make sure a human sees the sign-up.
  try {
    MailApp.sendEmail(
      COORDINATOR_EMAIL,
      "[spin-noise registry] sign-up forwarding FAILED — manual replay needed",
      "The Google-Form forwarder could not deliver a facility sign-up to " +
      ENDPOINT + "/registry after 2 attempts.\n\n" +
      "Last error: " + lastDetail + "\n\n" +
      "Full payload (replay with curl or runBackfill once the endpoint is healthy):\n\n" +
      JSON.stringify(payload, null, 2) + "\n"
    );
  } catch (mailErr) {
    Logger.log("ALERT EMAIL ALSO FAILED: " + mailErr);
  }
  Logger.log("Forwarding failed (" + lastDetail + "); coordinator emailed.");
  return null;
}
