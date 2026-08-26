#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
benchtop_feasibility.py -- honest feasibility numbers for a water spin-noise
measurement on Magritek Spinsolve-class benchtop NMR spectrometers
(43 / 60 / 80 / 90 MHz 1H), and the axion-band context.

Companion calculation to BENCHTOP_FEASIBILITY.md (same directory); every
number quoted there is produced by this script.  Run:

    python3 vendors/magritek/benchtop_feasibility.py

Physics (from the project fact brief, FDT-verified master formula):

    S_V(Delta)/S_floor = 1 + f_c*lambda_r*[(Ts/Tc - 2)*lambda - lambda_r]
                             / (lambda_tot**2 + Delta**2)
    f_c = Tc/(Tc + T_A)

  Uniform temperature (Ts = Tc, the benchtop case: coil and sample are both
  at the regulated magnet temperature) gives a pure Lorentzian absorption
  DIP (the Gueron dip) of fractional depth

    depth = f_c * lambda_r / lambda_tot,       HWHM = lambda_tot,

  with lambda_tot = lambda_0 + lambda_r (radiation damping adds to the
  dressed width; the dip depth is therefore self-capped at f_c < 1).

    lambda_r = (1/2) * mu0 * eta * Q * gamma * M0          [rad/s]
    M0       = n * gamma^2 * hbar^2 * I(I+1)/(3 kB Ts) * B0  (Curie, I=1/2)

Assumption RANGES (stated, not hidden):
    eta (filling factor)         0.3 - 0.6   (benchtop coil closely wraps
                                              the 5 mm tube; plausible span)
    Q (loaded coil quality)      50 - 250    (typical small RT probe range;
                                              NOT measured on a Spinsolve --
                                              a bench session must measure it)
    LW (amplitude FWHM, vendor)  0.2 - 0.5 Hz (vendor-claimed 50% linewidths
                                              across the Spinsolve family:
                                              0.2 Hz ULTRA models, ~0.4-0.5 Hz
                                              standard models; see memo for
                                              citations.  lambda_0 = pi*LW.)
    T_A (amplifier noise temp)   100 - 300 K
    Tc = Ts = 300 K              (uniform-temperature benchtop; Spinsolve
                                  magnets are thermally regulated near ~300 K)

