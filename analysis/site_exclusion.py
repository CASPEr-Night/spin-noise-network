#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site_exclusion.py -- combine the per-session worst-case axion-coupling
exclusions of ONE facility into the site's running exclusion curve.

    python3 analysis/site_exclusion.py report_a.json report_b.json ... [--out DIR]

Input : report.json files written by analysis/facility_report.py (a
        report directory is accepted in place of its report.json). Every
        report must carry the same facility_slug --- combining across
        sites is refused, that is the coordinator's job. Reports without
        a usable science.axion_exclusion (software-test reports, sessions
        whose exclusion was unavailable, curves with non-finite or
        non-positive couplings, exact duplicates of a bundle already
        given) are skipped and listed.
Output: site_exclusion_<slug>.json (inputs, per-session summaries, the
        combined sign-robust best coupling and its mass, the combined
        within-10x band and every contiguous within-10x segment, the
        same headline under each axis-sign hypothesis, total noise
        seconds, sessions used and skipped, the combined curves) and
        site_exclusion_<slug>.txt (m_a [eV], g_site, one g column per
        sign hypothesis, then one g column per session) in --out
        (default: the directory of the first report).

Headline curve (v0.8, analysis note Sec. 8.5, option [B]). When every
session used carries curve.g90_wind_conservative --- the standard-halo
wind at its actual direction for that session's site and times, D_cal
applied --- the site headline minimises THAT curve
(combined.g90_wind_conservative_best_gev_inv, its mass and offset), and
curve.g90_wind_nominal (D_cal = 1) is combined the same way when every
session carries it too (combined.g90_wind_nominal_best_gev_inv). The
worst-parallel curve g90_worst (wind parallel to B0, the v0.7 headline)
is ALWAYS combined by the same rule and kept as
combined.g90_worst_best_gev_inv --- the robustness line and the
continuity with the released v0.7 numbers. When any session used lacks
the wind curve (a report predating v0.8, or a session whose wind model
was unavailable: halo.wind_model_unavailable) the headline falls back to
today's g90_worst combination, unchanged, and rule, headline_basis and
headline_curve say so --- construction is then the v0.7 text verbatim
(analysis note Sec. 8.5 names site_combined.rule as the carrier; the
Sec. 7 (iv) allow-list does not include this string). Every v0.7 key
keeps its name, position and meaning; the wind-aware keys are additive.

Combination rule. Every session's curve g_90(m_a) (each curve family
treated alike) is placed
on the site's common mass grid --- the nodes at integer multiples of
SITE_GRID_STEP_HZ (the per-session scan step) in nu_a inside each
session's scanned band, their union over sessions, so that N sessions
at one carrier share ~1100 nodes instead of N x 1100 and sessions at
different carriers add ~1100 nodes each with nothing filled in between
--- by the weaker of the two bracketing native points (never an
interpolation below the curve), +inf outside a session's scanned band
(no statement). Under one axis-sign hypothesis h the site curve is the
pointwise MINIMUM over sessions:

    g_h(m_a) = min_s g_90^(s)(m_a | h).

Each session's worst-case bound is an inequality a putative signal at
m_a must satisfy, so the minimum is the joint worst-case bound where the
bounds are set by the spin-noise line power --- no session's power is
added to another's, no session is reweighted.

Axis sign. A session whose frequency-axis sign is unverified (Agilent/
VnmrJ, vendor checklist item 2) places its line at carrier + offset
(nominal) or carrier - offset (mirror). The combiner takes the sign as
ONE shared unknown per vendor axis convention --- not as an unknown per
session --- on the PREMISE that every sign-unverified session of that
vendor at the site was acquired under one convention (one console, one
acquisition-software version, the offset read the same way in every
bundle), so that one measurement fixes the sign for all of them. It
therefore enumerates the 2^k joint hypotheses h over the k
sign-unverified vendors (a sign-verified session contributes the same
curve under every h), computes g_h for each, and quotes as the site
curve the SIGN-ROBUST

    g_site(m_a) = max_h g_h(m_a),

defined only where every hypothesis makes a statement (other nodes are
dropped): under the premise the bound at m_a holds whichever sign is
true. The data do not check the premise: a second console, another
software version or a per-experiment axis reversal at one site breaks
it, and the shared rule is then tighter than the truth wherever it beats
the independent-sign rule (each session the pointwise maximum over its
two placements, then the minimum over sessions; max-min <= min-max, so
the shared rule is never looser). combined.sign_premise states the
premise, what breaks it, and the independent-sign headline that holds
without it (combined.independent_sign). The headline under each
hypothesis is quoted beside it (combined.if_sign), and
combined.sign_note states by what factor the robust headline is weaker
than the tighter conditional one: the unverified sessions' two
placements sit twice the line offset apart, so a bound that must hold
at one mass under either sign is set by the weaker placement, and a
single sign determination for the vendor collapses the site bound to
the conditional value at the corresponding mass. With no sign-unverified
session there is one hypothesis and g_site is the plain minimum. More
than MAX_SIGN_VENDORS sign-unverified vendors (never seen in practice)
fall back to the independent-sign rule, and the note says so.

Coverage caveat: each g_90^(s) is a one-sided 90% statement, and where a
session's P_90 is fluctuation-driven (a null block: statistical term 30%
or more of P_90) the minimum over N such sessions selects the luckiest
downward fluctuation and its coverage falls toward 0.9^N. The
per-session statistical fraction is recorded, its maximum over the
sessions is quoted with the combined number, and the site curve is a 90%
CL statement only where the sessions that set it are line-power
dominated.

The combination is deterministic and idempotent (the same inputs in any
order give the same combined curve, and the outputs carry no timestamp),
and while the sign-unverified vendors number at most MAX_SIGN_VENDORS
adding a report can only tighten the curve where the sessions overlap or
extend it where they do not (every g_h can only fall, and a new
sign-unverified vendor doubles the hypotheses without raising any of
them). The one exception is the fallback boundary: the report that
brings the (MAX_SIGN_VENDORS + 1)-th sign-unverified vendor switches the
rule to the independent-sign one, which is never tighter, so the curve
can loosen there (never seen in practice; rule and sign_note say when
it has happened). This is the mechanism by which the site's exclusion
updates as it takes more data: after every session, run facility_report on the new
bundle with --prior-reports over the site's earlier report.json files
(the new report then carries science.axion_exclusion.site_combined,
without the per-session and per-hypothesis curves), or run this script
over all of them. facility_report imports this module lazily by file
path.

Every number here is a conservative construction (2020 pilot: D_cal from
the pilot's 4.6 envelope, inflated by the ladder power envelope where a
session's references were gain-bridged; the standard-halo wind at its
actual direction for each session's site and times in the headline,
wind parallel to B0 in the worst-parallel robustness line; damping at
the broader measured width, no spin-noise subtraction), unpublished, and
many orders of magnitude above the astrophysical bounds on the same
coupling.

