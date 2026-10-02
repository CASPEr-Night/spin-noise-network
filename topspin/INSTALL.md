# TopSpin installation — spin-noise network acquisition kit

Three files go onto the spectrometer workstation:

| file | destination |
|---|---|
| `spin_noise_run.py` | `<TSHOME>/exp/stan/nmr/py/user/` |
| `pp/zgnoise2d` | `<TSHOME>/exp/stan/nmr/lists/pp/user/` |
| `pp/zgref2d` | `<TSHOME>/exp/stan/nmr/lists/pp/user/` |

`<TSHOME>` is your TopSpin installation directory, e.g.
`/opt/topspin4.1.4` (Linux), `C:\Bruker\TopSpin4.1.4` (Windows),
`/opt/topspin3.6.5`, etc. If you are unsure, type `set` inside TopSpin
or look at the title bar of the TopSpin window.

The script also **installs both pulse programs automatically** on
first run if it can find your TopSpin directory, so step 2 below is a
belt-and-braces copy — do it anyway if you can. `zgnoise2d` is the
pulse-free noise sequence; `zgref2d` (since v0.7.6) is the small-flip
reference sequence — Bruker's `zg2d` without the delay it computes from
`d20`, which came out negative and refused to compile on Torino's
Avance Neo (2026-09-25). Older scripts used `zg2d` directly.

Since v0.7.7 the script sets the acquisition mode to `DIGMOD` `digital`
/ `DSPFIRM` `sharp` on every experiment it acquires with, both pulse
programs write each row during a dedicated data-transfer delay `d11`
(1 s by default), and a row probe checks before the first reference
block that the row the console is given comes back with data. Why: at
Oulu (Avance III HD 500, AQS DRU-E, TopSpin 3.7.0, 2026-09-30) every
1 MB pseudo-2D row was acquired once and the block then aborted by the
receiver unit 12-18 s into its second row (`DRUCONTR ... too much data
for the LAN capacity`), leaving full-size `ser` files of one row and
zeros. Every Oulu acquisition ran `baseopt` from the operator's parameter
set -- a mode in which Bruker documents 16x the points processed inside
the DRU -- while Torino's Neo acquired the same rows in `digital`; the
mode switch is the fix -- confirmed on Oulu's console on 2026-10-02,
when the refused row acquired at the first try once set to `digital` by
hand -- `d11` the insurance, the probe the safety net
(`docs/TROUBLESHOOTING.md` has the evidence). Since v0.7.8 the script
reads the mode before writing it: one `DIGMOD` write per session, at the
setup expno, and no `DSPFIRM` write on a console that couples the two as
TopSpin 3.7.0 does -- the v0.7.7 desktest at Oulu had popped
`GetEnuOrd[DSPFIRM]: enumeration name sharp not found` once per
experiment (harmless; see the troubleshooting list below).

Works on TopSpin **2.x, 3.x and 4.x** (the script is written for the
embedded Jython interpreter and avoids anything version-specific; every
optional command — `atma`, `topshim`, `pulsecal`, `rga` — degrades to an
operator dialog when missing).

**A note on TopSpin 5.0** (released 2026): TopSpin 5.0 (released 2026, Avance Neo / Fourier 80) has not yet been tested: Bruker documents the Jython layer our script runs in as a standard component alongside the newer CPython interface, so it is expected to work, but we have not verified it — if your console runs TopSpin 5, please tell us what happens (you would be our first).

## Install

1. Copy `spin_noise_run.py` to `<TSHOME>/exp/stan/nmr/py/user/`.
   (Alternative: in TopSpin type `edpy`, use *File → Import…* and pick
   the file — that lands it in the same place.)
2. Copy `pp/zgnoise2d` **and** `pp/zgref2d` to
   `<TSHOME>/exp/stan/nmr/lists/pp/user/`.
3. There is no step 3. No Python packages, no licenses, no network
   access is needed on the spectrometer.

## Run

1. Fill a 5 mm tube with water — tap, distilled, or D2O-doped, whatever
   you have. **You will record what it is; nothing is "wrong".** ~550 µL.
