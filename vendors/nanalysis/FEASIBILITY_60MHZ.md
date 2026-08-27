# 60 MHz feasibility: water spin noise on a Nanalysis NMReady 60PRO

*Sibling note to `vendors/magritek/BENCHTOP_FEASIBILITY.md` — same physics,
same formulas, same companion calculation (`benchtop_feasibility.py` in the
Magritek directory), re-evaluated at 60 MHz with Nanalysis-spec parameters.
The appendix gives the exact parameter swap so every number here is
reproducible. Ranges are stated jointly; the ranges ARE the claim.
Contact: John W. Blanchard, jwbquantum@gmail.com.*

---

## 1. Question

The second benchtop brand in the network: can a Nanalysis NMReady 60PRO
(60 MHz ¹H, 1.4 T thermally regulated permanent magnet, room-temperature
integrated probe, no cryogens) see the neat-water spin-noise dip — and does
Nanalysis's linewidth spec, which differs from Magritek's, change the
Magritek memo's conclusion?

## 2. What actually differs from the Magritek 60 MHz case

The master formula and the uniform-temperature (Guéron dip) limit are
identical — coil, sample, and magnet share one regulated temperature, so the
expected feature is a pure Lorentzian absorption dip of depth
`f_c·λ_r/λ_tot`, HWHM `λ_tot = λ₀ + λ_r`, with `f_c = Tc/(Tc + T_A)`.
Three inputs move:

