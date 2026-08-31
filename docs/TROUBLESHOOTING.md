# Troubleshooting — by symptom

Indexed by **what you actually see**, not by subsystem. Every entry:
symptom → cause → fix. Distilled from a systematic failure-mode review
(2026-08-31) of every install surface; exact message texts are quoted
from the code so you can search this page for them.

**Start here, always:**

    python3 uploader/upload_bundle.py --doctor

The doctor checks Python, config.json, the schema, the network path,
TLS trust, and your system clock — no bundle needed, nothing uploaded
— and every FAIL line names its fix. Most "upload problems" are
diagnosed by this one command. (Windows: `py -3` instead of
`python3`, here and everywhere below.)

---

## 1. Installing / starting the run (Bruker TopSpin)

**`xpy spin_noise_run` says the script is unknown.**
The .py is not in TopSpin's user-python directory. It belongs in
`<TSHOME>/exp/stan/nmr/py/user/` — on TopSpin 4.x installs, TSHOME is
typically `/opt/topspin4.x.y`; on Windows, `C:\Bruker\TopSpin4.x.y`.
Re-copy, then retype the command (no `.py` extension).

**"Could not write the pulse program to: ..." during the first run.**
TopSpin was installed by IT as root/admin and `pp/user` is not
writable. Copy `topspin/pp/zgnoise2d` there by hand with elevated
rights, exactly as the dialog asks, then press OK.

**A TopSpin error names `zgnoise2d` right when the noise block starts,
then the script asks about a missing raw-data file.**
The pulse program is absent or did not compile (very old TopSpin
without `Avance.incl`: open the pp file and comment out the
`#include` line — the sequence uses none of its macros). Press
**Cancel** at the acquisition-check dialog, fix the pp, rerun. Never
press OK through that dialog: it would finish the session with no
noise data in the bundle.

**TopSpin pops its own errors about `atma` / `topshim` / `pulsecal`.**
Normal on consoles without an ATM unit or those licences: the script
detects the failure and degrades to an operator dialog asking you to
do that step by hand (wobb / your usual shim / your known P90). If
you prefer, tune, shim, and calibrate BEFORE starting the script —
the automatic steps are then harmless no-ops on top of a good state.

---

## 2. During / after the run (Bruker TopSpin)

**"When can I walk away?"**
After the **90-degree pulse confirmation** dialog — the last question.
The RG ladder and opening reference then run unattended (~15 min),
and the noise block auto-starts after a 30 s status-line countdown.
There is no "noise block starting" dialog (versions before 0.5.1 had
one — it stranded overnight runs when nobody was left to click it).

**Morning screen shows a "final notes" dialog and there is no zip
yet.** Normal and by design: the bundle is written AFTER you answer
the morning notes question. Answer it; the zip appears seconds later.

**The run crashed / TopSpin died / power failed mid-session.**
The acquired expnos are safe on disk under the
`SPINNOISE_<date>_<time>` dataset. Do not rerun into the same
dataset. Pack what exists from any machine:

    python3 packer/pack_bundle.py --vendor bruker <dataset_dir> \
        --answers answers.json

**Two `spinnoise_*.zip` files in the dataset directory.**
One is a desktest/rehearsal bundle. Check which is which:

    python3 - <<'EOF'
    import zipfile, json, sys
    m = json.loads(zipfile.ZipFile(sys.argv[1]).read("meta.json"))
    print((m.get("software") or {}).get("run_mode"))
    EOF
    (pass the zip path as the argument; want: "live")

Upload the `live` one. The uploader refuses test bundles anyway
("run_mode is 'desktest' -- a plumbing test"), so a mixup is caught.

---

## 3. Packing (JEOL / Magritek / Agilent / Nanalysis)

**"no Bruker experiments found under ... NOTE: this directory contains
what looks like agilent data -- did you mean --vendor agilent?"**
Exactly what it says: the packer defaults to `--vendor bruker`; pass
the right vendor flag.

**"does not look like a Spinsolve spin-noise session" (Magritek).**
You acquired with the normal Spinsolve interface, whose timestamped
folder names the packer cannot order. Copy each experiment folder
into a numeric directory (`1`, `2`, `3`, … in acquisition order) and
rerun — the message says the same.

**"SKIPPED N file(s) without the NN_ expno prefix".**
The packer found correctly named experiments AND strays. Strays are
never silently renumbered (that would relabel your whole session):
rename them per the Tier-1 checklist (`11_sn_ref_open.<ext>`) if they
belong, or ignore the warning if they don't.