Python 3 + numpy only.
"""

from __future__ import print_function

import argparse
import itertools
import json
import os
import sys

import numpy as np

EV_PER_HZ = 4.135667696e-15
# the per-session scan step (facility_report.EXCL_SCAN_HZ[2]) --- the
# site grid is anchored at integer multiples of it in absolute nu_a
SITE_GRID_STEP_HZ = 4.0
STAT_FRACTION_FLUCTUATION = 0.30
SIGN_HYPOTHESES = ("nominal", "mirror")
# 2^k joint sign hypotheses are enumerated for k sign-unverified vendors;
# beyond this the independent-sign rule (max over placements per
# session) is used instead
MAX_SIGN_VENDORS = 3
# sign-group key of a sign-unverified session whose vendor is unrecorded
# (it shares its sign with no other session)
UNRECORDED_VENDOR_PREFIX = "unrecorded-vendor:"
# curve families (keys of science.axion_exclusion.curve): the v0.7
# worst-parallel curve is always combined; the v0.8 wind-aware curves
# (analysis note Sec. 8.5) are combined when every session carries them
WORST_CURVE = "g90_worst"
WIND_CONSERVATIVE_CURVE = "g90_wind_conservative"
WIND_NOMINAL_CURVE = "g90_wind_nominal"
WIND_DIRECTION_TEXT = ("the standard-halo wind at its actual direction for "
                       "each session's site and times")
WORST_DIRECTION_TEXT = "wind parallel to B0"


class SiteMismatch(ValueError):
    """Reports from more than one facility_slug were given together."""


def load_report(path):
    """(path, report dict) from a report.json or a report directory."""
    p = os.path.abspath(path)
    if os.path.isdir(p):
        p = os.path.join(p, "report.json")
    with open(p, "r") as fh:
        rep = json.load(fh)
    if not isinstance(rep, dict):
        raise ValueError("top level of %s is %s, not an object"
                         % (p, type(rep).__name__))
    return p, rep


def session_exclusion(report):
    """The usable science.axion_exclusion of a report, or (None, why)."""
    sci = report.get("science")
    if not isinstance(sci, dict):
        return None, "no science section (report_type %s)" % report.get(
            "report_type")
    ex = sci.get("axion_exclusion")
    if not isinstance(ex, dict):
        return None, ("no science.axion_exclusion (report generator v%s "
                      "predates it)" % report.get("report_version"))
    if not ex.get("available"):
        return None, "exclusion unavailable: %s" % ex.get("reason", "?")
    curve = ex.get("curve") or {}
    m = curve.get("m_a_ev")
    g = curve.get("g90_worst")
    if not m or not g or len(m) != len(g):
        return None, "exclusion carries no curve"
    mm = curve.get("m_a_ev_mirror")
    if mm is not None and len(mm) != len(m):
        return None, "mirror mass axis does not match the curve"
    ga = np.asarray(g, dtype=float)
    ma = np.asarray(m, dtype=float)
    if not (np.all(np.isfinite(ga)) and np.all(ga > 0)
            and np.all(np.isfinite(ma)) and np.all(ma > 0)
            and (mm is None or np.all(np.isfinite(np.asarray(mm, float))))):
        return None, ("curve carries non-finite or non-positive values "
                      "(%d of %d couplings unusable)"
                      % (int(np.count_nonzero(~(np.isfinite(ga)
                                                 & (ga > 0)))), ga.size))
    return ex, None


def _usable_curve(values, n):
    """(array, None) when values is a list of n finite positive numbers,
    else (None, why)."""
    try:
        ga = np.asarray(values, dtype=float)
    except (TypeError, ValueError):
        return None, "not numeric"
    if ga.ndim != 1 or ga.size != n:
        return None, "%d values against %d masses" % (ga.size, n)
    bad = ~(np.isfinite(ga) & (ga > 0))
    if bad.any():
        return None, ("%d of %d couplings non-finite or non-positive"
                      % (int(np.count_nonzero(bad)), ga.size))
    return ga, None


def session_wind_curves(ex, report=None):
    """The usable wind-aware curves of a usable exclusion (analysis note
    Sec. 8.5): {conservative: array or None, nominal: array or None,
    why: why the conservative curve is absent or unusable (None when
    usable), nominal_why: likewise for the D_cal = 1 companion}."""
    curve = ex.get("curve") or {}
    version = (report or {}).get("report_version") or "unrecorded"
    n = len(curve.get("m_a_ev") or [])
    out = {"conservative": None, "nominal": None, "why": None,
           "nominal_why": None}
    for fam, key, why_key in (("conservative", WIND_CONSERVATIVE_CURVE,
                               "why"),
                              ("nominal", WIND_NOMINAL_CURVE,
                               "nominal_why")):
        g = curve.get(key)
        if g is None:
            halo = ex.get("halo") if isinstance(ex.get("halo"), dict) else {}
            if halo.get("wind_model_unavailable"):
                out[why_key] = ("curve.%s absent: wind model unavailable (%s)"
                                % (key, halo["wind_model_unavailable"]))
            else:
                out[why_key] = ("curve.%s absent: the report predates it "
                                "(generator v%s)" % (key, version))
            continue
        ga, why = _usable_curve(g, n)
        if ga is None:
            out[why_key] = "curve.%s unusable: %s" % (key, why)
            continue
        out[fam] = ga
    return out


def session_vendor(report, ex):
    """The vendor whose frequency-axis convention a session's line
    offsets follow: axion_exclusion.line.vendor, else the report's
    science.frequency_axis_sign or raw_data_read vendor, else None."""
    v = (ex.get("line") or {}).get("vendor")
    sci = report.get("science") if isinstance(report.get("science"),
                                              dict) else {}
    for key in ("frequency_axis_sign", "raw_data_read"):
        if v:
            break
        sub = sci.get(key)
        v = sub.get("vendor") if isinstance(sub, dict) else None
    return str(v).lower() if v else None


def session_label(path, report):
    """A session name from the bundle file name (its timestamp and hash
    once the 'spinnoise_<slug>_' prefix is dropped), falling back to the
    report directory's name."""
    bundle = report.get("bundle")
    if isinstance(bundle, str) and bundle:
        stem = bundle[:-4] if bundle.lower().endswith(".zip") else bundle
        prefix = "spinnoise_%s_" % report.get("facility_slug")
        if stem.startswith(prefix) and len(stem) > len(prefix):
            return stem[len(prefix):]
        return stem
    return os.path.basename(os.path.dirname(path)) or path


def session_summary(label, path, report, ex):
    res = ex.get("result") or {}
    line = ex.get("line") or {}
    sp = ex.get("signal_power") or {}
    dc = ex.get("calibration_derating") or {}
    bands = res.get("band_10x_uev") or {}
    verified = line.get("sign_verified")
    return {
        "label": label,
        "report": path,
        "bundle": report.get("bundle"),
        "bundle_sha256": report.get("bundle_sha256"),
        "generated_utc": report.get("generated_utc"),
        "report_version": report.get("report_version"),
        "vendor": session_vendor(report, ex),
        "carrier_mhz": line.get("carrier_mhz"),
        "sign_verified": verified,
        "g90_worst_best_gev_inv": res.get("g90_worst_best_gev_inv"),
        "m_a_at_best_uev": res.get("m_a_at_best_uev"),
        "offset_at_best_hz": res.get("offset_at_best_hz"),
        "band_10x_uev": (bands.get("intersection") if verified is False
                         else bands.get("nominal")),
        "band_10x_basis": ("intersection of the two sign hypotheses"
                           if verified is False else "nominal mass axis"),
        "D_cal": dc.get("D_cal"),
        "D_cal_pilot": dc.get("D_cal_pilot"),
        "statistical_fraction_of_P90": sp.get("statistical_fraction_of_P90"),
        "noise_seconds": sp.get("noise_seconds"),
        "n_rows": sp.get("n_rows"),
        # v0.8 additive (analysis note Sec. 8.5): the session's wind-aware
        # headline, its D_cal = 1 companion, and the wind angle range
        "g90_wind_conservative_best_gev_inv":
            res.get("g90_wind_conservative_best_gev_inv"),
        "offset_at_best_wind_conservative_hz":
            res.get("offset_at_best_wind_conservative_hz"),
        "g90_wind_nominal_best_gev_inv":
            res.get("g90_wind_nominal_best_gev_inv"),
        "offset_at_best_wind_nominal_hz":
            res.get("offset_at_best_wind_nominal_hz"),
        "wind_theta_deg_range": _theta_range(ex),
        "wind_model_unavailable": ((ex.get("halo") or {})
                                   .get("wind_model_unavailable")
                                   if isinstance(ex.get("halo"), dict)
                                   else None),
        "wind_curve": (session_wind_curves(ex, report)["conservative"]
                       is not None),
    }


