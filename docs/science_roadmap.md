# Science roadmap: physics beyond the core axion search

Status: planning document, no code committed against it yet.
Produced 2026-09-01 by a systematic discovery pass: five independent
generation lenses proposed 28 candidate physics targets for the
network, and every candidate was then adversarially re-derived from
the hardware facts and graded against the strongest published bound
the reviewer could identify. Numbers below are order-of-magnitude
estimates for PRIORITIZATION -- re-derive anything here before it
goes into a proposal or a paper. Rejected entries are kept on record,
with their kill arguments, precisely so they are not re-proposed.

Cost tiers used throughout:

- Tier A: pure re-analysis of data the network already records, or
  trivial software additions.
- Tier B: software/protocol changes only (different nucleus, sample,
  tuning, schedule) -- still runnable overnight by facility staff.
- Tier C: cheap per-site hardware or sample upgrades (GPSDO, special
  crystal, source mass).

## 1. Adopt into the core program

### 1.1 Lock-step spectral tiling (Tier A) -- FLAGSHIP

Instead of sitting at one fixed mass point per magnet, step the lock
offset through ~27 positions per night and stitch the records in
absolute-frequency space. The 2H lock shift of +/- a few kHz
multiplies by gamma_H/gamma_D = 6.51 at the 1H channel, so the
tiling reach is +/-13-26 kHz per site -- contiguous swaths instead
of isolated 300 Hz slivers. Scan figure-of-merit is exactly neutral
(27 bins costs 27^(1/4) = 2.3x per-bin depth for 27x coverage,
recovered wherever bins are revisited), and the on/off-resonance
step pattern is a built-in veto no fixed-frequency haloscope has:
spin lines move with the step, instrumental lines do not, and a real
DM line stays fixed in absolute frequency while its amplitude
follows the resonant envelope.

Verified points: OCXO accuracy (1e-7..1e-8 = 6-60 Hz at 600 MHz) is
already adequate against the 300 Hz virial width; lock settle and T1
re-equilibration are seconds against ~20 min segments; drifts
(chemical shift, susceptibility, temperature) are common-mode across
steps.

Deltas from the shipped v0.5.x sweep feature: update the synthesized
receiver carrier (SFO1) per step so the 7 kHz window follows the
line, log the lock offset per segment in the bundle, add the
analysis-side stitching. Candidate for v0.6.

### 1.2 Cross-site mass-point overlap and veto protocol (Tier A)

Coincidence bookkeeping across sites suppresses accidental line
candidates by 1e2-1e4 (trials matching within +/- one virial width
at sites whose spur combs map to different axion masses). Two
corrections from review: the OCXO alone already supports +/- one-
linewidth cross-site mass matching (GPSDO is optional hardening, not
a prerequisite); and only site pairs at the bottom of the same-
nominal-field spread distribution (spread 60-600 kHz at 600 MHz vs
stepping reach +/-13-26 kHz) can be brought onto the same mass
point -- a minority. Deltas: per-segment lock-offset metadata, a
cross-site persistent-line catalog, absolute-frequency bookkeeping.

## 2. Free analysis riders (Tier A -- same recorded bits)

### 2.1 Axial-vector dark matter reinterpretation -- WORLD-LEADING

