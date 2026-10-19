# Spin-Noise Network — Facility Protocol

*One 5 mm tube of water, one command, ~45 minutes of unattended magnet time.
This document is for the facility manager or staff scientist running the
measurement. Installation instructions are in `topspin/INSTALL.md`; upload
instructions are at the end of this page.*

---

## What this measures, in one paragraph

Even with no RF pulse applied, the nuclear spins in your sample exchange
energy with the probe's resonant circuit, and this leaves a measurable
fingerprint in the noise floor at the Larmor frequency. McCoy and Ernst first
recorded this "spin noise" signal and its back-action on the tuned circuit
(A. M. McCoy, R. R. Ernst, *Chem. Phys. Lett.* **159**, 587 (1989)); Guéron
and Leroy showed in the same year that on a conventional probe the dominant
effect is *absorptive*: the warm spins damp the circuit's Johnson noise,
producing a **dip** in the noise power spectrum at the water line (M. Guéron,
J. L. Leroy, *J. Magn. Reson.* **85**, 209 (1989)). Hoult and Ginsberg later
clarified the underlying reciprocity and radiation-damping physics (D. I.
Hoult, N. S. Ginsberg, *J. Magn. Reson.* **148**, 182 (2001)). The sign and
size of the feature depend on the temperature balance of the system: on a
**room-temperature probe** you should see the Guéron absorption dip, whose
fractional depth goes roughly as the circuit's coupling factor times the
ratio of radiation-damping to total linewidth (f_c·λ_r/λ_tot); on a
**cryoprobe**, where the coil and preamplifier are much colder than the
sample, the spins are *hotter* than the electronics and the feature flips
into an **emission bump**. By collecting the same water measurement across
many probes — RT, N₂-cooled, He-cooled, different vendors and field
strengths — the network maps this (T_sample, T_coil, T_preamp, η·Q) law
experimentally, on real hardware, at fleet scale. As a bonus, every run also
banks a long, well-characterized record of a receiver operating at or near
the spin-noise limit; these records are directly useful to fundamental-
physics searches (CASPEr-type axion dark-matter experiments) that need to
know exactly how quiet an NMR detection chain can be.

## Why water, and why we ask about the H₂O fraction

Water is the one sample every facility has, and its single strong ¹H line
sits exactly where the probe is optimized. The spin-noise feature scales
with proton density, so **the protonation level of the sample is not a
formality — it is the measurement**. We have a quantitative demonstration of
how much it matters: a 2022 archival measurement on a room-temperature
400 MHz instrument — a companion run to the 2020 dataset that seeded this
network, taken before this protocol existed — searched a nominally aqueous
sample for the Guéron dip and found none at all, with a 95% upper limit of
0.70% of the spin-coupled noise floor. The leading explanation was heavy
D₂O dilution of the sample — a factor of ~35 or more in proton density would
do it. The first network sessions complicate that reading: in September 2026
a room-temperature 400 MHz instrument at SIU Carbondale showed a clear
spin-noise feature (+27% of the floor) even on a 1% H₂O tube, so the 2022
null cannot be pinned on dilution alone. The probe's tuning state — which
sets the feature's sign and can null it at the bump-to-dip crossover — and
a BSMS field sweep left running during acquisition (documented for that 2022
archive, see below) are equally live explanations. The measurement was fine
— the unknowns were what was in the tube, how the probe was tuned, and
whether the field was sweeping, because nobody had recorded them. The
protocol therefore records all three. On the sample: tap water, distilled
water, and D₂O-doped water are all perfectly fine — **as long as you tell us
which one it is**. If you add a little D₂O for lock (10% is typical), that
is fine and expected — just answer the H₂O-fraction question honestly.

