# Tier-1 pilot runbook — first supervised run on real hardware

Operational script for the remote supervised pilot: we screen-share (or
NoMachine) into the facility's TopSpin workstation while a local colleague
sits at the console. `topspin/spin_noise_run.py` (v0.7.8) has not yet run a
complete session on a real spectrometer. Its live contacts so far: two
unsupervised desktests at Torino (TopSpin 4.4.0) — the first (2026-09-17)
stopped on a namespace bug fixed in v0.7.2; the second (2026-09-18, v0.7.2)
ran to a bundle, with TopSpin's name-only validation of enumerated
parameters forcing the manual `parmode` fallback, fixed in v0.7.3 — a live
attempt with v0.7.3 (2026-09-22) that acquired nothing because the console
never created the F1 parameter file (fixed in v0.7.5), and the first live
run with v0.7.5 (Torino, 2026-09-25): the pulse-free noise block acquired
(89 rows) and the report resolves the spin-noise dip, but both reference
blocks were refused by the console — Bruker's `zg2d` paces its rows with a
delay computed from `d20`, which the script never set, so it was negative.
v0.7.6 replaced `zg2d` with the project's `zgref2d` (one `d1` per row, no
computed delay). Oulu's first live run with it (2026-09-30, Avance III HD
500, AQS DRU-E, TopSpin 3.7.0) then met the receiver unit: every pseudo-2D
block acquired its first 1 MB row and was aborted 12–18 s into the second
(`DRUCONTR ... too much data for the LAN capacity`) while the 64 kB rungs
passed, and TopSpin left full-size `ser` files holding one acquired row
and zeros, which the script took for data. Every Oulu expno ran `DIGMOD`
`baseopt` from the operator's parameter set (16x the points inside the
DRU, per Bruker; Torino's Neo acquired the same rows in `digital`) — the
cause, confirmed on 2026-10-02 when the refused row acquired at the first
try once set to `digital` by hand. v0.7.7 sets `DIGMOD` `digital` / `DSPFIRM` `sharp`
on every experiment, writes each row during a 1 s data-transfer delay
`d11` (insurance), adds the attended **row probe** (expno 17: the row
comes back with data, or a shorter row is found — the safety net) and
checks every block's content afterwards. v0.7.8 reads the mode before
writing it: one `DIGMOD` write per session and no `DSPFIRM` write on a
console that couples the two (the v0.7.7 desktest at Oulu, 2026-10-02,
completed cleanly but popped `GetEnuOrd[DSPFIRM]: enumeration name sharp
not found` once per experiment — harmless, gone). Tier −1 and Tier 0 are
green (`testing/tier0_desktest.md`).

The pilot runs the plain default session only. The optional modes that
exist as of v0.6 (`rdopt`, `sweep`, `AUTOSTEP`) stay OFF — each has its
own validation gate (`docs/autostep_bench_checklist.md` for autostep) and
none is part of a first supervised run.

Roles below: **R** = remote operator (us), **L** = local colleague at the
console. Contact for everything: John W. Blanchard, jwbquantum@gmail.com.

---

## 1. Before the call (T−1 day)

### 1.1 Send the facility

- [ ] Install kit (one email or zip):
  - `topspin/spin_noise_run.py`
  - `topspin/pp/zgnoise2d` and `topspin/pp/zgref2d` (both; the script
    installs them itself where it can write `pp/user`)
  - `uploader/upload_bundle.py` + `uploader/config.example.json`
  - `schema/meta.schema.json` — preserve the repo layout: `schema/` one
    level up from the directory holding `upload_bundle.py` (the uploader
    looks for `../schema/meta.schema.json` relative to itself). If the
    layout differs, pass `--schema <path>` explicitly; otherwise the
    selftest silently skips schema validation (WARN) yet still prints
    `RESULT: PASS`, defeating step 10's check.
  - `PROTOCOL.md` and `topspin/INSTALL.md`
- [ ] The two-directory copy instructions (from `topspin/INSTALL.md`):

  | file | destination |
  |---|---|
  | `spin_noise_run.py` | `<TSHOME>/exp/stan/nmr/py/user/` |
  | `pp/zgnoise2d` | `<TSHOME>/exp/stan/nmr/lists/pp/user/` |
  | `pp/zgref2d` | `<TSHOME>/exp/stan/nmr/lists/pp/user/` |

- [ ] Sample request: **5 mm tube, ~550 µL plain water, with a KNOWN H₂O
      fraction** (tap or distilled is fine; if D₂O-doped, they must know the
      percentage — the H₂O-fraction dialog is the measurement, per
      PROTOCOL.md). Solids/MAS site: a sealed rotor of water instead — §6.
