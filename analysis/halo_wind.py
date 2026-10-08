#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
halo_wind.py -- the standard-halo-model dark-matter wind at an NMR magnet:
lab velocity, apex, wind angle to B0 at a site and UTC time, the session's
angle histogram, and the v_perp^2-weighted ALP lineshape at an arbitrary
wind angle (analysis specification v0.8, Sec. 8; port of the 2026-10-06
prototype shm_wind.py, which this file supersedes for the report).

Conventions follow analysis/facility_report.py: the halo is an isotropic
Maxwellian with sigma = v0/sqrt(2), v0 = 220 km/s, truncated at v_esc =
544 km/s in the galactic frame, boosted by the lab velocity; the drive is
the gradient component transverse to B0, so every velocity sample is
weighted by v_perp^2 and Int lam dnu = <v_perp^2/c^2>.

Lab velocity v_lab(t) = v_sun + v_earth(t): the Sun's motion relative to
the halo in galactic (U, V, W) = (11.1, 220 + 12.24, 7.25) km/s (|v| =
232.6, the reports' 233) plus the Earth's orbital velocity (29.79 km/s)
in the Freese, Lisanti & Savage (2013) parameterisation. B0 of a
standard-bore magnet is along the local vertical, so the wind angle
theta(t) to B0 is the angle between -v_lab and the zenith; it repeats
with the sidereal day. For a horizontal B0 (MRI nodes, electromagnets)
the zenith is replaced by the horizontal unit vector at the recorded
bore azimuth. The sign of B0 is irrelevant (the drive is even in
v_perp), so theta is folded into [0, 90] degrees.

alp_lineshape_angle() generalises facility_report.alp_lineshape() by one
line (the boost becomes (V sin theta, 0, V cos theta)) on the SAME random
stream, so theta = 90 at V = 233 km/s reproduces alp_lineshape(..., False)
and theta = 0 reproduces alp_lineshape(..., True) bit-identically.

Python 3.8 compatible, numpy only. facility_report.py loads this file by
path (halo_wind_module()); it imports nothing from the report.
UNPUBLISHED, internal: the wind-aware construction is a preliminary
sensitivity bookkeeping, not a publication-grade limit.
"""
import datetime
import math

import numpy as np

C_KMS = 299792.458
SHM_V0_KMS = 220.0
SHM_VLAB_KMS = 233.0
SHM_VESC_KMS = 544.0
# galactic Cartesian (U toward the centre, V along rotation, W toward the
# north galactic pole), km/s
V_SUN_GAL_KMS = (11.1, 220.0 + 12.24, 7.25)
V_EARTH_KMS = 29.79
EPS1_GAL = np.array([0.9931, 0.1170, -0.01032])   # Earth's velocity direction at the March equinox
EPS2_GAL = np.array([-0.0670, 0.4927, -0.8676])   # ... a quarter year later
# galactic -> equatorial J2000 (transpose of the standard eq -> gal matrix;
# precession since J2000 is 0.4 deg, neglected)
GAL2EQ = np.array([[-0.054876, 0.494109, -0.867666],
                   [-0.873437, -0.444830, -0.198076],
                   [-0.483835, 0.746982, 0.455984]])
UTC = datetime.timezone.utc
J2000 = datetime.datetime(2000, 1, 1, 12, tzinfo=UTC)
MARCH_EQUINOX_DOY = 79.5           # ~March 21 00:00 UT, days since Jan 1 00:00
TROPICAL_YEAR_D = 365.2422
SIDEREAL_DAY_H = 23.9345
THETA_BIN_DEG = 2.0                # session angle binning (spec Sec. 8.3)

_V_SUN = np.array(V_SUN_GAL_KMS, dtype=float)


# ---------------------------------------------------------------- time
def days_since_j2000(t_utc):
    return (t_utc - J2000).total_seconds() / 86400.0


def gmst_deg(t_utc):
    """Greenwich mean sidereal time, degrees (IAU 1982 polynomial)."""
    d = days_since_j2000(t_utc)
    T = d / 36525.0
    return (280.46061837 + 360.98564736629 * d + 0.000387933 * T * T) % 360.0


def utc_from_local(iso_local, tz_offset_min):
    """UTC datetime from a console-local 'YYYY-MM-DDTHH:MM:SS' string and
    the bundle's local_timezone_offset_min; None when unparseable."""
    try:
        naive = datetime.datetime.strptime(str(iso_local).strip()[:19],
                                           "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None
    tz = datetime.timezone(datetime.timedelta(minutes=float(tz_offset_min)))
    return naive.replace(tzinfo=tz).astimezone(UTC)


def utc_from_epoch_ms(ms):
    """UTC datetime from a Unix epoch in milliseconds (the clock audit's
    wall_start_ms / wall_end_ms)."""
    return datetime.datetime.fromtimestamp(float(ms) / 1000.0, UTC)


def block_spread_times(t_start, t_end, n_rows):
    """Row i of an n-row block at t_start + (i + 1/2) (t_end - t_start) / n
    (spec Sec. 8.4: equal-duration rows filling the block's wall time)."""
    n = max(int(n_rows), 1)
    span = (t_end - t_start).total_seconds()
    return [t_start + datetime.timedelta(seconds=span * (i + 0.5) / n)
            for i in range(n)]


# ------------------------------------------------------ lab velocity
def v_lab_gal(t_utc):
    """Lab velocity through the halo, galactic Cartesian, km/s."""
    year_start = datetime.datetime(t_utc.year, 1, 1, tzinfo=UTC)
    doy = (t_utc - year_start).total_seconds() / 86400.0
    phase = 2.0 * math.pi * (doy - MARCH_EQUINOX_DOY) / TROPICAL_YEAR_D
    v_e = V_EARTH_KMS * (EPS1_GAL * math.cos(phase)
                         + EPS2_GAL * math.sin(phase))
    return _V_SUN + v_e


def v_lab_eq(t_utc):
    """Lab velocity in equatorial J2000 Cartesian coordinates, km/s."""
    return GAL2EQ.dot(v_lab_gal(t_utc))


def radec_deg(vec):
    """(RA, Dec) in degrees of an equatorial Cartesian vector."""
    n = float(np.linalg.norm(vec))
    return (math.degrees(math.atan2(vec[1], vec[0])) % 360.0,
            math.degrees(math.asin(vec[2] / n)))


def apex_radec_deg(t_utc):
    """The apex (direction of v_lab; the wind blows from it), RA/Dec deg."""
    return radec_deg(v_lab_eq(t_utc))


# ------------------------------------------------------- site frame
def zenith_eq(lat_deg, lon_east_deg, t_utc):
    """Unit vector of the local vertical in equatorial coordinates."""
    lst = math.radians((gmst_deg(t_utc) + lon_east_deg) % 360.0)
    lat = math.radians(lat_deg)
    return np.array([math.cos(lat) * math.cos(lst),
                     math.cos(lat) * math.sin(lst), math.sin(lat)])


def horizontal_b0_eq(lat_deg, lon_east_deg, azimuth_deg, t_utc):
    """Unit vector of a horizontal B0 at the given bore azimuth (degrees
    east of north), built in the horizon frame and rotated to equatorial:
    east = (-sin LST, cos LST, 0), north = zenith x east."""
    lst = math.radians((gmst_deg(t_utc) + lon_east_deg) % 360.0)
    lat = math.radians(lat_deg)
    east = np.array([-math.sin(lst), math.cos(lst), 0.0])
    north = np.array([-math.sin(lat) * math.cos(lst),
                      -math.sin(lat) * math.sin(lst), math.cos(lat)])
    az = math.radians(azimuth_deg)
    return math.cos(az) * north + math.sin(az) * east


def b0_axis_eq(lat_deg, lon_east_deg, t_utc, azimuth_deg=None):
    """B0 unit vector in equatorial coordinates: the zenith for a vertical
    bore, the horizontal vector at azimuth_deg for a horizontal one."""
    if azimuth_deg is None:
        return zenith_eq(lat_deg, lon_east_deg, t_utc)
    return horizontal_b0_eq(lat_deg, lon_east_deg, azimuth_deg, t_utc)


def wind_angle_deg(lat_deg, lon_east_deg, t_utc, azimuth_deg=None):
    """Angle between the DM wind (-v_lab) and B0, folded to [0, 90] deg
    (the drive is even in v_perp, so the sign of B0 does not matter)."""
    v = v_lab_eq(t_utc)
    v = v / np.linalg.norm(v)
    c = abs(float(np.dot(v, b0_axis_eq(lat_deg, lon_east_deg, t_utc,
                                       azimuth_deg))))
    return math.degrees(math.acos(min(1.0, c)))


# ---------------------------------------------------------- session
def session_wind(lat_deg, lon_east_deg, times_utc, bin_deg=THETA_BIN_DEG,
                 azimuth_deg=None):
    """Per-row wind angles and the session's angle histogram (bin centres
    with weights summing to 1, equal weight per row), the session-mean
    |v_lab| and apex, for a list of row UTC times."""
    th = np.array([wind_angle_deg(lat_deg, lon_east_deg, t, azimuth_deg)
                   for t in times_utc], dtype=float)
    vl_vec = np.array([v_lab_eq(t) for t in times_utc], dtype=float)
    vl = np.sqrt(np.einsum("ij,ij->i", vl_vec, vl_vec))
    edges = np.arange(0.0, 90.0 + bin_deg, bin_deg)
    h, _ = np.histogram(th, bins=edges)
    centers = 0.5 * (edges[1:] + edges[:-1])
    sel = h > 0
    ra, dec = radec_deg(vl_vec.mean(axis=0))
    return {"theta_deg": th, "vlab_kms": vl,
            "bins_deg": centers[sel], "weights": h[sel] / float(h.sum()),
            "bin_deg": float(bin_deg),
            "sin2_mean": float(np.mean(np.sin(np.radians(th)) ** 2)),
            "theta_min": float(th.min()), "theta_max": float(th.max()),
            "theta_mean": float(th.mean()), "vlab_mean": float(vl.mean()),
            "apex_ra_deg": ra, "apex_dec_deg": dec, "n_rows": int(th.size)}


# -------------------------------------------------------- lineshape
def alp_lineshape_angle(grid_hz, nu_a_hz, theta_deg, vlab_kms, nsamp, seed):
    """facility_report.alp_lineshape generalised to a wind at theta from
    B0 (z along B0): Monte-Carlo v_perp^2-weighted SHM lineshape lam(nu)
    on grid_hz (offsets above nu_a, in 1/Hz), Int lam dnu = <v_perp^2/c^2>.
    theta 0 is the reports' 'parallel' worst case, 90 their
    'perpendicular' nominal; the random stream is the report's, so both
    are reproduced bit-identically at vlab_kms = SHM_VLAB_KMS (the boost
    components are snapped to exactly 0 within 1e-12 of it, which keeps
    cos(90 deg) = 6e-17 from touching the last bit). Returns
    (lam, <v_perp^2/c^2>)."""
    rng = np.random.default_rng(seed)
    v = rng.normal(0.0, SHM_V0_KMS / math.sqrt(2.0), size=(int(nsamp), 3))
    v = v[np.einsum("ij,ij->i", v, v) < SHM_VESC_KMS ** 2]
    th = math.radians(float(theta_deg))
    bz = float(vlab_kms) * math.cos(th)
    bx = float(vlab_kms) * math.sin(th)
    if abs(bz) < 1e-12:
        bz = 0.0
    if abs(bx) < 1e-12:
        bx = 0.0
    if bz:
        v[:, 2] += bz
    if bx:
        v[:, 0] += bx
    v2 = np.einsum("ij,ij->i", v, v)
    vperp2 = v[:, 0] ** 2 + v[:, 1] ** 2
    dnu = nu_a_hz * v2 / (2.0 * C_KMS ** 2)
    step = grid_hz[1] - grid_hz[0]
    edges = np.concatenate([grid_hz - 0.5 * step, [grid_hz[-1] + 0.5 * step]])
    w, _ = np.histogram(dnu, bins=edges, weights=vperp2 / C_KMS ** 2)
    return w / v.shape[0] / step, float(np.mean(vperp2) / C_KMS ** 2)


def session_lineshape_bins(grid_hz, nu_a_hz, sw, nsamp, seed):
    """session_lineshape plus the per-bin lineshapes it evaluated:
    (lam, <v_perp^2/c^2>, lam_bins) with lam_bins {bin centre in degrees
    (rounded to 1e-6) -> the lam of alp_lineshape_angle at that centre,
    the session-mean |v_lab|, nsamp and seed}. The sidereal-modulation
    template bank seeds its cache from lam_bins instead of re-running the
    same Monte Carlo (identical inputs, so identical arrays bit for bit)."""
    lam = np.zeros_like(np.asarray(grid_hz, dtype=float))
    vp = 0.0
    lam_bins = {}
    for th, wgt in zip(sw["bins_deg"], sw["weights"]):
        lam_b, vp_b = alp_lineshape_angle(grid_hz, nu_a_hz, float(th),
                                          sw["vlab_mean"], nsamp, seed)
        lam_bins[round(float(th), 6)] = lam_b
        lam += wgt * lam_b
        vp += wgt * vp_b
    return lam, float(vp), lam_bins


def session_lineshape(grid_hz, nu_a_hz, sw, nsamp, seed):
    """Row-weighted mean lineshape at the session's binned wind angles,
    every bin at the single session-mean |v_lab| (spec Sec. 8.3).
    Returns (lam, <v_perp^2/c^2>); session_lineshape_bins returns the
    per-bin arrays beside them."""
    lam, vp, _bins = session_lineshape_bins(grid_hz, nu_a_hz, sw, nsamp,
                                            seed)
    return lam, vp


# ----------------------------------------------------- diagnostics
def sidereal_angle_curve(lat_deg, lon_east_deg, date_utc, n=97,
                         azimuth_deg=None):
    """(hours, theta_deg) over one sidereal day from date_utc, the endpoint
    excluded (t = 0 and t = 23.9345 h are the same sidereal phase, so a
    sampled day never counts a phase twice)."""
    ts = [date_utc + datetime.timedelta(hours=SIDEREAL_DAY_H * i / n)
          for i in range(n)]
    hours = np.array([(t - date_utc).total_seconds() / 3600.0 for t in ts])
    th = np.array([wind_angle_deg(lat_deg, lon_east_deg, t, azimuth_deg)
                   for t in ts])
    return hours, th


def sidereal_sin2_closed_form(lat_deg, apex_dec_deg):
    """<sin^2 theta> over a full sidereal day for a vertical B0:
    1 - A^2 - B^2/2 with A = sin(dec) sin(phi), B = cos(dec) cos(phi)."""
    a = math.sin(math.radians(apex_dec_deg)) * math.sin(math.radians(lat_deg))
    b = math.cos(math.radians(apex_dec_deg)) * math.cos(math.radians(lat_deg))
    return 1.0 - a * a - 0.5 * b * b


if __name__ == "__main__":
    t = datetime.datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    print("apex RA/Dec (deg) on 2026-10-05: %.1f %.1f, |v_lab| %.1f km/s"
          % (apex_radec_deg(t) + (float(np.linalg.norm(v_lab_gal(t))),)))
    for name, lat, lon in (("Oulu", 65.0, 25.5), ("Carbondale", 37.7, -89.2),
                           ("Lausanne", 46.5, 6.6), ("Torino", 45.1, 7.7)):
        h, th = sidereal_angle_curve(lat, lon, t)
        print("%-11s theta over a sidereal day %5.1f .. %5.1f deg, <sin^2> "
              "%.3f (closed form %.3f)"
              % (name, th.min(), th.max(),
                 np.mean(np.sin(np.radians(th)) ** 2),
                 sidereal_sin2_closed_form(lat, apex_radec_deg(t)[1])))