def _theta_range(ex):
    """[theta_min, theta_max] in degrees from halo.wind_model, else None."""
    halo = ex.get("halo") if isinstance(ex.get("halo"), dict) else {}
    wm = halo.get("wind_model") if isinstance(halo.get("wind_model"),
                                              dict) else {}
    lo, hi = wm.get("theta_min_deg"), wm.get("theta_max_deg")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
        return [float(lo), float(hi)]
    return None


def _nodes_hz(nu_lo, nu_hi, step=SITE_GRID_STEP_HZ):
    """Integer multiples of step inside [nu_lo, nu_hi] (Hz)."""
    k_lo = int(np.ceil(nu_lo / step - 1e-9))
    k_hi = int(np.floor(nu_hi / step + 1e-9))
    return np.arange(k_lo, k_hi + 1, dtype=float) * step


def _resample_weaker(nu_native, g_native, nodes, step=SITE_GRID_STEP_HZ):
    """g at each node from the native curve: the native value at a
    coincident point, else the LARGER of the two bracketing points (a
    bound, never an interpolation below the curve) --- +inf outside."""
    order = np.argsort(nu_native)
    nu, g = nu_native[order], g_native[order]
    out = np.full(nodes.shape, np.inf)
    inside = (nodes >= nu[0] - 1e-6 * step) & (nodes <= nu[-1] + 1e-6 * step)
    idx = np.searchsorted(nu, nodes[inside], side="right")
    left = np.clip(idx - 1, 0, nu.size - 1)
    right = np.clip(idx, 0, nu.size - 1)
    on_point = np.abs(nodes[inside] - nu[left]) <= 1e-6 * step
    out[inside] = np.where(on_point, g[left], np.maximum(g[left], g[right]))
    return out


def _segments(nodes, mask, step=SITE_GRID_STEP_HZ):
    """[[lo, hi], ...] in Hz of the runs of consecutive nodes with mask."""
    ks = np.round(nodes / step).astype(np.int64)
    segs, start = [], None
    for i in range(nodes.size):
        if mask[i] and start is None:
            start = i
        if start is not None and (not mask[i] or i == nodes.size - 1
                                  or ks[i + 1] != ks[i] + 1):
            end = i if mask[i] else i - 1
            segs.append([float(nodes[start]), float(nodes[end])])
            start = None
    return segs


def _uev(pair):
    return [pair[0] * EV_PER_HZ * 1e6, pair[1] * EV_PER_HZ * 1e6]


def _headline(nodes, g, gkey="g90_worst_best_gev_inv"):
    """The best coupling on the finite part of g over nodes: its index
    and {best g (under gkey), its mass, the within-10x band around it,
    every within-10x segment}."""
    fin = np.isfinite(g)
    idx = np.flatnonzero(fin)
    best_i = int(idx[np.argmin(g[idx])])
    best = float(g[best_i])
    segs_hz = _segments(nodes, fin & (g < 10.0 * best))
    around = [s for s in segs_hz if s[0] <= nodes[best_i] <= s[1]][0]
    return best_i, {
        gkey: best,
        "m_a_at_best_ev": float(nodes[best_i] * EV_PER_HZ),
        "m_a_at_best_uev": float(nodes[best_i] * EV_PER_HZ * 1e6),
        "band_10x_ev": [around[0] * EV_PER_HZ, around[1] * EV_PER_HZ],
        "band_10x_uev": _uev(around),
        "band_10x_segments_uev": [_uev(s) for s in segs_hz],
    }


def hypothesis_label(assignment, vendors):
    """'nominal' / 'mirror' when one vendor's sign is unverified, else
    'vendor sign, vendor sign, ...' in vendor order."""
    if len(vendors) == 1:
        return assignment[vendors[0]]
    return ", ".join("%s %s" % (v, assignment[v]) for v in vendors)


def _column_name(label):
    return "g_if_" + label.replace(", ", "+").replace(" ", "-")


def _vendor_text(vendor, n_sessions=None):
    """'vendor agilent' (': N session(s)' appended when n_sessions is
    given), or for a session whose vendor is unrecorded --- its own sign
    group --- 'vendor unrecorded, session <label>'."""
    if vendor.startswith(UNRECORDED_VENDOR_PREFIX):
        return ("vendor unrecorded, session %s"
                % vendor[len(UNRECORDED_VENDOR_PREFIX):])
    if n_sessions is None:
        return "vendor %s" % vendor
    return "vendor %s: %d session%s" % (vendor, n_sessions,
                                        "" if n_sessions == 1 else "s")


def _headline_sentence(n_used, wind_missing, wind_why, nominal_missing):
    """The sentence of rule (and of construction when the wind-aware
    curve is the headline) that names the headline curve: wind-aware
    conservative (option [B]) when every session used carries it, else
    the v0.7 worst-parallel fallback and why --- on the fallback path it
    is carried by rule and headline_basis only."""
    if not wind_missing:
        if not nominal_missing:
            nominal = (", and curve.%s (the same wind, D_cal = 1) likewise "
                       "as combined.g90_wind_nominal_best_gev_inv"
                       % WIND_NOMINAL_CURVE)
        else:
            nominal = ("; curve.%s (D_cal = 1) is absent from %d of %d "
                       "session(s) (%s) and is not combined"
                       % (WIND_NOMINAL_CURVE, len(nominal_missing), n_used,
                          ", ".join(nominal_missing)))
        return ("headline curve %s (%s, D_cal applied): every one of the %d "
                "session(s) carries it; curve.%s (%s, the v0.7 headline) is "
                "combined by the same rule and kept as "
                "combined.g90_worst_best_gev_inv, the robustness line%s"
                % (WIND_CONSERVATIVE_CURVE, WIND_DIRECTION_TEXT, n_used,
                   WORST_CURVE, WORST_DIRECTION_TEXT, nominal))
    return ("headline curve %s (%s, the v0.7 construction): the "
            "wind-aware curve.%s (%s) is absent from %s, so the site "
            "headline falls back to the worst-parallel combination, "
            "unchanged from v0.7; the wind-aware combination needs the "
            "curve in every session of the site"
            % (WORST_CURVE, WORST_DIRECTION_TEXT, WIND_CONSERVATIVE_CURVE,
               WIND_DIRECTION_TEXT,
               _wind_missing_detail(n_used, wind_missing, wind_why)))


def _wind_missing_detail(n_used, wind_missing, wind_why):
    """'K of N session(s) (sessions: why; ...)', the sessions grouped by
    reason, 'every session' when one reason covers them all."""
    by_why = {}
    for l in wind_missing:
        by_why.setdefault(wind_why.get(l, "?"), []).append(l)
    if len(wind_missing) == n_used and len(by_why) == 1:
        return ("every one of the %d session(s) (%s)"
                % (n_used, list(by_why)[0]))
    return ("%d of %d session(s) (%s)"
            % (len(wind_missing), n_used,
               "; ".join("%s: %s" % (", ".join(ls), why)
                         for why, ls in sorted(by_why.items()))))


def _rule_text(n_vendors, shared, wind_headline=False, headline=""):
    grid = ("on the site's common %g Hz mass grid (each node the weaker "
            "of its two bracketing native points), +inf outside a "
            "session's scanned band" % SITE_GRID_STEP_HZ)
    kind = (("wind-aware conservative g_90(m_a) (curve.%s)"
             % WIND_CONSERVATIVE_CURVE) if wind_headline
            else "worst-case g_90(m_a)")
    tail = (" --- " + headline) if headline else ""
    if n_vendors == 0:
        return ("g_site(m_a) = min over sessions of each session's "
                "%s %s --- every session's axis sign is "
                "verified, so there is one sign hypothesis and the site "
                "curve is this plain minimum%s" % (kind, grid, tail))
    if shared:
        return ("g_site(m_a) = max over the %d joint axis-sign hypotheses "
                "h of g_h(m_a), with g_h(m_a) = min over sessions of each "
                "session's %s placed at carrier + offset "
                "(nominal) or carrier - offset (mirror) as h assigns its "
                "vendor's sign, %s --- the sign is taken as ONE shared "
                "unknown per vendor axis convention (%d sign-unverified "
                "vendor%s) on the premise that every such session of a "
                "vendor at this site follows one axis convention "
                "(combined.sign_premise states it and what breaks it), a "
                "sign-verified session contributes the same curve under "
                "every h, and g_site is stated only where every h makes a "
                "statement, so under that premise it holds whichever sign "
                "is true%s"
                % (2 ** n_vendors, kind, grid, n_vendors,
                   "" if n_vendors == 1 else "s", tail))
    return ("g_site(m_a) = min over sessions of each session's %s "
            "%s, every sign-unverified session entered as the "
            "max over its two mass placements --- the independent-sign "
            "fallback: %d sign-unverified vendors exceed the %d the shared-"
            "sign enumeration (2^k joint hypotheses) supports; treating "
            "the signs as independent is never tighter than sharing them, "
            "so the report that brought the %dth vendor may have loosened "
            "the site curve%s"
            % (kind, grid, n_vendors, MAX_SIGN_VENDORS, MAX_SIGN_VENDORS + 1,
               tail))


