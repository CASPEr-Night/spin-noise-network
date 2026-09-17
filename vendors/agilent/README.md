# Agilent/Varian path — converter-first strategy

**Status: VALIDATED ON REAL HARDWARE, 2026-09-14 — one console family,
one instrument.** Two sessions on the partner instrument — a **400 MHz
Agilent DD2 running VnmrJ 3.2** (Boyd Goodson, SIU Carbondale), 5 mm
HCN probe, room temperature, **no autotune/match** — were acquired (68
and 66 experiments), packed, validated and uploaded: V1–V6 PASS on
both, zero reader warnings, byte round-trip exact. The partner
checklist at the end is annotated item by item with what that settled:
items 2 (2026-09-16: the FFT-convention axis is mirrored, shown two
independent ways), 4, 5 (external-driver form), 8 and 10 answered, 1,
6 and 7 partly, and **items 3 and 9 remain open** — item 3 has an
acceptable bound path without a spectrum analyser (an empty-probe or
pure-D₂O noise run, below). The report resolves the axis sign for
every bundle instead of assuming it. `spin_noise_run.mac` has
still not run on hardware. Everything below that the console did not
settle stays marked UNVERIFIED and appears in that checklist.
Contact: John W. Blanchard <jwbquantum@gmail.com>.

## Strategy in one paragraph