- [ ] The facility's filled `config.json` (endpoint + token) sent
      **privately** by the maintainer (never in the install-kit email) so
      step 11's automatic upload works on the day. The ingest endpoint is
      live; nothing needs deploying.

### 1.2 Ask the facility (answers back before the call)

- [ ] TopSpin version and `<TSHOME>` path (script supports 2.x–4.x; note
      which quirks from §6 apply).
- [ ] Which instrument, and its facility slug: **one slug per instrument**
      — a site with two magnets is two network nodes (e.g. `uni-oulu` and
      `uni-oulu400`); one `config.json` serves both at upload time.
- [ ] Free disk space on the data partition (default run writes roughly
      200 MB: noise ser file ~1 MB/row × ~85 rows for 30 min, plus
      references, the row probe, ladder, and the bundle zip — still ask
      for ≥10 GB free as headroom for reruns and longer noise blocks; a
      DESKTEST rehearsal now writes about as much, its mocked raw data
      having the real size).
- [ ] Console generation, probe (RT / N₂ cryo / He cryo), ATM or manual
      tune/match, sample changer or manual insertion.
- [ ] Any local Python 3 (version — uploader floor is 3.6) on the
      workstation or a nearby machine.

### 1.3 Confirm logistics

- [ ] Remote access method **tested end-to-end** the day before (NoMachine /
      screen share / TeamViewer — whatever the facility allows), including
      who clicks "accept" on their side.
- [ ] Who is physically present at the console for the whole session (name,
      role) — the pilot is supervised on both ends.
- [ ] Session recording consent: ask explicitly; record only if they say yes.
- [ ] Agree the 60–90 min window; magnet reserved for ~2 h to be safe.

---

## 2. Session script (60–90 min)

1. **Greet + scope statement (R, 2 min).** Say exactly what will and will
   not be touched: "The script creates one dataset, `SPINNOISE_<date>`, in
   your current data directory. It runs ordinary tune/match and shimming, a
   1° pulse calibration, four tiny-flip 1D spectra, and a pulse-free noise
   acquisition. Pulsing is limited to the standard pulse calibration and
   ~1° tips at the calibrated observe power — no long or repeated
   high-power irradiation. It never changes instrument configuration, and
   reads/writes nothing outside that dataset plus the three files we
   installed."
2. **Verify environment (L drives, R watches, 5 min).** TopSpin version in
   the title bar matches what they reported; probe string (`edhead` or
   status bar); console (`ii` info / `uxnmr.info` if handy). Note all three
   in the pilot log.
3. **SIMULATE run first (10 min).** With a **1D** ¹H dataset open:
   `xpy spin_noise_run simulate`. Walk the full dialog chain aloud —
   greeting shows `*** SIMULATE MODE ***`, then facility → slug → contact
   consent → sample (H₂O fraction!) → VT → duration → lock → sweep
   confirmation → hardware check → probe type → probe temperatures →
   [only if this console needs it: `parmode` by hand for the first
   pseudo-2D, right here, once] → P90
   confirmation → final notes (the noise block AUTO-STARTS after a 30 s
   status-line countdown; the P90 confirmation is the walk-away point). Confirm the final dialog
   reports a bundle zip path and the zip exists. This proves the dialog
   chain and bundling on *their* TopSpin before anything touches hardware.
4. **DESKTEST run (10 min).** `xpy spin_noise_run desktest`. Watch for the
   mocked-hardware lines (`DESKTEST -> mocked 'atma'`, `'topshim'`,
   `'pulsecal'`, `'rga'`), no manual-fallback dialogs other than a possible
   one-time `parmode` request at the start of setup, no Jython traceback,
   expnos {1, 10, 11, 12, 13, 14, 15, 16} created, bundle produced.
   Do **not** upload simulate/desktest bundles.
5. **Sample in (L, 5 min).** Insert the water tube (or eject via sample
   changer first), set the usual VT setpoint, let it equilibrate a few
   minutes. Record the sample's stated H₂O fraction now, while the person
   who made it is present.
6. **Live run starts:** `xpy spin_noise_run`. Answer the dialogs with real
   values. Two confirmations get narrated by R:
   - **Lock OFF.** The script asks the lock state; turn it off if possible
     (`lock off` / BSMS LOCK key). Narrate why: lock RF can leak into the
     ¹H channel near the water line.
   - **BSMS field sweep OFF.** The script asks you to open `bsmsdisp` and
     physically verify SWEEP is OFF before answering. Narrate why: in 2022 a
     sweeping field smeared the spin-noise feature over kHz and quietly
     contaminated an archival dataset — the script cannot verify this on
     all console generations, so the human confirmation is the safeguard.
     If L cannot confirm, prefer to fix it rather than proceed flagged.