def _d_cal_text(sessions, wind=False):
    """'D_cal ...' as the sessions actually used it, the pilot envelope
    named as provenance; wind=True adds that the wind-aware conservative
    curve carries the same derating as the worst-parallel one."""
    vals = sorted(set(round(float(s["D_cal"]), 2) for s in sessions
                      if isinstance(s.get("D_cal"), (int, float))))
    pil = sorted(set(round(float(s["D_cal_pilot"]), 2) for s in sessions
                     if isinstance(s.get("D_cal_pilot"), (int, float))))
    if not vals:
        return "D_cal unrecorded"
    pilot = ("the 2020 pilot's %s envelope" % "/".join("%.1f" % p for p in pil)
             if pil else "the 2020 pilot's envelope")
    reused = "reused unmeasured" + (
        ", the same derating in the wind-aware conservative and the "
        "worst-parallel curves" if wind else "")
    inflated = (", inflated by the ladder power envelope where a session's "
                "references were gain-bridged")
    if len(vals) == 1:
        return ("D_cal %.2f in every session (%s, %s%s)"
                % (vals[0], pilot, reused,
                   inflated if pil and vals[0] > max(pil) + 1e-9 else ""))
    return ("D_cal %.2f to %.2f by session (%s, %s%s --- "
            "sessions[].D_cal)" % (vals[0], vals[-1], pilot, reused, inflated))


def _construction_text(sessions, wind_headline, headline):
    """The site's construction string: the wind-aware conservative
    construction (option [B]) when it is the headline, the headline
    sentence appended; else the v0.7 worst-parallel string VERBATIM
    (byte-identical to the v0.7 combiner's), the headline stated in
    rule and headline_basis --- Sec. 8.5 names rule as the carrier and
    the Sec. 7 (iv) allow-list does not license changing this string
    when the combination itself is the unchanged v0.7 one."""
    tail = ("--- unpublished, far above "
            "astrophysical bounds --- where a session's bound is set by "
            "its spin-noise line the limit does NOT improve with more "
            "measurement time, where it is set by a fluctuation "
            "(statistical term %.0f%% or more of P_90) it is a 90%% "
            "one-sided statement whose minimum over N sessions has "
            "coverage below 90%% (toward 0.9^N)"
            % (100 * STAT_FRACTION_FLUCTUATION))
    if wind_headline:
        thetas = [s["wind_theta_deg_range"] for s in sessions
                  if s.get("wind_theta_deg_range")]
        theta = ((" (theta %.0f-%.0f degrees over the sessions)"
                  % (min(t[0] for t in thetas), max(t[1] for t in thetas)))
                 if thetas else "")
        return ("wind-aware conservative per session (2020 pilot "
                "calibration: %s, %s%s, damping at the broader measured "
                "width, no spin-noise subtraction; the worst-parallel curve, "
                "%s, is combined alongside as the robustness line) %s --- %s"
                % (_d_cal_text(sessions, True), WIND_DIRECTION_TEXT, theta,
                   WORST_DIRECTION_TEXT, tail, headline))
    return ("worst-case per session (2020 pilot construction: %s, wind "
            "parallel to B0, damping at the broader measured width, no "
            "spin-noise subtraction) %s" % (_d_cal_text(sessions), tail))


def _nu_l_hz(ex):
    """The session's Larmor frequency (Hz) the curve offsets are measured
    from: line.nu_L_hz_nominal, else carrier + line offset, else None."""
    line = ex.get("line") or {}
    v = line.get("nu_L_hz_nominal")
    if isinstance(v, (int, float)):
        return float(v)
    c, o = line.get("carrier_mhz"), line.get("offset_hz")
    if isinstance(c, (int, float)):
        return float(c) * 1e6 + (float(o) if isinstance(o, (int, float))
                                 else 0.0)
    return None


def _offset_at(nu_node, ex, mirror):
    """nu_a - nu_L (Hz) of a site node for the session whose line sets it,
    on its nominal placement (nu_L - nu_a on the mirror one); None when
    the session records no Larmor frequency."""
    nu_l = _nu_l_hz(ex)
    if nu_l is None:
        return None
    return float(nu_l - nu_node) if mirror else float(nu_node - nu_l)


def _sign_block(best, if_sign, order, gkey, n_unverified, vendors, groups,
                split_txt, nodes, col_own, labels):
    """The shared-sign texts for one curve family, in key order:
    robust_over_conditional_factor, sign_note, independent_sign (the
    headline without the premise, None where no node lies under both
    placements of one session) and sign_premise."""
    cond_best = min(if_sign[l][gkey] for l in order)
    factor = best / cond_best
    lone_console = (len(vendors) == 1
                    and vendors[0].startswith(UNRECORDED_VENDOR_PREFIX))
    note = (
        "the sign-robust headline %.3g GeV^-1 %s the tighter "
        "sign-conditional headline (%s) because %d session(s) are taken "
        "to share %s, so the bound at each mass must hold with their "
        "lines placed at carrier + offset and at carrier - offset, %s "
        "apart --- a single sign determination for %s (a tof-shift "
        "test, vendor checklist item 2: confirm the tof sign and "
        "reference convention) collapses the site bound to the "
        "conditional value at the corresponding mass"
        % (best,
           ("is %.1fx weaker than" % factor) if factor >= 1.05 else
           "matches to within %.0f%%" % (100 * (factor - 1.0)),
           "; ".join("if %s %.3g GeV^-1 at %.7f ueV"
                     % (l, if_sign[l][gkey], if_sign[l]["m_a_at_best_uev"])
                     for l in order),
           n_unverified,
           ("ONE unverified frequency-axis sign (%s)"
            % _vendor_text(vendors[0]))
           if len(vendors) == 1 else
           ("ONE unverified frequency-axis sign per vendor (%s)"
            % ", ".join(_vendor_text(v, len(groups[v])) for v in vendors)),
           split_txt,
           "that session's console" if lone_console else
           "that vendor" if len(vendors) == 1 else "each such vendor"))
    # the bound without the premise: the independent-sign rule
    g_ind = col_own.min(axis=0)
    ind = None
    if np.isfinite(g_ind).any():
        i_ind, ind = _headline(nodes, g_ind, gkey)
        del ind["band_10x_segments_uev"]
        ind["session_setting_best"] = labels[int(np.argmin(col_own[:, i_ind]))]
    premise = (
        "the shared sign is a premise the data do not check: every "
        "sign-unverified session of one vendor at this site is taken to "
        "follow one axis convention (one console, one acquisition-"
        "software version, the offset read the same way in every "
        "bundle); a second console, another software version or a "
        "per-experiment axis reversal at this site breaks it, and the "
        "site bound is then tighter than the truth wherever the shared "
        "rule beats the independent-sign rule --- without the premise "
        "(each session the max over its two placements, then the min "
        "over sessions) the headline is %s"
        % (("%.3g GeV^-1 at %.7f ueV (set by %s), %s"
            % (ind[gkey], ind["m_a_at_best_uev"], ind["session_setting_best"],
               ("%.1fx weaker than the sign-robust headline"
                % (ind[gkey] / best))
               if ind[gkey] / best >= 1.005 else
               "the same as the sign-robust headline (the premise does "
               "not tighten the headline here)"))
           if ind else
           "undefined (no site node lies under both placements of one "
           "session)"))
    return [("robust_over_conditional_factor", factor), ("sign_note", note),
            ("independent_sign", ind), ("sign_premise", premise)]


