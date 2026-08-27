# Agilent/Varian path — converter-first strategy

**Status: DRAFT PENDING HARDWARE VALIDATION.** No Agilent/Varian
spectrometer has run this protocol yet. The validation partner is
identified: a **400 MHz Agilent DD2 running VnmrJ 3.2** (Boyd Goodson,
SIU Carbondale), 5 mm HCN probe, room temperature, **no autotune/match**
— the partner checklist at the end is written against that machine.
Everything below that could not be verified against real documentation,
OpenVnmrJ source, or real data files is marked UNVERIFIED and appears in
that checklist.
Contact: John W. Blanchard <jwbquantum@gmail.com>.

## Strategy in one paragraph

Like the JEOL path, the Agilent path is **converter-first**, in two
tiers — but with one important difference in our favor: unlike Delta,
the VnmrJ macro language (MAGICAL) is genuinely public, because
**OpenVnmrJ** (github.com/OpenVnmrJ) is the open-source continuation of
VnmrJ — same macro language, same manual pages, same shipped macro
library lineage as the proprietary VnmrJ 3.2 on the partner instrument.
**Tier 1 (available now, draft):** the operator follows a short manual
checklist — the same physics protocol as the Bruker run, acquired with
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
a documented-idiom draft awaiting its first bench session.

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
  Magritek had real V2.02.27 output), so the format implementation is
  *documentation-verified only* until the partner session parses a real
  session (checklist item 10).
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
  (IMSERC VnmrJ 3.2A sheet; UCSB reference). The `tof` sign/reference
  convention as an O1 analog is UNVERIFIED (checklist item 2).

## What ships in this directory

| File | Purpose |
|---|---|
| `agilent_reader.py` | Stdlib-only reader: binary `fid` + `procpar` per the nmrglue-documented layout (structural checks, no magic-value assertions). CLI: `python3 agilent_reader.py inspect <dir.fid>`. |
| `spin_noise_run.mac` | Tier-2 DRAFT MAGICAL acquisition macro (wexp-chained state machine; see its header). Never run on hardware; every unsettled construct is UNVERIFIED(n)-marked against the checklist below. |
| `make_synthetic_agilent_data.py` | Deterministic synthetic session generator (`.fid` directories + packer questionnaire), so the whole chain is testable today without a spectrometer. |
| `test_agilent_chain.sh` | synthetic session → `packer/pack_bundle.py --vendor agilent` → `uploader --selftest` → meta.json + inspector assertions. Working; run it. |

The packer adapter lives in `packer/pack_bundle.py` (`AgilentReader`),
which delegates parsing to this directory. End-to-end:

```
python3 packer/pack_bundle.py <session_dir> --answers answers_packer.json --vendor agilent
python3 uploader/upload_bundle.py spinnoise_<slug>_<stamp>_<hex>.zip
```

## Tier 1 — the operator checklist

One 5 mm tube of water, ~45 minutes of magnet time, ordinary VnmrJ
tools — no macro needed. Each `svf` save gets a **numeric prefix** that
maps it onto the Bruker experiment plan (`topspin/spin_noise_run.py`;
PROTOCOL.md) so the analysis treats both fleets identically. Save
everything into one directory, one `.fid` per step.