One shimming caution follows from the same physics: **do not shim against
radiation damping.** On a near-neat water sample the ¹H line carries, on
top of the width field inhomogeneity gives it, a broadening from the
probe's back-action on the spins, and gradient shimming cannot touch that
part. A controlled test on the SIU Carbondale 400 MHz instrument is the
demonstration: identical shims and identical acquisition, only the tube
swapped, gave a symmetric 8.5 Hz line on a dilute tube (1% H₂O in D₂O,
Gd-doped) and a 32 Hz asymmetric line on 90/10 H₂O/D₂O — the dilute tube
bounds what inhomogeneity contributes, and the excess on the 90/10 tube is
radiation damping — while three gradient-shim iterations against the 90/10
line moved the high-order shims by hundreds of DAC units and changed the
linewidth by under 0.2 Hz. Judge shim quality on the lock signal or on a
dilute tube, not on the near-neat water line. The dip depth goes as
f_c·λ_r/λ_tot and λ_r *is* the radiation-damping rate, so a strongly
broadened near-neat line is the regime that produces a large noise
feature — leave it as it is.

## The six operator questions (and why each exists)

When you start the run, the script asks about six things. None are
decorative; each one has burned somebody.

1. **Facility confirmation** — confirms your facility slug and instrument so
   the bundle is attributed correctly. Wrong attribution means your probe's
   point lands on someone else's curve. The hardware-check dialogs that
   follow also ask for the probe's coil and preamplifier temperatures, and
   they mean *physical* temperatures: on a room-temperature probe the coil
   sits at the probe-body temperature, which is the lab ambient, and the
   preamplifier at its own physical ambient — enter the measured lab
   temperature (a thermometer reading of 296.5 K beats an assumed 298 K)
   and leave the field blank only if you have no reading. A better number
   learned after the upload goes to the maintainer as a number; it is
   applied at analysis time, not by re-uploading.
2. **Sample composition** — water type, H₂O fraction (%), D₂O doping (%),
   additives, tube OD, volume. See above: proton density sets the signal.
3. **VT setpoint** — the sample temperature T_s is one of the four
   parameters in the law we are measuring. Please let the sample equilibrate
   at the setpoint before starting.
4. **Lock state** — locked or unlocked is both acceptable; we record which,
   because the lock channel can inject small artifacts near the water line.
5. **BSMS field sweep OFF — confirm it.** This is the big one. If the field
   sweep is left running (as it is by default on many consoles when
   unlocked), the field ramps during acquisition and smears the spin-noise
   feature over several kHz — the resulting spectrum looks like a perfectly
   healthy flat noise floor with *no feature at all*. The 2022 archival run
   described above was recorded with the sweep still on, ramping at
   ~54 Hz/s — extracting any dip sensitivity from it took a dedicated
   chirp-tracking analysis that follows the line as the field moves, and
   that rescue only worked because the sweep waveform could be
   reconstructed after the fact. Network bundles will not get that luxury.
   The script asks you to physically confirm the sweep is off and records
   your answer — it cannot verify this on all console generations, so the
   confirmation is on you.
6. **Run duration** — default 30 min of noise acquisition (~45 min total
   with setup and references); options for 60 min, 180 min, or overnight.
   Longer runs average down the spectrum and make better axion-band records.
   Overnight runs on an idle weekend magnet are gold.

Optionally, you may leave a **contact email** so we can ask follow-up
questions about your probe; a separate consent question controls whether it
is stored at all.

## What the run actually does

