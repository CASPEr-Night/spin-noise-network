# Tier-0 desk test — spin_noise_run.py under a processing-only TopSpin

Purpose: exercise everything in `topspin/spin_noise_run.py` that does not
need a spectrometer, on a plain desk machine, before the first supervised
pilot run. Tier 0 proves the dialog chain, parameter handling, dataset
creation, `meta.json` writing and zip bundling against a **real TopSpin
interpreter** — the one environment the console stubs cannot imitate. The
five hardware commands (`atma`, `topshim`, `pulsecal`, `zg`, `rga`) are
mocked inside `safe_hw_cmd()` and nothing else is.

Time needed: ~1 h once, of which ~40 min is the TopSpin download.

---

## Tier −1: real-Jython harness (no TopSpin needed)

Before Tier 0, run the script under a **real Jython 2.7 interpreter** with
a stubbed TopSpin API — no TopSpin installation required, ~30 s total.
This executes `topspin/spin_noise_run.py` **unmodified** end to end
(`testing/jython_entry.py` registers `testing/topspin_stub.py` as the
`TopCmds` module, so the script runs its `IN_TOPSPIN` code paths: real
`java.util.zip` bundling, real `java.security` SHA-256, real `jarray`
buffers, and unicode dialog strings exactly as TopSpin's embedded Jython
delivers them). Requires `jython` (2.7.x) and `python3` on PATH.

One command runs everything:

```
./testing/run_jython_harness.sh
```

or the two mode runs individually:

```
jython -Dpython.path=testing testing/jython_entry.py simulate
jython -Dpython.path=testing testing/jython_entry.py desktest
```

Pass criteria (the wrapper enforces all of them; exit code 0 = pass):

1. Each mode run ends with `HARNESS simulate: PASS` /
   `HARNESS desktest: PASS` — meaning, per run: no unscripted dialog, no
   hardware-guard breach (`XCMD`/`ZG` never reached), no crash `ERRMSG`,
   no abort, the full expno tree **1, 10, 14, 15, 16, 11, 12, 13**, the
   pulse program installed, `meta.json` written twice with
   `run_mode` equal to the mode (so the bundle can never pass as data)
   and a real `sha256:<64 hex>` script self-fingerprint, and a bundle
   zip readable back through `java.util.zip.ZipFile`.
2. Each produced bundle passes the repository validator:
   `python3 uploader/upload_bundle.py <bundle.zip> --selftest`
   → `RESULT: PASS` (schema validation + SHA-256 verification of every
   data file — java-written digests checked by Python's hashlib).
3. `python3 testing/static_check.py` → `ALL CHECKS PASSED`.

What Tier −1 proves: the script parses and **executes** under real
Jython 2.7 (not just a Python-3 compile proxy), the java interop
(zip/digest) works, the SIMULATE/DESKTEST flows run every line to the
final dialog, and non-ASCII operator input survives to a valid
`meta.json`. What it cannot prove: the real TopSpin API objects and
version quirks (Tier 0) and the hardware commands (Tier 1).

---

## 1. Get a local TopSpin (processing-only, free academic license)

1. Go to the Bruker website → *Service & Support → Software Downloads →
   TopSpin* and register for a download account (any institutional or
   personal academic address works).
2. Download **TopSpin 4.x** for your OS (Windows, Linux, or macOS). Any
   4.x release is fine; 4.1.4 or later is what the network's facility
   docs assume.
3. During installation choose the **"Data processing only"** option — no
   spectrometer configuration, no acquisition license.
4. License class: Bruker's **free academic/non-profit TopSpin license**
   (processing-only). Request or activate it through the license step of
   the installer / Bruker's license portal (CodeMeter-based on current
   releases). Commercial desks need a paid processing license instead —
   the test is identical either way.
5. Start TopSpin once and note `<TSHOME>` (e.g. `/opt/topspin4.1.4`,
   `C:\Bruker\TopSpin4.1.4`) from the title bar.

## 2. Copy the two files in

| file (from this repo) | destination |
|---|---|
| `topspin/spin_noise_run.py` | `<TSHOME>/exp/stan/nmr/py/user/` |
| `topspin/pp/zgnoise2d` | `<TSHOME>/exp/stan/nmr/lists/pp/user/` |

(`edpy` → *File → Import…* also works for the script; the script offers
to install the pulse program itself, but copy it anyway.)

## 3. Open a 1H demo dataset

A processing-only install ships example data. Any 1D ¹H dataset works —
it is only a parameter template.

1. In TopSpin: *File → Open* (or `re`) and open the ¹H example set (on
   4.x installs typically found under `<TSHOME>/examdata`, e.g.
   `exam1d_1H/1/1`). If your install has no examdata, open any Bruker
   1D ¹H dataset you have lying around — contents are irrelevant.
