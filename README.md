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
| `analysis/facility_report.py` | the per-facility report (`report.html` + `report.json`) from one bundle; noise rows are streamed, so memory does not grow with the block length (v0.7.4); the clock audit reads each block's pulse program from `pulseprogram` (TopSpin 2.x/3.x) or `pulseprogram.precomp` (TopSpin 4.x Neo consoles, v0.7.6); rows that are all zeros are dropped, counted and FAILed — a refused block leaves a full-size `ser` of zeros on TopSpin 3.x (v0.7.7); the Bruker reference tip angle reads the channel power from `PLW` before the legacy `PL` array (whose 120 dB is TopSpin 3.x's 'never set' sentinel), and the clock audit models TopSpin 3.x's auto-inserted `dccorr`, corrects script-recorded expectations for the console's rounded `SW_h`, and reports a significant offset beyond 1e-5 as 'expectation model incomplete' rather than as a console-clock verdict (v0.7.9); v0.8 makes the wind-aware halo construction the exclusion headline (the standard-halo wind at its actual direction for the site and the UTC row times, vertical-bore B₀), keeps the wind-parallel worst case as a robustness line and adds the sidereal-modulation fit — see *The facility report* below |
| `packer/pack_bundle.py` | Python 3 stdlib-only standalone packer: a directory of vendor data files + `answers.json` (the operator questionnaire) → a validated bundle zip, identical in layout to the orchestrator's. Pluggable vendor readers: Bruker implemented (round-trip tested); JEOL/Magritek adapter interface defined |
| `packer/answers.example.json` | the questionnaire template for the packer (same questions as the TopSpin dialogs) |
| `uploader/upload_bundle.py` | Python 3 stdlib-only uploader — auto-selects single-shot vs. chunked-resumable upload by size (+ `--selftest` bundle validator; accepts schema v1.0–v2.0 bundles) |
| `schema/meta.schema.json` | the metadata contract (JSON Schema, v2.0 — vendor-neutral: required `vendor` enum + vendor-namespaced `instrument` blocks; v1.x bundles remain valid, absent vendor = Bruker) |
| `server/` | Cloudflare Worker + R2 ingest endpoint, single-shot + chunked/resumable (maintainer deploys once — `server/DEPLOY.md`) |
| `testing/` | real-Jython harness (`run_jython_harness.sh`), Tier-0 desk-test checklist (`tier0_desktest.md`), `static_check.py`, end-to-end upload test against a local Worker (`test_upload_integration.sh`), synthetic bundles (`make_synthetic_bundle.py` for the schema; `make_physics_bundle.py` for an injected spin-noise line with a known clock offset and, from v0.8, session times, site, injected sidereal modulation and laboratory cycle), and the report tests `test_report_qa_flags.py`, `test_report_bruker_refs.py`, `test_sweep_report.py`, `test_agilent_report.py`, `test_site_exclusion.py` and `test_wind_sidereal.py` (v0.8, two tiers — see *The facility report*) |
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

## The facility report

`python3 analysis/facility_report.py <bundle.zip> --out <dir>` turns one bundle into
`report.html` and `report.json`: the spin-noise line (sign, depth, width), the receiver
calibrations, the clock audit, a QA table (OK / WARN / FAIL rows) and an "honesty" list
that spells out the construction behind every number. Every exclusion number in it is
UNPUBLISHED and preliminary — an internal sensitivity bookkeeping, far above the
astrophysical bounds, not a detection claim and not a publication-grade limit — and the
report says so wherever one is printed. What v0.8 adds:

