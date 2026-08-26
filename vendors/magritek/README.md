# Magritek Spinsolve port (Work Package B)

**Status: DRAFT PENDING BENCH VALIDATION.** The macro has never run on a
Spinsolve. However, a documentation-verification pass (2026-08-26, log below)
checked it construct-by-construct against Magritek's own SpinsolveExpert
manuals and real SpinsolveExpert macros/output found in public repositories;
most of the original `UNVERIFIED` marks are now resolved with citations, the
macro was restructured into the *documented* SpinsolveExpert automation-script
shape, and the remaining marks are genuinely bench-only (checklist below).
The Python side (reader, packer adapter, synthetic-data generator) is tested
end-to-end against the bundle contract, and its two file-format parsers have
now also been checked against a real Spinsolve experiment folder (see log).
Maintainer contact: jwbquantum@gmail.com.

## Contents

| file | what it is | status |
|---|---|---|
| `spin_noise_run_spinsolve.mac` | SpinsolveExpert automation script (draft) | doc-verified 2026-08-26; needs bench session |
| `magritek_reader.py` | acqu.par + .1d parser and bundle packer adapter | tested against synthetic data |
| `make_synthetic_magritek_data.py` | builds a fake Spinsolve session for testing | working |
| `test_magritek_chain.sh` | synthetic session → pack → uploader `--selftest` | working, run it |
| `BENCHTOP_FEASIBILITY.md` + `benchtop_feasibility.py` | the physics memo and its calculation | done |

## Why a benchtop port at all

Spinsolve instruments sit at 43 / 60 / 80 / 90 (and 100) MHz ¹H — permanent
Halbach magnets at 1.0–2.1 T, always at field, no cryogens, thousands
deployed in teaching and industrial labs. In axion-mass coordinates that is
0.18–0.37 μeV, beginning just above (within ~7% of) the previous
highest-mass nuclear-spin search at 0.166 μeV. The physics memo in this directory shows the expected
water spin-noise feature on a benchtop is a *large* uniform-temperature
Guéron dip (radiation-damping dominated, depth plausibly 40–75%), i.e. a
benchtop is potentially the easiest place in the entire network to see spin
noise — and the cleanest calibration anchor for the temperature-contrast law,
because coil, sample, and magnet share one regulated temperature.

## What maps 1:1 from the Bruker/TopSpin protocol

The science protocol survives the port unchanged:

- **Block structure**: setup → receiver-gain ladder → opening small-flip
  reference → N no-pulse noise acquisitions → closing reference. Same roles,
  same `meta.json` role names (`setup`, `rg_ladder`, `reference_open`,
  `noise`, `reference_close`).
- **Operator questions**: facility, sample composition (H₂O fraction — the
  2022 lesson applies with full force), temperature, lock state, run
  duration, contact/consent. Six questions, same as TopSpin.
- **Answers file**: the macro writes `answers.json` in the session directory;
  the packer merges it with the parsed acquisition parameters. This is the
  same division of labor as `spin_noise_run.py` (dialogs → meta) on Bruker.
- **Bundle contract**: `magritek_reader.py` emits schema **2.0** bundles
  (`vendor: "magritek"`, `instrument.magritek` block) validated by
  `uploader/upload_bundle.py --selftest`; upload chain, checksums, filename
  convention (`spinnoise_<slug>_<stamp>_<4hex>.zip`) all unchanged.