2. Confirm the dataset is current (its name shows in the data window
   title).

## 4. Run SIMULATE mode

```
xpy spin_noise_run simulate
```

SIMULATE skips the hardware blocks entirely (it does not even walk the
tune/shim/pulsecal call chain). Answer the dialogs with the test values
below. Expect:

- [ ] Greeting dialog shows `*** SIMULATE MODE ... ***`.
- [ ] All operator dialogs appear, in order: facility (institution /
      city / country / email) → facility slug → contact consent →
      sample → VT setpoint → duration → lock state → BSMS sweep
      confirmation → hardware check → probe type → probe temperatures →
      P90 confirmation → noise-block start notice → final notes.
- [ ] Terminal shows `SIMULATE -> ...` / `SIMULATE: zg mocked ...` lines
      and **no** errors.
- [ ] Final dialog reports the bundle path.

Suggested dialog answers (used in the pass criteria below): institution
`Desk Test Lab`, slug `desktest-lab`, sample `distilled water`, H2O
`100`, D2O `0`, duration `30 min (default)`, lock `OFF`, sweep
`Yes — SWEEP is OFF`.

## 5. Run DESKTEST mode

```
xpy spin_noise_run desktest
```

DESKTEST is the actual Tier-0 test: it drives the **real** TopSpin API —
the full `INPUT_DIALOG`/`SELECT`/`CONFIRM` chain, `GETPAR`/`PUTPAR` on
live parameter sets, `WR`/`RE` dataset creation, `meta.json` writing and
java-zip bundling — and mocks **only** the hardware commands, inside
`safe_hw_cmd()`. Answer the dialogs with the same test values. Expect:

- [ ] Greeting dialog shows `*** DESKTEST MODE ... ***`.
- [ ] The setup phase runs *through* the tune/shim/pulsecal code path:
      terminal shows `DESKTEST -> mocked 'atma'`, `... mocked 'topshim'`,
      `... mocked 'pulsecal'`, later `... mocked 'rga' (RG=101)` (twice) — and no
      manual-fallback dialog appears for any of them (the mock reports
      success, as a working command would).
- [ ] The pulse program `zgnoise2d` is written to
      `<TSHOME>/exp/stan/nmr/lists/pp/user/` (or the script confirms it
      is already there).
- [ ] Each acquisition step prints `DESKTEST: zg mocked (...)` — no
      "cannot see a raw-data file" dialog.
- [ ] No Jython traceback anywhere.

## 6. Pass criteria (all must hold, DESKTEST run)

1. **Expno tree as documented** (`topspin/INSTALL.md` expno map): a
   dataset `SPINNOISE_<yyyymmdd>` exists in the current data directory
   containing expnos **1, 10, 14, 15, 16, 11, 12, 13** — each a `WR`
   copy of the template (real `acqus` etc.; no `ser`/`fid` for the
   mocked acquisitions is expected and correct).
2. **meta.json written twice**: once in the `SPINNOISE_<date>` dataset
   directory, once inside the bundle staging dir — and it contains a
   `software` object with `"script_version": "0.1.0"`,
   `"schema_version": "1.1"`, `"script_sha256"` equal to either
   `sha256:<64 hex>` or `"unavailable"`, and `"run_mode": "desktest"`.
3. **Bundle zip produced**:
   `SPINNOISE_<date>/spinnoise_desktest-lab_<YYYYMMDD_HHMMSS>Z_<4hex>.zip`,
   openable by any zip tool, with `meta.json` at the zip root and the
   expno directories under `data/`.
4. **Selftest validator passes.** Copy the zip to any machine with
   Python 3 and run, from the repo root:

   ```
   python3 uploader/upload_bundle.py <bundle.zip> --selftest
   ```

   Required output: `OK   : meta.json (schema_version 1.1) validates
   against meta.schema.json`, `OK   : verified sha256 of N data
   file(s).` and `RESULT: PASS`.
5. **Static checks green** (no TopSpin needed):

   ```
   python3 testing/static_check.py
   ```

   must end with `ALL CHECKS PASSED`.

**Never upload a desk-test bundle to the production endpoint.** It
contains copies of the demo dataset, not noise data; `meta.json` flags it
(`run_mode: desktest`), but don't rely on the flag — just don't send it.

## Known Tier-0 limitations (what this does NOT test)

- The five hardware commands themselves, acquisition timing, and RG
  behavior — that is Tier 1, the supervised pilot-facility run.
- `parmode` 2D conversion prompts on some TopSpin versions (dialog
  fallback exists; DESKTEST exercises the parameter writes but a
  processing-only install may accept them silently).
- `uxnmr.info` console detection (demo datasets may lack the file; the
  hardware-check dialog covers it).
