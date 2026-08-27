# Nanalysis NMReady path — converter-first strategy

**Status: DRAFT PENDING PARTNER VALIDATION.** No NMReady has run this
protocol yet. The validation partner is Boyd Goodson's group (Southern
Illinois University Carbondale): a 60 MHz **NMReady 60PRO**, software
**Nanalysis v2.2.6.2**, firmware **v2.6.0**, benchtop, room temperature, no
automated tune/match unit. Everything below that could not be verified
against real documentation or real instrument files is marked UNVERIFIED
and appears in the partner validation checklist at the end.
Contact: John W. Blanchard <jwbquantum@gmail.com>.

## Strategy in one paragraph

Like the JEOL path, this is **converter-first**, in two tiers. **Tier 1
(available now, draft):** an NMReady operator follows a short manual
checklist — the same physics protocol as the Bruker run, acquired with the
instrument's ordinary touchscreen tools as a series of 1D experiments —
exports each record as a JCAMP-DX **FID** file, and our converter
(`vendors/nanalysis/nanalysis_reader.py`) turns the exported files plus a
one-page questionnaire into a contract-conforming network bundle. The JCAMP
ingestion is not new code: it reuses the already-tested decoder from the
JEOL path, and the Nanalysis label vocabulary on top of it was verified
against **real NMReady instrument output** (see the research record).
**Tier 2 (requires the partner session):** establish, on Boyd's unit, the
two things no public document settles — whether a no-pulse acquisition is
expressible at all (Experiment Designer and/or NMReady-CONNECT), and
whether acquisition can be queued/scripted so the operator load drops
toward the Bruker path's five minutes. Tier 1 ships today because it
depends only on things we could actually verify; Tier 2 is a co-development
plan, not a promise.

## What is actually documented publicly (research record, 2026-08-27)

