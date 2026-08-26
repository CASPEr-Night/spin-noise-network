# Benchtop feasibility: water spin noise on Spinsolve-class instruments

*Physics memo for Work Package B (Magritek/Spinsolve port). Every number here
is produced by the committed companion script `benchtop_feasibility.py` in
this directory — run it to reproduce the tables. Assumptions are stated as
ranges; the ranges ARE the claim. Contact: John W. Blanchard,
jwbquantum@gmail.com.*

---

## 1. Question

Can a Magritek Spinsolve benchtop spectrometer (43 / 60 / 80 / 90 MHz ¹H;
permanent Halbach magnet, room-temperature coil, no cryogens) see the water
spin-noise feature at all — and what would a noise record from one be worth
to the axion search?

## 2. Physics inputs

The FDT-verified master formula for the detected voltage PSD near the line:

```
S_V(Δ)/S_floor = 1 + f_c·λ_r·[(Ts/Tc − 2)·λ − λ_r] / (λ_tot² + Δ²),
f_c = Tc/(Tc + T_A)
```

A benchtop is the textbook **uniform-temperature** case: coil, sample, and
magnet all sit at the regulated magnet temperature (~300 K — Spinsolve
magnets are thermally stabilized, and sample temperature control is done by
adjusting magnet temperature, not gas flow). Uniform temperature gives a pure
Lorentzian absorption **dip** (the Guéron dip) of fractional depth
`f_c·λ_r/λ_tot` and HWHM `λ_tot`, with

```
λ_r  = (1/2)·μ₀·η·Q·γ·M₀            (radiation-damping rate)
M₀   = n·γ²·ħ²/(4·k_B·Ts)·B₀        (Curie law, I = 1/2)
λ_tot = λ₀ + λ_r                     (rd adds to the dressed width,
                                      so the depth self-caps at f_c < 1)
```

Assumption ranges (all propagated jointly by the script):

