# Contributing

Two kinds of contribution arrive here, through two different doors.

## Facility support (something went wrong at your spectrometer)

Open a GitHub issue with the **Facility support** template. It asks for exactly
the fields we need to help you — TopSpin version, console, probe, which step
failed, what SIMULATE mode does, whether a bundle zip was produced. Fill in
what you can; "don't know" is always an acceptable answer. Paste error text
verbatim rather than paraphrasing it.

## Code contributions

Pull requests are welcome. The most valuable kind is a **TopSpin-version
portability fix**: the run script targets the Jython embedded in TopSpin
2.x-4.x, every version behaves a little differently, and a fix for your
version helps every facility after you. (Even just an issue with the exact
error text is a real contribution.)

Before submitting a PR, run both local checks from the repo root:

```sh
python3 testing/static_check.py
python3 uploader/upload_bundle.py \
    "$(python3 testing/make_synthetic_bundle.py)" --selftest
```

The first must end `ALL CHECKS PASSED`, the second `RESULT: PASS`. CI runs
the same two checks on Python 3.8 and 3.12, plus a schema-v1.0 bundle for
backward compatibility.

Design constraints to respect (decisions, not oversights):

- `uploader/upload_bundle.py` is Python 3 **standard library only**, runnable
  on Python 3.6 — facility workstations have no pip and no internet.
- `topspin/spin_noise_run.py` is **Jython 2.x** (TopSpin-embedded); it can
  never be imported by CPython, which is why the checks are static.
- Every hardware command must stay behind the `safe_hw_cmd` /
  `run_zg_and_wait` guards — `static_check.py` enforces this.

## Scope note

This file governs **code**. Measurement data (bundles) are governed by
[DATA_POLICY.md](DATA_POLICY.md) — ownership, permitted uses, co-authorship,
embargo, and withdrawal.

Contact: John W. Blanchard <jwbquantum@gmail.com>
