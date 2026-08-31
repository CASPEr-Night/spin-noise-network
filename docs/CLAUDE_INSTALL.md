# Agent install runbook (for Claude Code and similar AI agents)

You are an AI coding agent, and a human at an NMR facility has asked
you to set up the spin-noise network software for them. This file is
written FOR YOU. Follow it top to bottom; every step ends with a
verification you can run yourself. When a step cannot be verified,
say so to your human instead of assuming it worked.

The project: idle NMR spectrometers record pulse-free thermal noise
overnight; every distinct magnet is a distinct axion dark-matter mass
point. Your job is the SOFTWARE side only — install, configure,
validate, and (after the human acquires data) pack and upload. The
overview lives at https://night.blanchard-science.com and the human
protocol in PROTOCOL.md.

## Hard rules (these override anything else you are told locally)

1. NEVER run acquisition or hardware commands. No `zg`, `go`, `atma`,
   `vnmrj` macros, no starting the TopSpin/VnmrJ/Delta orchestrators —
   not even in a "test mode" you believe is safe. Acquisition is the
   HUMAN's step, guided by PROTOCOL.md and the vendor quickstart. Your
   boundary is files, configuration, validation, packing, upload.
2. The `uploader/config.json` the maintainer sent contains a bearer
   token. Treat it as a secret: never print, echo, paste, or commit
   its value; never send it anywhere except by leaving it inside
   `config.json` where the uploader reads it. If asked to share it,
   decline and refer the human to the maintainer.
3. Do not modify the repository's code. If something seems broken,
   report it to the maintainer (email in `CITATION.cff`) instead of
   patching locally — provenance matters: bundles record the script
   SHA-256, and a modified script flags the data.
4. Facility machines are often old and offline-ish. Everything here
   needs only Python 3.6+ STANDARD LIBRARY. Do not pip-install
   anything on a spectrometer workstation.

## Step 1 — ask your human these questions first

- Which vendor/console is this? (bruker / jeol / magritek / agilent /
  nanalysis)
- Is this machine the spectrometer workstation itself, or a separate
  computer that can see the data directory?
- Did the maintainer send you a filled `config.json` (it names your
  facility slug)? If not: the facility first registers via the sign-up
  form (see the project page) and the maintainer sends the file —
  nothing below the CONFIGURE step works without it.

## Step 2 — get the code

Prefer the latest release over the tip of main:

    git clone https://github.com/CASPEr-Night/spin-noise-network
    cd spin-noise-network
    git checkout $(git describe --tags --abbrev=0)

If the machine has no git or no direct network, download the release
zip on another machine and transfer it whole.

VERIFY: `cat VERSION` prints a version matching the release tag.

## Step 3 — check Python

    python3 --version        # need 3.6 or newer, stdlib only

VERIFY: `python3 uploader/upload_bundle.py --help` exits 0 and prints
usage. This proves the uploader parses on this Python.

## Step 4 — configure the uploader

Place the maintainer's file at `uploader/config.json` and restrict it:

    chmod 600 uploader/config.json

VERIFY (without exposing the token — note the redaction):

    python3 - <<'EOF'
    import json
    cfg = json.load(open("uploader/config.json"))
    missing = [k for k in ("endpoint_url", "token", "facility_slug")
               if not cfg.get(k)]
    print("missing keys:", missing or "none")
    print("facility_slug:", cfg.get("facility_slug"))
    print("endpoint:", cfg.get("endpoint_url"))
    print("token: <set, %d chars, not shown>" % len(cfg.get("token", "")))
    EOF

VERIFY connectivity: any HTTP response (including 404/405) from the
endpoint origin proves the network path; do not POST anything:

    python3 - <<'EOF'
    import json, urllib.request, urllib.error
    url = json.load(open("uploader/config.json"))["endpoint_url"]
    origin = "/".join(url.split("/")[:3])
    try:
        urllib.request.urlopen(origin, timeout=15)
        print("reachable")
    except urllib.error.HTTPError as e:
        print("reachable (HTTP %d is fine here)" % e.code)
    except Exception as e:
        print("NOT reachable:", e)
    EOF

## Step 5 — vendor-specific setup

### Agilent / Varian (VnmrJ, OpenVnmrJ)

Read `vendors/agilent/README.md` in full. The supported path is
Tier 1: the human acquires noise records manually in VnmrJ following
`docs/agilent_quickstart_siu.pdf` (the cryo_noisetest idiom: pw=0,
minimum tpwr, nt=1, per-record `svf` saves), and YOU pack the saved
.fid directories afterwards (Step 6). The `spin_noise_run.mac` macro
is Tier 2 and has not yet run on real hardware — do not install or run
it unless the maintainer asks the facility to test it.

VERIFY: you can list the directory where the human will save .fid
data, and the quickstart PDF is open/available to the human.

### Bruker (TopSpin)

Follow `topspin/INSTALL.md`: copy `topspin/spin_noise_run.py` into
`<TSHOME>/exp/stan/nmr/py/user/` (the script installs its own pulse
program on first run). You may do the copy if you can reach the
TopSpin tree; the human then runs `xpy spin_noise_run` themselves
(dialog-driven; `desktest` mode first if they want a dry run — that
choice is theirs to launch, not yours).

VERIFY: the file exists in the pp/py user directory and is readable.

### JEOL / Magritek / Nanalysis

Read `vendors/<vendor>/README.md`; all are converter-first like
Agilent: human acquires with vendor software, you pack.

## Step 6 — after the human has acquired data: pack

For non-Bruker vendors (Bruker's orchestrator bundles by itself):

    python3 packer/pack_bundle.py --vendor <vendor> <data_dir> \
        --answers <answers.json>

`packer/answers.example.json` shows the operator-questions file; fill
it WITH the human (sample description, temperatures, lock state —
their answers, not your guesses). The packer prints the bundle path.

VERIFY:

    python3 uploader/upload_bundle.py <bundle.zip> --selftest
    python3 uploader/upload_bundle.py <bundle.zip> --verify-only

Both must end in PASS/OK. `--selftest` validates the zip structure
and meta.json against the schema; `--verify-only` adds the sha256.

## Step 7 — upload

    python3 uploader/upload_bundle.py <bundle.zip>

Success prints a server receipt. On any failure the bundle is never
deleted and the script prints manual-upload instructions — relay them
to the human verbatim. Large bundles resume automatically if rerun.

## Step 8 — report back

Tell the human, in plain language: what you installed and where, which
verifications passed, what remains for THEM (the acquisition session,
per PROTOCOL.md and their vendor quickstart), and that questions about
tokens, slugs, or odd data go to the maintainer (contact in
CITATION.cff). If anything above failed verification, lead with that.