The script creates a dataset `SPINNOISE_<date>_<time>` containing: a setup
experiment (tune/match, shim, ¹H 90° calibration — automated where your
TopSpin supports it, dialog-driven where it does not); a four-step
receiver-gain ladder of tiny-flip 1D spectra (linearity check); a small-flip
pseudo-2D **reference** before the noise block; the **noise acquisition**
itself — a pseudo-2D using the `zgnoise2d` pulse program, which contains *no
pulse at all*, just receiver-open acquisition row after row at maximum
stable gain; and a closing reference identical to the opening one (drift
check). Apart from the ten short pulses of the calibration block below
(from v0.8.2 — at the console's own calibrated 90° power, the same pulses
`pulsecal` uses), nothing in the protocol pulses your sample at high
power, and nothing touches your probe beyond ordinary tune/match and
shimming. On the
vendor paths (the packer's Agilent quickstart, from v0.7.1) the gain ladder
is randomized and repeated — integer-dB levels in ~5 dB steps from 0 to the
sample's non-overflow maximum, each visited three times in one random
order — because a monotonic ladder cannot separate receiver compression
from signal drift: both SIU sessions showed a ~10% amplitude roll-off
toward the top rung that was degenerate with drift; the TopSpin script
follows from v0.8.2 with the dense ladder described below.

**Expected wall-clock time:** ~45 minutes for the default run, of which you
are needed for the first ~5 (the dialogs) and, from v0.8.2, the seven or
eight minutes of the dense gain ladder and the pulse-calibration block
that follow the four legacy rungs. Then walk away.

### A dense receiver-gain ladder (new in v0.8.2, unreleased)

The four legacy rungs stay exactly as they are (RG 1, 8, 64 and the
`rga` maximum at expnos 10, 14, 15, 16), but four rungs visited once
cannot separate receiver compression from signal drift — the report has
said so on every bundle (`rg_ladder.model = degenerate_single_visit`) —
and the step from the reference gain to the noise gain, which the
transduction constant assumes to be exactly the ratio of the two RG
numbers, was an extrapolation between rungs that merely bracket it. From
v0.8.2 the script therefore adds a dense ladder right after the legacy
rungs, at expnos 300–363: about seventeen levels, geometric from RG 1 to
the ladder maximum at two per octave (1, 1.4, 2, 2.8, 4, 5.7, 8, …), with
the reference gain (a quarter of the maximum) and the noise gain (the
maximum) always among them as levels of their own. Every level is
visited **twice**: once in a seeded random order and once in the reverse
of that order, so each gain is seen once early and once late in the
ladder — that is what lets a slow drift of the signal be told apart from
a gain-dependent compression. The seed is the session date, recorded, so
the order is reproducible. About 34 small-flip 1Ds, each the ~1° quick
acquisition the legacy rungs use: three to four minutes, attended. Your
console quantises a requested gain to its own table (about 1 dB steps on
Avance III and Neo, 3 dB on older consoles); the gain it took is what
`acqus` records and what the report uses. The argument `sparseladder`
skips the dense ladder and `meta.json` says so
(`calibration.rg_ladder_dense.skipped`, `why`); the rungs otherwise
appear in `calibration.rg_ladder` with their visit number, and
`rg_ladder_dense` records the levels, the seed and the visit count.

What the report derives from it: the compression of each gain level
with the drift fitted out (one amplitude, one drift rate and one
compression factor per level over both visits), the envelope of the
drift-corrected compression as today, and — new — the **bridge measured
at the two gains that matter**: the ratio of the fitted amplitudes at
the noise gain and the reference gain against the linear ratio of the
two RG numbers (`rg_ladder.bridge_measured`, `ratio_of_ratios` with its
error). From it the report computes the transduction constant the
headline would have with the measured bridge
(`transduction.rg_bridge_cross_check`, `applied_to_headline: false`);
the headline keeps the declared linear bridge, so every existing bundle's
report changes by added keys only. QA: WARN "RG ladder bridge" when the
ratio of ratios leaves 0.97–1.03, INFO — never FAIL — when the dense
ladder is absent or was skipped.

### Pulse calibration kept as data (new in v0.8.2, unreleased)

Every bundle so far records the 90° pulse only as a number
(`calibration.p90_us`, with the power it applies at), and every stored
pulsed experiment — the ladder rungs and the references — is acquired at
the ~1° small flip, which the script derives from that number by
attenuating the calibrated power by 39.08 dB. The tip angle that enters
the transduction constant, and so every exclusion, therefore rests on an
attenuation arithmetic that nobody can re-check from the raw files. From
v0.8.2 the calibration itself travels in the bundle: after the gain
ladders and before the row probe, while you are still at the console, the script
acquires ten quick 1D FIDs (`zg`, one scan, 20 s recycle delay) at the
**console's calibrated 90° power**, with the pulse length set to 0.25,
0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5 and 3 times the calibrated P90 — a
nutation curve, at expnos 80–95 (ten used, sixteen reserved). The 1 × P90
step is acquired first and `rga` is run once on it; that receiver gain is
kept for every step and recorded — the small-flip ladder's gain is never
reused for a full-power FID. Cost: about four minutes, attended.

The block runs **only at the calibrated power the script has read back
from the console** — the same parameter it attenuates for the small flip.
On a console where that power cannot be read (a legacy parameter set, a
blank calibration dialog) the block is skipped rather than run at a
guessed power, and `meta.json` says why (`calibration.nutation.skipped`,
`why`); the argument `nonutation` skips it deliberately. Nothing else
pulses at full power, and the noise blocks remain pulse-free. The block
has not yet run on hardware; its first live run is attended, as for
everything new.

What the report derives from it (`science.nutation`): the early-time
amplitude of each FID, a least-squares fit of
A(P1) = A₉₀ |sin(π P1 / 2 P90)| with P90 and A₉₀ free, and the fitted P90
against the declared one — the 180° null at 2 × P90 and the 270° peak at
3 × P90 locate P90 by symmetry, independently of any amplitude
calibration. A step whose samples reach the ADC's full scale is marked
clipped and left out of the fit. From A₉₀ the report predicts the
amplitude of a ~1° reference FID at the reference gain and compares it
with what the references measured — the small-flip cross-check, a direct
test of the 39 dB attenuation arithmetic and of the receiver between the
two gains. Both enter the report as cross-check keys and QA rows (WARN
when the fitted P90 differs from the declared one by more than 5%, or
when the cross-check ratio leaves 0.9–1.1; INFO, never FAIL, when the
block is absent or skipped); the exclusion headline keeps the declared
tip, so reports of earlier bundles do not change. Bundles from the
Agilent path do not carry the block until that driver gains it.

### Optional modes (off by default)

Two optional flags extend the standard run — both composable, both
documented in `docs/design_rdopt_locksweep.md`:

- `xpy spin_noise_run rdopt` — **rd-optimize**: after setup, the script
  scans a ladder of probe-tuning offsets (via ATM: `atma` at a shifted
  carrier), measures the small-flip FID decay rate at each (the decay is
  1/T2* + the radiation-damping rate), and runs the whole session at the
  offset with the strongest radiation damping. Needs an ATM probe;
  without one it skips itself with a note. Why a tuning away from the
  conventional optimum helps, and what it costs: *Tuning: spin noise
  versus pulses*, below.
- `xpy spin_noise_run sweep` — **field-stepped sweep** (v0.6:
  carrier-follow "spectral tiling"): instead of one long noise block, a
  ladder of noise blocks at stepped fields — up to 15 steps over up to
  ±25 ppm of B₀. Every step is a distinct axion mass point, so one
  session locally scans several adjacent masses, and the receiver
  carrier **follows each step automatically**, so the line stays
  centered in the window however far the ladder reaches. The field is
  stepped through the **lock reference**: with a lockable sample (e.g.
  90/10 H₂O/D₂O), set the Lock Shift by the quoted ppm (1 ppm = BF1 Hz
  at the observed nucleus, exact), re-lock — the servo carries the
  field to the new setpoint — then lock OFF for the acquisition as
  always. Jumps larger than one safe re-lock are walked in hops (the
  dialog quotes them; lock-shift path only). (Fallback for consoles
  without accessible shift mode: step the FIELD value unlocked —
  ~8 Hz/unit at ¹H standard bore, scaled for other nuclei — and don't
  re-lock mid-sweep — at a fixed reference autolock would pull the
  field straight back.) The estimator's sign convention is resolved at
  the baseline by an automatic carrier-displacement 1D (one extra
  ~30 s acquisition, no operator action). The script verifies each
  step by measuring the sample's line — the *measured* position is
  what enters the record — and verifies the restoration too. The BSMS
  field sweep stays off throughout. The dialogs that ask you to work
  the BSMS window (baseline, each step, lock off, restore) open as
  separate windows that do **not** block TopSpin where the console can
  show one (v0.8.1 — TopSpin's own dialogs do, and the first live sweep
  stalled on that): set the shift, re-lock, lock off, then press the
  button. An off-target reading waits 10 s and is measured once more
  before the step is judged or a dialog opens, because the field is
  still settling after a re-lock.

## Tuning: spin noise versus pulses

A probe has two tuning optima, and the one every operator knows is not
the one spin noise wants.

**The conventional optimum** — `atma`, `wobb`, the minimum of the
reflected-power curve at the Larmor frequency — matches the probe to
50 Ω at the line. It is the *transmit* optimum: the shortest 90° pulse
per watt, and the state the probe's power ratings assume.

**The receive optimum** is where the preamplifier sees the source
impedance at which its own noise contribution is smallest. Noise
matching and power matching are different conditions, and the cable
between probe and preamplifier transforms the impedance with frequency,
so the receive optimum sits some hundreds of kHz from the conventional
tune — above or below, depending on the probe and its cabling (on
cryoprobes the literature finds it 150–500 kHz below nominal). The
spin-noise literature calls it the spin-noise tuning optimum: there the
thermal spin-noise line is a pure absorptive dip; detuned from it the
line acquires a dispersive component and, far enough away, turns into a
bump. That progression is what the report's `tuning_ladder` section
reads, and the dispersive fraction of the headline line (`coadd_fit
.disp_norm`) is the number to watch. (Marion & Desvaux, J. Magn. Reson.
193, 153 (2008); Nausner et al., J. Magn. Reson. 198, 73 (2009);
Pöschko et al., ChemPhysChem 15, 3639 (2014).)

**What the network has measured so far — provisional.** Oulu, Avance III
HD 500, room-temperature BBO probe, 90/10 H₂O/D₂O, 16 October 2026:
`xpy spin_noise_run rdopt` scanned seven tunings from −500 to +250 kHz
and kept +250 kHz; the session then ran there.

| | conventional tune (three sessions, 5–6 Oct) | +250 kHz (16 Oct) |
|---|---|---|
| spin-noise dip, fraction of the floor | 0.287–0.295 | 0.355 |
| dispersive fraction of the line | 0.24 | 0.08 |
| 90° pulse | 8.44 µs | 9.66 µs |
| reference FID decay rate | 94.1–94.6 /s | 94.8 /s |
| noise floor at the noise gain (counts²/Hz) | 10.65 | 11.02 |

A deeper, more symmetric dip is the receive optimum showing itself; the
longer pulse is the transmit efficiency paid for it. Two caveats, which
is why this table will be replaced: the scan that ran (v0.8.0 defaults)
stopped at +250 kHz, the very setting it chose, so the optimum was not
bracketed — v0.8.1 scans to +600 kHz; and the scan's own figure of
merit, the decay rate of a small-flip FID, ranked +250 kHz 11% above the
conventional tune while the session's references show no such
difference, so the dispersive fraction of the spin-noise line is the
better criterion and the one a future version will rank by. The example
will be updated once a scan brackets the optimum.

**Advice by application.**

- *Spin-noise sessions, and any receive-limited acquisition* (weak
  signals, dilute samples, long signal averaging): tune to the receive
  optimum. The same noise-matching condition gives the best
  signal-to-noise for ordinary pulsed spectra — the literature above
  reports ten to twenty percent — at the price of a longer pulse. In the
  network, start sessions with `xpy spin_noise_run rdopt`; the report's
  `rd_optimize` block records what was scanned and chosen.
- *Transmit-limited work* (hard broadband pulses, decoupling, anything
  near the probe's power limit): keep the conventional tune. The pulses
  are shorter and the probe stays inside the regime its ratings were
  measured in.
- *Hygiene.* The script leaves the probe at the tuning it chose. A
  probe parked a few hundred kHz off is harmless for the next user,
  whose `atma` brings it back, but run `atma` yourself before handing
  the instrument over — until the script does it at the end of a
  session (queued).
- *Seeing it on your own instrument without the script:* with the lock
  off and no pulses, record a minute of noise around the water line at
  the conventional tune and at a few steps of detuning either side; the
  spin-noise dip is deepest and most symmetric at the receive optimum.
  The `tuning_ladder` role in the bundle format is exactly this
  measurement, and the report compares the settings.

## What gets uploaded

At the end, the script zips the complete Bruker experiment directories
(acqus, ser/fid, pulse program, `uxnmr.info`, audit trail) together with a
`meta.json` describing the instrument (TopSpin version, field, console,
probe string and type, coil/preamp temperatures if reported, and from
v0.8 the B0 orientation, `vertical` for every magnet this script runs
on), the sample,
your dialog answers, and SHA-256 checksums of every data file. The
`meta.json` follows schema version 1.2 (the last Bruker-only vintage —
still fully valid under the repository's current vendor-neutral schema
v2.0, where an absent vendor field means Bruker), which also records a
`software` block — script version and a runtime SHA-256 of the script
file itself — and the clock-audit timestamps described below, so every
bundle is traceable to the exact code that produced it. Then
`uploader/upload_bundle.py` sends the zip to the network's repository.
Data acquired with the vendor's own tooling (or on non-Bruker
instruments, as those reader paths mature) can instead be packed with
the standalone `packer/pack_bundle.py`, which writes schema-2.0 bundles
from a data directory plus an `answers.json` questionnaire carrying the
same answers as the dialogs.

v0.8 adds optional fields, none required and none asked of you at the
console: `facility.latitude_deg`, `facility.longitude_east_deg` (east
positive) and `facility.coordinates_basis` (`operator` or `registry_city`);
`spectrometer.b0_orientation` (`vertical`, `horizontal` or `unknown`) with
`spectrometer.b0_azimuth_deg` for a horizontal field;
`local_timezone_offset_basis` (`answers` or `packing_machine`, written by
the packer beside the offset); and `clock_audit.blocks[].row_started_offsets_ms`,
the start of each row within its block — requested, but not recorded by
any writer yet, so the analysis spreads the rows evenly over the block's
wall time until one does. Why the analysis wants them is the section
after the clock audit below.

**Privacy:** the bundle contains instrument and sample metadata only. The
sole item of personal information is the optional contact email, stored only
if you answered yes to the consent question. No usernames, no other
datasets from your spectrometer, nothing outside the `SPINNOISE_*` dataset
is read or uploaded. You keep the local copy; if the upload fails for any
reason the zip stays on disk with printed instructions for manual transfer.

## Frequency accuracy and the clock audit

A dark-matter line sits at an absolute frequency set by physics, not by your
console, so the network needs to know how far each console's master clock is
from true — a question chemistry never asks, because chemical shifts are
internally referenced ratios. Your console's master clock is a quartz
oscillator (OCXO) whose absolute accuracy is typically somewhere between one
part in 10⁸ and one part in 10⁷. That is entirely fine for this protocol —
nothing needs adjusting — but the network analysis wants the actual offset
measured, and the run measures it for free. Acquisition durations are timed
by the console's clock, while your workstation's wall clock is normally
disciplined by internet time servers (NTP). The script records wall-clock
timestamps immediately before and after each acquisition block, and comparing
the wall-clock elapsed time against the console-implied block duration gives
the fractional offset of your console clock. No hardware is attached and
nothing on your spectrometer is touched or reconfigured — the audit is
bookkeeping on timestamps the run produces anyway.

Two things help. First, please make sure NTP time synchronization is enabled
on the TopSpin workstation — it usually is by default, and if your
workstation is air-gapped, just mention that when you upload. Second, longer
runs sharpen the number: a default ~45-minute run pins the offset to a few
parts per million, an overnight run to a few parts in 10⁷, and repeated runs
over a week to about one part in 10⁸.

The number in your facility report labeled **console clock offset** is that
measured fractional offset. An offset of, say, +2×10⁻⁷ means every frequency
your console reports is high by 0.2 ppm — 120 Hz at 600 MHz — and the network
analysis corrects for it. It is not a fault, it requires no service call, and
no action is needed on your side.

## Where the magnet is, and when the rows ran

The dark-matter wind has a known direction. In the standard halo model the
laboratory moves through the halo at the Sun's 232.6 km/s plus the Earth's
29.79 km/s orbital velocity, toward the apex: RA 313°, Dec +48° for the
Sun's motion alone, and once the Earth's orbit is added a point that loops
through the year between Dec +41° and +54° (about RA 319°, Dec +54° in early
October; RA 321°, Dec +53° in mid-September); the report records the
session-mean value in `halo.wind_model.apex_ra_deg` / `apex_dec_deg`. Your magnet's B₀ is vertical, so the angle between the wind and B₀
at any moment follows from the site's latitude and longitude and the UTC
time — and the drive on the spins goes as the wind component perpendicular
to B₀. From v0.8 the facility report uses this instead of assuming a
direction: the headline exclusion number is computed with the wind at its
actual direction for your site and your run's hours, and the old "wind
parallel to B₀" number is kept as a robustness line. Two consequences:

- **Nothing more to enter.** The report takes the site from the registry's
  city gazetteer (0.1° is enough) and the row times from the clock-audit
  timestamps the run records anyway. If you know the magnet's coordinates,
  send them to the maintainer with the bundle — they go in as
  `facility.latitude_deg` / `facility.longitude_east_deg` at analysis time.
  From v0.8 the TopSpin script records `spectrometer.b0_orientation: vertical`;
  bundles from the standalone packer (any vendor) and bundles acquired before
  v0.8 leave the field absent, and the analysis reads absent as `vertical` for
  Bruker, Agilent and JEOL bundles (`b0_orientation_basis: default_nmr`) and as
  `unknown` for Magritek and Nanalysis. A horizontal-bore magnet would need
  `horizontal` plus its compass azimuth.
- **The hours matter.** The wind angle repeats with the sidereal day, so a
  session's sensitivity depends on when it ran. Over a sidereal day the
  angle runs 11–61° at Oulu, 16–88° at Carbondale, 7–80° at Lausanne; the
  headline bound is best at the hours of largest angle, which for a
  vertical magnet are around the apex's lower culmination (at Oulu about
  06:40 UTC, 09:40 local, in the first week of October — 09:55 on 1 October,
  09:36 on 5 October — four minutes earlier each day). A run
  that spans the change — 12 h from peak to trough, or 3 h placed across
  an extremum — also feeds the report's sidereal-modulation fit: an axion
  signal would rise and fall with the wind angle while the spin-noise line
  and the receiver floor stay put, so the fit looks for that rise and fall
  in the per-row line power and sets a bound on it. That bound gains with
  hours, so long runs stay the most valuable. The fit's section in the
  report is headed "preliminary, internal; not a detection claim", and no
  session so far shows a modulation.

Every exclusion number in the report is UNPUBLISHED and preliminary — an
internal sensitivity bookkeeping, far above the astrophysical bounds, not a
publication-grade limit.

## Quickstart

```
1. Fill a standard 5 mm tube with water (tap, distilled, or D2O-doped —
   any is fine, you will record which). ~550 uL.
2. Insert, set your usual VT setpoint, let it equilibrate.
3. In TopSpin, type:    xpy spin_noise_run
4. Answer the six dialogs. CONFIRM THE FIELD SWEEP IS OFF.
5. Walk away (~45 min). When it finishes, upload:
       python3 upload_bundle.py spinnoise_<yourfacility>_<timestamp>_<hex>.zip
   (First time: copy uploader/config.example.json to config.json and fill in
   the endpoint and token from the maintainer.)
```

Questions, tokens, and slugs: contact the network maintainer (address in
your `config.json`). Thank you — every probe added is a new point on the
curve.