| quantity | range | basis |
|---|---|---|
| η (filling factor) | 0.3–0.6 | benchtop coil closely wraps the 5 mm tube; plausible span, **not measured** |
| Q (loaded) | 50–250 | typical small RT-probe range; **must be measured at the bench session** |
| amplitude FWHM λ₀/π | 0.2–0.5 Hz | vendor 50% linewidth claims: 0.2 Hz for the ULTRA models ([Spinsolve family page](https://magritek.com/products/benchtop-nmr-spectrometer-spinsolve/)); <0.4 Hz for Spinsolve 90 ([AZoM spec sheet](https://www.azom.com/equipment-details.aspx?EquipID=8001)); 0.5 Hz for Spinsolve 80 ([launch announcement](https://www.teknoscienze.com/magritek-launch-the-80-mhz-spinsolve-80-the-highest-performance-and-most-powerful-benchtop-nmr-spectrometer-in-the-world/)) |
| T_A (amplifier noise temp) | 100–300 K | uncooled front end |
| Ts = Tc | 300 K | uniform-temperature benchtop |
| sample | neat water | n = 6.67×10²⁸ ¹H/m³ |

B₀ from the nominal frequency: B₀ = ν/(42.577 MHz/T).

## 3. Results

From `benchtop_feasibility.py` (ranges = min/max over the full assumption
box):

| ν (¹H) | B₀ | M₀ (A/m) | λ_r (s⁻¹) | λ_tot (s⁻¹) | dip FWHM (Hz) | **dip depth** | **t(5σ)** |
|---|---|---|---|---|---|---|---|
| 43 MHz | 1.010 T | 3.23×10⁻³ | 8.2–81.5 | 8.8–83.1 | 2.8–26.5 | **42–74%** | 1.7–46 s |
| 60 MHz | 1.409 T | 4.51×10⁻³ | 11.4–114 | 12.0–115 | 3.8–36.7 | **44–75%** | 1.2–31 s |
| 80 MHz | 1.879 T | 6.02×10⁻³ | 15.2–152 | 15.8–153 | 5.0–48.8 | **45–75%** | 0.9–23 s |
| 90 MHz | 2.114 T | 6.77×10⁻³ | 17.1–171 | 17.7–172 | 5.6–54.8 | **46–75%** | 0.8–20 s |

The structural fact behind these numbers: even at the *pessimistic* corner
(η = 0.3, Q = 50), λ_r ≈ 8 s⁻¹ already exceeds the vendor-linewidth
λ₀ ≈ 0.6–1.6 s⁻¹ by an order of magnitude. **A neat-water line on a benchtop
would be radiation-damping dominated**, so λ_r/λ_tot ≈ 1 and the dip depth
saturates near its cap f_c = Tc/(Tc+T_A) = 0.5–0.75. (Consistency check:
vendor lineshape specs are quoted on dilute/doped standards precisely because
neat water self-broadens; nobody should expect to see 0.2 Hz on a neat-water
tube, and we do not assume it — λ₀ enters only as the *non-rd* part of the
width.)

The time-to-5σ is a matched-bin estimate, t = 25/(depth²·FWHM), which is
deliberately simple and optimistic by an O(1) factor (no window losses, no
drift). Even taking that factor as 5×, **every corner of the assumption box
puts a 5σ detection inside a few minutes of noise data.**

### What could break this

The dip is large *if the model applies*. Honest failure modes, all checkable
in one bench session: (i) η·Q far below the assumed range (the single biggest
unknown — measure Q directly); (ii) the receiver chain not noise-floor-limited
at the relevant offsets (1/f, digital filter artifacts); (iii) the external
lock or magnet temperature regulation moving the line during acquisition
(interleaved references catch this); (iv) a doped/dilute sample — the 2022
lesson, hence the answers file records the sample; (v) the tuned-circuit
back-action changing the observed lineshape from the simple uniform-T formula
(the b/a phase admixture seen in the 2020 pilot; the fit must include line
phase).

If the measurement works, it would be — to the best of our knowledge, and
stated as a possibility, not a promise — **potentially the first spin-noise
detection on a benchtop NMR spectrometer**. Published spin-noise NMR work to
date (McCoy–Ernst 1989; Guéron–Leroy 1989; the modern cryoprobe literature)
is on high-field iron-magnet or superconducting systems.

## 4. Axion coverage

The field-to-mass map m_a[μeV] = ν[MHz]·4.1357×10⁻³ puts the Spinsolve family
at:

| ν | m_a |
|---|---|
| 43 MHz | 0.178 μeV |
| 60 MHz | 0.248 μeV |
| 80 MHz | 0.331 μeV |
| 90 MHz | 0.372 μeV |

The band **0.18–0.37 μeV begins just above the previous highest-mass
nuclear-spin dark-matter search at 166 neV = 0.166 μeV** (Aybas et al., PRL
126, 141802 (2021), CASPEr-e): the lowest Spinsolve point, 43 MHz =
0.178 μeV, sits within ~7% of that mass, leaving a narrow 0.166–0.178 μeV
sliver uncovered. The band sits below the 600 MHz pilot point at
2.48 μeV. No laboratory nuclear-spin experiment has constrained the proton
gradient coupling in this band.

### Honest sensitivity vs the 600 MHz pilot

Thermal M₀ is what the axion tips, and at 1–2 T it is 5–10× smaller than at
14.1 T (polarization 3.4–7.2×10⁻⁶ vs 4.8×10⁻⁵). Scaling the pilot's
worst-case construction (g_lim ∝ √(Γ·kT/(η·Q·ω))/M₀, ratios taken against the
pilot with (η·Q)_bench/(η·Q)_pilot scanned over 0.1–10 because the pilot's
detached-cable η·Q is bounded, not measured):

| ν | g_ap per pilot-equivalent exposure |
|---|---|
| 43 MHz | ~3×10⁻⁴ – 9×10⁻³ GeV⁻¹ |
| 60 MHz | ~2×10⁻⁴ – 7×10⁻³ GeV⁻¹ |
| 80 MHz | ~2×10⁻⁴ – 5×10⁻³ GeV⁻¹ |
| 90 MHz | ~1×10⁻⁴ – 4×10⁻³ GeV⁻¹ |

i.e. roughly one to two orders of magnitude weaker than the pilot's
5.12×10⁻⁵ GeV⁻¹, and five to seven orders above the SN1987A astrophysical
floor (~3.3×10⁻¹⁰ GeV⁻¹).

**What a benchtop exclusion would mean:** the first laboratory constraint on
the nuclear-spin gradient coupling anywhere in 0.18–0.37 μeV; a working,
cheap, cryogen-free network node whose magnet is at field 24/7 (overnight and
weekend exposure is nearly free); and — via the large expected dip — the
cleanest possible calibration anchor for the temperature-contrast law the
network measures, since the benchtop is the uniform-temperature limit where
the theory has no free parameters beyond η·Q.

**What it would not mean:** astrophysical competitiveness. An unpolarized
thermal benchtop run will not approach SN1987A, full stop. The honest value
is metrology, band coverage with laboratory systematics, network methodology,
and — exactly as at 600 MHz — a documented path where hyperpolarization, not
more averaging, buys the missing orders of magnitude.

---

*Author: John W. Blanchard (jwbquantum@gmail.com), with Claude (Anthropic,
San Francisco, California, USA). Draft pending bench validation; see
README.md in this directory for the validation checklist.*