**Wind-aware exclusion headline.** Through v0.7 the axion-coupling bound bracketed the
dark-matter wind: "parallel to B₀" for the worst case and "perpendicular to B₀" for the
nominal case. Neither is an uncertainty. In the standard halo model the laboratory's
velocity through the halo is known at every instant (the Sun's 232.6 km/s plus the
Earth's 29.79 km/s orbit), and a vertical-bore magnet has B₀ along the local vertical, so
the angle between the wind and B₀ follows from the site's coordinates and the UTC time of
each noise row. The v0.8 headline (`curve.g90_wind_conservative`,
`result.g90_wind_conservative_best_gev_inv`) is the 2020 pilot's construction with the
wind at its actual direction for this site and these times: every calibration systematic
at its limit-weakening extreme (the pilot's derating envelope `D_cal`, reused
unmeasured), damping at the broader of the measured widths, the whole |PSD − baseline|
line power attributed to a putative signal, one-sided Student-t statistics. Beside it the
report prints the `D_cal = 1` companion at the same wind (`g90_wind_nominal`) and keeps
the wind-parallel worst case as a robustness line ("even with the wind along B₀ the bound
is g < X"). Every v0.7 key keeps its name, position and meaning — the v0.8 keys are
additive.

**`halo.wind_model` and its fallbacks.** The block records what the construction used:
the Sun's galactic velocity and the Earth's orbital speed, the apex (RA, Dec), the site
and where its coordinates came from (`site.basis`: `operator` from
`facility.latitude_deg` / `longitude_east_deg`, else `registry_city` from the registry's
city gazetteer in `analysis/registry_report.py`), the B₀ orientation and its basis
(`recorded`, or `default_nmr`: an absent field is read as vertical for Bruker, Agilent
and JEOL bundles and as unknown for Magritek, Nanalysis and unrecognised vendors, whose
benchtop Halbach fields are transverse to the bore at an unknown azimuth), how the rows
were placed in UTC (`rows_timed_basis`: with a clock audit, `per_row` from
`clock_audit.blocks[].row_started_offsets_ms` once a writer records it, else
`block_spread` over each block's `wall_start_ms`/`wall_end_ms`; without a clock audit
(Agilent bundles, pre-1.2 Bruker bundles), `started_local` + `local_timezone_offset_min`
per experiment: `per_row` when every noise experiment holds one row (the VnmrJ protocol;
all four SIU reports today), `block_spread_local` between `started_local` and
`finished_local`, else `block_spread_estimated` from the row count; `tz_basis` says
whether that offset came from the operator's answers, from the packing machine's zone,
or is unrecorded (`unknown`, as in every bundle written so far)), the number of rows timed, the wind-angle range and mean, ⟨sin²θ⟩, the
session-mean |v_lab| and the 2° angle bins. When an input is missing the block is
replaced by `halo.wind_model_unavailable` = `orientation` | `coordinates` | `timing`, no
`g90_wind_*` key is written, the headline stays the v0.7 worst-parallel construction and
a QA WARN says so.

**Sidereal-modulation fit** (`science.sidereal_modulation`; HTML heading
"Sidereal-modulation fit (preliminary, internal; not a detection claim)"). As the Earth
turns, the wind angle to a vertical B₀ changes, and with it the power an axion signal
would put into the spin line — while the thermal spin-noise line and the receiver floor
do not depend on sidereal time. The fit regresses the per-row line power on the template
f(t) the halo model predicts for this site and these hours, P_k = P_SN + P_a·f(t_k): the
constant term absorbs the spin-noise line, so only the modulated fraction of a putative
signal is tested. That is a bound which needs no spin-noise subtraction and which,
unlike the entire-line-power construction, improves with measurement time. The per-row
power is a linear fixed-shape estimator (`per_row[].line_power_fixed_shape_counts2`: the
amplitude of the session's headline line shape solved per row), not the free per-row
fit, whose width rails on weak rows. What a session's hours buy is recorded as
`template.template_std` and `template.leverage` = √n·std(f): a 12 h run across the
template's fall from peak to trough gives std(f) ≈ 0.16–0.20, the Oulu overnight run
0.08, a 3 h daytime run 0.03. The fit is skipped, with `available` false and one of
exactly these `skip_reason` strings, when the exclusion is unavailable
(`exclusion_unavailable`), when the wind model is absent
(`wind_model_unavailable: orientation` | `coordinates` | `timing`), or when fewer than
30 rows carry a line power or std(f) < 0.02
(`no modulation leverage in this session's hours`). The object carries
the fixed `label` "sidereal-modulation fit, preliminary and internal --- one-sided 90% CL
bound on the modulated excess under the standard halo at this site's wind angles; not a
detection claim" and its own `honesty` list: the construction, the leverage these hours
gave, the rows clipped, the `solar_degeneracy` flag — `unbroken`, `regressed`, or
`inseparable` (a 24 h laboratory cycle is degenerate with the template within one
session; the smoothed per-row floor is fitted as a regressor against it, the
collinearity is recorded, and when it cannot be separated the flag says so instead of
claiming it was regressed out), that the bound scales with time, and the ratio to the
SN1987A bound. It is not a detection claim: none of the fitted P_a on the network's
sessions is significant.

**Site combiner** (`--prior-reports`, `science.axion_exclusion.site_combined`,
`analysis/site_exclusion.py`). When every session of the site carries
`curve.g90_wind_conservative`, the site headline is the minimum over sessions of that
curve, with the `D_cal = 1` companion combined the same way; the worst-parallel curve is
always combined alongside as the robustness line. When any session lacks the wind curve
(a report predating v0.8, or a session whose wind model was unavailable) the headline
falls back to the v0.7 worst-parallel combination, unchanged, and `site_combined.rule`
and `headline_basis` say which.

**Optional `meta.json` fields the v0.8 analysis reads** (all optional, none required, no
schema-version bump): `facility.latitude_deg`, `facility.longitude_east_deg` (east
positive) and `facility.coordinates_basis` (`operator` | `registry_city`);
`spectrometer.b0_orientation` (`vertical` | `horizontal` | `unknown`; the TopSpin script
writes `vertical`) and `spectrometer.b0_azimuth_deg` for a horizontal field;
`local_timezone_offset_basis` (`answers` | `packing_machine`, written by the packer); and
`clock_audit.blocks[].row_started_offsets_ms`, one entry per row — requested, not yet
written by any writer, so until then the analysis spreads the rows evenly over the
block's wall time. No writer records coordinates yet: the gazetteer resolves the
registered city, and a measured position goes in at analysis time through
`--meta-override`.

**Synthetic bundles and tests.** `testing/make_physics_bundle.py` writes a bundle with a
known injected line (`--feature bump|dip|none`, `--amp`, `--fwhm`) and a known clock
offset; v0.8 adds timed sessions (`--noise-rows`, `--start-utc`, `--row-cadence-s`,
`--tz-offset-min`), a site (`--site LAT,LON` or `--city` from the gazetteer),
`--b0-orientation` / `--b0-azimuth-deg`, an injected sidereal modulation
(`--inject-modulation P_A[@OFFSET_HZ]`, following the report's own Monte-Carlo template,
with the equivalent P_a per offset written to `injection_truth.modulation`) and a 24 h
laboratory cycle (`--lab-cycle-pct`, `--lab-cycle-peak-utc`). `testing/test_wind_sidereal.py`
(v0.8) has two tiers. The unit tier loads `analysis/facility_report.py` and
`analysis/halo_wind.py` by path and checks the lineshape at θ = 90° and 0° against the
released perpendicular and parallel calls bit for bit, the solar apex, the |v_lab|
extremes and the sidereal angle ranges of the network's sites (seconds). The
synthetic-bundle tier runs the report on generator bundles. By default (what CI and
`run_jython_harness.sh` execute, about 3 minutes) it checks that an injected modulation
on a 240-row timed Oulu bundle is recovered within 1.5 sigma, that the untimed 16-row
bundle is skipped with `wind_model_unavailable: coordinates`, that the available and the
skipped report both carry the heading and the honesty lines, and that `site_exclusion.py`
falls back to `g90_worst` when one session lacks the wind keys; `--full` (about 13
minutes) adds the null, a laboratory cycle separable from the template (regressed out)
and one peaking at the session start (reported as inseparable), the orientation skip and
the two-wind-session combiner. Both tiers use generator bundles
only — no facility data.

## For the coordinator

Deploy the repository once (`server/DEPLOY.md`: five wrangler commands, R2 free tier
covers ~10 GB), set the shared token, and hand facilities the endpoint + token pair.
`GET /list` and `GET /stats` show what has arrived. A zero-infrastructure alternative
(Zenodo community) is documented in the same file.

## Status and known caveats

- **Three complete live sessions on Bruker hardware (Oulu, 5–6 October 2026, v0.7.8), including a 9.5 h overnight run.** The script
  has been executed end-to-end under a real Jython 2.7 interpreter with a stubbed
  TopSpin API modelling thirteen console behaviours — simulate and desktest modes, bundle
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
  the same rows in `digital`; that was the leading explanation, the 30–50 ms write
  window before `wr` the weaker one — and on 2 October 2026 the operator at Oulu
  confirmed it on the hardware: expno 12 of the refused run, set to `digital` by hand,
  acquired at the first try. v0.7.7 sets `DIGMOD` `digital` / `DSPFIRM`
  `sharp` on every experiment, writes each row during a 1 s data-transfer delay
  (`d11`, insurance), probes before the references that the row comes back with data
  and shortens it if not (expno 17, decided by the content of the last row — the
  safety net), checks every block's content afterwards, and the report drops and
  FAILs all-zero rows. v0.7.8 reads the mode before writing it — one `DIGMOD` write per
  session, no `DSPFIRM` write on a console that couples the two, as TopSpin 3.7.0 does:
  the v0.7.7 desktest at Oulu (2 October 2026) completed cleanly but popped the
  console's own `GetEnuOrd[DSPFIRM]: enumeration name sharp not found` dialog once per
  experiment (harmless, and gone). With v0.7.8 the University of Oulu then ran the
  first complete Bruker sessions — 30 min (85 noise rows), 3 h (513 rows) and 9.5 h
  overnight (1710 rows), references and RG ladder included, on 5–6 October 2026 — and
  the reports of those bundles exposed two defects in the *analysis*, not the
  acquisition, both fixed in v0.7.9: the Bruker reference tip angle read the channel
  power from the legacy `PL` array, which TopSpin 3.x leaves at its 120 dB
  'never set' sentinel while the real power sits in `PLW` (the 1° small flip came out
  as 1.8×10⁻⁵°, the exclusion 5.7×10⁴ too strong); and the clock audit's pulse-program
  parser refused the `dccorr` statement TopSpin 3.x inserts into every stored
  program, so every block fell back to the script's recorded expectation — `AQ` from
  the requested 6900 Hz, not the console's 6893.38 Hz — and a 10⁻³ 'console-clock
  offset' was declared conclusive. The audit now says 'expectation model incomplete'
  for any significant offset no OCXO can have, and quotes the per-block excess
  (about 2–3 s per block plus a few ms per row of receiver/transfer overhead at Oulu)
  that remains unmodelled.
  The Agilent/VnmrJ path has run three real sessions (SIU Carbondale).
  Every TopSpin call is pinned to
  Bruker's *Python Programming in TopSpin* manual, with operator-dialog fallbacks
  wherever versions differ; the first run at a pilot facility should be supervised. Known soft spots (all degrade to dialogs,
  none fail silently): dataset creation requires a ¹H dataset open at start; 2D
  `PARMODE` switching may prompt on some versions; `pulsecal`/`atma`/`topshim`
  availability varies with TopSpin age.
- **v0.8 is an analysis release.** The acquisition script changes by one constant
  (`spectrometer.b0_orientation: vertical`); the report gains the wind-aware exclusion
  headline and the sidereal-modulation fit described above. Both have been run on the
  Oulu and SIU sessions and on synthetic bundles with an injected modulation (recovered
  within one sigma, null on spin noise alone); no real session shows a significant
  modulation. Every bound they print is UNPUBLISHED, an internal sensitivity
  bookkeeping; what has not been tested is the response to a real signal.
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