A light vector with a direct axial spin coupling (dark Z') drives
the identical candidate lines as the axion wind but WITHOUT the
v ~ 1e-3 velocity suppression: equal pseudo-field maps
g_A = g_aNN * m_a * v, i.e. wherever the core search reaches
g_aNN ~ 1e-8 GeV^-1 the same data give g_A ~ 2.5e-26 at 2.5 ueV.
No direct laboratory limit exists anywhere in the ueV decade, and
this reach sits 4-8 orders below even the indirect (UV-dependent)
meson-decay bounds -- the strongest constraint of any kind in most
of the band. Cost: one coupling-conversion factor and a slightly
different daily-modulation template in the analysis code. Framing
caution: consistent UV completions of light non-conserved axial
vectors are contrived; publish as a limit, never as a discovery
favorite.

### 2.2 In-bore dark-photon line search

Dark-photon dark matter drives an effective current in the detection
volume; the below-cutoff bore blocks external RFI but not this
in-volume source. Honest per-site reach after the sub-wavelength
pickup suppression ((R/lambdabar)^2 ~ 0.016 at 600 MHz -- a ~300x
power correction the first-pass estimate missed): eps ~ 1.4e-11 per
cryoprobe night, ~3e-12 per cryo site-year. Wins 3-30x over the
best existing bounds (WISPDMX ~1e-11 off-resonance, cosmology
~1e-10) only in the genuinely unscanned windows: ~400-460 MHz (the
9.4 T sites) plus gaps between historical cavity scans -- original
ADMX already covered 460-890 MHz at eps ~ few e-15 and CAPP-12TB
covered 1.025-1.185 GHz. Cautions: console synthesizer spurs are
ALSO immobile under lock stepping (the lock moves B0, not the
synthesizer), so the lock-step veto separates spin lines only;
spur-vs-DP discrimination rests on the spur catalog keyed to the
clock audit, blank-tube nights, and (optionally) a GPSDO tightening
the absolute-frequency test from ~60 Hz to <1 Hz.

### 2.3 High-resolution sub-virial channel

A second FFT pass at ~0.1 Hz effective resolution buys 2.5-7x in
coupling on cold-flow/caustic substructure (ADMX runs the same
HR/MR trick for g_agamma; nobody does it for the gradient coupling
at these mass points). Within-night OCXO stability (~1e-10) floors
the usable linewidth at ~0.06 Hz. NOT optional: Earth's rotation
against a ~300 km/s flow chirps the line by ~0.9 Hz over a night --
larger than the line width -- so diurnal (not just annual) chirp
templates are mandatory, plus the lock-step amplitude veto against
the sub-Hz instrumental spur forest.

### 2.4 Calibrated in-field excess-power archive (public data product)

A documented, versioned release of calibrated PSDs + covariance +
spur list + measured coil/shield transfer function (including the
sub-Compton shielding suppression) + B0/orientation/coordinates per
site. On every model anyone has written down we lose 1-3 orders to
dedicated experiments, but the archive makes the dark-photon,
axion-photon, and high-frequency-gravitational-wave reinterpretation
tables free forever, and slivers of it are the only limit of any
kind at those exact frequencies. The HFGW table is honest decoration
(h ~ 6e-18 vs cavity reinterpretations at 1e-21..1e-22 and any
plausible source below 1e-27) but costs zero nights.

## 3. New observables worth a protocol change (Tier B)

### 3.1 Sidereal Lorentz violation, B-scaled channel -- WORLD-LEADING

Observable: sidereal modulation of the clock-blind 1H/2H frequency
ratio (both synthesized from the same OCXO; B0 and oscillator cancel
exactly). The B-SCALED channel -- an anisotropy of the bound
proton/deuteron magnetic moment, delta-g proportional to B --
is genuinely virgin territory: even after a 3-10x systematics
haircut (spin-noise line pull needs a demonstrated 1e2-1e3
sidereal-coherent suppression; floor 3e-12..1e-11 fractional, not
the statistical 2e-13), this beats the BASE proton sidereal
sensitivity (~1e-9 fractional at 1.9 T) by 1e2-1e3, with another
x7-15 in dimension-5 coefficient space from running at 14-28 T.
The lock servo is the mechanism, not a bug: it transduces any
deuteron-sector shift into B0 and hence into the 1H channel.

Conceded honestly (and recorded in 5.3): the B-INDEPENDENT b-tilde
coefficients of the same observable lose by 1e4-3e7 to Hg/Cs and
3He/129Xe comagnetometers -- that channel is dead; only the
B-scaled one is claimed.

Deltas: standardized 90/10 H2O/D2O network sample, per-block lock
error-signal and lock-reference logging, phased-lineshape co-fit,
the detuning-ladder validation already planned for the core
program, and an SME theory workup for the deuteron.

### 3.2 Isotope-ladder X-nuclei campaign

2H and 13C nights give the first LABORATORY limits on the
axion-NEUTRON coupling at these mass points (31P is an odd-PROTON
nucleus, like 19F with a strongly quenched moment -- it adds a
proton-coupling mass point on the X channel, not a neutron probe;
corrected 2026-09-03 after a JWB question); 19F (0.941x the 1H
frequency, riding the 1H/19F coil at near-cryo sensitivity) gives
essentially free proton mass points densifying the scan. Honest
numbers after correcting a wrong gamma^1.5 scaling (the X channel
is circuit-noise-limited, so g_min ~ 1/(n*gamma^2.5-3*sigma)):
g_ann ~ 2e-5..2e-4 per season per mass point via 2H -- lab-frontier
exclusion, far above SN1987A, and honest about it. Protocol notes
that survived review: 2H observation REQUIRES lock-off nights (the
lock transmitter irradiates the deuterium resonance; persistent
drift 0.1 Hz/hr is negligible against the 46 Hz virial width);
13C-methanol needs overnight WALTZ decoupling or a proton-free
sample (13CS2) against the 140 Hz J-quartet. Samples ~$100-500
(neat D2O, 13C-methanol or 13CS2, C6F6, 85% H3PO4), all standard
5 mm tubes.

DECISION (JWB, 2026-09-01): the 19F rider is ADOPTED into the
near-term program, split off from the rest of the ladder. It needs
no X-channel calibration work (19F rides the 1H/19F coil at 0.941x
the 1H frequency, at or near cryo sensitivity), a C6F6 tube is
cheap and inert, and every F-capable site that runs it densifies
the proton mass scan for free. The 2H/13C neutron-coupling legs
(and the 31P proton-channel leg) stay Tier B pending volunteer
sites.

### 3.3 Dual-species transient network (GNOME complement)

Interleaved ~1 deg FIDs at 0.5-1 Hz on a ~1% H2O / 99% D2O sample
track the 1H line at ~1 mHz/s (23 pT proton-equivalent) -- the same
decade as GNOME's effective proton-coupling floor (alkali
magnetometers pay ~mu_N/mu_B against nuclear couplings), with exact
gamma_d/gamma_p dual-species tagging and B0/OCXO immunity in the
ratio. The systematic that sets the dilution: at 10% H2O, radiation
damping pulls the line at 1e3x the target; at 1% it is
controllable, pending demonstration. Tier A version (spin-noise
line tracking, 7-50 nT floor) is only a veto for events 500-1000x
above GNOME threshold. Deltas: >=10 Hz lock-error/correction
logging, the interleaved-FID pulse program, lock transfer-function
calibration via the existing lock-shift stepping, GNOME-style
coincidence pipeline with NTP timestamps.

