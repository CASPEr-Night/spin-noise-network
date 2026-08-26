#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_jeol_reader.py -- tests for the JEOL ingestion chain.

    python3 vendors/jeol/test_jeol_reader.py            # or: -m unittest

What is proven and what is not:
  * The JCAMP-DX tests are a genuine end-to-end validation of that path
    (published text format, synthetic files, exact numeric recovery,
    AFFN and DIFDUP forms cross-checked).
  * The .jdf tests prove the reader and the synthetic writer agree on the
    layout that was verified against real Delta files during development;
    they do NOT prove a live ECZ/ECZL console writes nothing surprising.
    That is a partner-session deliverable (vendors/jeol/README.md).
  * If the environment variable JEOL_REAL_DATA_DIR points at a directory
    of real .jdf files, an extra smoke test parses every one of them.

Python 3 stdlib only.
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jeol_reader                                    # noqa: E402
import make_synthetic_jeol as synth                   # noqa: E402


class SessionMixin(object):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="jeol_test_")
        cls.files = synth.build_session(cls.tmp, formats=("jdf", "jcamp"),
                                        npts=512)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)


class TestJcampPath(SessionMixin, unittest.TestCase):
    """The robust ingestion route: JCAMP-DX must fully work."""

    def test_ntuples_affn_roundtrip(self):
        """Synthetic FID -> JCAMP NTUPLES (AFFN) -> reader recovers the
        waveform to the 2^-24 quantization the format's FACTOR imposes."""
        ref_re, ref_im = synth.synth_fid(512, 1.0 / 6900.0, 250.0, 0.5,
                                         1000.0, 1, 1.0)
        result = jeol_reader.read_jcamp(self.path("11_sn_ref_open.jdx"))
        self.assertEqual(result["info"]["data_class"], "NTUPLES")
        self.assertEqual(result["info"]["data_type"], "NMR FID")
        self.assertIn("re", result["data"])
        self.assertIn("im", result["data"])
        self.assertEqual(len(result["data"]["re"]), 512)
        peak = max(max(abs(v) for v in ref_re),
                   max(abs(v) for v in ref_im))
        tol = peak / 2 ** 23        # one quantization step of slack
        for a, b in zip(ref_re, result["data"]["re"]):
            self.assertAlmostEqual(a, b, delta=tol)
        for a, b in zip(ref_im, result["data"]["im"]):
            self.assertAlmostEqual(a, b, delta=tol)

    def test_ntuples_metadata(self):
        result = jeol_reader.read_jcamp(self.path("12_sn_noise.jdx"))
        info = result["info"]
        self.assertAlmostEqual(info["spectrometer_freq_mhz"], 400.13,
                               places=6)
        self.assertEqual(info["nucleus"], "^1H")
        self.assertTrue(info["title"].startswith("SYNTHETIC"))

    def test_difdup_equals_affn(self):
        """The DIFDUP-compressed copy must decode to exactly the same
        integer table as the AFFN copy (same FACTOR, same data)."""
        affn = jeol_reader.read_jcamp(self.path("12_sn_noise.jdx"))
        difdup = jeol_reader.read_jcamp(os.path.join(self.tmp, "extras", "12_sn_noise_difdup.jdx"))
        for section in ("re", "im"):
            a = affn["data"][section]
            d = difdup["data"][section]
            self.assertEqual(len(a), len(d))
            for va, vd in zip(a, d):
                self.assertAlmostEqual(va, vd, delta=abs(va) * 1e-12 + 1e-30)

    def test_xydata_spectrum(self):
        result = jeol_reader.read_jcamp(os.path.join(self.tmp, "extras", "sn_spectrum_demo.jdx"))
        y = result["data"]["y"]
        self.assertEqual(len(y), 512)
        # the synthetic Lorentzian peaks at index npts//3 of the 2048-grid
        # generator call -- here simply: max value ~1000
        self.assertAlmostEqual(max(y), 1000.0, delta=1000.0 / 2 ** 20)

    def test_bundle_adapter_jcamp(self):
        result = jeol_reader.read_jcamp(self.path("12_sn_noise.jdx"))
        rec, notes = jeol_reader.to_experiment_record(
            result, expno=12, role="noise",
            started_local="2026-08-14T09:00:00",
            finished_local="2026-08-14T09:00:19")
        required = ["expno", "role", "pulprog", "td", "td1_rows", "sw_hz",
                    "o1_hz", "rg", "ns", "aq_s_per_row", "started_local",
                    "finished_local"]
        for key in required:
            self.assertIn(key, rec)
        self.assertEqual(rec["td"], 1024)   # 512 complex -> Bruker-style TD
        self.assertEqual(rec["role"], "noise")


