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
  8. Clock audit (schema 1.2 bundles): fits the fractional console-clock
     offset from wall-clock vs OCXO-implied elapsed time across blocks,
     re-deriving each block's expected duration from its bundled
     pulse-program text plus acqus (every programmed delay, pulse, and
     the per-scan pre-acquisition delay DE -- the acquisition-side
     formula models only AQ and d1, and a per-scan shortfall biases the
     offset by ~shortfall/scan-duration), states which
     absolute-frequency requirement tiers the offset satisfies, and
     flags short sessions as inconclusive. Older bundles without the
     audit are reported as such, without penalty; blocks whose
     pulse-program text or acqus cannot be modeled with certainty fall
     back to the script-recorded expectations, flagged per block.

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

# Clock audit (schema 1.2). Acquisition durations derive from the console's
# OCXO master clock; the workstation wall clock is normally NTP-disciplined.
# Fitting wall-clock elapsed vs OCXO-implied elapsed across the session's
# blocks measures the fractional console-clock offset for free.
NTP_JITTER_S = 0.010          # assumed wall-clock timestamp jitter (5-10 ms typical)
CLOCK_MIN_SPAN_S = 3600.0     # audits spanning less than 1 h are inconclusive
CLOCK_CONSISTENCY_MAX = 0.05  # blocks whose wall/OCXO ratio is off by more than
                              # this are overhead-dominated (dialogs, tune) and
                              # useless at the 1e-7 level: excluded from the fit

# Requirement tiers for absolute-frequency (axion-search) use of the data.
# Each entry: (tier id, name, fractional requirement, note).
CLOCK_TIERS = (
    ("i", "detection + per-site exclusion", 6.0e-7,
     "the axion virial linewidth (~6e-7 fractional, ~380 Hz at 600 MHz) "
     "dominates; a stock OCXO is fine"),
    ("ii", "mass-scale labeling", 1.0e-6,
     "a 1 ppm clock error is a 1 ppm axion-mass error; a stock OCXO is "
     "fine provided the offset is recorded -- which this audit does"),
    ("iii", "cross-site coincidence", 1.0e-7,
     "sites must agree to under ~1e-7; aged OCXOs can miss, which is "
     "exactly what this audit screens for"),
    ("iv", "sidereal-Doppler signature", 1.2e-9,
     "~1.2e-9 fractional (~0.7 Hz at 600 MHz); beyond any software audit "
     "of practical span -- needs disciplined or per-record-calibrated "
     "clocks (GPSDO reference input, or a GPSDO-locked pilot tone)"),
)


# ============================================================================
# Bruker readers (zip-resident)
# ============================================================================

def parse_jcamp(text):
    """Parse a Bruker JCAMP-DX parameter file into {name: value}.

    Scalars become float/int/str; <bracketed> strings are unwrapped;
    array blocks (e.g. the delay list D) are stored as whitespace-joined
    raw strings -- split() to index them.
    """
    out = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"##\$?([A-Za-z0-9_]+)=\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if re.match(r"^\(\d+\.\.\d+\)$", val) or (
                    val.startswith("(") and ")" in val
                    and not val.endswith(")")):
                # array header like (0..63); values on following lines
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
                    # string checks FIRST: real acqus carry DBL_MAX
                    # sentinels ('1.79769313486232e+308', parsed as inf)
                    # for unset doubles, and int(inf) raises OverflowError
                    fv = float(val)
                    out[key] = int(fv) if "." not in val \
                        and "e" not in val.lower() and fv == int(fv) else fv
                except (ValueError, OverflowError):
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

    def acqu2s(self, expno):
        p = "data/%d/acqu2s" % expno
        if not self.has(p):
            return {}
        try:
            return parse_jcamp(self.read(p).decode("utf-8", "replace"))
        except Exception:
            return {}

    def pulseprogram_text(self, expno):
        """The pulse-program text TopSpin stores in the expno dir, or None."""
        p = "data/%d/pulseprogram" % expno
        if not self.has(p):
            return None
        try:
            return self.read(p).decode("utf-8", "replace")
        except Exception:
            return None

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


# ============================================================================
# v0.6 analysis riders
#
# (a) Persistent-line catalog: every narrow excess in every readable
#     noise block, classified by how it moves when the carrier moves.
#     Under the v0.6 carrier-follow sweep the receiver window tracks
#     each field step, so across steps a SPIN line stays near its
#     baseline window position, a RECEIVER-CHAIN spur stays fixed in the
#     window frame, and an ABSOLUTE-frequency line (external RFI, a
#     console clock spur -- or a dark-matter candidate) marches through
#     the window by minus the carrier shift. This 3-way separation is
#     the groundwork for the dark-photon line search and the spur
#     catalog; without carrier diversity the window/absolute split is
#     recorded as indeterminate.
#
# (b) Sub-virial pass: a native-resolution (1/T_row) mean periodogram of
#     the headline noise block with a narrow-candidate list.
#     INFRASTRUCTURE ONLY: a real sub-virial dark-matter feature chirps
#     with Earth's rotation (~0.9 Hz over a night at 600 MHz), so no
#     physics claim is possible without the diurnal chirp templates --
#     which this pass does not yet apply, and says so.
# ============================================================================

CATALOG_NSIGMA = 5.0          # feature threshold, stacked-PSD sigmas
CATALOG_DC_EXCLUDE_HZ = 50.0  # skip the DC/carrier-leakage region
CATALOG_CLUSTER_TOL_HZ = 3.0  # same-line tolerance across blocks
CATALOG_MAX_LISTED = 40
SUBVIRIAL_NSIGMA = 6.0
SUBVIRIAL_MAX_WIDTH_HZ = 1.0
SUBVIRIAL_MAX_LISTED = 30


def _block_features(res, f0_local, w_ref):
    """Narrow excess features in one block's stacked normalized PSD:
    [{window_hz, excess, width_hz, is_spin}]. DC and band edges are
    excluded; 'is_spin' tags features within 3 linewidths of the
    block's expected line position."""
    rows = res.get("_rows") or []
    if not rows:
        return []
    f = rows[0]["f"]
    stack = np.mean([rr["pnorm"] for rr in rows], axis=0)
    edge = res.get("edge_hz") or 0.45 * res.get("fs_hz", 12000.0)
    core = (np.abs(f) < edge) & (np.abs(f) > CATALOG_DC_EXCLUDE_HZ)
    if not np.any(core):
        return []
    sig = robust_sigma(stack[core])
    if not sig or not np.isfinite(sig):
        return []
    hot = core & (stack - 1.0 > CATALOG_NSIGMA * sig)
    feats = []
    i = 0
    idx = np.where(hot)[0]
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
            j += 1
        seg = idx[i:j + 1]
        wts = stack[seg] - 1.0
        center = float(np.sum(f[seg] * wts) / np.sum(wts))
        spin_tol = 3.0 * max(w_ref or 0.0, 5.0)
        feats.append({
            "window_hz": center,
            "excess": float(np.max(stack[seg]) - 1.0),
            "excess_nsigma": float((np.max(stack[seg]) - 1.0) / sig),
            "width_hz": float(f[seg[-1]] - f[seg[0]]) if len(seg) > 1
                        else float(f[1] - f[0]),
            "is_spin": abs(center - f0_local) < spin_tol,
        })
        i = j + 1
    return feats


def _cluster_1d(items, key, tol):
    """Greedy 1-d clustering of dicts by items[key] within tol."""
    out = []
    for ft in sorted(items, key=lambda x: x[key]):
        if out and abs(ft[key] - out[-1]["center"]) <= tol:
            out[-1]["members"].append(ft)
            out[-1]["center"] = float(np.mean(
                [m[key] for m in out[-1]["members"]]))
        else:
            out.append({"center": float(ft[key]), "members": [ft]})
    return out