def combine_reports(reports, per_session_columns=True):
    """Combine [(path, report dict), ...] of one facility.

    Returns the site dict: facility_slug, n_sessions, sessions (per-session
    summaries, input order), skipped [{report, why}], rule, construction,
    combined {the sign-robust best coupling, its mass, the within-10x
    band around the best and every within-10x segment (also under
    'robust'), if_sign {hypothesis label -> the same headline under that
    axis-sign hypothesis}, sign_hypotheses (label order), sign_groups
    (vendor -> sign-unverified sessions, sorted), sign_note, sign_premise
    (the shared-sign premise, what breaks it, the headline without it)
    with independent_sign (that headline: each session the max over its
    two placements, then the min over sessions; None where no node is
    covered by both placements of one session), the covered mass
    segments, total noise seconds, the largest statistical fraction of
    P_90 among the sessions}, curve {m_a_ev, g_site on the sign-robust
    grid; per_session (each session's own sign-robust column: its curve,
    for a sign-unverified session the max over its two placements; None
    outside its band) and if_sign {m_a_ev on every node some hypothesis
    covers, g {label -> g_h or None}} unless per_session_columns is
    False} --- nodes where no hypothesis makes a statement are not
    carried. Raises SiteMismatch when the reports name more than one
    facility_slug.

    v0.8 additive keys (analysis note Sec. 8.5, option [B]; every key
    above keeps its name, position and meaning): headline_curve
    ('g90_wind_conservative' when every session used carries that curve,
    else 'g90_worst', the fallback), headline_basis (the sentence that
    says which and why, also appended to rule, and to construction only
    on the wind path --- the fallback construction is the v0.7 string
    verbatim),
    wind_sessions_missing (sorted labels lacking a usable wind curve);
    sessions[] gain g90_wind_conservative_best_gev_inv,
    offset_at_best_wind_conservative_hz, g90_wind_nominal_best_gev_inv,
    offset_at_best_wind_nominal_hz, wind_theta_deg_range,
    wind_model_unavailable, wind_curve; combined gains headline_curve,
    headline_basis, wind_sessions_missing, wind_conservative and
    wind_nominal (each the full headline block of that family under the
    same rule --- best, mass, offset_at_best_hz on the setting session's
    nominal placement, within-10x band and segments, session/hypothesis
    setting the best, if_sign, and on the shared-sign path the factor,
    sign_note, independent_sign and sign_premise --- or None), the flat
    headline g90_wind_conservative_best_gev_inv,
    m_a_at_best_wind_conservative_ev/_uev,
    offset_at_best_wind_conservative_hz, band_10x_wind_conservative_uev,
    session_setting_best_wind_conservative, g90_wind_nominal_best_gev_inv,
    m_a_at_best_wind_nominal_uev, offset_at_best_wind_nominal_hz,
    session_setting_best_wind_nominal, wind_theta_deg_range (over the
    sessions), worst_over_wind_conservative_factor and robustness_note
    (the headline and the "even with the wind along B_0" line, or the
    fallback statement); curve gains headline_curve,
    g_site_wind_conservative and g_site_wind_nominal (on curve.m_a_ev,
    None when not combined), per_session_wind_conservative and
    if_sign.g_wind_conservative when per_session_columns.
    """
    slugs = sorted(set(str(rep.get("facility_slug")) for _p, rep in reports
                       if rep.get("facility_slug") is not None))
    if len(slugs) > 1:
        raise SiteMismatch(
            "reports name %d facilities (%s) --- site_exclusion combines "
            "ONE site's sessions, never across sites" % (len(slugs),
                                                          ", ".join(slugs)))
    used, skipped, seen = [], [], {}
    for path, rep in reports:
        ex, why = session_exclusion(rep)
        if ex is None:
            skipped.append({"report": path, "why": why})
            continue
        key = rep.get("bundle_sha256") or rep.get("bundle") or path
        if key in seen:
            skipped.append({"report": path,
                            "why": "duplicate of a bundle already given as "
                                   "%s (sha256 %s)" % (seen[key],
                                                       str(key)[:16])})
            continue
        label = session_label(path, rep)
        n_same = sum(1 for _p, _r, _e, l in used if l.split("#")[0] == label)
        if n_same:
            label = "%s#%d" % (label, n_same + 1)
        seen[key] = label
        used.append((path, rep, ex, label))
    sessions = [session_summary(l, p, r, e) for p, r, e, l in used]
    labels = [l for _p, _r, _e, l in used]
    # v0.8 headline (option [B]): the wind-aware conservative curve when
    # every session used carries it, else the v0.7 worst-parallel one
    wind = [session_wind_curves(e, r) for _p, r, e, _l in used]
    # (sorted: the combination is independent of the input order)
    wind_missing = sorted(labels[i] for i, w in enumerate(wind)
                          if w["conservative"] is None)
    wind_why = dict((labels[i], w["why"]) for i, w in enumerate(wind)
                    if w["conservative"] is None)
    nominal_missing = sorted(labels[i] for i, w in enumerate(wind)
                             if w["nominal"] is None)
    wind_headline = bool(used) and not wind_missing
    wind_nominal = wind_headline and not nominal_missing
    headline = (_headline_sentence(len(used), wind_missing, wind_why,
                                   nominal_missing) if used else "")
    out = {"facility_slug": slugs[0] if slugs else None,
           "n_sessions": len(used),
           "sessions": sessions,
           "skipped": skipped,
           "rule": ("no usable session --- no site curve" if not used
                    else _rule_text(0, True, wind_headline, headline)),
           "construction": _construction_text(sessions, wind_headline,
                                              headline),
           "headline_curve": ((WIND_CONSERVATIVE_CURVE if wind_headline
                               else WORST_CURVE) if used else None),
           "headline_basis": headline or "no usable session",
           "wind_sessions_missing": wind_missing}
    if not used:
        out["combined"] = None
        out["curve"] = None
        return out
    placements = []
    for path, rep, ex, label in used:
        cur = ex["curve"]
        nu = np.asarray(cur["m_a_ev"], dtype=float) / EV_PER_HZ
        mir = None
        if cur.get("m_a_ev_mirror") is not None:
            mir = np.asarray(cur["m_a_ev_mirror"], dtype=float) / EV_PER_HZ
        placements.append((nu, mir, np.asarray(cur[WORST_CURVE],
                                               dtype=float)))
    families = [(WORST_CURVE, [g for _n, _m, g in placements])]
    if wind_headline:
        families.append((WIND_CONSERVATIVE_CURVE,
                         [w["conservative"] for w in wind]))
    if wind_nominal:
        families.append((WIND_NOMINAL_CURVE, [w["nominal"] for w in wind]))
    nodes = np.unique(np.concatenate(
        [_nodes_hz(a.min(), a.max()) for nu, mir, _g in placements
         for a in (nu, mir) if a is not None]))
    unverified = [i for i, (_n, mir, _g) in enumerate(placements)
                  if mir is not None]
    groups, vendor_of = {}, [None] * len(used)
    for i in unverified:
        # a session whose vendor is unrecorded shares its sign with no
        # other: the conservative (independent) choice
        vendor_of[i] = sessions[i]["vendor"] or (UNRECORDED_VENDOR_PREFIX
                                                + labels[i])
        groups.setdefault(vendor_of[i], []).append(labels[i])
    groups = {v: sorted(ls) for v, ls in groups.items()}
    vendors = sorted(groups)
    shared = len(vendors) <= MAX_SIGN_VENDORS
    if shared:
        hyps = [dict(zip(vendors, combo)) for combo in
                itertools.product(SIGN_HYPOTHESES, repeat=len(vendors))]
        mirror_rows = [np.array([v is not None and h[v] == "mirror"
                                 for v in vendor_of]) for h in hyps]
    else:
        hyps, mirror_rows = [None], None
    # the same placement and sign machinery for every curve family
    fam = {}
    for key, gs in families:
        c_nom = np.array([_resample_weaker(nu, g, nodes)
                          for (nu, _m, _w), g in zip(placements, gs)])
        c_mir = np.array([c_nom[i] if mir is None
                          else _resample_weaker(mir, g, nodes)
                          for i, ((_n, mir, _w), g)
                          in enumerate(zip(placements, gs))])
        # each session's own sign-robust column: its curve, or for a
        # sign-unverified session the max over its two placements
        c_own = np.maximum(c_nom, c_mir)
        if shared:
            h_cols = [np.where(m[:, None], c_mir, c_nom) for m in mirror_rows]
        else:
            h_cols = [c_own]
        g_h = np.array([c.min(axis=0) for c in h_cols])
        fam[key] = {"col_nom": c_nom, "col_mir": c_mir, "col_own": c_own,
                    "hyp_cols": h_cols, "g_hyp": g_h,
                    "g_site": g_h.max(axis=0)}
    worst = fam[WORST_CURVE]
    col_nom, col_mir, col_own = (worst["col_nom"], worst["col_mir"],
                                 worst["col_own"])
    hyp_cols, g_hyp, g_site = worst["hyp_cols"], worst["g_hyp"], worst["g_site"]
    finite = np.isfinite(g_site)
    out["rule"] = _rule_text(len(vendors), shared, wind_headline, headline)
    if not finite.any():
        out["combined"] = None
        out["curve"] = None
        out["skipped"].append({
            "report": "all", "why": "no session's scanned band contains a "
                                    "node of the %g Hz site grid%s"
                                    % (SITE_GRID_STEP_HZ,
                                       " under every sign hypothesis"
                                       if unverified else "")})
        return out

    def _is_mirror(i, node_i, h_star_i, F):
        """Whether session i's placement that sets node node_i under the
        headline hypothesis is the mirror one."""
        if vendor_of[i] is None:
            return False
        if shared:
            return hyps[h_star_i][vendor_of[i]] == "mirror"
        return bool(F["col_mir"][i, node_i] > F["col_nom"][i, node_i])

    best_i, head = _headline(nodes, g_site)
    best = head["g90_worst_best_gev_inv"]
    h_star = int(np.argmax(g_hyp[:, best_i]))
    winner = int(np.argmin(hyp_cols[h_star][:, best_i]))
    if_sign, order = {}, []
    if shared and vendors:
        for j, h in enumerate(hyps):
            label = hypothesis_label(h, vendors)
            i_h, head_h = _headline(nodes, g_hyp[j])
            head_h["session_setting_best"] = labels[int(np.argmin(
                hyp_cols[j][:, i_h]))]
            head_h["assignment"] = h
            if_sign[label] = head_h
            order.append(label)
    total_s = sum(float(s["noise_seconds"] or 0.0) for s in sessions)
    fracs = [s["statistical_fraction_of_P90"] for s in sessions
             if s["statistical_fraction_of_P90"] is not None]
    frac_max = max(fracs) if fracs else None
    frac_best = sessions[winner]["statistical_fraction_of_P90"]
    robust = dict(head)
    del robust["band_10x_segments_uev"]
    robust["session_setting_best"] = labels[winner]
    robust["hypothesis_setting_best"] = order[h_star] if order else None
    out["combined"] = dict(head)
    out["combined"].update({
        "session_setting_best": labels[winner],
        "band_10x_basis": ("the contiguous run of grid nodes with g_site < "
                           "10 x best that contains the best node --- "
                           "band_10x_segments_uev lists every such run"),
        "mass_coverage_ev": [float(nodes[finite].min() * EV_PER_HZ),
                             float(nodes[finite].max() * EV_PER_HZ)],
        "mass_coverage_segments_uev": [_uev(s) for s in
                                       _segments(nodes, finite)],
        "total_noise_seconds": total_s,
        "statistical_fraction_of_P90_max": frac_max,
        "statistical_fraction_of_P90_at_best": frac_best,
        "coverage_note": (
            "each session's bound is a one-sided 90%% statement --- the "
            "statistical term is %s of P_90 at most across these %d "
            "session(s) and %s in the session setting the best point, so "
            "the minimum over sessions%s %s"
            % (("%.0f%%" % (100 * frac_max)) if frac_max is not None
               else "unrecorded", len(used),
               ("%.0f%%" % (100 * frac_best)) if frac_best is not None
               else "unrecorded",
               " (under each axis-sign hypothesis)" if unverified else "",
               "is a joint worst-case bound set by spin-noise line power"
               if frac_max is not None
               and frac_max < STAT_FRACTION_FLUCTUATION else
               "selects the luckiest fluctuation where a fluctuation sets "
               "the bound (coverage toward 0.9^N there, N the sessions "
               "overlapping that mass)")),
        "robust": robust,
        "if_sign": if_sign,
        "sign_hypotheses": order,
        "sign_groups": groups,
        "sign_unverified_sessions": sorted(labels[i] for i in unverified),
        "sign_rule": (
            "no sign-unverified session: one hypothesis, the plain min "
            "over sessions" if not unverified else
            "one shared axis sign per vendor: %d joint hypotheses over %d "
            "vendor(s), g_site the max over them of the min over sessions"
            % (len(hyps), len(vendors)) if shared else
            "independent sign per session (fallback: %d sign-unverified "
            "vendors exceed %d): each such session the max over its two "
            "placements, then the min over sessions"
            % (len(vendors), MAX_SIGN_VENDORS)),
    })
    split_txt = None
    if unverified and shared:
        splits = sorted(set(
            round(float(np.mean(np.abs(placements[i][0] - placements[i][1]))),
                  1) for i in unverified))
        split_txt = (("%.0f Hz = %.3g ueV" % (splits[0],
                                              splits[0] * EV_PER_HZ * 1e6))
                     if len(splits) == 1 else
                     ("%.0f to %.0f Hz = %.3g to %.3g ueV"
                      % (splits[0], splits[-1], splits[0] * EV_PER_HZ * 1e6,
                         splits[-1] * EV_PER_HZ * 1e6)))
        for k, v in _sign_block(best, if_sign, order,
                                "g90_worst_best_gev_inv", len(unverified),
                                vendors, groups, split_txt, nodes, col_own,
                                labels):
            out["combined"][k] = v
    elif unverified:
        out["combined"]["sign_note"] = (
            "%d sign-unverified session(s) span %d vendor axis conventions, "
            "more than the %d the shared-sign enumeration supports: each "
            "such session enters the site curve as the pointwise MAXIMUM "
            "over its two mass placements (carrier + offset and carrier - "
            "offset), the signs treated as independent --- a bound never "
            "tighter than the shared-sign rule, so the report that brought "
            "the %dth vendor may have loosened the site curve, and every "
            "mass label stays two-valued by twice the line offset until "
            "each vendor's axis convention is established (a tof-shift "
            "test, vendor checklist item 2: confirm the tof sign and "
            "reference convention)"
            % (len(unverified), len(vendors), MAX_SIGN_VENDORS,
               MAX_SIGN_VENDORS + 1))

    # ---- v0.8 additive: the wind-aware families, each the same rule
    def _family_block(F, gkey):
        """The headline of one wind-aware family: the same fields as the
        worst-parallel headline plus the line offset at the best node,
        the per-hypothesis headlines and the shared-sign texts."""
        b_i, blk = _headline(nodes, F["g_site"], gkey)
        h_s = int(np.argmax(F["g_hyp"][:, b_i]))
        w_i = int(np.argmin(F["hyp_cols"][h_s][:, b_i]))
        blk["offset_at_best_hz"] = _offset_at(
            nodes[b_i], used[w_i][2], _is_mirror(w_i, b_i, h_s, F))
        blk["session_setting_best"] = labels[w_i]
        blk["hypothesis_setting_best"] = order[h_s] if order else None
        blk["statistical_fraction_of_P90_at_best"] = (
            sessions[w_i]["statistical_fraction_of_P90"])
        ifs = {}
        if shared and vendors:
            for j, h in enumerate(hyps):
                i_h, head_h = _headline(nodes, F["g_hyp"][j], gkey)
                head_h["session_setting_best"] = labels[int(np.argmin(
                    F["hyp_cols"][j][:, i_h]))]
                head_h["assignment"] = h
                ifs[hypothesis_label(h, vendors)] = head_h
        blk["if_sign"] = ifs
        if unverified and shared:
            for k, v in _sign_block(blk[gkey], ifs, order, gkey,
                                    len(unverified), vendors, groups,
                                    split_txt, nodes, F["col_own"], labels):
                blk[k] = v
        return blk

    comb = out["combined"]
    wc = (_family_block(fam[WIND_CONSERVATIVE_CURVE],
                        "g90_wind_conservative_best_gev_inv")
          if wind_headline else None)
    wn = (_family_block(fam[WIND_NOMINAL_CURVE],
                        "g90_wind_nominal_best_gev_inv")
          if wind_nominal else None)
    thetas = [s["wind_theta_deg_range"] for s in sessions
              if s.get("wind_theta_deg_range")]
    comb["headline_curve"] = out["headline_curve"]
    comb["headline_basis"] = headline
    comb["wind_sessions_missing"] = wind_missing
    comb["wind_conservative"] = wc
    comb["wind_nominal"] = wn
    comb["g90_wind_conservative_best_gev_inv"] = (
        wc["g90_wind_conservative_best_gev_inv"] if wc else None)
    comb["m_a_at_best_wind_conservative_ev"] = (wc["m_a_at_best_ev"]
                                                if wc else None)
    comb["m_a_at_best_wind_conservative_uev"] = (wc["m_a_at_best_uev"]
                                                 if wc else None)
    comb["offset_at_best_wind_conservative_hz"] = (wc["offset_at_best_hz"]
                                                   if wc else None)
    comb["band_10x_wind_conservative_uev"] = (wc["band_10x_uev"]
                                              if wc else None)
    comb["session_setting_best_wind_conservative"] = (
        wc["session_setting_best"] if wc else None)
    comb["g90_wind_nominal_best_gev_inv"] = (
        wn["g90_wind_nominal_best_gev_inv"] if wn else None)
    comb["m_a_at_best_wind_nominal_uev"] = (wn["m_a_at_best_uev"]
                                            if wn else None)
    comb["offset_at_best_wind_nominal_hz"] = (wn["offset_at_best_hz"]
                                              if wn else None)
    comb["session_setting_best_wind_nominal"] = (wn["session_setting_best"]
                                                 if wn else None)
    comb["wind_theta_deg_range"] = ([min(t[0] for t in thetas),
                                     max(t[1] for t in thetas)]
                                    if (wc and thetas) else None)
    comb["worst_over_wind_conservative_factor"] = (
        best / wc["g90_wind_conservative_best_gev_inv"] if wc else None)
    comb["robustness_note"] = (
        ("headline g_ap < %.3g GeV^-1 at %.7f ueV with %s%s (set by %s); "
         "even with the wind along B_0 the bound is g_ap < %.3g GeV^-1 at "
         "%.7f ueV (set by %s), %.2fx weaker%s"
         % (wc["g90_wind_conservative_best_gev_inv"], wc["m_a_at_best_uev"],
            WIND_DIRECTION_TEXT,
            (" (theta %.0f-%.0f degrees over the sessions)"
             % tuple(comb["wind_theta_deg_range"]))
            if comb["wind_theta_deg_range"] else "",
            wc["session_setting_best"], best, head["m_a_at_best_uev"],
            labels[winner], comb["worst_over_wind_conservative_factor"],
            ("; with D_cal = 1 and the same wind g_ap < %.3g GeV^-1 at "
             "%.7f ueV" % (wn["g90_wind_nominal_best_gev_inv"],
                           wn["m_a_at_best_uev"])) if wn else ""))
        if wc else
        ("headline g_ap < %.3g GeV^-1 at %.7f ueV on the worst-parallel "
         "curve (%s, the v0.7 construction, set by %s): the wind-aware "
         "curve.%s is absent from %s, so the v0.7 combination stands "
         "unchanged (rule and headline_basis say so)"
         % (best, head["m_a_at_best_uev"], WORST_DIRECTION_TEXT,
            labels[winner], WIND_CONSERVATIVE_CURVE,
            _wind_missing_detail(len(used), wind_missing, wind_why))))
    nodes_r, g_r, cols_r = nodes[finite], g_site[finite], col_own[:, finite]
    out["curve"] = {
        "m_a_ev": (nodes_r * EV_PER_HZ).tolist(),
        "g_site": g_r.tolist(),
        "grid_step_hz": SITE_GRID_STEP_HZ,
        "grid_note": ("nodes at integer multiples of %g Hz in nu_a inside "
                      "each session's scanned band (their union, nothing "
                      "between sessions at different carriers), each "
                      "session's value the weaker of its two bracketing "
                      "native points; g_site is carried where every "
                      "axis-sign hypothesis makes a statement"
                      % SITE_GRID_STEP_HZ),
    }
    if per_session_columns:
        out["curve"]["per_session"] = [
            [(None if not np.isfinite(v) else float(v)) for v in col]
            for col in cols_r]
        any_h = np.isfinite(g_hyp).any(axis=0)
        out["curve"]["if_sign"] = {
            "m_a_ev": (nodes[any_h] * EV_PER_HZ).tolist(),
            "g": {label: [(None if not np.isfinite(v) else float(v))
                          for v in gh[any_h]]
                  for label, gh in zip(order, g_hyp)},
            "note": ("g_h = min over sessions under axis-sign hypothesis h, "
                     "on every node some hypothesis covers (None where h "
                     "makes no statement); g_site = max over h, carried on "
                     "curve.m_a_ev only"),
        } if order else None
    else:
        out["curve"]["per_session"] = None
        out["curve"]["if_sign"] = None
        out["curve"]["per_session_note"] = (
            "per-session and per-hypothesis curves live in "
            "site_exclusion_<slug>.json/.txt written by "
            "analysis/site_exclusion.py, not in report.json")
    # v0.8 additive curve columns (the same grid and finiteness: a node
    # is inside or outside a session's band whichever curve is read)
    out["curve"]["headline_curve"] = out["headline_curve"]
    out["curve"]["g_site_wind_conservative"] = (
        fam[WIND_CONSERVATIVE_CURVE]["g_site"][finite].tolist()
        if wind_headline else None)
    out["curve"]["g_site_wind_nominal"] = (
        fam[WIND_NOMINAL_CURVE]["g_site"][finite].tolist()
        if wind_nominal else None)
    if per_session_columns:
        out["curve"]["per_session_wind_conservative"] = ([
            [(None if not np.isfinite(v) else float(v)) for v in col]
            for col in fam[WIND_CONSERVATIVE_CURVE]["col_own"][:, finite]]
            if wind_headline else None)
        if order and wind_headline:
            any_h = np.isfinite(g_hyp).any(axis=0)
            out["curve"]["if_sign"]["g_wind_conservative"] = {
                label: [(None if not np.isfinite(v) else float(v))
                        for v in gh[any_h]]
                for label, gh in zip(
                    order, fam[WIND_CONSERVATIVE_CURVE]["g_hyp"])}
    return out