### 3.4 Foundational pair

(a) First quantitative fluctuation-dissipation test on a nuclear-
spin ensemble at GHz: calibrated spin-noise PSD (fluctuation side)
vs small-flip chi'' (dissipation side) at the ~1% absolute-
calibration floor, including the two-temperature cryoprobe case
(20 K coil vs 300 K spins). Verified as unoccupied territory
(Johnson-noise thermometry stops <1 MHz; spin-noise literature is
~10% and qualitative). The quantum/KMS-asymmetry stretch goal is
DEAD: a phase-insensitive amplifier measures the symmetrized
spectrum, whose leading quantum correction is (hf/2kT)^2/3 ~ 2e-7.

(b) Absolute radiation-damping/collective-decay metrology: the
omega*Q*eta*M0 rate formula tested at few-percent absolute across
2x in frequency and 100x in M0 (H2O/D2O ladder) on many platforms,
~10x beyond the single-instrument ~10% literature. eta*Q comes from
reciprocity via the nutation-calibrated B1-per-sqrt(W) already in
the chain. Frame as coupled-mode/collective-decay physics, not
"Purcell" -- referees in magnetic resonance will (correctly) object.

## 4. Gated or deferred

### 4.1 Axion-photon piggyback -- gated on one number

The 9.4-28 T B0 through the detection volume makes every probe a
(terrible) haloscope; the coil form factor C for the axisymmetric
conversion field is the unknown, because an NMR coil is engineered
to have zero leading-order overlap with it (coupling survives only
through lead routing, tilts, and dielectric asymmetries;
C ~ 1e-2..1e-5). The gate: ~1 week of full-wave EM simulation of
the 2-3 dominant commercial probe geometries. If C >= 1e-2:
g ~ 2e-11 GeV^-1 per cryo site-year, beating CAST/globular clusters
(6.6e-11 / 4.7e-11) by ~3x in the 1.65-2.7 ueV window no cavity
experiment has ever scanned. If C ~ 1e-3: a tie -- publish the
simulation and stop. Claim nothing until C is in hand. Analysis
itself is free (same pipeline as 2.2).