**"every export in this session is a processed-SPECTRUM file"
(Nanalysis).** The NMReady touchscreen's default export is the
processed spectrum; the network needs the raw **FID** JCAMP-DX
export. Re-export every record as FID and repack.

**"answers file ... looks like a vendor macro/session file".**
Two answers.json dialects exist; the packer needs the NESTED one.
Start from `packer/answers.example.json` and fill in your values.

**"answers file ... is not valid JSON".**
Usually a Windows-editor injury. The loaders tolerate the Notepad
BOM automatically; if the message mentions curly "smart quotes",
recreate the file in a plain-text editor.

---

## 4. Uploading

Run `--doctor` first; then match the symptom:

**Four retries, then "upload failed after 4 attempts. The network or
server may be down".** The instrument subnet cannot reach the
internet (isolated network or institutional firewall) — the single
most common facility situation, and why the uploader is a standalone
file: copy the zip + `uploader/` + your `config.json` to any machine
on a normal network (USB stick is fine) and run the same command.
The zip is the complete record; nothing else is needed.

**"this machine cannot verify the server's TLS certificate ... check
the YEAR".** Not a network outage. Either the system clock is wrong
(dead CMOS battery on an old workstation — run `date`) or the OS
certificate store predates 2021. Fix the clock, or upload from a
current machine. Never disable certificate verification.

**HTTP 401 / 403.** The token in config.json is wrong, truncated, or
was rotated. Values are whitespace-stripped automatically, so a
newline from copy-paste is not the issue — ask the maintainer for a
fresh config.json.

**HTTP 409 Conflict.** A bundle with this exact filename already
exists server-side — almost always a re-upload of something that
already succeeded. If it is genuinely a different bundle, rename per
the printed instructions.

**"run_mode is 'desktest' -- a plumbing test".** You grabbed the
rehearsal zip; see section 2 for telling them apart.

**A big upload was interrupted.** Just rerun the identical command:
uploads over 50 MiB checkpoint after every part and resume where they
left off. A permanently stalled one can be abandoned with `--abort`
(the zip itself is never touched).

---

## 5. Validation (`--selftest`) failures

**"schema file not found".** You are running a loose copy of the
uploader outside the repository. Run from a full checkout, or pass
`--schema /path/to/meta.schema.json` — a selftest without the schema
would be an empty PASS, so it refuses.

**"meta.json fails schema validation: $.sample.vt_setpoint_k: 0.0
must be > 0"** (or another single bad field). Recoverable without
re-acquiring: fix the value in the dataset directory's `meta.json`,
re-zip, and validate again — `meta.json` is not covered by the data
checksums, so the measurement stays intact. (Current scripts re-ask
when a temperature looks like Celsius; bundles from ≤0.5.0 could
carry this.)

**"zip CRC check failed" / "not a readable zip file".** The copy from
the spectrometer was interrupted. Re-copy the original zip; never
re-zip a partially extracted tree.

---

## 6. Report flags that look like failures (but are diagnoses)

- **"BSMS field sweep unconfirmed"** — the operator answered "cannot
  verify". The data may be fine; the flag exists because a sweeping
  field produced weeks of silently void data in 2022. Next run: check
  `bsmsdisp` AFTER turning the lock off (unlocking re-enables the
  sweep on many consoles), then answer.
- **"lock recorded ON during the noise block"** — permitted and
  recorded; on some systems lock RF leaks into the ¹H channel, hence
  the caution.
- **Clock audit "inconclusive (short session)"** — normal for runs
  under an hour; it is a statement about audit precision, not a fault.
- **Per-block "expected source: script-recorded (pulse program ...)"**
  — the analysis could not model a pulse program's timing with
  certainty and fell back conservatively. Informational.

---

## 7. Sending a failure report that gets you a fast answer

Include: the exact command, its FULL output (copy-paste, not a
summary), `python3 --version`, your OS, vendor + console + software
version, and for packing problems one `procpar`/`acqu.par`/JCAMP
header (plain-text parameter files, no secrets). For a binary-format
mystery, add the first 64 bytes of the data file:

    python3 -c "print(open('fid','rb').read(64).hex())"

Maintainer contact: `CITATION.cff`. A precise failure report from a
real console is one of the most valuable contributions a facility can
make — the AI-agent runbook (`docs/CLAUDE_INSTALL.md`, Step 8) turns
producing one into a checklist.