7. **Setup phase (expno 1, ~10 min).** Tune/match (`atma` where present,
   dialog-guided manual flow otherwise — §6), `topshim`, `pulsecal` P90.
   Sanity-check the reported P90 (~7–15 µs typical) before confirming.
   Then the RG ladder (~30 s) and the **row probe** (expno 17, ~1 min when
   the first attempt passes; the script has set `DIGMOD` `digital` at the
   setup expno — `digmod` in TopSpin shows it, and `dspfirm` shows `sharp`
   without the script having written it): watch the status line for `row
   probe: attempt N passed` and note N and the setting in the pilot log —
   a console that needs the second attempt or a shorter row is recorded,
   not at fault; only `WARNING: row probe -- none of the 5 settings` is a
   stop-and-think moment (§6).
8. **Acquisition (~45 min wall clock; operator needed only at the start).**
   Watch the expno progression per the documented plan:

   | expno | what you should see |
   |---|---|
   | 10, 14, 15, 16 | RG ladder: quick 1° 1Ds at RG = 1, 8, 64, max (rga) |
   | 17 | row probe (v0.7.7): `zgnoise2d`, RG 1, 2 rows × ~20 s per attempt; `row probe: attempt N passed` on the status line |
   | 11 | reference_open: `zgref2d`, 1° pseudo-2D, ~170 s of rows (8 × ~21 s at the default row; v0.7.5 used Bruker's `zg2d`, which the console refused at Torino) |
   | 12 | noise block: `zgnoise2d`, no pulse, NS=1/row, RG fixed at max stable, rows fill the chosen duration (~85 rows for 30 min at the default row) |
   | 13 | reference_close: same as 11 |

   During the noise block R and L can chat/debrief — but keep the session
   connected so any dialog is answered immediately.
9. **Bundle creation.** Final-notes dialog → script zips the dataset with
   `meta.json` at the zip root and prints the bundle path
   (`spinnoise_<slug>_<timestamp>Z_<hex>.zip`). Confirm the file exists and
   is nonzero.
10. **Selftest validation on their machine** (any box with Python ≥3.6):
    `python3 upload_bundle.py <bundle.zip> --selftest`
    → must end `RESULT: PASS` (the orchestrator writes schema 1.2,
    which includes the clock-audit block timestamps and remains fully
    valid under the current v2.0 vendor-neutral contract; all sha256
    verified).
11. **Upload (primary).** With the facility's `config.json` (endpoint +
    token, sent privately beforehand) in place next to the uploader:
    `python3 upload_bundle.py <bundle.zip>`. Size is a non-issue: bundles
    over 50 MB automatically take the chunked path (50 MiB parts, up to
    5 GiB) and **resume after any interruption** — if the transfer drops,
    just rerun the same command. Success ends with `RECEIPT: <id>`; note it
    in the pilot log. The uploader never deletes the bundle; the local copy
    stays with the facility either way.
    *No-internet contingency only:* if the workstation (and every nearby
    machine, via USB stick) truly cannot reach the endpoint, email or
    file-transfer the zip to jwbquantum@gmail.com (share link if it exceeds
    mail limits).
12. **Wrap (5 min).** Thank L, confirm what happens next (§5), ask for the
    two-minute "anything that felt wrong?" debrief while it is fresh.

---

## 3. Live verification points

