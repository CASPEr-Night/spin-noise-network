# Magritek Spinsolve port (Work Package B)

**Status: DRAFT PENDING BENCH VALIDATION.** Nothing in this directory has run
on a real Spinsolve. The Python side (reader, packer adapter, synthetic-data
generator) is tested end-to-end against the bundle contract today; the Prospa
macro is a draft whose every unverified construct is marked `UNVERIFIED` and
listed in the validation checklist below. Maintainer contact:
jwbquantum@gmail.com.

## Contents

| file | what it is | status |
|---|---|---|
| `spin_noise_run_spinsolve.mac` | Prospa/SpinsolveExpert acquisition macro (draft) | draft, needs bench session |
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
  The equivalent guarantee is expressed in software: the noise block uses a
  pulse program containing **no pulse event at all** (or, fallback, the
  standard sequence with pulse amplitude at its minimum setting — both paths
  are in the macro, both flagged for bench verification). No detach also
  means no detach-induced tuning-state steps, removing the pilot's main
  line-motion systematic.
- **No BSMS, no deuterium lock — but there IS a lock.** Spinsolve uses an
  *external hardware lock*: a separate reference sample and channel built
  into the magnet, no D₂O needed (vendor-documented across the family). The
  Bruker failure mode "field sweep left running" (the 2022 kHz-smearing
  lesson) has no direct equivalent; the analogous questions here are
  (i) whether the external lock actively steps B₀ during a noise block and
  (ii) how the magnet temperature regulation moves the line between blocks.
  Both are recorded (`environment.locked`, operator notes) and both are
  exactly what the interleaved references measure. **Bench session must
  determine whether the lock can and should be frozen during noise blocks.**
- **Permanent magnet thermal drift** replaces superconducting-magnet
  stability. Spinsolve regulates magnet temperature; residual drift between
  references is a fit parameter, not a surprise.
- **Receiver gain** is `rxGain` in dB (Prospa parameter name verified against
  real Kea/Prospa macros; the exact legal range on a Spinsolve is
  UNVERIFIED). The bundle stores Bruker-comparable *linear amplitude* gain
  `rg = 10^(rxGain_dB/20)` per experiment and the raw dB value in
  `instrument.magritek.rx_gain`.
- **N separate 1D noise acquisitions** instead of one pseudo-2D: Prospa
  saves each acquisition as a folder (`acqu.par` + `data.1d`), so the noise
  block is N repeated experiments, each a `meta.json` `experiments[]` entry
  with role `noise`. The packer preserves acquisition order via `expno`.
- **Console clock audit**: same wall-clock-vs-acquisition-duration trick as
  TopSpin in principle; what Prospa exposes for per-block timestamps is
  UNVERIFIED, so the draft macro records its own start/end times and the
  packer forwards them; precision to be established at the bench.

## Two automation paths (research summary)

1. **SpinsolveExpert + Prospa macro (the path this port takes).**
   SpinsolveExpert is Magritek's scripting-capable software: pulse-program
   editing/compilation plus the Prospa macro language (the same language as
   Magritek Kea consoles — `procedure`/`endproc`, `#` comments,
   `if/endif`, `for … next`, `try/catch/endtry`, `getpar`/`setpar`,
   `$var$` string interpolation; verified against published Prospa macros).
   Only this path can express a no-pulse acquisition and direct `rxGain`
   control.
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
3. In SpinsolveExpert/Prospa: run `spin_noise_run_spinsolve` and answer the
   six questions (or pre-edit the constants block at the top of the macro).
4. The macro runs ladder → reference → N noise blocks → reference, writing
   each block to the session directory plus `answers.json`. Default session
   ~45 min; operator needed for the first ~5.
5. On any machine with Python 3:
   `python3 vendors/magritek/magritek_reader.py pack <session_dir>` →
   `spinnoise_*.zip`; then the normal
   `python3 uploader/upload_bundle.py <zip>`.

## File-format facts used by the reader (with provenance)

- Session data files: `acqu.par` (acquisition parameters) and `data.1d`
  (FID) per experiment folder; `spectrum.1d`/`spectrum_processed.1d` are
  processed products and are ignored by the packer. [Source: nmrglue
  `fileio/spinsolve.py` (BSD-3), which reads real Spinsolve data.]
- `acqu.par` is plain text, one `key = value` per line; values in double
  quotes are strings, otherwise int-then-float-then-string. [nmrglue, same
  file.]
- `.1d` binary layout: 32-byte header of eight 4-byte little-endian fields
  `[owner, format, version, dataType, xDim, yDim, zDim, qDim]`, then IEEE
  float32 little-endian payload; for a 1D acquisition of N complex points
  the payload is 3N floats — N x-axis values followed by interleaved
  re/im pairs. [nmrglue, same file.] The meaning of the header magic values
  and `dataType` codes is UNVERIFIED (nmrglue reads past them without
  decoding); the reader checks structural consistency (payload length vs
  dimensions) rather than magic values.
- Parameter names `b1Freq`, `rxGain`, `rxPhase`, `nrPnts`, `dwellTime`,
  `nrScans` are the Prospa-ecosystem names (verified in published Kea Prospa
  macros; `bandwidth`, `lowestFrequency`, `rxChannel` additionally verified
  in nmrglue's acqu.par handling). Their exact spelling and units **in a
  Spinsolve-written acqu.par** are marked UNVERIFIED in code and checked
  leniently (the reader records what it finds verbatim).

## Validation checklist — first bench session on a real Spinsolve

Work through in order; each item closes specific `UNVERIFIED` marks in
`spin_noise_run_spinsolve.mac` / `magritek_reader.py`:

1. [ ] SpinsolveExpert version and Prospa version strings; where they are
       machine-readable (fills `instrument.magritek.*_version`).
2. [ ] Confirm the Prospa macro loads and the dialog/constants flow runs
       (syntax fixes expected — the draft is written to be fixable in
       minutes by a Prospa-literate operator).
3. [ ] Pulse-program path: confirm how SpinsolveExpert defines/compiles a
       sequence with **no pulse event**, or the minimum-amplitude fallback;
       verify zero transmitter output during the noise block (scope or
       spectrum check if available).
4. [ ] `rxGain`: legal range and step on this model; confirm dB convention;
       find max stable gain with no ADC clipping on a noise block.
5. [ ] External lock: can it be disabled/frozen during a noise block? Does
       it pulse or sweep anything into the receiver band? Record behavior.
6. [ ] acqu.par ground truth: dump one real file, confirm parameter names,
       units (`dwellTime` μs assumed; `b1Freq` MHz assumed), and which
       fields identify model/software versions.
7. [ ] data.1d ground truth: confirm header fields and payload layout against
       the reader on a real file (`magritek_reader.py inspect <file>`).
8. [ ] Timestamps: what Prospa offers for wall-clock ms (clock-audit
       feasibility); record timezone handling.
9. [ ] Run the full chain on real data: session → `pack` → `--selftest` →
       upload to the staging endpoint.
10. [ ] Measure loaded Q and estimate η (feeds the feasibility memo's
        biggest unknowns); then look for the dip — expected within minutes
        if the memo's box holds.

## Bundle mapping summary (schema 2.0)

| meta.json field | source |
|---|---|
| `vendor` | `"magritek"` |
| `instrument.magritek.spinsolve_software_version` | answers.json (operator) until item 1 above is closed |
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