def write_outputs(site, out_dir):
    """site_exclusion_<slug>.json and .txt in out_dir --- returns their
    paths."""
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    slug = site.get("facility_slug") or "unknown"
    jpath = os.path.join(out_dir, "site_exclusion_%s.json" % slug)
    tpath = os.path.join(out_dir, "site_exclusion_%s.txt" % slug)
    with open(jpath, "w") as fh:
        json.dump(site, fh, indent=1, sort_keys=True)
        fh.write("\n")
    comb = site.get("combined") or {}
    curve = site.get("curve") or {}
    ifs = curve.get("if_sign") or None
    order = comb.get("sign_hypotheses") or []
    with open(tpath, "w") as fh:
        labels = [s["label"] for s in site["sessions"]]
        wind_cols = [k for k in ("g_site_wind_conservative",
                                 "g_site_wind_nominal")
                     if curve.get(k) is not None]
        fh.write("# site %s: %s exclusion on g_ap, min over %d "
                 "session(s) of one-sided 90%% CL bounds%s --- unpublished, "
                 "far above astrophysical bounds\n"
                 % (slug,
                    "wind-aware conservative (headline column "
                    "g_site_wind_conservative; g_site the worst-parallel "
                    "robustness line)" if wind_cols else "worst-case",
                    site["n_sessions"],
                    " under each axis-sign hypothesis, the weaker "
                    "hypothesis quoted as g_site" if order else ""))
        fh.write("# rule: %s\n" % site["rule"])
        if comb.get("robustness_note"):
            fh.write("# headline: %s\n" % comb["robustness_note"])
        if comb.get("coverage_note"):
            fh.write("# coverage: %s\n" % comb["coverage_note"])
        if comb.get("sign_note"):
            fh.write("# sign: %s\n" % comb["sign_note"])
        if comb.get("sign_premise"):
            fh.write("# sign premise: %s\n" % comb["sign_premise"])
        if ifs:
            fh.write("# columns: g_site on the sign-robust grid (nodes where "
                     "every hypothesis makes a statement); g_if_* the min "
                     "over sessions under that hypothesis on its own grid; "
                     "g_<session> each session's own sign-robust column "
                     "(max over its placements) on g_site's grid; inf = no "
                     "statement carried\n")
        fh.write("# m_a [eV]   g_site [GeV^-1]   %s\n"
                 % "   ".join(["%s [GeV^-1]" % k for k in wind_cols]
                              + ["%s [GeV^-1]" % _column_name(l) for l in order]
                              + ["g_%s [GeV^-1]" % l for l in labels]))
        if curve:
            per = [curve[k] for k in wind_cols] + (curve.get("per_session")
                                                   or [])
            if ifs:
                # rows on every node some hypothesis covers, the robust and
                # per-session columns looked up by node index
                idx = {int(round(m / EV_PER_HZ / curve["grid_step_hz"])): i
                       for i, m in enumerate(curve["m_a_ev"])}
                rows = []
                for j, m in enumerate(ifs["m_a_ev"]):
                    i = idx.get(int(round(m / EV_PER_HZ
                                          / curve["grid_step_hz"])))
                    rows.append([m, curve["g_site"][i] if i is not None
                                 else None]
                                + [ifs["g"][l][j] for l in order]
                                + [col[i] if i is not None else None
                                   for col in per])
            else:
                rows = zip(*([curve["m_a_ev"], curve["g_site"]] + per))
            for row in rows:
                fh.write("  ".join(
                    "%.9e" % v if v is not None else "inf" for v in row))
                fh.write("\n")
    return jpath, tpath


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[3])
    ap.add_argument("reports", nargs="+",
                    help="report.json files (or report directories) of one "
                         "facility's sessions")
    ap.add_argument("--out", default=None,
                    help="output directory (default: the first report's "
                         "directory)")
    args = ap.parse_args(argv)
    reports = []
    for p in args.reports:
        try:
            reports.append(load_report(p))
        except (IOError, OSError, ValueError) as exc:
            print("ERROR: cannot read report %s: %s" % (p, exc))
            return 2
    try:
        site = combine_reports(reports)
    except SiteMismatch as exc:
        print("ERROR: %s" % exc)
        return 2
    out_dir = args.out or os.path.dirname(reports[0][0])
    jpath, tpath = write_outputs(site, out_dir)
    for s in site["skipped"]:
        print("skipped %s: %s" % (s["report"], s["why"]))
    comb = site.get("combined")
    if comb is None:
        print("no usable exclusion among %d report(s) --- wrote %s"
              % (len(reports), jpath))
        return 1
    print("site %s: %d session(s), %.0f s of noise data" % (
        site["facility_slug"], site["n_sessions"],
        comb["total_noise_seconds"]))
    for s in site["sessions"]:
        print("  %s: g_ap < %.3g GeV^-1 at %.7f ueV (carrier %s MHz, "
              "statistical term %s of P_90%s)%s"
              % (s["label"], s["g90_worst_best_gev_inv"] or float("nan"),
                 s["m_a_at_best_uev"] or float("nan"), s["carrier_mhz"],
                 ("%.0f%%" % (100 * s["statistical_fraction_of_P90"]))
                 if s["statistical_fraction_of_P90"] is not None else "n/a",
                 ", axis sign UNVERIFIED" if s["sign_verified"] is False
                 else "",
                 (" --- wind-aware conservative g_ap < %.3g GeV^-1 (theta "
                  "%s)" % (s["g90_wind_conservative_best_gev_inv"],
                           ("%.1f-%.1f deg" % tuple(s["wind_theta_deg_range"]))
                           if s.get("wind_theta_deg_range") else "n/a"))
                 if s.get("g90_wind_conservative_best_gev_inv") else
                 " --- no wind-aware curve"))
    if comb.get("wind_conservative"):
        wc = comb["wind_conservative"]
        print("combined wind-aware conservative (HEADLINE%s): g_ap < %.3g "
              "GeV^-1 at m_a = %.7f ueV (offset %s Hz), within-10x band "
              "%.7f..%.7f ueV, set by %s%s"
              % (", sign-robust" if comb["sign_hypotheses"] else "",
                 wc["g90_wind_conservative_best_gev_inv"],
                 wc["m_a_at_best_uev"],
                 ("%.1f" % wc["offset_at_best_hz"])
                 if wc["offset_at_best_hz"] is not None else "n/a",
                 wc["band_10x_uev"][0], wc["band_10x_uev"][1],
                 wc["session_setting_best"],
                 ("; D_cal = 1 companion g_ap < %.3g GeV^-1 at %.7f ueV"
                  % (comb["g90_wind_nominal_best_gev_inv"],
                     comb["m_a_at_best_wind_nominal_uev"]))
                 if comb.get("wind_nominal") else ""))
    else:
        print("headline: %s" % comb.get("robustness_note"))
    print("combined worst-case%s: g_ap < %.3g GeV^-1 at m_a = %.7f ueV, "
          "within-10x band around the best %.7f..%.7f ueV (%d segment(s) "
          "in total, set by %s)"
          % (" (sign-robust)" if comb["sign_hypotheses"] else "",
             comb["g90_worst_best_gev_inv"], comb["m_a_at_best_uev"],
             comb["band_10x_uev"][0], comb["band_10x_uev"][1],
             len(comb["band_10x_segments_uev"]), comb["session_setting_best"]))
    for label in comb["sign_hypotheses"]:
        h = comb["if_sign"][label]
        print("  if the axis sign is %s: g_ap < %.3g GeV^-1 at m_a = %.7f "
              "ueV, band %.7f..%.7f ueV (set by %s)"
              % (label, h["g90_worst_best_gev_inv"], h["m_a_at_best_uev"],
                 h["band_10x_uev"][0], h["band_10x_uev"][1],
                 h["session_setting_best"]))
    if comb.get("sign_note"):
        print("sign: %s" % comb["sign_note"])
    if comb.get("sign_premise"):
        print("sign premise: %s" % comb["sign_premise"])
    print("coverage: %s" % comb["coverage_note"])
    print("wrote %s and %s" % (jpath, tpath))
    return 0


if __name__ == "__main__":
    sys.exit(main())
