# Spin-Noise Network

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22100871.svg)](https://doi.org/10.5281/zenodo.22100871)

A community measurement program for nuclear spin noise. Any NMR facility with a Bruker
spectrometer can contribute a data point with one sample tube and one command — and the
bundle contract is now vendor-neutral (schema v2.0), with a standalone packer so JEOL,
Magritek, and Agilent/Varian data can join through converter/scripted paths (see the
vendor-support matrix below): the
protocol measures the spin-noise feature of water (the Guéron absorption dip on
room-temperature probes, the emission bump on cryoprobes) together with the receiver
calibrations needed to interpret it absolutely. Across many facilities this maps the
temperature-contrast law of circuit–spin coupling (McCoy & Ernst 1989; Guéron & Leroy
1989; Hoult & Ginsberg 2001) over the community's probe fleet — and banks
spin-noise-limited records relevant to fundamental-sensitivity and dark-matter
(CASPEr-style) analyses. The science background is in [PROTOCOL.md](PROTOCOL.md).

## For facilities (the 5-minute version)

1. Fill a standard 5 mm tube with plain water (~550 µL). Tap or distilled is fine —
   you'll be asked exactly what it is. **If it contains D₂O, you'll be asked the
   percentage; this matters more than anything else.**
2. Copy `topspin/spin_noise_run.py` into your TopSpin user-python directory and
   `topspin/pp/zgnoise2d` plus `topspin/pp/zgref2d` into your user pulse-program
   directory (details: [topspin/INSTALL.md](topspin/INSTALL.md)). Open any ¹H
   dataset. One facility slug per instrument: a site with two magnets is two nodes.
3. Type `xpy spin_noise_run`, answer the dialogs (institution, sample, run length —
   default ~45 min), stay one more minute for the row probe (the status line says
   which attempt passed), and walk away. The script runs setup → RG ladder → row
   probe → reference blocks → no-pulse noise blocks → closing references, tags
   everything, and leaves a single `spinnoise_<facility>_<timestamp>.zip`.
4. Send it:
   ```bash
   python3 uploader/upload_bundle.py /path/to/spinnoise_*.zip
   ```
   (First time: copy `uploader/config.example.json` to `config.json` and paste the
   endpoint + token from the coordinator. No config? The script prints where to email
   the zip instead.) Size never matters: small bundles go up in one request, big ones
   (overnight runs, up to 5 GiB) automatically switch to a chunked upload that
   **resumes where it left off** if the network drops or the machine reboots — just
   rerun the same command.

**Something not working?** [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
is indexed by symptom and quotes the exact error messages; the first move is
always `python3 uploader/upload_bundle.py --doctor`, which self-diagnoses the
install (Python, config, network path, TLS, clock) with no bundle needed.

**Using Claude Code (or another AI coding agent)?** Point it at
[docs/CLAUDE_INSTALL.md](docs/CLAUDE_INSTALL.md) — an agent-facing runbook that
walks it through install, configuration, validation, packing, and upload, with
verification gates at every step (and hard rules: agents never run acquisition
commands and never expose your upload token). The acquisition session itself
stays yours.

The script never touches your lock/sweep settings silently — it asks you to confirm
the BSMS field sweep is OFF and records your answer. It has a `SIMULATE` flag for a
dry run without touching hardware, and a `DESKTEST` flag that exercises the real
TopSpin API (dialogs, parameters, dataset creation, bundling) with only the hardware
commands mocked — runnable on a free processing-only TopSpin install
([testing/tier0_desktest.md](testing/tier0_desktest.md)).

## Repository layout

| Path | What it is |
|---|---|
| `topspin/spin_noise_run.py` | Jython orchestrator, runs inside TopSpin 2.x–4.x (5.0 untested — reports welcome) |
| `topspin/pp/zgnoise2d` | no-pulse pseudo-2D pulse program for the noise blocks (and the row probe); since v0.7.7 it defines `acqt0=0` (Bruker's pulse-free idiom) and writes each row during a data-transfer delay `d11`, and the script acquires it in `DIGMOD` `digital` — Oulu's Avance III HD, in `baseopt`, had every 1 MB row aborted by its DRU after the first |
| `topspin/pp/zgref2d` | small-flip pseudo-2D pulse program for the reference blocks (Bruker's `zg2d` without the `d20`-computed pacing delay that refused to compile at Torino, v0.7.6) |
| `topspin/INSTALL.md` | install paths, expno map, troubleshooting |
| `analysis/facility_report.py` | the per-facility report (`report.html` + `report.json`) from one bundle; noise rows are streamed, so memory does not grow with the block length (v0.7.4); the clock audit reads each block's pulse program from `pulseprogram` (TopSpin 2.x/3.x) or `pulseprogram.precomp` (TopSpin 4.x Neo consoles, v0.7.6); rows that are all zeros are dropped, counted and FAILed — a refused block leaves a full-size `ser` of zeros on TopSpin 3.x (v0.7.7) |
| `packer/pack_bundle.py` | Python 3 stdlib-only standalone packer: a directory of vendor data files + `answers.json` (the operator questionnaire) → a validated bundle zip, identical in layout to the orchestrator's. Pluggable vendor readers: Bruker implemented (round-trip tested); JEOL/Magritek adapter interface defined |
| `packer/answers.example.json` | the questionnaire template for the packer (same questions as the TopSpin dialogs) |
| `uploader/upload_bundle.py` | Python 3 stdlib-only uploader — auto-selects single-shot vs. chunked-resumable upload by size (+ `--selftest` bundle validator; accepts schema v1.0–v2.0 bundles) |
| `schema/meta.schema.json` | the metadata contract (JSON Schema, v2.0 — vendor-neutral: required `vendor` enum + vendor-namespaced `instrument` blocks; v1.x bundles remain valid, absent vendor = Bruker) |
| `server/` | Cloudflare Worker + R2 ingest endpoint, single-shot + chunked/resumable (maintainer deploys once — `server/DEPLOY.md`) |
| `testing/` | real-Jython harness (`run_jython_harness.sh`), Tier-0 desk-test checklist (`tier0_desktest.md`), `static_check.py`, end-to-end upload test against a local Worker (`test_upload_integration.sh`) |
| `VERSION` | repository release version (mirrored by `SCRIPT_VERSION` in the run script) |
| `PROTOCOL.md` | the science, the operator questions, and why each exists |
| `DATA_POLICY.md` | ownership, permitted uses, co-authorship, embargo, and withdrawal terms |

## Vendor support

| Vendor | Path | Status |
|---|---|---|
| **Bruker** (TopSpin 2.x–4.x; 5.0 untested — reports welcome) | full/automatic: the `topspin/spin_noise_run.py` orchestrator acquires, tags, and bundles everything itself; `packer/pack_bundle.py --vendor bruker` additionally repacks any existing TopSpin expno tree | Desk-tested end to end (real-Jython harness + packer round-trip); first supervised pilot pending |
| **JEOL** (Delta) | converter path: acquire with Delta, then pack the exported data with `packer/pack_bundle.py --vendor jeol`; acquisition automation to be developed with a partner facility | Adapter interface + schema block defined; reader **draft pending partner-facility validation** (.jdf parsing to follow the MIT-licensed [jeolconverter v1.0.1](https://www.npmjs.com/package/jeolconverter) (cheminfo, npm), with attribution) |
| **Magritek** (Spinsolve/SpinsolveExpert) | scripted path: a Prospa-driven acquisition plus `packer/pack_bundle.py --vendor magritek` | Adapter interface + schema block defined; reader **pending bench validation** (file conventions per [nmrglue's spinsolve reader](https://nmrglue.readthedocs.io/en/latest/reference/spinsolve.html)) |
| **Agilent/Varian** (VnmrJ 2.x–3.x, [OpenVnmrJ](https://github.com/OpenVnmrJ)) | converter path: acquire with VnmrJ's ordinary tools (Tier-1 checklist) or the draft MAGICAL macro `vendors/agilent/spin_noise_run.mac`, then pack with `packer/pack_bundle.py --vendor agilent` | Adapter interface + schema block defined; reader **draft pending partner-facility validation** (fid/procpar layout per [nmrglue's varian reader](https://nmrglue.readthedocs.io/en/latest/reference/varian.html), BSD-3; macro constructs verified against OpenVnmrJ manuals + Agilent's shipped `cryo_noisetest`); partner instrument identified (400 MHz DD2, VnmrJ 3.2) — see `vendors/agilent/README.md` |
| **Nanalysis** (NMReady benchtops) | converter-first: acquire with the standard software, export JCAMP-DX FIDs, pack with `packer/pack_bundle.py --vendor nanalysis` | Draft — reader validated against real NMReady exports; partner validation pending (`vendors/nanalysis/`) |

Every vendor lands in the same bundle contract: `meta.json` keeps the physics core
(frequencies, sample, temperatures, timing/clock audit, checksums, software
provenance) vendor-neutral, and everything instrument-specific lives in a
vendor-namespaced `instrument` block. Anything in the JEOL/Magritek/Agilent paths that
could not be verified against real vendor documentation is explicitly marked UNVERIFIED in
code and listed in the partner-session validation checklist at the top of
`packer/pack_bundle.py` — draft vendor code is expected; guessed-but-authoritative
vendor code is not.

## For the coordinator

Deploy the repository once (`server/DEPLOY.md`: five wrangler commands, R2 free tier
covers ~10 GB), set the shared token, and hand facilities the endpoint + token pair.
`GET /list` and `GET /stats` show what has arrived. A zero-infrastructure alternative
(Zenodo community) is documented in the same file.

## Status and known caveats

- **One live session on Bruker hardware so far, not yet a complete one.** The script
  has been executed end-to-end under a real Jython 2.7 interpreter with a stubbed
  TopSpin API modelling twelve console behaviours — simulate and desktest modes, bundle
  validated by the uploader (`testing/run_jython_harness.sh`) — and has completed
  DESKTEST inside TopSpin 4.4.0 at a partner facility (Torino, 17–18 September 2026),
  where two console-specific faults were found and fixed in v0.7.2 and v0.7.3, and
  inside TopSpin 3.7.0 (Oulu, 25 September 2026), where a third — the console did not
  create the F1 parameter file `acqu2` and silently dropped every parameter write into
  the 2D datasets — was found and fixed in v0.7.5 (Torino's first live attempt,
  22 September 2026, v0.7.3, met the same fault and acquired nothing). Torino's first
  live run with v0.7.5 (25 September 2026, Avance Neo 400, TopSpin 4.4.0) acquired the
  pulse-free noise block — 89 rows of 19 s, on which the report resolves the water
  spin-noise dip — but both small-flip reference blocks were refused at `zg`: Bruker's
  library `zg2d` paces its rows with a delay computed from `d20`, which the script never
  set, so the delay came out negative (−21.17 s). Fixed in v0.7.6 by the project's own
  reference pulse program `zgref2d` (`zg2d` without that line). Oulu's first live run
  with v0.7.6 (30 September 2026, Avance III HD 500, AQS DRU-E, TopSpin 3.7.0) met the
  next console-specific fault: every pseudo-2D block acquired its first 1 MB row and
  was then aborted by the receiver unit 12–18 s into the second (`Exception in
  DRUCONTR 1: Your pulse program produces too much data for the LAN capacity`) while
  the 64 kB 1D rungs passed, and TopSpin left full-size `ser` files holding one
  acquired row and zeros, which the script took for data. Every Oulu expno ran
  `DIGMOD` `baseopt` from the operator's parameter set — a mode in which Bruker
  documents 16x the points processed inside the DRU — while Torino's Neo had acquired
  the same rows in `digital`; that is the leading explanation, the 30–50 ms write
  window before `wr` the weaker one. v0.7.7 sets `DIGMOD` `digital` / `DSPFIRM`
  `sharp` on every experiment, writes each row during a 1 s data-transfer delay
  (`d11`, insurance), probes before the references that the row comes back with data
  and shortens it if not (expno 17, decided by the content of the last row — the
  safety net), checks every block's content afterwards, and the report drops and
  FAILs all-zero rows. A complete live session, references included, is still pending.
  The Agilent/VnmrJ path has run three real sessions (SIU Carbondale).
  Every TopSpin call is pinned to
  Bruker's *Python Programming in TopSpin* manual, with operator-dialog fallbacks
  wherever versions differ; the first run at a pilot facility should be supervised. Known soft spots (all degrade to dialogs,
  none fail silently): dataset creation requires a ¹H dataset open at start; 2D
  `PARMODE` switching may prompt on some versions; `pulsecal`/`atma`/`topshim`
  availability varies with TopSpin age.
- Sweep/lock state cannot be commanded portably — it is confirmed by the operator and
  recorded (a hard-won lesson: a field sweep left on smears the line by kHz).
- Bundle size is a non-issue up to 5 GiB: the uploader switches to a chunked,
  resumable upload for anything over 50 MB (50 MiB parts, well inside every
  Cloudflare plan's per-request cap). The zip is always preserved locally either way.

## Provenance

Grew out of analyses of two archival spin-noise datasets (EPFL 600 MHz cryoprobe 2020;
a room-temperature 400 MHz 2022) whose reconciliation required exactly the metadata
this program now records: H₂O fraction, sweep state, RG calibration, probe/coil
temperatures, and same-circuit references.
