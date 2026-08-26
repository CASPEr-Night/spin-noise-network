# JEOL path — converter-first strategy

**Status: DRAFT PENDING HARDWARE VALIDATION.** No JEOL spectrometer has run
this protocol yet. Everything below that could not be verified against real
documentation or real data files is marked UNVERIFIED and appears in the
partner validation checklist at the end.
Contact: John W. Blanchard <jwbquantum@gmail.com>.

## Strategy in one paragraph

The Bruker path automates acquisition end-to-end because TopSpin exposes an
embedded scripting layer that is publicly documented. JEOL's Delta software
has no comparably documented public automation API, so we do not pretend to
have one. Instead the JEOL path is **converter-first**, in two tiers.
**Tier 1 (available now, draft):** a JEOL operator follows a short manual
checklist — the same physics protocol as the Bruker run, acquired with
Delta's ordinary tools as a series of 1D experiments — and our converter
(`vendors/jeol/jeol_reader.py` + `packer/pack_bundle.py --vendor jeol`)
turns the saved files plus a one-page questionnaire into a
contract-conforming network bundle. **Tier 2 (requires a partner facility):**
co-develop Delta-side automation (experiment queue, a no-pulse experiment
file, machine-readable timing) with a facility that runs JEOL hardware, so
the operator load drops toward the Bruker path's five minutes. Tier 1 ships
today because it depends only on things we could actually verify; Tier 2 is
a co-development plan, not a promise.

## What is actually documented publicly (research record, 2026-08-26)

* **Delta software.** Delta is JEOL's standard acquisition/processing
  software for the JNM-ECZR/ECZS/ECA/ECX/ECS/ECA II/ECX II series (JEOL
  product pages), and the ECZ Luminous (JNM-ECZL G/R/S) series announced
  2021-11-01 supports remote multi-operator access and continuous automation
  with a sample changer (JEOL news release 20211101.5146). JEOL publishes
  short "Delta Tips" application notes (NMDT series) rather than a public
  scripting manual.
* **Automation surface.** JEOL's application note NM190013E ("Implementation
  of interleaved experiments with Delta Software") demonstrates that Delta
  experiment/pulse-program files are user-editable and that scan-loop
  behavior is controlled through acquisition parameters — evidence that a
  custom no-pulse experiment is *plausible*, not proof. Delta 4's automation
  writes a machine-readable `console.log` from which method timings can be
  extracted (open-source `delta4logtimes` project). No public document
  describes a supported external scripting API; that is exactly what Tier 2
  must establish with the partner.
* **JCAMP-DX export.** Delta and third-party tools (JEOL JASON, Mnova) can
  convert `.jdf` to JCAMP-DX; JCAMP-DX itself is a published standard
  (McDonald & Wilks, Appl. Spectrosc. 42, 151 (1988); Davies & Lampen,
  "JCAMP-DX for NMR", Appl. Spectrosc. 47, 1093 (1993)). The exact
  menu path and export flavor per Delta version is UNVERIFIED (partner
  checklist item 6) — which is why the converter accepts the standard's
  AFFN and DIFDUP/SQZ forms, XYDATA and NTUPLES layouts, rather than one
  guessed dialect.
