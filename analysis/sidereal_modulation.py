#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sidereal_modulation.py -- the sidereal-modulation fit of a session's
per-row line powers (analysis specification v0.8, Sec. 9; port of the
2026-10-06 prototype sidereal_modulation_fit.py, which this file
supersedes for the report).

Signal model (Sec. 9.1): the expected excess power in row k is
P_a f(t_k; Delta), where f(t; Delta) = xi^2(theta(t), V; Delta) /
<xi^2>_rows is the exclusion's response integral at the scan offset
Delta, evaluated at the wind angle theta(t) of the row (2 degree bins,
halo_wind.alp_lineshape_angle on the exclusion's random stream, every
bin at the session-mean |v_lab|) and normalised to its row mean. The
thermal spin-noise line and the receiver floor do not depend on sidereal
time, so the constant term of P_k = P_SN + P_a f(t_k) absorbs the line
and only the modulated fraction of a putative signal is tested: a bound
that needs no spin-noise subtraction and, unlike the entire-line-power
constructions, scales as 1/sqrt(tau_m).

Estimator (Sec. 9.2): P_k is the LINEAR fixed-shape line power -- the
amplitude of the session's headline line shape (centre, width and
dispersive fraction fixed) solved per row by linear least squares
against the baseline-normalised PSD, times pi w/2 x base_k
(fixed_shape_amplitude below; facility_report stores it as
per_row[].line_power_fixed_shape_counts2). The free five-parameter
per-row fit is used only in the debug/regression mode
SPINNOISE_SIDEREAL_ESTIMATOR=free_fit, which reproduces the Sec. 9.6
prototype targets.

Fits (Secs. 9.2-9.3): ordinary least squares; one robust clip of the
all-row residuals (|r - median| > ROW_CLIP_NMAD x 1.4826 x MAD, row set
fixed once from the raw two-parameter fit at the exclusion's wind-aware
best offset) and one refit; raw and floor-normalised variants, drift
terms, the per-row floor as a regressor against the solar-day
degeneracy; one-sided bound P_90,mod = max(P_a, 0) + t_90(n - p)
sigma(P_a); the per-offset curve on the exclusion's scan grid.