2. Insert the sample and, in this order, **lock, shim, tune** as you
   usually do (lock on), then **lock OFF** and **BSMS field sweep OFF**.
   The script calls `topshim` itself, but `topshim` refuses to run while
   the lock is off (`lock is off: please lock in prior to shimming`) and
   this protocol keeps the lock off, so its shimming dialog will appear:
   you shimmed already — press OK.
3. Open **an existing 1D ¹H dataset** (a PROTON demo set is fine). The
   script only uses it as a parameter template; since v0.7.4 it sets the
   dimensionality of every dataset it creates itself, but a 1D template
   is the clean start. Either template works from v0.7.5: neither
   TopSpin 3.7.0 nor 4.4.0 creates the F1 parameter file when a script
   switches a dataset to 2D (Oulu and Torino, September 2026), so the
   script brings `acqu2` from your template dataset if that is 2D, else
   from another experiment of the session, else from the console's own
   parameter library, and `meta.json` records which
   (`software.param_api.f1_files_source`). In the F1 file it acquires
   with — copied or inherited — it sets `FnMODE` to *undefined*,
   Bruker's rule for a pulse program without an `mc` statement
   (`f1_fnmode_edits` counts the files it edited).
4. In the TopSpin command line type:

   ```
   xpy spin_noise_run
   ```

5. Answer the dialogs (facility, sample, duration, and two **critical**
   confirmations: lock state and **BSMS field sweep OFF** — please
   actually check `bsmsdisp`, this one matters), and press OK at the
   shimming dialog (step 2). The **facility slug** the second dialog
   asks for is **one per instrument**: a site with two magnets is two
   network nodes (e.g. `uni-oulu` and `uni-oulu400`), each with its own
   slug in its own runs. One `config.json` serves both at upload time
   (the uploader only warns when a bundle's slug differs from the
   configured one).
6. Stay for one more minute: after the four gain-ladder rungs the
   **row probe** (v0.7.7, expno 17) acquires a short pulse-free
   pseudo-2D (in digital mode, which the script has set) and checks
   that its last row holds data; on most consoles the first attempt
   passes (~1 min) and the status line says so. A console whose receiver
   unit refuses the default row even so (Oulu's Avance III HD did in
   `baseopt`, 2026-09-30) makes it walk a ladder of a longer transfer
   delay and shorter rows, up to ~5 min, and the row length it settles
   on is recorded in `meta.json` (`calibration.row_probe`) — a console
   property, not a fault. Then walk away. Default total time is ~45 min
   (30 min noise block + setup/references). Overnight option available.
7. A final dialog shows the path of the finished bundle zip
   (`spinnoise_<slug>_<timestamp>_<hex>.zip`) and the one-line upload
   command:

   ```
   python3 uploader/upload_bundle.py <bundle.zip>
   ```

   Run that from the `spin_noise_network` distribution folder on any
   machine with Python 3 (the spectrometer host itself does not need
   internet access — carry the zip on a stick if needed).

## What the run does (expno map)

Dataset `SPINNOISE_<date>_<time>` in your current data directory:

| expno | role |
|---|---|
| 1 | setup: tune/match, shim, P90 calibration |
| 10, 14, 15, 16 | RG ladder: quick 1° 1D at RG = 1, 8, 64, max (rungs 2–4 sit at 14–16 because 11–13 are reserved) |
| 17 | row_probe (v0.7.7): `zgnoise2d`, RG 1, 2+ rows per attempt — finds the row length / transfer delay `d11` the receiver unit accepts; the last attempt's data stay here |
| 11 | reference_open: `zgref2d`, 1° pseudo-2D, ~170 s of rows (8 × ~21 s at the default row) |
| 12 | **noise**: `zgnoise2d`, *no pulse at all*, NS=1/row, RG max stable, rows fill the chosen duration |
| 13 | reference_close: same as 11 |