* **The `.jdf` native format.** No public specification exists, but the
  MIT-licensed open-source parser `jeolconverter` (cheminfo, npm; author
  Julien Wist) implements the layout, and the companion `jeol-data-test`
  corpus publishes 38 real Delta files (1D and 2D, ECA/ECZ-era, Delta
  5.x–6.x — the files' own version parameters read 5.1.2 through 6.2).
  Our reader ports the jeolconverter layout **with attribution** and — going
  beyond the port — was verified directly against the corpus during
  development: magic bytes `JEOL.NMR`, the 1360-byte header, the 64-byte
  parameter records, the packed creation dates (they match dates embedded in
  corpus filenames), the 1990-epoch second-resolution
  `actual_start_time`/`end_time` parameters (day-level match against an
  operator-typed title), the receiver-gain parameter names
  (`recvr_gain`, `recvr_gain_limit`), and full 1D + 2D data-section reads
  on all 38 files. See the provenance block at the top of
  `jeol_reader.py` for the exact verified/unverified split.

## What ships in this directory

| File | Purpose |
|---|---|
| `jeol_reader.py` | Stdlib-only reader: native `.jdf` (ported + corpus-verified layout) and JCAMP-DX (published standard; AFFN + SQZ/DIF/DUP, XYDATA + NMR NTUPLES). CLI: `python3 jeol_reader.py file.jdf --json`. |
| `make_synthetic_jeol.py` | Deterministic synthetic session generator (`.jdf` and `.jdx`), so the whole chain is testable today without a spectrometer. |
| `test_jeol_reader.py` | 14 tests: JCAMP end-to-end (exact numeric recovery, DIFDUP vs AFFN cross-check), `.jdf` round-trip on the verified layout, adapter mapping, and the full packer chain for both formats. Optional: `JEOL_REAL_DATA_DIR=<dir>` re-runs the reader over real files. |

The packer adapter lives in `packer/pack_bundle.py` (`JeolReader`), which
delegates parsing to this directory. End-to-end:

```
python3 packer/pack_bundle.py <session_dir> --answers answers.json --vendor jeol
python3 uploader/upload_bundle.py spinnoise_<slug>_<stamp>_<hex>.zip
```

The JCAMP-DX route is the **robust fallback**: it is a genuinely documented
text format, our decoder is validated end-to-end, and any Delta version that
can export JCAMP-DX can join the network even if its `.jdf` vintage
surprises us. Its cost: generic JCAMP exports carry no receiver gain and no
carrier offset, so those values must come from the operator log
(`answers.json`). The native `.jdf` route auto-discovers receiver gain,
sweep, frequency, scan counts, timestamps and the Delta version guess.

## Tier 1 — the operator checklist

One 5 mm tube of water, ~45 minutes of magnet time, ordinary Delta tools.
Each saved file gets a **numeric prefix** that maps it onto the Bruker
experiment plan (`topspin/spin_noise_run.py`; PROTOCOL.md) so the analysis
treats both fleets identically. Save everything into one directory, as
native `.jdf` (preferred) — one file per step.

| Step | Bruker equivalent (expno) | JEOL Tier-1 action | Save as |
|---|---|---|---|
| 0. Sample + setup | 1 (`setup`) | 5 mm tube, ~550 uL water (tap/distilled/D2O-doped — record which). Insert, set VT, equilibrate. Tune/match 1H, shim as usual, calibrate the 1H 90-degree pulse (or record the probe default). Write down p90 and the power setting. | (optional) `01_sn_setup.jdf` |
| 1. Gain ladder | 10/14/15/16 (`rg_ladder`, RG = 1/8/64/max) | Four `single_pulse` 1Ds, tiny flip (pulse width ~ p90/90, i.e. ~1 degree), 1 scan each, at four ascending receiver-gain settings ending at the maximum that does not overflow. JEOL gain units differ from Bruker RG — record the four `recvr_gain` values verbatim (the file records them too); do NOT convert. | `10_sn_ladder_a.jdf`, `14_sn_ladder_b.jdf`, `15_sn_ladder_c.jdf`, `16_sn_ladder_d.jdf` |
| 2. Opening reference | 11 (`reference_open`, small-flip pseudo-2D, 8 rows) | ONE `single_pulse` 1D, same tiny flip, 1 scan, at the ladder's maximum gain. (Deviation from Bruker's 8-row reference, accepted for Tier 1: the drift check needs one record before and one after the noise block; envelope statistics come from the noise records themselves. Tier 2 automation restores the 8-row form.) | `11_sn_ref_open.jdf` |
| 3. Noise block | 12 (`noise`, no-pulse pseudo-2D, ~19 s rows, ~30 min) | Repeated **no-pulse** 1Ds: pulse width set to 0 (if your Delta accepts it — see partner item 2; otherwise minimum pulse width + maximum transmitter attenuation, and say so in the notes), receiver gain at the ladder maximum, 1 scan, the longest acquisition time your console allows per record (aim >= 10 s), lock and any field sweep/compensation OFF if your instrument permits — describe the field state in the questionnaire. Repeat until >= 30 min total (e.g. 90+ records at 19 s). | `12_sn_noise.jdf`, then `17_sn_noise.jdf`, `18_sn_noise.jdf`, ... (12 first, then count up from 17; 13 is reserved) |
| 4. Closing reference | 13 (`reference_close`) | Identical to step 2. | `13_sn_ref_close.jdf` |
| 5. Operator log | six TopSpin dialogs + `meta.json` | Fill in `answers.json` (copy `packer/answers.example.json`, set `"vendor": "jeol"`): facility, sample (H2O fraction!), VT setpoint, lock/field state, p90, and — important for the clock audit — wall-clock start/finish times per file if you can note them (`experiments[].started_local/finished_local`). | `answers.json` |
| 6. Pack + upload | automatic zip + uploader | `python3 packer/pack_bundle.py <dir> --answers answers.json --vendor jeol`, then upload the printed zip. The packer validates before you send and tells you exactly what is missing. | bundle zip |

Notes for the operator: nothing in this protocol pulses your sample at high
power or touches the probe beyond ordinary tune/shim; the sample dialog's
H2O-fraction question is the measurement (see PROTOCOL.md for the 2022
lesson); and if your facility allows physically detaching or muting the
transmitter path for the noise block, that is a valuable extra — record
what you did in the notes.

## Tier 2 — Delta automation, co-developed with a partner facility

Goal: one command, ~5 operator minutes, like the Bruker path. What we can
bring: the bundle contract, the packer, the analysis, and a tested
synthetic-data harness. What only the partner session can establish
(because it is not publicly documented):

* whether a no-pulse experiment is expressible as a Delta experiment file
  (a `.jxp` with the pulse element removed or zeroed), and how row-repeated
  acquisition ("pseudo-2D") is best expressed;
* whether Delta's automation/queue can run our sequence unattended and what
  its scripting/batch surface actually is on the partner's Delta version;
* how to timestamp records to better than one second (console.log mining as
  in `delta4logtimes`, queue logs, or workstation-side wrappers) for the
  network clock audit;
