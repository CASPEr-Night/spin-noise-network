# TopSpin installation — spin-noise network acquisition kit

Two files go onto the spectrometer workstation:

| file | destination |
|---|---|
| `spin_noise_run.py` | `<TSHOME>/exp/stan/nmr/py/user/` |
| `pp/zgnoise2d` | `<TSHOME>/exp/stan/nmr/lists/pp/user/` |

`<TSHOME>` is your TopSpin installation directory, e.g.
`/opt/topspin4.1.4` (Linux), `C:\Bruker\TopSpin4.1.4` (Windows),
`/opt/topspin3.6.5`, etc. If you are unsure, type `set` inside TopSpin
or look at the title bar of the TopSpin window.

The script also **installs `zgnoise2d` automatically** on first run if
it can find your TopSpin directory, so step 2 below is a belt-and-braces
copy — do it anyway if you can.

Works on TopSpin **2.x, 3.x and 4.x** (the script is written for the
embedded Jython interpreter and avoids anything version-specific; every
optional command — `atma`, `topshim`, `pulsecal`, `rga` — degrades to an
operator dialog when missing).

**A note on TopSpin 5.0** (released 2026): TopSpin 5.0 (released 2026, Avance Neo / Fourier 80) has not yet been tested: Bruker documents the Jython layer our script runs in as a standard component alongside the newer CPython interface, so it is expected to work, but we have not verified it — if your console runs TopSpin 5, please tell us what happens (you would be our first).

## Install

1. Copy `spin_noise_run.py` to `<TSHOME>/exp/stan/nmr/py/user/`.
   (Alternative: in TopSpin type `edpy`, use *File → Import…* and pick
   the file — that lands it in the same place.)
2. Copy `pp/zgnoise2d` to `<TSHOME>/exp/stan/nmr/lists/pp/user/`.
3. There is no step 3. No Python packages, no licenses, no network
   access is needed on the spectrometer.

## Run

1. Fill a 5 mm tube with water — tap, distilled, or D2O-doped, whatever
   you have. **You will record what it is; nothing is "wrong".** ~550 µL.
2. Insert the sample and open **any existing ¹H dataset** (a PROTON demo
   set is fine). The script only uses it as a parameter template.
3. In the TopSpin command line type:

   ```
   xpy spin_noise_run
   ```

4. Answer the dialogs (facility, sample, duration, and two **critical**
   confirmations: lock state and **BSMS field sweep OFF** — please
   actually check `bsmsdisp`, this one matters).
5. Walk away. Default total time is ~45 min (30 min noise block +
   setup/references). Overnight option available.
6. A final dialog shows the path of the finished bundle zip
   (`spinnoise_<slug>_<timestamp>_<hex>.zip`) and the one-line upload
   command:

   ```
   python3 uploader/upload_bundle.py <bundle.zip>
   ```

   Run that from the `spin_noise_network` distribution folder on any
   machine with Python 3 (the spectrometer host itself does not need
   internet access — carry the zip on a stick if needed).

## What the run does (expno map)

Dataset `SPINNOISE_<date>` in your current data directory:

| expno | role |
|---|---|
| 1 | setup: tune/match, shim, P90 calibration |
| 10, 14, 15, 16 | RG ladder: quick 1° 1D at RG = 1, 8, 64, max (rungs 2–4 sit at 14–16 because 11–13 are reserved) |
| 11 | reference_open: 1° pseudo-2D, 8 rows × ~19 s |
| 12 | **noise**: `zgnoise2d`, *no pulse at all*, NS=1/row, RG max stable, rows fill the chosen duration |
| 13 | reference_close: same as 11 |

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

- **"No dataset is open"** — open any ¹H dataset first; it is the
  parameter template.
- **Pulse program not found at zg** — copy `pp/zgnoise2d` into
  `<TSHOME>/exp/stan/nmr/lists/pp/user/` by hand and rerun; the script
  will detect it.
- **`parmode` dialog appears** — some TopSpin versions ask before
  converting a dataset to 2D; answer yes/OK (the dataset is fresh, there
  is nothing to lose).
- **Script window shows a Jython error dialog** — the run stopped, but
  any acquisition already started finishes on its own and all data stays
  in `SPINNOISE_<date>`. Send the error text to the maintainers.
- **Old TopSpin (2.x)** — everything is written for Jython 2.2-level
  syntax; if `Avance.incl` is missing, delete the `#include` line in
  `zgnoise2d` (it is not used).

## What we ask you NOT to do

- Don't average scans in the noise experiment (NS must stay 1 per row).
- Don't leave the BSMS field sweep on. (The dialog will nag you. It is
  right to nag you.)
- Don't "improve" the water. Tap water with a described history is more
  valuable than an undocumented perfect sample.