### 4.2 Idle-MRI fleet nodes (64-298 MHz, 0.26-0.53 ueV per 1H)

The 10 L volume win survives honest accounting of coil dilution and
room temperature: ~5x one cryoprobe-night per scanner-night in
amplitude SNR, and a fleet-year reaches few e-8..1e-7 GeV^-1 -- the
closest approach to SN1987A (~1e-9) anywhere in the program, at
mass points nothing else covers. Real costs: raw-FID console access
agreements, a ~10 L low-loss phantom, per-scanner Q/T_sys/nutation
characterization. Pilot on 2-3 research scanners before any fleet
claim.

### 4.3 CASPEr-electric-lite (Tier C)

A poled ferroelectric 207Pb stick (~$100-500) in a standard tube
searches the oscillating-EDM coupling at the site's 207Pb frequency.
Arithmetic survives review, but 207Pb in disordered relaxors has
100+ kHz CSA/disorder spans (T2* ~ 3-30 us, not 0.3 ms), costing
3-10x beyond the honest 1H anchor band. Pilot-grade; try on a
non-cryo probe first (dielectric-loading risk).

### 4.4 Broadband ATMA-stepped X-channel scan -- mostly killed

The (R/lambdabar)^2 sub-wavelength suppression is fatal at the low
end: at 150 MHz we lose 100-1000x to Dark E-field Radio. The
surviving corner: 300-460 MHz on the >=23.5 T subset at
eps ~ 1e-11, against WISPDMX spot resonances and cosmology only.
Storage logistics (~0.5 TB/night/site wideband IQ) dominate the
real cost. Deferred; the 19F rider was split into 3.2.

### 4.5 GPSDO clock network (hardware yes, defect search no)

The GPS.DM-style topological-defect search is scientifically
near-empty: at transient timescales a GPSDO output IS its own
internal quartz (discipline loop ~mHz), so the logged observable is
quartz-vs-quartz co-located within meters, with ~100x common-mode
suppression of any constants-transient on top of a realistic
1e-11..1e-12 ADEV floor. BUT the ~$300-1000 GPSDO itself is
justified by 2.2 (spur discrimination), 2.3 (cross-night
stitching), and clock-audit hardening. Buy the hardware for those
reasons; skip the defect-search science layer.

### 4.6 Nonequilibrium FDT on the driven lock spins

A Harada-Sasa-style violation measurement on the continuously
driven 2H lock ensemble is real physics, but the 2H spin-noise
feature is ~1e-2 of the 1H bump and per-night line-power precision
is ~50% at room temperature (~5% cryo) -- 1-2 orders worse than
proposed. Needs raw lock-receiver access or an X-channel
observe/lock-drive protocol. Park it.

## 5. Considered and killed -- do not re-propose

Each entry was independently re-derived before rejection; the kill
arguments are the record.

- 5.1 ULF dark photon via lock/persistent-current channel: a
  sub-wavelength pickup pays (L/lambdabar) in field; at mHz that is
  6e-12, and SuperMAG's R_Earth-scale loop wins by >13 orders.
  General rule worth keeping: sub-wavelength EM pickups pay
  (L/lambdabar) -- an O(0.1) penalty at 600 MHz, a death sentence
  at mHz. Low-frequency ambitions belong to spin-coupled channels.