| input | Magritek memo (60 MHz) | this note (NMReady 60PRO) | basis |
|---|---|---|---|
| λ₀/π (amplitude FWHM, vendor lineshape spec) | 0.2–0.5 Hz | **0.5–1.0 Hz** | 60PRO spec sheet and 2020 user manual: resolution/lineshape LW(50%) < 1.0 Hz ([Nanalysis 60PRO brochure](https://yairtech.co.il/wp-content/uploads/2018/04/170109-60PRO-Brochure-web.pdf); [user manual, WPI copy](https://www.wpi.edu/sites/default/files/2025-07/Nanalysis-100-60-user-manual.pdf), 60 MHz specifications table). The current-production "Nanalysis-60" page claims < 0.5 Hz (< 0.008 ppm) at 50% and < 10 Hz (0.17 ppm) at 0.55% ([nanalysis.com](https://www.nanalysis.com/nmready-60pro)); the partner's older 60PRO is conservatively taken at the ≤ 1.0 Hz end, so the range spans both vintages. |
| Ts = Tc | 300 K | **306 K** | NMReady magnets are thermally regulated *above* room temperature; real v2.2.4.5 instrument output carries `##TEMPERATURE=33.000042` (Celsius-like, unit UNVERIFIED — partner checklist). A 2% temperature shift is immaterial to every number below; the table uses 300 K so the comparison with the Magritek memo is exact, and the appendix shows the 306 K variant changes depths by < 1%. |
| η, Q | 0.3–0.6, 50–250 | **same ranges, same honesty** | the NMReady coil geometry and loaded Q are not published; same plausible small-RT-probe box as the Magritek memo, and the partner session must measure Q (checklist). |

Everything else (neat-water proton density, Curie M₀ at 1.409 T,
T_A = 100–300 K uncooled front end) is unchanged.

## 3. Result at 60 MHz (B₀ = 1.409 T, m_a = 0.248 μeV)

Ranges = min/max over the full assumption box (η 0.3–0.6, Q 50–250,
λ₀/π 0.5–1.0 Hz, T_A 100–300 K, Ts = Tc = 300 K):

| quantity | value |
|---|---|
| M₀ (Curie, neat water) | 4.51×10⁻³ A/m (polarization 4.8×10⁻⁶) |
| λ_r | 11.4 – 114 s⁻¹ |
| λ₀ = π·LW | 1.6 – 3.1 s⁻¹ |
| λ_tot | 12.9 – 117 s⁻¹ (dip FWHM 4.1 – 37.2 Hz) |
| **dip depth** | **39% – 74%** |
| **t(5σ), matched-bin estimate** | **1.2 – 35 s** |

For comparison, the Magritek memo's 60 MHz row (λ₀/π 0.2–0.5 Hz) reads
44–75% and 1.2–31 s. **The linewidth-spec difference is immaterial**, and
the reason is structural, not accidental: even at the pessimistic corner
(η = 0.3, Q = 50) the radiation-damping rate λ_r ≈ 11 s⁻¹ exceeds the
worst-case Nanalysis λ₀ ≈ 3.1 s⁻¹ by a factor of ~4 (and the best-case
corner by a factor of ~70). A neat-water line on this instrument would be
**radiation-damping dominated**, so the depth saturates near its cap
f_c = Tc/(Tc+T_A) = 0.5–0.75 regardless of whether the shimmed linewidth is
0.2 Hz or 1.0 Hz. The only place the worse λ₀ shows up is the pessimistic
depth corner (39% vs 44%): when λ_r is smallest, a fatter λ₀ eats a little
more of the contrast. The dip stays minutes-scale detectable at every corner
of the box. (Same honest caveat as the parent memo: the t(5σ) estimator is
deliberately simple and optimistic by an O(1) factor; even 5× slower, every
corner is inside a few minutes of noise data.)

Consistency note, unchanged from the parent memo: vendor lineshape specs are
quoted on doped/standard samples precisely because neat water self-broadens
by radiation damping; λ₀ enters here only as the *non-rd* part of the width,
which is exactly what the spec bounds.

### What could break this

The parent memo's five failure modes apply verbatim (η·Q below the box — the
single biggest unknown; receiver chain not noise-floor-limited at ±5–40 Hz
offsets; the line moving during acquisition; a doped/dilute sample — the
2022 lesson; tuned-circuit back-action distorting the simple uniform-T
lineshape). Two are NMReady-specific and land on the partner checklist in
`README.md`: (i) the **internal lock** — the NMReady locks on ¹H or ²H
through its internal hardware; whether the lock channel radiates in the ¹H
band or steps B₀ *during* a record is not publicly documented and must be
established (and is exactly what the interleaved references measure);
(ii) the **automatic receiver-gain adjustment** the stock software performs
before each acquisition — the noise protocol needs a *fixed* gain (the
Experiment Settings menu allows one; verify it is honored record-to-record).

## 4. Axion coverage

60 MHz sits at **m_a = 0.248 μeV** — the same axion-mass point as a
Magritek Spinsolve 60, and that is a feature, not redundancy: two benchtop
*brands* at one mass point give the network a same-frequency,
different-hardware coincidence pair (different lock architecture, different
magnet regulation, different receiver chain — common-mode instrumental lines
do not survive the comparison, a genuine fixed-frequency dark-matter line
does, up to the site-dependent sidereal Doppler modulation the network
analysis exploits).

Honest sensitivity, scaled from the 600 MHz pilot exactly as in the parent
memo (η·Q ratio scanned 0.1–10):

| quantity | value |
|---|---|
| g_ap per pilot-equivalent exposure | ~2×10⁻⁴ – 7×10⁻³ GeV⁻¹ |
| vs the pilot's worst-case limit | 4–130× weaker |
| vs the SN1987A floor (~3.3×10⁻¹⁰) | 5–7 orders above |

What a 60PRO exclusion would mean: a second instrument class contributing
laboratory coverage at 0.248 μeV inside the 0.18–0.37 μeV benchtop band that
adjoins the previous highest-mass nuclear-spin search (0.166 μeV, Aybas et
al., PRL 126, 141802 (2021)); a cryogen-free node whose magnet is at field
24/7; and one more (probe, field, tuning) point for the temperature-contrast
law at the clean uniform-temperature limit. What it would not mean:
astrophysical competitiveness — unpolarized thermal benchtop runs will not
approach SN1987A, full stop; hyperpolarization, not averaging, is the
documented path to the missing orders of magnitude.

## Appendix: reproducing these numbers

Every number above comes from the committed Magritek companion script with
the three-parameter swap of Section 2:

```python
# from the repository root:
import importlib.util
spec = importlib.util.spec_from_file_location(
    "bf", "vendors/magritek/benchtop_feasibility.py")
bf = importlib.util.module_from_spec(spec); spec.loader.exec_module(bf)
bf.LW_RANGE_HZ = (0.5, 1.0)   # Nanalysis 60PRO lineshape spec (Section 2)
bf.FREQS_MHZ = (60.0,)        # the 60PRO point
bf.main()                     # Ts=Tc=300 K, as tabled
# 306 K variant (regulated magnet temperature, TEMPERATURE=33 C reading):
# setting T_SAMPLE = T_COIL = 306.0 moves M0 by -2%, lambda_r by -2%, and
# every depth corner by <1% -- immaterial, as claimed.
```

---

*Author: John W. Blanchard (jwbquantum@gmail.com), with Claude (Anthropic,
San Francisco, California, USA). Draft pending partner validation; see
README.md in this directory for the validation checklist.*