- **Interleaved references** as the drift check, and the receiver-gain
  ladder as the linearity check — both concepts port directly (gain is
  `rxGain` in dB here instead of Bruker's linear RG; see mapping below).

## What differs on a Spinsolve (and why no cable detach exists or is needed)

- **No transmitter cable to detach.** On the 2020 Bruker pilot the TX cable
  was physically detached to guarantee zero transmitter leakage, and the
  detach events visibly pulled the tuning state. A benchtop has no user
  -accessible TX path — probe, magnet, and electronics are one sealed box.
  The equivalent guarantee is expressed in software: the noise block either
  uses a compiled pulse program containing **no pulse event at all**
  (`useNoPulseSeq = "yes"`; whether the compiler accepts a pulse-free event
  list is bench item 3), or — the default, runnable day one — the stock
  Proton experiment with `90Amplitude1H = -85`, which the Pulse Programming
  Guide documents as **"no power"** (V1.40 p10; the amplitude scale is
  −85…0 dB). The residual excitation (~1e-5 deg tip with the 0.5 µs minimum
  pulse) is logged in `answers.json` as a built-in micro-reference — the
  2022 attenuator lesson applied. No detach also means no detach-induced
  tuning-state steps, removing the pilot's main line-motion systematic.
- **No BSMS, no deuterium lock — but there IS a lock.** Spinsolve uses an
  *external hardware lock*: a separate reference sample and channel built
  into the magnet, no D₂O needed (vendor-documented across the family). The
  Bruker failure mode "field sweep left running" (the 2022 kHz-smearing
  lesson) has no direct equivalent; the analogous questions here are
  (i) whether the external lock actively steps B₀ during a noise block and
  (ii) how the magnet temperature regulation moves the line between blocks.
  Both are recorded (`environment.locked`, operator notes) and both are
  exactly what the interleaved references measure. The documentation pass
  found that programmatic lock control **exists**: Magritek's own experiment
  macros call `ucsUtilities:suspendLock()` / `resumeLock()` around every
  acquisition (real V2.02.27 macro), i.e. the lock control loop is normally
  *suspended while acquiring* — encouraging for noise blocks. **Bench session
  must still confirm** this holds for long acquisitions run via `RunExpt`,
  whether the lock channel emits RF near the ¹H band, and whether the lock
  can stay frozen across an entire noise series (checklist item 5).
- **Permanent magnet thermal drift** replaces superconducting-magnet
  stability. Spinsolve regulates magnet temperature; residual drift between
  references is a fit parameter, not a surprise.
- **Receiver gain** is `rxGain` in dB (name confirmed in a real Spinsolve
  acqu.par, where `rxGain = 70` was observed). Legal values are a
  model-specific **discrete menu** (`$gData->rxGainMenu$` in the stock
  interface files), so the macro's gain parameters use that same menu and
  the hard-coded ladder gains [0, 20, 40, 60] must be snapped to menu values
  at the bench (checklist item 4). The bundle stores Bruker-comparable
  *linear amplitude* gain `rg = 10^(rxGain_dB/20)` per experiment and the
  raw dB value in `instrument.magritek.rx_gain`.
- **N separate 1D noise acquisitions** instead of one pseudo-2D: Prospa
  saves each acquisition as a folder (`acqu.par` + `data.1d`), so the noise
  block is N repeated experiments, each a `meta.json` `experiments[]` entry
  with role `noise`. The packer preserves acquisition order via `expno`.
- **Console clock audit**: same wall-clock-vs-acquisition-duration trick as
  TopSpin in principle. Prospa has a verified `time()` function (used as a
  seconds-valued clock in real Kea and SpinsolveExpert macros: `time(0)` /
  `t1 = time()` / `while(time()-t1 < …)`); the macro records per-block
  start/end from it and the packer forwards them. Its epoch and resolution
  are undocumented — bench item 7. SpinsolveExpert's own experiment folders
  are named `yymmdd-hhmmss …` (User Manual App. A), giving a 1 s absolute
  anchor if needed.

## Two automation paths (research summary)

1. **SpinsolveExpert + Prospa automation script (the path this port takes).**
   SpinsolveExpert is Magritek's scripting-capable software: pulse-program
   editing/compilation plus the Prospa macro language (the same language as
   Magritek Kea consoles). The User Manual (V1.41 §4 "Automation using a
   script") documents exactly the mechanism this port needs: a script macro
   with entry/`interface`/`getPlotInfo`/`backdoor` procedures, added to the
   Expert menu by drag-and-drop, which runs experiments with
   `(result, acqPar) = RunExpt(protocol, ["name = value", …])` — parameter
   overrides per call, `saveData` control, and the time-domain data returned
   in `result->tData`. The macro now has that documented shape. Only this
   path can express a no-pulse acquisition and direct `rxGain` control.
2. **Standard Spinsolve software remote API** (XML messages over TCP;
   protocols like `SHIM` with `CheckShim`/`QuickShim`/`PowerShim` options and
   `1D EXTENDED+`, verified against an open-source autosampler client). This
   path can automate shimming and reference spectra from a workstation
   Python script **but cannot run a no-pulse acquisition** — standard
   protocols always pulse. It remains useful as a setup/reference fallback
   on facilities without an Expert license; noise blocks require Expert.

## Operator flow (target: one bench visit, then unattended runs)

1. Fill a standard 5 mm tube with water (~550 μL); record what it is.
2. Load sample, run the standard shim (`QuickShim` is fine; record result).
3. In SpinsolveExpert: add the folder containing
   `spin_noise_run_spinsolve.mac` to the menu (drag-and-drop onto the menu
   bar — documented, User Manual §4.2), select it, fill in the six-question
   parameter list (or accept the defaults pre-edited in the entry
   procedure), press Run, confirm the summary dialog. Optionally first
   create the `SpinNoiseNoPulse` experiment (copy the Proton experiment
   files, edit the pulse program to the acquire-only body given in the
   macro header, compile) and set `useNoPulseSeq = "yes"`; the default path
   needs no compilation.
4. The macro runs ladder → reference → N noise blocks → reference, writing
   each block to the session directory plus `answers.json`. Default session
   ~45 min; operator needed for the first ~5.
5. On any machine with Python 3:
   `python3 vendors/magritek/magritek_reader.py pack <session_dir>` →
   `spinnoise_*.zip`; then the normal
   `python3 uploader/upload_bundle.py <zip>`.

## File-format facts used by the reader (with provenance)

- Session data files: `acqu.par` (acquisition parameters) and `data.1d`
  (FID) per experiment folder; `spectrum.1d`/`spectrum_processed.1d` and
  `.pt1` files are processed products and are ignored by the packer.
  [Sources: nmrglue `fileio/spinsolve.py` (BSD-3); SpinsolveExpert User
  Manual V1.41 App. A ("data.1d (MNova compatible data files)"); real
  V2.02.27 experiment folders.]
- `acqu.par` is plain text, one `key = value` per line; values in double
  quotes are strings, otherwise int-then-float-then-string. [nmrglue;
  **confirmed against a real Spinsolve-written acqu.par**, 2026-08-26 log
  below.] Real files can repeat keys (`duration` appeared 4×); the reader's
  last-value-wins behavior matches a sequential read.
- `.1d` binary layout: 32-byte header of eight 4-byte little-endian fields
  `[owner, format, version, dataType, xDim, yDim, zDim, qDim]`, then IEEE
  float32 little-endian payload; for a 1D acquisition of N complex points
  the payload is 3N floats — N x-axis values followed by interleaved
  re/im pairs. [nmrglue; **confirmed on a real data.1d** — the reader's
  `inspect` reports `structure_ok` with `n_complex_points` matching the
  file's `nrPnts`.] The header magic words decode as ASCII `PROS`, `DATA`,
  `V1.1`, with `dataType = 504` for a 1D complex FID (observed, not
  vendor-documented); the reader still checks structural consistency
  rather than magic values.
- Parameter names and units are now confirmed twice over: `b1Freq` (MHz),
  `rxGain` (dB), `rxPhase`, `nrPnts`, `nrScans`, `dwellTime` (µs),
  `bandwidth` (kHz), `lowestFrequency`, `rxChannel`, plus `acqTime` (ms),
  `experiment`, `expName`, `dataDirectory`, `softwareVersion`, `specID`,
  `specType` — all present in a real Spinsolve acqu.par; and the Pulse
  Programming Guide V1.40 (p11) explicitly defines `dwellTime` as "the
  sampling interval in microseconds", `acqTime` in ms, `bandwidth` in kHz
  (= 1000/dwellTime). The reader still parses leniently and records what
  it finds verbatim.

## Documentation verification log (2026-08-26)

The original plan assumed an expert human reviewer for the draft macro; none
was available, so public documentation became the reviewer. Every construct
in `spin_noise_run_spinsolve.mac` was checked against the sources below; the
macro was then rewritten in the documented SpinsolveExpert automation-script
shape. No Prospa syntax below is invented: every construct in the macro now
either appears verbatim in a cited source or carries an `UNVERIFIED(n)` mark
keyed to the bench checklist.

### Sources

- **S1 — SpinsolveExpert User Manual V1.41** (Magritek, Sept 2020; PDF in
  the public repo `github.com/the-iron-ryan/MQST_Winter2023_Lab`). Used: §3
  (batch list, WaitTime/StartAtTime, loops), §4 (automation scripts: script
  template with entry/`interface`/`getPlotInfo`/`backdoor` procedures;
  `RunExpt` example with `nrPnts`/`repTime`/`nrScans`/`dwellTime`/
  `pulseLength1H`/`saveData` overrides; `result->tData`; `assignstruct`;
  `mergelists`/`getsublist`/`list()`; `ucsFiles:saveAcquPar`/`savePlot`/
  `saveMNovaData`; menu drag-and-drop install), Appendix A (data storage:
  `yymmdd-hhmmss Protocol (comment)` folders; acqu.par + data.1d + proc.par
  contents), Appendix B (plot layout lists, `listto2d`).
- **S2 — SpinsolveExpert Pulse Programming Guide V1.40** (Magritek, March
  2020; same repo). Used: pulse-program commands (App. A: `initpp`/`endpp`,
  `acquire(mode, points[, duration])`, `delay` (2 µs–327 ms), `wait`
  (2 µs–167 s), `pulse`, `txon`/`txoff`, `loop`/`endloop`, `cleardata`,
  `setrxfreq`/`settxfreq`); variable-class prefixes (aXXX amplitude in dB,
  **range −85 (no power) to 0 (max)**; dXXX µs; nXXX integer; pNNN phase);
  interface control types (tb/tm/cb/rb/bt/dv) and datatypes (`string`,
  `float,[a,b]`, `integer`, `pulselength` 0.5–1000 µs, `pulseamp`,
  `reptime`; checkbox `"no,yes"` convention) (pp. 9–10); the Proton
  relationships table naming `90Amplitude1H`/`pulseLength1H` and defining
  `dwellTime` (µs), `acqTime` (ms), `bandwidth` (kHz) (p. 11); the
  run-sequence (§5: the framework creates the data folder and writes
  acqu.par before `backdoor` runs; `execpp` argument conventions); `ucsRun`
  helper list (App. C); Prospa language notes (App. D: Matlab-like, 500
  built-ins listed by `listcom`, F1 help, matrix syntax).
- **S3 — Real SpinsolveExpert V2.02.27 output + Magritek stock macros**
  (`github.com/fionnf/Nmr-simple-plotter`, experiment folders dated
  2026-07-15, 80 MHz system SPA4236): a genuine Spinsolve-written
  `acqu.par` (all parameter names/units above; `rxGain = 70` observed;
  `softwareVersion = "2.02.27"` machine-readable), a genuine `data.1d`
  (verified with `magritek_reader.py inspect`: `structure_ok`, header magic
  ASCII `PROS`/`DATA`/`V1.1`, dataType 504), a real `proc.par` whose
  on-disk lines match the `save()` string-list call in the macro that wrote
  it (verifying `save()` as the text-writer), and Magritek's autogenerated
  V5 experiment macros (`Fluorine1D.mac`, `_interface.mac`, `_pp.mac`):
  `gExpt->addExperiment`, `gSeq->initAndRunPP`, `ucsUtilities:isLockEnabled/
  suspendLock/resumeLock`, `gData->getXChannelParameters("19F")` with
  `PulseLength_19F`/`PowerLevel_19F`, `$gData->rxGainMenu$`/`nrPntsMenu`/
  `dwellTimeMenu` interface menus, `assignlist`, `struct()`, `->` access,
  `round()`, `getlistindex`, `isfile`/`load`/`rmfile`, `cd`, string
  interpolation in list items. (The .mac comments show local edits, but the
  architecture is Magritek's autogenerated form and the data alongside it
  is real instrument output.)
- **S4 — `github.com/migjet492/Thesis_mig40`** (`Chapter5_WaitTime_StopFlow
  .mac`, a real Expert batch command): script entry with 5-argument
  `gExpt->addExperiment(getmacropath(), name, parameters, ctrlLayout,
  plotLayout)`, 5-column `ctrlLayout` row, `backdoor(parameters)` +
  `assignstruct`, `time()` as a seconds clock, `pause()`, `while/endwhile`,
  `wvExpStatus == "stop"` abort check, `print`, serial I/O.
- **S5 — `github.com/murbanczyk/cat_on_mouse`** (Kea Prospa MOUSE macros,
  incl. Magritek-style UF sequences): **`query("Title","Message …")`
  two-argument form returning `"yes"`/`"no"`** (twice), `mkdir(name)` /
  `mkdir("$expNr$")` / `cd` / `getcwd()`, numbered per-experiment folders,
  `save("GUI.par", guipar)`, `time(0)`/`t1 = time()`, `message(…,"info")`
  and `message(…,"error")`, `size()`, vector element assignment
  (`dur[0] = …`), `depthArray[z]` indexing, `import1d`, `eval`, `scanstr`.
- **S6 — `github.com/Greerm2/Hand-Held-MRI`** (Kea imaging macro):
  procedure definitions with default arguments and multi-value
  `endproc(a,b,c)` returns, `(x,y) = :proc()` calls, `elseif`, single-line
  `try; …; catch; endtry`, `for … next` with expression bounds, 2D list
  literals, `message("Error","…\r…","error")`, expression interpolation
  (`"$128*1024/nrPnts$"`).
- **S7 — `github.com/murbanczyk/nhphip_spinsolve`** and
  **`github.com/NichVC/SpinsolveExpert-pulse-sequences`** (published
  SpinsolveExpert custom sequences, incl. paper supplementaries): the
  experiment-macro architecture (`getseqpar`/`initAndRunPP`/`execpp`,
  `_pp.mac` with `initpp … endpp(0)`), `getPlotInfo` pattern.
- **S8 — nmrglue `fileio/spinsolve.py`** (BSD-3, already cited in the
  reader) — cross-checked against S3's real files as described above.

### Resolved (construct → source)

| construct in the macro | resolution |
|---|---|
| script shape: entry + `interface` + `getPlotInfo` + `backdoor` | S1 §4 (documented template), S4 (real example) |
| `gExpt->addExperiment(path, name, parameters, ctrlLayout, plotLayout)` | S4 verbatim; 3-arg form in S3 |
| `RunExpt(protocol, ["name = value", …])` → `(result, acqPar)` | S1 §4.1 verbatim example |
| parameter overrides incl. `saveData=\"false\"`, `pulseLength1H`, `nrPnts`, `dwellTime`, `nrScans`, `repTime` | S1 §4.1 verbatim |
| `rxGain` as a settable experiment parameter | S3 (`rxGain` row in the stock interface + real acqu.par); mechanism per S1 §4.1 ("the second argument is a list of parameters to set") |
| pulse program body for the no-pulse sequence (`initpp`/`wait`/`acquire("overwrite",n)`/`endpp`) | S2 App. A + S3/S7 `_pp.mac` files (every command verbatim) |
| −85 dB = "no power" minimum amplitude (fallback noise path) | S2 p10 (`pulseamp` datatype) & p4 (aXXX range) |
| `pulselength` floor 0.5 µs (small-flip scaling clamp) | S2 p10 |
| `query(title, text)` two-arg confirm returning "yes"/"no" | S5 (twice, verbatim) |
| `message(title, text, "info"/"error")` | S5 |
| `mkdir`/`cd`/`getcwd()`; numbered folders `mkdir("$expno$")` | S5 verbatim |
| `save(filename, stringList)` writing verbatim text lines | S3 (macro list matches on-disk proc.par), S5 (`GUI.par`) |
| `list(acqPar)` = acqu.par-shaped string list | S1 §4.1 (`mergelists(list(acqPar), …)` → `ucsFiles:saveAcquPar`) |
| `ucsFiles:saveMNovaData(matrix, filename, list(acqPar), …)` | S1 §4.1 (writes `data.2d`; 1D use is bench item 6) |
| `time()` seconds clock for block stamps | S4, S5 |
| `wvExpStatus == "stop"` abort check between blocks | S4 |
| `assignstruct(parameters)` / `assignlist()` / `struct` `->` access | S1 §4.1, S3, S4 |
| `gData->getXChannelParameters(nucleus)` for the calibrated pulse | S3 (19F version verbatim; 1H key name is bench item 8) |
| interface control/datatype vocabulary used in `interface()` | S2 pp9–10 + S3 stock interface rows (incl. `$gData->rxGainMenu$` etc.) |
| `listto2d(["pt1"])` plot layout | S1 App. B, S4 verbatim |
| `try; …; catch; endtry`, `elseif`, `for…next`, `while/endwhile`, multi-value `endproc` returns, `(a,b) = :proc()`, `$var$` and `$expression$` interpolation, `\"` escapes, `round()`, `getlistindex`, list literals/indexing, `null` | S1/S2 App. D + S3–S7 (all verbatim) |
| `b1Freq`, `softwareVersion` read from returned `acqPar` | field names confirmed in S3's real acqu.par |
| acqu.par + data.1d as the packer's input files | S1 App. A + S3 real folder + S8 |

### Corrections (constructs the documentation contradicted)

1. `query("…", txt, "yesno")` (3-arg) → **2-arg form** per S5; no 3-arg
   dialog form found anywhere.
2. Invented pseudo-entry `expert:runExperiment(seq, par, dest)` → the
   documented **`RunExpt`** (S1 §4.1).
3. `fopen`/`fprintf`/`fclose` for answers.json: **no evidence in any public
   Prospa source**; replaced with the verified `save(filename, stringList)`
   text writer (S3/S5). The one residue — whether `save()` accepts a
   `.json` filename — is bench item 6.
4. List-append `ex = [ex, expno]` in `recordBlock`: no list-append idiom
   found in any source; replaced with plain string accumulation of the JSON
   block records (interpolation + `+` concatenation, both verified).
5. Bare top-level procedure → the **documented script-template shape**
   (S1 §4); a bare macro is a Kea/Prospa-standalone idiom, not how Expert
   runs automation.
6. `message(…,"warning")` → `"info"`/`"error"` (the two icon strings
   actually observed; "warning" icon unevidenced).
7. Placeholder helpers `getB1Freq`/`timeStamp`/`wallClockMs` deleted:
   `b1Freq`/`softwareVersion` now come from the returned `acqPar` (S3),
   block times from `time()` (S4/S5), and the session name from an operator
   tag because **no wall-clock datetime-string command could be verified**
   (bench item 7).
8. Reference tip was a raw degrees parameter with no mechanism → tip is now
   set the documented way: scaled `pulseLength1H` at calibrated amplitude,
   clamped to the documented 0.5 µs floor, with the *achieved* tip recorded
   in answers.json.
9. Single-shot `mkdir(session)` on a nested path → stepwise `mkdir`/`cd`
   with `try/catch` (S5 idiom; nested-path mkdir unevidenced).
10. `magritek_reader.py`: clock-audit now accepts float ms (Prospa number
    formatting in interpolated JSON is not guaranteed integer-literal), and
    `instrument.magritek.spinsolve_software_version` now prefers the real
    acqu.par `softwareVersion` field (S3) over the answers.json value.

### Honest limits of this pass

- The manuals found are **V1.40/V1.41 (2020)**; the real instrument output
  seen is **V2.02.27 (2026)** with V5-generation stock macros. The script
  mechanism (`RunExpt` etc.) appears in both eras' artifacts, but no V2-era
  *manual* was found; version drift is folded into bench items 1–2.
- S3's stock macros carry local-edit comments; only constructs consistent
  with Magritek's autogenerated architecture (and/or repeated in S1/S2/S7)
  were treated as verified.
- No public evidence was found for: a datetime-string command, `save()`
  behavior on non-`.par`/`.1d` extensions, list-append, a compiled
  pulse-free event list, or the script `ctrlLayout` column convention at
  this row count. All remain marked and bench-listed. Documentation on the
  pure-Prospa built-in command set (the ~500 `listcom` commands) is thin on
  the public web; the bench session has the full help viewer (F1) and
  should use it to close items 6–8 quickly.

## Validation checklist — first bench session on a real Spinsolve

Genuinely bench-only items, in order. Items 1–8 close the `UNVERIFIED(n)`
marks in `spin_noise_run_spinsolve.mac` (item 5 closes the lock question
recorded in `answers.json` rather than a syntax mark); 9–10 are the run
itself:

1. [ ] Script installs and loads: drag the folder onto the Expert menu
       (S1 §4.2), select it, confirm the parameter interface renders
       (`UNVERIFIED(1)`: ctrlLayout column convention at this row count,
       `dv` divider rows, `$gData->…Menu$` references in a script; also
       V2-era drift from the V1.4x manuals). Minutes of format fixing
       expected, not architecture changes.
2. [ ] `RunExpt` semantics on this install: `rxGain` override honored,
       `saveData = "false"` honored, `result->tData` is the raw
       unprocessed complex FID (not autophased/filtered), abort behavior
       (`UNVERIFIED(2)`).
3. [ ] Noise path (`UNVERIFIED(3)`): does the pulse-program compiler accept
       the acquire-only event list in the macro header? If yes, create and
       compile `SpinNoiseNoPulse` (documented copy+edit+compile flow, S2)
       and set `useNoPulseSeq = "yes"`. Either way, verify no measurable
       transmitter output during a noise block (scope/spectrum check if
       available) — for the default path this validates "−85 dB = no
       power" on this unit; the residual is already logged in answers.json.
4. [ ] `rxGain` values (`UNVERIFIED(4)`): dump this model's menu
       (`gData->rxGainMenu`), snap the ladder gains [0,20,40,60] to menu
       values, find max stable noise gain with no ADC clipping. Also
       confirm 65536 is in `nrPntsMenu` and 100 µs in `dwellTimeMenu`
       (16384 pts @ 100 µs is documented in S1's own example; 65536 is
       not).
5. [ ] External lock (`UNVERIFIED(5)`): stock experiments suspend the lock
       loop around every acquisition (`ucsUtilities:suspendLock()` /
       `resumeLock()`, S3). Confirm this holds under `RunExpt` for ~7 s
       acquisitions, whether the lock channel radiates near the ¹H band
       during noise blocks, and whether the lock can stay frozen across
       the whole noise series; set the `lockState` answer accordingly.
6. [ ] Macro-written files (`UNVERIFIED(6)`): `save("acqu.par",
       list(acqPar))` completeness vs a framework-written acqu.par;
       `ucsFiles:saveMNovaData(result->tData, "data.1d", …)` output passes
       `magritek_reader.py inspect`; `save()` accepts the `answers.json`
       filename (fallback: write as `.par` and rename, or find the text
       writer via F1/`listcom`).
7. [ ] Timestamps (`UNVERIFIED(7)`): `time()` epoch and resolution (clock
       audit needs differences at worst, an absolute anchor at best);
       timezone; if a datetime-string command exists, replace the
       `sessionTag` parameter with a real timestamp.
8. [ ] Calibrated-pulse keys (`UNVERIFIED(8)`):
       `gData->getXChannelParameters("1H")` key names on a ¹H system
       (`PulseLength_1H`/`PowerLevel_1H` by analogy with the verified 19F
       names); check the scaled reference pulse is ≥ 0.5 µs for this
       system's p90 (raise `refPulse_deg` if the clamp engages).
9. [ ] Run the full chain on real data: session → `pack` → `--selftest` →
       upload to the staging endpoint.
10. [ ] Measure loaded Q and estimate η (feeds the feasibility memo's
        biggest unknowns); then look for the dip — expected within minutes
        if the memo's box holds.

## Bundle mapping summary (schema 2.0)

| meta.json field | source |
|---|---|
| `vendor` | `"magritek"` |
| `instrument.magritek.spinsolve_software_version` | acqu.par `softwareVersion` (verbatim; machine-readable field confirmed in real output), else answers.json |
| `instrument.magritek.rx_gain` | acqu.par `rxGain` (dB, verbatim) |
| `instrument.magritek.data_format` | `"prospa-1d"` |
| `spectrometer.h1_freq_mhz` | acqu.par `b1Freq` (MHz assumed) |
| `spectrometer.field_tesla` | `b1Freq`/42.5774689 |
| `spectrometer.probe_type` | `"permanent-magnet-benchtop"` (2.0; the 1.2 fallback uses `"RT"`, the closest 1.x enum value) |
| `experiments[].td` | 2 × `nrPnts` (Bruker TD convention: re+im points) |
| `experiments[].sw_hz` | 1e6 / `dwellTime`[μs] |
| `experiments[].rg` | 10^(`rxGain`/20) (linear amplitude, Bruker-comparable) |
| `experiments[].aq_s_per_row` | `nrPnts` × `dwellTime` × 1e-6 |
| `checksums` | SHA-256 of every packed `data/…` file |

Schema note: if a facility must run the pre-2.0 toolchain, the packer can
emit a 1.2-shaped bundle with `--schema-version 1.2` (topspin_version field
carries `"n/a (Magritek Spinsolve)"`); 2.0 is the default and the right
target.

---

*Maintained by John W. Blanchard (jwbquantum@gmail.com), with Claude
(Anthropic, San Francisco, California, USA).*