- 5.2 Monopole-dipole force with an in-bore source mass: 0.5 kg at
  10 cm gives g_s*g_p reach ~1e-22 vs 3He/129Xe bounds at
  1e-29..1e-30. Lose by 7-8 orders; geometry cannot buy it back.
- 5.3 B-independent sidereal b-tilde on the 1H/2H ratio: systematic
  floor 2.5e-26 GeV vs comagnetometer bounds at 1e-30..8.4e-34;
  the deuteron-combination escape fails (mu_d ~ mu_p + mu_n,
  triangle inequality). The B-scaled channel (3.1) is the only
  live one.
- 5.4 Hz-kHz stochastic ALP sidebands / cross-site
  cross-correlation: best case g_p ~ 1e-4 GeV^-1 vs NASDUCK/
  comagnetometers at 1e-6..1e-10. Also the canonical answer to
  "why not cross-correlate distant sites?": the DM field coherence
  length at ueV masses is ~500 m -- same-hall pairs are coherent,
  the network is not.
- 5.5 Astrophysical burst follow-up (SN axions, FRBs, solar): MeV
  SN axions have in-band spectral weight suppressed by >=1e-27;
  in-band FRB EM is attenuated e^-35 by the below-cutoff bore and
  loses to thermal noise even unshielded. One paragraph in the
  methods paper pre-empts future proposals.
- 5.6 Pauli-exclusion violation in NMR: wrong-symmetry admixtures
  in liquids produce no new frequencies, only an intensity shift
  degenerate with concentration; best conceivable bound ~1e-2 vs
  molecular-spectroscopy 1e-11 and Borexino ~1e-50s.
- 5.7 Millicharged-particle cyclotron lines: the in-band-cyclotron
  and terrestrial-capture conditions are mutually exclusive by
  >an order of magnitude in epsilon at every mass.
- 5.8 Oscillating quark masses (dilaton DM) in the 1H/2H ratio:
  best-case d_mq ~ 1.6 at the single most favorable mass;
  MICROSCOPE beats it by >=3.5 orders there and 5-9 elsewhere.
  The block-wise ratio time series is still worth extracting as a
  free drift diagnostic (shared with the core analysis).
- 5.9 High-frequency gravitational waves as a physics driver:
  h ~ 6e-18 vs cavity reinterpretations at 1e-21..1e-22 and
  plausible sources below 1e-27. Survives only as a free
  reinterpretation table in the 2.4 archive.

## 6. Near-term actions

Software (v0.6 candidates, in rough order of leverage):

1. Lock-step tiling mode: per-step SFO1 carrier follow, per-segment
   lock-offset metadata in the bundle schema, stitching support in
   the analysis (1.1).
2. Per-block 2H lock error-signal and lock-reference logging --
   wanted independently by 1.2, 3.1, 3.3, and the core clock audit.
3. Analysis riders: dark-photon line search + axial-vector limit
   curve + sub-virial second pass with diurnal chirp templates +
   spur catalog keyed to the clock audit (2.1-2.3).
4. Excess-power archive format specification (2.4).

Non-software:

5. 19F pilot (ADOPTED 2026-09-01): survey F-capable probes in the
   site registry, ship C6F6 tubes, run pilot noise nights at 2-3
   sites, then fold 19F into the standard rotation (3.2).
6. The one-week probe form-factor EM simulation that gates 4.1.
7. Standardized network samples: 90/10 H2O/D2O (3.1) and
   1% H2O/99% D2O (3.3); remaining isotope-ladder kit (3.2) for
   volunteer sites.
8. Optional per-site GPSDO (~$300-1000) -- justified by 2.2/2.3,
   not by the core search (OCXO suffices there).

Provenance note: this roadmap was produced with AI assistance
(generation + adversarial review); the arithmetic shown is the
review pass's, and every number should be re-derived independently
before external use. The raw 28-entry catalog with full assessments
is retained by the maintainer.