Like the JEOL path, the Agilent path is **converter-first**, in two
tiers — but with one important difference in our favor: unlike Delta,
the VnmrJ macro language (MAGICAL) is genuinely public, because
**OpenVnmrJ** (github.com/OpenVnmrJ) is the open-source continuation of
VnmrJ — same macro language, same manual pages, same shipped macro
library lineage as the proprietary VnmrJ 3.2 on the partner instrument.
**Tier 1 (validated on the partner instrument, 2026-09-14):** the
operator follows a short manual checklist — the same physics protocol as the Bruker run, acquired with
VnmrJ's ordinary tools as a series of 1D experiments saved with `svf` —
and our converter (`vendors/agilent/agilent_reader.py` +
`packer/pack_bundle.py --vendor agilent`) turns the saved `.fid`
directories plus a one-page questionnaire into a contract-conforming
network bundle. **Tier 2 (draft macro, ships in this directory):**
`spin_noise_run.mac`, a MAGICAL acquisition macro that runs the whole
session — dialogs, gain ladder, references, N no-pulse noise blocks —
and writes the questionnaire itself, built construct-by-construct from
OpenVnmrJ documentation and from Agilent's own shipped
`cryo_noisetest` macro (which is, remarkably, already a receiver-only
noise measurement: `tpwr=-16 pw=0 nt=1 ss=0 pad=0 gain=50`, verbatim).
Tier 1 depends only on things any VnmrJ operator does daily; Tier 2 is
a documented-idiom draft awaiting its first bench session. A third
form — an external acquisition driver run from the console
workstation, submitting one block at a time into a running VnmrJ
through `listenon`/`send2Vnmr` — drove the second partner session end
to end and is being contributed to this directory (see "Observed on
the real console" below).

## Why this partner instrument matters physically

A 400 MHz room-temperature probe sits at the axion-mass coordinate
1.65 µeV and expects the **Guéron absorption dip** (uniform-temperature
physics — the calibration anchor of the temperature-contrast law). It
is also the direct re-test of the 2022 companion run: that run, on a
room-temperature 400 MHz instrument, found **no dip at a 95% UL of
0.70% of the spin-coupled floor** — a quantitative null explained by
heavy D₂O dilution of the sample (neat water would have shown a 25–65%
dip, excluded ≥ 35×). One tube of *known-fraction* water on the
partner's DD2 settles that story on the same field point, with the H₂O
fraction recorded this time. That is the single highest-leverage
measurement this port enables.

## What is actually documented publicly (research record, 2026-08-27)

* **The `fid` + `procpar` file family.** Every VnmrJ save (`svf`) writes
  a `<name>.fid` directory holding `procpar` (text parameter tree),
  `fid` (binary data), `text`, and `log` (svf manual page, OpenVnmrJ
  `src/common/manual/svf`). The binary `fid` layout and the `procpar`
  record format are implemented, completely, by **nmrglue's varian
  reader** (`nmrglue/fileio/varian.py`, BSD-3-Clause,
  github.com/jjhelmus/nmrglue) — the authoritative open-source
  implementation, which itself cites "Varian MR News 2005-04-18" and
  Agilent's *VnmrJ User Programming* manual. Our reader ports that
  layout **with attribution** (facts quoted in `agilent_reader.py`):
  32-byte big-endian file header `>6ihhi` = [nblocks, ntraces, np,
  ebytes, tbytes, bbytes, vers_id, status, nbheaders]; 28-byte block
  headers `>4hi4f`; status bits S_FLT/S_32 select float32/int32/int16;
  procpar records with an 11-field first line, count-prefixed values
  (quoted strings one per line), and an enumerable line.
  **Honest gap vs the JEOL port:** no public corpus of raw `.fid`
  directories was found to verify against (JEOL had 38 real files;
  Magritek had real V2.02.27 output), so the format implementation was
  *documentation-verified only* until the partner session parsed a real
  session. Closed 2026-09-14: 134 real `.fid` directories from the
  partner DD2 parsed with zero warnings (checklist item 10).
* **MAGICAL macro language + acquisition control.** OpenVnmrJ publishes
  the full manual set (`src/common/manual/`) and macro library
  (`src/common/maclib/`). Verified there, verbatim: `go`/`ga`/`au`
  semantics — **go and au return after submission**, `'sync'`/`'next'`
  only synchronize submission, `au` runs the `wbs`/`wnt`/`wexp`
  processing macros, and `au('wait')` exists only in automation mode
  (manual/go) — hence the macro's wexp-chained design;
  `write('reset'/'file'/'line3', …)` incl. format-into-variable
  (manual/write); `shell('cmd'):$ans` (manual/shell); `svf` file list
  (manual/svf); `mkdir('-p', …):$res` (manual/mkdir);
  `exists(name,'parameter'/'file'):$x` (manual/exists);
  `input('prompt'):$var` (manual/input); `lookup('file'/'read')`
  (manual/lookup); **`unixtime`/`systemtime`** returning epoch seconds
  (+ µs) or a strftime-formatted string (manual/unixtime — this is the
  clock-audit hook); `sleep` (manual/sleep); `create(name, type, tree,
  init)` (manual/create); MAGICAL control flow and string idioms
  (`$var`, `if/then/else/endif`, `while/do/endwhile`, `substr`,
  `format`, concatenation) as used across the shipped maclib (e.g.
  `cft2da`).
* **A no-pulse acquisition is not just expressible — Agilent shipped
  one.** `src/common/maclib/cryo_noisetest` acquires decoupler-noise
  spectra with **`at=.128 d1=.872 tpwr=-16 pw=0 nt=1 ss=0 pad=0
  gain=50`** and `wshim='n' in='n' spin='n' alock='n'`, chained through
  `wexp='cryo_noisetest(\'…\')'` + `au` + `return`, with
  `systemtime(...)` date stamps and `mkdir`/`exists`/`lookup` file
  bookkeeping. Our macro is deliberately the same shape with the same
  no-pulse idiom (`s2pul`, `pw=0`, `tpwr=-16`). `tpwr=-16` is the
  bottom of the standard transmitter-power scale on this hardware
  family (the value cryo_noisetest itself uses as "off"); **whether the
  TX chain is genuinely silent** on the partner's DD2 is checklist
  item 3 — the 2022 attenuator lesson says measure, never assume.
* **Receiver gain.** `gain` is receiver gain in dB; typical documented
  range 0–60 with overflow warnings, and autogain is `gain='n'`
  (UCSB VnmrJ parameter reference; IMSERC VnmrJ 3.2A sheet: "gain —
  receiver gain (in dB)"). Autogain **cannot be used** here (and is
  documented as unusable for arrayed experiments anyway): every block
  sets `gain` explicitly, and the ladder measures linearity. The DD2's
  exact legal values/step and its maximum stable noise-block gain are
  UNVERIFIED (checklist item 1).
* **Parameter semantics used by the converter.** `np` is the TOTAL
  number of points (real+imag interleaved — the same counting
  convention as Bruker TD), tied to `sw` and `at` by the standard
  relation **at = np/(2·sw)**; on a Varian/Agilent system the console
  recomputes np from sw and at (UIUC "Acquisition Time and Spectral
  Width in NMR"; UMN Varian instructions). `sfrq` is the observe-channel
  frequency in MHz, `tof` the transmitter offset, `nt` transients,
  `seqfil`/`pslabel` the sequence name, `tn` the observe nucleus
  (IMSERC VnmrJ 3.2A sheet; UCSB reference). The `tof` sign convention
  as an O1 analog was answered on the partner console (checklist item
  2): `tof` is recorded verbatim as `o1_hz`, and the FFT-convention
  frequency axis of the fid is mirrored relative to the physical one.
  `reffrq` (MHz) is the 0 ppm frequency VnmrJ derives from the lock
  solvent, tied to the acquisition by `reffrq = sfrq − sw/2 + rfl −
  rfp` (`rfl`, `rfp` in Hz) — an identity that held to the hertz on
  every real procpar and that the report uses as a zero-cost axis-sign
  cross-check.

## What ships in this directory

| File | Purpose |
|---|---|
| `agilent_reader.py` | Stdlib-only reader: binary `fid` + `procpar` per the nmrglue-documented layout (structural checks, no magic-value assertions). CLI: `python3 agilent_reader.py inspect <dir.fid>`. |
| `spin_noise_run.mac` | Tier-2 DRAFT MAGICAL acquisition macro (wexp-chained state machine; see its header). Never run on hardware; every unsettled construct is UNVERIFIED(n)-marked against the checklist below. |
| `make_synthetic_agilent_data.py` | Deterministic synthetic session generator (`.fid` directories + packer questionnaire), so the whole chain is testable today without a spectrometer. v0.7.1 options, all off by default: `--ladder-levels-db "0,5,...,40" --ladder-repeats 3 --ladder-random-seed S` (the randomized repeated ladder, rungs 1000+ in acquisition order, integer-dB `gain`, `pad` 1, `time_run` ~6 s apart), `--tuning-ladder N` (N settings x 2 `noise_tune` blocks with `tuning` objects), `--signcal` (a `tof` +200 Hz reference whose synthetic line sits 200 Hz lower in the window — the physical convention — and whose `sfrq` is displaced with it, as on VnmrJ). Every procpar carries a lock referencing consistent with that physical line — `reffrq` fixed so water at 4.75 ppm sits at the line's +537.5 Hz, `rfl` from `reffrq = sfrq − sw/2 + rfl − rfp` in each experiment — so the report's referencing cross-check reads +1 on these sessions. In these extended sessions a 1e5-count line and the noise both scale as 10^(gain/20), so every rung has SNR ~440 and the report reads the ladder as linear to 0.5% (a 2000-count line over gain-independent noise, the first draft, read as 20% compression: the analysis anchors on the lowest, noisiest level). |
| `test_agilent_chain.sh` | synthetic session → `packer/pack_bundle.py --vendor agilent` → `uploader --selftest` → meta.json + inspector assertions, plus the v0.7.1 assertions: repeated rungs verbatim, unsorted and linear to 1.5% rung by rung (pure-Python DFT), tuning objects verbatim, save-name role inference including the letters rule, schema-invalid and non-finite tuning values rejected with a named key, an answered ladder cross-checked against the session, the signcal's displaced `sfrq` kept out of `h1_freq_mhz`, the signcal line displacement checked by DFT, the referencing identity in every procpar, and — when numpy is importable — the report's own reading: drift_compression model, time order `randomized`, amplitude envelope under 1%, axis sign +1 from the signcal pair with the lock-referencing cross-check agreeing. Working; run it. |

The packer adapter lives in `packer/pack_bundle.py` (`AgilentReader`),
which delegates parsing to this directory. End-to-end:

```
python3 packer/pack_bundle.py <session_dir> --answers answers_packer.json --vendor agilent
python3 uploader/upload_bundle.py spinnoise_<slug>_<stamp>_<hex>.zip
```

## Tier 1 — the operator checklist

One 5 mm tube of water, ~1 h 20 min of magnet time, ordinary VnmrJ
tools — no macro needed. Each `svf` save gets a **numeric prefix** that
maps it onto the Bruker experiment plan (`topspin/spin_noise_run.py`;
PROTOCOL.md) so the analysis treats both fleets identically, and an
`_sn_<role>` suffix the packer reads as the experiment's role —
whatever follows the role word is free unless it runs straight on in
letters (`17_sn_noise_b`, `17_sn_noise2`, `17_sn_noise-1` are noise
blocks; `17_sn_noisetest` is not recognised and the packer stops).
Save everything into one directory, one `.fid` per step.

| Step | Bruker equivalent (expno) | VnmrJ Tier-1 action | Save as |
|---|---|---|---|
| 0. Sample + setup | 1 (`setup`) | 5 mm tube, ~550 µL water (tap/distilled/D₂O-doped — record which). Insert, set/record temperature, equilibrate. Tune/match ¹H **by hand** (the partner probe has no autotune), shim as usual, calibrate the ¹H 90° pulse (or record the probe-file value). Write down pw90 and tpwr. Note the lab (ambient) temperature: the questionnaire's coil and preamp temperatures are physical, and on an RT probe both are the ambient. | (optional) `01_sn_setup` |
| 1. Gain ladder | 10/14/15/16 (`rg_ladder`; the Tier-1 rungs are numbered 1000+) | Find the highest `gain` at which a tiny-flip `s2pul` 1D (`pw` ≈ pw90/90, i.e. ~1°, `nt=1`) does not overflow the receiver — sample-dependent: 60 dB on a 1% H₂O Gd-doped tube, 40 dB on 90/10 H₂O/D₂O, same probe. Then visit every integer-dB level from 0 to that maximum in 5 dB steps (0/5/…/40: 9 levels), **each three times, in one random permutation**, `pad=1` so the receiver settles after every gain change — 27 rungs, ~3 min. Why: both SIU ladders showed a ~10% amplitude roll-off toward the top rung that a monotonic ladder cannot tell from drift; repeats at random moments separate compression (follows the gain) from drift (follows the clock). **Set `gain` explicitly — never `gain='n'` (autogain).** Set `pw` and `tpwr` explicitly too and write them down. The console may store a different gain than requested (13.3 → 14, 26.7 → 26 here); the packer reads the stored value from each rung's procpar, so nothing is listed by hand. | `svf('1000_sn_ladder_35')`, `svf('1001_sn_ladder_35')`, … in acquisition order (prefixes 1000, 1001, …; the gain in the name is for the operator) |
| 2. Opening reference | 11 (`reference_open`) | ONE `s2pul` 1D, same tiny flip (the ladder's `pw`/`tpwr`, set explicitly), `nt=1`, `pad=0`, at the ladder's maximum gain, `at` ≈ 2 s. (Deviation from Bruker's 8-row reference, accepted for Tier 1 exactly as on the JEOL path.) | `svf('11_sn_ref_open')` |
| 3. Noise block | 12 (`noise`) | Repeated **no-pulse** 1Ds: `pw=0 tpwr=-16 nt=1 ss=0 pad=0`, gain at the ladder maximum. Record length is a point-count cap, `np` ≤ 524288, so `at_max = 524288/(2·sw)` — 41 s at sw = 6410 Hz; use `at=30` and ~60 blocks for 30 min (a narrower `sw` buys proportionally longer records). Autoshim and autolock off (`wshim='n' alock='n'`); lock ON recommended (`alock='n'` does not release an established lock) — **describe the lock/z0 state in the questionnaire**. Repeat until ≥ 30 min total. This is Agilent's own receiver-only idiom (`cryo_noisetest`). | `svf('12_sn_noise')`, then `17_sn_noise`, `18_sn_noise`, … (12 first, then count up from 17; 13 is reserved) |
| 4. Closing reference | 13 (`reference_close`) | Identical to step 2 — same `at`, same `gain`, same `pw`/`tpwr` as step 2 — a matched drift bracket. The noise blocks leave `pw=0 tpwr=-16` behind, so set both back explicitly. | `svf('13_sn_ref_close')` |
| 5. Spin-noise tuning ladder | — (role `noise_tune`, v0.7.1) | ≥ 5 tuning/match settings bracketing the normal one — normal, then one and two steps to either side, a step being whatever the console lets you set and read reproducibly (capacitor turns, reflected power, wobble minimum) — with **no-pulse blocks per setting** (step-3 parameters, `at=30`): two blocks suffice on a 90/10 tube (~11σ per 30 s block on the SIU instrument); a 1%-class tube gives ~2.5σ per block, two blocks stay under the report's 5σ threshold and every setting reads "undetermined", so take six. Do the normal setting inside the ladder as its own blocks. Record the console's tune/match readings per setting in the questionnaire's `tuning` objects (`setting_index` 0 = normal, readings in whatever units you have). The report names as nearest the spin-noise tuning optimum the setting with the smallest dispersive fraction \|b/a\| — at the optimum the dispersive admixture vanishes; the setting where the feature nulls and flips sign is nearly pure dispersion, the farthest from it. Analysed as a ladder of its own; **never co-added with step 3**. Retune to normal afterwards. | `svf('2000_sn_tune_0')`, `svf('2001_sn_tune_0')`, `svf('2010_sn_tune_1')`, … (setting k → 20k0, 20k1, …; the trailing `_k` is read) |
| 6. Axis-sign check | 29 (`sweep_signcal`) | One more reference identical to step 4 (same `pw`/`tpwr`/`gain`/`at`, probe at normal tuning) with `tof=tof+200`; restore `tof` afterwards. The report takes tof(signcal) − tof(reference) = +200 Hz as the displacement; in the physical convention the line's apparent in-window offset **drops** by 200 Hz, a rise means the axis is mirrored — on this console it rose (checklist 2: mirrored). The report resolves the sign per bundle from this pair, or without it from the procpar's own lock referencing. | `svf('3000_sn_signcal')` |
| 7. Operator log | six TopSpin dialogs + `meta.json` | Fill in the questionnaire (copy `packer/answers.example.json`, set `"vendor": "agilent"`, add `instrument.field_state_notes`; copy the console's `/vnmr/vnmrrev` into the session directory as `vnmrrev`, or set `instrument.vnmrj_version` by hand): facility, sample (**H₂O fraction!**), temperatures (physical: coil and preamp = lab ambient on an RT probe, a measured value over an assumed 298 K), lock state, pw90/tpwr, the `tuning` entries of step 5. Roles come from the save names and `calibration.rg_ladder` from the rungs' stored gains, so the experiment list holds only the `tuning` entries. | `answers_packer.json` |
| 8. Pack + upload | automatic zip + uploader | `python3 packer/pack_bundle.py <dir> --answers answers_packer.json --vendor agilent`, then upload the printed zip. The packer validates before you send and lists exactly what is missing. Temperatures learned better after the upload go to the maintainer as numbers (applied at analysis time), not as a re-upload. | bundle zip |

Notes for the operator: nothing in this protocol pulses your sample at
high power or touches the probe beyond ordinary tune/shim; the
H₂O-fraction question **is** the measurement (the 2022 lesson, see
PROTOCOL.md); and if your facility allows physically detaching or
muting the transmitter path for the noise block, that is a valuable
extra — record what you did in the notes.

## Tier 2 — the MAGICAL macro (draft)

`spin_noise_run.mac` automates the whole Tier-1 table: `input()`
dialogs for the six operator questions, then a wexp-chained state
machine (each block submits with `au`, names the macro as `wexp`,
returns; the next invocation saves the finished block with `svf` into
the numbered session layout and submits the next block). It writes
`answers_packer.json` itself at setup — crash-safe: whatever acquired
before a crash is already packable — plus `spin_noise_times.txt`, a
per-block wall-clock log from `unixtime` (epoch seconds; the future
clock-audit input). Design constraints it respects:

* **No blocking waits exist** outside automation mode (go/au manual:
  submission-only synchronization; `sleep` caps at 60 s), so chaining
  through `wexp` is not a stylistic choice — it is the documented
  mechanism, and Agilent's own `cryo_noisetest` is the shipped
  precedent, down to using acquisition parameters as loop state.
* **The noise path is never assumed silent.** `pw=0 tpwr=-16` is
  recorded in the questionnaire and in every noise block's own procpar;
  checklist item 3 measures the residual.
* **Nothing touches lock/z0 or the probe.** Autoshim and autolock are
  turned off for the blocks (`wshim='n' alock='n'`, the cryo_noisetest
  preamble); the lock state itself is the operator's, asked and
  recorded — the Agilent analog of the Bruker BSMS-sweep confirmation.

## What the partner session settled, and what it did not

Four things this README would not promise before 2026-09-14, revisited
against the two SIU sessions. First, **the format implementation** has
now met real files: 134 `.fid` directories from the DD2 — no-pulse
noise blocks, references, ladder steps — parsed with zero warnings,
byte round-trip exact, status bits selecting float32 as documented
(item 10 cleared). Second, **receiver-gain semantics**: legal `gain`
values are integer dB and two ladders ran without overflow (0/20/40/60
dB, and 0/14/26/40 dB by procpar `gain` — requested as
0/13.3/26.7/40), but the amplitude transfer curve is
not yet fitted and the safe maximum is sample-dependent — bundles still
record dB verbatim with the Bruker-comparable 10^(dB/20) mapping
labeled as exactly that, and no cross-gain calibration is attempted
(item 1 partial). Third, **transmitter silence at pw=0** is still
unmeasured — no spectrum analyser was available at the partner site —
so it remains assumed, not shown, the 2022 lesson unpaid (item 3 open).
Fourth, **the macro itself**: `spin_noise_run.mac` has still not run;
the wexp quoting, `au` chaining and absolute-path `svf` it relies on
were exercised from outside VnmrJ by the external driver (66/66
blocks), which is the tested form of Tier 2 today. Until the macro has
its own bench session it stays labeled DRAFT, and Tier 1 — or the
driver — is the recommended path. A fifth thing settled later: the sign of the frequency axis (item 2),
answered on 2026-09-16 two independent ways and now resolved by the
report for every bundle. All of this is one console family,
one instrument: a second Agilent site is the next boundary.

## Partner validation checklist (Boyd Goodson's DD2, VnmrJ 3.2)

Each item began as an UNVERIFIED assumption or open question carried by
the code. The two sessions of 2026-09-14 (one manual per the
quickstart, one script-driven) settled some and not others; each item
carries its status and the evidence. `UNVERIFIED(n)` marks in
`spin_noise_run.mac`, `agilent_reader.py`, and `packer/pack_bundle.py`
cross-reference these numbers.

1. **`gain` values and linearity.** Legal receiver-gain values and step
   on the DD2 (documentation says dB, typically 0–60); the maximum
   noise-block gain with no ADC/receiver overflow; then the RG ladder
   against a fixed ~1° signal to fit the amplitude transfer curve.
   Snap the macro's `$ladgain*`/`$noisegain` defaults to legal values.
   *2026-09-14: PARTIAL.* Legal gains are integer dB, as reported.
   Ladders of 0/20/40/60 dB (1% H₂O Gd-doped tube, noise blocks at
   60 dB) and 0/14/26/40 dB (90/10 tube, noise blocks at 40 dB) ran
   without receiver overflow at their maxima. The second ladder was
   requested as 0/13.3/26.7/40 and the console stored 14 and 26 —
   values nearest-integer rounding would not give — so the legal step
   or rounding rule is not settled by two points; the session-2 bundle
   carries the requested values in `calibration.rg_ladder` (4.62,
   21.63) and the stored ones in `experiments[].rg` (5.01, 19.95). The
   amplitude transfer curve is not yet fitted. The safe maximum is strongly
   sample-dependent — 60 vs 40 dB on the same probe — so the ladder is
   scaled to an operator-found maximum, never fixed.
   *Ladder redesign (v0.7.1).* In both SIU ladders the rungs'
   amplitude per unit gain fell by ~10% from the bottom rung to the top
   (−11% and −10%; the reference brackets themselves agree to 0.3%),
   and in a monotonic four-rung ladder that roll-off is degenerate with signal
   drift — gain and time rise together. The Tier-1 ladder is therefore
   now integer-dB levels in ~5 dB steps from 0 to the sample's
   non-overflow maximum, **each visited three times in one random
   permutation** (order on record through procpar `time_run` →
   `experiments[].started_local`), `pad=1` after every gain change,
   ~3 min in all: bracketing and randomization let the analysis
   separate receiver compression (follows the gain) from drift
   (follows the clock). `calibration.rg_ladder` lists the rungs in
   acquisition order with duplicate gains, taken from the rungs' stored
   `gain` when the questionnaire omits it; nothing in the packer sorts
   or dedups it. An answered ladder is cross-checked against the
   session — a rung naming an expno the data lacks, or an rg more than
   1% off that experiment's stored gain (the session-2 case above),
   gets a WARN and is still recorded verbatim; the analysis reads the
   stored gain. Bracketed and randomized ladders are both analyzed:
   the report classifies the time order it saw (monotonic, bracketed —
   one run up then one run down — or randomized) and fits the
   drift-compression model whenever levels repeat. SIU session 3
   (2026-09-16) ran a bracketed ladder, 1/6.31/31.6/100/100/31.6/6.31/1
   in rg, and showed 9–11% amplitude compression toward the top level
   in both the ascending and the descending ladder with under 0.5%
   drift between them — the compression is the receiver's, not the
   signal's. The randomized permutation has not yet run on the
   console.
2. **`tof`/`sfrq` conventions.** Confirm `sfrq` (MHz, observe channel,
   with `tn='H1'`) is the right `h1_freq_mhz`, and the `tof` sign and
   reference convention as the Bruker-O1 analog; fix the adapter if the
   convention differs.
   *2026-09-16: ANSWERED on the partner console.* The FFT-convention
   frequency axis of the fid is **mirrored** relative to the physical
   one: the physical line frequency is carrier − f_FFT. Established
   two independent ways. (i) A +200 Hz `tof` step moved the apparent
   line by +199.6 Hz (ratio 0.998) — in the physical convention it
   would have dropped by 200 Hz. (ii) VnmrJ's lock referencing places
   water ~500 Hz below the carrier: `reffrq` = 399.618776561 MHz is
   the 0 ppm frequency (and `reffrq = sfrq − sw/2 + rfl − rfp` holds
   to the hertz: 399.6211743 MHz − 3205.128 Hz + 807.389 Hz), so water
   at 4.75 ppm sits at −499.5 Hz from the carrier, where the FFT
   convention had it at about +540 Hz. `tof` is recorded verbatim as
   `o1_hz` — no sign flip on `o1_hz` itself. `sfrq` = 399.6211743 MHz
   (with `tn='H1'`) is the right `h1_freq_mhz`. The report does not
   assume the answer: it resolves the sign per bundle by the signcal
   pair when one is present, otherwise by the referencing check
   (`science.frequency_axis_sign.referencing_check`), and calls a
   disagreement between the two a CONFLICT (QA FAIL). The two
   September sessions carried no signcal pair and are resolved by the
   referencing check, which places their spin line on one physical
   mass axis near 1.652698 µeV (1.6526982 and 1.6526981 µeV for the
   two sessions' 534.4 and 540.8 Hz line offsets — the former mirror
   placement; the exclusion's best coupling sits 248 Hz below the
   line, at 1.6526971 µeV).
   *v0.7.1: resolved per bundle by the signcal pair.* One extra
   reference (`sweep_signcal`, saved `NN_sn_signcal`) with `tof`
   displaced by +200 Hz and the same `pw`/`tpwr`/`gain`/`at` as the
   references. The packer records `tof` verbatim as `o1_hz`, and
   because `sfrq` is the observe transmitter frequency and tracks
   `tof`, the signcal's `sfrq` never votes on `spectrometer.h1_freq_mhz`
   (the session's carrier is the other experiments'). The report
   takes o1_hz(signcal) − o1_hz(reference) as the displacement, and in
   the physical convention the line's apparent in-window offset drops
   by exactly that amount — a rise means the axis is mirrored — and
   records which it found. A bundle without the pair is resolved by
   the lock-referencing check when the sample is water-like, the
   observe nucleus ¹H and the referencing identity holds within 2 Hz;
   only a bundle where neither applies keeps the sign-unverified
   caveat.
3. **No-pulse silence.** With `s2pul pw=0 tpwr=-16`: is the transmitter
   chain measurably silent during a noise block (spectrum analyzer on
   the TX path if available, otherwise the noise floor itself)? Measure
   the residual tip if any; record the result in the questionnaire
   notes. If the facility can physically mute/detach the TX path,
   qualify that too.
   *2026-09-14: OPEN.* No spectrum analyser was available at the
   partner site. Nothing anomalous was seen in the noise floor, which
   is not a measurement of transmitter silence.
   *Acceptable bound without a spectrum analyser.* A noise run of
   ≥ 10 min with the step-3 parameters (`pw=0 tpwr=-16`) on an empty
   probe or on a pure-D₂O tube — no protons, so no spin line — bounds
   transmitter leakage at the water frequency to a few percent of the
   noise floor: any line there is the transmitter's, not the sample's,
   and its absence at that level is the measurement. Record the run in
   the questionnaire notes and mark the item "bounded by empty-probe
   run" (or "by pure-D₂O run"); a spectrum analyser on the TX path
   remains the direct measurement.
4. **DSP and record limits.** Whether `dsp='n'` applies/behaves on the
   DD2 (inline vs realtime DSP); maximum `np`/`at` per 1D record (the
   macro defaults to 10 s records; longer is better for the noise
   floor); any oversampling settings that change the fid layout.
   *2026-09-14: ANSWERED.* The record-length limit is a point-count
   cap: procpar `np` max 524288 (min 32, step 2), so `at_max =
   524288/(2·sw)` — 40.9 s at sw = 6410.26 Hz, matching the ~40 s
   ceiling hit at the bench; 30 s records were used (3205 Hz → ~82 s,
   1602 Hz → ~164 s). `dsp` is not a parameter on this system;
   `oversamp`, `fb` and `dmf` exist (noise blocks: oversamp 1, fb 4000,
   dmf 29412) and the fid layout was the documented one. Derive the
   block count from a target duration — `at = min(desired, at_max)`,
   blocks = ceil(target/at) — never a hardcoded count.
5. **Macro mechanics on VnmrJ 3.2.** Install `spin_noise_run.mac`, run
   a 2-noise-block session: wexp quoting/recursion, created `sn_*`
   parameter persistence across blocks, `input()` numeric typing,
   `mkdir('-p')`, `systemtime` format strings. Expect minutes of format
   fixing, not architecture changes.
   *2026-09-14: ANSWERED for the external-driver form.* Per block,
   `<params> wexp='svf(\'<absolute path>\')' au` sent into a running
   VnmrJ via `listenon` + `send2Vnmr`, waiting for the `.fid` to appear
   before the next: 66/66 blocks OK. wexp quoting, `au` chaining and
   absolute-path `svf` work on 3.2. `spin_noise_run.mac` itself —
   created `sn_*` parameter persistence, `input()` typing,
   `mkdir('-p')`, `systemtime` formats — is still untested.
6. **Timestamps and the clock audit.** `unixtime` resolution on the
   workstation and its NTP discipline (`chronyc tracking`/`ntpq -pn`);
   then wire `spin_noise_times.txt` into per-experiment
   `started_local`/`finished_local` and a schema-1.2+ `clock_audit`
   block (the Bruker orchestrator's audit needs block wall-clock stamps
   + OCXO-implied durations — both are available here: at·nt per
   block). Also check what timestamps procpar itself carries
   (`time_run`?) and their timezone.
   *2026-09-14: PARTIAL.* procpar carries `time_run`, `time_complete`
   and `time_saved` (also `time_submitted`, `time_svfdate`) as
   `YYYYMMDDTHHMMSS` in console-local wall-clock time; the packer now
   fills `started_local`/`finished_local` from them. NTP discipline of
   the console still needs checking at every session: the partner
   console was found 2 h 54 min fast in UTC and in the wrong zone, with
   ntpd stopped for 92 days, and was corrected before either session —
   a bundle built from an undisciplined clock passes `--selftest` and
   `--verify-only` with every timestamp wrong. `unixtime` resolution
   and the `clock_audit` block remain to be wired.
7. **`svf` behavior.** Absolute session paths, the exact file set
   written on 3.2 (procpar/fid/text/log), collision behavior, and
   whether `svf(..., 'nodb')` is preferable on a database-enabled
   install.
   *2026-09-14: PARTLY.* `svf` writes `<name>.fid` holding `fid`,
   `log`, `procpar` and `text`; absolute session paths work; names
   starting with a digit are accepted. Collision behavior and `'nodb'`
   were not tested.
8. **VnmrJ version provenance.** A machine-readable source for the
   VnmrJ version (procpar parameter? `/vnmr/vnmrrev`?) so
   `instrument.vnmrj_version` stops being operator-entered.
   *2026-09-14: ANSWERED.* `/vnmr/vnmrrev` is machine-readable — "VnmrJ
   VERSION 3.2 REVISION A / September 21, 2011 / vnmrsdd2" — and the
   packer fills `instrument.agilent.vnmrj_version` from a copy of it
   placed at the top of the session directory as `vnmrrev` whenever
   answers.json does not give one, so the value need not be
   operator-entered.
9. **External reference input.** Whether the DD2 console accepts an
   external 10 MHz reference (future GPSDO clock option; facility
   consent required) — record model and connector, do not touch.
   *2026-09-14: OPEN.* Not examined.
10. **Live-console surprises.** Parse a fresh session from the DD2 with
    `agilent_reader.py inspect` and the packer; fix whatever the
    documentation did not teach us (procpar record wrapping, arrayed
    parameters, status-bit surprises, big-endian assumptions).
    *2026-09-14: CLEARED.* fid/procpar implementation correct against
    real DD2 output: 537 procpar keys parsed, structure OK, zero
    warnings; `tbytes = np·ebytes`, `bbytes = tbytes + 28`, file size
    = 32 + 28 + data; status 201 → float32 (`ebytes` 4). Big-endian
    layout, procpar record format and status bits all confirmed on
    no-pulse noise blocks, references and ladder steps.

## Observed on the real console (SIU Carbondale, 2026-09-14)

Notes from the two partner sessions that belong next to the checklist
without being checklist items. One console family, one instrument.

* **Query live VnmrJ state through `listenon`/`send2Vnmr`; never read
  `curpar` as live state.** To learn a parameter's current value from
  outside VnmrJ, send a command that has VnmrJ itself write the value
  out (`write('file', ...)` or `shell(...)`) and read that file. Do
  not read `curpar` — or `exp*/curpar` — as the live state: VnmrJ
  flushes it lazily, minutes late, so the file describes some earlier
  state of the experiment. A stale `curpar` produced a false failure
  at SIU on 2026-09-16 (a driver check read a value the console had
  long since changed); it could as easily produce a false success.
* **`send2Vnmr` location.** The `listenon` manual page says
  `/vnmr/acqbin/send2Vnmr`; on this install it is
  `/vnmr/bin/send2Vnmr`. The `listenon` macro writes to
  `$vnmruser/.talk`. Anything scripting VnmrJ from outside should look
  in both places.
* **Do not shim against radiation damping.** On the 90/10 H₂O/D₂O tube
  the water line was ~32 Hz FWHM and asymmetric — it looks like a shim
  failure and is not one. Controlled test, identical shims and
  acquisition (gain 30, pw 0.05, tpwr 56, at 2 s), only the tube
  swapped: 1% H₂O + Gd, FWHM 8.50 Hz, asymmetry 1.00; 90% H₂O, FWHM
  32.37 Hz, asymmetry 1.59. Field inhomogeneity does not depend on
  proton concentration, so 3.8× broadening at ~90× proton density is
  radiation damping, and the FID shape agrees (log-magnitude curvature
  −301 vs +2.2; first-twelfth decay ratio 74× vs 13×). Three
  gradient-shim iterations on the 90/10 tube moved z3–z5 by hundreds
  of DAC units (z4 +747, z5 +658, z3 −376) while FWHM changed < 0.2 Hz
  and asymmetry < 0.02 — the shim iterated against a linewidth it
  cannot influence and the high-order terms diverged. Judge shim
  quality on the lock signal or a dilute/doped tube, and not by DAC
  magnitudes (z3 −8499, z4 7327, z3y −8304 gave the symmetric 8.5 Hz
  line). Dip depth goes as f_c·λ_r/λ_tot and λ_r *is* radiation
  damping, so a strongly RD-broadened near-neat sample is the regime
  that produces a large Guéron dip: the broad line is the phenomenon,
  not a defect.
* **Lock ON during the noise blocks**, unless there is a reason not to.
  Opening-to-closing reference drift: locked session +537.54 →
  +537.79 Hz (0.25 Hz over a 38 min bracket, procpar `time_run`
  17:36:25 → 18:14:37), asymmetry 1.66 → 1.66; unlocked session
  +533.79 → +534.35 Hz (0.56 Hz over a 78 min bracket, 14:42:27 →
  16:00:07), asymmetry 1.38 → 1.18. That is 0.0065 vs 0.0072 Hz/min —
  the same drift rate within what two brackets can show — and the
  unlocked session's lineshape change is confounded by its closing
  reference having been taken at 2.25× the opening flip angle (Tier-1
  step 4). The two sessions therefore do not measure what the lock
  buys; the recommendation is operator practice. (FWHM is not
  comparable between the sessions — different samples.) `alock='n'` in
  the block parameters disables automatic re-locking only. Record the
  state either way.
* **Scale the gain ladder to the sample.** The maximum non-overflowing
  gain was 60 dB on the 1% H₂O Gd-doped tube and 40 dB on the 90/10
  tube — same probe, same day. A fixed 0/20/40/60 ladder is not
  portable; a ladder scaled to the operator-found maximum is, and from
  v0.7.1 that means every integer-dB level in 5 dB steps from 0 to the
  maximum, each visited three times in one random order (Tier-1 step 1,
  checklist item 1; the September sessions' four evenly spaced gains
  were the first form of the scaling). The ~10% roll-off both sessions
  showed toward the top rung is degenerate with drift in a monotonic
  ladder, which is what the repeats and the randomization resolve. Read
  the stored `gain` back after setting it: the session-2 driver asked
  for 13.3 and 26.7 dB and the console stored 14 and 26, so the ladder
  that ran was 0/14/26/40 (the packer now WARNs when an answered rung's
  rg contradicts the stored gain).
* **Record length.** `np` ≤ 524288 caps `at` at 524288/(2·sw) — ~41 s
  at sw = 6410 Hz. Both sessions used 30 s records (62 and 60 noise
  blocks); derive the block count from a target duration.
* **Console clock.** Found set to America/Los_Angeles while in Central
  time and ~2 h 54 min fast in UTC, ntpd stopped for 92 days (ntp.conf
  fine, daemon not running); corrected before either session (zone
  set, stepped from the pool, hwclock synced, ntpd enabled, stratum-1
  GPS peer, offset ~−64 ms). Check `ntpq -pn` on the console before
  every session — the procpar timestamps are only as good as that
  clock.
* **The external acquisition driver.** Session 2 was acquired
  unattended by a Python 2.6 stdlib-only script running on the console
  workstation (RHEL 6.1, Python 2.6.6, OpenSSL 1.0.0 — the console
  cannot run the tooling or reach the endpoint, and never holds the
  token). It submits one block at a time into a running VnmrJ via
  `listenon` + `send2Vnmr`, waits for the `.fid` to land, then submits
  the next — no self-chaining, no `sn_*` parameters, ordinary
  prompting, debuggable. It makes a new session folder per run, writes
  `answers.json` *before* acquiring (an aborted session is still
  packable), derives `at` from the `np` cap and the block count from a
  target duration, scales the gain ladder to an operator-supplied
  maximum, takes both references identically, persists an operator
  profile so a repeat session re-asks only what changed, and has a
  `--dry-run` mode. Offered upstream as a contribution to this
  directory. It is the tested form of Tier 2 today; the MAGICAL macro
  remains the untested one.
* **Session 1 sample caveat — a worked example of why the H₂O fraction
  is the measurement.** The session-1 tube was a 1% H₂O in D₂O
  Gd-doped lineshape standard (0.1 mg/ml GdCl₃, 0.1% DSS): ~100×
  proton-diluted, plus paramagnetic broadening. Per PROTOCOL.md the
  2022 null had a 95% upper limit of 0.70% and neat water would show
  25–65%; at 100× dilution the expected dip is ~0.25–0.65%, at or
  below that limit, so session 1 cannot settle the 2022 question. It
  stands as a clean software-validation run and a known-fraction anchor
  point, not a physics result. Session 2 (90% H₂O, no Gd, no DSS,
  fraction recorded) is the one intended to bear on the physics.

## Bundle mapping summary (schema 2.0)

| meta.json field | source |
|---|---|
| `vendor` | `"agilent"` |
| `instrument.agilent.vnmrj_version` | answers `instrument.vnmrj_version` when given; else parsed from `/vnmr/vnmrrev` copied into the session directory as `vnmrrev` (checklist 8) |
| `instrument.agilent.receiver_gain_db` | procpar `gain` (dB, verbatim; noise-block = max) |
| `instrument.agilent.data_format` | `"varian-fid"` |
| `instrument.agilent.field_state_notes` | operator-entered (lock/z0 state — the BSMS-confirmation analog) |
| `spectrometer.h1_freq_mhz` | procpar `sfrq` (MHz) when `tn` is ¹H, from every experiment except the `sweep_signcal` 1D (its `sfrq` tracks the displaced `tof`); a disagreement among the rest is a WARN and takes the minimum |
| `spectrometer.field_tesla` | sfrq/42.5774806 |
| `spectrometer.probe_type` | `"RT"` for the partner instrument |
| `experiments[].role` | answers `experiments[].role` when given; else the Tier-1 save-name suffix: `_sn_setup` → setup, `_sn_ladder` → rg_ladder, `_sn_ref_open` → reference_open, `_sn_noise` → noise, `_sn_ref_close` → reference_close, `_sn_tune` → noise_tune, `_sn_signcal` → sweep_signcal; the role word may be followed by anything but a letter (`_sn_noise2`, `_sn_noise-1`, `_sn_noise_b` read; `_sn_noisetest` does not); an answered role that contradicts the name wins, with a WARN |
| `experiments[].tuning` | answers `experiments[].tuning` verbatim — `{setting_index (int ≥ 0, required), label, tune_reading, match_reading (number or null), units, note}`, no other keys, type-checked by the packer and then the schema; else `{"setting_index": k}` from an `_sn_tune_k` save name; a `noise_tune` block with neither gets a WARN |
| `calibration.rg_ladder` | answers verbatim — acquisition order, duplicates kept, never sorted or deduplicated — with a WARN per rung whose expno the session lacks or whose rg is more than 1% off that experiment's stored gain; omitted, built from the `rg_ladder` experiments' stored `gain` in meta order (tip_deg from `calibration.reference_tip_deg`, default 1.0) |
| `experiments[].td` | procpar `np` (total re+im points — same counting convention as Bruker TD) |
| `experiments[].td1_rows` | fid header `nblocks` (1 for the Tier-1 plain 1Ds) |
| `experiments[].sw_hz` | procpar `sw` |
| `experiments[].o1_hz` | procpar `tof` (Hz, verbatim, no sign flip). Axis sign resolved per bundle by the `sweep_signcal` pair — o1_hz(signcal) − o1_hz(reference) is the displacement and the line's apparent in-window offset drops by it on a physical axis, rises on a mirrored one — or, without the pair, by the VnmrJ lock referencing (procpar `reffrq`/`rfl`/`rfp` against the water shift); the partner console's axis is mirrored (item 2) |
| `experiments[].rg` | 10^(`gain`/20) (linear amplitude, Bruker-comparable; same mapping as the Magritek adapter) |
| `experiments[].ns` | procpar `nt` |
| `experiments[].aq_s_per_row` | procpar `at` (= np/(2·sw)) |
| `experiments[].started_local` | procpar `time_run` (`YYYYMMDDTHHMMSS`, console-local wall clock — checklist 6) |
| `experiments[].finished_local` | procpar `time_complete` (else `time_saved`) |
| `checksums` | SHA-256 of every packed `data/…` file |

## Attribution and references

* `fid`/`procpar` layout ported from **nmrglue**
  (`nmrglue/fileio/varian.py`, BSD-3-Clause, J. J. Helmus & C. P.
  Jaroniec; https://github.com/jjhelmus/nmrglue), which cites "Varian
  MR News 2005-04-18" and the Agilent *VnmrJ User Programming* manual
  as its own sources. Helmus & Jaroniec, *J. Biomol. NMR* **55**, 355
  (2013).
* **OpenVnmrJ** (https://github.com/OpenVnmrJ/OpenVnmrJ), the
  open-source continuation of VnmrJ: `src/common/manual/` pages for
  go/au, write, shell, svf, mkdir, exists, input, lookup,
  unixtime/systemtime, sleep, create; `src/common/maclib/cryo_noisetest`
  (the shipped receiver-only noise-test macro this port's design
  follows); `src/common/maclib/cft2da` (MAGICAL idiom reference).
* VnmrJ parameter semantics: UCSB "Basic VnmrJ Commands and Parameters"
  (https://nmr.chem.ucsb.edu/docs/compars.html — gain range/overflow/
  autogain); Northwestern IMSERC "Common Commands/Macros/Parameters
  VNMRJ 3.2A" (gain in dB; go/ga; sfrq/tof/pw/at/sw/nt); UIUC
  "Acquisition Time and Spectral Width in NMR" and UMN "Varian NMR
  Instructions" (at = np/(2·sw), console-side np recomputation).