The row length (TD, default 262144) and the transfer delay (D11,
default 1 s) may differ between consoles; whatever the probe settled on
is what expnos 11–13 were acquired with, and `meta.json` records it. The
acquisition mode is `DIGMOD` `digital` / `DSPFIRM` `sharp` on every expno
(set by the script at the setup expno and carried along by `WR`;
`software.param_api.digmod_form` says whether the console took it and
`dspfirm_form` how `DSPFIRM` got there -- `coupled` on TopSpin 3.7.0,
which moves it by itself).

`meta.json` is written into the dataset directory and into the bundle.

## Desk-testing without a spectrometer

Open the script (`edpy spin_noise_run`) and set `SIMULATE = True` near
the top, or run

```
xpy spin_noise_run simulate
```

All dialogs, dataset bookkeeping, `meta.json` and the zip are exercised;
`zg`, `rga`, `atma`, `topshim`, `pulsecal` are skipped.

For a stronger check on a **processing-only TopSpin install** (free
academic license, no spectrometer), use DESKTEST mode:

```
xpy spin_noise_run desktest
```

DESKTEST runs the *real* TopSpin API calls that are safe without
hardware — the full dialog chain, `GETPAR`/`PUTPAR`, `WR`/`RE` dataset
creation, `meta.json` writing, zip bundling — and mocks only the five
hardware commands. The resulting bundle is flagged
`"run_mode": "desktest"` in `meta.json`; never upload it. The full
step-by-step checklist with pass criteria is
[`../testing/tier0_desktest.md`](../testing/tier0_desktest.md).

## Troubleshooting

- **"No dataset is open"** — open a 1D ¹H dataset first; it is the
  parameter template.
- **The gain-ladder acquisitions run far longer than a few seconds each
  (script v0.7.3 or earlier)** — the dataset you had open was a 2D one, and
  the script's 1D experiments inherited its row count (seen in Torino's
  desktest bundle, 2026-09-21). Stop the run, open a 1D ¹H dataset and start
  again. v0.7.4 switches the setup expno and the rungs to 1D itself.
- **Pulse program not found at zg** — copy `pp/zgnoise2d` and
  `pp/zgref2d` into `<TSHOME>/exp/stan/nmr/lists/pp/user/` by hand and
  rerun; the script will detect them.
- **TopSpin shows `Cannot load line: duration is negative
  (-21166512.000000 us) In 'zg2d': line 24` as the opening reference
  (expno 11) starts (script v0.7.5 or earlier)** — Bruker's `zg2d` paces
  its rows with `"DELTA=d20-((d1+aq)*(ns+ds))-30m"` and the script never
  set `d20`, so the delay was −21.17 s and the console refused to
  compile it; Torino's first live run (2026-09-25) lost both reference
  blocks this way while the noise block acquired. Answer the script's
  *acquisition check* with Cancel and install v0.7.6, whose `zgref2d`
  has no such line. Workaround only, on an older script: set `d20` to
  21.2 s in the template dataset before starting (`d20 21.2`). See
  `docs/TROUBLESHOOTING.md`.
- **TopSpin shows `Exception in DRUCONTR 1: Your pulse program produces
  too much data for the LAN capacity. ->Experiment aborted by DRU1!`
  during a reference or noise block (script v0.7.6 or earlier; Avance
  III HD, TopSpin 3.7.0 at Oulu, 2026-09-30)** — the receiver unit
  acquired the first 1 MB row of each block completely and aborted the
  block 12–18 s into the second row; TopSpin left full-size `ser` files
  of one row and zeros, which the old script took for data. The cause,
  confirmed at Oulu on 2026-10-02: the acquisition mode — every Oulu
  expno ran `DIGMOD` `baseopt` (16x the points inside the DRU, per
  Bruker), Torino's Neo acquired the same rows in `digital`, and the
  refused row acquired at the first try once set to `digital` by hand;
  the 30–50 ms write window before `wr` was the weaker one (the abort
  came mid-row). Install v0.7.8: the
  script sets `DIGMOD` `digital` / `DSPFIRM` `sharp` on every experiment,
  both pulse programs write during a 1 s transfer delay `d11`
  (insurance), the row probe (expno 17) checks before the references
  that the row comes back with data and shortens it if not (safety net),
  and every block is checked for content afterwards. That is what the
  manual check on the old dataset did (expno 12 of
  `SPINNOISE_20260930_1201`, `1 td` → 1, `digmod` → digital, `zg`). See
  `docs/TROUBLESHOOTING.md`.