def persistent_line_catalog(catalog_blocks, w_ref):
    """3-way persistent-line classification across noise blocks.

    catalog_blocks: [{expno, res, carrier_shift_hz, f0_local}] --
    carrier_shift_hz is how far the receiver window moved from the
    baseline carrier (0 for standard blocks and legacy fixed-carrier
    sweeps; the step target under carrier-follow)."""
    all_feats = []
    for b in catalog_blocks:
        for ft in _block_features(b["res"], b["f0_local"], w_ref):
            ft["expno"] = b["expno"]
            ft["carrier_shift_hz"] = float(b["carrier_shift_hz"])
            ft["absolute_hz"] = ft["window_hz"] + float(
                b["carrier_shift_hz"])
            all_feats.append(ft)
    shifts = sorted(set(round(b["carrier_shift_hz"], 1)
                        for b in catalog_blocks))
    diversity = len(shifts) > 1
    catalog = {"n_blocks": len(catalog_blocks),
               "carrier_shifts_hz": shifts,
               "carrier_diversity": diversity,
               "n_features_raw": len(all_feats),
               "lines": [],
               "note": (
                   "classification: 'spin_line' = tracks the expected "
                   "line position; 'window_fixed' = constant offset "
                   "from the (moving) carrier -> receiver-chain spur; "
                   "'absolute_fixed' = constant absolute frequency -> "
                   "external RFI, console clock spur, or dark-matter "
                   "candidate; 'persistent_same_shift_indeterminate' = "
                   "repeated, but only in blocks sharing one carrier "
                   "shift, so the frames cannot be separated for it; "
                   "'single_block' = seen once, unclassifiable. "
                   "Window/absolute discrimination "
                   "requires carrier diversity (a carrier-follow sweep); "
                   "this session %s." % (
                       "has it" if diversity else
                       "does NOT have it -- window and absolute frames "
                       "coincide"))}
    nonspin = [ft for ft in all_feats if not ft["is_spin"]]
    spin = [ft for ft in all_feats if ft["is_spin"]]
    spin_tol = 3.0 * max(w_ref or 0.0, 5.0)
    if diversity and spin:
        # Under carrier-follow the spin line IS window-fixed, so any
        # per-block remnant that escaped its own block's is_spin tag
        # (mis-seeded fit, weak row) must not cluster into a
        # "receiver-chain spur" (review F4): fold everything near the
        # mean spin window position back into the spin group.
        mean_spin_w = float(np.mean([ft["window_hz"] for ft in spin]))
        keep = []
        for ft in nonspin:
            if abs(ft["window_hz"] - mean_spin_w) < spin_tol:
                ft["is_spin"] = True
                spin.append(ft)
            else:
                keep.append(ft)
        nonspin = keep
    if spin:
        catalog["lines"].append({
            "class": "spin_line",
            "n_blocks_seen": len(set(ft["expno"] for ft in spin)),
            "mean_window_hz": float(np.mean(
                [ft["window_hz"] for ft in spin])),
            "max_excess_nsigma": float(np.max(
                [ft["excess_nsigma"] for ft in spin]))})
    used = set()
    for frame, cls in (("window_hz", "window_fixed"),
                       ("absolute_hz", "absolute_fixed")):
        for cl in _cluster_1d([ft for ft in nonspin
                               if id(ft) not in used],
                              frame, CATALOG_CLUSTER_TOL_HZ):
            expnos = set(m["expno"] for m in cl["members"])
            csh = set(round(m["carrier_shift_hz"], 1)
                      for m in cl["members"])
            accept_cls = None
            if len(expnos) >= 2 and (len(csh) >= 2 or not diversity):
                accept_cls = cls
                if not diversity:
                    accept_cls = "persistent_frame_indeterminate"
            elif (len(expnos) >= 2 and diversity and len(csh) == 1
                  and frame == "window_hz"):
                # persistent across blocks that share one carrier shift
                # (e.g. the standard noise block + the target-0 step):
                # real and repeated, but the frames cannot be separated
                # for it (review F5)
                accept_cls = "persistent_same_shift_indeterminate"
            if accept_cls is None:
                continue
            for m in cl["members"]:
                used.add(id(m))
            catalog["lines"].append({
                "class": accept_cls,
                "n_blocks_seen": len(expnos),
                "center_hz": cl["center"],
                "frame": frame.replace("_hz", ""),
                "max_excess_nsigma": float(np.max(
                    [m["excess_nsigma"] for m in cl["members"]]))})
        if not diversity:
            break     # one pass is meaningful without carrier diversity
    singles = [ft for ft in nonspin if id(ft) not in used]
    for ft in sorted(singles, key=lambda x: -x["excess_nsigma"]
                     )[:CATALOG_MAX_LISTED]:
        catalog["lines"].append({
            "class": "single_block", "expno": ft["expno"],
            "window_hz": ft["window_hz"],
            "absolute_hz": ft["absolute_hz"],
            "excess_nsigma": ft["excess_nsigma"]})
    catalog["n_lines_listed"] = len(catalog["lines"])
    return catalog


