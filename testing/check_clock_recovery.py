#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_clock_recovery.py -- assert that a facility report's clock-audit fit
recovered a KNOWN injected fractional console-clock offset.

    python3 testing/check_clock_recovery.py <report.json> --injected 3e-7 \
        --within-nsigma 1 --require-conclusive
    python3 testing/check_clock_recovery.py <report.json> --injected 1e-3 \
        --within-nsigma 3 --detect-nsigma 5

Reads the report.json written by analysis/facility_report.py and checks its
clock_audit object. Two distinct test roles (see run_jython_harness.sh):

  * REALISM check (--injected 3e-7 --within-nsigma 1 --require-conclusive):
    the Jython-harness session carries a realistic aged-OCXO offset. Its
    ~1 h span cannot RESOLVE 3e-7 (fit precision is a few 1e-6), so the
    assertion is coverage -- the fit must land within its stated 1-sigma
    uncertainty of the truth, and the audit must be marked conclusive.
  * POWERED check (--injected 1e-3 --within-nsigma 3 --detect-nsigma 5):
    an offset ~9 sigma above the fit precision. Coverage alone would pass
    a broken fit that always returns 0, so this case additionally demands
    a >= 5-sigma DETECTION of the nonzero offset: |fit| > 5*err. The
    zero-offset null (--injected 0 --within-nsigma 3) closes the other
    side: a fit biased away from zero fails it.
  * DE-DISCRIMINATION check (--injected 0 --expect-refined 7
    --recorded-model-biased-nsigma 5, on a bundle built with a large
    --de-us): the headline (acqus-refined) fit must recover the truth
    while the recorded-model comparison fit must sit many sigma AWAY
    from it -- proving the DE refinement engaged and did real work,
    not just that both fits agree. --expect-refined alone asserts the
    refinement engaged on at least N usable blocks.

Exit 0 iff every requested assertion holds. Prints the fitted numbers
either way.
"""

from __future__ import print_function

import argparse
import json
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="assert clock-audit recovery of an injected offset")
    ap.add_argument("report_json", help="path to a facility report.json")
    ap.add_argument("--injected", type=float, required=True,
                    help="the KNOWN injected fractional clock offset")
    ap.add_argument("--within-nsigma", type=float, default=1.0,
                    help="require |fit - injected| <= N * fit_err (default 1)")
    ap.add_argument("--detect-nsigma", type=float, default=None,
                    help="additionally require |fit| > N * fit_err "
                         "(powered nonzero-offset case only)")
    ap.add_argument("--require-conclusive", action="store_true",
                    help="require the audit's conclusive flag (session span "
                         "past the report's minimum)")
    ap.add_argument("--expect-refined", type=int, default=None,
                    help="require the acqus-DE expectation refinement to "
                         "have engaged on at least N usable blocks")
    ap.add_argument("--recorded-model-biased-nsigma", type=float,
                    default=None,
                    help="require the recorded-model comparison fit to sit "
                         "MORE than N of its sigma away from the injected "
                         "offset (proves the DE refinement did real work)")
    args = ap.parse_args(argv)

    with open(args.report_json, "r") as fh:
        report = json.load(fh)
    ca = report.get("clock_audit")

    failures = []

    def check(name, ok, detail=""):
        tag = "PASS" if ok else "FAIL"
        line = "%s : %s" % (tag, name)
        if detail and not ok:
            line += "\n       %s" % detail
        print(line)
        if not ok:
            failures.append(name)

    check("report.json carries a clock_audit object", isinstance(ca, dict))
    if not isinstance(ca, dict):
        print("CLOCK RECOVERY: FAIL")
        return 1
    check("clock_audit available", ca.get("available") is True,
          "note: %s" % ca.get("note"))
    if args.require_conclusive:
        check("clock_audit conclusive (session span long enough)",
              ca.get("conclusive") is True,
              "status: %s" % ca.get("status"))

    fit = ca.get("fractional_offset")
    err = ca.get("fractional_offset_err")
    check("fit produced fractional_offset and fractional_offset_err",
          isinstance(fit, (int, float)) and isinstance(err, (int, float))
          and err > 0)
    if not (isinstance(fit, (int, float)) and isinstance(err, (int, float))
            and err > 0):
        print("CLOCK RECOVERY: FAIL")
        return 1

    print("       injected %.6e | fitted %.6e +/- %.3e | pull %+.2f sigma"
          % (args.injected, fit, err, (fit - args.injected) / err))

    check("recovery: |fit - injected| <= %.3g * err"
          % args.within_nsigma,
          abs(fit - args.injected) <= args.within_nsigma * err,
          "|%.3e - %.3e| = %.3e > %.3e"
          % (fit, args.injected, abs(fit - args.injected),
             args.within_nsigma * err))
    if args.detect_nsigma is not None:
        check("detection: |fit| > %.3g * err (nonzero offset resolved)"
              % args.detect_nsigma,
              abs(fit) > args.detect_nsigma * err,
              "|%.3e| <= %.3e" % (fit, args.detect_nsigma * err))

    er = ca.get("expectation_refinement") or {}
    rm = ca.get("recorded_model") or {}
    if er.get("n_refined") is not None:
        med = er.get("median_fractional_correction")
        print("       refinement: %d refined / %d recorded-only%s"
              % (er.get("n_refined", 0), er.get("n_recorded_only", 0),
                 (" | median DE correction %+.3e" % med)
                 if med is not None else ""))
    if rm.get("fractional_offset") is not None:
        print("       recorded-model fit %.6e +/- %.3e"
              % (rm["fractional_offset"], rm["fractional_offset_err"]))
    if args.expect_refined is not None:
        check("refinement engaged on >= %d usable blocks"
              % args.expect_refined,
              int(er.get("n_refined") or 0) >= args.expect_refined,
              "n_refined = %s" % er.get("n_refined"))
    if args.recorded_model_biased_nsigma is not None:
        rfit = rm.get("fractional_offset")
        rerr = rm.get("fractional_offset_err")
        ok = (isinstance(rfit, (int, float))
              and isinstance(rerr, (int, float)) and rerr > 0
              and abs(rfit - args.injected)
              > args.recorded_model_biased_nsigma * rerr)
        check("recorded-model fit biased > %.3g sigma from the injected "
              "offset (DE refinement did real work)"
              % args.recorded_model_biased_nsigma, ok,
              "recorded-model %s +/- %s vs injected %.3e"
              % (rfit, rerr, args.injected))

    if failures:
        print("CLOCK RECOVERY: FAIL (%d check(s))" % len(failures))
        return 1
    print("CLOCK RECOVERY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