| Step | Bruker equivalent (expno) | VnmrJ Tier-1 action | Save as |
|---|---|---|---|
| 0. Sample + setup | 1 (`setup`) | 5 mm tube, ~550 µL water (tap/distilled/D₂O-doped — record which). Insert, set/record temperature, equilibrate. Tune/match ¹H **by hand** (the partner probe has no autotune), shim as usual, calibrate the ¹H 90° pulse (or record the probe-file value). Write down pw90 and tpwr. | (optional) `01_sn_setup` |
| 1. Gain ladder | 10/14/15/16 (`rg_ladder`) | Four `s2pul` 1Ds, tiny flip (`pw` ≈ pw90/90, i.e. ~1°), `nt=1`, at four ascending `gain` settings (e.g. 0/20/40/60 dB) ending at the maximum that does not overflow the receiver. **Set `gain` explicitly — never `gain='n'` (autogain).** Record the four dB values; the procpar records them too. | `svf('10_sn_ladder_a')` … `svf('16_sn_ladder_d')` (prefixes 10/14/15/16) |
| 2. Opening reference | 11 (`reference_open`) | ONE `s2pul` 1D, same tiny flip, `nt=1`, at the ladder's maximum gain, `at` ≈ 2 s. (Deviation from Bruker's 8-row reference, accepted for Tier 1 exactly as on the JEOL path.) | `svf('11_sn_ref_open')` |
| 3. Noise block | 12 (`noise`) | Repeated **no-pulse** 1Ds: `pw=0 tpwr=-16 nt=1 ss=0 pad=0`, gain at the ladder maximum, the longest `at` your console allows comfortably per record (aim ≥ 10 s), autoshim and autolock off (`wshim='n' alock='n'`), lock state your choice — **describe the lock/z0 state in the questionnaire**. Repeat until ≥ 30 min total. This is Agilent's own receiver-only idiom (`cryo_noisetest`). | `svf('12_sn_noise')`, then `17_sn_noise`, `18_sn_noise`, … (12 first, then count up from 17; 13 is reserved) |
| 4. Closing reference | 13 (`reference_close`) | Identical to step 2. | `svf('13_sn_ref_close')` |
| 5. Operator log | six TopSpin dialogs + `meta.json` | Fill in the questionnaire (copy `packer/answers.example.json`, set `"vendor": "agilent"`, add `instrument.vnmrj_version`, `instrument.field_state_notes`): facility, sample (**H₂O fraction!**), temperature, lock state, pw90/tpwr. | `answers_packer.json` |
| 6. Pack + upload | automatic zip + uploader | `python3 packer/pack_bundle.py <dir> --answers answers_packer.json --vendor agilent`, then upload the printed zip. The packer validates before you send and lists exactly what is missing. | bundle zip |

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

## What cannot be promised until the partner session

Four things in particular we can NOT promise today, and will not imply.
First, **the format implementation has never met a real file**: the
fid/procpar layout is ported from nmrglue's documented reader, but no
public corpus of raw VnmrJ `.fid` directories was found (unlike JEOL's
38-file corpus), so a fresh session from the DD2 must be parsed before
the reader is trusted. Second, **receiver-gain semantics**: `gain` is
dB by documentation, but the DD2's legal values, step size, and
amplitude linearity are unmeasured — bundles record dB verbatim (and
the Bruker-comparable linear mapping 10^(dB/20) is labeled as exactly
that), and no cross-gain calibration will be attempted until the
partner ladder maps the transfer curve. Third, **transmitter silence at
pw=0**: Agilent's own noise-test macro treats `pw=0 tpwr=-16` as
receiver-only, but whether the DD2's TX chain emits anything during
such a block has to be measured, not assumed — the 2022 lesson.
Fourth, **the macro itself**: `spin_noise_run.mac` is built exclusively
from documented constructs and a shipped-macro precedent, but VnmrJ 3.2
predates the current OpenVnmrJ tree; version drift (wexp quoting,
created-parameter behavior, svf path handling) is expected to cost
minutes of fixing at the bench, not architecture changes — and until
that session happens the macro stays labeled DRAFT and Tier 1 is the
recommended path.

## Partner validation checklist (Boyd Goodson's DD2, VnmrJ 3.2)

Each item is an UNVERIFIED assumption or open question carried by the
code; the reader/adapter/macro stay "draft" until these are checked off
on the real instrument. `UNVERIFIED(n)` marks in `spin_noise_run.mac`,
`agilent_reader.py`, and `packer/pack_bundle.py` cross-reference these
numbers.

1. **`gain` values and linearity.** Legal receiver-gain values and step
   on the DD2 (documentation says dB, typically 0–60); the maximum
   noise-block gain with no ADC/receiver overflow; then the RG ladder
   against a fixed ~1° signal to fit the amplitude transfer curve.
   Snap the macro's `$ladgain*`/`$noisegain` defaults to legal values.