def subvirial_pass(bundle, exp, f0_local, w_ref, fs_default):
    """Native-resolution mean periodogram of one noise block + narrow
    candidates. Infrastructure pass -- no chirp templates yet."""
    rows, acq = bundle.read_rows(exp["expno"], exp)
    if rows is None or rows.shape[0] == 0:
        return None
    fs = float(acq.get("SW_h", exp.get("sw_hz", fs_default)))
    n = int(rows.shape[1])
    if n < 4096:
        return None
    # truncate each row to the largest power of two: real console TDs
    # often carry large prime factors, which push numpy's FFT onto the
    # slow Bluestein path (minutes per row instead of seconds). The
    # resolution loss is < 2x and irrelevant for a candidate list.
    n = 1 << (n.bit_length() - 1)
    win = np.hanning(n)
    ps = np.zeros(n)
    for x in rows:
        ps += np.abs(np.fft.fftshift(np.fft.fft(x[:n] * win))) ** 2
    ps /= float(rows.shape[0])
    f = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / fs))
    df = fs / n
    # coarse baseline: chunked medians, interpolated
    chunk = max(1024, n // 2048)
    nb = n // chunk
    fb = np.array([np.mean(f[i * chunk:(i + 1) * chunk])
                   for i in range(nb)])
    bb = np.array([np.median(ps[i * chunk:(i + 1) * chunk])
                   for i in range(nb)])
    bb[bb <= 0] = np.min(bb[bb > 0]) if np.any(bb > 0) else 1.0
    base = np.interp(f, fb, bb)
    pn = ps / base
    edge = 0.45 * fs
    spin_tol = 3.0 * max(w_ref or 0.0, 5.0)
    core = ((np.abs(f) < edge) & (np.abs(f) > CATALOG_DC_EXCLUDE_HZ)
            & (np.abs(f - f0_local) > spin_tol))
    if not np.any(core):
        return {"expno": exp["expno"], "error": "empty analysis band"}
    sig = robust_sigma(pn[core])
    if not sig or not np.isfinite(sig):
        # degenerate spectrum (clipped/constant rows): without this
        # guard a zero sigma reaches divisions and puts Infinity/NaN
        # into report.json, which strict parsers reject (review F6)
        return {"expno": exp["expno"],
                "error": "degenerate spectrum (zero/NaN sigma)"}
    cands = []
    idx = np.where(core & (pn - 1.0 > SUBVIRIAL_NSIGMA * sig))[0]
    i = 0
    while i < len(idx) and len(cands) < 10 * SUBVIRIAL_MAX_LISTED:
        j = i
        while j + 1 < len(idx) and idx[j + 1] == idx[j] + 1:
            j += 1
        seg = idx[i:j + 1]
        width = (f[seg[-1]] - f[seg[0]]) if len(seg) > 1 else df
        if width <= SUBVIRIAL_MAX_WIDTH_HZ:
            wts = pn[seg] - 1.0
            cands.append({
                "window_hz": float(np.sum(f[seg] * wts) / np.sum(wts)),
                "width_hz": float(width),
                "excess_nsigma": float((np.max(pn[seg]) - 1.0) / sig)})
        i = j + 1
    cands.sort(key=lambda c: -c["excess_nsigma"])
    return {"expno": exp["expno"], "resolution_hz": df,
            "n_rows": int(rows.shape[0]), "sigma_norm": float(sig),
            "n_candidates": len(cands),
            "candidates": cands[:SUBVIRIAL_MAX_LISTED],
            "note": (
                "native-resolution (%.3g Hz) incoherent mean periodogram "
                "of the headline noise block; candidates are narrow "
                "(<= %.1f Hz) excesses away from the spin line and DC. "
                "INFRASTRUCTURE PASS ONLY: no diurnal/annual chirp "
                "templates are applied yet, and a genuine sub-virial "
                "dark-matter line chirps by ~1 Hz per night from Earth's "
                "rotation -- treat every candidate as an instrumental "
                "spur hypothesis until the template search exists."
                % (df, SUBVIRIAL_MAX_WIDTH_HZ))}


def axion_mass_bookkeeping(meta):
    """Per-site mass coordinate and coupling-conversion factors for the
    downstream (coordinator-side) limit pipeline. h = 4.135667696e-15
    eV s: 1 MHz of carrier = 4.135667696e-3 ueV of axion mass."""
    spec = meta.get("spectrometer") or {}
    f_mhz = spec.get("observe_freq_mhz") or spec.get("h1_freq_mhz")
    if not f_mhz:
        return None
    m_uev = float(f_mhz) * 4.135667696e-3
    m_gev = m_uev * 1e-15
    return {
        "observe_freq_mhz": float(f_mhz),
        "axion_mass_coordinate_uev": m_uev,
        "axial_vector_conversion_gev": m_gev * 1e-3,
        "note": (
            "mass coordinate of this session's carrier; the same "
            "candidate lines and limits reinterpret to axial-vector "
            "dark matter via g_A = g_aNN[GeV^-1] * (%.3e GeV) -- the "
            "factor is m_a * v with v = 1e-3 c. No velocity "
            "suppression applies to the axial-vector coupling, which "
            "is why the identical data are 3 orders of magnitude "
            "more constraining there (see the network science "
            "roadmap)." % (m_gev * 1e-3))}


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
# Clock audit (schema 1.2): console-clock offset from wall-vs-OCXO elapsed
# ============================================================================

# The expectation refinement derives each block's OCXO-implied duration
# from the pulse-program text TopSpin stored in that expno (bundled by
# the run script's copy_tree) plus the acqus parameters -- NOT from a
# name-keyed table of assumed structures: the text is the record of what
# the pulse programmer actually executed. The parser is deliberately
# conservative: any statement it does not recognize, any ambiguous loop
# structure, NS != 1, or DS != 0 makes it fall back to the recorded
# expectation, flagged per block. The wall/OCXO consistency gate then
# arbitrates empirically: a wrong timing model shows up as a wall
# mismatch and excludes the block loudly instead of biasing the fit.

_PP_LABEL = re.compile(r"^(\d+)\s+(.*)$")
_PP_GO = re.compile(r"^go=(\d+)$")
_PP_DELAY = re.compile(r"^d(\d+)$")
_PP_PULSE = re.compile(r"^p(\d+)$")
_PP_LITERAL = re.compile(r"^(\d+(?:\.\d+)?)(s|m|u|n)$")
_PP_UNIT_S = {"s": 1.0, "m": 1e-3, "u": 1e-6, "n": 1e-9}
# Zero-duration bookkeeping tokens: write/zero/loop control, phase
# references and phase-program definitions ('ph31' / 'ph31=0' /
# 'ph1 = 0 2'), loop targets and counts.
_PP_ZERO = re.compile(r"^(ze|zd|wr|if|mc|lo|to|times|exit|=|"
                      r"ph\d+(=.*)?|#\d*|td\d*|\d+|"
                      r"F\d\([A-Za-z0-9_]*\))$")


def _jcamp_array(acq, key):
    """A JCAMP array (e.g. D, P) as a list of floats, or []."""
    try:
        return [float(v) for v in str(acq.get(key, "")).split()]
    except (TypeError, ValueError):
        return []


def pp_timing_model(pp_text, rows):
    """Parse pulse-program text into (pre_terms, row_terms) timing lists.

    Terms are ('d', N) / ('p', N) / ('lit', seconds) / ('go',): the
    programmed delays, pulses, and acquisitions of one pass. row_terms
    execute once per row (the go-loop body, delimited by the go target
    label and the lo/mc line that jumps back to it); pre_terms execute
    once. Statements sharing a line with a delay run concurrently with
    it (the 'd1 wr #0 if #0 ze' idiom), so a line's duration is its
    single duration token. Returns (None, None, reason) when the text
    cannot be modeled with certainty -- unknown statement, two durations
    on one line, no go, or a missing loop when rows > 1.
    """
    lines, labels = [], {}
    for raw in pp_text.splitlines():
        code = raw.split(";", 1)[0].strip()
        if not code or code.startswith("#"):    # blank / preprocessor
            continue
        m = _PP_LABEL.match(code)
        if m:
            labels[int(m.group(1))] = len(lines)
            code = m.group(2).strip()
        lines.append(code)

    per_line, go_idx = [], []
    for i, code in enumerate(lines):
        dur = None
        for t in code.split():
            if _PP_GO.match(t):
                term = ("go",)
            elif _PP_DELAY.match(t):
                term = ("d", int(_PP_DELAY.match(t).group(1)))
            elif _PP_PULSE.match(t):
                term = ("p", int(_PP_PULSE.match(t).group(1)))
            elif _PP_LITERAL.match(t):
                m = _PP_LITERAL.match(t)
                term = ("lit", float(m.group(1)) * _PP_UNIT_S[m.group(2)])
            elif _PP_ZERO.match(t):
                continue
            else:
                return None, None, "unrecognized statement '%s'" % t
            if dur is not None:
                return None, None, "two durations on one line: '%s'" % code
            dur = term
        if dur == ("go",):
            go_idx.append(i)
        per_line.append(dur)

    if len(go_idx) != 1:
        return None, None, ("expected exactly one go statement, found %d"
                            % len(go_idx))
    m = _PP_GO.match([t for t in lines[go_idx[0]].split()
                      if _PP_GO.match(t)][0])
    target = int(m.group(1))
    if target not in labels:
        return None, None, "go target label %d not found" % target
    start = labels[target]
    close = None
    for i in range(go_idx[0] + 1, len(lines)):
        toks = lines[i].split()
        if ("lo" in toks or "mc" in toks) and "to" in toks:
            try:
                if int(toks[toks.index("to") + 1]) == target:
                    close = i
                    break
            except (ValueError, IndexError):
                return None, None, "unparseable loop close: '%s'" % lines[i]
    if close is None:
        if rows > 1:
            return None, None, ("no loop back to label %d but rows > 1"
                                % target)
        close = len(lines) - 1
    pre = [d for d in per_line[:start] if d is not None]
    row = [d for d in per_line[start:close + 1] if d is not None]
    return pre, row, None


def refined_block_expectation(bundle, exps_by_no, block):
    """Recompute one clock-audit block's OCXO-implied duration from the
    bundle's own pulse-program text and acqus parameters.

    Everything the pulse programmer executes is OCXO-clocked, so every
    programmed delay, pulse, and the per-scan pre-acquisition delay DE
    belong on the predicted side of the fit; a per-scan shortfall eps
    biases the fitted offset by ~eps/(scan duration) -- ~3e-7 even for a
    stock 6.5 us DE on ~19 s rows (the real 2020 console used 59.4 us),
    which matters at requirement tiers ii/iii. The acquisition-side
    formula in spin_noise_run.py models only AQ and d1; here the bundled
    pulseprogram text supplies the actual per-row structure and acqus
    supplies the parameter values (D/P arrays, DE, TD, SW_h). Row counts
    come from meta['experiments'], with acqu2s TD as fallback. Anything
    the conservative parser cannot model keeps the recorded expectation,
    flagged per block; the wall/OCXO consistency gate arbitrates any
    residual model error empirically.

    Returns (refined_seconds or None, info dict for the report row).
    """
    rec = block.get("ocxo_expected_s")
    try:
        expno = int(block.get("expno"))
    except (TypeError, ValueError):
        return None, {"refine_note": "non-numeric expno"}
    acq = bundle.acqus(expno)
    if not acq:
        return None, {"refine_note": "no acqus readable for this expno"}
    pp_text = bundle.pulseprogram_text(expno)
    if pp_text is None:
        return None, {"refine_note": "no pulseprogram text in the bundle "
                                     "for this expno"}
    exp_meta = exps_by_no.get(block.get("expno")) or {}
    try:
        td = int(acq["TD"])
        swh = float(acq["SW_h"])
        de_s = float(acq["DE"]) * 1e-6           # acqus stores DE in us
        ns = int(acq.get("NS", 1) or 1)
        ds = int(acq.get("DS", 0) or 0)
    except (KeyError, TypeError, ValueError):
        return None, {"refine_note": "acqus lacks TD/SW_h/DE"}
    try:
        rows = int(exp_meta.get("td1_rows", 0) or 0)
    except (TypeError, ValueError):
        rows = 0
    if rows <= 0:
        try:
            rows = int(bundle.acqu2s(expno).get("TD", 0) or 0)
        except (TypeError, ValueError):
            rows = 0
    if td <= 0 or swh <= 0 or rows <= 0 or de_s < 0:
        return None, {"refine_note": "non-physical acqus parameters or "
                                     "unknown row count"}
    if ns != 1:
        return None, {"refine_note": "NS=%d: per-scan loop semantics not "
                                     "modeled (network protocol is NS=1)"
                                     % ns}
    if ds != 0:
        return None, {"refine_note": "DS=%d dummy scans are not in the "
                                     "timing model" % ds}
    pre, row, why = pp_timing_model(pp_text, rows)
    if why is not None:
        return None, {"refine_note": "pulseprogram not modelable: %s" % why}
    aq = td / (2.0 * swh)
    dvals = _jcamp_array(acq, "D")
    pvals = _jcamp_array(acq, "P")

    def term_seconds(terms):
        total = 0.0
        for t in terms:
            if t[0] == "go":
                total += de_s + aq
            elif t[0] == "lit":
                total += t[1]
            elif t[0] == "d":
                if t[1] >= len(dvals):
                    raise IndexError("D%d not in acqus D array" % t[1])
                total += dvals[t[1]]
            elif t[0] == "p":
                if t[1] >= len(pvals):
                    raise IndexError("P%d not in acqus P array" % t[1])
                total += pvals[t[1]] * 1e-6      # P array is microseconds
        return total

    try:
        refined = term_seconds(pre) + rows * term_seconds(row)
    except IndexError as e:
        return None, {"refine_note": str(e)}
    if refined <= 0:
        return None, {"refine_note": "non-positive modeled duration"}
    info = {"de_s_per_scan": de_s, "scans": rows,
            "pulprog": str(acq.get("PULPROG", "")).strip("<> "),
            "pp_row_terms": len(row)}
    if isinstance(rec, (int, float)) and rec > 0:
        # informational only -- the wall gate is the arbiter; a large
        # value here usually means the acquisition-side formula missed
        # programmed delays that the pulse-program text reveals
        info["recorded_discrepancy"] = refined / rec - 1.0
    return refined, info


def analyze_clock_audit(meta, bundle=None):
    """Fit the fractional console-clock offset from the bundle's clock_audit
    blocks.

    Model: wall_i = c + (1 + delta) * ocxo_i across usable blocks, where
    ocxo_i is the OCXO-implied acquisition duration and wall_i the
    NTP-disciplined wall-clock elapsed time. The intercept c absorbs the
    constant per-block housekeeping overhead (disk writes etc., not
    OCXO-derived); delta is the fractional clock offset. Per-point sigma is
    sqrt(2)*NTP_JITTER_S (two timestamps per block). Blocks with no OCXO
    prediction (setup) or with wall/OCXO mismatch beyond
    CLOCK_CONSISTENCY_MAX (overhead-dominated) are excluded and listed.

    When `bundle` is given, each block's expected duration is re-derived
    from its bundled pulse-program text plus acqus via
    refined_block_expectation(); blocks that cannot be modeled with
    certainty keep the script-recorded value, flagged. When any block is
    refined, the recorded-model fit is reported alongside for comparison
    under 'recorded_model'.
    """
    out = {"available": False}
    ca = meta.get("clock_audit")
    if not isinstance(ca, dict) or not isinstance(ca.get("blocks"), list) \
            or not ca.get("blocks"):
        out["note"] = (
            "no clock audit in this bundle -- it predates the audit "
            "(recorded from script v0.2 / schema 1.2 onward). No penalty: "
            "the absolute-clock tiers are simply unassessed for this "
            "session.")
        return out
    out["available"] = True
    out["workstation_time_source"] = str(
        ca.get("workstation_time_source", "unknown"))
    out["ntp_status_raw"] = str(ca.get("ntp_status_raw", ""))[:2000]

    exps_by_no = {}
    for e in (meta.get("experiments") or []):
        if isinstance(e, dict) and e.get("expno") is not None:
            exps_by_no[e.get("expno")] = e

    usable, rows = [], []
    t0_ms, t1_ms = None, None
    for b in ca["blocks"]:
        try:
            ws = int(b["wall_start_ms"])
            we = int(b["wall_end_ms"])
            wall_s = (we - ws) / 1000.0
        except Exception:
            rows.append({"expno": b.get("expno"), "role": b.get("role"),
                         "used": False, "why": "unreadable wall times"})
            continue
        t0_ms = ws if t0_ms is None else min(t0_ms, ws)
        t1_ms = we if t1_ms is None else max(t1_ms, we)
        rec = b.get("ocxo_expected_s")
        exp, src, rinfo = rec, "script-recorded", {}
        if bundle is not None and isinstance(rec, (int, float)) and rec > 0:
            refined, rinfo = refined_block_expectation(bundle, exps_by_no, b)
            if refined is not None:
                exp, src = refined, "acqus-refined"
        row = {"expno": b.get("expno"), "role": b.get("role"),
               "wall_s": wall_s, "ocxo_expected_s": exp,
               "ocxo_recorded_s": rec, "expected_source": src}
        row.update(rinfo)
        if exp is None:
            row.update({"used": False,
                        "why": "no OCXO prediction (tune/shim/dialog time)"})
        elif not isinstance(exp, (int, float)) or exp <= 0 or wall_s <= 0:
            row.update({"used": False, "why": "non-positive duration"})
        elif abs(wall_s / exp - 1.0) > CLOCK_CONSISTENCY_MAX:
            row.update({"used": False,
                        "why": "wall/OCXO mismatch %.1f%% -- overhead-"
                               "dominated, unusable at the 1e-7 level"
                               % (100.0 * abs(wall_s / exp - 1.0))})
        else:
            row.update({"used": True,
                        "block_offset": wall_s / exp - 1.0})
            usable.append((float(exp), wall_s, float(rec),
                           src == "acqus-refined"))
        rows.append(row)
    out["blocks"] = rows
    out["n_blocks"] = len(rows)
    out["n_usable"] = len(usable)
    span_s = ((t1_ms - t0_ms) / 1000.0) if (t0_ms is not None) else 0.0
    out["session_span_s"] = span_s
    out["audited_ocxo_s"] = float(sum(p[0] for p in usable))

    if not usable:
        out["status"] = ("clock audit present but no usable blocks; no "
                         "offset fit possible")
        out["conclusive"] = False
        return out

    def _offset_fit(pairs):
        """wall = c + (1+delta)*ocxo over (ocxo_s, wall_s) pairs; errors
        from the KNOWN per-point sigma sqrt(2)*NTP_JITTER_S (two
        timestamps per block), not from residual scatter."""
        fx = np.array([p[0] for p in pairs])
        fy = np.array([p[1] for p in pairs])
        sigma_pt = math.sqrt(2.0) * NTP_JITTER_S
        f = {}
        if len(pairs) >= 3 and float(np.ptp(fx)) > 1.0:
            # OLS with intercept
            xb, yb = float(np.mean(fx)), float(np.mean(fy))
            sxx = float(np.sum((fx - xb) ** 2))
            slope = float(np.sum((fx - xb) * (fy - yb)) / sxx)
            f["delta"] = slope - 1.0
            f["err"] = sigma_pt / math.sqrt(sxx)
            f["model"] = ("wall = c + (1+delta)*ocxo, intercept absorbs "
                          "constant per-block overhead")
            f["intercept_s"] = yb - slope * xb
        else:
            # too few blocks for an intercept: inverse-variance mean of
            # the per-block ratios (overhead then biases delta high)
            d = fy / fx - 1.0
            w = (fx / sigma_pt) ** 2
            f["delta"] = float(np.sum(w * d) / np.sum(w))
            f["err"] = float(1.0 / math.sqrt(np.sum(w)))
            f["model"] = ("weighted mean of per-block ratios (too few "
                          "blocks for an intercept; per-block overhead "
                          "biases the offset high)")
        return f

    fit = _offset_fit([(p[0], p[1]) for p in usable])
    delta, delta_err = fit["delta"], fit["err"]
    out["fit_model"] = fit["model"]
    if "intercept_s" in fit:
        out["overhead_intercept_s"] = fit["intercept_s"]
    out["fractional_offset"] = float(delta)
    out["fractional_offset_err"] = float(delta_err)
    out["assumed_timestamp_jitter_s"] = NTP_JITTER_S

    n_refined = sum(1 for p in usable if p[3])
    tot_ocxo = sum(p[0] for p in usable)
    unref_ocxo = sum(p[0] for p in usable if not p[3])
    out["expectation_refinement"] = {
        "n_refined": n_refined,
        "n_recorded_only": len(usable) - n_refined,
        "unrefined_usable_ocxo_fraction":
            (unref_ocxo / tot_ocxo) if tot_ocxo > 0 else None,
        "note": ("expected durations re-derived from each block's bundled "
                 "pulse-program text plus acqus (every programmed delay, "
                 "pulse, and the per-scan pre-acquisition delay DE); the "
                 "acquisition-side formula models only AQ and d1, and a "
                 "per-scan shortfall biases the offset by "
                 "~shortfall/scan-duration"),
    }
    if n_refined and tot_ocxo > 0 and unref_ocxo / tot_ocxo > 0.05:
        out["expectation_refinement"]["caution"] = (
            "refinement is partial and the unrefined blocks carry %.0f%% "
            "of the audited OCXO time -- the fit mixes two duration "
            "models; treat fine-tier verdicts with care"
            % (100.0 * unref_ocxo / tot_ocxo))
    if n_refined:
        corr = [(p[0] - p[2]) / p[2] for p in usable if p[3] and p[2] > 0]
        if corr:
            out["expectation_refinement"]["median_fractional_correction"] \
                = float(np.median(np.array(corr)))
        rfit = _offset_fit([(p[2], p[1]) for p in usable])
        out["recorded_model"] = {
            "fractional_offset": float(rfit["delta"]),
            "fractional_offset_err": float(rfit["err"]),
            "fit_model": rfit["model"],
            "note": ("the same usable blocks fitted against the "
                     "acquisition-side (AQ+d1 only) expected durations, "
                     "for comparison; the headline fit uses the "
                     "pulse-program-derived durations"),
        }

    out["conclusive"] = bool(span_s >= CLOCK_MIN_SPAN_S)
    if not out["conclusive"]:
        out["status"] = ("clock audit inconclusive (short session): span "
                         "%.0f s < %.0f s; numbers below are reported but "
                         "should not be used for tier claims"
                         % (span_s, CLOCK_MIN_SPAN_S))
    else:
        out["status"] = ("fractional console-clock offset %.3g +/- %.3g "
                         "over a %.2f h session span"
                         % (delta, delta_err, span_s / 3600.0))

    bound = abs(delta) + delta_err
    out["offset_bound"] = float(bound)
    tiers = []
    for tid, name, req, note in CLOCK_TIERS:
        t = {"tier": tid, "name": name, "requirement": req, "note": note}
        if not out["conclusive"]:
            t["verdict"] = "unassessed (audit inconclusive)"
        elif delta_err > req:
            t["verdict"] = ("audit precision insufficient (+/-%.1g > %.1g "
                            "requirement) -- needs a longer session"
                            % (delta_err, req))
        elif bound <= req:
            t["verdict"] = "satisfied (|offset|+err = %.2g <= %.1g)" \
                % (bound, req)
        else:
            t["verdict"] = ("NOT satisfied: measured offset %.2g exceeds "
                            "the %.1g requirement" % (bound, req))
        tiers.append(t)
    out["tiers"] = tiers
    return out


def render_clock_audit_html(clock):
    """HTML fragment for the clock-audit section (used by both the science
    and the software-test report paths)."""
    parts = []
    A = parts.append
    A("<h2>Clock audit (console OCXO vs workstation NTP)</h2>"
      "<div class='card'>")
    if not clock.get("available"):
        A("<p class='muted'>%s</p></div>" % esc(clock.get("note", "")))
        return "".join(parts)
    A("<p class='small'>Acquisition durations derive from the console's "
      "OCXO master clock; the workstation wall clock is normally "
      "NTP-disciplined. Fitting wall-clock elapsed against OCXO-implied "
      "elapsed across the session's blocks measures the fractional "
      "console-clock offset with zero extra hardware. Precision scales as "
      "the %.0f ms timestamp jitter over the audited span.</p>"
      % (1000 * clock.get("assumed_timestamp_jitter_s", NTP_JITTER_S)))
    if clock.get("fractional_offset") is not None:
        cls = "" if clock.get("conclusive") else " class='warn'"
        A("<p%s><span class='big'>%+.3g &plusmn; %.2g</span> fractional "
          "console-clock offset <span class='small'>(%s)</span></p>"
          % (cls, clock["fractional_offset"], clock["fractional_offset_err"],
             esc(clock.get("fit_model", ""))))
    A("<p class='%s'>%s</p>" % ("small" if clock.get("conclusive")
                                else "warn", esc(clock.get("status", ""))))
    A("<p class='small'>session span %.2f h &middot; audited OCXO time "
      "%.0f s &middot; %d of %d blocks usable &middot; time source: "
      "<code>%s</code></p>"
      % (clock.get("session_span_s", 0) / 3600.0,
         clock.get("audited_ocxo_s", 0), clock.get("n_usable", 0),
         clock.get("n_blocks", 0),
         esc(clock.get("workstation_time_source", "unknown"))))
    er = clock.get("expectation_refinement") or {}
    if er.get("n_refined"):
        med = er.get("median_fractional_correction")
        rm = clock.get("recorded_model") or {}
        extra = ""
        if med is not None:
            extra += " &middot; median DE correction %+.2e" % med
        if rm.get("fractional_offset") is not None:
            extra += (" &middot; recorded-model fit for comparison: "
                      "%+.3g &plusmn; %.2g"
                      % (rm["fractional_offset"],
                         rm["fractional_offset_err"]))
        A("<p class='small'>expected durations re-derived from the "
          "bundled pulse-program text + acqus for %d of %d usable "
          "blocks%s</p>"
          % (er["n_refined"],
             er["n_refined"] + er.get("n_recorded_only", 0), extra))
        if er.get("caution"):
            A("<p class='warn'>%s</p>" % esc(er["caution"]))
    if clock.get("blocks"):
        A("<table><tr><th>expno</th><th>role</th><th>wall (s)</th>"
          "<th>OCXO expected (s)</th><th>expected source</th>"
          "<th>used</th><th>per-block offset</th></tr>")
        for b in clock["blocks"]:
            off = b.get("block_offset")
            src_html = esc(b.get("expected_source", ""))
            if b.get("refine_note"):
                src_html += (" <span class='small'>(%s)</span>"
                             % esc(b["refine_note"]))
            A("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
              "<td>%s</td><td>%s</td><td>%s</td></tr>"
              % (fmt(b.get("expno")), esc(b.get("role", "?")),
                 fmt(b.get("wall_s"), 6), fmt(b.get("ocxo_expected_s"), 6),
                 src_html,
                 "yes" if b.get("used") else
                 "no &mdash; %s" % esc(b.get("why", "")),
                 ("%+.2e" % off) if off is not None else "&mdash;"))
        A("</table>")
    if clock.get("tiers"):
        A("<p class='small'><b>Requirement tiers</b> (absolute-frequency "
          "use of this site's records):</p>")
        A("<table><tr><th>tier</th><th>requirement</th><th>fractional"
          "</th><th>verdict</th></tr>")
        for t in clock["tiers"]:
            v = t["verdict"]
            cls = "ok" if v.startswith("satisfied") else (
                "fail" if v.startswith("NOT") else "warn")
            A("<tr><td>%s</td><td>%s <span class='small'>(%s)</span></td>"
              "<td>%.1e</td><td class='%s'>%s</td></tr>"
              % (esc(t["tier"]), esc(t["name"]), esc(t["note"]),
                 t["requirement"], cls, esc(v)))
        A("</table>")
    if clock.get("ntp_status_raw"):
        A("<p class='small'>NTP status probe (verbatim):</p>"
          "<pre class='small' style='white-space:pre-wrap; overflow-x:auto'>"
          "<code>%s</code></pre>" % esc(clock["ntp_status_raw"][:600]))
    A("</div>")
    return "".join(parts)


def render_rdopt_html(ctx):
    """HTML fragment for the rd-optimize tuning scan (empty when the
    feature did not run). Falls back to the meta object so software-test
    reports show it too."""
    ro = ctx.get("rd_optimize")
    if not isinstance(ro, dict):
        ro = (ctx["meta"].get("calibration") or {}).get("rd_optimize")
    if not isinstance(ro, dict) or not ro.get("enabled"):
        return ""
    parts = []
    A = parts.append
    A("<h2>rd-optimize (probe-tuning scan)</h2><div class='card'>")
    A("<p class='small'>Small-flip FID envelope decay rate vs probe-"
      "tuning offset: the envelope decays at 1/T2* + lambda_r, so with "
      "the shim fixed the largest rate marks the strongest radiation "
      "damping. The session ran at the chosen offset.</p>")
    offs = ro.get("offsets_khz") or []
    rates = ro.get("decay_rates_per_s") or []
    amps = ro.get("amplitudes") or []
    exps = ro.get("scan_expnos") or []
    if offs:
        A("<table><tr><th>offset (kHz)</th><th>decay rate (1/s)</th>"
          "<th>amplitude (counts)</th><th>expno</th></tr>")
        for i in range(len(offs)):
            A("<tr><td>%+.0f</td><td>%s</td><td>%s</td><td>%s</td></tr>"
              % (offs[i],
                 fmt(rates[i] if i < len(rates) else None),
                 fmt(amps[i] if i < len(amps) else None, 0),
                 fmt(exps[i] if i < len(exps) else None)))
        A("</table>")
    A("<p><b>chosen offset: %+.0f kHz</b> <span class='small'>&mdash; "
      "%s</span></p>"
      % (ro.get("chosen_offset_khz") or 0.0, esc(ro.get("note", ""))))
    A("</div>")
    return "".join(parts)


def render_line_catalog_html(ctx):
    """HTML fragment for the v0.6 persistent-line catalog + sub-virial
    pass + mass bookkeeping (empty when none ran)."""
    cat = ctx.get("line_catalog")
    sub = ctx.get("subvirial")
    mb = ctx.get("mass_book")
    if not (cat or sub or mb):
        return ""
    parts = []
    A = parts.append
    A("<h2>Persistent-line catalog (dark-photon / spur groundwork)</h2>")
    if mb:
        A("<p>Axion-mass coordinate of this session: "
          "<b>%s &micro;eV</b> (carrier %s MHz). Axial-vector "
          "reinterpretation factor g<sub>A</sub>/g<sub>aNN</sub> = "
          "%.3e GeV.</p>"
          % (fmt(mb.get("axion_mass_coordinate_uev"), 6),
             fmt(mb.get("observe_freq_mhz"), 6),
             mb.get("axial_vector_conversion_gev") or 0.0))
    if cat:
        A("<p class='note'>%s</p>" % esc(cat.get("note", "")))
        A("<table><tr><th>class</th><th>frame</th><th>center (Hz)</th>"
          "<th>blocks seen</th><th>max excess (&sigma;)</th></tr>")
        for ln in cat.get("lines", []):
            A("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
              "<td>%s</td></tr>"
              % (esc(ln.get("class", "")),
                 esc(str(ln.get("frame", ln.get("expno", "")))),
                 fmt(ln.get("center_hz", ln.get("window_hz",
                     ln.get("mean_window_hz"))), 1),
                 ln.get("n_blocks_seen", 1),
                 fmt(ln.get("max_excess_nsigma",
                            ln.get("excess_nsigma")), 1)))
        A("</table>")
    if sub and not sub.get("error"):
        A("<h3>Sub-virial pass (headline block, %s Hz resolution)</h3>"
          % fmt(sub.get("resolution_hz"), 4))
        A("<p class='note'>%s</p>" % esc(sub.get("note", "")))
        cands = sub.get("candidates") or []
        if cands:
            A("<table><tr><th>window offset (Hz)</th><th>width (Hz)</th>"
              "<th>excess (&sigma;)</th></tr>")
            for c in cands[:15]:
                A("<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
                  % (fmt(c["window_hz"], 2), fmt(c["width_hz"], 3),
                     fmt(c["excess_nsigma"], 1)))
            A("</table>")
        else:
            A("<p>No narrow candidates above %.0f&sigma;.</p>"
              % SUBVIRIAL_NSIGMA)
    elif sub and sub.get("error"):
        A("<p class='note'>sub-virial pass failed: %s</p>"
          % esc(sub["error"]))
    return "".join(parts)


def render_field_sweep_html(ctx):
    """HTML fragment for the field-stepped sweep (empty when the feature
    did not run). Uses the science-path per-step analysis when present,
    else the raw meta object (software-test reports)."""
    fs = ctx.get("field_sweep")
    if not isinstance(fs, dict):
        m = ctx["meta"].get("field_sweep")
        if not isinstance(m, dict) or not m.get("enabled"):
            return ""
        fs = {"steps": m.get("steps") or [], "note": m.get("note", ""),
              "restored_offset_hz": m.get("restored_offset_hz"),
              "headline_expno": None}
    steps = fs.get("steps") or []
    parts = []
    A = parts.append
    A("<h2>Field-stepped sweep (one mass point per step)</h2>"
      "<div class='card'>")
    if fs.get("note"):
        A("<p class='small'>%s</p>" % esc(fs.get("note", "")))
    cf = bool(fs.get("carrier_follow"))
    if steps:
        lc_head = "line center (Hz)"
        if cf:
            lc_head = "line center (Hz, local window)"
        A("<table><tr><th>step</th><th>target (Hz)</th>"
          "<th>measured (Hz)</th><th>basis</th><th>dev (Hz)</th>"
          "<th>rows</th><th>noise expno</th>"
          "<th>%s</th><th>FWHM (Hz)</th></tr>" % lc_head)
        for i in range(len(steps)):
            s = steps[i]
            meas = fmt(s.get("measured_offset_hz"), 1)
            if s.get("offset_basis") == "unverified":
                meas = "&mdash; <span class='small'>(unverified)</span>"
            A("<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td>"
              "<td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
              "<td>%s</td></tr>"
              % (i + 1, fmt(s.get("target_offset_hz"), 1), meas,
                 esc(s.get("offset_basis", "")),
                 fmt(s.get("local_deviation_hz"), 1),
                 fmt(s.get("rows")),
                 fmt(s.get("noise_expno", s.get("expno"))),
                 fmt(s.get("line_center_hz"), 2),
                 fmt(s.get("fwhm_hz"), 2)))
        A("</table>")
        for s in steps:
            if s.get("orchestrator_note"):
                A("<p class='small'>step expno %s: %s</p>"
                  % (fmt(s.get("noise_expno", s.get("expno"))),
                     esc(s["orchestrator_note"])))
    restored = fs.get("restored_offset_hz")
    if restored is not None:
        A("<p class='small'>field restored to %+.1f Hz of baseline after "
          "the ladder</p>" % restored)
    if fs.get("headline_expno") is not None:
        A("<p class='small'>headline science block: expno %s (the step "
          "nearest the baseline field)</p>" % fs["headline_expno"])
    A("</div>")
    return "".join(parts)


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
    nuc_txt = ""
    if spec.get("observe_nucleus") and spec.get("observe_nucleus") != "1H":
        nuc_txt = (" &middot; observed nucleus: %s at %.6g MHz"
                   % (esc(spec["observe_nucleus"]),
                      float(spec.get("observe_freq_mhz", 0) or 0)))
    A("<div class='muted'>%s, %s, %s &middot; %s &middot; %.6g MHz "
      "(1H-equivalent, &approx;%.4g T)%s &middot; probe: %s (%s)</div>"
      % (esc(fac.get("institution", "?")), esc(fac.get("city", "?")),
         esc(fac.get("country", "?")), esc(spec.get("console", "?")),
         float(spec.get("h1_freq_mhz", 0) or 0),
         float(spec.get("field_tesla", 0) or 0), nuc_txt,
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
        # The clock audit is timestamp plumbing, not spin physics, so it IS
        # analyzed for software-test bundles -- the harness uses exactly
        # this to validate the offset fit against a known injected offset.
        # On a desk machine the recorded wall times are the desk's, so the
        # numbers describe the test host, never a spectrometer.
        A(render_clock_audit_html(ctx["clock"]))
        A(render_rdopt_html(ctx))
        A(render_field_sweep_html(ctx))
        A(render_line_catalog_html(ctx))
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

    # ---- clock audit
    A(render_clock_audit_html(ctx["clock"]))

    # ---- optional features (empty fragments when absent)
    A(render_rdopt_html(ctx))
    A(render_field_sweep_html(ctx))
    A(render_line_catalog_html(ctx))

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

    # ---- clock audit (both report types: it is timestamp plumbing, not
    # spin physics, and the harness validates the fit on desktest bundles)
    clock = analyze_clock_audit(meta, bundle)
    report["clock_audit"] = strip_private(clock)

    # ---- 2. run-mode gate
    if run_mode in SOFTWARE_TEST_MODES:
        report["report_type"] = "software-test"
        report["science"] = None
        report["note"] = ("run_mode '%s': plumbing test, not data; science "
                          "analysis refused by design" % run_mode)
        ctx = {"report_type": "software-test", "meta": meta,
               "bundle_path": bundle_path, "validation_msgs": val_msgs,
               "validation_ok": val_ok, "names": sorted(bundle.names),
               "clock": clock}
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

    # ---- 3. noise blocks: a single 'noise' block, or the field-stepped
    # 'noise_sweep' ladder. Every sweep step is its own axion mass point
    # (mass coordinate = measured carrier + step offset). Each step's
    # line is fitted AT ITS OWN SHIFTED POSITION (fit_line searches only
    # +/-LINE_SEARCH_HZ around its seed, so seeding at the baseline
    # would fit noise for every shifted step); the sign of the shift on
    # this PSD axis is resolved empirically by fitting both candidates
    # and keeping the more significant one. Steps whose offset was never
    # MEASURED are not line-fitted at all -- an unverified target is not
    # a position. The measured step nearest baseline doubles as the
    # headline science block.
    sweep_meta = meta.get("field_sweep") or {}
    sweep_steps_meta = sweep_meta.get("steps") or []

    def _sweep_step_of(expno):
        for s in sweep_steps_meta:
            if s.get("noise_expno") == expno:
                return s
        return None

    def _sweep_measured_of(expno):
        s = _sweep_step_of(expno)
        return s.get("measured_offset_hz") if s else None

    def _fit_significance(r):
        tot = 0.0
        for pr in r["per_row"]:
            ft = pr.get("fit")
            if ft and ft.get("amp_err"):
                tot += min(abs(ft["amp_norm"]) / ft["amp_err"], 10.0)
        return tot

    def _analyze_sweep_step(e, off):
        """Fit at both sign candidates of the measured offset; keep the
        candidate whose per-row line fits are more significant (a real
        line beats a noise fit decisively). Returns (result, seed).

        v0.6 carrier-follow bundles: the receiver window moved WITH the
        step, so the line sits near its baseline LOCAL position and the
        seed is the small residual deviation (measured - target), not
        the full offset -- the sign candidates are the deviation's."""
        step = _sweep_step_of(e.get("expno"))
        if step and step.get("carrier_o1_hz") is not None:
            # Prefer the RECORDED local deviation: when the orchestrator
            # substituted measured = target (sign-unresolved exception),
            # off - target is 0 while the line really sits at the
            # deviation -- seeding at 0 would let a noise fit pass as a
            # measured step (review finding F1, 2026-09-03). Also lets
            # unverified steps (off None) fit at their known local seed.
            dev = step.get("local_deviation_hz")
            if dev is None and off is not None:
                dev = off - (step.get("target_offset_hz") or 0.0)
            dev = dev or 0.0
            cands = [dev, -dev] if dev else [0.0]
        else:
            cands = [off, -off] if off else [0.0]
        best, best_seed, best_score = None, None, -1.0
        for c in cands:
            r = analyze_noise_block(bundle, e, f0_guess + c, fs_default)
            if r is None:
                continue
            score = _fit_significance(r)
            if score > best_score:
                best, best_seed, best_score = r, c, score
        return best, best_seed

    noise_exps = list(by_role.get("noise", []))
    sweep_exps = sorted(by_role.get("noise_sweep", []),
                        key=lambda e: e.get("expno", 0))
    cf_mode = bool(sweep_meta.get("carrier_follow"))
    frame_indeterminate = []
    analyzed = {}         # expno -> (result_or_None, seed_offset_or_None)
    for e in noise_exps:
        analyzed[e.get("expno")] = (analyze_noise_block(
            bundle, e, f0_guess, fs_default), 0.0)
    for e in sweep_exps:
        expno = e.get("expno")
        off = _sweep_measured_of(expno)
        step = _sweep_step_of(expno)
        has_carrier = bool(step and step.get("carrier_o1_hz") is not None)
        if cf_mode and not has_carrier:
            # the sweep declares carrier-follow but this step does not
            # say where its window was: no frame to fit in (review F2)
            analyzed[expno] = (None, None)
            frame_indeterminate.append(expno)
        elif off is None and not has_carrier:
            analyzed[expno] = (None, None)   # v0.5: unverified, no fit
        else:
            # carrier-follow steps fit at their known LOCAL seed even
            # when the step offset was never verified (review F4): the
            # window position is commanded digitally, so the local
            # frame is exact regardless of field verification.
            analyzed[expno] = _analyze_sweep_step(e, off)

    headline_order = noise_exps + sorted(
        [e for e in sweep_exps
         if _sweep_measured_of(e.get("expno")) is not None],
        key=lambda e: abs(_sweep_measured_of(e.get("expno"))))
    noise_res, f0_detect = None, f0_guess
    for e in headline_order:
        res, seed = analyzed.get(e.get("expno"), (None, None))
        if res is not None:
            noise_res = res
            f0_detect = f0_guess + (seed or 0.0)
            break

    sweep_analysis = None
    if sweep_exps:
        if cf_mode:
            note = ("carrier-follow sweep (v0.6): the receiver window "
                    "tracked each step, so per-step line centers are in "
                    "the LOCAL moved-window frame (expected near the "
                    "baseline position) and the physical step offset is "
                    "measured_offset_hz in the baseline-field frame; "
                    "the sign convention was resolved once at baseline "
                    "by the carrier-displacement calibration; steps "
                    "whose window position is unknown are not fitted; "
                    "the headline science analysis uses the measured "
                    "step nearest the baseline field")
        else:
            note = ("field-stepped sweep: every step is a distinct "
                    "axion mass point, line-fitted at its own measured "
                    "offset (sign resolved empirically per step); "
                    "steps without a measured offset are listed but "
                    "not fitted; the headline science analysis uses "
                    "the measured step nearest the baseline field")
        sweep_analysis = {
            "headline_expno": (noise_res or {}).get("expno"),
            "carrier_follow": cf_mode,
            "sign_convention_basis":
                sweep_meta.get("sign_convention_basis"),
            "baseline_line_offset_hz":
                sweep_meta.get("baseline_line_offset_hz"),
            "restored_offset_hz": sweep_meta.get("restored_offset_hz"),
            "field_restored": sweep_meta.get("field_restored"),
            "note": note,
            "steps": []}
        if frame_indeterminate:
            sweep_analysis["frame_indeterminate_expnos"] = \
                frame_indeterminate
        unsigned_basis = (
            sweep_meta.get("sign_convention_basis") == "unresolved")
        for e in sweep_exps:
            expno = e.get("expno")
            r, seed = analyzed.get(expno, (None, None))
            off = _sweep_measured_of(expno)
            basis = "unverified"
            if off is not None:
                basis = "measured"
                if unsigned_basis:
                    # the documented v0.6 exception: measured == target,
                    # confirmed only by an unsigned deviation
                    basis = "target_confirmed_unsigned"
            entry = {"expno": expno, "measured_offset_hz": off,
                     "offset_basis": basis,
                     "rows": e.get("td1_rows"), "readable": bool(r)}
            for s in sweep_steps_meta:
                if s.get("noise_expno") == expno:
                    entry["target_offset_hz"] = s.get("target_offset_hz")
                    if s.get("local_deviation_hz") is not None:
                        entry["local_deviation_hz"] = s.get(
                            "local_deviation_hz")
                    if s.get("note"):
                        entry["orchestrator_note"] = s.get("note")
                    if s.get("carrier_o1_hz") is not None:
                        entry["carrier_o1_hz"] = s.get("carrier_o1_hz")
                        entry["lock_shift_target_ppm"] = s.get(
                            "lock_shift_target_ppm")
            if expno in frame_indeterminate:
                entry["note"] = ("sweep declares carrier_follow but this "
                                 "step carries no carrier_o1_hz: window "
                                 "frame unknown, no line fit (QA WARN)")
            elif off is None and entry.get("carrier_o1_hz") is not None:
                entry["note"] = ("step offset never verified -- no "
                                 "mass-point position is claimed; the "
                                 "line is still fitted in the (exactly "
                                 "known) local window frame so the "
                                 "block serves the line catalog")
            elif off is None:
                entry["note"] = ("offset never measured (verification "
                                 "failed or was skipped): no line fit -- "
                                 "an unverified target is not a position")
            if r:
                entry["fit_seed_offset_hz"] = seed
                # line position: mean of per-row fit centers (the coadd
                # fit is in the self-aligned frame and carries only a
                # residual shift)
                centers = [pr["fit"]["center_hz"] for pr in r["per_row"]
                           if "fit" in pr]
                if centers:
                    entry["line_center_hz"] = float(np.mean(centers))
                    entry["line_center_spread_hz"] = float(
                        max(centers) - min(centers))
                if "coadd_fit" in r:
                    cf = r["coadd_fit"]
                    entry["fwhm_hz"] = cf.get("fwhm_hz")
                    entry["amp_norm"] = cf.get("amp_norm")
                    entry["amp_err"] = cf.get("amp_err")
            sweep_analysis["steps"].append(entry)

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
        # (shifted by the headline sweep step's measured offset when the
        # headline block is a field-stepped one)
        r0 = noise_res["_rows"][0]
        # co-add unaligned (no feature to align on)
        stack = np.mean([rr["pnorm"] for rr in noise_res["_rows"]], axis=0)
        ul, ul_details = upper_limit_at(
            r0["f"], stack, f0_detect,
            [max(0.5 * w_ref, 2.0), w_ref, 2.0 * w_ref])
        detection["upper_limit_95_amp"] = ul
        detection["upper_limit_details"] = ul_details
        detection["upper_limit_note"] = (
            "95%% one-sided limit on |feature amplitude| relative to the "
            "floor, at the reference-anchored line position%s, profiled "
            "over widths 0.5/1/2 x the reference linewidth (%.1f Hz)"
            % ((" shifted %+.1f Hz for the headline sweep step"
                % (f0_detect - f0_guess))
               if abs(f0_detect - f0_guess) > 1e-9 else "", w_ref))

    # ---- v0.6 riders: persistent-line catalog, sub-virial pass, and
    # the axion-mass / axial-vector bookkeeping.
    catalog_blocks = []
    base_o1 = sweep_meta.get("baseline_carrier_o1_hz")
    for e in noise_exps + sweep_exps:
        expno = e.get("expno")
        r, seed = analyzed.get(expno, (None, None))
        if not r:
            continue
        step = _sweep_step_of(expno)
        shift = 0.0
        if step and step.get("carrier_o1_hz") is not None:
            # exact commanded window shift; fall back to the target for
            # writers that record carrier_o1_hz without the baseline
            if base_o1 is not None:
                shift = float(step["carrier_o1_hz"]) - float(base_o1)
            else:
                shift = float(step.get("target_offset_hz") or 0.0)
        # spin tag anchored on the FITTED line position when available:
        # a mis-seeded block must not let the spin line masquerade as a
        # window-fixed spur (review F4)
        f0_loc = f0_guess + (seed or 0.0)
        centers = [pr["fit"]["center_hz"] for pr in r["per_row"]
                   if "fit" in pr]
        if centers:
            f0_loc = float(np.mean(centers))
        catalog_blocks.append({"expno": expno, "res": r,
                               "carrier_shift_hz": shift,
                               "f0_local": f0_loc})
    line_catalog = (persistent_line_catalog(catalog_blocks, w_ref)
                    if catalog_blocks else None)
    subvirial = None
    if noise_res:
        head_exp = None
        for e in noise_exps + sweep_exps:
            if e.get("expno") == noise_res.get("expno"):
                head_exp = e
                break
        if head_exp is not None:
            try:
                subvirial = subvirial_pass(bundle, head_exp, f0_detect,
                                           w_ref, fs_default)
            except Exception as exc:
                subvirial = {"error": str(exc)}
    mass_book = axion_mass_bookkeeping(meta)

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
    if frame_indeterminate:
        qa.append({"level": "WARN",
                   "check": "carrier-follow frame consistency",
                   "detail": ("field_sweep declares carrier_follow but "
                              "steps %s carry no carrier_o1_hz -- their "
                              "window frame is unknown and they were "
                              "not line-fitted"
                              % frame_indeterminate)})

    # ---- honesty section
    honesty = [
        "Determined: the receiver's measured noise spectrum around the "
        "observed line, the feature contrast (or its upper limit), "
        "linewidth, "
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
    if (sweep_analysis
            and sweep_meta.get("sign_convention_basis") == "unresolved"):
        honesty.append("Sweep sign convention UNRESOLVED for this session: "
                       "per-step offsets labeled 'target_confirmed_"
                       "unsigned' are the commanded targets, confirmed "
                       "only by an unsigned deviation within the "
                       "substitution cap -- they are not signed "
                       "measurements (the documented v0.6 exception to "
                       "the measured-not-target rule).")

    figs = make_figures(noise_res, refs, ladder, detection)
    ctx = {"report_type": report["report_type"], "meta": meta,
           "bundle_path": bundle_path, "validation_msgs": val_msgs,
           "validation_ok": val_ok, "noise": noise_res, "refs": refs,
           "ladder": ladder, "detection": detection, "headline": headline,
           "temp_contrast": temp_contrast, "floor_cal": floor_cal,
           "qa": qa, "figs": figs, "honesty": honesty,
           "names": sorted(bundle.names), "clock": clock,
           "field_sweep": sweep_analysis,
           "line_catalog": line_catalog, "subvirial": subvirial,
           "mass_book": mass_book,
           "rd_optimize": (meta.get("calibration") or {}).get("rd_optimize")}
    html = render_html(ctx)

    report["science"] = strip_private({
        "line_position_guess_hz": f0_guess,
        "reference_linewidth_hz": w_ref,
        "noise": noise_res, "references": refs, "rg_ladder": ladder,
        "detection": detection, "headline": headline,
        "temperature_contrast": temp_contrast, "floor_calibration": floor_cal,
        "field_sweep": sweep_analysis,
        "line_catalog": line_catalog,
        "subvirial_pass": subvirial,
        "axion_mass_bookkeeping": mass_book,
        "rd_optimize": (meta.get("calibration") or {}).get("rd_optimize"),
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
