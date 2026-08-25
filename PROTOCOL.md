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
0.70% of the spin-coupled noise floor. Neat water on that probe would have
shown an unmistakable dip, so the null itself carries the lesson: the sample
must have been heavily D₂O-diluted, by a factor of at least ~35 in proton
density. The measurement was fine — the unknown was what was in the tube,
because nobody had recorded it. Hence the sample dialog: tap water,
distilled water, and D₂O-doped water are all perfectly fine samples — **as
long as you tell us which one it is**. If you add a little D₂O for lock
(10% is typical), that is fine and expected — just answer the H₂O-fraction
question honestly.

## The six operator questions (and why each exists)

When you start the run, the script asks about six things. None are
decorative; each one has burned somebody.

1. **Facility confirmation** — confirms your facility slug and instrument so
   the bundle is attributed correctly. Wrong attribution means your probe's
   point lands on someone else's curve.
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

The script creates a dataset `SPINNOISE_<date>` containing: a setup
experiment (tune/match, shim, ¹H 90° calibration — automated where your
TopSpin supports it, dialog-driven where it does not); a four-step
receiver-gain ladder of tiny-flip 1D spectra (linearity check); a small-flip
pseudo-2D **reference** before the noise block; the **noise acquisition**
itself — a pseudo-2D using the `zgnoise2d` pulse program, which contains *no
pulse at all*, just receiver-open acquisition row after row at maximum
stable gain; and a closing reference identical to the opening one (drift
check). Nothing in the protocol pulses your sample at high power, and
nothing touches your probe beyond ordinary tune/match and shimming.

**Expected wall-clock time:** ~45 minutes for the default run, of which you
are needed for the first ~5 (the dialogs). Then walk away.

## What gets uploaded

At the end, the script zips the complete Bruker experiment directories
(acqus, ser/fid, pulse program, `uxnmr.info`, audit trail) together with a
`meta.json` describing the instrument (TopSpin version, field, console,
probe string and type, coil/preamp temperatures if reported), the sample,
your dialog answers, and SHA-256 checksums of every data file. The
`meta.json` follows schema version 1.1, which also records a `software`
block — script version and a runtime SHA-256 of the script file itself —
so every bundle is traceable to the exact code that produced it. Then
`uploader/upload_bundle.py` sends the zip to the network's repository.

**Privacy:** the bundle contains instrument and sample metadata only. The
sole item of personal information is the optional contact email, stored only
if you answered yes to the consent question. No usernames, no other
datasets from your spectrometer, nothing outside the `SPINNOISE_*` dataset
is read or uploaded. You keep the local copy; if the upload fails for any
reason the zip stays on disk with printed instructions for manual transfer.

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