2. **`tof`/`sfrq` conventions.** Confirm `sfrq` (MHz, observe channel,
   with `tn='H1'`) is the right `h1_freq_mhz`, and the `tof` sign and
   reference convention as the Bruker-O1 analog; fix the adapter if the
   convention differs.
3. **No-pulse silence.** With `s2pul pw=0 tpwr=-16`: is the transmitter
   chain measurably silent during a noise block (spectrum analyzer on
   the TX path if available, otherwise the noise floor itself)? Measure
   the residual tip if any; record the result in the questionnaire
   notes. If the facility can physically mute/detach the TX path,
   qualify that too.
4. **DSP and record limits.** Whether `dsp='n'` applies/behaves on the
   DD2 (inline vs realtime DSP); maximum `np`/`at` per 1D record (the
   macro defaults to 10 s records; longer is better for the noise
   floor); any oversampling settings that change the fid layout.
5. **Macro mechanics on VnmrJ 3.2.** Install `spin_noise_run.mac`, run
   a 2-noise-block session: wexp quoting/recursion, created `sn_*`
   parameter persistence across blocks, `input()` numeric typing,
   `mkdir('-p')`, `systemtime` format strings. Expect minutes of format
   fixing, not architecture changes.
6. **Timestamps and the clock audit.** `unixtime` resolution on the
   workstation and its NTP discipline (`chronyc tracking`/`ntpq -pn`);
   then wire `spin_noise_times.txt` into per-experiment
   `started_local`/`finished_local` and a schema-1.2+ `clock_audit`
   block (the Bruker orchestrator's audit needs block wall-clock stamps
   + OCXO-implied durations — both are available here: at·nt per
   block). Also check what timestamps procpar itself carries
   (`time_run`?) and their timezone.
7. **`svf` behavior.** Absolute session paths, the exact file set
   written on 3.2 (procpar/fid/text/log), collision behavior, and
   whether `svf(..., 'nodb')` is preferable on a database-enabled
   install.
8. **VnmrJ version provenance.** A machine-readable source for the
   VnmrJ version (procpar parameter? `/vnmr/vnmrrev`?) so
   `instrument.vnmrj_version` stops being operator-entered.
9. **External reference input.** Whether the DD2 console accepts an
   external 10 MHz reference (future GPSDO clock option; facility
   consent required) — record model and connector, do not touch.
10. **Live-console surprises.** Parse a fresh session from the DD2 with
    `agilent_reader.py inspect` and the packer; fix whatever the
    documentation did not teach us (procpar record wrapping, arrayed
    parameters, status-bit surprises, big-endian assumptions).

## Bundle mapping summary (schema 2.0)

| meta.json field | source |
|---|---|
| `vendor` | `"agilent"` |
| `instrument.agilent.vnmrj_version` | operator-entered (checklist 8) |
| `instrument.agilent.receiver_gain_db` | procpar `gain` (dB, verbatim; noise-block = max) |
| `instrument.agilent.data_format` | `"varian-fid"` |
| `instrument.agilent.field_state_notes` | operator-entered (lock/z0 state — the BSMS-confirmation analog) |
| `spectrometer.h1_freq_mhz` | procpar `sfrq` (MHz) when `tn` is ¹H |
| `spectrometer.field_tesla` | sfrq/42.5774806 |
| `spectrometer.probe_type` | `"RT"` for the partner instrument |
| `experiments[].td` | procpar `np` (total re+im points — same counting convention as Bruker TD) |
| `experiments[].td1_rows` | fid header `nblocks` (1 for the Tier-1 plain 1Ds) |
| `experiments[].sw_hz` | procpar `sw` |
| `experiments[].o1_hz` | procpar `tof` (convention UNVERIFIED, item 2) |
| `experiments[].rg` | 10^(`gain`/20) (linear amplitude, Bruker-comparable; same mapping as the Magritek adapter) |
| `experiments[].ns` | procpar `nt` |
| `experiments[].aq_s_per_row` | procpar `at` (= np/(2·sw)) |
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
