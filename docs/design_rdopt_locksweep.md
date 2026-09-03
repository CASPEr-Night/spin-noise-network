# Design: rd-optimize and lock-referenced field sweep

Status: in development on `master` (targeted for v0.5.0, alongside the
pulse-program-derived clock audit). Both features are OPTIONS of the
TopSpin orchestrator `topspin/spin_noise_run.py`, off by default:

    xpy spin_noise_run rdopt          # tuning scan before the session
    xpy spin_noise_run sweep          # field-stepped noise blocks
    xpy spin_noise_run rdopt sweep    # both
    (composable with simulate / desktest as usual)

Origin: commitments to the ASU partner session (2026-08-27/28) -- (a)
radiation-damping parameter optimization "should be automated in the
software package"; (b) with a 90/10 H2O/D2O sample "the lock could
drive a sweep". The 850's TCI cryoprobe has ATM, so (a) is actuatable
there; (b) needs only a lockable sample and the BSMS field/lock
controls every console has.

## 1. rd-optimize: probe-tuning scan for maximum radiation damping

WHY. The spin-noise signature and the phased-model calibration both
grow with the radiation-damping rate lambda_r ~ eta*Q*gamma*M0, and
lambda_r depends on where the probe resonance sits relative to the
Larmor frequency. The conventional `atma` match (50 ohm at SFO1) is
optimized for pulsing, not for spin-noise pickup -- the spin-noise
literature (Nausner/Mueller 2009; Poeschko et al.) finds the useful
tuning optimum offset from the pulse optimum by tens to hundreds of
kHz on cryoprobes. We scan it empirically instead of assuming.

HOW. After the standard setup block (atma + shim + P90):

1. For each offset Dk in an operator-editable ladder (default
   0, -100, -200, -350, -500, +100, +250 kHz -- weighted BELOW nominal
   because the spin-noise literature finds cryoprobe optima 150-500 kHz
   below the pulse tune: Nausner 2010, Poeschko 2014, and a measured
   -488 kHz on a 600 TCI; 0 is always first, as the baseline):
   a. set O1 = o1_water + Dk (temporarily), run `atma` (guarded, as in
      setup), restore O1. The probe is now tuned Dk AWAY from the
      observe frequency. If atma fails at the first step, the feature
      is skipped with a note in meta (no ATM = no actuation; scan
      results would be meaningless).
   b. acquire a quick small-flip 1D (TD_LADDER-size, fixed RG,
      existing set_small_flip machinery), expnos 20+.
   c. measure the FID envelope decay rate in pure Jython (struct-parse
      the fid, log-linear fit of |signal|): for a small tip from +z,
      the envelope decays at 1/T2* + lambda_r, and T2* is fixed by the
      shim -- so argmax(decay rate) over the ladder = argmax(lambda_r).
      No FFT, no processing chain, version-proof. Amplitude is
      recorded too (a tuning so far off that pickup collapses is
      disqualified: amplitude >= 0.3 x max required).