class TestJdfPath(SessionMixin, unittest.TestCase):
    """Native .jdf: internal-consistency roundtrip on the verified layout."""

    def test_header_fields(self):
        result = jeol_reader.read_jdf(self.path("12_sn_noise.jdf"))
        h = result["header"]
        self.assertEqual(h["endian"], "little")
        self.assertEqual(h["data_format"], "One_D")
        self.assertEqual(h["data_type"], "float64")
        self.assertEqual(h["instrument"], "ECA")
        self.assertEqual(h["axis_type"][0], "Complex")
        self.assertEqual(h["data_points"][0], 512)
        self.assertEqual(h["creation_date"]["year"], 2026)
        self.assertEqual(h["creation_date"]["month"], 8)
        self.assertEqual(h["creation_date"]["day"], 14)
        self.assertIn("SYNTHETIC", h["title"])

    def test_param_digest(self):
        result = jeol_reader.read_jdf(self.path("12_sn_noise.jdf"))
        info = result["info"]
        self.assertEqual(info["nucleus"], "Proton")
        self.assertEqual(info["x_points"], 512)
        self.assertEqual(info["scans"], 1)
        self.assertAlmostEqual(info["sweep_hz"], 6900.0, places=6)
        self.assertAlmostEqual(info["spectrometer_freq_hz"], 400.13e6,
                               delta=1.0)
        self.assertAlmostEqual(info["recvr_gain_raw"], 60.0, places=9)
        self.assertEqual(info["experiment"], "sn_nopulse.jxp")
        # 1990-epoch decode sanity: generator stamps ~2026-08-14
        self.assertTrue(info["actual_start_time_iso"].startswith("2026-08-1"))

    def test_data_roundtrip_exact(self):
        """float64 in, float64 out: bit-exact recovery."""
        nre, nim = synth.synth_noise(512, 1.0 / 6900.0, 250.0, 12.0, 3.0,
                                     101, 1.0)
        result = jeol_reader.read_jdf(self.path("12_sn_noise.jdf"))
        self.assertEqual(result["data"]["re"], nre)
        self.assertEqual(result["data"]["im"], nim)

    def test_bundle_adapter_jdf(self):
        result = jeol_reader.read_jdf(self.path("11_sn_ref_open.jdf"))
        rec, notes = jeol_reader.to_experiment_record(result, expno=11,
                                                      role="reference_open")
        self.assertEqual(rec["td"], 1024)
        self.assertEqual(rec["ns"], 1)
        self.assertAlmostEqual(rec["sw_hz"], 6900.0, places=6)
        self.assertAlmostEqual(rec["rg"], 20.0, places=9)
        self.assertEqual(rec["pulprog"], "single_pulse.jxp")
        # honesty markers must survive into the notes
        self.assertIn("UNVERIFIED", notes["recvr_gain_semantics"])
        self.assertIn("UNVERIFIED", notes["time_source"])

    def test_read_any_dispatch(self):
        jdf = jeol_reader.read_any(self.path("12_sn_noise.jdf"))
        jdx = jeol_reader.read_any(self.path("12_sn_noise.jdx"))
        self.assertEqual(jdf["info"]["format"], "jdf")
        self.assertEqual(jdx["info"]["format"], "jcamp-dx")

    def test_rejects_garbage(self):
        bad = os.path.join(self.tmp, "garbage.bin")
        with open(bad, "wb") as fh:
            fh.write(b"\x00" * 4096)
        with self.assertRaises(jeol_reader.JeolReadError):
            jeol_reader.read_jdf(bad)


REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PACKER = os.path.join(REPO, "packer", "pack_bundle.py")


@unittest.skipUnless(os.path.isfile(PACKER),
                     "packer/pack_bundle.py not present")