Python 3.8 compatible, numpy only (the Student-t quantile and the
halo_wind module are passed in by facility_report, which loads this
file by path). UNPUBLISHED, internal: a preliminary sensitivity
bookkeeping, not a detection claim and not a publication-grade limit.
"""
import datetime
import math

import numpy as np

LABEL = ("sidereal-modulation fit, preliminary and internal --- one-sided "
         "90% CL bound on the modulated excess under the standard halo at "
         "this site's wind angles; not a detection claim")
HEADING = ("Sidereal-modulation fit (preliminary, internal; not a detection "
           "claim)")
ROW_CLIP_NMAD_DEFAULT = 5.0      # facility_report.ROW_CLIP_NMAD is passed in
MAD_TO_SIGMA = 1.4826
MIN_ROWS = 30                    # spec Sec. 9.2 skip rule
MIN_TEMPLATE_STD = 0.02          # ... at the exclusion's wind-aware best offset
SKIP_NO_LEVERAGE = "no modulation leverage in this session's hours"
DRIFT_QUADRATIC_ABOVE_H = 6.0    # spec Sec. 9.3 (b)
ANTIPHASE_SHIFT_H = 12.0         # spec Sec. 7 (vi): f(t + 12 h)
ESTIMATOR_RATIO_WARN = 0.10      # |<P_k>/(P_net/win_frac) - 1| QA threshold
ROWS_EXCLUDED_WARN_FRAC = 0.10
DAY_CURVE_LEAD_H = 6.0           # the plotted sidereal day starts 6 h early
DAY_CURVE_N = 241
SHM_V0_KMS = 220.0
# spec Sec. 9.3 (c), referee E2: the per-row floor regressors are low-passed
# (edge-aware running mean in time order) over about FLOOR_SMOOTH_SPAN_H so
# the per-row estimation noise of baseline_psd_at_line (reliability ~0.6 at
# 180 s synthetic rows) does not attenuate the regressor's coefficient and
# leave the 24 h laboratory cycle in P_a (errors-in-variables)
FLOOR_SMOOTH_SPAN_H = 1.5
FLOOR_SMOOTH_MIN_ROWS = 5
FLOOR_SMOOTH_MAX_FRAC = 0.25     # the window never exceeds a quarter of the rows
VIF_COLLINEAR_WARN = 5.0         # VIF(f | regressors) above this: leverage surrendered
VIF_COLLINEAR_FAIL = 20.0        # ... above this (sigma x4.5+): not separable
SMOOTHED_SIGNAL_NOISY_BELOW = 0.8  # resolved fraction of the smoothed regressor's
#                                  variance below this: regressor noisy
CENTRE_OFFSET_SMOOTH_ROWS = 31   # referee P3 centre_offset diagnostic (running median)
LAM_BIN_KEY_DECIMALS = 6         # E7 lam_bins cache keys: bin centre deg rounded


# ----------------------------------------------------------- estimator
def fixed_shape_amplitude(f_hz, pnorm, center_hz, fwhm_hz, disp_fraction,
                          half_hz, min_points=30):
    """Amplitude a of the fixed line shape a [L(u) + r u L(u)] + c, u =
    (f - f0)/(w/2), L = 1/(1 + u^2), r the headline dispersive fraction
    b/a, solved by linear least squares on |f - f0| < half_hz of the
    baseline-normalised PSD (spec Sec. 9.2). None when the window holds
    fewer than min_points bins."""
    f_hz = np.asarray(f_hz, dtype=float)
    pnorm = np.asarray(pnorm, dtype=float)
    m = np.abs(f_hz - center_hz) < half_hz
    if int(np.count_nonzero(m)) < min_points or not fwhm_hz:
        return None
    u = (f_hz[m] - center_hz) / (fwhm_hz / 2.0)
    L = 1.0 / (1.0 + u * u)
    M = np.column_stack([L * (1.0 + disp_fraction * u), np.ones(L.size)])
    coef = np.linalg.lstsq(M, pnorm[m], rcond=None)[0]
    return float(coef[0])


# ------------------------------------------------------------ template
def theta_bin_index(theta_deg, bin_deg):
    """(bin index per row, bin centres) on the 2 degree grid of Sec. 8.3."""
    edges = np.arange(0.0, 90.0 + bin_deg, bin_deg)
    idx = np.clip(np.digitize(np.asarray(theta_deg, dtype=float), edges) - 1,
                  0, len(edges) - 2)
    centres = 0.5 * (edges[:-1] + edges[1:])
    return idx, centres


class TemplateBank(object):
    """xi^2(theta_bin; Delta) over the scan grid, one Monte-Carlo lineshape
    per 2 degree bin (cached), the exclusion's response integral
    vectorised over the offsets."""

    def __init__(self, hw, grid_hz, nu_a_hz, gamma_per_s, offsets_hz,
                 vlab_kms, bin_deg, nsamp, seed, c_omega2, lam_bins=None):
        self.hw = hw
        self.grid = np.asarray(grid_hz, dtype=float)
        self.nu_a = float(nu_a_hz)
        self.offsets = np.asarray(offsets_hz, dtype=float)
        self.vlab = float(vlab_kms)
        self.bin_deg = float(bin_deg)
        self.nsamp = int(nsamp)
        self.seed = int(seed)
        self.c_omega2 = float(c_omega2)
        self.resp = 1.0 / (gamma_per_s ** 2 + (
            2.0 * math.pi * (self.offsets[:, None] + self.grid[None, :])) ** 2)
        self.trapz = getattr(np, "trapezoid", None) or np.trapz
        self._bins = {}
        self.n_seeded = 0
        # E7: seed the cache from the lineshapes the exclusion's
        # session_lineshape already computed for this session's bins (same
        # grid, nu_a, session-mean |v_lab|, nsamp and seed, so the xi^2 is
        # bit-identical to a fresh Monte Carlo); keyed by bin centre in
        # degrees rounded to 1e-6; anything that does not match is ignored
        if lam_bins:
            _idx, centres = theta_bin_index([0.0], self.bin_deg)
            by_key = {}
            for b in range(len(centres)):
                by_key[round(float(centres[b]), LAM_BIN_KEY_DECIMALS)] = b
            for key, lam in lam_bins.items():
                try:
                    b = by_key.get(round(float(key), LAM_BIN_KEY_DECIMALS))
                    lam = np.asarray(lam, dtype=float)
                except (TypeError, ValueError):
                    continue
                if b is None or lam.shape != self.grid.shape:
                    continue
                self._bins[b] = self._xi2_from_lam(lam)
                self.n_seeded += 1

    def _xi2_from_lam(self, lam):
        return self.c_omega2 * self.trapz(lam[None, :] * self.resp, self.grid,
                                          axis=1)

    def xi2_bin(self, b, centres):
        if b not in self._bins:
            lam, _vp = self.hw.alp_lineshape_angle(
                self.grid, self.nu_a, float(centres[b]), self.vlab,
                self.nsamp, self.seed)
            self._bins[b] = self._xi2_from_lam(lam)
        return self._bins[b]

    def xi2_rows(self, theta_deg):
        """xi^2[k, j] for rows at theta_deg[k] and offsets[j]."""
        idx, centres = theta_bin_index(theta_deg, self.bin_deg)
        rows = [self.xi2_bin(int(b), centres) for b in idx]
        return np.vstack(rows) if rows else np.zeros((0, self.offsets.size))

    def n_bins_evaluated(self):
        return len(self._bins)


# ------------------------------------------------------------- fitting
def ols(P, X):
    """Ordinary least squares P = X beta: (beta, err, sigma_row, resid,
    dof); the covariance is sigma_row^2 (X^T X)^-1 with sigma_row the RMS
    residual (the rows carry no individual errors)."""
    P = np.asarray(P, dtype=float)
    X = np.asarray(X, dtype=float)
    beta = np.linalg.lstsq(X, P, rcond=None)[0]
    resid = P - X.dot(beta)
    dof = max(int(P.size - X.shape[1]), 1)
    s2 = float(resid.dot(resid)) / dof
    cov = s2 * np.linalg.pinv(X.T.dot(X))
    err = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    return beta, err, math.sqrt(s2), resid, dof


def design(f, t_h=None, drift_orders=(), regressors=()):
    """[1, f, drift terms in (t - <t>)^order, regressors...]."""
    cols = [np.ones(f.size), np.asarray(f, dtype=float)]
    if t_h is not None and drift_orders:
        tc = np.asarray(t_h, dtype=float) - float(np.mean(t_h))
        for order in drift_orders:
            cols.append(tc ** order)
    for z in regressors:
        cols.append(np.asarray(z, dtype=float))
    return np.column_stack(cols)


def fit_curve(P, F, Z):
    """OLS of P on [Z, F[:, j]] for every column j of F at once
    (Frisch-Waugh: P and F residualised against the fixed regressors Z,
    which include the constant). Returns (P_a, sigma_Pa) per column and
    the residual degrees of freedom."""
    P = np.asarray(P, dtype=float)
    Zp = np.linalg.pinv(Z)
    Pz = P - Z.dot(Zp.dot(P))
    Fz = F - Z.dot(Zp.dot(F))
    sff = np.sum(Fz * Fz, axis=0)
    sff = np.where(sff > 0.0, sff, np.finfo(float).tiny)
    b = np.sum(Fz * Pz[:, None], axis=0) / sff
    res = Pz[:, None] - b[None, :] * Fz
    dof = max(int(P.size - Z.shape[1] - 1), 1)
    s2 = np.sum(res * res, axis=0) / dof
    return b, np.sqrt(s2 / sff), dof


def clip_mask(resid, nmad):
    """Rows kept by one robust clip: |r - median(r)| <= nmad x 1.4826 x
    MAD(r) (spec Sec. 9.2). Returns (keep, mad_sigma, threshold)."""
    resid = np.asarray(resid, dtype=float)
    med = float(np.median(resid))
    mad_sigma = MAD_TO_SIGMA * float(np.median(np.abs(resid - med)))
    thr = nmad * mad_sigma
    keep = np.abs(resid - med) <= thr
    return keep, mad_sigma, thr


def one_sided_bound(P_a, sigma, t90):
    return max(float(P_a), 0.0) + float(t90) * float(sigma)


def _corr(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.size < 3 or np.std(a) == 0.0 or np.std(b) == 0.0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def running_mean_edge_aware(x, window):
    """Running mean over an odd window of rows; at the ends the window
    shrinks to the rows available (no padding, no wrap, no phase shift)."""
    x = np.asarray(x, dtype=float)
    n = x.size
    if n == 0:
        return x.copy()
    h = max(int(window), 1) // 2
    cs = np.concatenate([[0.0], np.cumsum(x)])
    i = np.arange(n)
    lo = np.clip(i - h, 0, n)
    hi = np.clip(i + h + 1, 0, n)
    return (cs[hi] - cs[lo]) / (hi - lo)


def running_median_edge_aware(x, window):
    """Running median over an odd window of rows, shrinking at the ends."""
    x = np.asarray(x, dtype=float)
    n = x.size
    h = max(int(window), 1) // 2
    return np.array([float(np.median(x[max(0, i - h):min(n, i + h + 1)]))
                     for i in range(n)])


def smooth_window_rows(t_h, span_h=FLOOR_SMOOTH_SPAN_H):
    """(odd window in rows, row cadence in s) spanning about span_h at the
    session's median row cadence, never below FLOOR_SMOOTH_MIN_ROWS nor
    above FLOOR_SMOOTH_MAX_FRAC of the rows (E2)."""
    t = np.sort(np.asarray(t_h, dtype=float))
    n = t.size
    d = np.diff(t)
    d = d[d > 0]
    cadence_s = float(np.median(d)) * 3600.0 if d.size else None
    if cadence_s:
        w = int(round(span_h * 3600.0 / cadence_s))
    else:
        w = FLOOR_SMOOTH_MIN_ROWS
    w = min(w, max(FLOOR_SMOOTH_MIN_ROWS, int(n * FLOOR_SMOOTH_MAX_FRAC)))
    w = max(w, FLOOR_SMOOTH_MIN_ROWS)
    w = min(w, n if n % 2 else max(n - 1, 1))
    if w % 2 == 0:
        w += 1
    return int(w), cadence_s


def smooth_in_time_order(x, t_h, window):
    """running_mean_edge_aware applied in time order, returned in row order."""
    x = np.asarray(x, dtype=float)
    order = np.argsort(np.asarray(t_h, dtype=float), kind="stable")
    y = np.empty_like(x)
    y[order] = running_mean_edge_aware(x[order], window)
    return y


def _residualise(a, Z):
    """a minus its least-squares projection on the columns of Z."""
    a = np.asarray(a, dtype=float)
    Z = np.asarray(Z, dtype=float)
    if Z.ndim == 1:
        Z = Z[:, None]
    return a - Z.dot(np.linalg.pinv(Z).dot(a))


def partial_corr(a, b, Z):
    """corr(a, b) after projecting both on the columns of Z (Z holding
    the constant); None when either residual has no variance."""
    return _corr(_residualise(a, Z), _residualise(b, Z))


def regressor_diagnostics(raw_z, sm_z, f, window_rows):
    """E2 diagnostics of one low-passed regressor against the template on
    the same rows: corr(raw, f), corr(smoothed, f), its single VIF, the
    reliability ratio var(smoothed)/var(raw), and the fraction of the
    smoothed regressor's variance that is resolved slow variation rather
    than the per-row noise surviving the running mean (white noise of
    variance var(raw) - var(smoothed) is reduced by the window)."""
    raw_z = np.asarray(raw_z, dtype=float)
    sm_z = np.asarray(sm_z, dtype=float)
    f = np.asarray(f, dtype=float)
    var_raw = float(np.var(raw_z))
    var_sm = float(np.var(sm_z))
    c_sm = _corr(sm_z, f)
    noise_raw = max(var_raw - var_sm, 0.0)
    noise_after = noise_raw / max(int(window_rows), 1)
    signal = max(var_sm - noise_after, 0.0)
    return {"corr_with_f_raw": _corr(raw_z, f), "corr_with_f_smoothed": c_sm,
            "vif_f_single": (float(min(1.0 / max(1.0 - c_sm ** 2, 1e-6), 1e6))
                             if c_sm is not None else None),
            "reliability_var_ratio": (var_sm / var_raw if var_raw > 0
                                      else None),
            "std_raw": float(math.sqrt(var_raw)),
            "std_smoothed": float(math.sqrt(var_sm)),
            "noise_std_after_smoothing": float(math.sqrt(noise_after)),
            "smoothed_signal_fraction": (signal / var_sm if var_sm > 0
                                         else None)}


def vif_of(f, regressors):
    """Variance-inflation factor of the template against the regressor
    set: 1 / (1 - R^2) of f regressed on [1, regressors...]; 1.0 with no
    regressors; capped at 1e6."""
    f = np.asarray(f, dtype=float)
    regs = [np.asarray(z, dtype=float) for z in regressors]
    if not regs or f.size < 3 or np.std(f) == 0.0:
        return 1.0
    Z = np.column_stack([np.ones(f.size)] + regs)
    r = _residualise(f, Z)
    sst = float(np.sum((f - f.mean()) ** 2))
    r2 = 1.0 - float(r.dot(r)) / sst if sst > 0 else 0.0
    return float(min(1.0 / max(1.0 - r2, 1e-6), 1e6))


# -------------------------------------------- centre-offset diagnostic
def centre_offset_block(rows, f0, fwhm_hz, disp_fraction, half_hz,
                        window_rows=CENTRE_OFFSET_SMOOTH_ROWS, df_hz=None):
    """noise.line_power_fixed_shape.centre_offset (referee P3, diagnostic
    only; the Sec. 9.2 estimator keeps the headline centre f0): the
    per-row fitted centres relative to f0 over the confident rows
    (center_used_for_alignment), their mean, rms and quartile means in
    row order, the largest |running median| over window_rows rows, and
    the fixed-shape estimator's own amplitude response to a noiseless
    line of the headline shape displaced by that offset (window, flat
    offset and dispersive term included; the pure-Lorentzian formula
    understates it). Pure function of per_row[] and the headline shape;
    the caller stores the result."""
    offs, conf, res_hz = [], [], []
    for pr in rows or []:
        fit = pr.get("fit") if isinstance(pr, dict) else None
        if not fit or fit.get("center_hz") is None:
            continue
        offs.append(float(fit["center_hz"]) - float(f0))
        conf.append(bool(pr.get("center_used_for_alignment")))
        if pr.get("resolution_hz"):
            res_hz.append(float(pr["resolution_hz"]))
    offs = np.asarray(offs, dtype=float)
    conf = np.asarray(conf, dtype=bool)
    out = {"n_rows": int(offs.size), "n_confident": int(conf.sum()),
           "mean_hz": None, "rms_hz": None, "quartile_mean_hz": None,
           "smoothing_window_rows": int(window_rows),
           "smoothed_offset_at_max_hz": None, "max_abs_smoothed_hz": None,
           "amplitude_factor_at_max": None, "P_bias_fraction_at_max": None,
           "lorentzian_formula_at_max": None,
           "basis": ("per-row fit.center_hz minus the headline centre f0 "
                     "(%.2f Hz) over the rows whose centre passed the "
                     "alignment confidence test; quartile means in row "
                     "order; max_abs_smoothed_hz is the largest |running "
                     "median over %d confident rows|; amplitude_factor_at_"
                     "max is fixed_shape_amplitude of a noiseless line of "
                     "the headline shape (FWHM %.2f Hz, dispersive fraction "
                     "%.3f) displaced by smoothed_offset_at_max_hz, over "
                     "the one at f0, within +/-%.0f Hz; P_bias_fraction_at_"
                     "max = factor - 1 bounds the fractional bias of the "
                     "fixed-centre P_k (and so of P_SN) at that offset; "
                     "lorentzian_formula_at_max is 1/(1 + (2 delta/FWHM)^2) "
                     "for comparison. Diagnostic only: the headline centre "
                     "stays at f0 per Sec. 9.2"
                     % (float(f0), int(window_rows), float(fwhm_hz),
                        float(disp_fraction), float(half_hz)))}
    if int(conf.sum()) < 2 or not fwhm_hz or fwhm_hz <= 0:
        return out
    c = offs[conf]
    out["mean_hz"] = float(c.mean())
    out["rms_hz"] = float(math.sqrt(float(np.mean(c * c))))
    out["quartile_mean_hz"] = [float(q.mean()) for q in np.array_split(c, 4)
                               if q.size]
    w = min(int(window_rows), int(c.size))
    if w % 2 == 0:
        w -= 1
    w = max(w, 1)
    sm = running_median_edge_aware(c, w)
    k = int(np.argmax(np.abs(sm)))
    delta = float(sm[k])
    out["smoothed_offset_at_max_hz"] = delta
    out["max_abs_smoothed_hz"] = abs(delta)
    df = float(df_hz) if df_hz else (float(np.median(res_hz)) if res_hz
                                      else 0.1)
    hw_ = float(fwhm_hz) / 2.0
    axis = float(f0) + np.arange(-(half_hz + 4.0 * fwhm_hz),
                                 half_hz + 4.0 * fwhm_hz, df)

    def line(shift):
        u = (axis - float(f0) - shift) / hw_
        L = 1.0 / (1.0 + u * u)
        return 1.0 + L * (1.0 + float(disp_fraction) * u)

    a0 = fixed_shape_amplitude(axis, line(0.0), f0, fwhm_hz, disp_fraction,
                               half_hz)
    a1 = fixed_shape_amplitude(axis, line(delta), f0, fwhm_hz, disp_fraction,
                               half_hz)
    if a0 and a1 is not None:
        out["amplitude_factor_at_max"] = float(a1 / a0)
        out["P_bias_fraction_at_max"] = float(a1 / a0 - 1.0)
    out["lorentzian_formula_at_max"] = float(1.0 / (1.0 + (delta / hw_) ** 2))
    return out


def _fit_summary(P, f, t90_of, t_h=None, drift_orders=(), regressors=(),
                 names=()):
    """One fit with its bound: dict of P_SN, P_a, sigma_Pa, sigma_row, dof,
    P90_mod, t90, the drift coefficients and regressor coefficients."""
    X = design(f, t_h, drift_orders, regressors)
    beta, err, s_row, resid, dof = ols(P, X)
    t90 = t90_of(dof)
    out = {"P_SN_counts2": float(beta[0]), "P_a_counts2": float(beta[1]),
           "sigma_Pa_counts2": float(err[1]), "sigma_row_counts2": s_row,
           "dof": int(dof), "n_params": int(X.shape[1]),
           "t90": float(t90),
           "P90_mod_counts2": one_sided_bound(beta[1], err[1], t90)}
    k = 2
    drift = []
    for order in drift_orders:
        drift.append({"order": int(order), "value_per_h": float(beta[k]),
                      "unit": "counts^2 per h^%d" % order
                      if order > 1 else "counts^2 per h"})
        k += 1
    out["drift_terms"] = drift
    regs = []
    for z, name in zip(regressors, names):
        regs.append({"name": name, "coefficient": float(beta[k]),
                     "coefficient_err": float(err[k]),
                     "corr_with_f": _corr(z, f)})
        k += 1
    out["regressors"] = regs
    out["_resid"] = resid
    return out


# ---------------------------------------------------------------- run
def run(inp):
    """The science.sidereal_modulation object (spec Sec. 9.5) from the
    inputs facility_report assembles (see sidereal_modulation_fit there):
      hw                 the halo_wind module
      P                  per-row line power (counts^2), rows with a fit
      floor              per-row baseline_psd_at_line
      psd_median_in_band per-row psd_median_in_band (optional; the second
                         floor regressor of Sec. 9.3 (c) when present)
      t_utc              per-row UTC datetimes
      theta_deg          per-row wind angle
      wind_precomputed   optional (E7): {"times": [...], "sw": the
                         halo_wind.session_wind dict, "lam_bins": {bin
                         centre deg: lam on grid_hz}} from the exclusion;
                         lam_bins seeds the template cache (bit-identical
                         to a fresh Monte Carlo), times/sw stand in for
                         t_utc/theta_deg only when those are absent and
                         the lengths match; everything works without it
      wind               the exclusion's halo.wind_model dict
      site               (lat, lon_east, azimuth_or_None)
      grid_hz, offsets_hz, nu_a_hz, gamma_per_s, nsamp, seed, c_omega2
      bin_deg            template_theta_bin_deg
      P90                the exclusion's P_90 (counts^2)
      g_wind_nominal, g_wind_conservative   the exclusion's curves
      offset_best_hz     result.offset_at_best_wind_nominal_hz
      g_wc_best, g_worst_best               result scalars
      m_a_ev, m_a_ev_mirror (or None)       the exclusion's mass axes
      t90_of             callable dof -> t_90(dof)
      row_clip_nmad, use_floor_regressor, estimator (name), per_row_field
      n_rows_total, n_rows_no_fit, alignment_gate_fired
      p_net, win_frac    the exclusion's P_net and window fraction
      sn1987a            SN1987A_GAP_GEV_INV
    Returns the object; private '_plot' data for the HTML figure is
    stripped by the report writer."""
    hw = inp["hw"]
    P = np.asarray(inp["P"], dtype=float)
    floor = np.asarray(inp["floor"], dtype=float)
    n = int(P.size)
    wp = inp.get("wind_precomputed") or {}
    if not isinstance(wp, dict):
        wp = {}
    if inp.get("theta_deg") is not None:
        theta = np.asarray(inp["theta_deg"], dtype=float)
    else:
        sw_pre = wp.get("sw") or {}
        theta = np.asarray(sw_pre.get("theta_deg", []), dtype=float)
    if inp.get("t_utc") is not None:
        t_utc = list(inp["t_utc"])
    else:
        t_utc = list(wp.get("times") or [])
    if theta.size != n or len(t_utc) != n:
        raise ValueError("sidereal_modulation.run: %d powers but %d angles "
                         "and %d times" % (n, theta.size, len(t_utc)))
    wind = inp["wind"]
    offsets = np.asarray(inp["offsets_hz"], dtype=float)
    t90_of = inp["t90_of"]
    nmad = float(inp.get("row_clip_nmad", ROW_CLIP_NMAD_DEFAULT))
    use_reg = bool(inp.get("use_floor_regressor", True))
    off_best = float(inp["offset_best_hz"])
    j_best = int(np.argmin(np.abs(offsets - off_best)))

    obj = {"available": False, "skip_reason": None, "label": LABEL,
           "estimator": {"name": inp.get("estimator", "fixed_shape"),
                         "per_row_field": inp.get("per_row_field"),
                         "basis": inp.get("estimator_basis")},
           "n_rows": int(inp.get("n_rows_total", n)),
           "rows_with_line_power": n,
           "rows_without_fit": int(inp.get("n_rows_no_fit", 0)),
           "rows_used": None, "rows_excluded": None,
           "rows_excluded_frac": None, "row_veto": None,
           "row_clip_nmad": nmad,
           "rows_timed_basis": wind.get("rows_timed_basis"),
           "tz_basis": wind.get("tz_basis"),
           "alignment_gate_fired": bool(inp.get("alignment_gate_fired"))}
    if n < 2:
        # spec 9.2: the skip vocabulary is fixed -- fewer than two usable
        # rows is the degenerate end of "fewer than 30 rows"; the count goes
        # into honesty, never into skip_reason
        obj["skip_reason"] = SKIP_NO_LEVERAGE
        obj["honesty"] = ["Sidereal-modulation fit not run: %s (%d row(s) "
                          "with a fitted line and a line power; the fit needs "
                          "at least %d rows and std(f) >= %.2f, and no "
                          "template is built below 2 rows). UNPUBLISHED."
                          % (SKIP_NO_LEVERAGE, n, MIN_ROWS, MIN_TEMPLATE_STD)]
        return obj

    # ---- templates on the scan grid, rows at their 2 degree bins
    bank = TemplateBank(hw, inp["grid_hz"], inp["nu_a_hz"],
                        inp["gamma_per_s"], offsets, wind["vlab_mean_kms"],
                        inp["bin_deg"], inp["nsamp"], inp["seed"],
                        inp["c_omega2"], lam_bins=wp.get("lam_bins"))
    xi2 = bank.xi2_rows(theta)                     # n x n_off
    xi2_mean = xi2.mean(axis=0)                    # <xi^2>_rows per offset
    # P5: f is normalised over the rows with a line power, the same row
    # set as the exclusion's xi2_actual (session_wind over all timed rows)
    # whenever rows_without_fit = 0, which keeps the Sec. 9.2 transduction
    # identity g_mod = g_wind_nominal sqrt(P90_mod / P90) exact. It is NOT
    # renormalised to the clipped rows (that would move the clipped Sec.
    # 9.6 targets and break the identity); if a future bundle has
    # rows_without_fit > 0, normalise over all timed rows, not the kept
    # ones. template_std and leverage are on this pre-clip set,
    # curve.template_std on curve.row_set.
    F = xi2 / xi2_mean[None, :]
    f = F[:, j_best]
    f_std = float(np.std(f))
    t0 = min(t_utc)
    t_h = np.array([(t - t0).total_seconds() / 3600.0 for t in t_utc])
    span_h = float(t_h.max() - t_h.min()) if n else 0.0

    # closed-form intuition over a sidereal day (printed, never used)
    lat, lon, az = inp["site"]
    day0 = t0.replace(minute=0, second=0, microsecond=0) \
        - datetime.timedelta(hours=DAY_CURVE_LEAD_H)
    h_day, th_day = hw.sidereal_angle_curve(lat, lon, day0, DAY_CURVE_N, az)
    vp_day = SHM_V0_KMS ** 2 + wind["vlab_mean_kms"] ** 2 \
        * np.sin(np.radians(th_day)) ** 2
    template = {"offset_hz": float(offsets[j_best]),
                "f_min": float(f.min()), "f_max": float(f.max()),
                "template_std": f_std, "leverage": None,
                "theta_min_deg": float(theta.min()),
                "theta_max_deg": float(theta.max()),
                "template_theta_bin_deg": float(inp["bin_deg"]),
                "closed_form_swing": float(vp_day.max() / vp_day.min()),
                "basis": ("f(t; Delta) = xi^2(theta(t), V; Delta) / "
                          "<xi^2>_rows: the exclusion's response integral "
                          "at the scan offset from the Monte-Carlo lineshape "
                          "at the row's wind-angle bin (%g degree bins, "
                          "every bin at the session-mean |v_lab| %.1f "
                          "km/s, %d samples, seed %d), normalised to the "
                          "mean over the %d rows with a line power; "
                          "closed_form_swing is max/min of v0^2 + V^2 "
                          "sin^2 theta over one sidereal day, the "
                          "intuition only"
                          % (inp["bin_deg"], wind["vlab_mean_kms"],
                             inp["nsamp"], inp["seed"], n))}
    obj["template"] = template
    if n < MIN_ROWS or f_std < MIN_TEMPLATE_STD:
        obj["skip_reason"] = SKIP_NO_LEVERAGE
        template["leverage"] = float(math.sqrt(n) * f_std)
        obj["honesty"] = [
            "Sidereal-modulation fit not run: %s (%d rows with a line power, "
            "template std(f) = %.4f at %+.0f Hz, theta %.1f-%.1f degrees; "
            "the fit needs at least %d rows and std(f) >= %.2f). UNPUBLISHED."
            % (SKIP_NO_LEVERAGE, n, f_std, offsets[j_best], theta.min(),
               theta.max(), MIN_ROWS, MIN_TEMPLATE_STD)]
        return obj

    # ---- the row set: one robust clip on the all-row raw two-parameter
    # residuals at the wind-aware best offset, fixed for every variant
    raw_all = _fit_summary(P, f, t90_of)
    keep, mad_sigma, thr = clip_mask(raw_all["_resid"], nmad)
    n_keep = int(np.count_nonzero(keep))
    n_excl = n - n_keep
    obj["rows_used"] = n_keep
    obj["rows_excluded"] = n_excl
    obj["rows_excluded_frac"] = float(n_excl) / n
    obj["row_veto"] = (
        "one robust clip on the all-row raw two-parameter residuals at "
        "%+.0f Hz: |r_k - median(r)| > %.0f x 1.4826 x MAD(r) = %.3g "
        "counts^2 (MAD sigma %.3g against an RMS of %.3g); rows without a "
        "line fit (%d) dropped before the fit; n_spikes is not a veto"
        % (offsets[j_best], nmad, thr, mad_sigma, raw_all["sigma_row_counts2"],
           obj["rows_without_fit"]))
    template["leverage"] = float(math.sqrt(n_keep) * f_std)

    # ---- floor normalisation and the regressors (Sec. 9.3 (a), (c); E2)
    floor_mean = float(np.mean(floor))
    Pn = P * floor_mean / floor
    win_rows, cadence_s = smooth_window_rows(t_h)
    reg_specs = []                 # (name, source, raw z, smoothed z): primary
    pm_spec = None                 # psd_median_in_band: recorded as variants
    reg_unavailable = []
    env_off = not use_reg
    if use_reg:
        raw = floor / floor_mean - 1.0
        reg_specs.append(("baseline_psd_at_line_smoothed",
                          "baseline_psd_at_line", raw,
                          smooth_in_time_order(raw, t_h, win_rows)))
        # the optional second floor regressor of Sec. 9.3 (c): the primary
        # design stays p = 3 (Sec. 9.2), so it is fitted and recorded as
        # variants (alone, and beside the baseline regressor) with the
        # same diagnostics, not put into the primary
        pmib = inp.get("psd_median_in_band")
        if pmib is not None and len(pmib) == n:
            try:
                pm = np.asarray([float(v) for v in pmib], dtype=float)
            except (TypeError, ValueError):
                pm = None
            if pm is not None and np.all(np.isfinite(pm)) and np.all(pm > 0):
                raw2 = pm / float(np.mean(pm)) - 1.0
                sm2 = smooth_in_time_order(raw2, t_h, win_rows)
                if float(np.std(sm2)) > 0.0:
                    pm_spec = ("psd_median_in_band_smoothed",
                               "psd_median_in_band", raw2, sm2)
                else:
                    reg_unavailable.append("psd_median_in_band (constant)")
            else:
                reg_unavailable.append("psd_median_in_band (non-finite or "
                                       "non-positive rows)")
        else:
            reg_unavailable.append("psd_median_in_band (not supplied)")
        dropped = [s[0] for s in reg_specs if float(np.std(s[3])) <= 0.0]
        reg_specs = [s for s in reg_specs if float(np.std(s[3])) > 0.0]
        reg_unavailable.extend("%s (constant)" % d for d in dropped)
    regs = [s[3] for s in reg_specs]
    names = [s[0] for s in reg_specs]
    use_reg = bool(regs)           # 'regressed' only when one is in the design
    drift_orders = (1, 2) if span_h > DRIFT_QUADRATIC_ABOVE_H else (1,)

    def sub(a):
        return a[keep]

    fit = _fit_summary(sub(Pn), sub(f), t90_of, regressors=[sub(z) for z in regs],
                       names=names)
    fit_drift = _fit_summary(sub(Pn), sub(f), t90_of, sub(t_h), drift_orders,
                             [sub(z) for z in regs], names)
    fit_drift1 = (fit_drift if drift_orders == (1,) else
                  _fit_summary(sub(Pn), sub(f), t90_of, sub(t_h), (1,),
                               [sub(z) for z in regs], names))
    fit_noreg = _fit_summary(sub(Pn), sub(f), t90_of)
    fit_all = _fit_summary(Pn, f, t90_of, regressors=regs, names=names)
    fit_raw = _fit_summary(sub(P), sub(f), t90_of)
    resid = fit["_resid"]
    rob = MAD_TO_SIGMA * float(np.median(np.abs(resid - np.median(resid))))
    chi2_dof = (float(resid.dot(resid)) / (fit["dof"] * rob * rob)
                if rob > 0 else None)

    # P2: the quadratic drift term's collinearity with the template over
    # this window (t^2 and f residualised on [1, t] over the kept rows)
    quad_pc = quad_vif = quad_sigma_ratio = None
    if len(drift_orders) > 1:
        Zlin = np.column_stack([np.ones(n_keep), sub(t_h)])
        quad_pc = partial_corr(sub(t_h) ** 2, sub(f), Zlin)
        if quad_pc is not None:
            quad_vif = float(min(1.0 / max(1.0 - quad_pc ** 2, 1e-6), 1e6))
        if fit_drift1["sigma_Pa_counts2"] > 0:
            quad_sigma_ratio = float(fit_drift["sigma_Pa_counts2"]
                                     / fit_drift1["sigma_Pa_counts2"])

    # E2: regressor diagnostics on the kept rows of the primary design
    vif_set = vif_of(sub(f), [sub(z) for z in regs])
    smoothing_desc = {"kind": "running mean, edge-aware, time order",
                      "window_rows": win_rows,
                      "window_h": (win_rows * cadence_s / 3600.0
                                   if cadence_s else None)}
    rc_regs = []
    for (name, source, raw_z, sm_z), rd in zip(reg_specs, fit["regressors"]):
        d = {"name": name, "source": source,
             "coefficient": rd["coefficient"],
             "coefficient_err": rd["coefficient_err"]}
        d.update(regressor_diagnostics(sub(raw_z), sub(sm_z), sub(f),
                                       win_rows))
        rc_regs.append(d)
        rd["vif_f"] = d["vif_f_single"]
        rd["smoothing"] = smoothing_desc
    collinear = bool(regs) and vif_set > VIF_COLLINEAR_WARN
    inseparable = bool(regs) and vif_set > VIF_COLLINEAR_FAIL
    unresolved = [r for r in rc_regs if r["smoothed_signal_fraction"] is not None
                  and r["smoothed_signal_fraction"] <= 0.0]
    noisy_partial = [r for r in rc_regs
                     if r["smoothed_signal_fraction"] is not None
                     and 0.0 < r["smoothed_signal_fraction"]
                     < SMOOTHED_SIGNAL_NOISY_BELOW]
    noisy = bool(unresolved or noisy_partial)
    sig_ratio = (fit["sigma_Pa_counts2"] / fit_noreg["sigma_Pa_counts2"]
                 if fit_noreg["sigma_Pa_counts2"] > 0 else None)

    # the psd_median_in_band variants (Sec. 9.3 (c) optional regressor)
    pm_diag = None
    variants = {}
    if pm_spec is not None:
        pm_diag = {"name": pm_spec[0], "source": pm_spec[1]}
        pm_diag.update(regressor_diagnostics(sub(pm_spec[2]), sub(pm_spec[3]),
                                             sub(f), win_rows))
        pm_diag["corr_with_primary_regressor"] = (
            _corr(sub(regs[0]), sub(pm_spec[3])) if regs else None)
        for key, specs in (("psd_median_only", [pm_spec]),
                           ("baseline_and_psd_median", reg_specs + [pm_spec])):
            zs = [s[3] for s in specs]
            fv = _fit_summary(sub(Pn), sub(f), t90_of,
                              regressors=[sub(z) for z in zs],
                              names=[s[0] for s in specs])
            variants[key] = {
                "P_a_counts2": fv["P_a_counts2"],
                "sigma_Pa_counts2": fv["sigma_Pa_counts2"],
                "P90_mod_counts2": fv["P90_mod_counts2"],
                "n_params": fv["n_params"], "regressors": fv["regressors"],
                "vif_f_regressor_set": vif_of(sub(f), [sub(z) for z in zs]),
                "sigma_Pa_ratio_vs_primary": (
                    fv["sigma_Pa_counts2"] / fit["sigma_Pa_counts2"]
                    if fit["sigma_Pa_counts2"] > 0 else None),
                "delta_P_a_vs_primary_counts2": (fv["P_a_counts2"]
                                                 - fit["P_a_counts2"])}
    both = variants.get("baseline_and_psd_median")
    regressor_check = {
        "n_regressors": len(regs),
        "smoothing": {"kind": ("running mean over an odd window of rows in "
                               "time order, the window shrinking at the "
                               "ends; target span %.1f h, never above %.0f%% "
                               "of the rows" % (FLOOR_SMOOTH_SPAN_H,
                                                100 * FLOOR_SMOOTH_MAX_FRAC)),
                      "window_rows": win_rows,
                      "window_h": (win_rows * cadence_s / 3600.0
                                   if cadence_s else None),
                      "row_cadence_s": cadence_s},
        "regressors": rc_regs,
        "psd_median_in_band": pm_diag,
        "variants": variants,
        "vif_f_regressor_set": float(vif_set),
        "P_a_with_counts2": fit["P_a_counts2"],
        "P_a_without_counts2": fit_noreg["P_a_counts2"],
        "delta_P_a_counts2": fit["P_a_counts2"] - fit_noreg["P_a_counts2"],
        "delta_P_a_over_sigma_without": (
            (fit["P_a_counts2"] - fit_noreg["P_a_counts2"])
            / fit_noreg["sigma_Pa_counts2"]
            if fit_noreg["sigma_Pa_counts2"] > 0 else None),
        "sigma_Pa_ratio_with_over_without": sig_ratio,
        "collinear_with_template": collinear,
        "inseparable_from_template": inseparable,
        "regressor_noisy": noisy,
        "regressors_unavailable": reg_unavailable,
        "env_switch_off": env_off,
        "basis": ("the primary design carries the low-passed baseline_psd_at_"
                  "line (p = 3, Sec. 9.2); psd_median_in_band, the optional "
                  "second floor regressor of Sec. 9.3 (c), is fitted as the "
                  "variants psd_median_only and baseline_and_psd_median and "
                  "recorded, not put into the primary. corr_with_f_raw/"
                  "smoothed: correlation of the raw and the low-passed "
                  "regressor with the template on the kept rows; vif_f_"
                  "single = 1/(1 - corr^2); vif_f_regressor_set = 1/(1 - "
                  "R^2) of f on [1, regressors]; reliability_var_ratio = "
                  "var(smoothed)/var(raw), the fraction of the raw "
                  "regressor's variance that survives the low pass (the "
                  "rest is per-row estimation noise, which would attenuate "
                  "the coefficient and leave the cycle in P_a); smoothed_"
                  "signal_fraction = 1 - (var(raw) - var(smoothed)) / "
                  "(window_rows x var(smoothed)), the part of the smoothed "
                  "regressor's variance that is resolved slow variation "
                  "rather than noise surviving the window (0: consistent "
                  "with noise alone); collinear_with_template when the VIF "
                  "exceeds %.0f (the 24 h cycle is then removed only at the "
                  "price of leverage, sigma(P_a) x sqrt(VIF)), inseparable_"
                  "from_template above %.0f (cycle and template not "
                  "separable within this session: a run from one template "
                  "extremum to the other with the cycle peaking at the "
                  "start), regressor_noisy when a smoothed_signal_fraction "
                  "is below %.2f"
                  % (VIF_COLLINEAR_WARN, VIF_COLLINEAR_FAIL,
                     SMOOTHED_SIGNAL_NOISY_BELOW))}

    primary = {
        "P_SN_counts2": fit["P_SN_counts2"], "P_a_counts2": fit["P_a_counts2"],
        "sigma_Pa_counts2": fit["sigma_Pa_counts2"],
        "sigma_row_counts2": fit["sigma_row_counts2"], "dof": fit["dof"],
        "chi2_dof": chi2_dof,
        "chi2_dof_basis": ("sum(r^2)/dof over (1.4826 MAD(r))^2 = (sigma_row "
                           "/ robust sigma)^2 on the primary fit's residuals; "
                           "a heavy-tail indicator, 1 for Gaussian rows, "
                           "above 1 for heavy tails, below 1 for smooth "
                           "unmodelled structure; NOT a goodness-of-fit: the "
                           "rows carry no per-row errors, so no chi^2 exists"),
        "sigma_row_robust_counts2": (float(rob) if rob > 0 else None),
        "drift_terms": fit_drift["drift_terms"],
        "P_a_with_drift_counts2": fit_drift["P_a_counts2"],
        "sigma_Pa_with_drift_counts2": fit_drift["sigma_Pa_counts2"],
        "P_a_with_linear_drift_counts2": fit_drift1["P_a_counts2"],
        "sigma_Pa_with_linear_drift_counts2": fit_drift1["sigma_Pa_counts2"],
        "linear_drift_per_h": fit_drift1["drift_terms"][0]["value_per_h"],
        "drift_quadratic_partial_corr_with_f": quad_pc,
        "drift_quadratic_vif_f": quad_vif,
        "drift_quadratic_sigma_ratio": quad_sigma_ratio,
        "drift_basis": ("P_a_with_drift is the Sec. 9.3 (b) variant (linear "
                        "term, plus a quadratic beyond %.0f h); P_a_with_"
                        "linear_drift the linear-only variant, the one the "
                        "honesty line quotes; drift_quadratic_partial_corr_"
                        "with_f is corr(t^2, f) after both are residualised "
                        "on [1, t] over the kept rows, drift_quadratic_vif_f "
                        "= 1/(1 - corr^2) and drift_quadratic_sigma_ratio = "
                        "sigma(P_a) with quadratic / with linear only; near "
                        "1 the quadratic variant is a degeneracy check, not "
                        "a systematic (null when only order 1 was fitted)"
                        % DRIFT_QUADRATIC_ABOVE_H),
        "floor_normalised": True,
        "regressors": fit["regressors"],
        "regressor_check": regressor_check,
        "P_a_without_regressors_counts2": fit_noreg["P_a_counts2"],
        "sigma_Pa_without_regressors_counts2": fit_noreg["sigma_Pa_counts2"],
        "P_a_with_psd_median_regressor_counts2": (both["P_a_counts2"]
                                                  if both else None),
        "sigma_Pa_with_psd_median_regressor_counts2": (
            both["sigma_Pa_counts2"] if both else None),
        "n_rows": n_keep, "n_params": fit["n_params"], "t90": fit["t90"],
        "P90_mod_counts2": fit["P90_mod_counts2"],
        "P90_mod_with_drift_counts2": fit_drift["P90_mod_counts2"]}
    obj["fit"] = primary

    def four(d, extra=None):
        o = {"P_a_counts2": d["P_a_counts2"],
             "sigma_Pa_counts2": d["sigma_Pa_counts2"],
             "sigma_row_counts2": d["sigma_row_counts2"],
             "P90_mod_counts2": d["P90_mod_counts2"],
             "n_rows": int(d["dof"] + d["n_params"]), "t90": d["t90"]}
        if extra:
            o.update(extra)
        return o
    obj["fit_allrows"] = four(fit_all, {"floor_normalised": True,
                                        "regressors": fit_all["regressors"]})
    obj["fit_raw"] = four(fit_raw, {"floor_normalised": False})
    obj["fit_allrows_raw"] = four(raw_all, {"floor_normalised": False})

    # ---- per-offset curve, clipped set, primary flavour
    Z = np.column_stack([np.ones(n_keep)] + [sub(z) for z in regs])
    Pa_c, sPa_c, dof_c = fit_curve(sub(Pn), F[keep], Z)
    t90_c = t90_of(dof_c)
    P90_c = np.maximum(Pa_c, 0.0) + t90_c * sPa_c
    P90 = float(inp["P90"])
    g_wn = np.asarray(inp["g_wind_nominal"], dtype=float)
    g_wc = np.asarray(inp["g_wind_conservative"], dtype=float)
    scale = np.sqrt(P90_c / P90)
    g_nom = g_wn * scale
    g_con = g_wc * scale
    j_min = int(np.argmin(g_nom))
    curve = {"nu_a_minus_nu_L_hz": offsets.tolist(),
             "m_a_ev": list(inp["m_a_ev"])}
    if inp.get("m_a_ev_mirror") is not None:
        curve["m_a_ev_mirror"] = list(inp["m_a_ev_mirror"])
    curve.update({"row_set": "clipped",
                  # C4: the design is self-describing (primary flavour)
                  "floor_normalised": True,
                  "n_params": int(Z.shape[1] + 1),
                  "regressors": list(names),
                  "flavour": "primary",
                  "template_std": np.std(F[keep], axis=0).tolist(),
                  "P_a_counts2": Pa_c.tolist(),
                  "sigma_Pa_counts2": sPa_c.tolist(),
                  "P90_mod_counts2": P90_c.tolist(),
                  "g90_nominal_mod": g_nom.tolist(),
                  "g90_conservative_mod": g_con.tolist()})
    obj["curve"] = curve

    # the raw two-parameter curves (the prototype's flavour) for the
    # Sec. 9.6 minima, clipped and all-row sets
    Z1 = np.ones((n_keep, 1))
    Pa_r, sPa_r, dof_r = fit_curve(sub(P), F[keep], Z1)
    P90_r = np.maximum(Pa_r, 0.0) + t90_of(dof_r) * sPa_r
    g_r = g_wn * np.sqrt(P90_r / P90)
    jr = int(np.argmin(g_r))
    Pa_ra, sPa_ra, dof_ra = fit_curve(P, F, np.ones((n, 1)))
    P90_ra = np.maximum(Pa_ra, 0.0) + t90_of(dof_ra) * sPa_ra
    g_ra = g_wn * np.sqrt(P90_ra / P90)
    jra = int(np.argmin(g_ra))

    # ---- bound at the exclusion's wind-aware best offset
    P90_mod = float(P90_c[j_best])
    g_nom_b = float(g_nom[j_best])
    g_con_b = float(g_con[j_best])
    bound = {"P90_mod_counts2": P90_mod, "t90": float(t90_c),
             "g90_nominal_mod_gev_inv": g_nom_b,
             "g90_conservative_mod_gev_inv": g_con_b,
             "offset_at_best_hz": float(offsets[j_best]),
             "curve_min": {"offset_hz": float(offsets[j_min]),
                           "g90_nominal_mod_gev_inv": float(g_nom[j_min]),
                           "g90_conservative_mod_gev_inv": float(g_con[j_min]),
                           "row_set": "clipped", "floor_normalised": True,
                           "n_params": int(Z.shape[1] + 1),
                           "regressors": list(names), "flavour": "primary"},
             "improvement_vs_line_power": {
                 "nominal": float(math.sqrt(P90 / P90_mod)) if P90_mod > 0
                 else None,
                 "conservative": float(inp["g_wc_best"] / g_con_b),
                 "vs_worst_parallel": float(inp["g_worst_best"] / g_con_b)},
             "curve_min_raw": {"row_set": "clipped",
                               "floor_normalised": False, "n_params": 2,
                               "regressors": [], "flavour": "raw_two_parameter",
                               "offset_hz": float(offsets[jr]),
                               "g90_nominal_mod_gev_inv": float(g_r[jr]),
                               "P90_mod_counts2": float(P90_r[jr])},
             "curve_min_raw_allrows": {"row_set": "all",
                                       "floor_normalised": False, "n_params": 2,
                                       "regressors": [],
                                       "flavour": "raw_two_parameter",
                                       "offset_hz": float(offsets[jra]),
                                       "g90_nominal_mod_gev_inv": float(
                                           g_ra[jra]),
                                       "P90_mod_counts2": float(P90_ra[jra])}}
    obj["bound"] = bound

    # ---- anti-phase check: the template 12 h later, same bins and offset
    t_anti = [t + datetime.timedelta(hours=ANTIPHASE_SHIFT_H) for t in t_utc]
    th_anti = np.array([hw.wind_angle_deg(lat, lon, t, az) for t in t_anti])
    xi2_anti = bank.xi2_rows(th_anti)[:, j_best]
    f_anti = xi2_anti / xi2_anti.mean()
    anti = _fit_summary(sub(Pn), sub(f_anti), t90_of,
                        regressors=[sub(z) for z in regs], names=names)
    obj["antiphase_check"] = {"P_a_counts2": anti["P_a_counts2"],
                              "sigma_counts2": anti["sigma_Pa_counts2"],
                              "template_std": float(np.std(f_anti)),
                              "shift_h": ANTIPHASE_SHIFT_H}
    # E2: the flag follows the design (a regressor is actually in it), the
    # basis states what was measured, and the caveats say when the
    # regressor cannot do its job within this session
    obj["solar_degeneracy"] = "regressed" if use_reg else "unbroken"
    # spec 7 (vi) / 9.3 (c): a regressor that cannot be separated from the
    # template (VIF above VIF_COLLINEAR_FAIL) records the degeneracy, it does
    # not break it -- the flag says so rather than claiming "regressed"
    _rc = (obj.get("fit") or {}).get("regressor_check") or {}
    if use_reg and _rc.get("inseparable_from_template"):
        obj["solar_degeneracy"] = "inseparable"
    if use_reg:
        parts = ["; ".join(
            "%s (from %s, running mean over %d rows%s): coefficient %+.3g "
            "+/- %.3g counts^2 per unit fractional change, corr(regressor, "
            "f) = %s raw / %s smoothed, reliability var(smoothed)/var(raw) "
            "= %s, resolved fraction of the smoothed regressor %s"
            % (r["name"], r["source"], win_rows,
               (" = %.2f h" % regressor_check["smoothing"]["window_h"])
               if regressor_check["smoothing"]["window_h"] else "",
               r["coefficient"], r["coefficient_err"],
               "%.3f" % r["corr_with_f_raw"] if r["corr_with_f_raw"] is not None
               else "n/a",
               "%.3f" % r["corr_with_f_smoothed"]
               if r["corr_with_f_smoothed"] is not None else "n/a",
               "%.3g" % r["reliability_var_ratio"]
               if r["reliability_var_ratio"] is not None else "n/a",
               "%.2f" % r["smoothed_signal_fraction"]
               if r["smoothed_signal_fraction"] is not None else "n/a")
            for r in rc_regs)]
        if pm_diag is not None and both:
            parts.append("psd_median_in_band (optional second regressor, "
                         "Sec. 9.3 (c)) recorded as variants, not in the "
                         "primary: corr(smoothed, f) = %s, resolved fraction "
                         "%s; beside the baseline regressor P_a = %+.1f +/- "
                         "%.1f (VIF %.2f, sigma x%.2f vs the primary), alone "
                         "%+.1f +/- %.1f"
                         % ("%.3f" % pm_diag["corr_with_f_smoothed"]
                            if pm_diag["corr_with_f_smoothed"] is not None
                            else "n/a",
                            "%.2f" % pm_diag["smoothed_signal_fraction"]
                            if pm_diag["smoothed_signal_fraction"] is not None
                            else "n/a",
                            both["P_a_counts2"], both["sigma_Pa_counts2"],
                            both["vif_f_regressor_set"],
                            both["sigma_Pa_ratio_vs_primary"] or float("nan"),
                            variants["psd_median_only"]["P_a_counts2"],
                            variants["psd_median_only"]["sigma_Pa_counts2"]))
        parts.append("VIF(f | regressors) = %.2f; P_a moved from %+.1f +/- "
                     "%.1f without the regressors to %+.1f +/- %.1f with "
                     "them (sigma x%.2f)"
                     % (vif_set, fit_noreg["P_a_counts2"],
                        fit_noreg["sigma_Pa_counts2"], fit["P_a_counts2"],
                        fit["sigma_Pa_counts2"], sig_ratio or float("nan")))
        if inseparable:
            parts.append("CAVEAT: the regressor and the sidereal template "
                         "are not separable over this window (VIF %.0f, "
                         "sigma x%.1f): a 24 h cycle peaking near the "
                         "session start follows the template's fall from "
                         "peak to trough, so P_a is unconstrained with the "
                         "regressor and biased without it; the degeneracy "
                         "is NOT broken within this session, only recorded"
                         % (vif_set, sig_ratio or float("nan")))
        elif collinear:
            parts.append("CAVEAT: the regressor is partially collinear with "
                         "the sidereal template over this window (VIF %.1f): "
                         "a 24 h cycle is removed at the price of leverage "
                         "(sigma x%.1f), not for free"
                         % (vif_set, sig_ratio or float("nan")))
        if unresolved:
            parts.append("CAVEAT: the smoothed regressor %s is consistent "
                         "with per-row estimation noise alone (no slow "
                         "floor variation resolved above %.2g%% rms over "
                         "%d-row windows): it acts as a random slow "
                         "regressor, so a laboratory cycle at that level "
                         "is untested by it and the degeneracy is NOT "
                         "broken within this session"
                         % (", ".join(r["name"] for r in unresolved),
                            100.0 * max(r["noise_std_after_smoothing"]
                                        for r in unresolved), win_rows))
        if noisy_partial:
            parts.append("CAVEAT: regressor noisy (only %s of the smoothed "
                         "regressor's variance is resolved slow variation, "
                         "below %.2f); the degeneracy is only partially "
                         "broken within this session"
                         % (", ".join("%.0f%%" % (100 * r["smoothed_signal_fraction"])
                                      for r in noisy_partial),
                            SMOOTHED_SIGNAL_NOISY_BELOW))
        obj["solar_degeneracy_basis"] = "floor regressor applied: " \
            + "; ".join(parts)
    else:
        obj["solar_degeneracy_basis"] = (
            "no regressor in the design (%s), so the degeneracy is not "
            "broken within this session"
            % ("regressors switched off" if env_off else
               "regressors unavailable: " + ", ".join(reg_unavailable)))

    # ---- estimator diagnostic (spec Sec. 9.2)
    p_net, win_frac = inp.get("p_net"), inp.get("win_frac")
    ratio = None
    if p_net and win_frac:
        ratio = float(np.mean(P) / (p_net / win_frac))
    obj["estimator_check"] = {
        "row_mean_Pk_counts2": float(np.mean(P)),
        "P_net_over_window_fraction_counts2": (float(p_net / win_frac)
                                               if p_net and win_frac else None),
        "ratio": ratio, "warn_above": ESTIMATOR_RATIO_WARN}

    # ---- the template over one sidereal day for the HTML (same bins)
    xi2_day = bank.xi2_rows(th_day)[:, j_best]
    f_day = xi2_day / xi2_mean[j_best]
    obj["_plot"] = {"h_day": h_day, "f_day": f_day, "day0": day0,
                    "t_h": t_h, "f": f, "P": Pn, "keep": keep,
                    "P_SN": fit["P_SN_counts2"], "P_a": fit["P_a_counts2"],
                    "sigma_Pa": fit["sigma_Pa_counts2"], "P90_mod": P90_mod,
                    "offset_hz": float(offsets[j_best]), "theta": theta,
                    "lead_h": DAY_CURVE_LEAD_H}
    obj["template_bins_evaluated"] = bank.n_bins_evaluated()
    obj["template_bins_seeded"] = int(bank.n_seeded)

    # ---- honesty (ends with UNPUBLISHED and the SN1987A ratio)
    est = inp.get("estimator", "fixed_shape")
    reg_txt = ("the low-passed per-row floor as regressor%s (%s; running "
               "mean over %d rows; VIF(f | regressors) = %.2f; without "
               "them P_a = %+.1f +/- %.1f)"
               % ("s" if len(regs) > 1 else "",
                  ", ".join("%s: coefficient %+.3g +/- %.3g counts^2 per "
                            "unit fractional change, corr(regressor, f) = %s"
                            % (r["source"], r["coefficient"],
                               r["coefficient_err"],
                               "%.3f" % r["corr_with_f_smoothed"]
                               if r["corr_with_f_smoothed"] is not None
                               else "n/a") for r in rc_regs),
                  win_rows, vif_set, fit_noreg["P_a_counts2"],
                  fit_noreg["sigma_Pa_counts2"])
               if use_reg else "no regressor")
    quad_txt = ""
    if len(drift_orders) > 1:
        quad_txt = ("; adding a quadratic term gives %+.1f +/- %.1f, but "
                    "over a %.1f h window t^2 is %s correlated with the "
                    "template after the linear term (sigma x%s), so that "
                    "variant is a degeneracy check, not a systematic"
                    % (fit_drift["P_a_counts2"], fit_drift["sigma_Pa_counts2"],
                       span_h,
                       "%.0f%%" % (100.0 * abs(quad_pc)) if quad_pc is not None
                       else "n/a",
                       "%.1f" % quad_sigma_ratio if quad_sigma_ratio
                       else "n/a"))
    honesty = [
        "Construction: the per-row %s line power P_k (%s), floor-normalised "
        "as P_k x <floor>/floor_k, fitted by ordinary least squares to P_SN "
        "+ P_a f(t_k; Delta) on the headline noise group with %s. The "
        "constant term absorbs the thermal spin-noise line and the receiver "
        "floor, which do not depend on sidereal time; only the fraction of "
        "a putative signal that follows the standard-halo wind angle at "
        "this site (theta %.1f-%.1f degrees over the session, template f "
        "%.3f-%.3f at %+.0f Hz) is tested, with no spin-noise subtraction. "
        "The transduction kappa*M0 and D_cal are the exclusion's: g_mod = "
        "g_wind x sqrt(P_90,mod / P_90), nominal at D_cal = 1 and "
        "conservative with D_cal as derated."
        % ("linear fixed-shape" if est == "fixed_shape"
           else "free five-parameter (DEBUG mode)", inp.get("per_row_field"),
           reg_txt, theta.min(), theta.max(), f.min(), f.max(),
           offsets[j_best]),
        "Leverage this session's hours gave: std(f) = %.3f over %d rows "
        "(sqrt(n) std(f) = %.1f; a full sidereal day gives 0.16-0.20 at the "
        "October lab speed): sigma(P_a) = %.1f counts^2 against a per-row "
        "scatter of %.1f. Result P_a = %+.1f +/- %.1f counts^2 (with a "
        "linear drift term %+.1f +/- %.1f, %+.2f counts^2/h%s), one-sided "
        "90%% bound P_90,mod = %.1f counts^2 "
        "with t_90(%d) = %.3f, against the entire-line-power P_90 = %.1f: a "
        "factor %.2f in coupling nominal-to-nominal, %.2f against the "
        "wind-aware conservative headline, %.2f against the v0.7 "
        "worst-parallel. Anti-phase template f(t + 12 h): P_a = %+.1f +/- "
        "%.1f."
        % (f_std, n_keep, template["leverage"], fit["sigma_Pa_counts2"],
           fit["sigma_row_counts2"], fit["P_a_counts2"],
           fit["sigma_Pa_counts2"], fit_drift1["P_a_counts2"],
           fit_drift1["sigma_Pa_counts2"],
           fit_drift1["drift_terms"][0]["value_per_h"], quad_txt,
           P90_mod, dof_c, t90_c, P90,
           bound["improvement_vs_line_power"]["nominal"] or float("nan"),
           bound["improvement_vs_line_power"]["conservative"],
           bound["improvement_vs_line_power"]["vs_worst_parallel"],
           anti["P_a_counts2"], anti["sigma_Pa_counts2"]),
        "Rows: %d of %d rows with a line power used; %d excluded "
        "(%.1f%%) by one robust clip at %.0f MAD on the all-row raw "
        "residuals (all-row raw fit P_a = %+.1f +/- %.1f, bound %.1f; "
        "clipped raw %+.1f +/- %.1f, bound %.1f); %d rows without a line "
        "fit dropped. The per-offset curve (clipped set) has its own "
        "minimum g_ap < %.3g GeV^-1 nominal at %+.0f Hz."
        % (n_keep, n, n_excl, 100.0 * n_excl / n, nmad,
           raw_all["P_a_counts2"], raw_all["sigma_Pa_counts2"],
           raw_all["P90_mod_counts2"], fit_raw["P_a_counts2"],
           fit_raw["sigma_Pa_counts2"], fit_raw["P90_mod_counts2"],
           obj["rows_without_fit"], g_nom[j_min], offsets[j_min]),
        "Solar-day degeneracy: %s --- within one session a 24 h laboratory "
        "cycle is degenerate with the sidereal template up to the template's "
        "fixed phase and harmonic content; %s. Across the network the "
        "sidereal phase differs with longitude while laboratory cycles are "
        "local-solar."
        % (obj["solar_degeneracy"], obj["solar_degeneracy_basis"]),
        "Residual check: fit.chi2_dof = %s is (sigma_row / robust sigma)^2 "
        "= (%.1f / %s)^2, a heavy-tail indicator (1 for Gaussian rows), not "
        "a goodness-of-fit: the rows carry no per-row errors."
        % ("%.2f" % chi2_dof if chi2_dof is not None else "n/a",
           fit["sigma_row_counts2"], "%.1f" % rob if rob > 0 else "n/a"),
        "Unlike the entire-line-power constructions, this bound DOES scale "
        "with measurement time: sigma(P_a) = sigma_row / (sqrt(n) std(f)), "
        "so P_90,mod falls as 1/sqrt(tau_m) at fixed leverage, and faster "
        "when added hours raise std(f).",
    ]
    if obj["alignment_gate_fired"]:
        honesty.append(
            "CAVEAT: the co-add alignment gate fired on this session, so the "
            "headline line shape behind P_k is the unaligned fit; under Sec. "
            "9.2 this session is a demonstration, not a regression target.")
    if ratio is not None:
        honesty.append(
            "Estimator check: the row mean of P_k is %+.1f counts^2 against "
            "the exclusion's P_net / window fraction %+.1f (ratio %.3f%s)."
            % (np.mean(P), p_net / win_frac, ratio,
               "; QA WARN above |ratio - 1| = %.2f" % ESTIMATOR_RATIO_WARN
               if abs(ratio - 1.0) > ESTIMATOR_RATIO_WARN else ""))
    if est != "fixed_shape":
        honesty.append(
            "DEBUG/REGRESSION MODE: SPINNOISE_SIDEREAL_ESTIMATOR=%s --- P_k "
            "is per_row[].integrated_power_counts2, the free five-parameter "
            "per-row fit, whose width rails at low SNR; the released "
            "estimator is the fixed-shape line power." % est)
    sn = float(inp["sn1987a"])
    honesty.append(
        "UNPUBLISHED --- %.1e times above the SN1987A cooling bound of %.1e "
        "GeV^-1 on the same coupling (conservative flavour, D_cal applied; "
        "%.1e times at D_cal = 1). None of this is a detection claim."
        % (g_con_b / sn, sn, g_nom_b / sn))
    obj["honesty"] = honesty
    obj["note"] = (
        "template.* and bound.* are quoted at the exclusion's wind-aware "
        "best offset result.offset_at_best_wind_nominal_hz; fit (primary) "
        "and fit_allrows are floor-normalised with the low-passed floor "
        "regressor(s) %s (solar_degeneracy '%s'; fit.regressor_check "
        "holds the measured collinearity and reliability), fit_raw and "
        "fit_allrows_raw are the raw two-parameter fits of the Sec. 9.6 "
        "prototype; curve and bound.curve_min are in the primary design "
        "on the clipped set (curve.floor_normalised, n_params = %d, "
        "regressors record it), bound.curve_min_raw / "
        "curve_min_raw_allrows the prototype's raw two-parameter minima; "
        "invariant: bound.P90_mod_counts2 = fit.P90_mod_counts2 = "
        "curve.P90_mod_counts2 at bound.offset_at_best_hz (same design, "
        "same rows); fit.chi2_dof is (sigma_row / robust sigma)^2 as "
        "fit.chi2_dof_basis states, a heavy-tail indicator, not a "
        "goodness-of-fit; drift_terms are fitted beside the primary (order "
        "2 only beyond %.0f h) and reported as P_a_with_drift, the "
        "linear-only variant as P_a_with_linear_drift, the one quoted in "
        "honesty; %s; the clip row set is fixed once from the all-row raw "
        "fit at the best offset and reused unchanged at every offset and "
        "for every variant."
        % (names if names else "none", obj["solar_degeneracy"],
           int(Z.shape[1] + 1), DRIFT_QUADRATIC_ABOVE_H,
           ("over this %.1f h window t^2 is %.3f correlated with the "
            "template after the linear term (VIF %.0f, sigma x%.1f), so "
            "the quadratic variant is a degeneracy check"
            % (span_h, quad_pc, quad_vif, quad_sigma_ratio or float("nan")))
           if quad_pc is not None else
           "no quadratic term was fitted (window %.1f h)" % span_h))
    obj["available"] = True
    return obj