- **TopSpin shows `GetEnuOrd[DSPFIRM]: enumeration name sharp not found`,
  once per experiment (script v0.7.7; TopSpin 3.7.0 at Oulu,
  2026-10-02)** — harmless: the console had already moved `DSPFIRM` to
  `sharp` when the script set `DIGMOD` `digital`, and refused the
  script's explicit `DSPFIRM` `sharp` (its enum table spells it
  `sharp(standard)`) with a dialog that raised no exception, so the
  script repeated it on every expno. Every dataset was acquired in
  `digital` / `sharp`; close the dialogs. v0.7.8 reads the mode before
  writing it and writes `DSPFIRM` only when the console did not couple.
  See `docs/TROUBLESHOOTING.md`.
- **TopSpin shows `rga: acqt0 not set in pulse program, result may be
  incorrect!` at the noise block (script v0.7.6 or earlier)** —
  harmless: the old `zgnoise2d` defined no time origin; `rga` still
  returned the gain that keeps the pulse-free rows clear of full scale.
  v0.7.7's `zgnoise2d` carries `"acqt0=0"`, Bruker's own idiom for a
  pulse-free acquisition, and the warning is gone. See
  `docs/TROUBLESHOOTING.md`.
- **`parmode` dialog appears** — some TopSpin versions ask before
  converting a dataset to 2D; answer yes/OK (the dataset is fresh, there
  is nothing to lose).
- **TopSpin shows an error about `PARMODE` / `GetEnuOrd`, then the
  script asks you to type `parmode`** — TopSpin validates parameters
  written from a script by name; the old script wrote a number. Script
  v0.7.3 or later writes the documented name, verifies it, and does the
  first switch while you are still at the console (at the start of the
  setup step), so at worst you type `parmode` in TopSpin (or use the
  `eda` toolbar button *Change data dimensionality*), select 2D, once.
  On v0.7.2, do as the dialog says — `parmode`, choose 2D, OK — once;
  TopSpin's own error pop-up (not the script's request) then recurs at
  the noise block and at the closing reference and may need closing. See
  `docs/TROUBLESHOOTING.md`.
- **TopSpin shows `The requested format file is invalid:
  .../SPINNOISE_<date>/<expno>/acqu2: getpar: No such file or directory`
  (script v0.7.4 or earlier; TopSpin 3.7.0 at Oulu and 4.4.0 at Torino,
  both with a 1D template)** — the console accepted the switch to 2D but
  did not create the F1 parameter file `acqu2`, and while it was missing
  every parameter write into that dataset was silently lost, with no
  error reaching the script (the pseudo-2D blocks kept the 1D template's
  TD/RG/pulse program); on 4.4.0 `zg` then refused each block with
  `inconsistent PARMODE 2D ... acqu2` — answer the script's *acquisition
  check* dialog with Cancel in that case. Do not run live with v0.7.4 on
  such a console; v0.7.5 creates the file itself and reads TD and RG
  back after writing. See `docs/TROUBLESHOOTING.md` for what to check in
  `meta.json`.
- **Script window shows a Jython error dialog** — the run stopped, but
  any acquisition already started finishes on its own and all data stays
  in `SPINNOISE_<date>_<time>`. Send the error text to the maintainers.
- **Old TopSpin (2.x)** — everything is written for Jython 2.2-level
  syntax; if `Avance.incl` is missing, delete the `#include` line in
  `zgnoise2d` and in `zgref2d` (neither uses it).

## What we ask you NOT to do

- Don't average scans in the noise experiment (NS must stay 1 per row).
- Don't leave the BSMS field sweep on. (The dialog will nag you. It is
  right to nag you.)
- Don't "improve" the water. Tap water with a described history is more
  valuable than an undocumented perfect sample.