| stage | expected | observed (fill in) |
|---|---|---|
| SIMULATE | full dialog chain, zip created, no errors | |
| DESKTEST | expnos 1,10,14,15,16,11,12,13; mocked-hw lines; no fallback dialogs | |
| Tune/match | atma completes (or manual flow used); wobble curve sane | |
| P90 | pulsecal value plausible for the probe (~7–15 µs at listed power) | |
| RG ladder | RG values 1, 8, 64 accepted; rga returns a max RG without error | |
| Row probe (17) | `row probe: attempt 1 passed` (TD 262144, d11 1.0 s) — or WHICH attempt passed and its setting; no `DRUCONTR` dialog on the passing attempt | |
| Reference (11) | `zgref2d` compiles (no TCube *duration is negative* dialog); no `DRUCONTR` dialog; FID visible on each row; water line where expected | |
| Noise block (12) | rows accumulating at ~21 s/row (row = aq + d1 + d11); RG unchanged; no re-pulse; no `rga: acqt0 not set` warning (v0.7.7's `zgnoise2d` defines `acqt0=0`; on v0.7.6 it was harmless) | |
| Lock/sweep | neither re-enabled at any point (check bsmsdisp again mid-run) | |
| Reference (13) | line position within ~Hz of expno 11 (drift check) | |
| Bundle | zip at printed path; meta.json at zip root; run_mode "live" | |
| Selftest | `RESULT: PASS` on the facility machine | |
| Upload | `UPLOAD OK` + `RECEIPT: <id>` from upload_bundle.py | |

---

## 4. Abort criteria and rollback

**Stop the session** (kill the script window; any acquisition already
started finishes on its own and stays in `SPINNOISE_<date>`) if:

- any hardware command (`atma`, `topshim`, `pulsecal`, `zg`, `rga`) hangs
  with no progress for >5 min;
- any parameter outside the SPINNOISE dataset appears changed, or TopSpin
  shows unexpected configuration prompts;
- the operator (L) is uncomfortable for any reason — no justification
  needed; their console, their call;
- remote connection drops and cannot be restored within ~10 min (L can
  safely let a running acquisition finish, or `stop` it).

**Guarantee:** the run modifies nothing outside the `SPINNOISE_<date>`
dataset directory plus the three installed files. Complete removal, if the
facility wants everything gone:

```
rm -rf <DATADIR>/SPINNOISE_<yyyymmdd>
rm <TSHOME>/exp/stan/nmr/py/user/spin_noise_run.py
rm <TSHOME>/exp/stan/nmr/lists/pp/user/zgnoise2d
rm <TSHOME>/exp/stan/nmr/lists/pp/user/zgref2d
```

(Windows: delete the same four paths in Explorer.) `<DATADIR>` is the data
directory of the template dataset they had open. Nothing else was written.

---

## 5. After the session

- [ ] Within 24 h: run the facility report generator
      (`analysis/facility_report.py`) on the received bundle and read the
      honesty section before sending anything onward.
- [ ] Send the facility the report + a thank-you note (from
      jwbquantum@gmail.com) — include their probe's first point on the
      temperature-contrast curve if the feature is visible.
- [ ] Update the site's running axion-coupling exclusion: run
      `analysis/facility_report.py` on the new bundle with
      `--prior-reports` over the site's earlier `report.json` files (the
      new report then carries `science.axion_exclusion.site_combined`), or
      run `analysis/site_exclusion.py` over all of them — the combined
      curve is the pointwise minimum over sessions under each axis-sign
      hypothesis, so a new session can only tighten or extend it while
      the site's sign-unverified vendors number at most three (a fourth
      switches the combiner to its never-tighter independent-sign
      fallback and the curve can loosen there; the `rule` says so). Where
      the vendor's frequency-axis sign is unverified (Agilent/VnmrJ) the
      headline is the weaker of the two sign hypotheses and the
      `sign_note` quantifies the cost; the sign is taken as one shared
      unknown per vendor — a premise, not a measurement: `sign_premise`
      states it (one console, one acquisition-software convention across
      the site's sessions of that vendor), what breaks it, and the
      headline without it — so one tof-shift test (vendor checklist
      item 2) collapses the site bound to the conditional value quoted
      beside it.
      Read the `coverage_note` with the number: where a session's P_90 is
      fluctuation-driven (statistical term 30% or more, a null block) the
      minimum over N such sessions has coverage toward 0.9^N, not 90%.
      Worst-case, unpublished numbers, far above astrophysical bounds —
      never quote them as more than that.
- [ ] Record lessons in `testing/pilot_notes_<slug>_<date>.md`: every dialog
      that confused L, every timing surprise, every quirk hit from §6, the
      exact TopSpin version string.
- [ ] Any script fix arising from the pilot: bump `SCRIPT_VERSION` to the
      next patch version, keep the `VERSION` file in sync
      (`testing/static_check.py` enforces it), re-run the Tier −1 harness
      (`./testing/run_jython_harness.sh`), tag the commit with the version.
- [ ] Do not name the facility in anything public without their OK.

---

## 6. Contingency appendix

- **TopSpin 2.x/3.x dialog quirks (script guards exist).** Old Jython
  (~2.2 on 2.x) — script avoids all modern syntax; `CURDATA()` returns 5
  elements on ≤3.1 vs 4 on newer (handled in `ds_path()`); some versions
  pop a `parmode` prompt before 2D conversion — answer yes/OK, the dataset
  is fresh; every missing command (`atma`, `topshim`, `pulsecal`, `rga`)
  degrades to an operator dialog rather than crashing.
- **TopSpin 2.x pulse program:** if compilation complains about
  `Avance.incl`, delete the `#include` line in `zgnoise2d` and in
  `zgref2d` (unused in both).
- **`Cannot load line: duration is negative ... In 'zg2d': line 24` at
  expno 11 (script v0.7.5 or earlier):** Bruker's `zg2d` with `d20` = 0;
  Cancel the acquisition-check dialog and install v0.7.6 (`zgref2d`).
  Workaround on an older script only: `d20 21.2` in the template dataset
  before starting. `docs/TROUBLESHOOTING.md` has the arithmetic.
- **`Exception in DRUCONTR 1: Your pulse program produces too much data
  for the LAN capacity. ->Experiment aborted by DRU1!` at expno 11/12/13
  (script v0.7.6 or earlier; Avance III HD, Oulu 2026-09-30):** the
  receiver unit acquired the first 1 MB row of each block and aborted the
  block 12–18 s into the second; the `ser` files it leaves are full-size
  zeros with one acquired row — not data. The cause, confirmed at Oulu
  on 2026-10-02: the acquisition mode (`DIGMOD` `baseopt` on every Oulu
  expno; Torino's `digital` rows acquired; the refused expno 12 of
  `SPINNOISE_20260930_1201` acquired at the first try once set to `1 td`
  1, `digmod` digital by hand); the 30–50 ms write window was the weaker
  one. Install v0.7.8 (`DIGMOD` `digital` set by the script at the setup
  expno, read before written; transfer delay `d11` as insurance, row
  probe as safety net, content checks; `docs/TROUBLESHOOTING.md`). On
  v0.7.8 the row probe walks its ladder
  by itself; if it says `WARNING: row probe -- none of the 5 settings came
  back with data`, let the session finish (it will, without dialogs),
  send the bundle and the console's error text — `software.param_api.
  digmod_form` and the `calibration.row_probe.attempts` list are the
  evidence. `rga: acqt0 not set in pulse program` on the noise block was
  a harmless v0.7.6 warning; v0.7.7's `zgnoise2d` defines `acqt0=0`.
- **`GetEnuOrd[DSPFIRM]: enumeration name sharp not found` dialogs piling
  up, one per experiment (script v0.7.7; TopSpin 3.7.0 at Oulu,
  2026-10-02):** harmless — the console had already coupled `DSPFIRM` to
  `sharp` when `DIGMOD` went `digital`, and refused the script's explicit
  `DSPFIRM` `sharp` (its enum table spells it `sharp(standard)`) with a
  dialog that raised no exception, so the script repeated it on every
  expno. Close them; every dataset is in `digital` / `sharp`. v0.7.8
  reads the mode before writing it and does not write `DSPFIRM` on such
  a console.
- **No ATM probe:** the tune/match step falls back to a dialog — L wobbles
  and tunes manually (`wobb`), then confirms in the dialog. Same for a
  missing `rga`: the script asks L to run rga / set RG manually and type
  the final RG value into the dialog.
- **Sample changer vs manual insertion:** either is fine; do it before
  starting the script (the script never ejects/injects). With a changer,
  make sure the water tube is actually in the magnet, not just in the
  carousel.
- **Facility Python 3 too old for the uploader (floor is 3.6):** run the
  selftest and upload from any other machine — copy the zip on a stick;
  the spectrometer host never needs internet. The uploader is stdlib-only
  by design; do not pip-install anything on their box.
- **`config.json` missing or token rejected:** should not happen — sending
  the filled config privately is a T−1 item (§1.1). If it slipped anyway,
  use the email fallback in step 11; the bundle is never deleted, so nothing
  is lost by uploading later. Zenodo remains the zero-infrastructure archive
  fallback (`server/` docs).
- **Solids/MAS probe (static rotor variant):** the protocol is unchanged —
  only the sample holder differs. Water goes in a sealed rotor (4 mm ≈
  80 µL; prefer the larger rotor for volume), the rotor stays **static**
  (no spinning at any point), and shimming a static sample in an MAS probe
  gives a broader water line than a liquids probe — that is expected and
  the analysis handles it; the references still show the line. Probes
  without a ²H lock channel run unlocked: answer the lock dialog
  truthfully ("no lock available") — it is a recorded state, not a fault —
  and the bracketing references (expnos 11/13) become the drift check, so
  compare them with extra care in §3.
- **Mid-run Jython error dialog:** the run stops but running acquisitions
  finish and all data stays in `SPINNOISE_<date>`; photograph/copy the
  error text — it is pilot gold — and decide with L whether to bundle
  manually (zip the dataset directory) or rerun.
