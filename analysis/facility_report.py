#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
facility_report.py -- the per-facility deliverable of the spin-noise network:
"your receiver's measured distance from the fundamental sensitivity ceiling".

    python3 analysis/facility_report.py <bundle.zip> [--out DIR]

Input : one bundle zip in the network format (expno tree per topspin/INSTALL.md,
        meta.json per schema/meta.schema.json v1.1).
Output: report.html (single self-contained file; figures inlined as base64
        PNG; light/dark friendly) and report.json (machine-readable numbers)
        in the output directory (default: <bundle_stem>_report next to the
        bundle).

Pipeline (adapted from the validated 2020 EPFL pilot analysis,
Spin_Noise_2020/extracted/spin_noise_pipeline.py; scipy replaced with
numpy-only equivalents so facility machines need nothing beyond numpy +
matplotlib):

  1. Bundle validation via the uploader's own selftest validator.
  2. Run-mode gate: simulate/desktest bundles get a clearly marked
     SOFTWARE-TEST report and NO science numbers (protects against test
     data masquerading as results). synthetic-injection bundles run the
     full science pipeline but are watermarked as validation, not data.
  3. Noise block: per-row Welch PSDs (Hann, 50% overlap, power co-added --
     never amplitude), spike replacement outside the protected line region,
     broad-Savitzky-Golay baseline normalization, absorptive+dispersive
     line fit per row, drift alignment, co-add, master refit. Both signs
     handled (RT Gueron dip / cryo emission bump). If no significant
     feature: calibrated upper limit on the feature amplitude.
  4. RG ladder: amplitude linearity across the ladder expnos (the 2020
     pilot's biggest untested systematic; surfaced prominently).
  5. References: A0 back-extrapolation, linewidth, open/close line-position
     stability, reference-tail floor vs gain-bridged noise floor.
  6. Headline numbers: spin-coupled floor fraction, temperature-contrast
     point (honest 'requires coil/preamp temperatures' when absent),
     distance from the fundamental ceiling with stated assumptions
     (2020 methodology: pairing factor, back-action).
  7. QA flags: sweep state, lock state, ADC clipping, spike counts,
     timestamp sanity, software provenance.

Spin-noise master formula (FDT-verified; see the project fact brief):
  S_V(Delta)/S_floor = 1 + f_c*lambda_r*[(Ts/Tc - 2)*lambda - lambda_r]
                           / (lambda_tot^2 + Delta^2),   f_c = Tc/(Tc+T_A).
Uniform temperature -> pure absorption DIP (Gueron dip); cold circuit ->
emission BUMP with larger contrast. RT probes absolutely measure spin noise
(McCoy & Ernst 1989; Gueron & Leroy 1989).

Authors: Blanchard, Ebadi, Claude (Anthropic).
Contact: John W. Blanchard <jwbquantum@gmail.com>.
"""

from __future__ import print_function

import argparse
import base64
import datetime
import io
import json
import math
import os
import re
import sys
import zipfile

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(REPO, "uploader"))
import upload_bundle  # noqa: E402  (the repo's own validator)

try:
    with open(os.path.join(REPO, "VERSION"), "r") as _fh:
        REPORT_VERSION = _fh.read().strip()
except Exception:
    REPORT_VERSION = "unknown"

CONTACT = "John W. Blanchard <jwbquantum@gmail.com>"

# ---------------------------------------------------------------- constants
# Kept in Hz (physics scales), converted to bins per bundle. Values mirror
# the validated 2020 pipeline where a direct analogue exists.
NPERSEG_MAX = 32768        # Welch segment cap (2020 value)
DROP_ROW = 512             # samples dropped at each noise-row start (filter transient)
EDGE_FRAC = 0.43           # analysis band |f| < EDGE_FRAC*SW (digital-filter rolloff outside)
LINE_SEARCH_HZ = 40.0      # per-row search half-window around the reference-derived guess
LINE_MASK_HZ = 100.0       # masked around the line for baseline/spike protection
FIT_HALF_HZ = 150.0        # fit window half-width
SPIKE_NSIGMA = 6.0         # spike threshold in robust sigmas of narrow-SG residual
SG_NARROW_HZ = 7.0         # narrow SG window (~half the expected linewidth)
SG_BROAD_HZ = 1500.0       # broad SG baseline window (~hundred linewidths)
COADD_HALF_HZ = 300.0      # co-added grid half-width
DETECT_NSIGMA = 5.0        # amplitude significance required to claim a feature
UL_CL = 1.645              # one-sided 95% CL multiplier for upper limits

# 2020-methodology systematic envelope (stated, never silently applied):
PAIRING_FACTOR_2020 = 1.4          # A0-vs-window pairing factor (absolute calibrations)
BACKACTION_RANGE_2020 = (2.7, 3.7)  # cold-circuit back-action suppression range
RG_POWER_ENVELOPE_UNTESTED = 0.20   # +/-20% in power if the RG ladder is absent

SOFTWARE_TEST_MODES = ("simulate", "desktest")


# ============================================================================
# Bruker readers (zip-resident)
# ============================================================================

def parse_jcamp(text):
    """Parse a Bruker JCAMP-DX parameter file into {name: value}.

    Scalars become float/int/str; <bracketed> strings are unwrapped;
    array blocks are stored as raw strings (not needed here).
    """
    out = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"##\$?([A-Za-z0-9_]+)=\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith("(") and ")" in val and not val.endswith(")"):
                # array header like (0..7); values on following lines
                buf = []
                i += 1
                while i < len(lines) and not lines[i].startswith("##"):
                    buf.append(lines[i].strip())
                    i += 1
                out[key] = " ".join(buf)
                continue
            if val.startswith("<") and val.endswith(">"):
                out[key] = val[1:-1]
            else:
                try:
                    fv = float(val)
                    out[key] = int(fv) if fv == int(fv) and "." not in val \
                        and "e" not in val.lower() else fv
                except ValueError:
                    out[key] = val
        i += 1
    return out


class Bundle(object):
    """Read-only access to the bundle zip contents."""

    def __init__(self, path):
        self.path = path
        self.zf = zipfile.ZipFile(path, "r")
        self.names = set(self.zf.namelist())
        self.meta = json.loads(self.zf.read("meta.json").decode("utf-8"))

    def has(self, name):
        return name in self.names

    def read(self, name):
        return self.zf.read(name)

    def acqus(self, expno):
        p = "data/%d/acqus" % expno
        if not self.has(p):
            return {}
        try:
            return parse_jcamp(self.read(p).decode("utf-8", "replace"))
        except Exception:
            return {}

    def read_rows(self, expno, exp_meta):
        """Return (rows, acq) where rows is an (n_rows, n_complex) complex
        array from data/<expno>/ser or fid, or (None, acq) if unreadable.
        """
        acq = self.acqus(expno)
        td = int(acq.get("TD", exp_meta.get("td", 0)) or 0)
        n_rows = int(exp_meta.get("td1_rows", 1) or 1)
        bytord = int(acq.get("BYTORDA", 0) or 0)
        dtypa = int(acq.get("DTYPA", 0) or 0)
        if dtypa == 2:
            dt = np.dtype("<f8" if bytord == 0 else ">f8")
        else:
            dt = np.dtype("<i4" if bytord == 0 else ">i4")
        raw = None
        is_ser = False
        for fn in ("ser", "fid"):
            p = "data/%d/%s" % (expno, fn)
            if self.has(p):
                raw = self.read(p)
                is_ser = (fn == "ser")
                break
        if raw is None or td < 4:
            return None, acq
        row_bytes = td * dt.itemsize
        padded = int(math.ceil(row_bytes / 1024.0)) * 1024
        rows = []
        if is_ser and n_rows > 1:
            stride = padded if len(raw) >= n_rows * padded else row_bytes
            for r in range(n_rows):
                chunk = raw[r * stride: r * stride + row_bytes]
                if len(chunk) < row_bytes:
                    break
                v = np.frombuffer(chunk, dtype=dt).astype(np.float64)
                rows.append(v[0::2] + 1j * v[1::2])
        else:
            v = np.frombuffer(raw[:row_bytes], dtype=dt).astype(np.float64)
            if v.size < td:
                v = np.frombuffer(raw, dtype=dt).astype(np.float64)
            rows.append(v[0::2] + 1j * v[1::2])
        if not rows:
            return None, acq
        return np.array(rows), acq

    def raw_int_stats(self, expno):
        """Max |value| and full-scale fraction for the ADC-clipping check."""
        acq = self.acqus(expno)
        bytord = int(acq.get("BYTORDA", 0) or 0)
        dtypa = int(acq.get("DTYPA", 0) or 0)
        for fn in ("ser", "fid"):
            p = "data/%d/%s" % (expno, fn)
            if self.has(p):
                raw = self.read(p)
                if dtypa == 2:
                    v = np.frombuffer(raw[: (len(raw) // 8) * 8],
                                      dtype="<f8" if bytord == 0 else ">f8")
                    return {"max_abs": float(np.max(np.abs(v))) if v.size else 0.0,
                            "fullscale_fraction": None, "dtype": "float64"}
                v = np.frombuffer(raw[: (len(raw) // 4) * 4],
                                  dtype="<i4" if bytord == 0 else ">i4")
                if not v.size:
                    return None
                mx = float(np.max(np.abs(v.astype(np.float64))))
                return {"max_abs": mx, "fullscale_fraction": mx / 2147483647.0,
                        "dtype": "int32"}
        return None


# ============================================================================
# numpy-only DSP (scipy-free equivalents of the 2020 pipeline stages)
# ============================================================================

def savgol(y, window, order):
    """Savitzky-Golay smoothing via convolution (reflect-padded edges)."""
    window = int(window)
    if window % 2 == 0:
        window += 1
    window = max(window, order + 2 if (order + 2) % 2 == 1 else order + 3)
    if window >= y.size:
        window = (y.size // 2) * 2 - 1
    half = window // 2
    x = np.arange(-half, half + 1, dtype=np.float64)
    A = np.vander(x, order + 1, increasing=True)
    coeffs = np.linalg.pinv(A)[0]          # evaluates the LSQ polynomial at 0
    ypad = np.concatenate([y[half:0:-1], y, y[-2:-half - 2:-1]])
    return np.convolve(ypad, coeffs[::-1], mode="valid")


def welch_psd(x, fs, nperseg):
    """Two-sided Welch PSD of complex data (Hann, 50% overlap, constant
    detrend, density scaling), fftshifted. Matches scipy.signal.welch."""
    nperseg = int(min(nperseg, x.size))
    step = nperseg // 2
    n = np.arange(nperseg)
    win = 0.5 - 0.5 * np.cos(2.0 * np.pi * n / nperseg)   # periodic Hann
    scale = 1.0 / (fs * np.sum(win * win))
    nseg = (x.size - nperseg) // step + 1
    acc = np.zeros(nperseg)
    for i in range(nseg):
        seg = x[i * step: i * step + nperseg]
        seg = seg - np.mean(seg)
        X = np.fft.fft(seg * win)
        acc += (X * np.conj(X)).real
    p = acc * scale / max(nseg, 1)
    f = np.fft.fftfreq(nperseg, d=1.0 / fs)
    return np.fft.fftshift(f), np.fft.fftshift(p), nseg


def robust_sigma(x):
    return 1.4826 * np.median(np.abs(x - np.median(x)))


def lineshape(f, a, b, f0, w, c):
    """Lorentzian absorption (a) + dispersive (b) + flat offset c; w = FWHM."""
    u = (f - f0) / (w / 2.0)
    return c + (a + b * u) / (1.0 + u ** 2)


def _linear_abc(fm, pm, f0, w):
    """For fixed (f0, w) the model is linear in (a, b, c): solve exactly."""
    u = (fm - f0) / (w / 2.0)
    L = 1.0 / (1.0 + u ** 2)
    M = np.column_stack([L, u * L, np.ones_like(fm)])
    coef, res, _, _ = np.linalg.lstsq(M, pm, rcond=None)
    model = M.dot(coef)
    ssr = float(np.sum((pm - model) ** 2))
    return coef, ssr


def fit_line(f, pnorm, f0_guess, search_hz=LINE_SEARCH_HZ,
             w_lo=1.0, w_hi=80.0, allow_dip=True):
    """Absorptive+dispersive line fit by coarse-to-fine grid over (f0, w)
    with exact linear solves for (a, b, c). Returns (popt, perr, ssr, m).

    Handles both signs: the linear solve places no sign constraint on a.
    """
    m = np.abs(f - f0_guess) < FIT_HALF_HZ
    fm, pm = f[m], pnorm[m]
    if fm.size < 30:
        raise ValueError("fit window too small")
    df = f[1] - f[0]
    f0s = np.arange(f0_guess - search_hz, f0_guess + search_hz + df, max(df, 0.25))
    ws = np.geomspace(w_lo, w_hi, 40)
    best = (None, np.inf)
    for f0 in f0s:
        for w in ws:
            coef, ssr = _linear_abc(fm, pm, f0, w)
            if ssr < best[1]:
                best = ((coef, f0, w), ssr)
    (coef, f0, w), ssr = best
    # two refinement passes
    for span_f, span_w, nf, nw in ((2.0, 1.6, 21, 21), (0.4, 1.12, 21, 21)):
        f0s = np.linspace(f0 - span_f, f0 + span_f, nf)
        ws = np.geomspace(max(w / span_w, w_lo), min(w * span_w, w_hi * 1.5), nw)
        best = (None, np.inf)
        for f0c in f0s:
            for wc in ws:
                c2, s2 = _linear_abc(fm, pm, f0c, wc)
                if s2 < best[1]:
                    best = ((c2, f0c, wc), s2)
        (coef, f0, w), ssr = best
    a, b, c = [float(v) for v in coef]
    popt = np.array([a, b, f0, w, c])
    # parameter covariance from the numerical Jacobian, curve_fit-style
    # (cov scaled by reduced chi^2, i.e. sigma estimated from residuals)
    J = np.empty((fm.size, 5))
    eps = [1e-6, 1e-6, max(df, 0.05) * 0.1, max(w * 1e-3, 1e-3), 1e-6]
    for k in range(5):
        pp = popt.copy()
        pm_ = popt.copy()
        pp[k] += eps[k]
        pm_[k] -= eps[k]
        J[:, k] = (lineshape(fm, *pp) - lineshape(fm, *pm_)) / (2 * eps[k])
    dof = max(fm.size - 5, 1)
    s2 = ssr / dof
    try:
        cov = s2 * np.linalg.pinv(J.T.dot(J))
        perr = np.sqrt(np.clip(np.diag(cov), 0, None))
    except Exception:
        perr = np.full(5, np.nan)
    return popt, perr, ssr, int(fm.size)


def matched_filter_npe(f, pnorm, w, f0_line, edge_hz, exclude_dc_hz=50.0):
    """CASPEr-style normalized power excess: convolve (pnorm-1) with a
    unit-power Lorentzian kernel, normalize by the off-line std."""
    df = f[1] - f[0]
    half = int(np.ceil(5 * w / df))
    fk = np.arange(-half, half + 1) * df
    kern = 1.0 / (1.0 + (fk / (w / 2.0)) ** 2)
    kern /= np.sqrt(np.sum(kern ** 2))
    y = np.convolve(pnorm - 1.0, kern[::-1], mode="same")
    inwin = (np.abs(f) < edge_hz) & (np.abs(f) > exclude_dc_hz)
    offline = inwin & (np.abs(f - f0_line) > 300.0)
    npe = (y - np.mean(y[offline])) / np.std(y[offline])
    return npe, offline


# ============================================================================
# Analysis stages
# ============================================================================

def pick_nperseg(n):
    """Largest power of two <= n/4, capped at the 2020 value."""
    if n < 4096:
        return max(256, 2 ** int(math.floor(math.log(max(n, 2), 2))) // 2)
    return int(min(NPERSEG_MAX, 2 ** int(math.floor(math.log(n / 4.0, 2)))))


def analyze_reference_row(x, fs, grpdly):
    """One pulsed small-flip row: A0 back-extrapolation + amplitude-spectrum
    lineshape fit (same model as the noise line: apples-to-apples width)."""
    out = {}
    g = int(round(grpdly)) if grpdly and grpdly > 0 else 68
    t = (np.arange(x.size) - g) / fs
    env = np.abs(x)
    # earliest clean decay rate (8-20 ms) and direct A0 back-extrapolation
    m_r = (t > 0.008) & (t < 0.020) & (env > 0)
    m0 = (t > 0.008) & (t < 0.014)
    if m_r.sum() > 8 and m0.sum() > 3:
        cr = np.polyfit(t[m_r], np.log(env[m_r]), 1)
        r_early = float(-cr[0])
        out["early_decay_rate_per_s"] = r_early
        out["A0_counts"] = float(np.median(env[m0]) * math.exp(r_early * 0.011))
    else:
        out["A0_counts"] = float(np.max(env)) if env.size else 0.0
        out["early_decay_rate_per_s"] = None
    # spectrum from the true FID start
    start = g + 12
    n_after = x.size - start
    nfft = 2 ** int(math.floor(math.log(max(n_after, 256), 2)))
    seg = x[start:start + nfft]
    seg = seg - np.mean(seg[nfft // 2:])
    spec = np.fft.fftshift(np.fft.fft(seg))
    fax = np.fft.fftshift(np.fft.fftfreq(nfft, d=1.0 / fs))
    amp = np.abs(spec)
    i0 = int(np.argmax(amp))
    f0_pk = float(fax[i0])
    try:
        popt, perr, ssr, npts = fit_line(fax, amp, f0_pk, search_hz=10.0,
                                         w_lo=0.5, w_hi=120.0)
        out["line_center_hz"] = float(popt[2])
        out["fwhm_amp_hz"] = float(abs(popt[3]))
        out["fwhm_amp_err_hz"] = float(perr[3])
        out["amp_b_over_a"] = float(popt[1] / popt[0]) if popt[0] else None
    except Exception as exc:
        out["line_center_hz"] = f0_pk
        out["fwhm_amp_hz"] = None
        out["fit_error"] = str(exc)
    # tail floor (counts^2/Hz) from the last 30% of the row -- valid once the
    # signal has decayed; flagged as approximate in the report
    tail = x[int(x.size * 0.7):]
    if tail.size >= 2048:
        ftl, ptl, _ = welch_psd(tail, fs, pick_nperseg(tail.size))
        band = np.abs(ftl) < EDGE_FRAC * fs
        out["tail_floor_counts2perhz"] = float(np.median(ptl[band]))
    else:
        out["tail_floor_counts2perhz"] = None
    return out


def analyze_reference_exp(bundle, exp, fs_default):
    rows, acq = bundle.read_rows(exp["expno"], exp)
    if rows is None:
        return {"expno": exp["expno"], "role": exp["role"], "readable": False}
    fs = float(acq.get("SW_h", exp.get("sw_hz", fs_default)))
    grpdly = float(acq.get("GRPDLY", 0) or 0)
    per_row = [analyze_reference_row(r, fs, grpdly) for r in rows]
    ok = [r for r in per_row if r.get("fwhm_amp_hz")]
    res = {"expno": exp["expno"], "role": exp["role"], "readable": True,
           "n_rows": len(rows), "fs_hz": fs, "grpdly": grpdly,
           "rg": float(exp.get("rg", acq.get("RG", 0)) or 0),
           "per_row": per_row}
    if ok:
        res["line_center_hz"] = float(np.mean([r["line_center_hz"] for r in ok]))
        res["line_center_std_hz"] = float(np.std([r["line_center_hz"] for r in ok]))
        res["fwhm_amp_hz"] = float(np.mean([r["fwhm_amp_hz"] for r in ok]))
        res["A0_counts"] = float(np.mean([r["A0_counts"] for r in per_row]))
        tails = [r["tail_floor_counts2perhz"] for r in per_row
                 if r.get("tail_floor_counts2perhz")]
        res["tail_floor_counts2perhz"] = float(np.mean(tails)) if tails else None
    return res


def analyze_noise_row(x, fs, f0_guess, edge_hz):
    """2020 stages 1-4 on one noise row."""
    nps = pick_nperseg(x.size - DROP_ROW)
    f, p, nseg = welch_psd(x[DROP_ROW:], fs, nps)
    df = f[1] - f[0]
    line_mask = np.abs(f - f0_guess) < LINE_MASK_HZ
    # spike replacement guided by narrow-SG residuals (line protected)
    nb = max(5, int(round(SG_NARROW_HZ / df)) | 1)
    smooth = savgol(p, nb, 2)
    resid = p - smooth
    sig = robust_sigma(resid)
    spikes = (np.abs(resid) > SPIKE_NSIGMA * sig) & (~line_mask)
    pc = p.copy()
    pc[spikes] = smooth[spikes]
    # broad-SG baseline with the line bridged, then divide
    pb = pc.copy()
    if line_mask.any():
        pb[line_mask] = np.interp(f[line_mask], f[~line_mask], pc[~line_mask])
    bb = min(int(round(SG_BROAD_HZ / df)) | 1, (pb.size // 3) * 2 - 1)
    base = savgol(pb, bb, 3)
    pnorm = pc / base
    return {"f": f, "pnorm": pnorm, "base": base, "psd": p, "nseg": nseg,
            "n_spikes": int(np.count_nonzero(spikes)), "df": df,
            "nperseg": nps}


def analyze_noise_block(bundle, exp, f0_guess, fs_default):
    rows, acq = bundle.read_rows(exp["expno"], exp)
    if rows is None:
        return None
    fs = float(acq.get("SW_h", exp.get("sw_hz", fs_default)))
    edge_hz = EDGE_FRAC * fs
    out = {"expno": exp["expno"], "fs_hz": fs, "n_rows": int(rows.shape[0]),
           "rg": float(exp.get("rg", acq.get("RG", 0)) or 0),
           "edge_hz": edge_hz, "per_row": [], "_rows": []}
    for x in rows:
        r = analyze_noise_row(x, fs, f0_guess, edge_hz)
        row = {"nseg": r["nseg"], "n_spikes": r["n_spikes"],
               "resolution_hz": r["df"], "nperseg": r["nperseg"]}
        try:
            popt, perr, ssr, npts = fit_line(r["f"], r["pnorm"], f0_guess)
            a, b, f0, w, c = [float(v) for v in popt]
            row["fit"] = {"amp_norm": a, "amp_err": float(perr[0]),
                          "disp_norm": b, "disp_err": float(perr[1]),
                          "center_hz": f0, "center_err_hz": float(perr[2]),
                          "fwhm_hz": w, "fwhm_err_hz": float(perr[3]),
                          "offset": c}
            row["asymmetry_b_over_a"] = b / a if a else None
            base_at_line = float(np.median(
                r["base"][np.abs(r["f"] - f0) < 50.0]))
            row["baseline_psd_at_line"] = base_at_line
            row["integrated_power_counts2"] = a * math.pi * w / 2.0 * base_at_line
            npe, offline = matched_filter_npe(r["f"], r["pnorm"], w, f0, edge_hz)
            i_line = int(np.argmin(np.abs(r["f"] - f0)))
            row["npe_at_line"] = float(npe[i_line])
            row["offline_norm_var"] = float(
                np.var(r["pnorm"][offline & (np.abs(r["f"]) < edge_hz)]))
        except Exception as exc:
            row["fit_error"] = str(exc)
        out["per_row"].append(row)
        out["_rows"].append(r)
    # drift-aligned power co-add of the normalized PSDs.
    # Alignment uses only CONFIDENT per-row centers (>=3 sigma amplitude of
    # the majority sign, center error < FWHM/2): for a weak feature, aligning
    # every row on its own noisy center smears the co-added line and biases
    # the recovered amplitude low (injection-verified failure mode). Rows
    # without a confident center get the confident rows' weighted mean shift.
    fitted = [(rr, pr) for rr, pr in zip(out["_rows"], out["per_row"])
              if "fit" in pr]
    if fitted:
        amps = np.array([pr["fit"]["amp_norm"] for _, pr in fitted])
        errs = np.array([max(pr["fit"]["amp_err"], 1e-12) for _, pr in fitted])
        maj_sign = 1.0 if np.sum(amps / errs ** 2) >= 0 else -1.0
        conf = []
        for rr, pr in fitted:
            ft = pr["fit"]
            good = (np.sign(ft["amp_norm"]) == maj_sign
                    and abs(ft["amp_norm"]) >= 3.0 * ft["amp_err"]
                    and ft["center_err_hz"] < ft["fwhm_hz"] / 2.0)
            pr["center_used_for_alignment"] = bool(good)
            conf.append(good)
        if any(conf):
            cc = np.array([pr["fit"]["center_hz"] for (_, pr), g
                           in zip(fitted, conf) if g])
            ce = np.array([max(pr["fit"]["center_err_hz"], 1e-6)
                           for (_, pr), g in zip(fitted, conf) if g])
            mean_shift = float(np.sum(cc / ce ** 2) / np.sum(1.0 / ce ** 2))
        else:
            mean_shift = f0_guess
        out["coadd_n_rows_self_aligned"] = int(np.count_nonzero(conf))
        df = fitted[0][0]["df"]
        grid = np.arange(-COADD_HALF_HZ, COADD_HALF_HZ + df / 2, df)
        acc = np.zeros_like(grid)
        for (rr, pr), g in zip(fitted, conf):
            shift = pr["fit"]["center_hz"] if g else mean_shift
            acc += np.interp(grid, rr["f"] - shift, rr["pnorm"])
        avg = acc / len(fitted)
        out["_coadd"] = {"grid": grid, "avg": avg}
        popt, perr, ssr, npts = fit_line(grid, avg, 0.0, search_hz=15.0)
        a, b, f0, w, c = [float(v) for v in popt]
        out["coadd_fit"] = {
            "amp_norm": a, "amp_err": float(perr[0]),
            "disp_norm": b, "disp_err": float(perr[1]),
            "center_shift_hz": f0, "fwhm_hz": w, "fwhm_err_hz": float(perr[3]),
            "offset": c, "asymmetry_b_over_a": (b / a if a else None),
            "asymmetry_err": (abs(b / a) * math.sqrt(
                (perr[0] / a) ** 2 + (perr[1] / b) ** 2)
                if a and b else None),
            "n_rows_coadded": len(fitted),
        }
    return out


def upper_limit_at(f, pnorm, f0_ref, w_candidates):
    """Calibrated 95% upper limit on |feature amplitude| (normalized units)
    at a fixed line position, profiled over candidate widths."""
    best = 0.0
    details = []
    m = np.abs(f - f0_ref) < FIT_HALF_HZ
    fm, pm = f[m], pnorm[m]
    for w in w_candidates:
        coef, ssr = _linear_abc(fm, pm, f0_ref, w)
        a = float(coef[0])
        u = (fm - f0_ref) / (w / 2.0)
        L = 1.0 / (1.0 + u ** 2)
        M = np.column_stack([L, u * L, np.ones_like(fm)])
        s2 = ssr / max(fm.size - 3, 1)
        cov = s2 * np.linalg.pinv(M.T.dot(M))
        sa = math.sqrt(max(cov[0, 0], 0))
        ul = abs(a) + UL_CL * sa
        details.append({"fwhm_hz": float(w), "amp": a, "amp_err": sa,
                        "ul95": ul})
        best = max(best, ul)
    return best, details


def analyze_rg_ladder(bundle, meta, f0_guess, fs_default):
    """Amplitude linearity across the RG ladder (2020's untested link)."""
    ladder_meta = meta.get("calibration", {}).get("rg_ladder", [])
    exps = {e["expno"]: e for e in meta.get("experiments", [])
            if e.get("role") == "rg_ladder"}
    rungs = []
    for entry in ladder_meta:
        expno = entry.get("expno")
        if expno not in exps:
            continue
        rows, acq = bundle.read_rows(expno, exps[expno])
        if rows is None:
            continue
        fs = float(acq.get("SW_h", exps[expno].get("sw_hz", fs_default)))
        g = int(round(float(acq.get("GRPDLY", 68) or 68)))
        x = rows[0]
        start = g + 12
        nfft = 2 ** int(math.floor(math.log(max(x.size - start, 256), 2)))
        seg = x[start:start + nfft]
        spec = np.abs(np.fft.fftshift(np.fft.fft(seg - np.mean(seg))))
        fax = np.fft.fftshift(np.fft.fftfreq(nfft, d=1.0 / fs))
        near = np.abs(fax - f0_guess) < 300.0
        if not near.any():
            near = np.abs(fax) < EDGE_FRAC * fs
        off = (np.abs(fax) < EDGE_FRAC * fs) & (np.abs(fax - f0_guess) > 400.0)
        amp = float(np.max(spec[near]) - np.median(spec[off]))
        rungs.append({"expno": expno, "rg": float(entry.get("rg", 0)),
                      "amplitude_counts": amp})
    if len(rungs) < 2:
        return {"available": False, "n_rungs_with_data": len(rungs),
                "rungs": rungs,
                "note": "fewer than 2 ladder acquisitions with readable data; "
                        "receiver-gain linearity UNTESTED for this bundle "
                        "(the 2020 pilot's largest unverified systematic)"}
    rg = np.array([r["rg"] for r in rungs])
    am = np.array([r["amplitude_counts"] for r in rungs])
    s = float(np.sum(am * rg) / np.sum(rg * rg))     # best line through origin
    dev = am / (s * rg) - 1.0
    for r, d in zip(rungs, dev):
        r["fractional_deviation"] = float(d)
    return {"available": True, "rungs": rungs,
            "slope_counts_per_rg": s,
            "max_abs_fractional_deviation": float(np.max(np.abs(dev))),
            "max_abs_power_deviation": float(2.0 * np.max(np.abs(dev))),
            "note": "amplitude vs RG fitted through the origin; deviation is "
                    "per-rung amplitude / (slope*RG) - 1"}


# ============================================================================
# Physics interpretation (master formula; every assumption stated)
# ============================================================================

def temperature_contrast_point(meta, coadd, headline_notes):
    """Master-formula inversion where the bundle declares temperatures.

    S_V(D)/S_floor = 1 + f_c*l_r*[(Ts/Tc-2)*l - l_r]/(l_tot^2+D^2),
    f_c = Tc/(Tc+T_A). With measured contrast a and l_tot = pi*FWHM,
    solve (g+1)*l_r^2 - g*l_tot*l_r + a*l_tot^2/f_c = 0, g = Ts/Tc - 2.
    """
    spec = meta.get("spectrometer", {})
    samp = meta.get("sample", {})
    tc = spec.get("coil_temp_k")
    ta = spec.get("preamp_temp_k")
    ts = samp.get("vt_setpoint_k")
    probe = spec.get("probe_type", "unknown")
    out = {"probe_type": probe, "coil_temp_k": tc, "preamp_temp_k": ta,
           "sample_temp_k": ts}
    missing = [n for n, v in (("coil_temp_k", tc), ("preamp_temp_k", ta),
                              ("vt_setpoint_k", ts)) if v is None]
    if missing:
        out["status"] = ("requires coil/preamp temperatures: bundle does not "
                         "declare %s; the temperature-contrast point cannot "
                         "be computed" % ", ".join(missing))
        headline_notes.append(out["status"])
        return out
    if coadd is None:
        out["status"] = "no fitted feature to invert"
        return out
    a = coadd["amp_norm"]
    l_tot = math.pi * coadd["fwhm_hz"]
    f_c = tc / (tc + ta)
    g = ts / tc - 2.0
    out.update({"f_c": f_c, "g_TsOverTc_minus2": g, "lambda_tot_per_s": l_tot})
    # maximum achievable contrast at this (Ts, Tc, T_A):
    if g + 1.0 > 0:
        k_max = f_c * g * g / (4.0 * (g + 1.0))
        out["max_contrast_at_declared_temps"] = k_max
        out["measured_contrast_within_max"] = bool(a <= k_max + 1e-12)
    A, B, C = (g + 1.0), (-g * l_tot), (a * l_tot ** 2 / f_c)
    disc = B * B - 4 * A * C
    if disc >= 0 and abs(A) > 1e-12:
        roots = [(-B - math.sqrt(disc)) / (2 * A), (-B + math.sqrt(disc)) / (2 * A)]
        roots = [r for r in roots if 0 < r <= l_tot * 1.0001]
        out["lambda_r_solutions_per_s"] = roots
        out["status"] = "computed from declared temperatures (see assumptions)"
    else:
        out["lambda_r_solutions_per_s"] = []
        out["status"] = ("declared temperatures cannot reproduce the measured "
                         "contrast within the master formula -- either a "
                         "temperature is misdeclared or a transmission-line "
                         "phase enhancement is present (as in the 2020 pilot)")
        headline_notes.append(out["status"])
    return out


def headline_numbers(meta, noise, coadd, detection, ladder, notes):
    """Spin-coupled floor fraction + distance-from-ceiling, 2020 methodology,
    with every assumption written out."""
    out = {"assumptions": [
        "Fundamental ceiling defined as a receiver whose on-resonance noise "
        "floor is entirely spin-coupled (Gueron dip depth -> 1, or "
        "equivalently amplifier + uncoupled-circuit contribution -> 0); "
        "distance quoted in dB of on-resonance power.",
        "Contrast-based numbers are as-measured and need no absolute "
        "calibration; converting to absolute spin counts additionally "
        "requires the 2020 pairing factor (~%.1f, A0-vs-window) and, for "
        "cold-circuit probes, the radiation-damping back-action suppression "
        "(x%.1f-%.1f), neither applied here." % (
            PAIRING_FACTOR_2020, BACKACTION_RANGE_2020[0], BACKACTION_RANGE_2020[1]),
    ]}
    if not detection.get("detected"):
        out["status"] = ("no significant spin-noise feature; headline numbers "
                         "are upper limits (see detection section)")
        ul = detection.get("upper_limit_95_amp")
        if ul is not None:
            out["spin_coupled_floor_fraction_ul95"] = ul
            out["distance_from_ceiling_db_lower_bound"] = \
                -10.0 * math.log10(min(ul, 1.0)) if ul > 0 else None
        return out
    a = coadd["amp_norm"]
    sa = coadd["amp_err"]
    sign = "bump" if a > 0 else "dip"
    out["feature_sign"] = sign
    if a > 0:
        frac = a / (1.0 + a)
        dfrac = sa / (1.0 + a) ** 2
        dist_db = 10.0 * math.log10((1.0 + a) / a)
        ddist = 10.0 / math.log(10.0) * sa / (a * (1.0 + a))
        out["spin_coupled_floor_fraction_note"] = (
            "emission bump: fraction of the ON-RESONANCE noise power that is "
            "spin-generated excess, a/(1+a)")
    else:
        depth = abs(a)
        frac = depth
        dfrac = sa
        dist_db = -10.0 * math.log10(min(depth, 1.0))
        ddist = 10.0 / math.log(10.0) * sa / depth
        out["spin_coupled_floor_fraction_note"] = (
            "Gueron dip: dip depth = f_c*lambda_r/lambda_tot = spin-coupled "
            "fraction of the floor at uniform temperature (a lower bound on "
            "the circuit coupling f_c)")
    out["spin_coupled_floor_fraction"] = frac
    out["spin_coupled_floor_fraction_err"] = dfrac
    out["distance_from_ceiling_db"] = dist_db
    out["distance_from_ceiling_db_stat_err"] = ddist
    # systematic envelope from receiver-gain linearity
    if ladder.get("available"):
        rg_pow = ladder["max_abs_power_deviation"]
        out["assumptions"].append(
            "Receiver-gain linearity measured on this bundle's RG ladder: "
            "max power deviation %.1f%%." % (100 * rg_pow))
    else:
        rg_pow = RG_POWER_ENVELOPE_UNTESTED
        out["assumptions"].append(
            "RG ladder unavailable: receiver-gain linearity UNTESTED; the "
            "2020 envelope of +/-20% in power is assumed and folded into "
            "the systematic uncertainty.")
    out["distance_from_ceiling_db_sys_err"] = 10.0 * math.log10(1.0 + rg_pow)
    out["rg_linearity_power_envelope"] = rg_pow
    return out


# ============================================================================
# QA
# ============================================================================

def parse_local(s):
    try:
        return datetime.datetime.strptime(s.strip()[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def qa_flags(bundle, meta, noise_res, validation_msgs):
    env = meta.get("environment", {})
    sw = meta.get("software", {})
    flags = []

    def add(level, name, detail):
        flags.append({"level": level, "check": name, "detail": detail})

    # sweep / lock state as recorded
    if env.get("lock_sweep_confirmed_off") is True:
        add("OK", "BSMS field sweep",
            "operator confirmed the field sweep was OFF")
    else:
        add("FAIL", "BSMS field sweep",
            "sweep-off NOT confirmed: a running sweep smears the feature by "
            "kHz; treat any line result with suspicion")
    if env.get("locked") is True:
        add("WARN", "deuterium lock",
            "lock recorded ON during the noise block; lock RF can perturb "
            "the noise floor -- interpret with care")
    else:
        add("OK", "deuterium lock", "lock recorded OFF (as recommended)")
    # ADC clipping
    for exp in meta.get("experiments", []):
        st = bundle.raw_int_stats(exp["expno"])
        if st is None:
            add("WARN", "ADC check expno %d" % exp["expno"],

                "no raw data file readable")
            continue
        if st["fullscale_fraction"] is None:
            add("OK", "ADC check expno %d" % exp["expno"],
                "float64 data (TopSpin 4 style); clipping check not "
                "applicable, max |value| %.3g" % st["max_abs"])
        elif st["fullscale_fraction"] > 0.90:
            add("FAIL", "ADC check expno %d" % exp["expno"],
                "raw int32 data reaches %.0f%% of full scale -- clipping "
                "likely" % (100 * st["fullscale_fraction"]))
        else:
            add("OK", "ADC check expno %d" % exp["expno"],
                "max |sample| = %.3g (%.2g%% of int32 full scale)"
                % (st["max_abs"], 100 * st["fullscale_fraction"]))
    # spikes
    if noise_res:
        spikes = [r.get("n_spikes", 0) for r in noise_res.get("per_row", [])]
        tot = int(np.sum(spikes))
        lvl = "OK" if tot < 0.001 * sum(
            r.get("nperseg", 1) for r in noise_res["per_row"]) else "WARN"
        add(lvl, "PSD spikes replaced",
            "%d bins across %d rows (per-row: %s)" % (tot, len(spikes), spikes))
    # timestamps
    exps = meta.get("experiments", [])
    bad_pairs = [e["expno"] for e in exps
                 if (parse_local(e.get("started_local", "")) and
                     parse_local(e.get("finished_local", "")) and
                     parse_local(e["finished_local"]) < parse_local(e["started_local"]))]
    if bad_pairs:
        add("FAIL", "timestamps",
            "finished < started for expno(s) %s" % bad_pairs)
    else:
        add("OK", "timestamps", "finished >= started for every experiment")
    starts = [(e["expno"], parse_local(e.get("started_local", ""))) for e in exps]
    starts = [(n, t) for n, t in starts if t]
    if len(starts) >= 2:
        order = [n for n, _ in sorted(starts, key=lambda p: p[1])]
        listed = [n for n, _ in starts]
        if order != listed:
            add("WARN", "experiment order",
                "start times are not monotonic in listed order (%s); normal "
                "for archival repackages, check provenance otherwise" % order)
    # software provenance -- WARN if either the script version or the
    # run_mode is missing: a bundle without a declared run_mode is
    # schema-valid (run_mode is optional in 1.1, absent in 1.0) but its
    # provenance is incomplete, and the science/software-test gate had to
    # assume 'live'.
    prov_ok = bool(sw.get("script_version")) and bool(sw.get("run_mode"))
    prov_detail = ("script_version=%s run_mode=%s script_sha256=%s" % (
        sw.get("script_version", "MISSING"), sw.get("run_mode", "undeclared"),
        (sw.get("script_sha256", "unavailable") or "")[:23]))
    if not sw.get("run_mode"):
        prov_detail += (" -- run_mode undeclared: cannot verify this bundle "
                        "is not a software test; treated as live data")
    add("OK" if prov_ok else "WARN", "software provenance", prov_detail)
    # validator messages that were warnings
    for m in validation_msgs:
        if m.startswith("WARN"):
            add("WARN", "bundle validator", m[7:].strip())
    return flags


# ============================================================================
# Figures
# ============================================================================

def fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight",
                facecolor="white")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def make_figures(noise_res, refs, ladder, detection):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figs = {}
    C_DATA, C_FIT, C_ALT = "#3b6ea5", "#c44e52", "#55a868"

    if noise_res and "_coadd" in noise_res:
        grid = noise_res["_coadd"]["grid"]
        avg = noise_res["_coadd"]["avg"]
        fig, ax = plt.subplots(figsize=(7.2, 4.2))
        ax.plot(grid, avg, color=C_DATA, lw=0.8,
                label="co-added normalized PSD (%d rows)"
                % noise_res["coadd_fit"]["n_rows_coadded"])
        cf = noise_res["coadd_fit"]
        model = lineshape(grid, cf["amp_norm"], cf["disp_norm"],
                          cf["center_shift_hz"], cf["fwhm_hz"], cf["offset"])
        ax.plot(grid, model, color=C_FIT, lw=1.6,
                label="absorptive+dispersive fit")
        ax.axhline(1.0, color="0.5", lw=0.7, ls=":")
        ax.set_xlabel("offset from aligned line center (Hz)")
        ax.set_ylabel("PSD / floor")
        ax.set_xlim(-150, 150)
        ax.legend(frameon=False, fontsize=9)
        ax.set_title("Drift-aligned co-added spin-noise line")
        figs["coadd"] = fig_to_b64(fig)

    if noise_res and noise_res.get("_rows"):
        r0 = noise_res["_rows"][0]
        fig, ax = plt.subplots(figsize=(7.2, 3.4))
        ax.semilogy(r0["f"], r0["psd"], color=C_DATA, lw=0.5,
                    label="row 1 PSD")
        ax.semilogy(r0["f"], r0["base"], color=C_FIT, lw=1.2,
                    label="broad-SG baseline")
        ax.set_xlabel("offset from carrier (Hz)")
        ax.set_ylabel("PSD (counts$^2$/Hz)")
        ax.legend(frameon=False, fontsize=9)
        ax.set_title("Full-band PSD and baseline (first noise row)")
        figs["fullband"] = fig_to_b64(fig)

        fits = [pr["fit"] for pr in noise_res["per_row"] if "fit" in pr]
        if fits:
            fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0))
            idx = np.arange(1, len(fits) + 1)
            axes[0].errorbar(idx, [f_["amp_norm"] for f_ in fits],
                             yerr=[f_["amp_err"] for f_ in fits],
                             fmt="o", color=C_DATA, ms=4)
            axes[0].set_xlabel("noise row")
            axes[0].set_ylabel("peak excess (x floor)")
            axes[1].plot(idx, [f_["center_hz"] for f_ in fits], "o-",
                         color=C_ALT, ms=4, lw=0.8)
            axes[1].set_xlabel("noise row")
            axes[1].set_ylabel("line center (Hz)")
            fig.suptitle("Per-row line amplitude and center (drift track)",
                         fontsize=10)
            fig.tight_layout()
            figs["perrow"] = fig_to_b64(fig)

    if ladder.get("available"):
        fig, ax = plt.subplots(figsize=(5.4, 3.6))
        rg = [r["rg"] for r in ladder["rungs"]]
        am = [r["amplitude_counts"] for r in ladder["rungs"]]
        ax.loglog(rg, am, "o", color=C_DATA, ms=6, label="ladder rung")
        xx = np.geomspace(min(rg), max(rg), 50)
        ax.loglog(xx, ladder["slope_counts_per_rg"] * xx, "-",
                  color=C_FIT, lw=1.2, label="linear (through origin)")
        ax.set_xlabel("receiver gain (RG)")
        ax.set_ylabel("line amplitude (counts)")
        ax.legend(frameon=False, fontsize=9)
        ax.set_title("RG ladder linearity (max dev %.1f%% amp)"
                     % (100 * ladder["max_abs_fractional_deviation"]))
        figs["ladder"] = fig_to_b64(fig)
    return figs


# ============================================================================
# Report rendering
# ============================================================================

CSS = """
:root { --bg:#ffffff; --fg:#1c2430; --muted:#5b6b7d; --card:#f5f7fa;
        --accent:#3b6ea5; --ok:#2e7d32; --warn:#b26a00; --fail:#c62828;
        --border:#d7dee7; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151a21; --fg:#e4e9f0; --muted:#9aa8b8; --card:#1e2530;
          --accent:#7aa7d4; --ok:#7bc67e; --warn:#e0a458; --fail:#ef7b73;
          --border:#33404f; } }
body { background:var(--bg); color:var(--fg);
       font:15px/1.55 -apple-system,'Segoe UI',Helvetica,Arial,sans-serif;
       max-width:960px; margin:2rem auto; padding:0 1.2rem; }
h1 { font-size:1.5rem; margin-bottom:.2rem; }
h2 { font-size:1.15rem; border-bottom:1px solid var(--border);
     padding-bottom:.25rem; margin-top:2rem; }
.small, .muted { color:var(--muted); font-size:.88rem; }
.banner { border:2px solid var(--fail); color:var(--fail); padding:.8rem 1rem;
          border-radius:8px; font-weight:700; margin:1rem 0; }
.banner.info { border-color:var(--warn); color:var(--warn); }
.card { background:var(--card); border:1px solid var(--border);
        border-radius:8px; padding:1rem 1.2rem; margin:.8rem 0; }
table { border-collapse:collapse; width:100%; font-size:.9rem; }
th,td { text-align:left; padding:.3rem .6rem; border-bottom:1px solid var(--border); }
th { color:var(--muted); font-weight:600; }
.ok { color:var(--ok); font-weight:700; } .warn { color:var(--warn); font-weight:700; }
.fail { color:var(--fail); font-weight:700; }
.fig { background:#ffffff; border:1px solid var(--border); border-radius:8px;
       padding:.6rem; margin:.8rem 0; text-align:center; }
.fig img { max-width:100%; height:auto; }
.big { font-size:1.35rem; font-weight:700; }
code { background:var(--card); padding:.05rem .3rem; border-radius:4px; }
ul { margin:.3rem 0 .3rem 1.2rem; }
"""


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def fmt(v, nd=3):
    if v is None:
        return "&mdash;"
    if isinstance(v, float):
        if v == 0:
            return "0"
        if abs(v) >= 1e4 or abs(v) < 1e-3:
            return "%.*g" % (nd, v)
        return "%.*f" % (nd, v)
    return esc(v)


def render_html(ctx):
    """Assemble the single-file HTML report from the context dict."""
    meta = ctx["meta"]
    parts = []
    A = parts.append
    A("<meta charset='utf-8'>")
    A("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    A("<title>Spin-noise facility report — %s</title>"
      % esc(meta.get("facility", {}).get("facility_slug", "unknown")))
    A("<style>%s</style>" % CSS)

    fac = meta.get("facility", {})
    spec = meta.get("spectrometer", {})
    sw = meta.get("software", {})
    A("<h1>Spin-noise network — facility report</h1>")
    A("<div class='muted'>%s, %s, %s &middot; %s &middot; %.6g MHz "
      "(&approx;%.4g T) &middot; probe: %s (%s)</div>"
      % (esc(fac.get("institution", "?")), esc(fac.get("city", "?")),
         esc(fac.get("country", "?")), esc(spec.get("console", "?")),
         float(spec.get("h1_freq_mhz", 0) or 0),
         float(spec.get("field_tesla", 0) or 0),
         esc(spec.get("probe_string", "?")), esc(spec.get("probe_type", "?"))))
    A("<div class='muted small'>bundle: <code>%s</code> &middot; acquired "
      "run_mode=<code>%s</code> by script v%s &middot; report generator "
      "v%s &middot; generated %s UTC &middot; contact: %s</div>"
      % (esc(os.path.basename(ctx["bundle_path"])),
         esc(sw.get("run_mode", "undeclared")),
         esc(sw.get("script_version", "?")), esc(REPORT_VERSION),
         datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
         esc(CONTACT)))

    if ctx["report_type"] == "software-test":
        A("<div class='banner'>SOFTWARE-TEST REPORT — NOT A SCIENCE RESULT."
          "<br>This bundle declares run_mode='%s': the hardware commands were "
          "mocked and the files contain no measurement. No science numbers "
          "are produced from such bundles, by design.</div>"
          % esc(sw.get("run_mode")))
        A("<h2>Bundle validation</h2><div class='card'><ul>")
        for m in ctx["validation_msgs"]:
            cls = "fail" if m.startswith("ERROR") else (
                "warn" if m.startswith("WARN") else "ok")
            A("<li class='%s'>%s</li>" % (cls, esc(m)))
        A("</ul><p>Validator verdict: <span class='%s'>%s</span></p></div>"
          % ("ok" if ctx["validation_ok"] else "fail",
             "PASS" if ctx["validation_ok"] else "FAIL"))
        A("<h2>Bundle inventory</h2><div class='card'><table>"
          "<tr><th>expno</th><th>role</th><th>pulprog</th><th>TD</th>"
          "<th>rows</th><th>RG</th><th>data files present</th></tr>")
        for e in meta.get("experiments", []):
            present = ", ".join(sorted(
                os.path.basename(n) for n in ctx["names"]
                if n.startswith("data/%d/" % e["expno"]))) or "none"
            A("<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
              "<td>%s</td><td>%s</td></tr>"
              % (e["expno"], esc(e.get("role")), esc(e.get("pulprog")),
                 fmt(e.get("td")), fmt(e.get("td1_rows")), fmt(e.get("rg")),
                 esc(present)))
        A("</table><p class='small'>The plumbing that produced this bundle "
          "(dialog chain, dataset bookkeeping, meta.json, zip, checksums) "
          "was exercised end to end; that is all this report certifies."
          "</p></div>")
        A(_footer())
        return "".join(parts)

    if not sw.get("run_mode"):
        A("<div class='banner info'>PROVENANCE INCOMPLETE — this bundle's "
          "software block declares no run_mode (optional in schema 1.1, "
          "absent in 1.0). It is analyzed as live data, but the "
          "software-test gate could not verify that; see the software-"
          "provenance QA flag.</div>")
    if sw.get("run_mode") == "synthetic-injection":
        A("<div class='banner info'>SYNTHETIC-INJECTION VALIDATION BUNDLE — "
          "the data are numerically generated with a known injected feature. "
          "The numbers below validate the pipeline; they are NOT a "
          "measurement of any instrument.</div>")
    if sw.get("run_mode") == "archival-repackage":
        A("<div class='banner info'>ARCHIVAL REPACKAGE — real spectrometer "
          "data recorded before this network existed, repackaged into the "
          "bundle format. Timestamps and some metadata are reconstructed; "
          "see QA flags.</div>")

    # ---- headline
    hd = ctx["headline"]
    det = ctx["detection"]
    A("<h2>Headline: distance from the fundamental sensitivity ceiling</h2>")
    A("<div class='card'>")
    if det.get("detected"):
        A("<p><span class='big'>%.2f &plusmn; %.2f dB</span> "
          "(stat) &plusmn; %.2f dB (sys) above the fully spin-coupled "
          "(fundamental) noise floor at resonance.</p>"
          % (hd["distance_from_ceiling_db"],
             hd["distance_from_ceiling_db_stat_err"],
             hd["distance_from_ceiling_db_sys_err"]))
        A("<p>Spin-coupled floor fraction: <b>%.3f &plusmn; %.3f</b> "
          "<span class='small'>(%s)</span></p>"
          % (hd["spin_coupled_floor_fraction"],
             hd["spin_coupled_floor_fraction_err"],
             esc(hd["spin_coupled_floor_fraction_note"])))
    else:
        A("<p><b>No significant spin-noise feature was detected.</b> "
          "95%% upper limit on the feature amplitude: "
          "<span class='big'>%s &times; floor</span>.</p>"
          % fmt(det.get("upper_limit_95_amp")))
        if hd.get("distance_from_ceiling_db_lower_bound") is not None:
            A("<p>The receiver therefore sits <b>&ge; %.1f dB</b> from the "
              "fully spin-coupled ceiling under this sample and tuning "
              "(a lower bound, not a measurement of the receiver alone: a "
              "weakly protonated sample gives the same null &mdash; the 2022 "
              "lesson).</p>" % hd["distance_from_ceiling_db_lower_bound"])
    tc = ctx["temp_contrast"]
    A("<p><b>Temperature-contrast point:</b> ")
    if "requires" in str(tc.get("status", "")):
        A("<span class='warn'>%s</span>" % esc(tc["status"]))
    else:
        A("%s" % esc(tc.get("status", "")))
        if tc.get("lambda_r_solutions_per_s"):
            A("<br><span class='small'>master-formula radiation-damping rate "
              "solutions: %s s<sup>-1</sup> (f_c = %.3f, &lambda;_tot = %.1f "
              "s<sup>-1</sup>)</span>"
              % (", ".join("%.1f" % r for r in tc["lambda_r_solutions_per_s"]),
                 tc.get("f_c", float("nan")),
                 tc.get("lambda_tot_per_s", float("nan"))))
    A("</p>")
    A("<p class='small'><b>Assumptions (all of them):</b></p><ul class='small'>")
    for s in hd.get("assumptions", []):
        A("<li>%s</li>" % esc(s))
    A("</ul></div>")

    # ---- feature
    A("<h2>Spin-noise feature</h2><div class='card'>")
    if det.get("detected"):
        cf = ctx["noise"]["coadd_fit"]
        sign_word = ("emission BUMP (cold-circuit signature)"
                     if cf["amp_norm"] > 0 else
                     "absorption DIP (Gueron dip, uniform-temperature signature)")
        A("<table><tr><th>quantity</th><th>value</th></tr>")
        A("<tr><td>sign / character</td><td>%s</td></tr>" % sign_word)
        A("<tr><td>peak excess (amplitude rel. to floor)</td>"
          "<td>%.3f &plusmn; %.3f</td></tr>" % (cf["amp_norm"], cf["amp_err"]))
        A("<tr><td>FWHM</td><td>%.2f &plusmn; %.2f Hz</td></tr>"
          % (cf["fwhm_hz"], cf["fwhm_err_hz"]))
        ba = cf.get("asymmetry_b_over_a")
        A("<tr><td>dispersive fraction b/a</td><td>%s%s</td></tr>"
          % (fmt(ba), (" &plusmn; %.3f" % cf["asymmetry_err"])
             if cf.get("asymmetry_err") else ""))
        A("<tr><td>line center (mean over rows)</td><td>%.1f Hz from carrier"
          "</td></tr>" % det.get("line_center_hz", float("nan")))
        A("<tr><td>combined significance (matched-filter NPE, quadrature)"
          "</td><td>%.1f&sigma;</td></tr>" % det.get("npe_combined", float("nan")))
        A("<tr><td>amplitude significance (co-added fit)</td>"
          "<td>%.1f&sigma;</td></tr>" % det.get("amp_significance", float("nan")))
        A("</table>")
    else:
        A("<p>No feature at &ge;%.0f&sigma;. Upper limit constructed at the "
          "reference line position, profiled over plausible widths:</p>"
          % DETECT_NSIGMA)
        A("<table><tr><th>assumed FWHM (Hz)</th><th>fitted amp</th>"
          "<th>&sigma;</th><th>UL95</th></tr>")
        for d in det.get("upper_limit_details", []):
            A("<tr><td>%.1f</td><td>%.4f</td><td>%.4f</td><td>%.4f</td></tr>"
              % (d["fwhm_hz"], d["amp"], d["amp_err"], d["ul95"]))
        A("</table>")
    A("</div>")
    for key in ("coadd", "fullband", "perrow"):
        if key in ctx["figs"]:
            A("<div class='fig'><img alt='%s' src='data:image/png;base64,%s'>"
              "</div>" % (key, ctx["figs"][key]))

    # ---- per-row table
    if ctx["noise"] and ctx["noise"].get("per_row"):
        A("<h2>Per-row noise analysis</h2><div class='card'><table>"
          "<tr><th>row</th><th>segments</th><th>spikes</th><th>amp</th>"
          "<th>FWHM (Hz)</th><th>center (Hz)</th><th>b/a</th><th>NPE</th></tr>")
        for i, pr in enumerate(ctx["noise"]["per_row"]):
            if "fit" in pr:
                ft = pr["fit"]
                A("<tr><td>%d</td><td>%d</td><td>%d</td>"
                  "<td>%.2f&plusmn;%.2f</td><td>%.1f</td><td>%.1f</td>"
                  "<td>%.2f</td><td>%.1f</td></tr>"
                  % (i + 1, pr["nseg"], pr["n_spikes"], ft["amp_norm"],
                     ft["amp_err"], ft["fwhm_hz"], ft["center_hz"],
                     pr.get("asymmetry_b_over_a") or float("nan"),
                     pr.get("npe_at_line", float("nan"))))
            else:
                A("<tr><td>%d</td><td>%d</td><td>%d</td>"
                  "<td colspan='5' class='warn'>fit failed: %s</td></tr>"
                  % (i + 1, pr.get("nseg", 0), pr.get("n_spikes", 0),
                     esc(pr.get("fit_error", "?"))))
        A("</table></div>")

    # ---- RG ladder
    lad = ctx["ladder"]
    A("<h2>Receiver-gain linearity (RG ladder)</h2><div class='card'>")
    if lad.get("available"):
        A("<p>Max amplitude deviation from linearity: <b>%.1f%%</b> "
          "(&asymp;%.1f%% in power) across %d rungs.</p>"
          % (100 * lad["max_abs_fractional_deviation"],
             100 * lad["max_abs_power_deviation"], len(lad["rungs"])))
        A("<table><tr><th>expno</th><th>RG</th><th>amplitude (counts)</th>"
          "<th>deviation</th></tr>")
        for r in lad["rungs"]:
            A("<tr><td>%d</td><td>%.4g</td><td>%.4g</td><td>%+.2f%%</td></tr>"
              % (r["expno"], r["rg"], r["amplitude_counts"],
                 100 * r.get("fractional_deviation", 0)))
        A("</table>")
    else:
        A("<p class='warn'>%s</p>" % esc(lad.get("note", "unavailable")))
    A("<p class='small'>Receiver-gain linearity was the largest UNTESTED "
      "systematic of the 2020 pilot (its calibration bridges RG 184.37 &rarr; "
      "0.96); the network protocol measures it at every facility for exactly "
      "that reason.</p></div>")
    if "ladder" in ctx["figs"]:
        A("<div class='fig'><img alt='ladder' "
          "src='data:image/png;base64,%s'></div>" % ctx["figs"]["ladder"])

    # ---- references
    A("<h2>References and floor calibration</h2><div class='card'>")
    refs = ctx["refs"]
    if refs:
        A("<table><tr><th>expno</th><th>role</th><th>rows</th><th>RG</th>"
          "<th>A0 (counts)</th><th>FWHM_amp (Hz)</th><th>center (Hz)</th>"
          "<th>tail floor (counts&sup2;/Hz)</th></tr>")
        for r in refs:
            if not r.get("readable"):
                A("<tr><td>%d</td><td>%s</td>"
                  "<td colspan='6' class='warn'>unreadable</td></tr>"
                  % (r["expno"], esc(r["role"])))
                continue
            A("<tr><td>%d</td><td>%s</td><td>%d</td><td>%.4g</td><td>%s</td>"
              "<td>%s</td><td>%s</td><td>%s</td></tr>"
              % (r["expno"], esc(r["role"]), r["n_rows"], r["rg"],
                 fmt(r.get("A0_counts"), 5), fmt(r.get("fwhm_amp_hz")),
                 fmt(r.get("line_center_hz"), 5),
                 fmt(r.get("tail_floor_counts2perhz"))))
        A("</table>")
        cal = ctx["floor_cal"]
        if cal:
            A("<ul class='small'>")
            for k, label in (
                ("line_stability_open_close_hz",
                 "line-position stability open&rarr;close (Hz)"),
                ("a0_ratio_close_over_open", "A0 close/open"),
                ("noise_floor_counts2perhz_at_noise_rg",
                 "noise floor at line, noise RG (counts&sup2;/Hz)"),
                ("noise_floor_bridged_to_ref_rg",
                 "noise floor bridged to reference RG (counts&sup2;/Hz)"),
                ("gain_ratio_amplitude", "gain ratio RG_noise/RG_ref"),
                ("floor_consistency_bridged_over_ref_tail",
                 "floor consistency: bridged noise floor / reference tail floor"),
                ("spin_line_integrated_counts2_at_ref_rg",
                 "integrated spin-line power at reference RG (counts&sup2;)"),
            ):
                if cal.get(k) is not None:
                    A("<li>%s: <b>%s</b></li>" % (label, fmt(cal[k], 4)))
            A("</ul>")
            if cal.get("floor_consistency_note"):
                A("<p class='small'>%s</p>" % esc(cal["floor_consistency_note"]))
    else:
        A("<p class='warn'>No readable reference experiments; floor "
          "calibration and line-position anchoring unavailable.</p>")
    A("</div>")

    # ---- QA
    A("<h2>QA flags</h2><div class='card'><table>"
      "<tr><th>status</th><th>check</th><th>detail</th></tr>")
    for fl in ctx["qa"]:
        A("<tr><td class='%s'>%s</td><td>%s</td><td>%s</td></tr>"
          % (fl["level"].lower(), fl["level"], esc(fl["check"]),
             esc(fl["detail"])))
    A("</table></div>")

    # ---- what was and was not determined
    A("<h2>What this report does and does not establish</h2><div class='card'>"
      "<ul class='small'>")
    for line in ctx["honesty"]:
        A("<li>%s</li>" % esc(line))
    A("</ul></div>")
    A(_footer())
    return "".join(parts)


def _footer():
    return ("<p class='muted small' style='margin-top:2rem'>Spin-noise "
            "network per-facility report &middot; generator v%s &middot; "
            "methodology: 2020 EPFL pilot pipeline (Welch power averaging, "
            "spike replacement, SG baseline, absorptive+dispersive fit, "
            "gain bridging) &middot; Blanchard, Ebadi, Claude (Anthropic) "
            "&middot; contact %s</p>" % (esc(REPORT_VERSION), esc(CONTACT)))


# ============================================================================
# main
# ============================================================================

def strip_private(obj):
    """Drop numpy arrays / private keys for the JSON output."""
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items()
                if not k.startswith("_")}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return None
    return obj


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("bundle", help="path to spinnoise_*.zip")
    ap.add_argument("--out", default=None,
                    help="output directory (default: <bundle_stem>_report "
                         "next to the bundle)")
    args = ap.parse_args(argv)

    bundle_path = os.path.abspath(args.bundle)
    if not os.path.isfile(bundle_path):
        print("ERROR: no such file: %s" % bundle_path)
        return 2
    out_dir = args.out or os.path.join(
        os.path.dirname(bundle_path),
        os.path.splitext(os.path.basename(bundle_path))[0] + "_report")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    # ---- 1. validation via the uploader's own selftest logic
    schema_path = os.path.join(REPO, "schema", "meta.schema.json")
    val_ok, val_msgs = upload_bundle.verify_bundle(bundle_path, schema_path)
    for m in val_msgs:
        print(m)
    if not val_ok:
        print("ERROR: bundle failed validation; no report produced. Fix the "
              "problems above (or re-create the bundle).")
        return 1

    bundle = Bundle(bundle_path)
    meta = bundle.meta
    sw = meta.get("software", {}) if isinstance(meta.get("software"), dict) else {}
    run_mode = sw.get("run_mode", "undeclared")

    report = {
        "report_version": REPORT_VERSION,
        "generated_utc": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bundle": os.path.basename(bundle_path),
        "bundle_sha256": upload_bundle.sha256_of_file(bundle_path),
        "facility_slug": meta.get("facility", {}).get("facility_slug"),
        "run_mode": run_mode,
        "software": sw,
        "validation": {"ok": val_ok, "messages": val_msgs},
    }

    # ---- 2. run-mode gate
    if run_mode in SOFTWARE_TEST_MODES:
        report["report_type"] = "software-test"
        report["science"] = None
        report["note"] = ("run_mode '%s': plumbing test, not data; science "
                          "analysis refused by design" % run_mode)
        ctx = {"report_type": "software-test", "meta": meta,
               "bundle_path": bundle_path, "validation_msgs": val_msgs,
               "validation_ok": val_ok, "names": sorted(bundle.names)}
        html = render_html(ctx)
        _write(out_dir, html, report)
        print("SOFTWARE-TEST report written to %s" % out_dir)
        return 0

    report["report_type"] = "science" if run_mode in ("live", "undeclared",
                                                      "archival-repackage") \
        else "synthetic-validation"

    # ---- collect experiments by role
    exps = meta.get("experiments", [])
    by_role = {}
    for e in exps:
        by_role.setdefault(e.get("role"), []).append(e)
    fs_default = float(exps[0].get("sw_hz", 6900.0)) if exps else 6900.0

    # ---- 5. references first (they anchor the line-position guess)
    refs = []
    for role in ("reference_open", "reference_close"):
        for e in by_role.get(role, []):
            refs.append(analyze_reference_exp(bundle, e, fs_default))
    readable_refs = [r for r in refs if r.get("readable") and
                     r.get("line_center_hz") is not None]
    if readable_refs:
        f0_guess = float(np.mean([r["line_center_hz"] for r in readable_refs]))
        w_ref = float(np.mean([r["fwhm_amp_hz"] for r in readable_refs
                               if r.get("fwhm_amp_hz")]) or 12.0)
    else:
        f0_guess, w_ref = 0.0, 12.0

    # ---- 3. noise blocks
    noise_res = None
    for e in by_role.get("noise", []):
        noise_res = analyze_noise_block(bundle, e, f0_guess, fs_default)
        if noise_res is not None:
            break

    detection = {"detected": False}
    if noise_res and "coadd_fit" in noise_res:
        cf = noise_res["coadd_fit"]
        amp_sig = abs(cf["amp_norm"]) / cf["amp_err"] if cf["amp_err"] else 0.0
        npes = [pr.get("npe_at_line", 0.0) for pr in noise_res["per_row"]
                if "fit" in pr]
        npe_comb = float(math.sqrt(sum(v * v for v in npes)))
        centers = [pr["fit"]["center_hz"] for pr in noise_res["per_row"]
                   if "fit" in pr]
        detection.update({
            "amp_significance": amp_sig, "npe_combined": npe_comb,
            "line_center_hz": float(np.mean(centers)) if centers else None,
            "line_center_drift_hz": (float(max(centers) - min(centers))
                                     if centers else None),
        })
        if amp_sig >= DETECT_NSIGMA:
            detection["detected"] = True
    if noise_res and not detection["detected"]:
        # calibrated upper limit at the reference-anchored position
        r0 = noise_res["_rows"][0]
        # co-add unaligned (no feature to align on)
        stack = np.mean([rr["pnorm"] for rr in noise_res["_rows"]], axis=0)
        ul, ul_details = upper_limit_at(
            r0["f"], stack, f0_guess,
            [max(0.5 * w_ref, 2.0), w_ref, 2.0 * w_ref])
        detection["upper_limit_95_amp"] = ul
        detection["upper_limit_details"] = ul_details
        detection["upper_limit_note"] = (
            "95%% one-sided limit on |feature amplitude| relative to the "
            "floor, at the reference line position, profiled over widths "
            "0.5/1/2 x the reference linewidth (%.1f Hz)" % w_ref)

    # ---- 4. RG ladder
    ladder = analyze_rg_ladder(bundle, meta, f0_guess, fs_default)

    # ---- floor calibration / reference-noise consistency
    floor_cal = {}
    if noise_res and readable_refs:
        opens = [r for r in readable_refs if r["role"] == "reference_open"]
        closes = [r for r in readable_refs if r["role"] == "reference_close"]
        if opens and closes:
            floor_cal["line_stability_open_close_hz"] = float(
                closes[0]["line_center_hz"] - opens[0]["line_center_hz"])
            if opens[0].get("A0_counts") and closes[0].get("A0_counts"):
                floor_cal["a0_ratio_close_over_open"] = float(
                    closes[0]["A0_counts"] / opens[0]["A0_counts"])
        rg_noise = noise_res.get("rg") or 1.0
        rg_ref = readable_refs[0].get("rg") or 1.0
        gain = rg_noise / rg_ref if rg_ref else None
        floors = [pr.get("baseline_psd_at_line") for pr in noise_res["per_row"]
                  if pr.get("baseline_psd_at_line")]
        if floors and gain:
            fl = float(np.mean(floors))
            floor_cal["noise_floor_counts2perhz_at_noise_rg"] = fl
            floor_cal["gain_ratio_amplitude"] = gain
            floor_cal["noise_floor_bridged_to_ref_rg"] = fl / gain ** 2
            tails = [r.get("tail_floor_counts2perhz") for r in readable_refs
                     if r.get("tail_floor_counts2perhz")]
            if tails:
                floor_cal["floor_consistency_bridged_over_ref_tail"] = float(
                    (fl / gain ** 2) / np.mean(tails))
                floor_cal["floor_consistency_note"] = (
                    "reference tail floor uses the last 30% of each reference "
                    "row and may retain residual signal for slowly decaying "
                    "FIDs. A ratio near 1 confirms the gain bridge; a ratio "
                    "far BELOW 1 with a low-RG reference usually means the "
                    "reference floor is digitizer/backend dominated (front-"
                    "end noise attenuated below the ADC floor at low gain) "
                    "-- expected, and why the 2020 calibration bridged gains "
                    "on the pulsed SIGNAL amplitude, not on the floor. Other "
                    "departures from 1 flag RG nonlinearity or a gain-chain "
                    "change between blocks.")
            if detection.get("detected"):
                cf = noise_res["coadd_fit"]
                floor_cal["spin_line_integrated_counts2_at_ref_rg"] = float(
                    cf["amp_norm"] * math.pi * cf["fwhm_hz"] / 2.0 * fl
                    / gain ** 2)

    # ---- 6. headline numbers
    headline_notes = []
    coadd = noise_res.get("coadd_fit") if noise_res else None
    temp_contrast = temperature_contrast_point(meta, coadd, headline_notes)
    headline = headline_numbers(meta, noise_res, coadd, detection, ladder,
                                headline_notes)
    if detection.get("detected") and coadd:
        expected = {"RT": "dip", "N2-cryo": "bump", "He-cryo": "bump"}.get(
            meta.get("spectrometer", {}).get("probe_type"))
        got = "bump" if coadd["amp_norm"] > 0 else "dip"
        headline["sign_vs_probe_type"] = (
            "consistent (%s probe, %s observed)" % (
                meta["spectrometer"]["probe_type"], got)
            if expected == got else
            "UNEXPECTED: %s probe but %s observed -- check tuning state and "
            "temperatures" % (meta["spectrometer"].get("probe_type"), got))

    # ---- 7. QA
    qa = qa_flags(bundle, meta, noise_res, val_msgs)
    if detection.get("detected") and "UNEXPECTED" in str(
            headline.get("sign_vs_probe_type", "")):
        qa.append({"level": "WARN", "check": "feature sign vs probe type",
                   "detail": headline["sign_vs_probe_type"]})

    # ---- honesty section
    honesty = [
        "Determined: the receiver's measured noise spectrum around the 1H "
        "line, the feature contrast (or its upper limit), linewidth, "
        "dispersive admixture, floor calibration against the small-flip "
        "references, and the QA state of the acquisition.",
        "The distance-from-ceiling number is contrast-based and holds for "
        "THIS sample, tuning state, and temperature; it is not a universal "
        "property of the spectrometer.",
    ]
    if not ladder.get("available"):
        honesty.append("NOT determined: receiver-gain linearity (no usable "
                       "RG ladder in this bundle) -- the 2020 pilot's "
                       "largest untested systematic; the +/-20% power "
                       "envelope is assumed instead.")
    if "requires" in str(temp_contrast.get("status", "")):
        honesty.append("NOT determined: the temperature-contrast point "
                       "(coil/preamp temperatures absent from meta.json); "
                       "declaring them in a future run upgrades this report "
                       "for free.")
    if not detection.get("detected"):
        honesty.append("A null here does not distinguish a weakly coupled "
                       "receiver from a weakly protonated sample (2022 "
                       "lesson); the recorded H2O fraction is %s%%."
                       % meta.get("sample", {}).get("h2o_fraction_pct"))
    if run_mode == "archival-repackage":
        honesty.append("Archival repackage: acquisition predates the network "
                       "protocol; RG ladder and declared temperatures were "
                       "not part of the original session.")

    figs = make_figures(noise_res, refs, ladder, detection)
    ctx = {"report_type": report["report_type"], "meta": meta,
           "bundle_path": bundle_path, "validation_msgs": val_msgs,
           "validation_ok": val_ok, "noise": noise_res, "refs": refs,
           "ladder": ladder, "detection": detection, "headline": headline,
           "temp_contrast": temp_contrast, "floor_cal": floor_cal,
           "qa": qa, "figs": figs, "honesty": honesty,
           "names": sorted(bundle.names)}
    html = render_html(ctx)

    report["science"] = strip_private({
        "line_position_guess_hz": f0_guess,
        "reference_linewidth_hz": w_ref,
        "noise": noise_res, "references": refs, "rg_ladder": ladder,
        "detection": detection, "headline": headline,
        "temperature_contrast": temp_contrast, "floor_calibration": floor_cal,
        "qa_flags": qa, "honesty": honesty,
    })
    _write(out_dir, html, report)
    print("report written to %s" % out_dir)
    return 0


def _write(out_dir, html, report):
    with open(os.path.join(out_dir, "report.html"), "w") as fh:
        fh.write(html)
    with open(os.path.join(out_dir, "report.json"), "w") as fh:
        json.dump(strip_private(report), fh, indent=1, default=float)
        fh.write("\n")


if __name__ == "__main__":
    sys.exit(main())
