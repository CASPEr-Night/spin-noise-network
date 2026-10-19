# Pulse calibration kept as data: the nutation block (v0.8.2 design, 2026-10-19)

**Why.** Every bundle to date records the 90-degree pulse only as a number (calibration.p90_us,
p90_power_db_or_w, from pulsecal or the operator). The calibration FIDs are not retained, and no
stored experiment is acquired at any flip angle other than the ~1-degree small flip (P1 = P90 with
the power attenuated by SMALL_FLIP_EXTRA_DB = 39.08 dB). The tip angle that enters kappa*M0, and so
every exclusion, therefore rests on a power-attenuation assumption. John's requirement (2026-10-19):
the pulse calibration must be in the bundle as data, so that a sceptic can re-derive P90 and the
small-flip tip from the raw files. Physics side: the 90-degree amplitude also pins the pulsed
signal level against the spin-noise terms (the figure fig_amplitudes_realdata.png has a dashed
"NOT acquired" line where this measurement belongs).

## Console script (topspin/spin_noise_run.py) — section 8c, after the RG ladder, before the row probe
- New constants: `EXP_NUTATION = range(80, 96)` reserved (at most 16 steps; the sweep's future
  layout uses 100+/200+, the present verify 1Ds 31..45, rdopt 20..26, noise 50+ — 80..95 is free),
  `NUTATION_MULT = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]` (P1 = mult x P90: the
  180-degree null at 2.0 and the 270-degree peak at 3.0 locate P90 by symmetry, independent of
  amplitude calibration), `NUTATION_D1_S = 20.0` (>= 5 T1 of water at 11.7 T), `TD_NUTATION =
  TD_LADDER`, argv flag `nonutation` (skip; meta records why) and `NUTATION_ON = 1` default.
- Each step is a quick 1D exactly like acquire_quick_1d (zg, TD_LADDER, SWH_HZ, 1 scan, D11 as
  set), but at the CALIBRATED 90-degree power: PLdB1/PL1 = p90_db (the read-back parameter db_par,
  the same one set_small_flip writes), P1 = mult x p90_us. New helper `set_full_power(p90_db,
  db_parname)` next to set_small_flip; if p90_db is None (legacy console, blank dialog) the block is
  SKIPPED with meta reason "p90 power unknown", never run at an unknown power.
- Receiver gain: run `rga` ONCE on the first step that is P1 = P90 (mult 1.0 is acquired first,
  then the remaining multipliers in list order), keep that RG for all steps; record it. Never reuse
  the small-flip ladder's RG for a full-power FID.
- Clock audit per expno (clock_block_begin/end, role "nutation"), record_experiment(role
  "nutation"), clear_raw_data before zg, run_zg_and_wait attended=1 (this is the attended setup
  stage; the operator is at the console), ocxo_expected_s with D1 = NUTATION_D1_S.
- meta.json: `calibration.nutation = {"expnos": [...], "p1_us": [...], "multipliers": [...],
  "p90_us_assumed": p90_us, "power_db": p90_db, "power_parname": db_par, "rg": rg, "d1_s":
  NUTATION_D1_S, "td": TD_NUTATION, "skipped": false}` or `{"skipped": true, "why": "..."}`.
  Bundle packer: add the nutation expnos to all_expnos. SCRIPT_VERSION "0.8.2".
- Stub (testing/topspin_stub.py): zg at full power returns an FID of amplitude
  A90 |sin(pi/2 P1/P90_true)| with an RD-shortened decay (use a P90_true the stub owns, 8.5 us by
  default, so a harness check can see the script's declared P90 differ from the truth by a
  known 3%); the existing flavours keep passing; add one flavour where rga fails on the full-power
  step (fallback RG = 1, recorded) and one with `nonutation`.

## Report (analysis/facility_report.py)
- `analyze_nutation(bundle, meta, fs_default)` -> `science.nutation`: per step the early
  time-domain amplitude with analyze_reference_row's estimator (A0_counts; same group-delay
  handling as the references), plus the spectral line amplitude the ladder uses, both reported;
  a saturation check per step (max |int32 sample| against 2^31, flat-top run length) ->
  `clipped` list. Fit A(P1) = A90 |sin(pi P1 / (2 P90))| over the unclipped steps (least squares,
  P90 and A90 free, start from the declared P90); outputs p90_fit_us, p90_fit_err_us,
  p90_declared_us, ratio_fit_over_declared, A90_counts (at the nutation RG), chi2_dof, n_steps,
  the step table. Cross-check: `small_flip_check = {"predicted_A1_counts_at_ref_rg": A90 x
  sin(tip_deg) x (RG_ref/RG_nut) x (ladder gain bridge as the transduction uses it),
  "measured_A1_counts": reference A0 (mean of the readable references), "ratio_measured_over_
  predicted", "note"}`; with p90_fit the implied tip of the references: tip_from_nutation_deg =
  90 x P1_ref/p90_fit x 10^(-39.08/20)... state the formula used. HEADLINE UNCHANGED: kappa*M0
  and the exclusion keep the declared tip (byte-for-byte regression on every existing bundle must
  show additions only); the nutation enters as a cross-check key `transduction.tip_cross_check`
  and an honesty line when it is present.
- QA rows: WARN "nutation P90" if |ratio_fit_over_declared - 1| > 0.05; WARN "small-flip
  linearity" if the measured/predicted ratio is outside 0.9-1.1; OK rows otherwise; INFO when the
  block is absent or skipped (older bundles, nonutation) — never FAIL.