2. Choose D* = argmax decay rate among qualified points; require
   >= 10% improvement over D=0, else keep D=0 ("pulse tune already
   RD-optimal at this precision").
3. Re-tune at D* (same O1-shift trick).
4. When D* != 0: re-run pulsecal AT THE CHOSEN TUNING and confirm the
   new P90 with the operator (the SNTO literature does the same after
   retuning). A mistuned probe lengthens the 90-degree pulse, and the
   whole pulsed sensitivity chain -- RG ladder, references, sweep
   verifications -- rides on the tip angle; both the setup and the
   recalibrated P90 are recorded. The session then runs at D*.

RECORDED: meta.calibration.rd_optimize = {enabled, offsets_khz,
decay_rates_per_s, amplitudes, chosen_offset_khz, improvement_frac,
note}. Each scan 1D is a clock-audit block (role rdopt_scan) -- more
blocks, better clock fit, for free.

MOCK MODES: simulate/desktest mock the acquisitions, so there is no
fid to measure; the scan records nulls, chooses 0, and notes
"mocked". The harness asserts the structure, not the physics.

## 2. Field-stepped noise blocks ("lock sweep")

WHY. Every distinct Larmor frequency is a distinct axion mass point
(nominal field != actual field; the mass coordinate is the measured
carrier). The BSMS field adjustment spans O(kHz..tens of kHz) at 1H --
tens of axion linewidths (~5e-7 fractional, ~300 Hz at 600 MHz). A
ladder of field steps turns one magnet into a LOCAL MASS SCAN of
several adjacent points in a single overnight session.

HOW -- operator-in-the-loop with measurement verification. There is no
portable programmatic field-shift command across TopSpin/BSMS
generations (verified against the Bruker BSMS/ELCB manuals, 2026-08-31:
the H0 value lives behind the BSMS panel/keyboard/service web, driven
internally over CORBA; the GETFIELD/PUTFIELD-style AU macros sometimes
rumored do not exist). The script therefore does not actuate; it
PLANS, INSTRUCTS, MEASURES, and RECORDS.

FIELD MECHANICS (from the BSMS manuals; primary method per JWB,
2026-08-31 -- "why not just lock the deuterium resonance? It doesn't
force the 1H signal to be anywhere in particular"): the lock servo
holds B0 wherever the 2H lock REFERENCE points -- it constrains the
1H line only through B0. So the sweep's PRIMARY actuator is the LOCK
SHIFT (the 2H reference; +/-200 ppm range, 0.001 ppm resolution): set
the shift, RE-LOCK, and autolock carries the field to the new
setpoint (capture ~1000 field units ~ 8 kHz at 1H covers any step
here). The step size is exact and magnet-independent: 1 ppm of lock
shift = 1 ppm of B0 = BF1 Hz at 1H by definition. The lock is turned
OFF for each acquisition (the standing leak-hygiene rule; staying
locked is permitted and recorded). Re-locking is only a trap at a
FIXED reference -- autolock would pull a manually stepped field
straight back -- which is why the FALLBACK method (consoles without
accessible shift mode) is unlocked FIELD-DAC stepping with no relock
until the sweep ends: ~8 Hz per field unit at 1H standard bore
(~4-6 Hz wide bore; the H0 DAC spans ~ +/-80 kHz at 1H). Either way
the MEASURED offsets are the record. The BSMS field SWEEP must stay
off (standing protocol rule), and the field needs a few seconds to
settle after each step (eddy currents).

1. Dialog: number of steps N (default 5) and half-span in Hz (default
   +/-1500 Hz, capped at +/-3000 Hz -- the acquisition band is
   +/-SWH/2 = +/-3450 Hz and the line must stay verifiable inside
   it). Targets evenly spaced, low to high; per-step noise duration =
   selected total / N, floor 300 s. The operator can decline at the
   baseline gate, falling back to the standard single noise block
   (never an abort -- earlier data is kept).
2. Per step k:
   a. dialog: set the Lock Shift to target/BF1 ppm and re-lock (or,
      fallback, shift the FIELD by ~target/8 units unlocked), then
      lock off.
   b. verification 1D (expnos 30+): measure the dominant line's offset
      from the carrier via the pulse-pair estimator (pure Jython, no
      FFT); the MEASURED offset -- never the target -- is the record.
      The estimator's sign convention is a receiver property: it is
      resolved against the operator's shift direction on the first
      large step and recorded (sign_convention_flip). Off-target by
      more than max(20%, 30 Hz): warn dialog (retry / accept / skip).
      Stale-data protection: fid/ser inherited from the WR() template
      copy are deleted before every acquisition, so a silently failed
      zg can never pass the previous experiment's data off as a fresh
      measurement.
   c. noise block (zgnoise2d, standard parameters, expnos 50+), rows
      sized to the per-step duration.
   The operator can END the sweep at any step dialog, keeping
   everything acquired; nothing after acquisition begins can abort
   the session.
3. After the last step: reset the Lock Shift to baseline + re-lock
   (autolock carries the field back) + verification 1D; the
   restoration quality is recorded (field_restored), then
   reference_close runs as usual.

RECORDED: meta.field_sweep = {enabled, requested_half_span_hz, steps:
[{index, target_offset_hz, measured_offset_hz, verify_expno,
noise_expno, rows}], restored_offset_hz}. Every verification and
noise block is a clock-audit block. Experiments get roles
sweep_verify / noise_sweep.

ANALYSIS CONTRACT. Each noise_sweep block is its own mass point: the
facility report analyzes each independently (line fit + PSD), and the
registry/coverage view counts them as separate frequencies. The
per-step measured offset + the session carrier metadata define the
absolute frequency of each step.

## 2b. v0.6 update: carrier-follow ("spectral tiling")

Adopted from the science roadmap's flagship recommendation
(docs/science_roadmap.md, 1.1). Three changes to the sweep:

1. CARRIER-FOLLOW. Per-step O1 = baseline O1 + target, for BOTH the
   verification 1D and the noise block (recorded per step as
   carrier_o1_hz). The line stays centered in the window at every
   step, so the +/-3000 Hz window cap is gone; the new half-span cap
   is a conservative lock-shift excursion (SWEEP_SPAN_PPM_MAX = 25 ppm
   of B0), with each individual RE-LOCK jump limited to
   SWEEP_HOP_MAX_HZ = 4000 Hz (autolock capture safety; bigger
   entry/exit jumps are instructed as multi-hop re-locks). Step count
   raised to 15. Verification now measures the line's LOCAL offset;
   the physical step is target + sign*local_deviation, and analysis
   seeds per-step fits with the small deviation, not the full offset.
2. DETERMINISTIC SIGN CALIBRATION. Carrier-follow removes the sign
   information the v1 first-large-step inference used (a correct step
   reads ~0 local offset under either convention). The convention is
   now resolved at the baseline by one quick 1D at a carrier moved
   +500 Hz with the field untouched (expno 29, role sweep_signcal,
   no operator action): physical convention reads -500 Hz. Recorded
   as sign_convention_basis.
3. NUCLEUS AWARENESS (19F pilot, docs/f19_pilot.md). The observed
   nucleus is inherited from the template (NUC1) and recorded
   (spectrometer.observe_nucleus / observe_freq_mhz; h1_freq_mhz is
   the 1H-equivalent); the field-unit estimate scales by the gamma
   ratio; the lock-shift arithmetic was already nucleus-exact. The
   lock channel (LOCNUC/BF2/SFO2) is recorded per session for the
   ratio analyses.

## 2c. v0.6.x: AUTOSTEP -- programmatic actuation (Tier 2, opt-in)

The v1 "no portable door" finding was too strong (JWB challenge,
2026-09-03, confirmed by a three-lens documentation/wild-code review):
`lopo <solvent>` is DOCUMENTED on TopSpin 2.x-4.x to push the edlock
table's per-solvent Distance -- which IS the BSMS LOCK SHIFT -- to the
BSMS without lock-in, and `lock <solvent>` autolocks the field to the
new setpoint; the table is the plain file conf/instr/<instrum>/2Hlock.
Below that, Bruker's shipped inc/bsms_program (`bsmscmd`,
GETBSMSVAL/PUTBSMSVAL) writes BSN_FIELD in production elsewhere and
reads BSN_LOCK_SHIFT in Bruker's own pulsecal.

AUTOSTEP (xpy spin_noise_run sweep autostep) implements the documented
chain: clone the session solvent's table row as ZZDUMMY (layout-blind),
detect the shift column ONCE per console via the edlock GUI as oracle
(operator moves ZZDUMMY's Shift by exactly +1.000 ppm; the changed
column is cached), then per step rewrite that one number + noqu lopo +
noqu lock + settle + lock off. Lock OFF automation = two 3-line AU
programs from the DOCUMENTED macros (LOCK_OFF/LOCK_ON/SWEEP_OFF),
installed and operator-verified once at setup; without them the
operator chooses semi-attended (one lock-off dialog per step) or
locked-and-recorded noise blocks. The pristine table is snapshotted
(memory + .spinnoise_backup) and byte-restored at the end. The
actuator's own sign is resolved on the first step by measurement
(landing at -target flips it once, deterministically). EVERY failure
falls back to the 2b operator dialogs for that step; verification 1Ds,
hop discipline, and measured-not-target are unchanged. Per-step
actuation_basis and lock state are recorded (schema, field_sweep
.autostep). First hardware use per console family:
docs/autostep_bench_checklist.md.

Analysis riders added with this change (analysis/facility_report.py):
the persistent-line catalog (spin / window-fixed / absolute-fixed
classification across carrier shifts -- the dark-photon line-search
and spur-catalog groundwork), the native-resolution sub-virial pass
(infrastructure only: no diurnal chirp templates yet, and it says so),
and the axion-mass / axial-vector bookkeeping (g_A = g_aNN * m_a * v).

## 3. What deliberately did NOT go in v1

- Automatic field actuation. Console-specific and, per the manuals,
  not exposed to TopSpin scripting at all (BSMS internals are CORBA-
  driven; the only documented AU-level door is the bsms_exam low-level
  example, whose source ships with TopSpin installs -- worth reading
  on a partner console). The verification-1D design makes the feature
  correct regardless of which knob the operator used; a scriptable
  path found later slots in as an xcmd_or_dialog upgrade without
  changing the data contract.
- Schema formalization. Both meta objects ride as optional extra keys
  (the validator allows additional properties); they get schema
  entries at the v0.5.0 release bump.
- Vendor ports. Both features are Bruker-first (ATM + BSMS); the
  concepts port later if partners ask.
- A pulse-free sensitivity calibration. The pulse-free route would
  modulate the transmission-line phase between probe and preamp (the
  Bendet-Taicher/Jerschow cable-length physics) with a servo-controlled
  phase shifter, reading the calibration out of the noise-line shape
  alone. Rejected for the network (JWB, 2026-08-31): added hardware,
  added cost, and an operational burden on non-expert facilities.
  Small-angle calibration pulses are the pragmatic channel; only the
  noise blocks themselves must stay pulse-free.

## 4. Safety posture

No new hardware command names: the features reuse `atma` and `zg`
through the existing safe_hw_cmd / xcmd_or_dialog guards, and
everything else is dialogs. static_check's guard assertions continue
to apply; the clock-audit begin/end pairing check covers the new
blocks. Nothing pulses beyond the existing small-flip machinery, and
the noise blocks remain pulse-free zgnoise2d.
