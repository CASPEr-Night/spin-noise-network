# Tier-1 pilot runbook — first supervised run on real hardware

Operational script for the remote supervised pilot: we screen-share (or
NoMachine) into the facility's TopSpin workstation while a local colleague
sits at the console. This is the first time `topspin/spin_noise_run.py`
(v0.2.0, the current `VERSION`) touches a real spectrometer; Tier −1 and Tier 0 are already green
(`testing/tier0_desktest.md`).

Roles below: **R** = remote operator (us), **L** = local colleague at the
console. Contact for everything: John W. Blanchard, jwbquantum@gmail.com.

---

## 1. Before the call (T−1 day)

### 1.1 Send the facility

- [ ] Install kit (one email or zip):
  - `topspin/spin_noise_run.py`
  - `topspin/pp/zgnoise2d`
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

- [ ] Sample request: **5 mm tube, ~550 µL plain water, with a KNOWN H₂O
      fraction** (tap or distilled is fine; if D₂O-doped, they must know the
      percentage — the H₂O-fraction dialog is the measurement, per
      PROTOCOL.md).
- [ ] Ingest endpoint deployed (HANDOFF step 6, ~10 min) and the endpoint +
      token sent to the facility **privately** (not in the install-kit email)
      so step 11's automatic upload works on the day.

### 1.2 Ask the facility (answers back before the call)

- [ ] TopSpin version and `<TSHOME>` path (script supports 2.x–4.x; note
      which quirks from §6 apply).
- [ ] Free disk space on the data partition (default run writes roughly
      200 MB: noise ser file ~1 MB/row × ~95 rows for 30 min, plus
      references, ladder, and the bundle zip — still ask for ≥10 GB free
      as headroom for reruns and longer noise blocks).
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
   reads/writes nothing outside that dataset plus the two files we
   installed."
2. **Verify environment (L drives, R watches, 5 min).** TopSpin version in
   the title bar matches what they reported; probe string (`edhead` or
   status bar); console (`ii` info / `uxnmr.info` if handy). Note all three
   in the pilot log.
3. **SIMULATE run first (10 min).** With any ¹H dataset open:
   `xpy spin_noise_run simulate`. Walk the full dialog chain aloud —
   greeting shows `*** SIMULATE MODE ***`, then facility → slug → contact
   consent → sample (H₂O fraction!) → VT → duration → lock → sweep
   confirmation → hardware check → probe type → probe temperatures → P90
   confirmation → noise-start notice → final notes. Confirm the final dialog
   reports a bundle zip path and the zip exists. This proves the dialog
   chain and bundling on *their* TopSpin before anything touches hardware.
4. **DESKTEST run (10 min).** `xpy spin_noise_run desktest`. Watch for the
   mocked-hardware lines (`DESKTEST -> mocked 'atma'`, `'topshim'`,
   `'pulsecal'`, `'rga'`), no manual-fallback dialogs, no Jython traceback,
   expno tree 1, 10, 14, 15, 16, 11, 12, 13 created, bundle produced.
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
8. **Acquisition (~45 min wall clock; operator needed only at the start).**
   Watch the expno progression per the documented plan:

   | expno | what you should see |
   |---|---|
   | 10, 14, 15, 16 | RG ladder: quick 1° 1Ds at RG = 1, 8, 64, max (rga) |
   | 11 | reference_open: 1° pseudo-2D, 8 rows × ~19 s |
   | 12 | noise block: `zgnoise2d`, no pulse, NS=1/row, RG fixed at max stable, rows fill the chosen duration (~95 rows for 30 min) |
   | 13 | reference_close: same as 11 |

   During the noise block R and L can chat/debrief — but keep the session
   connected so any dialog is answered immediately.
9. **Bundle creation.** Final-notes dialog → script zips the dataset with
   `meta.json` at the zip root and prints the bundle path
   (`spinnoise_<slug>_<timestamp>Z_<hex>.zip`). Confirm the file exists and
   is nonzero.
10. **Selftest validation on their machine** (any box with Python ≥3.6):
    `python3 upload_bundle.py <bundle.zip> --selftest`
    → must end `RESULT: PASS` (the current schema — 1.2, which
    includes the clock-audit block timestamps — validates, all sha256
    verified).
11. **Upload (primary).** With the ingest Worker deployed (see HANDOFF step
    6 — deploy it BEFORE the pilot) and the facility's `config.json` filled
    with the endpoint + token sent privately beforehand:
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
| Reference (11) | FID visible on each row; water line where expected | |
| Noise block (12) | rows accumulating at ~20 s/row; RG unchanged; no re-pulse | |
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
dataset directory plus the two installed files. Complete removal, if the
facility wants everything gone:

```
rm -rf <DATADIR>/SPINNOISE_<yyyymmdd>
rm <TSHOME>/exp/stan/nmr/py/user/spin_noise_run.py
rm <TSHOME>/exp/stan/nmr/lists/pp/user/zgnoise2d
```

(Windows: delete the same three paths in Explorer.) `<DATADIR>` is the data
directory of the template dataset they had open. Nothing else was written.

---

## 5. After the session

- [ ] Within 24 h: run the facility report generator
      (`analysis/facility_report.py`; if it is not yet in the repo at pilot
      time, write it against this bundle — the pilot bundle is its first
      test case) on the received bundle.
- [ ] Send the facility the report + a thank-you note (from
      jwbquantum@gmail.com) — include their probe's first point on the
      temperature-contrast curve if the feature is visible.
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
  `Avance.incl`, delete the `#include` line in `zgnoise2d` (unused).
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
- **Ingest not deployed / no token:** should not happen — deploying the
  Worker before the pilot is a T−1 item (HANDOFF step 6; ~10 minutes). If
  it slipped anyway, use the email fallback in step 11. Zenodo remains the
  zero-infrastructure archive fallback (`server/` docs).
- **Mid-run Jython error dialog:** the run stops but running acquisitions
  finish and all data stays in `SPINNOISE_<date>`; photograph/copy the
  error text — it is pilot gold — and decide with L whether to bundle
  manually (zip the dataset directory) or rerun.