- Figure `nutation` (alt='nutation'): amplitude vs P1 with the fit, declared and fitted P90
  marked; HTML section "Pulse calibration kept as data" after the RG ladder section; JSON under
  science.nutation; the Agilent path reports `available: false, why: "not acquired by the
  Agilent driver"` until the driver gains the block.
- Synthetic bundles (testing/make_synthetic_bundle.py): optional nutation expnos 80.. with a
  known P90_true; test_report_bruker_refs.py (or a new testing/test_nutation_report.py): the
  fit recovers P90_true within 1%, the small-flip ratio within 2% on a clean synthetic, the
  clipped-step exclusion works, an old bundle without the block gives available=false and the
  rest of report.json byte-identical except added keys.

## Schema (schema/meta.schema.json) and docs
- Add optional `calibration.nutation` (object as above, with `skipped`/`why`); keep schema_version
  "1.2" if the validator tolerates the additive field under calibration (check
  additionalProperties there); otherwise bump to "1.3" and accept both in the script's selftest,
  the uploader's validator and the report (facility_report accepts the supported-versions list).
- PROTOCOL.md: new subsection under the calibration section ("Pulse calibration kept as data"),
  expno table row 80..95, the timing cost (~4 min), what the report derives, and the rule that
  the block runs at the console's calibrated power only. README.md v0.8.2 bullet (unreleased).
  docs/TROUBLESHOOTING.md: full-power FID overloading the receiver (rga on the 90-degree step is
  the remedy; what a clipped step looks like in the report), radiation damping near the
  180-degree null (why the fit weights the 90- and 270-degree steps and reports chi2).
- House rules: no claims of publication; the Agilent/SIU path unchanged until Phoenix and Boyd
  are asked; the block's first live run is attended (standing rule for anything never run on
  hardware).

## Addendum (2026-10-19, John): a dense receiver-gain ladder

**Why.** Four rungs visited once cannot separate receiver compression from signal drift (the
report says so: `rg_ladder.model = degenerate_single_visit`), and the bridge from the reference
gain to the noise gain (x20 in amplitude at Oulu) is an extrapolation between rungs that bracket
it, inflated into D_cal by the ladder's envelope. A sceptic wants the gain curve measured at the
two gains that matter and densely in between, twice, so that compression and drift separate.

**Console script.** Keep the four legacy rungs (expnos 10, 14, 15, 16; RG 1, 8, 64, rga) exactly
as they are (max_rg comes from rung 16's rga as before). Then, unless the argv flag `sparseladder`
is given, acquire the dense ladder in reserved expnos 300..363:
- Levels: geometric from RG 1 to max_rg at two levels per octave (1, 1.4, 2, 2.8, 4, 5.7, 8, 11,
  16, 23, 32, 45, 64, 90, 128, 181, ... up to max_rg), plus, always and exactly, the reference
  gain (moderate_rg = max_rg/4) and the noise gain (max_rg) as levels of their own. The console
  quantises a requested RG; the acqus value is the record (as the report already handles).
- Two visits per level: visit 1 in a seeded random permutation of the levels, visit 2 in the
  reverse order, so every level is seen once early and once late. Seed = the session date as an
  integer, recorded, so a re-run is reproducible. About 34 rungs x (1.2 s acquisition + 2 s D1 +
  console overhead) = 3 to 4 minutes, attended, right after the legacy rungs and before the
  nutation block.
- Each rung is a small-flip quick 1D exactly like a legacy rung (zg, TD_LADDER, SWH_HZ, 1 scan,
  D1_REF_S, set_small_flip, set_rg(level), clear_raw_data, clock block role "rg_ladder",
  record_experiment role "rg_ladder"). Entries are appended to calibration.rg_ladder in the
  existing format plus `"visit": 1|2` and, once per session, `calibration.rg_ladder_dense =
  {"expnos": [...], "levels": [...], "seed": N, "visits": 2, "skipped": false}` (or the skipped
  form with `why`: "sparseladder flag"). Packer: add the expnos to all_expnos.
- Stub and harness: the stub already answers small-flip 1Ds with the script's own mock; the
  harness expectations gain the 300.. rungs (role rg_ladder, pulprog zg) and a check that both
  visits of every level exist, that moderate_rg and max_rg appear as levels, and that
  `sparseladder` produces the skipped form and no 300.. expnos.

**Report.** `analyze_rg_ladder` already fits the multi-visit model (amp = A rg c(rg)(1 + beta
(t - t0))); verify it on 17 levels x 2 visits + 4 legacy rungs (synthetic bundle option
`--dense-ladder`), report per-level compression c(rg) with errors, beta, and the envelope from the
drift-corrected compression as today. Add `rg_ladder.bridge_measured = {"rg_ref", "rg_noise",
"amplitude_ratio_fitted", "amplitude_ratio_linear", "ratio_of_ratios", "err"}` from the fitted
per-level amplitudes at the two gains the transduction bridges; add `transduction.rg_bridge_
cross_check` (the kappa*M0 the headline would have with the measured bridge, `applied_to_
headline: false`); the headline stays on the declared linear bridge so every existing bundle
regresses with additions only. Figure: amplitude / RG against RG for both visits with the fitted
compression curve; QA WARN "RG ladder bridge" when ratio_of_ratios is outside 0.97-1.03, INFO
when the dense ladder is absent. Docs: PROTOCOL (the rung count and time), TROUBLESHOOTING (a
rung the console refuses at a quantised RG), INSTALL expno map (300..363).