* the receiver-gain transfer curve (ladder measurement against a fixed
  small signal).

## What cannot be promised until a partner session

Three things in particular we can NOT promise today, and will not imply.
First, **receiver-gain semantics**: we verified the parameter *names*
(`recvr_gain`, `recvr_gain_limit`) against real files, but not the units,
step quantization, or linearity of JEOL's gain chain — so JEOL bundles
record gain verbatim, and no cross-gain calibration will be attempted until
a partner ladder measurement maps the transfer curve. Second, **no-pulse
acquisition**: the entire noise protocol assumes the receiver can acquire
with no excitation pulse; on Bruker we ship a pulse program that provably
does this, but whether Delta accepts a zero-length pulse, and whether the
transmitter chain is genuinely silent when it does, is unknown — the
Tier-1 checklist offers a fallback (minimum pulse width + maximum
attenuation, honestly recorded) and the partner session must qualify the
clean solution. Third, **timestamp precision**: the network's clock audit
needs wall-clock-vs-console-clock comparisons; `.jdf` files carry
second-resolution start/end times whose timezone we could not verify, which
is sufficient for bookkeeping but NOT for the audit's parts-in-1e7
ambitions — until the partner session finds a sub-second machine-readable
timing source on the JEOL side, JEOL bundles contribute noise records and
temperature-contrast points, not clock-audit points.

## Partner validation checklist

Each item is an UNVERIFIED assumption or open question carried by the code;
the reader/adapter stays "draft" until these are checked off on real
hardware. (Cross-referenced from `jeol_reader.py` and
`packer/pack_bundle.py` comments.)

1. **`recvr_gain` units and linearity.** Names verified in real files
   (values 56.0 with limit 102.0 on a 500 MHz ECA-era file); units
   (dB-like?), step size, and amplitude linearity unknown. Run the RG
   ladder against a fixed ~1-degree signal; fit the transfer curve.
2. **No-pulse expressibility.** Can `single_pulse` (or a custom `.jxp`)
   run with pulse width 0? Is the TX chain silent (check with a spectrum
   analyzer or the noise floor itself)? If not: qualify the
   min-pulse + max-attenuation fallback and measure the residual tip.
3. **Timestamps.** Timezone and accuracy of `actual_start_time`/`end_time`
   (1990-epoch, 1 s resolution — epoch verified at day level on one corpus
   file); meaning of the packed-date words' low 16 bits (time-of-day?);
   any sub-second machine-readable timing source (console.log, queue log)
   for the clock audit.
4. **Delta version provenance.** The `.jdf` `version` parameter reads
   "5.3.2 [Windows -" on a corpus file (16-char truncation). Confirm it is
   the Delta release across versions, or find the authoritative field.
5. **Carrier-offset semantics.** Is `x_offset` the O1 analog? Sign and
   reference convention, and unit context per experiment type (ppm vs Hz —
   the adapter converts only unambiguous units and defers otherwise).
6. **JCAMP-DX export recipe.** Exact menu path and export flavor (NTUPLES
   vs paired blocks, AFFN vs DIFDUP) on the partner's Delta version; add
   one real exported file to the test suite as a regression fixture.
7. **Endianness and vintages.** All 38 corpus files are little-endian; the
   reader honors the header's endian flag but the big-endian path is
   untested. Establish which console/Delta vintages (ECZ vs ECZL vs ECA)
   produce what.
8. **Console limits for the noise block.** Maximum points and acquisition
   time per 1D record on the partner's console (Bruker path uses ~19 s
   rows); the actual `recvr_gain_limit` meaning.
9. **External reference input.** Whether the partner's console generation
   accepts an external 10 MHz reference (future GPSDO clock option;
   facility consent required) — record model and connector, do not touch.
10. **Live-console surprises.** Parse a fresh session from the partner's
    instrument with `JEOL_REAL_DATA_DIR` pointed at it; fix whatever the
    corpus did not teach us.

## Attribution and references

* `.jdf` layout ported from **cheminfo/jeolconverter** v1.0.1 (MIT license,
  Julien Wist and contributors; https://www.npmjs.com/package/jeolconverter),
  `src/parseJEOL.js` and `src/conversionTables.js`, with independent
  byte-level verification against the **jeol-data-test** corpus (npm,
  cheminfo) — 38 real Delta `.jdf` files. The version is pinned at 1.0.1;
  the project's former GitHub repository URL now returns 404, so the npm
  package page is the canonical source (`npm pack jeolconverter@1.0.1`
  retrieves the exact MIT-licensed tarball if a local provenance copy is
  ever needed).
* JCAMP-DX: McDonald & Wilks, Appl. Spectrosc. **42**, 151 (1988);
  Davies & Lampen, Appl. Spectrosc. **47**, 1093 (1993).
* JEOL Delta and ECZ/ECZL series: JEOL product pages and the 2021-11-01
  ECZ Luminous news release; application note NM190013E (interleaved
  experiments); "Delta Tips" NMDT application-note series;
  `delta4logtimes` (A. Botana, GitHub) for Delta 4 console.log timing.