class TestPackerChain(unittest.TestCase):
    """Full chain: synthetic JEOL session -> packer --vendor jeol ->
    validated bundle zip (validation runs inside the packer via the
    uploader's verify_bundle). Run separately for the native-jdf and the
    JCAMP-DX fallback sessions."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="jeol_pack_")
        cls.answers_path = os.path.join(cls.tmp, "answers.json")
        answers = {
            "vendor": "jeol",
            "run_mode": "desktest",
            "facility": {
                "institution": "JEOL chain selftest (synthetic)",
                "city": "Nowhere", "country": "n/a",
                "facility_slug": "ci-jeol-selftest",
                "contact_email": "", "contact_consent": False,
            },
            "sample": {
                "description": "synthetic water (no sample exists)",
                "h2o_fraction_pct": 100, "d2o_pct": 0, "additives": "none",
                "tube_od_mm": 5, "sample_volume_ul": 550,
                "vt_setpoint_k": 298,
            },
            "environment": {"locked": False,
                            "operator_notes": "synthetic; never upload"},
            "spectrometer": {"probe_type": "unknown",
                             "console": "synthetic JEOL",
                             "probe_string": "synthetic"},
            "instrument": {
                "delta_version": "0.0-synthetic",
                "field_state_notes": "synthetic bundle; nothing swept",
            },
            "calibration": {"p90_us": 10.0, "topshim_ok": False},
            "experiments": [
                {"expno": 11, "role": "reference_open",
                 "o1_hz": 0.0, "rg": 20.0},
                {"expno": 12, "role": "noise", "o1_hz": 0.0, "rg": 60.0},
                {"expno": 17, "role": "noise", "o1_hz": 0.0, "rg": 60.0},
            ],
        }
        import json
        with open(cls.answers_path, "w") as fh:
            json.dump(answers, fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run_packer(self, data_dir):
        import subprocess
        out_dir = os.path.join(self.tmp, "bundles")
        proc = subprocess.run(
            [sys.executable, PACKER, data_dir,
             "--answers", self.answers_path, "--vendor", "jeol",
             "--out-dir", out_dir],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        self.assertEqual(
            proc.returncode, 0,
            "packer failed:\nSTDOUT:%s\nSTDERR:%s"
            % (proc.stdout, proc.stderr))
        bundle = proc.stdout.strip().splitlines()[-1]
        self.assertTrue(os.path.isfile(bundle))
        self.assertIn("validation: PASS", proc.stderr)
        return bundle, proc.stderr

    def test_pack_jdf_session(self):
        data_dir = os.path.join(self.tmp, "session_jdf")
        synth.build_session(data_dir, formats=("jdf",), npts=256)
        bundle, log = self._run_packer(data_dir)
        # the .jdf path must auto-discover rg and the Delta version guess
        import zipfile, json
        with zipfile.ZipFile(bundle) as zf:
            meta = json.loads(zf.read("meta.json").decode())
            names = zf.namelist()
        self.assertEqual(meta["vendor"], "jeol")
        self.assertEqual(meta["instrument"]["jeol"]["data_format"], "jdf")
        roles = [e["role"] for e in meta["experiments"]]
        self.assertEqual(roles, ["reference_open", "noise", "noise"])
        self.assertEqual([e["expno"] for e in meta["experiments"]],
                         [11, 12, 17])
        self.assertIn("data/12/12_sn_noise.jdf", names)
        # receiver gain must have been discovered from the .jdf itself
        self.assertEqual(meta["instrument"]["jeol"]["receiver_gain"], 60.0)

    def test_pack_jcamp_session(self):
        data_dir = os.path.join(self.tmp, "session_jcamp")
        synth.build_session(data_dir, formats=("jcamp",), npts=256)
        bundle, log = self._run_packer(data_dir)
        import zipfile, json
        with zipfile.ZipFile(bundle) as zf:
            meta = json.loads(zf.read("meta.json").decode())
            names = zf.namelist()
        self.assertEqual(meta["instrument"]["jeol"]["data_format"],
                         "jcamp-dx")
        self.assertIn("data/12/12_sn_noise.jdx", names)
        # extras/ must NOT be discovered as experiments
        self.assertEqual(len(meta["experiments"]), 3)


@unittest.skipUnless(os.environ.get("JEOL_REAL_DATA_DIR"),
                     "set JEOL_REAL_DATA_DIR to run against real .jdf files")
class TestRealFiles(unittest.TestCase):
    """Optional smoke test against real Delta files (not shipped)."""

    def test_parse_all(self):
        root = os.environ["JEOL_REAL_DATA_DIR"]
        parsed = 0
        for name in sorted(os.listdir(root)):
            if not name.lower().endswith(".jdf"):
                continue
            result = jeol_reader.read_jdf(os.path.join(root, name))
            self.assertTrue(result["info"]["nucleus"])
            self.assertTrue(result["data"])
            parsed += 1
        self.assertGreater(parsed, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
