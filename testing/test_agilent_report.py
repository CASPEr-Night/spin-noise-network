#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_agilent_report.py -- validate facility_report's Agilent/Varian read
path and Tier-1 noise aggregation on a synthetic VnmrJ session.

    python3 testing/test_agilent_report.py [--out-dir DIR]
                                           [--real-bundle ZIP ...]

Steps:

  1. build a synthetic Agilent session with
     vendors/agilent/make_synthetic_agilent_data.py (white int32 noise,
     sigma 1000 counts per re/im sample; 4 ladder + 2 reference + 3
     single-row noise expnos) and pack it with packer/pack_bundle.py
     --vendor agilent;
  2. derive a science-path copy: the generator stamps run_mode 'desktest'
     so its bundle can never pass as data, and facility_report refuses
     science numbers for desktest bundles by design. The copy is
     restamped 'synthetic-injection' (the schema's run_mode for
     numerically generated validation data); nothing else changes;
  3. run facility_report on it and assert that every noise / reference /
     ladder experiment is read as Agilent int32 with 1 row and the
     generator's point count, that the block headers were verified (scale
     0, recorded), that the per-row DC is recorded but NOT subtracted,
     that the three noise experiments are aggregated into ONE block of 3
     rows in meta order carrying their source expno and start time, that
     per-row stats are finite and match the generator's white-noise PSD
     (2*sigma^2/sw), that no Bruker default was applied (group delay 0,
     format 'agilent', nothing refused), that S_32 samples get no assumed
     full scale, that the references are reported as a matched pair, that
     the detection's fit basis is stated and consistent with the
     alignment cross-check (the per-row significance gate keeps a
     featureless block's rows from self-aligning), that the frequency-
     sign caveat is stated with the lock-referencing cross-check
     'unusable' (no reference line), and
     that science.axion_exclusion is present with finite worst-case
     numbers (references gain-bridged to the noise rg, D_cal inflated by
     the ladder envelope) and the 'does NOT improve with more measurement
     time' honesty line;
  4. fail-loudly copies: one noise expno stripped of its procpar must be
     REFUSED with an explicit reason (QA FAIL, block shrinks to 2 rows)
     and must NOT also receive an 'ADC check ... OK' row; with every
     noise expno stripped the spin-noise analysis must be marked
     UNAVAILABLE instead of printing a null result; a closing reference
     re-written with a different pw must surface as an unmatched
     reference pair in report.json, the QA flags and the HTML; a noise
     expno at a different rg plus swapped start times must yield two
     parameter groups AND the headline block's time-order warning;
  5. direct Bundle checks on hand-built fids: float32 with a DC offset
     (dtype float32, full-scale fraction None, DC recorded and kept),
     int16 (full scale 32767), int32 (full scale unknown), a block header
     with non-zero scale (refused), a block header whose element-type
     bits disagree with the file header (refused), a header/procpar np
     mismatch (refused), a missing procpar (refused, also by the ADC
     check), an unparseable procpar and a procpar lacking sw (refused
     with the specific reason);
  6. the clock-audit expectation refinement on an Agilent expno degrades
     to a refine_note without a traceback;
  7.-11. v0.7.1 fixtures, each a rewrite of the packed bundle with fids
     the test writes itself (Varian layout, big-endian float32, line
     physics under the test's control -- independent of the generator's
     own options):
     * the generator's monotonic single-visit ladder is reported as
       'compression and drift degenerate (monotonic ladder)'; the same
       ladder with its started_local stamps removed as 'single-visit,
       time order unknown' (never 'monotonic'); a descending order is
       monotonic too, one run up then down (or down then up)
       'bracketed' -- SIU session 3's 1, 6.31, 31.6, 100, 100, 31.6,
       6.31, 1 -- and any other order 'randomized';
     * a sweep_signcal 1D whose carrier (procpar tof -> o1_hz) is moved
       +200 Hz against line-bearing references at the same rg/pw: the
       line written 200 Hz LOWER resolves sign +1 (physical), 200 Hz
       HIGHER resolves -1 (mirrored), 50 Hz higher stays unverified
       (ambiguous) -- with the exclusion on one mass axis (no mirror)
       when resolved and analysis/site_exclusion.py treating the session
       as sign-verified; a noise-only signcal 1D (no line) is refused as
       unusable by the line-quality gate (spectrum peak below 10 robust
       sigmas above the off-line median, or width sub-bin / pinned at
       the fit bound) and the sign stays unverified; every trial records
       the position-tied minimum displacement it had to exceed (these
       four fixtures carry procpars without reffrq/rfl/rfp, or with a
       referencing that agrees, so they test the signcal path alone);
     * the VnmrJ lock-referencing cross-check (zero extra acquisition):
       lock_referencing_check on the real SIU session-2 procpar values
       (reffrq 399.618776561 MHz, rfl 807.38928971 Hz, sw 6410.256 Hz,
       sfrq 399.6211743 MHz) finds the identity reffrq = sfrq - sw/2 +
       rfl - rfp to a millihertz and water ~500 Hz BELOW the carrier,
       so the measured +540.8 Hz is the MIRRORED placement (sign -1);
       the same numbers with the offset negated read physical, +100 Hz
       undetermined, rfl off by 10 Hz identity_failed, tn C13 or a
       water-free sample not_applicable, a missing key unavailable, a
       line within 2 tolerances of the carrier undetermined; on
       fixtures, line-bearing references with the generator's physical
       referencing resolve +1 (basis vnmrj_lock_referencing, one mass
       axis, site combiner sign-verified, QA OK), a mirrored referencing
       -1 with nu_L = carrier - offset, a signcal that agrees is
       recorded beside the primary calibration, a signcal that
       DISAGREES makes the sign a CONFLICT (unverified, QA FAIL naming
       both, mirror placement kept), an ambiguous signcal leaves the
       referencing to resolve the sign, a water-free sample and a
       broken identity leave it unverified;
     * the co-add alignment gate: 15 rows of a weak (+0.15) feature,
       ~2 sigma each, are NOT self-aligned (alignment_check.gate
       'per_row_significance_below_3sigma', median recorded, headline
       = the unaligned stack, which recovers the injected amplitude,
       QA WARN and honesty naming the gate) while 6 rows of a strong
       (+0.6) feature pass the gate and keep the aligned co-add;
     * eleven noise_tune blocks at three labeled tuning settings (pure
       dip / dispersive bump / mixed dip) plus two blocks without a
       tuning object: settings grouped by setting_index (fallback: one
       per expno, QA WARN), per-setting fits recover the injected
       lineshapes, the nearest optimum is the pure-dip setting (an RT
       probe's equilibrium sign) while the dispersive BUMP setting is
       excluded as the far-detuned side, a ladder of bumps only leaves
       the optimum undetermined, and the headline block keeps exactly
       its 3 noise-role rows;
     * --meta-override with room-temperature coil/preamp temperatures on
       an RT bump: recorded path by path (old/new), a leaf equal to the
       bundle's own value listed as unchanged (not an override), the
       source recorded by basename and sha256, temperature-contrast
       point computed with the statement that preamp_temp_k stands in
       for the amplifier noise temperature T_A, and the model's sign
       disagreement stated instead of forced; an override that changes
       nothing yields a QA OK row and no 'Overrides applied' line; a
       schema-invalid override is rejected with exit 1 and no report, a
       non-object one with exit 2;
     * a randomized 5-level x 3-visit ladder with injected per-level
       compression and a +0.8 %/min linear drift: the drift fit recovers
       beta within 2 sigma and every compression within 0.01, the
       envelope feeds the headline (time order 'randomized'); the same
       rungs without time stamps fall back to compression_only; one rung
       rewritten as an all-zero fid is excluded as featureless and the
       report still runs on the other 14.

With --real-bundle, each given bundle (the SIU Carbondale DD2 sessions)
is also run through the report and checked for: every expno read as
Agilent float32 with a zero-scale block header, nothing refused, the
noise expnos aggregated into one block, the detection's fit basis
consistent with the alignment cross-check and the headline fraction
derived from that same fit -- on session 1 (1% H2O, rows ~2.7 sigma)
the per-row significance gate fires and the headline stays the
unaligned ~0.27, on session 2 (90/10, rows ~11.6 sigma) it passes and
the aligned ~0.670 stands -- the reference pair recorded (with a QA
WARN when it is not matched), and science.axion_exclusion present: on
the matched-reference session (SIU session 2) the worst-case best g_ap
must lie within 15% of the maintainer's 3.05e-4 GeV^-1 scratch
cross-check AND each input must agree on its own --- kappa*M0 within 12%
of 7.77e5 counts (the scratch takes A0 from the first four FID samples,
the report from its fit), the damping FWHM within 10% of 19.3 Hz, the
positive-part line power within 20% of 0.270 counts^2 and P_90 between
0.9 and 1.5 times the scratch's 0.2776 counts^2 (the report integrates
|PSD - baseline| over the Lorentzian window fraction, the scratch the
positive part only) --- so a cancellation between inputs cannot pass;
on the unmatched session (SIU session 1) the exclusion must be marked
unmatched with each reference at its own tip and the SMALLER kappa*M0
used; on both the VnmrJ lock referencing resolves the axis sign as
MIRRORED (-1: reffrq puts water ~500 Hz below the carrier, the FFT
convention found it ~+540 Hz), so the exclusion sits on ONE physical
mass axis, nu_L = carrier - offset, best at 1.6526971 ueV, no mirror
placement, and the site combiner treats the session as sign-verified.

Exit 0 iff every check passes.
"""

from __future__ import print_function

import argparse
import copy
import datetime
import hashlib
import importlib.util
import json
import math
import os
import random
import struct
import subprocess
import sys
import tempfile
import zipfile

import numpy as np

TESTING = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(TESTING, ".."))
GENERATOR = os.path.join(REPO, "vendors", "agilent",
                         "make_synthetic_agilent_data.py")
PACKER = os.path.join(REPO, "packer", "pack_bundle.py")
REPORT = os.path.join(REPO, "analysis", "facility_report.py")

FAILURES = []


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    line = "%s : %s" % (tag, name)
    if detail and not ok:
        line += "\n       %s" % str(detail)[:400]
    print(line)
    if not ok:
        FAILURES.append(name)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def derive_bundle(src, dst, run_mode=None, drop_members=(),
                  replace_members=None, edit_meta=None):
    """Copy a bundle, optionally restamping software.run_mode, dropping
    zip members (with their checksum entries), replacing or ADDING
    members (with their checksum entries recomputed) and editing meta in
    place."""
    replace_members = replace_members or {}
    zin = zipfile.ZipFile(src)
    meta = json.loads(zin.read("meta.json"))
    if run_mode:
        meta["software"]["run_mode"] = run_mode
    for m in drop_members:
        meta["checksums"].pop(m, None)
    for m, data in replace_members.items():
        meta["checksums"][m] = "sha256:" + hashlib.sha256(data).hexdigest()
    if edit_meta:
        edit_meta(meta)
    existing = set(zin.namelist())
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in zin.namelist():
            if n == "meta.json" or n in drop_members:
                continue
            zout.writestr(n, replace_members.get(n, zin.read(n)))
        for n in sorted(replace_members):
            if n not in existing:
                zout.writestr(n, replace_members[n])
        zout.writestr("meta.json", json.dumps(meta, indent=1) + "\n")
    return meta


def procpar_edit(pp_bytes, name, value=None):
    """The generator's procpar with one real-valued record set to `value`
    (or removed when value is None). Records are three lines: header,
    '1 <value>', enumerable."""
    lines = pp_bytes.decode("ascii").split("\n")
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith(name + " "):
            if value is not None:
                out.extend([lines[i], "1 %.12g" % value, lines[i + 2]])
            i += 3
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).encode("ascii")


def procpar_get(pp_bytes, name):
    """The first value of a real-valued procpar record, or None."""
    lines = pp_bytes.decode("ascii").split("\n")
    for i, line in enumerate(lines):
        if line.startswith(name + " "):
            return float(lines[i + 1].split()[1])
    return None


def procpar_set(pp_bytes, name, value):
    """The generator's procpar with one record set to `value`: a number
    for a real record ('1 <value>'), a str for a string record
    ('1 "<value>"'). Records are three lines: header, values,
    enumerable."""
    lines = pp_bytes.decode("ascii").split("\n")
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith(name + " "):
            val = ('1 "%s"' % value) if isinstance(value, str) \
                else "1 %.12g" % value
            out.extend([lines[i], val, lines[i + 2]])
            i += 3
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).encode("ascii")


# ---------------------------------------------------------------------------
# v0.7.1 fixtures: line-bearing Varian fids written here, in the real DD2
# layout (32-byte file header, 28-byte block header, big-endian float32
# re/im), so the physics under test -- line positions, per-setting
# lineshapes, rung amplitudes with an injected drift -- is fully under the
# test's control and independent of the generator's options.
# ---------------------------------------------------------------------------

S_DATA, S_FLT = 0x1, 0x8
F0_LINE_HZ = 600.0          # the sample line's in-window offset (all fixtures)
FWHM_LINE_HZ = 12.0
FIXTURE_T0 = datetime.datetime(2026, 3, 1, 9, 2, 0)   # after the base session


def varian_fid_f32(row):
    """One-block, one-trace Varian fid of a complex row as big-endian
    float32 (status S_DATA|S_FLT, as the SIU DD2 writes)."""
    vals = np.empty(2 * row.size, dtype=">f4")
    vals[0::2] = row.real
    vals[1::2] = row.imag
    npts = int(vals.size)
    tbytes = npts * 4
    head = struct.pack(">6ihhi", 1, 1, npts, 4, tbytes, 28 + tbytes, 0,
                       S_DATA | S_FLT, 1)
    bhead = struct.pack(">4hi4f", 0, S_DATA | S_FLT, 1, 0, 1,
                        0.0, 0.0, 0.0, 0.0)
    return head + bhead + vals.tobytes()


def synth_noise_row(rng, n, fs, floor, a, b, f0, fwhm):
    """Complex series whose two-sided PSD is floor*(1 + (a + b*u)/(1+u^2)),
    u = (f - f0)/(fwhm/2) -- the report's own lineshape model, injected
    exactly (testing/make_physics_bundle.py's construction)."""
    f = np.fft.fftfreq(n, d=1.0 / fs)
    shape = np.ones(n)
    if a != 0.0 or b != 0.0:
        u = (f - f0) / (fwhm / 2.0)
        shape = shape + (a + b * u) / (1.0 + u ** 2)
    shape = np.clip(shape, 0.05, None)
    amp = np.sqrt(floor * shape * fs * n / 2.0)
    X = amp * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return np.fft.ifft(X)


def synth_line_row(rng, n, fs, a0, f0, fwhm, sigma):
    """Small-flip FID: decaying complex exponential at f0 (Lorentzian
    FWHM) over white noise of per-component sigma."""
    t = np.arange(n) / fs
    sig = a0 * np.exp((2j * np.pi * f0 - np.pi * fwhm) * t)
    return sig + sigma * (rng.standard_normal(n) + 1j * rng.standard_normal(n))


def vnmrj_stamp(dt):
    return dt.strftime("%Y%m%dT%H%M%S")


def iso_stamp(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


class FixtureBuilder(object):
    """Rewrites the packed synthetic Agilent bundle: line-bearing
    references, plus optional signcal / noise_tune / randomized-ladder /
    headline-bump experiments, each a new expno directory with its own
    procpar (edited copy of a generator procpar) and a float32 fid."""

    def __init__(self, base_zip, gen, seed):
        self.base = base_zip
        self.gen = gen
        self.zin = zipfile.ZipFile(base_zip)
        self.meta0 = json.loads(self.zin.read("meta.json"))
        self.rng = np.random.default_rng(seed)
        self.members = {}
        self.drop = set()
        self.new_exps = []
        self.replaced_exps = {}
        self.clock = FIXTURE_T0
        self.ladder_cal = None
        self.referencing = None

    def _exp0(self, expno):
        return next(e for e in self.meta0["experiments"]
                    if e["expno"] == expno)

    def _write_exp(self, expno, template_expno, role, row, procpar_edits,
                   meta_edits, seconds):
        """New expno from the template's procpar with the edits, the fid
        of `row`, and a meta entry copied from the template's; stamps
        run back to back from the fixture clock."""
        pp = self.zin.read("data/%d/procpar" % template_expno)
        n_total = 2 * row.size
        at_s = row.size / self.gen.SW_HZ
        t_run = self.clock
        t_done = t_run + datetime.timedelta(seconds=seconds)
        self.clock = t_done + datetime.timedelta(seconds=4)
        edits = {"np": n_total, "at": at_s, "time_run": vnmrj_stamp(t_run),
                 "time_complete": vnmrj_stamp(t_done),
                 "time_saved": vnmrj_stamp(t_done)}
        edits.update(procpar_edits)
        for k, v in edits.items():
            pp = procpar_set(pp, k, v)
        self.members["data/%d/procpar" % expno] = pp
        self.members["data/%d/fid" % expno] = varian_fid_f32(row)
        e = copy.deepcopy(self._exp0(template_expno))
        e.update({"expno": expno, "role": role, "td": n_total,
                  "aq_s_per_row": at_s, "started_local": iso_stamp(t_run),
                  "finished_local": iso_stamp(t_done)})
        e.update(meta_edits)
        self.new_exps.append(e)
        return e

    def replace_fid(self, expno, row, procpar_edits=None):
        """Replace an existing expno's fid (and np/at) in place, keeping
        its stamps and meta entry apart from td / aq."""
        pp = self.zin.read("data/%d/procpar" % expno)
        n_total = 2 * row.size
        at_s = row.size / self.gen.SW_HZ
        edits = {"np": n_total, "at": at_s}
        edits.update(procpar_edits or {})
        for k, v in edits.items():
            pp = procpar_set(pp, k, v)
        self.members["data/%d/procpar" % expno] = pp
        self.members["data/%d/fid" % expno] = varian_fid_f32(row)
        self.replaced_exps[expno] = {"td": n_total, "aq_s_per_row": at_s}

    def line_references(self, n=8192, a0=5000.0, sigma=20.0):
        """Both references carry the sample line at F0_LINE_HZ."""
        for expno in (11, 13):
            self.replace_fid(expno, synth_line_row(
                self.rng, n, self.gen.SW_HZ, a0, F0_LINE_HZ, FWHM_LINE_HZ,
                sigma))

    def signcal(self, expno, displacement_hz, apparent_shift_hz, n=8192,
                a0=5000.0, sigma=20.0):
        """A sweep_signcal 1D: the closing reference's pulse and gain,
        carrier (procpar tof, meta o1_hz) moved by displacement_hz, the
        line written at F0 + apparent_shift_hz (the test chooses the
        physics: -d physical, +d mirrored, anything else ambiguous)."""
        row = synth_line_row(self.rng, n, self.gen.SW_HZ, a0,
                             F0_LINE_HZ + apparent_shift_hz, FWHM_LINE_HZ,
                             sigma)
        return self._write_exp(
            expno, 13, "sweep_signcal", row,
            {"tof": self.gen.TOF_HZ + displacement_hz},
            {"o1_hz": self.gen.TOF_HZ + displacement_hz}, seconds=3)

    def noise_block(self, expno, role, a, b, n=131072, tuning=None,
                    floor=200.0):
        """A pulse-free block (Tier-1 single row) with the report's
        lineshape injected at F0: role 'noise' (headline) or
        'noise_tune' with the tuning object."""
        row = synth_noise_row(self.rng, n, self.gen.SW_HZ, floor, a, b,
                              F0_LINE_HZ, FWHM_LINE_HZ)
        edits = {"tuning": tuning} if tuning is not None else {}
        return self._write_exp(expno, 12, role, row, {}, edits,
                               seconds=int(math.ceil(n / self.gen.SW_HZ)))

    def headline_bump(self, a, b, n=131072):
        """The three headline noise expnos rewritten with an injected
        feature (their stamps and expnos unchanged)."""
        for expno in self.gen.NOISE_EXPNOS:
            self.replace_fid(expno, synth_noise_row(
                self.rng, n, self.gen.SW_HZ, 200.0, a, b, F0_LINE_HZ,
                FWHM_LINE_HZ))

    def randomized_ladder(self, first_expno, levels_db, visits, compression,
                          beta_per_min, step_s=12, n=8192, a0_per_rg=100.0,
                          sigma_per_rg=0.5, shuffle_seed=3):
        """The v0.7.1 ladder: every integer-dB level visited `visits`
        times in one random permutation, expnos in acquisition order,
        started_local step_s apart, rung amplitude
            a0_per_rg x rg x compression[level] x (1 + beta (t - t0))
        with noise scaled by rg. Replaces the generator's ladder (its
        expnos and members dropped). Returns [(expno, level_db, t_min)]."""
        order = [db for db in levels_db for _ in range(visits)]
        random.Random(shuffle_seed).shuffle(order)
        for expno, _tag, _gain in self.gen.LADDER:
            self.drop.update("data/%d/%s" % (expno, fn)
                             for fn in ("fid", "procpar", "text"))
        t0 = self.clock
        plan, cal = [], []
        for k, db in enumerate(order):
            expno = first_expno + k
            rg = 10.0 ** (db / 20.0)
            t_min = k * step_s / 60.0
            amp = a0_per_rg * rg * compression[db] * (1.0 + beta_per_min * t_min)
            row = synth_line_row(self.rng, n, self.gen.SW_HZ, amp,
                                 F0_LINE_HZ, FWHM_LINE_HZ, sigma_per_rg * rg)
            self.clock = t0 + datetime.timedelta(seconds=k * step_s)
            self._write_exp(expno, 10, "rg_ladder", row, {"gain": float(db)},
                            {"rg": rg}, seconds=3)
            plan.append((expno, db, t_min))
            cal.append({"expno": expno, "rg": rg, "tip_deg": 1.0})
        self.ladder_cal = cal
        return plan

    def set_referencing(self, sign, f_app=F0_LINE_HZ, rfl_shift_hz=0.0):
        """Every procpar of the written bundle re-referenced so that
        water's PHYSICAL offset from the carrier is sign x f_app:
        reffrq = (sfrq + sign f_app) / (1 + 4.75e-6), rfl from the
        VnmrJ identity reffrq = sfrq - sw/2 + rfl - rfp (plus
        rfl_shift_hz, to break the identity on purpose)."""
        self.referencing = ("set", sign, f_app, rfl_shift_hz)

    def strip_referencing(self):
        """Every procpar without reffrq/rfl/rfp (a console whose
        referencing was not saved): the lock-referencing check must
        report 'unavailable'."""
        self.referencing = ("strip",)

    def _apply_referencing(self):
        if self.referencing is None:
            return
        names = set(n for n in self.zin.namelist()
                    if n.startswith("data/") and n.endswith("/procpar")
                    and n not in self.drop)
        names |= set(n for n in self.members
                     if n.startswith("data/") and n.endswith("/procpar"))
        for name in sorted(names):
            pp = self.members.get(name) or self.zin.read(name)
            if self.referencing[0] == "strip":
                for key in ("reffrq", "rfl", "rfp"):
                    pp = procpar_edit(pp, key, None)
            else:
                _op, sign, f_app, rfl_shift = self.referencing
                sfrq_hz = procpar_get(pp, "sfrq") * 1e6
                sw_hz = procpar_get(pp, "sw")
                reffrq_hz = (sfrq_hz + sign * f_app) / (1.0 + 4.75e-6)
                rfl_hz = reffrq_hz - sfrq_hz + sw_hz / 2.0 + rfl_shift
                pp = procpar_set(pp, "reffrq", reffrq_hz / 1e6)
                pp = procpar_set(pp, "rfl", rfl_hz)
                pp = procpar_set(pp, "rfp", 0.0)
            self.members[name] = pp

    def write(self, dst, run_mode="synthetic-injection"):
        self._apply_referencing()
        drop_expnos = set(int(m.split("/")[1]) for m in self.drop)
        new_exps = self.new_exps
        replaced = self.replaced_exps
        ladder_cal = self.ladder_cal

        def edit(meta):
            exps = [e for e in meta["experiments"]
                    if e["expno"] not in drop_expnos]
            for e in exps:
                if e["expno"] in replaced:
                    e.update(replaced[e["expno"]])
            meta["experiments"] = exps + new_exps
            if ladder_cal is not None:
                meta["calibration"]["rg_ladder"] = ladder_cal

        return derive_bundle(self.base, dst, run_mode=run_mode,
                             drop_members=tuple(sorted(self.drop)),
                             replace_members=self.members, edit_meta=edit)


def run_report(bundle, out_dir, extra_args=()):
    subprocess.check_call([sys.executable, REPORT, bundle, "--out", out_dir]
                          + list(extra_args), stdout=subprocess.DEVNULL)
    with open(os.path.join(out_dir, "report.json")) as fh:
        report = json.load(fh)
    with open(os.path.join(out_dir, "report.html")) as fh:
        html = fh.read()
    return report, html


def run_report_expect_failure(bundle, out_dir, extra_args=()):
    """(returncode, stdout) of a report run that must refuse; the output
    directory must end up without a report.json."""
    proc = subprocess.run([sys.executable, REPORT, bundle, "--out", out_dir]
                          + list(extra_args), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def fmt3(v):
    return ("%.3f" % v) if finite(v) else str(v)


def fit_basis_consistent(sci):
    """detection.fit_basis names the fit the headline numbers came from,
    and it is the unaligned one exactly when the per-row significance
    gate fired or the alignment cross-check is SUSPECT. Returns (ok,
    detail)."""
    noise = sci.get("noise") or {}
    det = sci.get("detection") or {}
    hd = sci.get("headline") or {}
    basis = det.get("fit_basis")
    ac = noise.get("alignment_check") or {}
    suspect = bool(ac.get("suspect"))
    gated = bool(ac.get("gate_fired"))
    want = "unaligned_fit" if (suspect or gated) else "coadd_fit"
    fit = noise.get(basis) or {}
    ok = (basis == want and noise.get("headline_fit") == want
          and fit.get("amp_norm") == det.get("fit_amp_norm")
          and fit.get("amp_err") == det.get("fit_amp_err"))
    if ok and det.get("detected"):
        a = fit["amp_norm"]
        frac = a / (1.0 + a) if a > 0 else abs(a)
        ok = abs((hd.get("spin_coupled_floor_fraction") or 0) - frac) < 1e-9
        if suspect or gated:
            ok = ok and "UNALIGNED" in det.get("fit_basis_note", "") \
                and (sci.get("floor_calibration") or {}).get(
                    "spin_line_fit_basis", "unaligned_fit") == "unaligned_fit"
    detail = "basis=%s want=%s suspect=%s gated=%s headline=%s det=%s" % (
        basis, want, suspect, gated, hd.get("spin_coupled_floor_fraction"),
        {k: det.get(k) for k in ("detected", "fit_amp_norm", "fit_amp_err")})
    return ok, detail


def check_real_bundle(path, work):
    """Invariants of the read path and headline plumbing on a real
    Agilent bundle."""
    tag = os.path.basename(path)
    out_dir = os.path.join(work, "report_real_" + tag[:-4])
    report, html = run_report(path, out_dir)
    sci = report.get("science") or {}
    meta_real = json.loads(zipfile.ZipFile(path).read("meta.json"))
    exps = meta_real["experiments"]
    h2o = (meta_real.get("sample") or {}).get("h2o_fraction_pct") or 0.0
    noise_expnos = [e["expno"] for e in exps if e["role"] == "noise"]
    rr = sci.get("raw_data_read") or {}
    by = rr.get("by_expno") or {}
    check("%s: science report, every expno read (%d), none refused"
          % (tag, len(exps)),
          report.get("report_type") == "science" and len(by) == len(exps)
          and not rr.get("refused"), json.dumps(rr.get("refused"))[:300])
    check("%s: every expno Agilent float32, 1 row, block header scale 0 "
          "recorded, DC recorded not subtracted" % tag,
          all(v.get("format") == "agilent" and v.get("dtype") == "float32"
              and v.get("n_rows") == 1
              and (v.get("fid_block_header") or {}).get("scale") == 0
              and v.get("dc_offset_subtracted") is False
              and finite(v.get("dc_offset_max_abs")) for v in by.values()),
          json.dumps(list(by.values())[:1])[:300])
    noise = sci.get("noise") or {}
    check("%s: %d noise expnos aggregated into one block" % (tag,
                                                             len(noise_expnos)),
          noise.get("n_rows") == len(noise_expnos)
          and noise.get("expnos") == noise_expnos
          and (sci.get("noise_aggregation") or {}).get("consistent") is True,
          "n_rows=%s" % noise.get("n_rows"))
    ok, detail = fit_basis_consistent(sci)
    check("%s: detection fit basis consistent with the alignment "
          "cross-check and the headline fraction" % tag, ok, detail)
    ac = noise.get("alignment_check") or {}
    qa = sci.get("qa_flags") or []
    align_rows = [q for q in qa if q["check"] == "co-add alignment"]
    med = ac.get("per_row_significance_median")
    if h2o < 10.0:
        # SIU session 1 (1% H2O, Gd): rows ~2.7 sigma at the stack's line
        check("%s: weak rows (median %s sigma < 3) -> alignment gate "
              "'per_row_significance_below_3sigma' fires, rows NOT self-"
              "aligned, headline = the unaligned stack (~0.27), aligned "
              "co-add recorded for reference, outcome check not run, QA "
              "WARN and honesty name the gate, HTML says 'gated off'"
              % (tag, fmt3(med)),
              ac.get("gate_fired") is True
              and ac.get("gate") == "per_row_significance_below_3sigma"
              and finite(med) and 1.5 < med < 3.0
              and ac.get("suspect") is False
              and "not run" in ac.get("outcome_check", "")
              and noise.get("headline_fit") == "unaligned_fit"
              and 0.22 < noise["unaligned_fit"]["amp_norm"] < 0.32
              and finite((noise.get("coadd_fit") or {}).get("amp_norm"))
              and "GATED" in ac.get("verdict", "")
              and len(align_rows) == 1 and align_rows[0]["level"] == "WARN"
              and "gate fired" in align_rows[0]["detail"]
              and any("gated off" in h for h in sci.get("honesty") or [])
              and "gated off" in html and "UNALIGNED stack" in html
              and "%.3f" % noise["unaligned_fit"]["amp_norm"] in html
              and all(finite(pr.get("npe_at_stack_line"))
                      for pr in noise.get("per_row") or []),
              json.dumps({k: ac.get(k) for k in (
                  "gate", "gate_fired", "per_row_significance_median",
                  "suspect", "aligned_amp", "unaligned_amp")}))
    else:
        # SIU session 2 (90/10): rows ~11.6 sigma, aligned and unaligned agree
        check("%s: strong rows (median %s sigma >= 3) -> alignment gate "
              "passes, outcome check run and not SUSPECT, headline = the "
              "aligned co-add (~0.670), no QA row" % (tag, fmt3(med)),
              ac.get("gate_fired") is False and ac.get("gate") == "passed"
              and finite(med) and med >= 3.0
              and ac.get("suspect") is False
              and "run:" in ac.get("outcome_check", "")
              and noise.get("headline_fit") == "coadd_fit"
              and 0.65 < noise["coadd_fit"]["amp_norm"] < 0.69
              and not align_rows,
              json.dumps({k: ac.get(k) for k in (
                  "gate", "gate_fired", "per_row_significance_median",
                  "suspect", "aligned_amp", "unaligned_amp")}))
    adc = [q for q in qa if q["check"].startswith("ADC check")]
    check("%s: ADC checks: %d rows, all float32 'not applicable', no FAIL"
          % (tag, len(adc)),
          len(adc) == len(exps) and all(
              q["level"] == "OK" and "float32" in q["detail"]
              and "not applicable" in q["detail"] for q in adc))
    pair = sci.get("reference_pair") or {}
    warned = any(q["check"] == "reference pair" and q["level"] == "WARN"
                 for q in qa)
    check("%s: reference pair recorded (matched=%s) with QA WARN iff "
          "unmatched" % (tag, pair.get("matched")),
          isinstance(pair.get("matched"), bool) and len(pair.get(
              "pulses") or []) == 2 and warned == (not pair["matched"])
          and ((not pair["matched"]) == ("NOT a matched pair" in html)),
          json.dumps(pair)[:300])
    if not pair.get("matched"):
        check("%s: unmatched pair caveat sits next to the A0 ratio" % tag,
              "a0_ratio_caveat" in (sci.get("floor_calibration") or {}))
    ex = sci.get("axion_exclusion") or {}
    res = ex.get("result") or {}
    curve = ex.get("curve") or {}
    check("%s: science.axion_exclusion available, finite worst-case and "
          "nominal best g, curve arrays of one length" % tag,
          ex.get("available") is True
          and finite(res.get("g90_worst_best_gev_inv"))
          and res["g90_worst_best_gev_inv"] > 0
          and finite(res.get("g90_nominal_best_gev_inv"))
          and res["g90_nominal_best_gev_inv"] < res["g90_worst_best_gev_inv"]
          and len(curve.get("m_a_ev") or []) == len(curve.get("g90_worst")
                                                    or []) > 0
          and len(curve["m_a_ev"]) == len(curve.get("g90_nominal") or []),
          json.dumps(res or ex)[:300])
    if not ex.get("available"):
        return
    tr = ex["transduction"]
    sp = ex["signal_power"]
    g = res["g90_worst_best_gev_inv"]
    if pair.get("matched"):
        check("%s: matched references -> worst-case best g %.3g GeV^-1 "
              "within 15%% of the 3.05e-4 scratch cross-check, kappa*M0 "
              "the mean of both references at the noise rg" % (tag, g),
              abs(g / 3.05e-4 - 1.0) < 0.15
              and tr["matched_references"] is True
              and tr["rg_bridged"] is False and len(tr["references"]) == 2
              and abs(tr["kappa_M0_counts"] - sum(
                  p["kappa_M0_counts"] for p in tr["references"]) / 2.0)
              < 1e-6 * tr["kappa_M0_counts"],
              json.dumps(tr)[:400])
        dmw = ex["damping"]["fwhm_used_hz"]
        check("%s: each exclusion input agrees with the scratch on its own "
              "--- kappa*M0 %.4g (7.77e5 +/-12%%), damping FWHM %.2f Hz "
              "(19.3 +/-10%%), positive-part line power %.4g (0.270 "
              "+/-20%%), P_90 %.4g (0.9..1.5 x 0.2776), tip 1 deg from "
              "pw 0.05 / p90 4.5 us at tpwr 56 dB"
              % (tag, tr["kappa_M0_counts"], dmw,
                 sp["P_positive_part_counts2"], sp["P_90_counts2"]),
              abs(tr["kappa_M0_counts"] / 7.77e5 - 1.0) < 0.12
              and abs(dmw / 19.3 - 1.0) < 0.10
              and abs(sp["P_positive_part_counts2"] / 0.270 - 1.0) < 0.20
              and 0.9 < sp["P_90_counts2"] / 0.2776 < 1.5
              and sp["P_absolute_excess_counts2"]
              >= sp["P_positive_part_counts2"]
              and abs(sp["P_line_counts2"] - sp["P_absolute_excess_counts2"]
                      / sp["lorentzian_window_fraction"]) < 1e-9
              and all(abs(p["tip_deg"] - 1.0) < 1e-9
                      and p["pulse_us"] == 0.05 and p["power_db"] == 56.0
                      for p in tr["references"]),
              json.dumps({"km0": tr["kappa_M0_counts"], "fwhm": dmw,
                          "sp": sp})[:500])
    else:
        kms = [p["kappa_M0_counts"] for p in tr["references"]]
        check("%s: unmatched references -> marked, each reference at its "
              "own tip (%s deg), the SMALLER kappa*M0 (%.4g of %s) used, "
              "said in JSON, honesty and HTML"
              % (tag, [round(p["tip_deg"], 3) for p in tr["references"]],
                 tr["kappa_M0_counts"], ["%.4g" % k for k in kms]),
              tr["matched_references"] is False and len(kms) == 2
              and abs(tr["kappa_M0_counts"] - min(kms)) < 1e-9 * min(kms)
              and len(set(round(p["tip_deg"], 6)
                          for p in tr["references"])) == 2
              and "NOT a matched pair" in tr["basis"]
              and any(h.startswith("Unmatched references")
                      for h in ex.get("honesty") or [])
              and "SMALLER" in html, json.dumps(tr)[:400])
    fas = sci.get("frequency_axis_sign") or {}
    rc = fas.get("referencing_check") or {}
    f_app = sci.get("line_position_guess_hz")
    check("%s: axis sign VERIFIED -1 (mirrored) by the VnmrJ lock "
          "referencing -- reffrq 399.618776561 MHz, identity to 2 Hz, "
          "water predicted ~-500 Hz vs measured %s Hz, tolerance ~120 Hz, "
          "no two-way ambiguity, no calibration attempt"
          % (tag, fmt3(f_app)),
          fas.get("verified") is True and fas.get("sign") == -1
          and fas.get("basis") == "vnmrj_lock_referencing"
          and rc.get("verdict") == "mirrored" and rc.get("sign") == -1
          and abs(rc.get("reffrq_mhz", 0) - 399.618776561) < 1e-8
          and abs(rc.get("rfl_hz", 0) - 807.389) < 0.01
          and rc.get("rfp_hz") == 0.0
          and abs(rc.get("sw_hz", 0) - 6410.25641026) < 1e-6
          and abs(rc.get("sfrq_mhz", 0) - 399.6211743) < 1e-9
          and abs(rc.get("identity_residual_hz", 9)) < 2.0
          and rc.get("delta_ppm_assumed") == 4.75
          and abs(rc.get("predicted_offset_hz", 0) + 499.5) < 3.0
          and rc.get("measured_offset_hz") == f_app
          and 110.0 < rc.get("tolerance_hz", 0) < 130.0
          and rc.get("source_expno") in (11, 13)
          and "two_way_ambiguity_ppm" not in fas
          and "calibration" not in fas
          and "MIRRORED" in fas.get("note", ""),
          json.dumps(rc)[:700])
    check("%s: exclusion on ONE physical mass axis: axis_sign -1, nu_L = "
          "carrier - offset, best mass 1.6526971 ueV (the former mirror "
          "placement), no mirror mass/band/curve, sign note names the "
          "referencing" % tag,
          ex["line"]["sign_verified"] is True
          and ex["line"]["axis_sign"] == -1
          and ex["line"]["axis_sign_basis"] == "vnmrj_lock_referencing"
          and abs(ex["line"]["nu_L_hz_nominal"]
                  - (ex["line"]["carrier_mhz"] * 1e6
                     - ex["line"]["offset_hz"])) < 1e-3
          and abs(res["m_a_at_best_uev"] - 1.6526971) < 5e-7
          and "m_a_uev_mirror" not in ex["line"]
          and "m_a_at_best_mirror_uev" not in res
          and sorted(res["band_10x_uev"]) == ["nominal"]
          and "m_a_ev_mirror" not in curve
          and "NEGATED" in ex["line"]["basis"]
          and "lock referencing" in ex["line"].get("sign_note", "")
          and not any("INTERSECTION" in h for h in ex.get("honesty") or []),
          json.dumps({"line": ex["line"], "m_a": res.get("m_a_at_best_uev"),
                      "bands": sorted(res["band_10x_uev"])})[:600])
    sign_rows = [q for q in qa if q["check"] == "frequency-axis sign"]
    check("%s: QA OK row for the sign, honesty RESOLVED line, HTML names "
          "the VnmrJ lock referencing and MIRRORED, no 'SIGN unverified', "
          "mass bookkeeping without the sign caveat, headline block carries "
          "sign -1 / basis" % tag,
          len(sign_rows) == 1 and sign_rows[0]["level"] == "OK"
          and any("RESOLVED by the VnmrJ lock referencing" in h
                  for h in sci.get("honesty") or [])
          and "VnmrJ lock referencing" in html and "MIRRORED" in html
          and "SIGN unverified" not in html
          and "offset_sign_caveat" not in (
              sci.get("axion_mass_bookkeeping") or {})
          and noise.get("axis_sign_unverified") is False
          and noise.get("axis_sign") == -1
          and noise.get("axis_sign_basis") == "vnmrj_lock_referencing")
    site_mod = load_module("snn_site_excl_real",
                           os.path.join(REPO, "analysis", "site_exclusion.py"))
    site = site_mod.combine_reports([(os.path.join(out_dir, "report.json"),
                                      report)])
    comb = site.get("combined") or {}
    check("%s: the site combiner treats the session as sign-verified "
          "(nominal mass axis, no unverified session)" % tag,
          site.get("n_sessions") == 1
          and comb.get("sign_unverified_sessions") == []
          and site["sessions"][0]["sign_verified"] is True
          and site["sessions"][0]["band_10x_basis"] == "nominal mass axis"
          and abs(site["sessions"][0]["m_a_at_best_uev"]
                  - res["m_a_at_best_uev"]) < 1e-12,
          json.dumps(site.get("sessions"))[:400])
    dm = ex["damping"]
    check("%s: Gamma = pi x max(noise POWER FWHM %.2f, reference MAGNITUDE "
          "FWHM %.2f / sqrt3) Hz, both widths recorded"
          % (tag, dm["noise_line_power_fwhm_hz"] or 0,
             dm["reference_magnitude_fwhm_hz"] or 0),
          abs(dm["fwhm_used_hz"] - max(dm["noise_line_power_fwhm_hz"],
                                       dm["reference_magnitude_fwhm_hz"]
                                       / 3 ** 0.5)) < 1e-9
          and abs(dm["gamma_per_s"] - math.pi * dm["fwhm_used_hz"]) < 1e-9,
          json.dumps(dm)[:300])
    check("%s: D_cal 4.6 recorded (no gain bridge on this session)" % tag,
          ex["calibration_derating"]["D_cal"] == 4.6
          and ex["calibration_derating"]["D_cal_pilot"] == 4.6)
    lad = sci.get("rg_ladder") or {}
    check("%s: single-visit monotonic ladder -> degenerate_monotonic with "
          "'compression and drift degenerate (monotonic ladder)', envelope "
          "still feeds the headline" % tag,
          lad.get("model") == "degenerate_monotonic"
          and "compression and drift degenerate (monotonic ladder)"
          in lad.get("note", "")
          and abs((sci.get("headline") or {}).get(
              "rg_linearity_power_envelope", -1)
              - lad.get("max_abs_power_deviation", -2)) < 1e-12,
          json.dumps({k: lad.get(k) for k in ("model", "note")})[:300])
    check("%s: no noise_tune / sweep_signcal -> tuning_ladder null, sign "
          "status without a calibration attempt, meta_overrides null" % tag,
          sci.get("tuning_ladder") is None
          and "calibration" not in (sci.get("frequency_axis_sign") or {})
          and sci.get("meta_overrides") is None)
    check("%s: HTML card present with the inputs, the time-independence "
          "line, and no 'published' anywhere" % tag,
          "Axion-coupling exclusion (worst-case, this session)" in html
          and "does NOT improve with more measurement time" in html
          and "%.3g" % g in html
          and "publication" not in html.lower()
          and html.lower().count("published")
          == html.lower().count("unpublished"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--real-bundle", action="append", default=[],
                    help="a real Agilent bundle zip to run the invariant "
                         "checks on (repeatable; read-only)")
    args = ap.parse_args(argv)
    work = args.out_dir or tempfile.mkdtemp(prefix="agilent_report_test_")
    if not os.path.isdir(work):
        os.makedirs(work)

    gen = load_module("snn_agilent_gen", GENERATOR)
    sys.path.insert(0, os.path.join(REPO, "analysis"))
    import facility_report as fr

    # ------------------------------------------------------------------
    # 1. synthetic session -> packed bundle
    # ------------------------------------------------------------------
    session = subprocess.check_output(
        [sys.executable, GENERATOR, "--out-dir", work],
        stderr=subprocess.DEVNULL).decode().strip().splitlines()[-1]
    bundle = subprocess.check_output(
        [sys.executable, PACKER, session, "--answers",
         os.path.join(session, "answers_packer.json"), "--vendor", "agilent",
         "--out-dir", work],
        stderr=subprocess.DEVNULL).decode().strip().splitlines()[-1]
    check("synthetic Agilent session packed", os.path.isfile(bundle), bundle)
    meta0 = json.loads(zipfile.ZipFile(bundle).read("meta.json"))
    check("packed bundle is stamped desktest (never data)",
          meta0["software"].get("run_mode") == "desktest")

    # ------------------------------------------------------------------
    # 2./3. science-path copy
    # ------------------------------------------------------------------
    sci_bundle = os.path.join(work, os.path.basename(bundle)[:-9]
                              + "_beef.zip")
    meta = derive_bundle(bundle, sci_bundle, run_mode="synthetic-injection")
    report, html = run_report(sci_bundle, os.path.join(work, "report_sci"))
    sci = report.get("science") or {}
    check("science path ran (report_type synthetic-validation)",
          report.get("report_type") == "synthetic-validation"
          and bool(sci), report.get("report_type"))

    exps = meta["experiments"]
    by_role = {}
    for e in exps:
        by_role.setdefault(e["role"], []).append(e)
    noise_expnos = [e["expno"] for e in by_role["noise"]]
    n_complex = {e["expno"]: e["td"] // 2 for e in exps}

    rr = sci.get("raw_data_read") or {}
    by = rr.get("by_expno") or {}
    check("every experiment read (%d of %d), none refused"
          % (len(by), len(exps)),
          len(by) == len(exps) and not rr.get("refused"),
          "refused=%s" % rr.get("refused"))
    check("every experiment read as format 'agilent', dtype int32 "
          "(from the fid status bits), 1 row, generator point count",
          all(by.get(str(e["expno"]), {}).get("format") == "agilent"
              and by[str(e["expno"])].get("dtype") == "int32"
              and by[str(e["expno"])].get("n_rows") == 1
              and by[str(e["expno"])].get("n_points_complex")
              == n_complex[e["expno"]]
              for e in exps),
          json.dumps(by)[:400])
    check("point counts: noise %d complex, reference/ladder %d complex"
          % (gen.NOISE_NP // 2, gen.REF_NP // 2),
          all(by[str(e)]["n_points_complex"] == gen.NOISE_NP // 2
              for e in noise_expnos)
          and all(by[str(e["expno"])]["n_points_complex"] == gen.REF_NP // 2
                  for e in by_role["reference_open"] + by_role["rg_ladder"]))
    check("block header verified and recorded on every row (scale 0, "
          "status agrees with the file header); DC recorded, NOT subtracted",
          all((v.get("fid_block_header") or {}).get("scale") == 0
              and (v["fid_block_header"].get("status") & 0xC)
              == (v.get("fid_status") & 0xC)
              and v.get("dc_offset_subtracted") is False
              and finite(v.get("dc_offset_max_abs")) for v in by.values()),
          json.dumps(list(by.values())[:1])[:300])
    check("read-path note states the block-header check and that the DC "
          "is not subtracted",
          "scale must be 0" in rr.get("agilent_note", "")
          and "NOT subtracted" in rr.get("agilent_note", ""))

    noise = sci.get("noise") or {}
    agg = sci.get("noise_aggregation") or {}
    check("noise aggregation: %d noise expnos -> ONE consistent block"
          % len(noise_expnos),
          agg.get("n_parameter_groups") == 1 and agg.get("consistent") is True
          and agg.get("headline_expnos") == noise_expnos, json.dumps(agg)[:300])
    check("headline noise block has n_rows == number of noise expnos",
          noise.get("n_rows") == len(noise_expnos)
          and noise.get("n_experiments") == len(noise_expnos)
          and noise.get("expnos") == noise_expnos,
          "n_rows=%s expnos=%s" % (noise.get("n_rows"), noise.get("expnos")))
    per_row = noise.get("per_row") or []
    check("per-row records carry source expno (meta order) and started_local",
          [pr.get("expno") for pr in per_row] == noise_expnos
          and all(pr.get("started_local") for pr in per_row),
          str([(pr.get("expno"), pr.get("started_local"))
               for pr in per_row]))
    check("noise block is Agilent-format, rg 10^(60/20)",
          noise.get("data_format") == "agilent"
          and abs((noise.get("rg") or 0) - 10.0 ** (gen.NOISE_GAIN_DB / 20.0))
          < 1e-6 and noise.get("axis_sign_unverified") is True)

    # white complex noise, independent re/im of variance sigma^2, sampled
    # at sw: two-sided PSD = 2*sigma^2/sw (counts^2/Hz)
    expect_psd = 2.0 * 1000.0 ** 2 / gen.SW_HZ
    meds = [pr.get("psd_median_in_band") for pr in per_row]
    check("per-row PSD medians finite and within 15%% of the generator's "
          "white-noise floor %.0f counts^2/Hz" % expect_psd,
          meds and all(finite(m) and abs(m / expect_psd - 1.0) < 0.15
                       for m in meds), str(meds))
    stats_ok = True
    for pr in per_row:
        for k in ("nseg", "n_spikes", "resolution_hz", "nperseg"):
            if not finite(pr.get(k)):
                stats_ok = False
        ft = pr.get("fit")
        if ft and not all(finite(ft.get(k)) for k in
                          ("amp_norm", "amp_err", "center_hz", "fwhm_hz")):
            stats_ok = False
    check("per-row statistics all finite", stats_ok,
          json.dumps(per_row)[:400])
    check("row length is the noise expno's aq (np/(2 sw)) in seconds",
          abs((noise.get("row_seconds") or 0)
              - gen.NOISE_NP / (2.0 * gen.SW_HZ)) < 1e-9,
          noise.get("row_seconds"))

    refs = sci.get("references") or []
    check("both references readable, 1 row x %d complex points, Agilent "
          "format, group delay 0 (no Bruker GRPDLY default)"
          % (gen.REF_NP // 2),
          len(refs) == 2 and all(
              r.get("readable") and r.get("n_rows") == 1
              and r.get("n_points_complex") == gen.REF_NP // 2
              and r.get("data_format") == "agilent"
              and r.get("group_delay_points_used") == 0 for r in refs),
          json.dumps([{k: r.get(k) for k in (
              "expno", "readable", "n_rows", "n_points_complex",
              "data_format", "group_delay_points_used")} for r in refs]))
    check("reference rg = 10^(%.0f/20) from procpar gain" % gen.REF_GAIN_DB,
          all(abs(r.get("rg", 0) - 10.0 ** (gen.REF_GAIN_DB / 20.0)) < 1e-6
              for r in refs))
    pair = sci.get("reference_pair") or {}
    check("references recorded as a MATCHED pair (same pw/tpwr), no QA WARN",
          pair.get("matched") is True and len(pair.get("pulses") or []) == 2
          and not any(q["check"] == "reference pair"
                      for q in sci.get("qa_flags") or [])
          and "matched pair" in html and "NOT a matched pair" not in html,
          json.dumps(pair)[:300])

    lad = sci.get("rg_ladder") or {}
    rungs = lad.get("rungs") or []
    check("RG ladder: 4 rungs read, rg %s, %d complex points each, "
          "finite amplitudes, group delay 0"
          % ([10.0 ** (g / 20.0) for _e, _t, g in gen.LADDER], gen.REF_NP // 2),
          lad.get("available") is True and len(rungs) == 4
          and [r["expno"] for r in rungs] == [e for e, _t, _g in gen.LADDER]
          and all(abs(r["rg"] - 10.0 ** (g / 20.0)) < 1e-6
                  for r, (_e, _t, g) in zip(rungs, gen.LADDER))
          and all(r.get("n_points_complex") == gen.REF_NP // 2
                  and finite(r.get("amplitude_counts"))
                  and r.get("group_delay_points_used") == 0 for r in rungs)
          and not lad.get("grpdly_note"),
          json.dumps(lad)[:400])

    det = sci.get("detection") or {}
    check("detection ran on the aggregated block (not 'unavailable')",
          not det.get("unavailable") and finite(det.get("amp_significance")),
          json.dumps(det)[:300])
    ok, detail = fit_basis_consistent(sci)
    check("detection states its fit basis, consistent with the alignment "
          "cross-check and the headline", ok, detail)
    check("frequency-axis sign marked UNVERIFIED for the Agilent bundle",
          (sci.get("frequency_axis_sign") or {}).get("verified") is False
          and "UNVERIFIED" in ((sci.get("frequency_axis_sign") or {})
                               .get("note") or ""))
    rc0 = (sci.get("frequency_axis_sign") or {}).get("referencing_check") or {}
    check("line-free references -> the lock-referencing cross-check is "
          "'unusable' (no reference line to place), stated in the note and "
          "the honesty line, sign stays unverified",
          rc0.get("verdict") == "unusable" and rc0.get("sign") is None
          and "usable line" in rc0.get("why", "")
          and "lock-referencing cross-check" in (
              (sci.get("frequency_axis_sign") or {}).get("note") or "")
          and any("lock-referencing cross-check is unusable" in h
                  for h in sci.get("honesty") or []),
          json.dumps(rc0)[:400])
    check("axion-mass bookkeeping carries the offset-sign caveat",
          "SIGN" in ((sci.get("axion_mass_bookkeeping") or {})
                     .get("offset_sign_caveat") or ""))
    qa = sci.get("qa_flags") or []
    fails = [q for q in qa if q.get("level") == "FAIL"]
    check("no QA FAIL on the clean bundle", not fails, json.dumps(fails)[:300])
    adc = [q for q in qa if q["check"].startswith("ADC check")]
    check("ADC checks are dtype-aware: %d int32 rows, S_32 full scale NOT "
          "assumed ('not applicable'), none 'float64'" % len(adc),
          len(adc) == len(exps) and all(
              "int32" in q["detail"] and "not applicable" in q["detail"]
              and "full scale" not in q["detail"].split("not applicable")[1]
              for q in adc)
          and not any("float64" in q["detail"] for q in adc),
          json.dumps(adc)[:300])
    check("HTML states aggregation, read path and the sign caveat",
          "aggregated as ONE noise block" in html
          and "Raw-data read path" in html and "UNVERIFIED" in html)
    ex = sci.get("axion_exclusion") or {}
    res = ex.get("result") or {}
    check("axion exclusion present with finite numbers (worst-case and "
          "nominal best g, mass, band, 1100-point curve)",
          ex.get("available") is True
          and finite(res.get("g90_worst_best_gev_inv"))
          and res["g90_worst_best_gev_inv"] > 0
          and finite(res.get("g90_nominal_best_gev_inv"))
          and finite(res.get("m_a_at_best_uev"))
          and all(finite(v) for v in res.get("band_10x_offset_hz") or [None])
          and len((ex.get("curve") or {}).get("g90_worst") or []) == 1100
          and all(finite(v) and v > 0 for v in ex["curve"]["g90_worst"]),
          json.dumps(res or ex)[:400])
    if ex.get("available"):
        tr = ex["transduction"]
        check("references at 20 dB bridged to the 60 dB noise rg (x100 in "
              "amplitude), tip 90 x 1.0/10.0 = 9 deg, D_cal inflated by "
              "the ladder's power envelope",
              tr["rg_bridged"] is True
              and all(abs(p["rg_bridge_amplitude"] - 100.0) < 1e-6
                      and abs(p["tip_deg"] - 9.0) < 1e-9
                      for p in tr["references"])
              and ex["calibration_derating"]["D_cal"] > 4.6
              and "inflated" in ex["calibration_derating"]["basis"]
              and any(h.startswith("Receiver-gain bridge")
                      for h in ex["honesty"]), json.dumps(tr)[:400])
        hon = ex.get("honesty") or []
        check("axion exclusion honesty: 'does NOT improve with more "
              "measurement time', D_cal reuse, sign caveat, unpublished",
              any("does NOT improve with more measurement time" in h
                  for h in hon)
              and any("D_cal" in h and "pilot" in h for h in hon)
              and any("UNVERIFIED" in h for h in hon)
              and any("UNPUBLISHED" in h for h in hon), json.dumps(hon)[:400])
        if ex["signal_power"]["feature"] == "null":
            sp = ex["signal_power"]
            check("null block -> P_90 fluctuation-level (positive part < 5 "
                  "per-row sigma, statistical term > 20%) and said "
                  "'statistics-limited'",
                  sp["P_positive_part_counts2"] < 5 * sp["sigma_stat_counts2"]
                  and sp["statistical_fraction_of_P90"] > 0.2
                  and any("statistics-limited" in h for h in hon),
                  json.dumps(sp)[:300])
        check("HTML card 'Axion-coupling exclusion (worst-case, this "
              "session)' with the time-independence line; global honesty "
              "points at it",
              "Axion-coupling exclusion (worst-case, this session)" in html
              and "does NOT improve with more measurement time" in html
              and any("Axion-coupling exclusion" in h
                      for h in sci.get("honesty") or []))

    # the packer stamps real vendor-software acquisitions
    # 'external-acquisition': that must be a science report, not a
    # synthetic validation
    ext_bundle = os.path.join(work, os.path.basename(bundle)[:-9]
                              + "_ea01.zip")
    derive_bundle(bundle, ext_bundle, run_mode="external-acquisition")
    rep_ext, html_ext = run_report(ext_bundle, os.path.join(work, "report_ext"))
    check("run_mode external-acquisition -> report_type 'science' with the "
          "same aggregated block",
          rep_ext.get("report_type") == "science"
          and ((rep_ext.get("science") or {}).get("noise") or {}).get(
              "n_rows") == len(noise_expnos)
          and "SYNTHETIC-INJECTION" not in html_ext,
          rep_ext.get("report_type"))

    # ------------------------------------------------------------------
    # 4. fail loudly
    # ------------------------------------------------------------------
    one_bad = os.path.join(work, os.path.basename(bundle)[:-9] + "_bad1.zip")
    dropped = noise_expnos[-1]
    derive_bundle(bundle, one_bad, run_mode="synthetic-injection",
                  drop_members=("data/%d/procpar" % dropped,))
    rep1, html1 = run_report(one_bad, os.path.join(work, "report_bad1"))
    s1 = rep1.get("science") or {}
    refused = (s1.get("raw_data_read") or {}).get("refused") or {}
    check("expno %d without procpar is REFUSED with an explicit reason"
          % dropped,
          str(dropped) in refused
          and "no procpar" in refused[str(dropped)]
          and "NOT" in refused[str(dropped)], json.dumps(refused)[:300])
    n1 = s1.get("noise") or {}
    check("headline block shrinks to the readable %d rows and lists the "
          "skipped expno" % (len(noise_expnos) - 1),
          n1.get("n_rows") == len(noise_expnos) - 1
          and n1.get("expnos") == noise_expnos[:-1]
          and [s["expno"] for s in n1.get("skipped_experiments") or []]
          == [dropped], json.dumps({k: n1.get(k) for k in (
              "n_rows", "expnos", "skipped_experiments")}))
    qa1 = s1.get("qa_flags") or []
    check("QA carries a FAIL row for the refused expno",
          any(q["level"] == "FAIL" and "expno %d" % dropped in q["check"]
              for q in qa1), json.dumps([q for q in qa1
                                         if q["level"] == "FAIL"])[:300])
    adc1 = [q for q in qa1 if q["check"] == "ADC check expno %d" % dropped]
    check("the refused expno gets NO 'ADC check OK' row (its ADC row is the "
          "refusal)",
          len(adc1) == 1 and adc1[0]["level"] == "FAIL"
          and "procpar" in adc1[0]["detail"], json.dumps(adc1)[:300])
    check("HTML marks the refusal", "Refused" in html1 and "EXCLUDED" in html1)

    all_bad = os.path.join(work, os.path.basename(bundle)[:-9] + "_bad3.zip")
    derive_bundle(bundle, all_bad, run_mode="synthetic-injection",
                  drop_members=tuple("data/%d/procpar" % e
                                     for e in noise_expnos))
    rep3, html3 = run_report(all_bad, os.path.join(work, "report_bad3"))
    s3 = rep3.get("science") or {}
    det3 = s3.get("detection") or {}
    check("with no readable noise expno the analysis is UNAVAILABLE, not a "
          "null result",
          det3.get("unavailable") is True and det3.get("detected") is False
          and "upper_limit_95_amp" not in det3
          and "UNAVAILABLE" in (s3.get("headline") or {}).get("status", ""),
          json.dumps(det3)[:300])
    check("HTML says UNAVAILABLE and never 'No significant spin-noise "
          "feature'",
          "UNAVAILABLE" in html3
          and "No significant spin-noise feature was detected" not in html3)
    check("all three refusals listed",
          sorted((s3.get("raw_data_read") or {}).get("refused") or {})
          == sorted(str(e) for e in noise_expnos))
    ex3 = s3.get("axion_exclusion") or {}
    check("with no readable noise block the axion exclusion is unavailable "
          "with a reason, never a number; honesty says NOT determined",
          ex3.get("available") is False
          and "no readable noise block" in ex3.get("reason", "")
          and "result" not in ex3
          and any(h.startswith("NOT determined: the axion-coupling")
                  for h in s3.get("honesty") or [])
          and "Not computed" in html3, json.dumps(ex3)[:300])

    # closing reference acquired at a different flip angle (SIU session
    # 1's Defect 1): must be machine-readable, not HTML-only
    zsrc = zipfile.ZipFile(bundle)
    pp13 = zsrc.read("data/13/procpar")
    unmatched = os.path.join(work, os.path.basename(bundle)[:-9]
                             + "_pw13.zip")
    derive_bundle(bundle, unmatched, run_mode="synthetic-injection",
                  replace_members={"data/13/procpar":
                                   procpar_edit(pp13, "pw", 2.25)})
    rep_u, html_u = run_report(unmatched, os.path.join(work, "report_pw13"))
    s_u = rep_u.get("science") or {}
    pair_u = s_u.get("reference_pair") or {}
    check("unmatched reference pair (pw 1.0 vs 2.25 us) recorded in "
          "report.json with both pulses",
          pair_u.get("matched") is False
          and sorted(p["pw_us"] for p in pair_u.get("pulses") or [])
          == [1.0, 2.25] and "NOT a matched pair" in pair_u.get("note", ""),
          json.dumps(pair_u)[:300])
    check("unmatched pair -> QA WARN 'reference pair', honesty line, HTML "
          "warning, caveat next to the A0 ratio",
          any(q["check"] == "reference pair" and q["level"] == "WARN"
              for q in s_u.get("qa_flags") or [])
          and any("Reference pair" in h for h in s_u.get("honesty") or [])
          and "NOT a matched pair" in html_u
          and "a0_ratio_caveat" in (s_u.get("floor_calibration") or {}),
          json.dumps([q for q in s_u.get("qa_flags") or []
                      if q["level"] == "WARN"])[:300])

    # two parameter groups AND swapped start times in the headline group:
    # the time-order warning must fire regardless of the group count
    def _two_groups(m):
        by_no = {e["expno"]: e for e in m["experiments"]}
        by_no[noise_expnos[-1]]["rg"] = 100.0
        a, b = by_no[noise_expnos[0]], by_no[noise_expnos[1]]
        for k in ("started_local", "finished_local"):
            a[k], b[k] = b[k], a[k]

    multi = os.path.join(work, os.path.basename(bundle)[:-9] + "_grp2.zip")
    derive_bundle(bundle, multi, run_mode="synthetic-injection",
                  edit_meta=_two_groups)
    rep_m, _html_m = run_report(multi, os.path.join(work, "report_grp2"))
    s_m = rep_m.get("science") or {}
    agg_m = s_m.get("noise_aggregation") or {}
    check("noise expno at another rg -> 2 parameter groups, headline group "
          "= the %d same-rg expnos, flagged inconsistent" % (
              len(noise_expnos) - 1),
          agg_m.get("n_parameter_groups") == 2
          and agg_m.get("consistent") is False
          and agg_m.get("headline_expnos") == noise_expnos[:-1],
          json.dumps({k: agg_m.get(k) for k in (
              "n_parameter_groups", "consistent", "headline_expnos")}))
    check("swapped start times inside the headline group -> time_order_note "
          "and QA WARN 'noise row time order' even with 2 groups",
          bool(agg_m.get("time_order_note"))
          and any(q["check"] == "noise row time order"
                  for q in s_m.get("qa_flags") or []),
          json.dumps(agg_m.get("time_order_note")))

    # ------------------------------------------------------------------
    # 5. direct Bundle checks on hand-built fids
    # ------------------------------------------------------------------
    def varian_fid(values, status, block_status=None, scale=0):
        """One block, one trace; values interleaved re/im."""
        if status & fr.VARIAN_S_FLT:
            fmt, ebytes = "f", 4
        elif status & fr.VARIAN_S_32:
            fmt, ebytes = "i", 4
        else:
            fmt, ebytes = "h", 2
        npts = len(values)
        tbytes = npts * ebytes
        bbytes = fr.VARIAN_BLOCK_HEADER_BYTES + tbytes
        head = struct.pack(fr.VARIAN_FILE_HEADER, 1, 1, npts, ebytes, tbytes,
                           bbytes, 0, status, 1)
        bhead = struct.pack(fr.VARIAN_BLOCK_HEADER, scale,
                            status if block_status is None else block_status,
                            1, 0, 1, 0.0, 0.0, 0.0, 0.0)
        return head + bhead + struct.pack(">%d%s" % (npts, fmt), *values)

    # the procpar of the packed synthetic noise expno is reused verbatim
    # (np = NOISE_NP, gain = NOISE_GAIN_DB), so this part depends only on
    # the generator's public constants
    mini = os.path.join(work, "mini_agilent.zip")
    npts = gen.NOISE_NP
    pp_bytes = zsrc.read("data/%d/procpar" % noise_expnos[0])
    import random
    rnd = random.Random(7)
    f32 = []
    for _ in range(npts // 2):
        f32 += [rnd.gauss(5.0, 1.0), rnd.gauss(-3.0, 1.0)]   # DC 5-3j
    i16 = [int(rnd.gauss(0, 2000)) for _ in range(npts)]
    i16[10] = 30000
    i32 = [int(rnd.gauss(0, 20000)) for _ in range(npts)]
    i32[20] = 32767                                          # railed 16-bit ADC
    with zipfile.ZipFile(mini, "w") as zf:
        zf.writestr("meta.json", json.dumps({
            "vendor": "agilent", "schema_version": "2.0",
            "experiments": [{"expno": n, "role": "noise"}
                            for n in range(1, 10)]}))
        for n in (1, 2, 3, 5, 6, 7):
            zf.writestr("data/%d/procpar" % n, pp_bytes)
        zf.writestr("data/1/fid", varian_fid(f32, 0x1 | fr.VARIAN_S_FLT))
        zf.writestr("data/2/fid", varian_fid(i16, 0x1))
        zf.writestr("data/3/fid", varian_fid(i16[:256], 0x1 | fr.VARIAN_S_32))
        zf.writestr("data/4/fid", varian_fid(i16, 0x1))
        zf.writestr("data/5/fid", varian_fid(i16, 0x1, scale=3))
        zf.writestr("data/6/fid", varian_fid(f32, 0x1 | fr.VARIAN_S_FLT,
                                             block_status=0x1))
        zf.writestr("data/7/fid", varian_fid(i32, 0x1 | fr.VARIAN_S_32))
        zf.writestr("data/8/procpar", bytes(range(256)) * 8)
        zf.writestr("data/8/fid", varian_fid(i16, 0x1))
        zf.writestr("data/9/procpar", procpar_edit(pp_bytes, "sw"))
        zf.writestr("data/9/fid", varian_fid(i16, 0x1))
    b = fr.Bundle(mini)
    rows, acq = b.read_rows(1, {"expno": 1, "td": npts, "td1_rows": 1})
    check("float32 fid: dtype float32, TD/SW_h/RG/NS/DS/DE/GRPDLY/PULPROG "
          "synthesized from procpar",
          rows is not None and acq.get("_dtype") == "float32"
          and acq.get("TD") == npts and abs(acq.get("SW_h", 0) - gen.SW_HZ)
          < 1e-9 and abs(acq.get("RG", 0)
                         - 10.0 ** (gen.NOISE_GAIN_DB / 20.0)) < 1e-9
          and acq.get("NS") == 1 and acq.get("DS") == 0
          and acq.get("DE") == 0.0 and acq.get("GRPDLY") == 0.0
          and acq.get("PULPROG") == "s2pul", str(acq)[:300])
    check("float32 fid: per-row complex mean (DC 5-3j) recorded in read_log "
          "and KEPT in the rows",
          rows is not None and abs(complex(rows[0].mean()) - (5 - 3j)) < 0.2
          and b.read_log[1]["dc_offset_subtracted"] is False
          and abs(b.read_log[1]["dc_offset_max_abs"] - abs(5 - 3j)) < 0.2
          and b.read_log[1]["fid_block_header"]["scale"] == 0,
          "mean=%s log=%s" % (rows[0].mean() if rows is not None else None,
                              b.read_log.get(1)))
    st = b.raw_int_stats(1)
    check("float32 fid: fullscale_fraction None",
          st and st["dtype"] == "float32" and st["fullscale_fraction"] is None,
          str(st))
    rows2, acq2 = b.read_rows(2, {"expno": 2, "td": npts, "td1_rows": 1})
    st2 = b.raw_int_stats(2)
    check("int16 fid: dtype int16, full scale 32767, %d complex points"
          % (npts // 2),
          rows2 is not None and rows2.shape == (1, npts // 2)
          and acq2.get("_dtype") == "int16" and st2["dtype"] == "int16"
          and abs(st2["fullscale_fraction"] - 30000.0 / 32767.0) < 1e-9,
          str(st2))
    rows7, acq7 = b.read_rows(7, {"expno": 7, "td": npts, "td1_rows": 1})
    st7 = b.raw_int_stats(7)
    check("int32 (S_32) fid: read as int32, but NO full scale assumed "
          "(a railed 16-bit ADC word must not pass as 0.0015%)",
          rows7 is not None and acq7.get("_dtype") == "int32"
          and st7["dtype"] == "int32" and st7["fullscale_fraction"] is None
          and st7["max_abs"] >= 32767.0, str(st7))
    rows3, _acq3 = b.read_rows(3, {"expno": 3, "td": npts, "td1_rows": 1})
    check("fid np != procpar np is refused, not read",
          rows3 is None and 3 in b.read_errors
          and "disagrees" in b.read_errors[3], str(b.read_errors.get(3)))
    rows5, _acq5 = b.read_rows(5, {"expno": 5, "td": npts, "td1_rows": 1})
    check("block header scale 3 is refused (samples 8x low), not rescaled",
          rows5 is None and 5 in b.read_errors
          and "scale 3" in b.read_errors[5]
          and "8-fold" in b.read_errors[5], str(b.read_errors.get(5)))
    rows6, _acq6 = b.read_rows(6, {"expno": 6, "td": npts, "td1_rows": 1})
    check("block header element-type bits disagreeing with the file header "
          "are refused",
          rows6 is None and 6 in b.read_errors
          and "disagrees with the file header" in b.read_errors[6],
          str(b.read_errors.get(6)))
    rows4, _acq4 = b.read_rows(4, {"expno": 4, "td": npts, "td1_rows": 1})
    check("Agilent expno without procpar is refused, not read",
          rows4 is None and 4 in b.read_errors
          and "no procpar" in b.read_errors[4]
          and "files present: fid" in b.read_errors[4],
          str(b.read_errors.get(4)))
    b_adc = fr.Bundle(mini)
    st4 = b_adc.raw_int_stats(4)
    check("ADC check on an expno without procpar refuses too (None + "
          "read_errors), never an 'OK' on unread data",
          st4 is None and 4 in b_adc.read_errors
          and "no procpar" in b_adc.read_errors[4],
          "%s | %s" % (st4, b_adc.read_errors.get(4)))
    rows8, _acq8 = b.read_rows(8, {"expno": 8, "td": npts, "td1_rows": 1})
    check("binary garbage procpar: refused with the parse diagnosis, not "
          "'no procpar'",
          rows8 is None and 8 in b.read_errors
          and "unusable" in b.read_errors[8]
          and "recognised" in b.read_errors[8]
          and "no procpar" not in b.read_errors[8],
          str(b.read_errors.get(8)))
    rows9, _acq9 = b.read_rows(9, {"expno": 9, "td": npts, "td1_rows": 1})
    check("procpar without an sw record: refused naming sw=None",
          rows9 is None and 9 in b.read_errors
          and "sw=None" in b.read_errors[9]
          and "np=%s" % float(npts) in b.read_errors[9],
          str(b.read_errors.get(9)))
    check("group_delay_points: Agilent 0, Bruker GRPDLY, Bruker default 68",
          fr.group_delay_points({"_vendor": "agilent"}) == 0
          and fr.group_delay_points({"GRPDLY": 67.98}) == 68
          and fr.group_delay_points({"GRPDLY": 76.0}) == 76
          and fr.group_delay_points({}) == fr.GRPDLY_DEFAULT == 68
          and fr.group_delay_points({"GRPDLY": -1}) == 68)

    # headline_fit: the aligned co-add unless the cross-check said SUSPECT
    aligned = {"amp_norm": 0.48, "amp_err": 0.02}
    unaligned = {"amp_norm": 0.27, "amp_err": 0.01}
    check("headline_fit picks coadd_fit normally, unaligned_fit when "
          "SUSPECT, coadd_fit when no unaligned fit exists, None when empty",
          fr.headline_fit({"coadd_fit": aligned, "unaligned_fit": unaligned,
                           "headline_fit": "coadd_fit"})
          == (aligned, "coadd_fit")
          and fr.headline_fit({"coadd_fit": aligned,
                               "unaligned_fit": unaligned,
                               "headline_fit": "unaligned_fit"})
          == (unaligned, "unaligned_fit")
          and fr.headline_fit({"coadd_fit": aligned,
                               "headline_fit": "unaligned_fit"})
          == (aligned, "coadd_fit")
          and fr.headline_fit({"coadd_fit": aligned}) == (aligned, "coadd_fit")
          and fr.headline_fit(None) == (None, None)
          and fr.headline_fit({}) == (None, None))
    # the exclusion's tip angle: calibration.p90_power_db_or_w is read only
    # with an explicit unit (the schema allows dB or watts), a Bruker
    # reference whose power does not resolve takes the ladder tip scaled
    # by its own P1/P90
    cal_ladder = {"p90_us": 10.0, "p90_power_db_or_w": "n/a",
                  "rg_ladder": [{"tip_deg": 1.0, "rg": 1.0}]}
    tip_2x, basis_2x = fr.reference_tip_deg(
        {"data_format": "bruker", "p1_us": 20.0}, cal_ladder)
    tip_1x, basis_1x = fr.reference_tip_deg(
        {"data_format": "bruker", "p1_us": 10.0}, cal_ladder)
    tip_w, basis_w = fr.reference_tip_deg(
        {"data_format": "bruker", "p1_us": 10.0, "pl1_db": 29.08},
        {"p90_us": 10.0, "p90_power_db_or_w": "0.1 W"})
    tip_bare, basis_bare = fr.reference_tip_deg(
        {"data_format": "bruker", "p1_us": 10.0, "pl1_db": 29.08},
        {"p90_us": 10.0, "p90_power_db_or_w": 20.0,
         "rg_ladder": [{"tip_deg": 1.0, "rg": 1.0}]})
    tip_ag, basis_ag = fr.reference_tip_deg(
        {"data_format": "agilent", "pw_us": 0.05, "tpwr_db": 56.0},
        {"p90_us": 4.5, "p90_power_db_or_w": "10 W"})
    check("p90_power_db: explicit dB read, watts converted on the Bruker "
          "PL scale (0.1 W -> +10 dB), refused for Agilent tpwr, a bare "
          "number refused, a unitless string refused",
          fr.p90_power_db("56 dB (tpwr)", "agilent")[0] == 56.0
          and abs(fr.p90_power_db("0.1 W", "bruker")[0] - 10.0) < 1e-9
          and fr.p90_power_db("10 W", "agilent")[0] is None
          and fr.p90_power_db(20.0, "bruker")[0] is None
          and "bare number" in fr.p90_power_db(20.0, "bruker")[1]
          and fr.p90_power_db("unknown", "bruker")[0] is None
          and fr.p90_power_db(None, "bruker")[0] is None)
    check("reference_tip_deg: Bruker power unresolved -> ladder tip x "
          "P1/P90 (2 deg at P1 = 2 x P90, 1 deg at P1 = P90, said in the "
          "basis); '0.1 W' with PL1 29.08 dB -> 90 x 10^(-(29.08-10)/20) "
          "= 10 deg; a bare 20 falls back to the ladder, never read as "
          "dB; Agilent with a watts p90 power keeps pw/p90 and says ASSUMED",
          abs(tip_2x - 2.0) < 1e-9 and "x P1 20 us / P90 10 us" in basis_2x
          and abs(tip_1x - 1.0) < 1e-9 and "P1 = P90 = 10 us" in basis_1x
          and abs(tip_w - 90.0 * 10 ** (-(29.08 - 10.0) / 20.0)) < 1e-9
          and abs(tip_bare - 1.0) < 1e-9 and "bare number" in basis_bare
          and "ASSUMED" in basis_bare
          and abs(tip_ag - 1.0) < 1e-9 and "ASSUMED" in basis_ag
          and "watts" in basis_ag,
          "%s %s | %s %s | %s %s | %s %s | %s %s" % (
              tip_2x, basis_2x, tip_1x, basis_1x, tip_w, basis_w,
              tip_bare, basis_bare, tip_ag, basis_ag))
    check("reference_pair_check: matched / unmatched / too few",
          fr.reference_pair_check([
              {"expno": 11, "readable": True, "pw_us": 0.05, "tpwr_db": 56},
              {"expno": 13, "readable": True, "pw_us": 0.05, "tpwr_db": 56}]
          )["matched"] is True
          and fr.reference_pair_check([
              {"expno": 11, "readable": True, "pw_us": 0.05, "tpwr_db": 56},
              {"expno": 13, "readable": True, "pw_us": 0.1125,
               "tpwr_db": 56}])["matched"] is False
          and fr.reference_pair_check([
              {"expno": 11, "readable": True, "pw_us": 0.05, "tpwr_db": 56},
              {"expno": 13, "readable": False}]) is None
          and fr.reference_pair_check([
              {"expno": 11, "readable": True},
              {"expno": 13, "readable": True}]) is None)

    # ------------------------------------------------------------------
    # 6. clock-audit refinement degrades gracefully on an Agilent expno
    # ------------------------------------------------------------------
    sb = fr.Bundle(sci_bundle)
    fake_meta = dict(sb.meta)
    fake_meta["clock_audit"] = {
        "workstation_time_source": "test", "ntp_status_raw": "",
        "blocks": [{"expno": noise_expnos[0], "role": "noise",
                    "wall_start_ms": 0, "wall_end_ms": 1000,
                    "ocxo_expected_s": 0.8192},
                   {"expno": 11, "role": "reference_open",
                    "wall_start_ms": 2000, "wall_end_ms": 2300,
                    "ocxo_expected_s": 0.2048}]}
    try:
        clk = fr.analyze_clock_audit(fake_meta, sb)
        blocks = clk.get("blocks") or []
        check("clock-audit refine on Agilent expnos: recorded expectation "
              "kept with a Varian refine_note, no traceback",
              len(blocks) == 2 and all(
                  b_["expected_source"] == "script-recorded"
                  and "Varian" in b_.get("refine_note", "") for b_ in blocks),
              json.dumps(blocks)[:300])
    except Exception as exc:
        check("clock-audit refine on Agilent expnos: no traceback", False,
              repr(exc))

    # ------------------------------------------------------------------
    # 7. v0.7.1: the legacy ladder is degenerate and says so
    # ------------------------------------------------------------------
    check("legacy monotonic single-visit ladder -> model degenerate_"
          "monotonic, 'compression and drift degenerate (monotonic ladder)' "
          "in the note, no drift block, 4 levels x 1 visit",
          lad.get("model") == "degenerate_monotonic"
          and "compression and drift degenerate (monotonic ladder)"
          in lad.get("note", "")
          and "drift" not in lad and lad.get("n_levels") == 4
          and lad.get("n_visits") == 4 and lad.get("time_order") == "monotonic",
          json.dumps({k: lad.get(k) for k in (
              "model", "note", "n_levels", "n_visits", "time_order")})[:400])
    check("headline rg_linearity_power_envelope is the ladder's power "
          "deviation (legacy path)",
          abs((sci.get("headline") or {}).get("rg_linearity_power_envelope",
                                                -1) - lad.get(
              "max_abs_power_deviation", -2)) < 1e-12)
    check("no noise_tune experiment -> science.tuning_ladder is null and no "
          "tuning card", sci.get("tuning_ladder") is None
          and "Spin-noise tuning ladder" not in html)
    check("no sweep_signcal -> sign status carries no calibration attempt",
          "calibration" not in (sci.get("frequency_axis_sign") or {}))

    def _rg(rgs):
        return [{"rg": float(r)} for r in rgs]
    check("ladder_time_order: ascending AND descending single-visit orders "
          "are 'monotonic' (equal neighbours ignored), any rung without a "
          "stamp 'unknown'",
          fr.ladder_time_order(_rg([1, 10, 100, 1000]), []) == "monotonic"
          and fr.ladder_time_order(_rg([1000, 100, 10, 1]), []) == "monotonic"
          and fr.ladder_time_order(_rg([1, 10, 10, 100]), []) == "monotonic"
          and fr.ladder_time_order(_rg([1, 10, 100]), [16]) == "unknown"
          and fr.ladder_time_order(_rg([1]), []) == "unknown")
    check("ladder_time_order: one run up then down is 'bracketed' -- SIU "
          "session 3's 1, 6.31, 31.6, 100, 100, 31.6, 6.31, 1 -- as is "
          "down then up; a plateau inside a run makes no step",
          fr.ladder_time_order(
              _rg([1, 6.31, 31.6, 100, 100, 31.6, 6.31, 1]), []) == "bracketed"
          and fr.ladder_time_order(_rg([100, 10, 1, 10, 100]), [])
          == "bracketed"
          and fr.ladder_time_order(_rg([1, 10, 10, 100, 10, 1]), [])
          == "bracketed"
          and fr.ladder_time_order(_rg([1, 10, 1]), []) == "bracketed")
    check("ladder_time_order: two or more direction changes are "
          "'randomized' -- [1, 100, 10, 1000], a shuffled 5 x 3 ladder -- "
          "and the label set is monotonic/bracketed/randomized/unknown",
          fr.ladder_time_order(_rg([1, 100, 10, 1000]), []) == "randomized"
          and fr.ladder_time_order(
              _rg([10, 1, 100, 10, 1000, 1, 100, 1000, 10]), [])
          == "randomized"
          and fr.ladder_time_order(_rg([1, 10, 1, 10]), []) == "randomized"
          and tuple(fr.LADDER_TIME_ORDERS)
          == ("monotonic", "bracketed", "randomized", "unknown"))
    # a bracketed repeated ladder (SIU session 3's layout) is analyzed:
    # the levels repeat, so the drift_compression model applies and the
    # note states the bracketed time order
    br_rungs = []
    for k, rg in enumerate([1.0, 6.31, 31.6, 100.0, 100.0, 31.6, 6.31, 1.0]):
        br_rungs.append({"expno": 500 + k, "rg": rg, "t_min": float(k),
                         "amplitude_counts": 50.0 * rg * (1 + 0.002 * k)})
    br_levels = fr.ladder_levels(br_rungs)
    br_fit = fr.fit_ladder_drift(br_rungs, br_levels)
    check("bracketed repeated ladder (SIU session 3 layout): 4 levels x 2 "
          "visits, time order 'bracketed', the drift fit is identifiable "
          "and recovers beta = 0.2 %/min with every compression 1",
          len(br_levels) == 4
          and fr.ladder_time_order(sorted(br_rungs, key=lambda r: r["t_min"]),
                                   []) == "bracketed"
          and br_fit is not None
          and abs(br_fit["beta_per_min"] - 0.002) < 1e-6
          and all(abs(math.exp(v) / math.exp(br_fit["log_k"][0]) - 1.0)
                  < 1e-6 for v in br_fit["log_k"]),
          json.dumps(br_fit and {"beta": br_fit["beta_per_min"]}))

    # the same legacy ladder with its stamps removed: the order of a
    # stamp-less ladder is not known, so 'monotonic' is never claimed
    def _strip_ladder_times(m):
        for e in m["experiments"]:
            if e["role"] == "rg_ladder":
                e["started_local"] = "unknown"
                e["finished_local"] = "unknown"
    lnt_zip = os.path.join(work, os.path.basename(bundle)[:-9] + "_lnt.zip")
    derive_bundle(sci_bundle, lnt_zip, edit_meta=_strip_ladder_times)
    rep_lnt, html_lnt = run_report(lnt_zip, os.path.join(work, "report_lnt"))
    s_lnt = rep_lnt.get("science") or {}
    lad_nt = s_lnt.get("rg_ladder") or {}
    check("single-visit ladder WITHOUT usable started_local -> time_order "
          "'unknown', model degenerate_single_visit, note 'single-visit, "
          "time order unknown' -- 'monotonic' claimed nowhere (note, "
          "headline assumption, HTML)",
          lad_nt.get("time_order") == "unknown"
          and lad_nt.get("model") == "degenerate_single_visit"
          and "single-visit, time order unknown"
          in lad_nt.get("degenerate_note", "")
          and "monotonic" not in lad_nt.get("degenerate_note", "")
          and not any("monotonic" in a_ for a_ in (
              s_lnt.get("headline") or {}).get("assumptions", []))
          and "time order unknown" in html_lnt
          and "monotonic ladder" not in html_lnt,
          json.dumps({k: lad_nt.get(k) for k in (
              "model", "time_order", "degenerate_note")})[:400])

    # ------------------------------------------------------------------
    # 8. v0.7.1: axis sign from a sweep_signcal 1D (+1, -1, ambiguous)
    # ------------------------------------------------------------------
    D_HZ = 200.0
    carrier_hz = meta["spectrometer"]["h1_freq_mhz"] * 1e6
    site_mod = load_module("snn_site_excl",
                           os.path.join(REPO, "analysis", "site_exclusion.py"))
    sign_reports = {}
    # the referencing beside each signcal: the generator's physical
    # referencing agrees with 'phys', a mirrored one with 'mirr', and the
    # ambiguous fixture carries none (so the signcal path stands alone)
    for tag, shift, want in (("phys", -D_HZ, 1), ("mirr", +D_HZ, -1),
                             ("ambg", +50.0, None)):
        fb = FixtureBuilder(bundle, gen, seed=11)
        fb.line_references()
        fb.signcal(60, D_HZ, shift)
        if tag == "mirr":
            fb.set_referencing(-1)
        elif tag == "ambg":
            fb.strip_referencing()
        zpath = os.path.join(work, os.path.basename(bundle)[:-9]
                             + "_sc%s.zip" % tag)
        fb.write(zpath)
        rep_s, html_s = run_report(zpath, os.path.join(work,
                                                        "report_sc" + tag))
        s_s = rep_s.get("science") or {}
        sign_reports[tag] = (os.path.join(work, "report_sc" + tag,
                                          "report.json"), rep_s)
        fas = s_s.get("frequency_axis_sign") or {}
        cal = fas.get("calibration") or {}
        trials = cal.get("trials") or []
        t0 = trials[0] if trials else {}
        ex_s = s_s.get("axion_exclusion") or {}
        line_s = ex_s.get("line") or {}
        res_s = ex_s.get("result") or {}
        qa_s = s_s.get("qa_flags") or []
        sign_rows = [q for q in qa_s if q["check"] == "frequency-axis sign"]
        check("signcal %s: trial records d = %+.0f Hz (o1 %+.0f vs %+.0f), "
              "both measured offsets and their change %+.0f Hz, tolerance "
              "30%% of |d|" % (tag, D_HZ, D_HZ, 0.0, shift),
              len(trials) == 1 and t0.get("signcal_expno") == 60
              and t0.get("reference_expno") in (11, 13)
              and abs(t0.get("displacement_hz", 0) - D_HZ) < 1e-9
              and abs(t0.get("o1_signcal_hz", 0) - D_HZ) < 1e-9
              and abs(t0.get("o1_reference_hz", 1)) < 1e-9
              and abs(t0.get("offset_reference_hz", 0) - F0_LINE_HZ) < 3.0
              and abs(t0.get("offset_signcal_hz", 0)
                      - (F0_LINE_HZ + shift)) < 3.0
              and abs(t0.get("offset_change_hz", 0) - shift) < 4.0
              and abs(t0.get("tolerance_hz", 0) - 0.3 * D_HZ) < 1e-9
              and abs(t0.get("rg", 0) - 10.0) < 1e-6 and t0.get("pw_us") == 1.0
              and finite(t0.get("min_displacement_hz"))
              and 5.0 <= t0["min_displacement_hz"] < D_HZ
              and finite(t0.get("position_uncertainty_hz"))
              and finite(t0.get("open_close_drift_hz"))
              and finite(t0.get("line_peak_snr_signcal"))
              and t0["line_peak_snr_signcal"] >= 10.0
              and finite(t0.get("line_peak_snr_reference"))
              and t0["line_peak_snr_reference"] >= 10.0
              and "line_quality_gate" in cal and "displacement_gate" in cal,
              json.dumps(cal)[:600])
        if want is not None:
            check("signcal %s: sign resolved to %+d, verified, basis carrier_"
                  "displacement_calibration, verdict %s, two-way ambiguity "
                  "dropped, QA OK row (no WARN)"
                  % (tag, want, "physical" if want == 1 else "mirrored"),
                  fas.get("verified") is True and fas.get("sign") == want
                  and fas.get("basis") == "carrier_displacement_calibration"
                  and cal.get("verdict") == ("physical" if want == 1
                                             else "mirrored")
                  and "two_way_ambiguity_ppm" not in fas
                  and len(sign_rows) == 1 and sign_rows[0]["level"] == "OK",
                  json.dumps(fas)[:600])
            nu_want = carrier_hz + want * F0_LINE_HZ
            check("signcal %s: exclusion on ONE mass axis -- sign_verified, "
                  "axis_sign %+d, nu_L = carrier %+.0f Hz, no mirror mass, no "
                  "mirror curve, band only nominal" % (tag, want,
                                                       want * F0_LINE_HZ),
                  line_s.get("sign_verified") is True
                  and line_s.get("axis_sign") == want
                  and abs(line_s.get("nu_L_hz_nominal", 0) - nu_want) < 3.0
                  and "m_a_uev_mirror" not in line_s
                  and "m_a_at_best_mirror_uev" not in res_s
                  and "m_a_ev_mirror" not in (ex_s.get("curve") or {})
                  and sorted(res_s.get("band_10x_uev") or {}) == ["nominal"]
                  and not any("INTERSECTION" in h
                              for h in ex_s.get("honesty") or []),
                  json.dumps({"line": line_s, "band": res_s.get(
                      "band_10x_uev")})[:600])
            check("signcal %s: HTML and honesty state the calibration, no "
                  "'SIGN unverified' anywhere; mass bookkeeping without the "
                  "sign caveat" % tag,
                  "carrier-displacement calibration" in html_s
                  and "SIGN unverified" not in html_s
                  and "UNVERIFIED" not in html_s
                  and any("RESOLVED" in h for h in s_s.get("honesty") or [])
                  and "offset_sign_caveat" not in (
                      s_s.get("axion_mass_bookkeeping") or {})
                  and (("MIRRORED" in html_s) == (want == -1)))
            noise_s = s_s.get("noise") or {}
            check("signcal %s: headline block flags axis_sign_unverified False "
                  "with the sign and basis" % tag,
                  noise_s.get("axis_sign_unverified") is False
                  and noise_s.get("axis_sign") == want
                  and noise_s.get("axis_sign_basis")
                  == "carrier_displacement_calibration")
            rc_s = fas.get("referencing_check") or {}
            check("signcal %s: the lock-referencing cross-check (%s) agrees "
                  "and is recorded beside the primary calibration -- basis "
                  "stays carrier_displacement_calibration, note and HTML "
                  "say it agrees" % (tag, rc_s.get("verdict")),
                  rc_s.get("verdict") == ("physical" if want == 1
                                          else "mirrored")
                  and rc_s.get("sign") == want
                  and rc_s.get("agreement", "").startswith("agrees")
                  and "cross-check agrees" in fas.get("note", "")
                  and "lock-referencing cross-check: %s -- agrees"
                  % rc_s.get("verdict") in html_s,
                  json.dumps(rc_s)[:500])
            site = site_mod.combine_reports([sign_reports[tag]])
            comb = site.get("combined") or {}
            check("signcal %s: the site combiner treats the session as sign-"
                  "verified (no unverified session, nominal mass axis, one "
                  "hypothesis)" % tag,
                  site.get("n_sessions") == 1
                  and comb.get("sign_unverified_sessions") == []
                  and site["sessions"][0]["sign_verified"] is True
                  and site["sessions"][0]["band_10x_basis"]
                  == "nominal mass axis",
                  json.dumps({"sess": site.get("sessions"),
                              "unv": comb.get("sign_unverified_sessions")})[:400])
        else:
            check("signcal ambiguous: sign stays UNVERIFIED, calibration "
                  "verdict ambiguous with the reason, QA WARN kept, mirror "
                  "mass kept in the exclusion, honesty names the attempt",
                  fas.get("verified") is False and fas.get("sign") is None
                  and cal.get("verdict") == "ambiguous"
                  and t0.get("verdict") == "ambiguous"
                  and "neither -d nor +d" in t0.get("why", "")
                  and "ambiguous" in fas.get("note", "")
                  and len(sign_rows) == 1 and sign_rows[0]["level"] == "WARN"
                  and line_s.get("sign_verified") is False
                  and finite(line_s.get("m_a_uev_mirror"))
                  and any("attempted and is ambiguous" in h
                          for h in s_s.get("honesty") or [])
                  and "UNVERIFIED" in html_s
                  and (fas.get("referencing_check") or {}).get("verdict")
                  == "unavailable"
                  and "reffrq" in (fas.get("referencing_check") or {}).get(
                      "why", ""),
                  json.dumps({"fas": fas, "line": line_s})[:700])

    # a signcal 1D WITHOUT a line (noise only, a0 = 0): the line-quality
    # gate must refuse it -- a noise argmax can never resolve the sign
    # (procpars without referencing, so nothing else resolves it either)
    fb = FixtureBuilder(bundle, gen, seed=11)
    fb.line_references()
    fb.signcal(60, D_HZ, -D_HZ, a0=0.0)
    fb.strip_referencing()
    nl_zip = os.path.join(work, os.path.basename(bundle)[:-9] + "_scnol.zip")
    fb.write(nl_zip)
    rep_nl, html_nl = run_report(nl_zip, os.path.join(work, "report_scnol"))
    s_nl = rep_nl.get("science") or {}
    fas_nl = s_nl.get("frequency_axis_sign") or {}
    cal_nl = fas_nl.get("calibration") or {}
    t_nl = (cal_nl.get("trials") or [{}])[0]
    check("signcal WITHOUT a line (noise-only 1D): the trial is refused as "
          "unusable by the line-quality gate, calibration verdict unusable, "
          "sign stays UNVERIFIED with the mirror mass kept, QA WARN, honesty "
          "names the attempt",
          fas_nl.get("verified") is False and fas_nl.get("sign") is None
          and cal_nl.get("verdict") == "unusable"
          and t_nl.get("verdict") == "unusable"
          and any(k in t_nl.get("why", "")
                  for k in ("no usable line", "not fitted"))
          and finite(((s_nl.get("axion_exclusion") or {}).get("line")
                      or {}).get("m_a_uev_mirror"))
          and any(q["check"] == "frequency-axis sign" and q["level"] == "WARN"
                  for q in s_nl.get("qa_flags") or [])
          and any("attempted and is unusable" in h
                  for h in s_nl.get("honesty") or [])
          and "UNVERIFIED" in html_nl,
          json.dumps(cal_nl)[:700])
    # the gates on the row analysis (40 noise-only seeds: the peak
    # excess of a line-free spectrum must stay below the gate every
    # time) and on fabricated 1D records
    rng_q = np.random.default_rng(5)
    noise_rows = []
    for _k in range(40):
        noise_only = 20.0 * (rng_q.standard_normal(8192)
                             + 1j * rng_q.standard_normal(8192))
        noise_rows.append(fr.analyze_reference_row(noise_only, gen.SW_HZ, 0))
    row_line = fr.analyze_reference_row(synth_line_row(
        rng_q, 8192, gen.SW_HZ, 5000.0, F0_LINE_HZ, FWHM_LINE_HZ, 20.0),
        gen.SW_HZ, 0)
    q_noise = [fr.reference_line_quality([r]) for r in noise_rows
               if r.get("fwhm_amp_hz")]
    q_line = fr.reference_line_quality([row_line])
    noise_snr = [r.get("line_peak_snr") for r in noise_rows
                 if r.get("line_peak_snr") is not None]
    check("analyze_reference_row line quality: 40 noise-only rows all "
          "score below %.0f robust sigmas (max %s) and are unusable with a "
          "reason; a 5000-count line scores above 100 with a resolved width "
          "off the fit bound (usable)"
          % (fr.SIGNCAL_MIN_LINE_SNR, fmt3(max(noise_snr) if noise_snr
                                          else None)),
          len(q_noise) >= 30
          and all(q.get("usable_for_sign_calibration") is False
                  and bool(q.get("why")) for q in q_noise)
          and noise_snr and max(noise_snr) < fr.SIGNCAL_MIN_LINE_SNR
          and q_line.get("usable_for_sign_calibration") is True
          and (q_line.get("peak_snr_min") or 0) > 100.0
          and row_line.get("fwhm_at_fit_bound") is False
          and row_line.get("fwhm_sub_bin") is False
          and finite(row_line.get("line_center_err_hz")),
          json.dumps({"noise_snr": noise_snr, "line": {
              k: row_line.get(k) for k in (
                  "line_center_hz", "fwhm_amp_hz", "line_peak_snr",
                  "fwhm_sub_bin", "fwhm_at_fit_bound",
                  "line_center_err_hz")}})[:600])

    def _one_d(expno, role, center, o1, snr=500.0, at_bound=False, err=0.05):
        ok = snr >= fr.SIGNCAL_MIN_LINE_SNR and not at_bound
        return {"expno": expno, "role": role, "readable": True,
                "line_center_hz": center, "line_center_std_hz": 0.0,
                "line_center_err_hz": err, "o1_hz": o1, "rg": 10.0,
                "pw_us": 1.0, "started_local": "2026-03-01T09:00:00",
                "line_quality": {"peak_snr_min": snr, "fwhm_sub_bin": False,
                                 "fwhm_at_fit_bound": at_bound,
                                 "usable_for_sign_calibration": ok,
                                 "why": None if ok else "fixture: no line"}}
    st0 = {"verified": False, "sign": None, "basis": "unverified", "note": ""}
    refs_ok = [_one_d(11, "reference_open", 500.0, 0.0),
               _one_d(13, "reference_close", 500.3, 0.0)]
    refs_drift = [_one_d(11, "reference_open", 500.0, 0.0),
                  _one_d(13, "reference_close", 503.0, 0.0)]
    r_good = fr.carrier_displacement_sign(
        [_one_d(60, "sweep_signcal", 300.0, 200.0)], refs_ok, dict(st0))
    r_small = fr.carrier_displacement_sign(
        [_one_d(60, "sweep_signcal", 492.0, 8.0)], refs_drift, dict(st0))
    r_badref = fr.carrier_displacement_sign(
        [_one_d(60, "sweep_signcal", 300.0, 200.0)],
        [_one_d(11, "reference_open", 500.0, 0.0, snr=4.0),
         _one_d(13, "reference_close", 500.3, 0.0, snr=4.0)], dict(st0))
    t_small = r_small["calibration"]["trials"][0]
    t_badref = r_badref["calibration"]["trials"][0]
    check("carrier_displacement_sign gates: d = +200 Hz with clean lines -> "
          "physical; d = +8 Hz against a 3.0 Hz open/close drift -> "
          "ambiguous below the position-tied minimum (3.0 Hz drift / 0.3 = "
          "10 Hz > the 5 Hz floor), recorded on the trial; line-less "
          "references -> unusable naming them",
          r_good.get("verified") is True and r_good.get("sign") == 1
          and r_small.get("verified") is False
          and t_small.get("verdict") == "ambiguous"
          and abs(t_small.get("open_close_drift_hz", 0) - 3.0) < 1e-9
          and t_small.get("min_displacement_hz", 0) > 10.0
          and "below the" in t_small.get("why", "")
          and r_badref.get("verified") is False
          and t_badref.get("verdict") == "unusable"
          and "11, 13" in t_badref.get("why", "")
          and "usable line" in t_badref.get("why", ""),
          json.dumps({"small": t_small, "badref": t_badref})[:600])

    # ------------------------------------------------------------------
    # 9. v0.7.1: spin-noise tuning ladder, headline untouched
    # ------------------------------------------------------------------
    # 8 single-row blocks of 13.1 s per setting: with 14 Welch segments
    # per row the per-row dispersive term scatters by ~0.3 (its fitted
    # error understates that, as for the headline block), so the
    # co-added b/a is good to ~0.25 -- enough to separate a pure
    # absorptive dip from |b/a| >= 1 settings, which is the rule's job
    ROWS_PER_SETTING = 8
    TUNE = [  # (setting_index, label, a, b, match_reading)
        (0, "operator's normal tuning", -0.45, 0.00, 0.0),
        (1, "match +2 turns", +0.35, +0.40, 2.0),
        (2, "match -2 turns", -0.35, -0.35, -2.0),
    ]
    fb = FixtureBuilder(bundle, gen, seed=23)
    fb.line_references()
    fb.headline_bump(0.5, 0.1)
    expno = 70
    tune_expnos = {}
    for idx, label, a, b, match in TUNE:
        tune_expnos[idx] = []
        for _k in range(ROWS_PER_SETTING):
            fb.noise_block(expno, "noise_tune", a, b, tuning={
                "setting_index": idx, "label": label, "tune_reading": 0.0,
                "match_reading": match, "units": "turns",
                "note": "fixture"})
            tune_expnos[idx].append(expno)
            expno += 1
    untagged = []
    for _k in range(2):
        fb.noise_block(expno, "noise_tune", -0.02, 0.0)
        untagged.append(expno)
        expno += 1
    tune_zip = os.path.join(work, os.path.basename(bundle)[:-9] + "_tune.zip")
    fb.write(tune_zip)
    rep_t, html_t = run_report(tune_zip, os.path.join(work, "report_tune"))
    s_t = rep_t.get("science") or {}
    tl = s_t.get("tuning_ladder") or {}
    settings = tl.get("settings") or []
    by_idx = {s.get("setting_index"): s for s in settings}
    noise_t = s_t.get("noise") or {}
    check("tuning ladder: headline block unchanged -- n_rows 3 from the "
          "noise-role expnos %s only, aggregation consistent, detection on"
          % list(gen.NOISE_EXPNOS),
          noise_t.get("n_rows") == 3
          and noise_t.get("expnos") == list(gen.NOISE_EXPNOS)
          and (s_t.get("noise_aggregation") or {}).get(
              "n_noise_experiments") == 3
          and (s_t.get("detection") or {}).get("detected") is True,
          json.dumps({"n_rows": noise_t.get("n_rows"),
                      "expnos": noise_t.get("expnos"),
                      "det": (s_t.get("detection") or {}).get("detected")}))
    n_tune = 3 * ROWS_PER_SETTING + 2
    check("tuning ladder: %d noise_tune blocks -> 5 settings (3 labeled + 2 "
          "unlabeled fallbacks), %d rows in total, grouping stated"
          % (n_tune, n_tune),
          tl.get("n_experiments") == n_tune and tl.get("n_settings") == 5
          and tl.get("n_rows") == n_tune
          and "fallback" in tl.get("grouping", "")
          and [s["setting_index"] for s in settings] == [0, 1, 2, None, None],
          json.dumps({k: tl.get(k) for k in (
              "n_experiments", "n_settings", "n_rows", "grouping")}))
    ok_groups = all(
        by_idx.get(idx, {}).get("expnos") == tune_expnos[idx]
        and by_idx[idx].get("n_rows") == ROWS_PER_SETTING
        and by_idx[idx].get("label") == label
        and (by_idx[idx].get("readings") or {}).get("match_reading") == match
        and abs((by_idx[idx].get("seconds") or 0)
                - ROWS_PER_SETTING * 131072 / gen.SW_HZ) < 1e-6
        for idx, label, _a, _b, match in TUNE)
    check("tuning ladder: each labeled setting groups its %d expnos with its "
          "label, readings and %d x 13.1 s" % (ROWS_PER_SETTING,
                                                ROWS_PER_SETTING), ok_groups,
          json.dumps([{k: s.get(k) for k in (
              "setting_index", "expnos", "n_rows", "label", "readings",
              "seconds")} for s in settings])[:800])
    fits_ok, fit_detail = True, []
    for idx, _label, a, b, _m in TUNE:
        s = by_idx.get(idx) or {}
        ba = s.get("asymmetry_b_over_a")
        fit_detail.append({"idx": idx, "a": s.get("amp_norm"),
                           "err": s.get("amp_err"), "b/a": ba,
                           "sig": s.get("significance"),
                           "fwhm": s.get("fwhm_hz")})
        if not (finite(s.get("amp_norm")) and abs(s["amp_norm"] - a) < 0.12
                and s.get("sign") == ("bump" if a > 0 else "dip")
                and finite(ba) and abs(ba - b / a) < 0.5
                and (s.get("significance") or 0) >= 5.0
                and finite(s.get("fwhm_hz"))
                and abs(s["fwhm_hz"] - FWHM_LINE_HZ) < 6.0
                and finite(s.get("center_hz"))
                and abs(s["center_hz"] - F0_LINE_HZ) < 5.0
                and finite(s.get("floor_counts2perhz"))
                and abs(s["floor_counts2perhz"] / 200.0 - 1.0) < 0.2):
            fits_ok = False
    check("tuning ladder: per-setting fits recover the injected amp (+/-"
          "0.12), sign, b/a (+/-0.5), FWHM, center and floor at >= 5 sigma",
          fits_ok, json.dumps(fit_detail))
    fallback = [s for s in settings if s.get("setting_index") is None]
    check("tuning ladder: unlabeled blocks %s are one setting each, "
          "insignificant, and a QA WARN names them and the missing field"
          % untagged,
          [s.get("expnos") for s in fallback] == [[e] for e in untagged]
          and all((s.get("significance") or 0) < 5.0 for s in fallback)
          and any(q["check"] == "tuning ladder metadata"
                  and q["level"] == "WARN"
                  and all(str(e) in q["detail"] for e in untagged)
                  and "tuning.setting_index" in q["detail"]
                  for q in s_t.get("qa_flags") or []),
          json.dumps([q for q in s_t.get("qa_flags") or []
                      if "tuning" in q["check"]])[:400])
    near = tl.get("nearest_optimum") or {}
    check("tuning ladder: nearest optimum is setting 0 (pure dip: |b/a| "
          "smallest among the >= 5 sigma settings OF THE RT EQUILIBRIUM "
          "SIGN); the dispersive BUMP setting 1 is listed as the far-detuned "
          "side, not a candidate (2 candidates of 3 significant); the rule "
          "names the probe class and the sign",
          near.get("setting_index") == 0 and near.get("expnos") == tune_expnos[0]
          and near.get("sign") == "dip"
          and near.get("expected_sign") == "dip"
          and near.get("probe_type") == "RT"
          and near.get("n_candidates") == 2 and near.get("n_significant") == 3
          and [o["setting_index"] for o in
               near.get("opposite_sign_settings") or []] == [1]
          and "far-detuned" in near.get("note", "")
          and "smallest |b/a|" in near.get("rule", "")
          and "5 sigma" in near.get("rule", "")
          and "equilibrium expectation" in near.get("rule", "")
          and "RT probe" in near.get("rule", ""),
          json.dumps(near)[:700])
    check("tuning ladder: note and honesty say the blocks are NEVER co-added "
          "with the headline; HTML card with table, nearest-optimum line "
          "and the far-detuned statement",
          "NEVER co-added" in tl.get("note", "")
          and any("NEVER co-added" in h for h in s_t.get("honesty") or [])
          and "Spin-noise tuning ladder" in html_t
          and "Nearest the spin-noise tuning optimum: setting 0" in html_t
          and "operator's normal tuning" in html_t
          and "far-detuned side" in html_t
          and "never co-added with the headline" in html_t)
    # the selection rule on fabricated settings: the reviewer's case of
    # an RT probe whose purest line is a BUMP
    FAB = [
        {"setting_index": 0, "label": "normal", "sign": "bump",
         "asymmetry_b_over_a": -0.15, "significance": 20.0,
         "amp_norm": 0.27, "expnos": [1]},
        {"setting_index": 2, "label": "further", "sign": "bump",
         "asymmetry_b_over_a": -0.05, "significance": 18.0,
         "amp_norm": 0.30, "expnos": [2]},
        {"setting_index": 3, "label": "other way", "sign": "dip",
         "asymmetry_b_over_a": 0.10, "significance": 12.0,
         "amp_norm": -0.20, "expnos": [3]}]
    n_rt = fr.tuning_nearest_optimum(FAB, "RT")
    n_bumps = fr.tuning_nearest_optimum(FAB[:2], "RT")
    n_cryo = fr.tuning_nearest_optimum(FAB, "He-cryo")
    n_unk = fr.tuning_nearest_optimum(FAB, "unknown")
    n_weak = fr.tuning_nearest_optimum(
        [dict(s, significance=2.0) for s in FAB], "RT")
    check("tuning_nearest_optimum: RT probe with bumps at |b/a| 0.15 / 0.05 "
          "and a dip at 0.10 -> the DIP (setting 3), the bumps named as "
          "the far-detuned side; bumps only -> undetermined, 'the purest "
          "bump is not it'; He-cryo -> the purest bump (setting 2); probe "
          "'unknown' -> undetermined, no sign expectation; all below 5 "
          "sigma -> undetermined",
          n_rt.get("setting_index") == 3 and n_rt.get("n_candidates") == 1
          and [o["setting_index"] for o in n_rt["opposite_sign_settings"]]
          == [0, 2] and "far-detuned" in n_rt.get("note", "")
          and n_bumps.get("setting_index") is None
          and "purest bump is not it" in n_bumps.get("why", "")
          and n_cryo.get("setting_index") == 2
          and n_cryo.get("expected_sign") == "bump"
          and n_unk.get("setting_index") is None
          and n_unk.get("expected_sign") is None
          and "no equilibrium sign expectation" in n_unk.get("why", "")
          and n_weak.get("setting_index") is None
          and "no setting reaches 5 sigma" in n_weak.get("why", ""),
          json.dumps({"rt": n_rt, "bumps": n_bumps.get("why"),
                      "unk": n_unk.get("why")})[:700])
    hd_t = s_t.get("headline") or {}
    check("RT bump headline: sign-vs-probe wording is the EQUILIBRIUM "
          "expectation qualified by tuning state (QA WARN), never a bare "
          "'expect a Gueron dip'",
          "UNEXPECTED for the equilibrium state" in hd_t.get(
              "sign_vs_probe_type", "")
          and "tuning state" in hd_t.get("sign_vs_probe_type", "")
          and "tuning ladder" in hd_t.get("sign_vs_probe_type", "")
          and any(q["check"] == "feature sign vs probe type"
                  for q in s_t.get("qa_flags") or []),
          hd_t.get("sign_vs_probe_type"))

    # ------------------------------------------------------------------
    # 10. v0.7.1: --meta-override (RT temperatures after the fact)
    # ------------------------------------------------------------------
    # probe_type 'RT' equals the bundle's own value: listed as unchanged,
    # never counted as an override
    ov_path = os.path.join(work, "override_rt.json")
    with open(ov_path, "w") as fh:
        json.dump({"spectrometer": {"coil_temp_k": 296.5,
                                    "preamp_temp_k": 296.5,
                                    "probe_type": "RT"}}, fh)
    with open(ov_path, "rb") as fh:
        ov_sha = hashlib.sha256(fh.read()).hexdigest()
    rep_o, html_o = run_report(tune_zip, os.path.join(work, "report_override"),
                               ["--meta-override", ov_path])
    s_o = rep_o.get("science") or {}
    mo = s_o.get("meta_overrides") or {}
    ovs = mo.get("overrides") or []
    check("--meta-override: the two CHANGED dotted paths recorded with old "
          "null -> new 296.5, the equal probe_type listed as unchanged (not "
          "an override), source by basename + sha256 (no absolute path), "
          "note in science.meta_overrides",
          mo.get("n_overrides") == 2
          and [o["path"] for o in ovs] == ["spectrometer.coil_temp_k",
                                           "spectrometer.preamp_temp_k"]
          and all(o["old"] is None and o["present_before"] is True
                  and o["new"] == 296.5 for o in ovs)
          and mo.get("unchanged_paths") == ["spectrometer.probe_type"]
          and mo.get("source") == "override_rt.json"
          and mo.get("source_sha256") == ov_sha
          and "schema-checked unchanged" in mo.get("note", "")
          and "validated" not in mo.get("note", "")
          and not any("validated" in h for h in s_o.get("honesty") or []),
          json.dumps(mo)[:600])
    tc_o = s_o.get("temperature_contrast") or {}
    check("--meta-override: temperature-contrast point computed for the RT "
          "bundle -- temps 296.5/296.5/298, equilibrium expectation 'dip', "
          "bump measured -> the model's sign disagreement is stated plainly, "
          "no lambda_r forced, no crash",
          tc_o.get("coil_temp_k") == 296.5 and tc_o.get("preamp_temp_k") == 296.5
          and tc_o.get("sample_temp_k") == 298.0
          and "requires" not in tc_o.get("status", "")
          and "disagrees with the data" in tc_o.get("status", "")
          and tc_o.get("lambda_r_solutions_per_s") == []
          and tc_o.get("predicted_sign_at_declared_temps", "").startswith("dip")
          and tc_o.get("measured_contrast_within_max") is False
          and finite(tc_o.get("f_c")) and abs(tc_o["f_c"] - 0.5) < 1e-9,
          json.dumps(tc_o)[:600])
    check("--meta-override: the depth bound is stated as CONDITIONAL on "
          "preamp_temp_k standing in for the amplifier noise temperature "
          "T_A (schema: physical temperature), with the f_c a 50-100 K "
          "noise temperature would give, in JSON and HTML",
          "with T_A = preamp_temp_k = 296.5 K" in tc_o.get(
              "predicted_sign_at_declared_temps", "")
          and "physical temperature" in tc_o.get("t_a_identification", "")
          and "noise temperature T_A" in tc_o.get("t_a_identification", "")
          and "50-100 K" in tc_o.get("t_a_identification", "")
          and ("f_c = %.2f-%.2f" % (296.5 / 396.5, 296.5 / 346.5))
          in tc_o.get("t_a_identification", "")
          and "T_A:" in html_o
          and "stands in for the amplifier noise temperature" in html_o,
          tc_o.get("t_a_identification"))
    qa_mo = [q for q in s_o.get("qa_flags") or []
             if q["check"] == "meta overrides"]
    check("--meta-override: HTML 'Overrides applied:' honesty line with both "
          "changed paths and the unchanged one named as such, QA WARN "
          "'2 field(s) overridden' naming the unchanged path, headline "
          "unchanged otherwise",
          "Overrides applied:" in html_o
          and "spectrometer.coil_temp_k null -&gt; 296.5" in html_o
          and len(qa_mo) == 1 and qa_mo[0]["level"] == "WARN"
          and "2 field(s) overridden" in qa_mo[0]["detail"]
          and "not overridden: spectrometer.probe_type" in qa_mo[0]["detail"]
          and any(h.startswith("Overrides applied")
                  and "spectrometer.probe_type were equal" in h
                  and "sha256" in h
                  for h in s_o.get("honesty") or [])
          and (s_o.get("noise") or {}).get("n_rows") == 3
          and "disagrees with the data" in html_o,
          json.dumps(qa_mo)[:400])
    # an override that changes nothing: recorded, QA OK, no 'applied' line
    noop_ov = os.path.join(work, "override_noop.json")
    with open(noop_ov, "w") as fh:
        json.dump({"spectrometer": {"probe_type": "RT"}}, fh)
    rep_n, html_n = run_report(
        tune_zip, os.path.join(work, "report_override_noop"),
        ["--meta-override", noop_ov])
    s_n = rep_n.get("science") or {}
    mo_n = s_n.get("meta_overrides") or {}
    qa_n = [q for q in s_n.get("qa_flags") or []
            if q["check"] == "meta overrides"]
    check("--meta-override that changes no value: n_overrides 0 with the "
          "path listed as unchanged, QA row OK (not WARN) saying so, no "
          "'Overrides applied' honesty line, temperature point still "
          "'requires'",
          mo_n.get("n_overrides") == 0 and mo_n.get("overrides") == []
          and mo_n.get("unchanged_paths") == ["spectrometer.probe_type"]
          and len(qa_n) == 1 and qa_n[0]["level"] == "OK"
          and "changed no value" in qa_n[0]["detail"]
          and not any(h.startswith("Overrides applied")
                      for h in s_n.get("honesty") or [])
          and "Overrides applied" not in html_n
          and "requires" in (s_n.get("temperature_contrast") or {}).get(
              "status", ""),
          json.dumps({"mo": mo_n, "qa": qa_n})[:400])
    check("without --meta-override the same bundle still says 'requires "
          "coil/preamp temperatures' and carries meta_overrides null",
          "requires" in (s_t.get("temperature_contrast") or {}).get(
              "status", "") and s_t.get("meta_overrides") is None
          and "Overrides applied" not in html_t)
    bad_ov = os.path.join(work, "override_bad.json")
    with open(bad_ov, "w") as fh:
        json.dump({"spectrometer": {"coil_temp_k": "warm"}}, fh)
    bad_dir = os.path.join(work, "report_override_bad")
    rc, out = run_report_expect_failure(tune_zip, bad_dir,
                                        ["--meta-override", bad_ov])
    check("--meta-override with a schema-invalid value is REJECTED: exit 1, "
          "the schema error named, no report written",
          rc == 1 and "fails schema validation" in out
          and "coil_temp_k" in out
          and not os.path.exists(os.path.join(bad_dir, "report.json")),
          "rc=%s out=%s" % (rc, out[-400:]))
    list_ov = os.path.join(work, "override_list.json")
    with open(list_ov, "w") as fh:
        json.dump([1, 2], fh)
    rc2, out2 = run_report_expect_failure(
        tune_zip, os.path.join(work, "report_override_list"),
        ["--meta-override", list_ov])
    check("--meta-override whose top level is not an object: exit 2 with a "
          "clear error", rc2 == 2 and "not a JSON object" in out2,
          "rc=%s out=%s" % (rc2, out2[-300:]))
    # the master-formula inversion itself, both regimes
    notes = []
    dip_tc = fr.temperature_contrast_point(
        {"spectrometer": {"coil_temp_k": 296.5, "preamp_temp_k": 296.5,
                          "probe_type": "RT"},
         "sample": {"vt_setpoint_k": 298.0}},
        {"amp_norm": -0.2, "amp_err": 0.01, "fwhm_hz": 15.0,
         "asymmetry_b_over_a": 0.0}, notes)
    eq_tc = fr.temperature_contrast_point(
        {"spectrometer": {"coil_temp_k": 298.0, "preamp_temp_k": 298.0,
                          "probe_type": "RT"},
         "sample": {"vt_setpoint_k": 298.0}},
        {"amp_norm": -0.2, "amp_err": 0.01, "fwhm_hz": 15.0,
         "asymmetry_b_over_a": 0.0}, notes)
    cryo_tc = fr.temperature_contrast_point(
        {"spectrometer": {"coil_temp_k": 20.0, "preamp_temp_k": 15.0,
                          "probe_type": "He-cryo"},
         "sample": {"vt_setpoint_k": 298.0}},
        {"amp_norm": 0.5, "amp_err": 0.01, "fwhm_hz": 15.0,
         "asymmetry_b_over_a": 0.0}, notes)
    check("temperature_contrast_point: RT dip -> a physical lambda_r "
          "solution; Ts = Tc exactly -> the linear case solves; cryo bump "
          "-> bump regime with its max contrast",
          dip_tc.get("status", "").startswith("computed")
          and len(dip_tc.get("lambda_r_solutions_per_s") or []) >= 1
          and all(0 < r <= dip_tc["lambda_tot_per_s"] * 1.0001
                  for r in dip_tc["lambda_r_solutions_per_s"])
          and eq_tc.get("status", "").startswith("computed")
          and len(eq_tc.get("lambda_r_solutions_per_s") or []) == 1
          and cryo_tc.get("status", "").startswith("computed")
          and cryo_tc.get("predicted_sign_at_declared_temps", "").startswith(
              "bump") and cryo_tc.get("measured_contrast_within_max") is True,
          json.dumps({"dip": dip_tc, "eq": eq_tc, "cryo": cryo_tc})[:800])
    recs = []
    merged = fr.deep_merge_meta(
        {"a": {"b": 1, "c": [1, 2]}, "d": None},
        {"a": {"b": 2, "c": [3]}, "d": {"x": 1}, "e": 5}, records=recs)
    check("deep_merge_meta: objects merge key by key, lists and scalars "
          "replace, new keys recorded as absent, the input is not mutated",
          merged == {"a": {"b": 2, "c": [3]}, "d": {"x": 1}, "e": 5}
          and [(r["path"], r["present_before"]) for r in recs]
          == [("a.b", True), ("a.c", True), ("d", True), ("e", False)],
          json.dumps({"merged": merged, "recs": recs}))
    recs2, same = [], []
    merged2 = fr.deep_merge_meta(
        {"a": {"b": 1, "c": [1, 2]}, "d": None},
        {"a": {"b": 1, "c": [1, 2]}, "d": None, "e": 5},
        records=recs2, unchanged=same)
    check("deep_merge_meta: a leaf equal to the base value (scalar, list, "
          "null) goes to `unchanged`, not to the records; long values are "
          "summarized by size in the override sentences",
          merged2 == {"a": {"b": 1, "c": [1, 2]}, "d": None, "e": 5}
          and [r["path"] for r in recs2] == ["e"]
          and same == ["a.b", "a.c", "d"]
          and fr.override_value_text(list(range(40))) == "[list of 40 item(s)]"
          and fr.override_value_text(296.5) == "296.5"
          and fr.override_change_text(
              {"path": "x", "present_before": False, "new": 1, "old": None})
          == "x (absent) -> 1",
          json.dumps({"recs": recs2, "same": same}))

    # ------------------------------------------------------------------
    # 11. v0.7.1: randomized repeated-rung ladder with an injected drift
    # ------------------------------------------------------------------
    LEVELS_DB = [0, 5, 10, 15, 20]
    COMPRESSION = {0: 1.0, 5: 0.99, 10: 0.97, 15: 0.94, 20: 0.90}
    BETA_INJ = 0.008                    # per minute (+0.8 %/min)
    fb = FixtureBuilder(bundle, gen, seed=37)
    fb.line_references()
    plan = fb.randomized_ladder(100, LEVELS_DB, 3, COMPRESSION, BETA_INJ)
    lad_zip = os.path.join(work, os.path.basename(bundle)[:-9] + "_ladr.zip")
    lad_meta = fb.write(lad_zip)
    rep_l, html_l = run_report(lad_zip, os.path.join(work, "report_ladder"))
    s_l = rep_l.get("science") or {}
    ld = s_l.get("rg_ladder") or {}
    drift = ld.get("drift") or {}
    lv = ld.get("levels") or []
    order_db = [db for _e, db, _t in plan]
    check("randomized ladder fixture: 15 rungs, 5 levels x 3 visits in a "
          "shuffled order, calibration.rg_ladder lists every rung "
          "(duplicates allowed), meta order = time order",
          len(lad_meta["calibration"]["rg_ladder"]) == 15
          and order_db != sorted(order_db)
          and [e["expno"] for e in lad_meta["experiments"]
               if e["role"] == "rg_ladder"] == list(range(100, 115)),
          str(order_db))
    check("drift ladder: model drift_compression over 15 visits of 5 levels "
          "in 'randomized' time order (stated in the note and the headline "
          "assumption), every rung read, none unreadable",
          ld.get("available") is True and ld.get("model") == "drift_compression"
          and ld.get("n_visits") == 15 and ld.get("n_levels") == 5
          and ld.get("time_order") == "randomized"
          and "randomized time order" in ld.get("note", "")
          and (s_l.get("headline") or {}).get("rg_ladder_time_order")
          == "randomized"
          and any("in randomized time order" in a_ for a_ in (
              s_l.get("headline") or {}).get("assumptions", []))
          and "randomized time order" in html_l
          and not ld.get("unreadable") and len(ld.get("rungs") or []) == 15
          and all(finite(r.get("t_min")) and finite(
              r.get("drift_corrected_deviation")) for r in ld["rungs"]),
          json.dumps({k: ld.get(k) for k in (
              "available", "model", "n_visits", "n_levels", "time_order",
              "unreadable")}))
    b_fit, b_err = drift.get("beta_pct_per_min"), drift.get("beta_err_pct_per_min")
    check("drift ladder: beta = %s +/- %s %%/min recovers the injected "
          "+0.8 %%/min within 2 sigma, with a meaningful error (< 0.25 "
          "%%/min), > 3 sigma significance, t0 and span recorded"
          % (fmt3(b_fit), fmt3(b_err)),
          finite(b_fit) and finite(b_err) and b_err < 0.25
          and abs(b_fit - 100 * BETA_INJ) <= 2.0 * b_err
          and (drift.get("significance") or 0) > 3.0
          and drift.get("t0_local") == iso_stamp(FIXTURE_T0)
          and abs(drift.get("span_min", 0) - 14 * 12 / 60.0) < 1e-9,
          json.dumps(drift))
    comp_ok, comp_detail = True, []
    for level in lv:
        db = int(round(level["rg_db"]))
        c_inj = COMPRESSION.get(db)
        comp_detail.append({"db": db, "c": level.get("compression"),
                            "err": level.get("compression_err"),
                            "scatter": level.get("scatter_over_repeats_rel"),
                            "n": level.get("n_visits")})
        if c_inj is None or level.get("n_visits") != 3 \
                or not finite(level.get("compression")) \
                or abs(level["compression"] - c_inj) > 0.01 \
                or abs(level["compression"] - c_inj) > 3.0 * max(
                    level.get("compression_err") or 0.0, 1e-4) \
                or not finite(level.get("scatter_over_repeats_rel")) \
                or level["scatter_over_repeats_rel"] > 0.01:
            comp_ok = False
    check("drift ladder: per-level compression recovers the injected "
          "1.00/0.99/0.97/0.94/0.90 within 0.01 and 3 sigma, 3 visits per "
          "level, scatter over repeats < 1%%, lowest level fixed to 1",
          comp_ok and len(lv) == 5 and lv[0]["compression"] == 1.0
          and lv[0]["compression_err"] == 0.0
          and [int(round(l_["rg_db"])) for l_ in lv] == LEVELS_DB,
          json.dumps(comp_detail))
    env = max(abs(l_["compression"] - 1.0) + l_["compression_err"]
              for l_ in lv) if lv else None
    check("drift ladder: envelope = max |c - 1| + err (about 10%%), residual "
          "RMS < 1%%, headline rg_linearity_power_envelope = 2 x envelope "
          "(drift-corrected), stated in the note",
          env is not None and abs(ld.get("max_abs_fractional_deviation", -1)
                                  - env) < 1e-12
          and 0.09 < env < 0.12
          and abs(ld.get("max_abs_power_deviation", -1) - 2 * env) < 1e-12
          and ld.get("residual_rms_rel", 1) < 0.01
          and abs((s_l.get("headline") or {}).get(
              "rg_linearity_power_envelope", -1) - 2 * env) < 1e-12
          and "drift" in ld.get("note", "") and "identifiable" in ld.get(
              "note", ""),
          json.dumps({"env": env, "ld": {k: ld.get(k) for k in (
              "max_abs_fractional_deviation", "max_abs_power_deviation",
              "residual_rms_rel")}, "hd": (s_l.get("headline") or {}).get(
                  "rg_linearity_power_envelope")}))
    check("drift ladder: HTML card shows the compression table, the drift "
          "rate in %/min, the drift-corrected column and the ladder figure",
          "Receiver compression envelope" in html_l and "%/min" in html_l
          and "drift-corrected" in html_l and "scatter over repeats" in html_l
          and "drift removed" in html_l and "alt='ladder'" in html_l)
    # the same rungs with the time stamps removed: repeats without time
    # -> compression_only, drift declared not separable
    def _strip_times(m):
        for e in m["experiments"]:
            if e["role"] == "rg_ladder":
                e["started_local"] = "unknown"
                e["finished_local"] = "unknown"
    notime_zip = os.path.join(work, os.path.basename(bundle)[:-9]
                              + "_ladt.zip")
    derive_bundle(lad_zip, notime_zip, edit_meta=_strip_times)
    rep_nt, _html_nt = run_report(notime_zip, os.path.join(work,
                                                            "report_ladder_nt"))
    ld_nt = (rep_nt.get("science") or {}).get("rg_ladder") or {}
    check("repeated rungs WITHOUT usable started_local -> compression_only, "
          "degenerate note naming the expnos, no drift block",
          ld_nt.get("model") == "compression_only" and "drift" not in ld_nt
          and "no usable started_local" in ld_nt.get("degenerate_note", "")
          and "100" in ld_nt.get("degenerate_note", "")
          and len(ld_nt.get("levels") or []) == 5,
          json.dumps({k: ld_nt.get(k) for k in ("model", "degenerate_note")})[:400])
    # one rung a BLANK fid (all zeros: amplitude exactly 0): a failed
    # acquisition must not abort the report -- it is excluded as
    # featureless, listed, and the drift fit runs on the other 14
    fb = FixtureBuilder(bundle, gen, seed=37)
    fb.line_references()
    plan_z = fb.randomized_ladder(100, LEVELS_DB, 3, COMPRESSION, BETA_INJ)
    blank_expno = plan_z[7][0]
    fb.members["data/%d/fid" % blank_expno] = varian_fid_f32(
        np.zeros(8192, dtype=complex))
    zl_zip = os.path.join(work, os.path.basename(bundle)[:-9] + "_ladz.zip")
    fb.write(zl_zip)
    rep_z, html_z = run_report(zl_zip, os.path.join(work, "report_ladder_z"))
    s_z = rep_z.get("science") or {}
    ld_z = s_z.get("rg_ladder") or {}
    fz = ld_z.get("featureless") or []
    dr_z = ld_z.get("drift") or {}
    check("randomized ladder with one BLANK rung (expno %d, all-zero fid): "
          "the report still runs, the rung is excluded as featureless "
          "(amplitude 0) and listed in the note and the HTML, the drift fit "
          "covers the other 14 visits and recovers beta within 3 sigma, "
          "the headline envelope is populated" % blank_expno,
          ld_z.get("available") is True
          and ld_z.get("model") == "drift_compression"
          and ld_z.get("n_visits") == 14 and len(ld_z.get("rungs") or []) == 14
          and blank_expno not in [r["expno"] for r in ld_z["rungs"]]
          and [f["expno"] for f in fz] == [blank_expno]
          and fz[0].get("amplitude_counts") == 0.0
          and "featureless" in ld_z.get("note", "")
          and "featureless" in html_z
          and finite(dr_z.get("beta_pct_per_min"))
          and abs(dr_z["beta_pct_per_min"] - 100 * BETA_INJ)
          <= 3.0 * dr_z.get("beta_err_pct_per_min", 0.0)
          and finite((s_z.get("headline") or {}).get(
              "rg_linearity_power_envelope")),
          json.dumps({"fz": fz, "model": ld_z.get("model"),
                      "n": ld_z.get("n_visits"), "drift": dr_z,
                      "unreadable": ld_z.get("unreadable")})[:600])
    z_rungs = []
    for k, (db, t) in enumerate([(0, 0), (5, 1), (10, 2), (0, 3), (5, 4),
                                 (10, 5)]):
        rg = 10.0 ** (db / 20.0)
        z_rungs.append({"expno": 100 + k, "rg": rg, "t_min": float(t),
                        "amplitude_counts": 100.0 * rg * (1 + 0.01 * t)})
    z_levels = fr.ladder_levels(z_rungs)
    z_ok = fr.fit_ladder_drift(z_rungs, z_levels)
    z_rungs[3]["amplitude_counts"] = 0.0
    z_zero = fr.fit_ladder_drift(z_rungs, z_levels)
    z_rungs[3]["amplitude_counts"] = -5.0
    z_neg = fr.fit_ladder_drift(z_rungs, z_levels)
    check("fit_ladder_drift: a rung with amplitude 0 or negative returns "
          "None (no LinAlgError, no nan), the same design with the rung "
          "positive fits beta = 1 %/min",
          z_zero is None and z_neg is None and z_ok is not None
          and abs(z_ok["beta_per_min"] - 0.01) < 1e-6,
          json.dumps(z_ok and {"beta": z_ok["beta_per_min"]}))

    # ------------------------------------------------------------------
    # 8b. v0.7.1: axis sign from the VnmrJ lock referencing (zero extra
    # acquisition), and its precedence against the signcal pair
    # ------------------------------------------------------------------
    # the real SIU session-2 noise procpar (scratch/75_sn_noise_procpar.txt)
    S2_PP = {"reffrq": 399.618776561, "sfrq": 399.6211743,
             "sw": 6410.25641026, "rfl": 807.38928971, "rfp": 0.0,
             "tn": "H1"}
    rc_m = fr.lock_referencing_check(S2_PP, 540.8, 90.0)
    rc_p = fr.lock_referencing_check(S2_PP, -540.8, 90.0)
    rc_u = fr.lock_referencing_check(S2_PP, 100.0, 90.0)
    rc_i = fr.lock_referencing_check(dict(S2_PP, rfl=817.389), 540.8, 90.0)
    rc_c = fr.lock_referencing_check(dict(S2_PP, tn="C13"), 540.8, 90.0)
    rc_w = fr.lock_referencing_check(S2_PP, 540.8, 0)
    rc_n = fr.lock_referencing_check(
        {k: v for k, v in S2_PP.items() if k != "reffrq"}, 540.8, 90.0)
    # a line 100 Hz from the carrier: the hypotheses (+/-100 Hz) lie 200
    # Hz apart, under 4 x the 120 Hz tolerance
    near = dict(S2_PP)
    near["reffrq"] = (S2_PP["sfrq"] * 1e6 + 100.0) / (1.0 + 4.75e-6) / 1e6
    near["rfl"] = (near["reffrq"] - S2_PP["sfrq"]) * 1e6 + S2_PP["sw"] / 2.0
    rc_s = fr.lock_referencing_check(near, 100.0, 90.0)
    check("lock_referencing_check on the real SIU session-2 procpar: the "
          "identity reffrq = sfrq - sw/2 + rfl - rfp holds to %.4f Hz, "
          "water predicted %.1f Hz from the carrier, measured +540.8 Hz "
          "-> MIRRORED, sign -1, tolerance %.1f Hz (0.3 ppm), hypotheses "
          "%.0f Hz apart" % (rc_m.get("identity_residual_hz", 9),
                             rc_m.get("predicted_offset_hz", 0),
                             rc_m.get("tolerance_hz", 0),
                             rc_m.get("hypothesis_separation_hz", 0)),
          rc_m["verdict"] == "mirrored" and rc_m["sign"] == -1
          and abs(rc_m["identity_residual_hz"]) < 0.01
          and abs(rc_m["predicted_offset_hz"] + 499.55) < 0.05
          and abs(rc_m["tolerance_hz"] - 0.3e-6 * 399.6211743e6) < 1e-6
          and rc_m["hypothesis_separation_hz"] > 4 * rc_m["tolerance_hz"]
          and rc_m["measured_offset_hz"] == 540.8
          and rc_m["delta_ppm_assumed"] == 4.75
          and rc_m["reffrq_mhz"] == 399.618776561
          and rc_m["rfl_hz"] == 807.38928971 and rc_m["rfp_hz"] == 0.0
          and rc_m["sw_hz"] == 6410.25641026
          and rc_m["sfrq_mhz"] == 399.6211743
          and "MIRRORED" in rc_m["summary"]
          and "carrier - reported offset" in rc_m["summary"],
          json.dumps(rc_m)[:600])
    check("lock_referencing_check: the offset negated reads PHYSICAL (+1); "
          "+100 Hz matches neither placement (undetermined, with the "
          "reason); rfl off by 10 Hz -> identity_failed; tn C13 -> "
          "not_applicable; h2o_fraction_pct 0 -> not_applicable; missing "
          "reffrq -> unavailable; a line 100 Hz from the carrier -> "
          "undetermined (hypotheses 200 Hz apart, under 4 tolerances)",
          rc_p["verdict"] == "physical" and rc_p["sign"] == 1
          and rc_u["verdict"] == "undetermined" and rc_u["sign"] is None
          and "matches neither" in rc_u["why"]
          and rc_i["verdict"] == "identity_failed" and rc_i["sign"] is None
          and abs(abs(rc_i["identity_residual_hz"]) - 10.0) < 0.01
          and rc_c["verdict"] == "not_applicable" and "C13" in rc_c["why"]
          and rc_w["verdict"] == "not_applicable"
          and "not a water sample" in rc_w["why"]
          and rc_n["verdict"] == "unavailable" and "reffrq" in rc_n["why"]
          and rc_s["verdict"] == "undetermined"
          and "too close to the carrier" in rc_s["why"]
          and abs(rc_s["hypothesis_separation_hz"] - 200.0) < 0.1,
          json.dumps({"p": rc_p["verdict"], "u": rc_u.get("why"),
                      "i": rc_i.get("why"), "c": rc_c.get("why"),
                      "w": rc_w.get("why"), "n": rc_n.get("why"),
                      "s": rc_s.get("why")})[:700])

    def _ref_fixture(tag, seed, build):
        fb_ = FixtureBuilder(bundle, gen, seed=seed)
        fb_.line_references()
        build(fb_)
        z_ = os.path.join(work, os.path.basename(bundle)[:-9]
                          + "_rf%s.zip" % tag)
        fb_.write(z_)
        rep_, html_ = run_report(z_, os.path.join(work, "report_rf" + tag))
        return rep_, html_, os.path.join(work, "report_rf" + tag,
                                         "report.json")

    # (a) references only, the generator's physical referencing -> +1
    rep_a, html_a, path_a = _ref_fixture("phys", 41, lambda fb_: None)
    s_a = rep_a.get("science") or {}
    fas_a = s_a.get("frequency_axis_sign") or {}
    rc_a = fas_a.get("referencing_check") or {}
    ex_a = s_a.get("axion_exclusion") or {}
    line_a = ex_a.get("line") or {}
    res_a = ex_a.get("result") or {}
    qa_a = [q for q in s_a.get("qa_flags") or []
            if q["check"] == "frequency-axis sign"]
    check("referencing only (no signcal), generator's physical referencing: "
          "sign +1 VERIFIED, basis vnmrj_lock_referencing, verdict physical "
          "(predicted %s vs measured %s Hz), identity to 2 Hz, source expno "
          "a reference, two-way ambiguity dropped, no calibration"
          % (fmt3(rc_a.get("predicted_offset_hz")),
             fmt3(rc_a.get("measured_offset_hz"))),
          fas_a.get("verified") is True and fas_a.get("sign") == 1
          and fas_a.get("basis") == "vnmrj_lock_referencing"
          and rc_a.get("verdict") == "physical" and rc_a.get("sign") == 1
          and abs(rc_a.get("identity_residual_hz", 9)) < 2.0
          and abs(rc_a.get("predicted_offset_hz", 0)
                  - gen.LINE_OFFSET_HZ) < 1.0
          and abs(rc_a.get("measured_offset_hz", 0) - F0_LINE_HZ) < 3.0
          and rc_a.get("source_expno") in (11, 13)
          and sorted(rc_a.get("referencing_expnos") or [])
          == sorted([11, 13] + list(gen.NOISE_EXPNOS))
          and "two_way_ambiguity_ppm" not in fas_a
          and "calibration" not in fas_a
          and "resolved by the VnmrJ lock referencing" in fas_a.get(
              "note", ""),
          json.dumps(fas_a)[:700])
    check("referencing only: exclusion on ONE mass axis (nu_L = carrier + "
          "600 Hz, no mirror), QA OK row, honesty RESOLVED line, HTML names "
          "the referencing and no 'SIGN unverified', headline block carries "
          "sign/basis, mass bookkeeping without the caveat",
          line_a.get("sign_verified") is True and line_a.get("axis_sign") == 1
          and line_a.get("axis_sign_basis") == "vnmrj_lock_referencing"
          and abs(line_a.get("nu_L_hz_nominal", 0)
                  - (carrier_hz + F0_LINE_HZ)) < 3.0
          and "m_a_uev_mirror" not in line_a
          and "m_a_at_best_mirror_uev" not in res_a
          and sorted(res_a.get("band_10x_uev") or {}) == ["nominal"]
          and "lock referencing" in line_a.get("sign_note", "")
          and len(qa_a) == 1 and qa_a[0]["level"] == "OK"
          and any("RESOLVED by the VnmrJ lock referencing" in h
                  for h in s_a.get("honesty") or [])
          and "VnmrJ lock referencing" in html_a
          and "SIGN unverified" not in html_a and "UNVERIFIED" not in html_a
          and (s_a.get("noise") or {}).get("axis_sign_unverified") is False
          and (s_a.get("noise") or {}).get("axis_sign_basis")
          == "vnmrj_lock_referencing"
          and "offset_sign_caveat" not in (
              s_a.get("axion_mass_bookkeeping") or {}),
          json.dumps({"line": line_a, "qa": qa_a})[:600])
    site_a = site_mod.combine_reports([(path_a, rep_a)])
    check("referencing only: the site combiner treats the session as sign-"
          "verified (nominal mass axis, no unverified session)",
          site_a.get("n_sessions") == 1
          and (site_a.get("combined") or {}).get("sign_unverified_sessions")
          == []
          and site_a["sessions"][0]["sign_verified"] is True
          and site_a["sessions"][0]["band_10x_basis"] == "nominal mass axis",
          json.dumps(site_a.get("sessions"))[:400])

    # (b) references only, referencing consistent with a MIRRORED axis
    rep_b, html_b, _p = _ref_fixture("mirr", 43,
                                     lambda fb_: fb_.set_referencing(-1))
    s_b = rep_b.get("science") or {}
    fas_b = s_b.get("frequency_axis_sign") or {}
    rc_b = fas_b.get("referencing_check") or {}
    line_b = (s_b.get("axion_exclusion") or {}).get("line") or {}
    check("referencing only, mirrored referencing (water predicted at -600 "
          "Hz): sign -1 VERIFIED, verdict mirrored, exclusion nu_L = carrier "
          "- 600 Hz with the offset NEGATED, HTML says MIRRORED",
          fas_b.get("verified") is True and fas_b.get("sign") == -1
          and fas_b.get("basis") == "vnmrj_lock_referencing"
          and rc_b.get("verdict") == "mirrored"
          and abs(rc_b.get("predicted_offset_hz", 0) + F0_LINE_HZ) < 1.0
          and abs(rc_b.get("identity_residual_hz", 9)) < 2.0
          and line_b.get("axis_sign") == -1
          and abs(line_b.get("nu_L_hz_nominal", 0)
                  - (carrier_hz - F0_LINE_HZ)) < 3.0
          and "NEGATED" in line_b.get("basis", "")
          and "m_a_uev_mirror" not in line_b
          and "MIRRORED" in html_b and "SIGN unverified" not in html_b,
          json.dumps({"fas": fas_b, "line": line_b})[:700])

    # (c) CONFLICT: the signcal says mirrored, the referencing physical
    rep_c, html_c, _p = _ref_fixture(
        "conf", 47, lambda fb_: fb_.signcal(60, D_HZ, +D_HZ))
    s_c = rep_c.get("science") or {}
    fas_c = s_c.get("frequency_axis_sign") or {}
    rc_c2 = fas_c.get("referencing_check") or {}
    cal_c = fas_c.get("calibration") or {}
    ex_c = s_c.get("axion_exclusion") or {}
    line_c = ex_c.get("line") or {}
    qa_c = [q for q in s_c.get("qa_flags") or []
            if q["check"] == "frequency-axis sign"]
    check("CONFLICT: signcal resolves -1 (mirrored), the referencing +1 "
          "(physical) -> verified False, sign None, basis 'conflict', "
          "two-way ambiguity restored, QA FAIL naming both determinations, "
          "honesty CONFLICT line, exclusion keeps the mirror placement and "
          "the intersection band, HTML shows the conflict",
          fas_c.get("verified") is False and fas_c.get("sign") is None
          and fas_c.get("basis") == "conflict"
          and cal_c.get("verdict") == "mirrored"
          and rc_c2.get("verdict") == "physical"
          and rc_c2.get("agreement", "").startswith("DISAGREES")
          and finite(fas_c.get("two_way_ambiguity_ppm"))
          and len(qa_c) == 1 and qa_c[0]["level"] == "FAIL"
          and "carrier-displacement" in qa_c[0]["detail"]
          and "lock referencing" in qa_c[0]["detail"]
          and "CONFLICT" in qa_c[0]["detail"]
          and any(h.startswith("Frequency-axis SIGN in CONFLICT")
                  for h in s_c.get("honesty") or [])
          and line_c.get("sign_verified") is False
          and finite(line_c.get("m_a_uev_mirror"))
          and all(k in (ex_c.get("result") or {}).get("band_10x_uev", {})
                  for k in ("nominal", "mirror", "intersection"))
          and "CONFLICT" in html_c
          and (s_c.get("noise") or {}).get("axis_sign_unverified") is True,
          json.dumps({"fas": {k: fas_c.get(k) for k in (
              "verified", "sign", "basis", "two_way_ambiguity_ppm")},
              "qa": qa_c})[:700])
    site_c = site_mod.combine_reports([(_p, rep_c)])
    check("CONFLICT: the site combiner treats the session as sign-"
          "unverified (intersection band, one unverified session)",
          site_c["sessions"][0]["sign_verified"] is False
          and site_c["sessions"][0]["band_10x_basis"]
          == "intersection of the two sign hypotheses"
          and len((site_c.get("combined") or {}).get(
              "sign_unverified_sessions") or []) == 1,
          json.dumps(site_c.get("sessions"))[:400])

    # (d) an AMBIGUOUS signcal beside a decisive referencing: the signcal
    # does not resolve the sign, the referencing does, and says so
    rep_d, html_d, _p = _ref_fixture(
        "ambg", 53, lambda fb_: fb_.signcal(60, D_HZ, +50.0))
    s_d = rep_d.get("science") or {}
    fas_d = s_d.get("frequency_axis_sign") or {}
    check("ambiguous signcal + physical referencing: sign +1 VERIFIED by the "
          "referencing, calibration recorded as ambiguous, note names the "
          "attempted calibration, QA OK",
          fas_d.get("verified") is True and fas_d.get("sign") == 1
          and fas_d.get("basis") == "vnmrj_lock_referencing"
          and (fas_d.get("calibration") or {}).get("verdict") == "ambiguous"
          and "attempted and is ambiguous" in fas_d.get("note", "")
          and (fas_d.get("referencing_check") or {}).get("verdict")
          == "physical"
          and any(q["check"] == "frequency-axis sign" and q["level"] == "OK"
                  for q in s_d.get("qa_flags") or []),
          json.dumps(fas_d)[:600])

    # (e) a water-free sample: the water-shift prediction does not apply
    def _no_water(m):
        m["sample"]["h2o_fraction_pct"] = 0
        m["sample"]["d2o_pct"] = 100
    fb = FixtureBuilder(bundle, gen, seed=59)
    fb.line_references()
    nw_zip = os.path.join(work, os.path.basename(bundle)[:-9] + "_rfnow.zip")
    fb.write(nw_zip)
    nw2_zip = os.path.join(work, os.path.basename(bundle)[:-9] + "_rfnow2.zip")
    derive_bundle(nw_zip, nw2_zip, edit_meta=_no_water)
    rep_e, html_e = run_report(nw2_zip, os.path.join(work, "report_rfnow"))
    s_e = rep_e.get("science") or {}
    fas_e = s_e.get("frequency_axis_sign") or {}
    rc_e = fas_e.get("referencing_check") or {}
    check("water-free sample (h2o_fraction_pct 0): referencing verdict "
          "not_applicable, sign stays UNVERIFIED with the mirror mass, QA "
          "WARN, honesty names the inconclusive cross-check",
          fas_e.get("verified") is False and fas_e.get("sign") is None
          and rc_e.get("verdict") == "not_applicable"
          and "not a water sample" in rc_e.get("why", "")
          and finite(((s_e.get("axion_exclusion") or {}).get("line")
                      or {}).get("m_a_uev_mirror"))
          and any(q["check"] == "frequency-axis sign" and q["level"] == "WARN"
                  for q in s_e.get("qa_flags") or [])
          and any("lock-referencing cross-check is not_applicable" in h
                  for h in s_e.get("honesty") or [])
          and "UNVERIFIED" in html_e,
          json.dumps(rc_e)[:400])

    # (f) a broken identity: reffrq does not describe this acquisition
    rep_f, _html_f, _p = _ref_fixture(
        "ident", 61, lambda fb_: fb_.set_referencing(1, rfl_shift_hz=10.0))
    s_f = rep_f.get("science") or {}
    fas_f = s_f.get("frequency_axis_sign") or {}
    rc_f = fas_f.get("referencing_check") or {}
    check("referencing identity broken by 10 Hz: verdict identity_failed "
          "with the residual, sign stays UNVERIFIED",
          fas_f.get("verified") is False
          and rc_f.get("verdict") == "identity_failed"
          and abs(abs(rc_f.get("identity_residual_hz", 0)) - 10.0) < 0.05
          and any(q["check"] == "frequency-axis sign" and q["level"] == "WARN"
                  for q in s_f.get("qa_flags") or []),
          json.dumps(rc_f)[:400])

    # (g) a quality-failing closing reference (a single-bin spike at -100
    # Hz: readable, with a center, usable_for_sign_calibration False)
    # under mirrored referencing: the measured offset is the quality-
    # passing reference's line alone (+600 Hz -> mirrored), not the two-
    # reference mean the report seeds its line search with (+250 Hz,
    # which matches neither placement within the 120 Hz tolerance)
    rep_q, _html_q, _p = _ref_fixture(
        "spike", 83, lambda fb_: (fb_.replace_fid(13, synth_line_row(
            fb_.rng, 8192, gen.SW_HZ, 400.0, F0_LINE_HZ - 700.0, 0.0, 20.0)),
                                  fb_.set_referencing(-1)))
    s_q = rep_q.get("science") or {}
    fas_q = s_q.get("frequency_axis_sign") or {}
    rc_q = fas_q.get("referencing_check") or {}
    ref13 = next((r for r in s_q.get("references") or []
                  if r.get("expno") == 13), {})
    check("quality-failing closing reference (spike at %s Hz, readable, "
          "usable_for_sign_calibration False) under mirrored referencing: "
          "measured_offset_hz is the quality-passing reference's line "
          "(expno 11, ~+600 Hz), measured_offset_expnos [11], verdict "
          "mirrored, sign -1 VERIFIED; the basis names the gap to the "
          "report's two-reference line_position_guess_hz (%s Hz)"
          % (fmt3(ref13.get("line_center_hz")),
             fmt3(rc_q.get("line_position_guess_hz"))),
          ref13.get("readable") is True
          and finite(ref13.get("line_center_hz"))
          and (ref13.get("line_quality") or {}).get(
              "usable_for_sign_calibration") is False
          and rc_q.get("measured_offset_expnos") == [11]
          and abs(rc_q.get("measured_offset_hz", 0) - F0_LINE_HZ) < 3.0
          and finite(rc_q.get("line_position_guess_hz"))
          and abs(rc_q["line_position_guess_hz"]
                  - rc_q["measured_offset_hz"]) > 300.0
          and abs((s_q.get("line_position_guess_hz") or 0)
                  - rc_q["line_position_guess_hz"]) < 1e-6
          and "from the report's line_position_guess_hz" in rc_q.get(
              "measured_offset_basis", "")
          and rc_q.get("verdict") == "mirrored" and rc_q.get("sign") == -1
          and fas_q.get("verified") is True and fas_q.get("sign") == -1
          and fas_q.get("basis") == "vnmrj_lock_referencing",
          json.dumps({"rc": rc_q, "ref13": {
              k: ref13.get(k) for k in ("readable", "line_center_hz",
                                        "line_quality")}})[:700])

    # ------------------------------------------------------------------
    # 13. co-add alignment gate: weak rows are never self-aligned
    # ------------------------------------------------------------------
    A_WEAK, A_STRONG = 0.15, 0.6
    fb = FixtureBuilder(bundle, gen, seed=67)
    fb.line_references()
    fb.headline_bump(A_WEAK, 0.0)
    for k in range(12):
        fb.noise_block(300 + k, "noise", A_WEAK, 0.0)
    weak_zip = os.path.join(work, os.path.basename(bundle)[:-9] + "_gweak.zip")
    fb.write(weak_zip)
    rep_w, html_w = run_report(weak_zip, os.path.join(work, "report_gweak"))
    s_w = rep_w.get("science") or {}
    n_w = s_w.get("noise") or {}
    ac_w = n_w.get("alignment_check") or {}
    ua_w = n_w.get("unaligned_fit") or {}
    ca_w = n_w.get("coadd_fit") or {}
    det_w = s_w.get("detection") or {}
    qa_w = [q for q in s_w.get("qa_flags") or [] if q["check"] == "co-add alignment"]
    check("weak-row block (15 rows, +%.2f injected, median per-row %s "
          "sigma): the gate 'per_row_significance_below_3sigma' fires, "
          "rows NOT self-aligned, headline_fit = unaligned_fit, both co-adds "
          "recorded, outcome check not run, every row carries "
          "npe_at_stack_line" % (A_WEAK, fmt3(ac_w.get(
              "per_row_significance_median"))),
          n_w.get("n_rows") == 15
          and ac_w.get("gate") == "per_row_significance_below_3sigma"
          and ac_w.get("gate_fired") is True
          and finite(ac_w.get("per_row_significance_median"))
          and ac_w["per_row_significance_median"] < 3.0
          and ac_w.get("per_row_significance_min_required") == 3.0
          and ac_w.get("suspect") is False
          and "not run" in ac_w.get("outcome_check", "")
          and n_w.get("headline_fit") == "unaligned_fit"
          and finite(ua_w.get("amp_norm")) and finite(ca_w.get("amp_norm"))
          and finite(ac_w.get("amp_ratio_aligned_over_unaligned"))
          and "GATED" in ac_w.get("verdict", "")
          and all(finite(pr.get("npe_at_stack_line"))
                  for pr in n_w.get("per_row") or []),
          json.dumps({k: ac_w.get(k) for k in (
              "gate", "gate_fired", "per_row_significance_median", "suspect",
              "aligned_amp", "unaligned_amp",
              "amp_ratio_aligned_over_unaligned")}))
    check("weak-row block: the unaligned headline recovers the injected "
          "+%.2f within 3 sigma (%s +/- %s), detection states the unaligned "
          "basis with the gate named, QA WARN 'co-add alignment' names the "
          "gate, honesty says 'gated off', HTML says 'gated off' and "
          "'UNALIGNED stack'" % (A_WEAK, fmt3(ua_w.get("amp_norm")),
                                fmt3(ua_w.get("amp_err"))),
          finite(ua_w.get("amp_norm"))
          and abs(ua_w["amp_norm"] - A_WEAK) < 3.0 * ua_w["amp_err"] + 0.02
          and det_w.get("fit_basis") == "unaligned_fit"
          and "gate fired" in det_w.get("fit_basis_note", "")
          and "per_row_significance_below_3sigma" in det_w.get(
              "fit_basis_note", "")
          and len(qa_w) == 1 and qa_w[0]["level"] == "WARN"
          and "gate fired" in qa_w[0]["detail"]
          and any("gated off" in h for h in s_w.get("honesty") or [])
          and "gated off" in html_w and "UNALIGNED stack" in html_w
          and fit_basis_consistent(s_w)[0],
          json.dumps({"ua": ua_w, "det": {k: det_w.get(k) for k in (
              "detected", "fit_basis", "fit_basis_note")}})[:700])
    fb = FixtureBuilder(bundle, gen, seed=71)
    fb.line_references()
    fb.headline_bump(A_STRONG, 0.0)
    for k in range(3):
        fb.noise_block(300 + k, "noise", A_STRONG, 0.0)
    strong_zip = os.path.join(work, os.path.basename(bundle)[:-9]
                              + "_gstrong.zip")
    fb.write(strong_zip)
    rep_g, html_g = run_report(strong_zip, os.path.join(work, "report_gstrong"))
    s_g = rep_g.get("science") or {}
    n_g = s_g.get("noise") or {}
    ac_g = n_g.get("alignment_check") or {}
    ca_g = n_g.get("coadd_fit") or {}
    check("strong-row block (6 rows, +%.1f injected, median per-row %s "
          "sigma >= 3): the gate passes, self-alignment attempted, outcome "
          "check run and not SUSPECT, headline_fit = coadd_fit recovering "
          "the injected amplitude within 3 sigma, detection detected on the "
          "aligned basis, no QA alignment row" % (A_STRONG, fmt3(ac_g.get(
              "per_row_significance_median"))),
          n_g.get("n_rows") == 6
          and ac_g.get("gate") == "passed" and ac_g.get("gate_fired") is False
          and finite(ac_g.get("per_row_significance_median"))
          and ac_g["per_row_significance_median"] >= 3.0
          and ac_g.get("suspect") is False
          and "run:" in ac_g.get("outcome_check", "")
          and "gate passed" in ac_g.get("verdict", "")
          and n_g.get("headline_fit") == "coadd_fit"
          and finite(ca_g.get("amp_norm"))
          and abs(ca_g["amp_norm"] - A_STRONG) < 3.0 * ca_g["amp_err"] + 0.03
          and (s_g.get("detection") or {}).get("detected") is True
          and (s_g.get("detection") or {}).get("fit_basis") == "coadd_fit"
          and not any(q["check"] == "co-add alignment"
                      for q in s_g.get("qa_flags") or [])
          and "gated off" not in html_g
          and fit_basis_consistent(s_g)[0],
          json.dumps({k: ac_g.get(k) for k in (
              "gate", "gate_fired", "per_row_significance_median", "suspect",
              "aligned_amp", "unaligned_amp")}))
    # the mirror image of the strong-row case (same seed, opposite sign):
    # the equilibrium sign on a room-temperature probe is a DIP, whose
    # per-row matched-filter NPE is negative
    fb = FixtureBuilder(bundle, gen, seed=71)
    fb.line_references()
    fb.headline_bump(-A_STRONG, 0.0)
    for k in range(3):
        fb.noise_block(300 + k, "noise", -A_STRONG, 0.0)
    dip_zip = os.path.join(work, os.path.basename(bundle)[:-9] + "_gdip.zip")
    fb.write(dip_zip)
    rep_d, html_d = run_report(dip_zip, os.path.join(work, "report_gdip"))
    s_d = rep_d.get("science") or {}
    n_d = s_d.get("noise") or {}
    ac_d = n_d.get("alignment_check") or {}
    ca_d = n_d.get("coadd_fit") or {}
    raw_d = [pr.get("npe_at_stack_line") for pr in n_d.get("per_row") or []]
    check("strong-DIP block (6 rows, %.1f injected; median per-row %s sigma "
          "in the dip's sign): the gate passes -- a dip and a bump of equal "
          "strength read alike -- raw per-row NPE all negative with "
          "per_row_significance_sign -1, headline_fit = coadd_fit recovering "
          "the injected amplitude within 3 sigma, detected as an absorption "
          "DIP on the aligned basis, no QA alignment row"
          % (-A_STRONG, fmt3(ac_d.get("per_row_significance_median"))),
          n_d.get("n_rows") == 6
          and ac_d.get("gate") == "passed" and ac_d.get("gate_fired") is False
          and finite(ac_d.get("per_row_significance_median"))
          and ac_d["per_row_significance_median"] >= 3.0
          and ac_d.get("per_row_significance_sign") == -1
          and len(raw_d) == 6 and all(finite(v) and v < 0 for v in raw_d)
          and ac_d.get("suspect") is False
          and "run:" in ac_d.get("outcome_check", "")
          and n_d.get("headline_fit") == "coadd_fit"
          and finite(ca_d.get("amp_norm"))
          and abs(ca_d["amp_norm"] + A_STRONG) < 3.0 * ca_d["amp_err"] + 0.03
          and (s_d.get("detection") or {}).get("detected") is True
          and (s_d.get("detection") or {}).get("fit_basis") == "coadd_fit"
          and not any(q["check"] == "co-add alignment"
                      for q in s_d.get("qa_flags") or [])
          and "absorption DIP" in html_d and "gated off" not in html_d
          and fit_basis_consistent(s_d)[0],
          json.dumps({k: ac_d.get(k) for k in (
              "gate", "gate_fired", "per_row_significance_median",
              "per_row_significance_sign", "suspect", "aligned_amp",
              "unaligned_amp")}))

    # ------------------------------------------------------------------
    # 12. real bundles, when given
    # ------------------------------------------------------------------
    for path in args.real_bundle:
        check_real_bundle(os.path.abspath(path), work)

    print("")
    if FAILURES:
        print("AGILENT REPORT TEST: FAIL (%d)" % len(FAILURES))
        return 1
    print("AGILENT REPORT TEST: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