* **Data export.** The NMReady saves spectra/FIDs as **JCAMP-DX** (`.dx`),
  plus pdf/png/csv; files can be exported to a **USB key** or a **network
  share** (SMB folder configured under Setup → System → Network Share), and
  the instrument runs Linux (Ubuntu) internally. Save options: dx locally,
  dx to USB, dx to network folder, png screen capture, print-to-pdf.
  [Nanalysis 100/60 MHz User Manual (© 2020), §5.0–5.1 "Saving and
  Exporting Data", §6.3 "Results", §6.4.2.2 networking; copy hosted by WPI:
  https://www.wpi.edu/sites/default/files/2025-07/Nanalysis-100-60-user-manual.pdf]
* **Real NMReady JCAMP-DX files exist in public and we tested against
  them.** Nanalysis Corp.'s own open-source JCAMP parser
  (github.com/nanalysis/jcamp-parser, GPLv3 — used as *documentation only*,
  no code taken) ships genuine instrument exports in its test corpus,
  including a 60 MHz 1D ¹H **FID** export
  (`NMReady_1D_1H_20210909_Test_formates.dx`, writer "Nanalysis NMReady
  v2.2.4.5" — one minor release below the partner's v2.2.6.2) and the same
  data as a processed-spectrum export (`...formatesS.jdx`), plus a 100 MHz
  file. `nanalysis_reader.py inspect` parses the real 60 MHz FID export
  cleanly, recovering the FID first-points exactly against the file's own
  `##FIRST=` values.
* **The exports are metadata-rich** (this is the pleasant surprise of this
  vendor path — generic JCAMP exports usually carry no gain and no
  carrier). Verified present in the real files: the software version inside
  the `##JCAMP-DX=5.01 $$ Nanalysis NMReady v2.2.4.5` comment
  (machine-readable); a timezone-qualified timestamp `##LONG DATE=
  2021/09/09 15:54:27+0200` **and** `##$DATE=<unix epoch>` (they agree to
  the second); `##$RECVR_GAIN` (= `##$RG`); Bruker-style acquisition labels
  `##$TD` (re+im convention, cross-checked against NPOINTS/VAR_DIM),
  `##$SWH` (Hz, = 1/dwell), `##$O1` (Hz, consistent with `##$O1P` ppm ×
  `##$SFO1`), `##$SFO1`/`##$BF1`, `##$AQ`, `##$NS`/`##$SCANS`, `##$DW`
  (half-dwell µs); pulse bookkeeping `##.OBSERVE 90` and `##$X_PULSE`
  (µs-consistent) and `##$PULSE_AMPLITUDE`; `##$TOTAL DURATION`;
  `##.FIELD` ($$ Tesla); `##TEMPERATURE` (Celsius-like, reads as the
  regulated magnet temperature); `##SPECTROMETER/DATA SYSTEM=NMReady
  60/<hostname>`. FID exports are `DATA CLASS=NTUPLES` with AFFN
  `(X++(R..R))`/`(X++(I..I))` pages — exactly the published-standard layout
  our JEOL-path decoder already handles.
* **Acquisition surface (stock touchscreen software).** Experiment
  Settings exposes: spectral width, number of points, scan delay, number of
  scans, spectral center, dummy scans (manual §3.2.2); **Pulse Width
  settable in degrees or microseconds**, and **Receiver Gain either auto or
  a fixed value** (§3.2.2 decoupling/pulse-width discussion). Before each
  acquisition the stock flow "performs a quick lock and automatic receiver
  gain adjustment" (§3.2) — the noise protocol must pin the gain instead.
  Experiment list includes Nutation (measures the 90° pulse), T1/T2, and
  **"designer (for advanced pulse programming)"**.
* **Automation surface.** Three distinct, all *optional* (licensed)
  features, none publicly documented at the pulse-sequence level:
  1. **Queuing** (manual §7.1): build a queue of configured experiments on
     the touchscreen, run unattended. Enough for Tier-1 repetition if the
     partner's unit has it.
  2. **Experiment Designer** (manual §3.2.2.21, §7.3): "design their own
     pulse programs, customizing the number of pulses, timing, phases etc."
     — the plausible clean route to a genuine no-pulse acquisition. Details
     are "contact your Nanalysis customer service representative"; **not
     publicly documented — the partner session must establish this.**
  3. **NMReady-CONNECT** (manual §6.4.2.2; nanalysis.com/software-packages;
     spec sheets list "API: Microsoft .NET & JSON"): a remote API to "set
     up, launch, and monitor experiments, monitor your instrument's
     performance, retrieve results in JCAMP-DX-format, and manage
     auto-shimming"; LabVIEW integration is a vendor-published example, and
     a developer zone exists (nanalysis.com/developer-zone, login
     required). Whether CONNECT can set pulse width/amplitude to zero, pin
     the receiver gain, or run a designer sequence is **not publicly
     documented — partner session must establish**. VNC remote control of
     the touchscreen is also available (§6.4.2.2 Remote sub-tab), which at
     minimum lets Boyd's group drive long sessions from a desk.
* **Instrument physics facts** (manual + spec sheets): 60 MHz = 1.4 T
  thermally regulated permanent magnet (regulated *above* room temperature;
  real files read `TEMPERATURE=33.0`); **internal lock on ¹H or ²H** (no
  external lock sample — the lock is a built-in channel; deuterated solvent
  recommended but a proteo lock exists, enabled per-unit by Nanalysis);
  lineshape spec LW(50%) < 1.0 Hz for the 60PRO vintage, < 0.5 Hz claimed
  for current production (see FEASIBILITY_60MHZ.md for citations);
  automated quick/medium/full shim routines plus standby shimming
  (§6.2.8, §8); status screen reports internal temperatures, frequency
  drift, and linewidth (§6.2).

## What ships in this directory

| file | what it is | status |
|---|---|---|
| `nanalysis_reader.py` | JCAMP-first session reader + standalone bundle packer + duck-typed `NanalysisReader` VendorReader adapter; also exports the schema-wiring constants | tested against synthetic data AND smoke-tested against a real v2.2.4.5 60 MHz FID export |
| `make_synthetic_nanalysis.py` | builds a fake NMReady session (real-file label vocabulary, deterministic values, stamped `desktest`) | working |
| `test_nanalysis_chain.sh` | synthetic session → reader → bundle → uploader `--selftest` against a patched schema copy → meta assertions → adapter smoke test; **needs no shared-file wiring** | working, run it |
| `FEASIBILITY_60MHZ.md` | the Guéron-dip estimate at 60 MHz with Nanalysis-spec parameters | done |
| `README.md` | this file: strategy, research record, operator + partner checklists, deferred-wiring instructions | — |

End-to-end today:

```
python3 vendors/nanalysis/nanalysis_reader.py pack <session_dir>   # -> spinnoise_*.zip
bash vendors/nanalysis/test_nanalysis_chain.sh                     # the whole chain, no hardware
```

## Tier 1 — the operator checklist

One 5 mm tube of water, ~45 minutes of magnet time, ordinary touchscreen
tools (plus the queue, if licensed). Each exported file gets the **numeric
prefix** that maps it onto the network experiment plan (same role numbering
as the Magritek session tree; noise records count up from 20). Export
everything into one directory (USB key or network share), as the JCAMP-DX
**FID** export — one file per step.

| Step | role | NMReady Tier-1 action | Export as |
|---|---|---|---|
| 0. Sample + setup | `setup` | 5 mm tube, ~550 µL water (tap/distilled/D2O-doped — record which). Insert, let the magnet-temperature equilibration settle, run the standard shim (quick or medium; record which and the reported linewidth). Run a Nutation experiment once to get the calibrated 90° pulse, or accept the instrument's stored value — the exports record it (`##.OBSERVE 90`). | (optional) `01_sn_setup.dx` |
| 1. Gain ladder | `rg_ladder` | Four 1D ¹H acquisitions, tiny flip (Pulse Width ~1 **degree** — the degrees unit in Experiment Settings makes this easy), 1 scan each, at four ascending **fixed** Receiver Gain values ending at the maximum that does not overflow. Receiver Gain must be set to a fixed value, NOT left on auto. Units of RECVR_GAIN are unverified — record the four values verbatim (the exports carry them too); do NOT convert. | `10_sn_ladder_a.dx`, `14_sn_ladder_b.dx`, `15_sn_ladder_c.dx`, `16_sn_ladder_d.dx` |
| 2. Opening reference | `reference_open` | ONE 1D, same tiny flip, 1 scan, at the ladder's maximum gain. | `11_sn_ref_open.dx` |
| 3. Noise block | `noise` | Repeated **minimum-excitation** 1Ds: Pulse Width set to 0 if the software accepts it (partner item 2; otherwise the smallest accepted value — 0.1° if the degrees field allows it — honestly recorded: the export's `##$X_PULSE` IS the record), fixed Receiver Gain at the ladder maximum, 1 scan, 0 dummy scans, the largest number of points × narrowest sensible spectral width to maximize the acquisition time per record (partner item 6 establishes the console limits; the real corpus file runs 2048 complex points at 735 Hz ≈ 2.8 s — aim for ≥ 10 s per record if the settings allow). Note the lock nucleus in use (2H if the sample is D2O-doped, 1H proteo lock, or — if the software permits — none) and whether the instrument was between standby-shim cycles. Repeat until ≥ 30 min total; use the queue if licensed. | `20_sn_noise.dx`, `21_sn_noise.dx`, `22_sn_noise.dx`, … |
| 4. Closing reference | `reference_close` | Identical to step 2. | `13_sn_ref_close.dx` |
| 5. Operator log | six-dialog equivalent | Fill in `answers.json` in the same directory (keys: `institution, city, country, facility_slug, contact_email, contact_consent, model, software_version, firmware_version, lock_nucleus, field_state_notes, sample_description, h2o_fraction_pct, d2o_pct, additives, tube_od_mm, sample_volume_ul, vt_setpoint_k, operator_notes, p90_us, ref_tip_deg, shim_ok` — the synthetic generator writes a complete example). The H₂O-fraction question is the measurement (the 2022 lesson). | `answers.json` |
| 6. Pack | bundle zip | On any machine with Python 3: `python3 vendors/nanalysis/nanalysis_reader.py pack <dir>`. **Export the FID, not the processed spectrum**: the FID export is `DATA TYPE=NMR FID` / `DATA CLASS=NTUPLES`; the reader warns if it sees a spectrum-only (`XYDATA`) export. Which Save menu choice produces which on v2.2.6.2 is partner item 1. | `spinnoise_*.zip` |

Notes for the operator: nothing here pulses the sample at high power or
touches the instrument beyond ordinary shim/lock use; the stock software's
automatic receiver-gain step is the one behavior that must be actively
overridden (fixed gain), and the tiny residual excitation of a
minimum-width pulse is not a bug — logged, it is a built-in micro-reference
(the 2022 attenuator lesson applied).

### Partner checklist specifics for Boyd's unit (60PRO, v2.2.6.2, fw 2.6.0)

* The verified real files were written by v2.2.4.5; v2.2.6.2 is one minor
  release up. First action: export one ordinary ¹H FID and run
  `nanalysis_reader.py inspect` on it — if the label set matches, the whole
  Tier-1 path is live immediately (checklist item 1).
* 60PRO units have no auto tune/match (consistent with "no ATM" for this
  unit) — tuning state is fixed hardware, which *removes* the Bruker
  pilot's detach-induced tuning-step systematic, and means the detuning
  ladder from the science protocol is not available on this node. The
  temperature-contrast physics point survives; note it in
  `field_state_notes`.
* Ask Nanalysis (or check the license screen) whether Queuing, Experiment
  Designer, or NMReady-CONNECT are enabled on this unit — each upgrades a
  Tier-2 item from "not possible" to "test it".

## Tier 2 — co-developed automation (what only the partner session can establish)

* whether **pulse width 0** (or amplitude 0) is accepted by the stock 1D
  experiment, and whether the transmitter chain is verifiably silent when
  it is (scope/spectrum check, or the noise floor itself);
* whether **Experiment Designer** can express a genuine no-pulse
  acquire-only sequence, and whether it is licensed on the partner unit;
* whether **NMReady-CONNECT** (JSON/.NET API) can run the whole session
  (fixed gain, long acquisitions, repeated records, JCAMP retrieval) from a
  workstation script — if yes, this vendor path gets a Bruker-class
  orchestrator, and the network clock audit gets workstation-side
  wall-clock stamps around every record;
* the **receiver-gain transfer curve** (ladder against a fixed ~1° signal);
* **timestamp semantics**: which event `##LONG DATE`/`##$DATE` stamp
  (acquisition start vs file save) and their resolution — until then,
  NMReady bundles contribute noise records and temperature-contrast points,
  not clock-audit points (the packer deliberately writes NO `clock_audit`
  block from these files);
* **lock behavior during acquisition**: does the internal lock transmit
  in-band or step B₀ during a record; can it be disabled for the noise
  block (and if not, does a ²H lock on a D₂O-doped sample leave the ¹H band
  clean — likely, but must be seen).

## What cannot be promised until a partner session

Three things in particular we can NOT promise today, and will not imply.
First, **no-pulse acquisition**: the stock software always pulses; whether
zero pulse width is accepted, and whether Designer/CONNECT can do better,
is unknown — the Tier-1 checklist ships the honest fallback
(minimum-width pulse, verbatim-recorded). Second, **receiver-gain
semantics**: the parameter is verified present (`##$RECVR_GAIN`, observed
value 14 on the real 60 MHz file) but its units, step quantization, and
linearity are not — bundles record it verbatim, and no cross-gain
calibration will be attempted until the partner ladder measurement. Third,
**automation**: Queuing, Designer, and CONNECT are all optional licensed
packages with no public pulse-level documentation; if none is enabled on
the partner unit, Tier 1 is a patient-operator protocol (repeated
touchscreen acquisitions — workable, ~30 button presses, and VNC remote
control makes it a desk job) and that is what we will say it is.

## Bundle mapping summary (schema-2.0-shaped)

| meta.json field | source |
|---|---|
| `vendor` | `"nanalysis"` |
| `instrument.nanalysis.software_version` | `##JCAMP-DX` label comment (`$$ Nanalysis NMReady v…`, machine-readable, verified), else answers.json |
| `instrument.nanalysis.firmware_version` | answers.json (no machine-readable source verified) |
| `instrument.nanalysis.receiver_gain` | `##$RECVR_GAIN` verbatim (units UNVERIFIED) |
| `instrument.nanalysis.lock_nucleus` | answers.json (`2H`/`1H`/`off`/`unknown`) |
| `spectrometer.h1_freq_mhz` | `##$SFO1` (else `##.OBSERVE FREQUENCY`) |
| `spectrometer.field_tesla` | `##.FIELD` verbatim (else ν/γ) |
| `experiments[].td` | `##$TD` verbatim (already re+im convention; cross-checked vs NPOINTS) |
| `experiments[].sw_hz` | `##$SWH` (else 1/`##FACTOR`[X]) |
| `experiments[].o1_hz` | `##$O1` (Hz, verified consistent with O1P×SFO1) |
| `experiments[].rg` | `##$RECVR_GAIN` verbatim |
| `experiments[].aq_s_per_row` | `##$AQ` |
| `experiments[].started_local` | `##LONG DATE` (anchor event UNVERIFIED) |
| `experiments[].finished_local` | started + `##$TOTAL DURATION` (derived, same caveat) |
| `local_timezone_offset_min` | the `##LONG DATE` offset when all files agree |
| `calibration.p90_us` | answers.json, else `##.OBSERVE 90` (µs-consistent, UNVERIFIED) |
| `calibration.rg_ladder[].tip_deg` | 90 × `##$X_PULSE` / `##.OBSERVE 90` |
| `checksums` | SHA-256 of every packed `data/…` file |

## Deferred wiring (follow-up commit — shared files, NOT touched here)

This directory is self-contained on purpose: a sibling work package is
editing the shared packer/schema/README concurrently. The follow-up wiring
commit consists of exactly three edits, and the payloads for the first two
are exported by `nanalysis_reader.py` as constants (the chain test already
validates bundles against a schema patched with them):

1. **`schema/meta.schema.json`**: append
   `nanalysis_reader.SCHEMA_VENDOR_ENUM_VALUE` (`"nanalysis"`) to
   `properties.vendor.enum`, and add
   `properties.instrument.properties.nanalysis =
   nanalysis_reader.INSTRUMENT_SCHEMA_BLOCK`.
2. **`packer/pack_bundle.py`**: register the adapter in `VENDOR_READERS`.
   The class is already written and smoke-tested here; the packer-side
   shim mirrors the existing `JeolReader`/`MagritekReader` lazy-import
   pattern:

   ```python
   class NanalysisReader(VendorReader):
       """Delegates to vendors/nanalysis/nanalysis_reader.py (see there)."""
       name = "nanalysis"
       def __init__(self):
           self._mod = None
           self._impl = None
       def _get(self):
           if self._impl is None:
               import importlib.util
               here = os.path.dirname(os.path.abspath(__file__))
               path = os.path.abspath(os.path.join(
                   here, "..", "vendors", "nanalysis",
                   "nanalysis_reader.py"))
               if not os.path.isfile(path):
                   raise PackError("vendors/nanalysis/nanalysis_reader.py "
                                   "not found (looked at %s)" % path)
               spec = importlib.util.spec_from_file_location(
                   "snn_nanalysis", path)
               mod = importlib.util.module_from_spec(spec)
               spec.loader.exec_module(mod)
               self._impl = mod.NanalysisReader(pack_error=PackError)
           return self._impl
       def discover_experiments(self, data_dir):
           return self._get().discover_experiments(data_dir)
       def read_experiment(self, dirpath):
           return self._get().read_experiment(dirpath)
       def instrument_block(self, answers, discovered):
           return self._get().instrument_block(answers, discovered)

   VENDOR_READERS = {
       ...,
       "nanalysis": NanalysisReader,
   }
   ```
3. **Top-level `README.md`**: add the support-matrix row — Nanalysis |
   converter-first (JCAMP-DX FID exports) | draft pending partner
   validation | `vendors/nanalysis/`.

After the wiring lands, `test_nanalysis_chain.sh` keeps passing unchanged
(its patched schema copy becomes a no-op patch), and a central-packer leg
analogous to the Magritek test's can be added.

## Attribution and references

* JCAMP-DX standard: McDonald & Wilks, Appl. Spectrosc. **42**, 151 (1988);
  Davies & Lampen, "JCAMP-DX for NMR", Appl. Spectrosc. **47**, 1093
  (1993). Decoder reused from `vendors/jeol/jeol_reader.py` (this
  repository; already validated end-to-end on the JEOL path).
* Real NMReady output: Nanalysis Corp., `jcamp-parser` test corpus
  (github.com/nanalysis/jcamp-parser, GPLv3) — used as format
  *documentation/fixtures only*; no GPL code is ported into this
  repository.
* Nanalysis 100 MHz & 60 MHz User Manual (© 2020 Nanalysis Corp.; copy
  hosted at wpi.edu) — export flow, Experiment Settings, queuing, Designer,
  CONNECT, shimming, lock.
* NMReady-60PRO spec sheets: yairtech.co.il 60PRO brochure;
  nanalysis.com/nmready-60pro (current "Nanalysis-60" page);
  nanalysis.com/software-packages (NMReady-CONNECT).

---

*Maintained by John W. Blanchard (jwbquantum@gmail.com), with Claude
(Anthropic, San Francisco, California, USA).*