All outputs are ranges over the cross-product of these assumption ranges.
"""

from __future__ import print_function

import itertools

# --- constants (SI) --------------------------------------------------------
MU0 = 4.0e-7 * 3.141592653589793      # T m / A
HBAR = 1.054571817e-34                # J s
KB = 1.380649e-23                     # J / K
NA = 6.02214076e23                    # 1 / mol
GAMMA_H = 2.6752218744e8              # rad / s / T  (1H gyromagnetic ratio)
GAMMA_H_MHZ_T = 42.5774689            # MHz / T
PI = 3.141592653589793

# --- water sample ----------------------------------------------------------
RHO_WATER = 997.0                     # kg/m^3 at ~25 C
M_WATER = 0.018015                    # kg/mol
N_PROTON = 2.0 * RHO_WATER * NA / M_WATER   # protons / m^3 (~6.67e28)

# --- assumption ranges ------------------------------------------------------
ETA_RANGE = (0.3, 0.6)
Q_RANGE = (50.0, 250.0)
LW_RANGE_HZ = (0.2, 0.5)              # vendor amplitude FWHM claims
TA_RANGE_K = (100.0, 300.0)
T_SAMPLE = 300.0                      # K; = Tc (uniform-temperature benchtop)
T_COIL = 300.0

FREQS_MHZ = (43.0, 60.0, 80.0, 90.0)  # nominal Spinsolve 1H frequencies

# --- 600 MHz pilot reference (2020 EPFL dataset, from the fact brief) -------
PILOT_NU_MHZ = 600.133705802
PILOT_B0_T = 14.095
PILOT_LAMBDA_TOT = PI * 11.9          # s^-1, dressed width of the noise line
PILOT_GAP_LIMIT = 5.12e-5             # GeV^-1, worst-case 90% CL at 2.4819 ueV
# neat protonated acetone: 6 1H per molecule, rho = 784 kg/m^3, M = 58.08 g/mol
N_PROTON_ACETONE = 6.0 * 784.0 * NA / 0.05808
UEV_PER_MHZ = 4.1357e-3               # m_a[ueV] = nu[MHz] * this


def curie_m0(n_spins, b0_t, temp_k):
    """Curie-law equilibrium magnetization for spin-1/2, A/m."""
    return n_spins * GAMMA_H**2 * HBAR**2 / (4.0 * KB * temp_k) * b0_t


def lambda_r(eta, q, m0):
    """Radiation-damping rate, s^-1 (rad/s of exponential decay)."""
    return 0.5 * MU0 * eta * q * GAMMA_H * m0


def dip_depth(lam_r, lam_0, t_c, t_a):
    """Uniform-temperature Gueron dip fractional depth f_c*lambda_r/lambda_tot."""
    f_c = t_c / (t_c + t_a)
    return f_c * lam_r / (lam_0 + lam_r)


def time_to_5sigma(depth, lam_tot):
    """Seconds of noise data to see the dip at 5 sigma.

    Single-bin estimate with the resolution bandwidth matched to the dip
    width: a Welch PSD bin of width W = FWHM = lambda_tot/pi averaged for
    time T contains ~T*W independent periodogram estimates, so the
    fractional PSD uncertainty is 1/sqrt(T*W) and the dip significance is
    depth*sqrt(T*W).  T_5sigma = 25 / (depth^2 * W).  (Optimistic by design
    simplicity -- no window losses, no drift; a real analysis loses an O(1)
    factor.  Stated as such in the memo.)
    """
    w_hz = lam_tot / PI
    return 25.0 / (depth * depth * w_hz)


def fmt_time(seconds):
    if seconds < 60:
        return "%.1f s" % seconds
    if seconds < 3600:
        return "%.1f min" % (seconds / 60.0)
    if seconds < 86400:
        return "%.1f h" % (seconds / 3600.0)
    return "%.1f d" % (seconds / 86400.0)


def main():
    print("=" * 78)
    print("Benchtop (Spinsolve-class) water spin-noise feasibility")
    print("assumptions: eta %.1f-%.1f, Q %.0f-%.0f, vendor LW %.1f-%.1f Hz,"
          % (ETA_RANGE[0], ETA_RANGE[1], Q_RANGE[0], Q_RANGE[1],
             LW_RANGE_HZ[0], LW_RANGE_HZ[1]))
    print("             T_A %.0f-%.0f K, Ts = Tc = %.0f K (uniform temperature)"
          % (TA_RANGE_K[0], TA_RANGE_K[1], T_SAMPLE))
    print("=" * 78)

    corners = list(itertools.product(ETA_RANGE, Q_RANGE, LW_RANGE_HZ, TA_RANGE_K))

    summary = {}
    for nu in FREQS_MHZ:
        b0 = nu / GAMMA_H_MHZ_T
        m0 = curie_m0(N_PROTON, b0, T_SAMPLE)
        pol = GAMMA_H * HBAR * b0 / (2.0 * KB * T_SAMPLE)

        lam_r_lo = lambda_r(ETA_RANGE[0], Q_RANGE[0], m0)
        lam_r_hi = lambda_r(ETA_RANGE[1], Q_RANGE[1], m0)

        depths = []
        times = []
        for eta, q, lw, ta in corners:
            lr = lambda_r(eta, q, m0)
            l0 = PI * lw
            d = dip_depth(lr, l0, T_COIL, ta)
            depths.append(d)
            times.append(time_to_5sigma(d, l0 + lr))
        d_lo, d_hi = min(depths), max(depths)
        t_lo, t_hi = min(times), max(times)

        m_a = nu * UEV_PER_MHZ

        print()
        print("--- %g MHz  (B0 = %.3f T, m_a = %.3f ueV) ---" % (nu, b0, m_a))
        print("  M0 (Curie, neat water)     : %.3e A/m   (polarization %.2e)"
              % (m0, pol))
        print("  lambda_r                   : %.1f - %.1f s^-1" % (lam_r_lo, lam_r_hi))
        print("  lambda_0 = pi*LW           : %.2f - %.2f s^-1"
              % (PI * LW_RANGE_HZ[0], PI * LW_RANGE_HZ[1]))
        print("  lambda_tot = lambda_0+lambda_r : %.1f - %.1f s^-1  (FWHM %.1f - %.1f Hz)"
              % (PI * LW_RANGE_HZ[0] + lam_r_lo, PI * LW_RANGE_HZ[1] + lam_r_hi,
                 (PI * LW_RANGE_HZ[0] + lam_r_lo) / PI,
                 (PI * LW_RANGE_HZ[1] + lam_r_hi) / PI))
        print("  NOTE: lambda_r >> lambda_0 across the whole assumption box --")
        print("        the neat-water line would be radiation-damping DOMINATED.")
        print("  Gueron dip depth           : %.0f%% - %.0f%%  (self-capped at f_c)"
              % (100 * d_lo, 100 * d_hi))
        print("  time to 5 sigma (matched-bin estimate): %s - %s"
              % (fmt_time(t_lo), fmt_time(t_hi)))
        summary[nu] = (d_lo, d_hi, t_lo, t_hi, m0, b0)

    # ---- axion context ------------------------------------------------------
    print()
    print("=" * 78)
    print("Axion-band context and honest sensitivity vs the 600 MHz pilot")
    print("=" * 78)
    print("  band covered by 43-90 MHz    : m_a = %.3f - %.3f ueV"
          % (FREQS_MHZ[0] * UEV_PER_MHZ, FREQS_MHZ[-1] * UEV_PER_MHZ))
    print("  previous highest-mass nuclear-spin search: 166 neV = 0.166 ueV")
    print("  (Aybas et al., PRL 126, 141802 (2021)) -- the benchtop band ADJOINS it.")

    m0_pilot = curie_m0(N_PROTON_ACETONE, PILOT_B0_T, 298.0)
    print()
    print("  600 MHz pilot reference: M0(acetone, 14.095 T, 298 K) = %.3e A/m"
          % m0_pilot)
    print("  pilot worst-case limit: g_ap < %.2e GeV^-1 at 2.4819 ueV"
          % PILOT_GAP_LIMIT)

    # Scaling estimate for the benchtop limit.  For the same worst-case
    # analysis (entire line power attributed to signal, spin-noise-limited
    # floor ~ kT), the coupling limit scales as
    #     g_lim  ~  sqrt( Gamma_dressed * kT / (eta*Q*omega) ) / M0
    # (axion tips M0 -> transverse EMF ~ omega*M_T; detected line power
    #  ~ eta*Q*omega*M0^2*theta^2; floor ~ kT).  Taking ratios to the pilot
    # cancels kT and the O(1) analysis construction.  eta*Q of the pilot's
    # detached-cable cryoprobe state is not separately known, so the ratio
    # (eta*Q)_bench/(eta*Q)_pilot is scanned over 0.1-10 -- an honest
    # admission that this term is only bounded, not measured.  Sample volume
    # in the coil is taken comparable (both 5 mm tubes); the memo flags this.
    print()
    print("  Scaling estimate (order-of-magnitude; assumptions in source):")
    for nu in FREQS_MHZ:
        d_lo, d_hi, t_lo, t_hi, m0, b0 = summary[nu]
        lam_lo = lambda_r(ETA_RANGE[0], Q_RANGE[0], m0) + PI * LW_RANGE_HZ[0]
        lam_hi = lambda_r(ETA_RANGE[1], Q_RANGE[1], m0) + PI * LW_RANGE_HZ[1]
        gs = []
        for lam, etaq_ratio in itertools.product((lam_lo, lam_hi), (0.1, 10.0)):
            ratio = ((lam / PILOT_LAMBDA_TOT) ** 0.5
                     * (PILOT_NU_MHZ / nu) ** 0.5
                     * (1.0 / etaq_ratio) ** 0.5
                     * (m0_pilot / m0))
            gs.append(PILOT_GAP_LIMIT * ratio)
        print("    %g MHz: g_ap ~ %.1e - %.1e GeV^-1 per pilot-equivalent exposure"
              % (nu, min(gs), max(gs)))
    print()
    print("  For comparison: SN1987A astrophysical bound ~3.3e-10 GeV^-1;")
    print("  a thermal benchtop exclusion is ~5-7 orders of magnitude above it.")
    print("  What it WOULD be: the first laboratory nuclear-spin constraint in")
    print("  0.18-0.37 ueV; a real detector-network node; a spin-noise-limited")
    print("  metrology record.  What it would NOT be: astrophysically competitive.")


if __name__ == "__main__":
    main()
