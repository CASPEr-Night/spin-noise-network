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

**`ValueError: invalid literal for float:` right after the optional
probe-temperature dialog (script v0.7.1 or earlier).**
Leaving the coil/preamp temperature fields blank, as that dialog itself
suggests, crashed the script on a real console (TopSpin 4.4.0,
2026-09-17) because TopSpin's Jython namespace shadows the name
`Exception`, so the script's catch-alls never caught the empty string.
Fixed in v0.7.2 — every catch-all now names both the Python and the Java
exception classes, and the script proves at start-up that its handlers
catch a Python exception on the console it is running on. Workaround on
an older script: type the physical temperatures instead of leaving the
fields blank (on a room-temperature probe both are the lab ambient, e.g.
`298`), which is also the value the analysis wants.

**`spin_noise_run: cannot run on this console` before the first dialog
(v0.7.2 or later).**
The start-up self-test above failed: this console's Jython resolves the
exception classes in a way the script does not handle, and continuing
would reproduce the crash above at the first blank field. Nothing was
started. Send us the message text and the TopSpin version; there is no
operator workaround.

**TopSpin shows `Could not convert '1' into enum: GetEnuOrd[PARMODE]:
enumeration name 1 not found`, then the script asks you to type `parmode`
(script v0.7.2 or earlier).**
TopSpin validates enumerated parameters written from a script by name
(`2D`); the old script wrote the ordinal (`1`), which TopSpin 4.4.0
rejected (first seen at Torino, 2026-09-18) and which no TopSpin version
is documented to accept. The script's request is correct and comes
once, at the opening reference (~15 min into the unattended stretch):
type `parmode` in TopSpin (or use the `eda` toolbar button *Change data
dimensionality*), select 2D, press OK; the later datasets inherit 2D.
What recurs on v0.7.2 is TopSpin's OWN error pop-up about `PARMODE`
(and the `1 FnMODE` one below) at every pseudo-2D dataset — three of
each per session, two of them after you have walked away — and such a
pop-up may hold the script until it is closed, with the acquired data
safe on disk. Fixed in v0.7.3: PARMODE is written by its documented
name, the dimensionality and the number of rows are verified by
readback, the dataset is reloaded after the switch, and the FIRST switch
happens while you are certainly at the console — at the start of the
setup step, before tuning — so if your console does need `parmode` by
hand, you do it once, there, and the script tells you whether anything
will be asked again. What the console accepted is recorded in
`meta.json` (`software.param_api`), and a form the console rejected is
never tried again in that session. Install v0.7.3 before a live run.

**`1 FnMODE: parameter not found in map` (script v0.7.2 or earlier).**
Cosmetic. The script tried to set the F1 acquisition mode to QF as a
courtesy to TopSpin's own 2D processing. Nothing depends on it: the
noise pulse program is a plain loop without an `mc` statement, for which
Bruker's acquisition reference requires FnMODE to stay `undefined`, and
the analysis reads the raw `ser` file directly. Close the message; the
run continues correctly. v0.7.3 no longer touches FnMODE at all. If you
want to look at the rows with `xf2` in TopSpin, set the processing
parameter MC2 to QF — that does not affect acquisition.

**TopSpin shows `The requested format file is invalid:
.../SPINNOISE_<date>/<expno>/acqu2: getpar: No such file or directory`
(script v0.7.4 or earlier; TopSpin 3.7.0, Avance III HD, Oulu desktest
2026-09-25 — and TopSpin 4.4.0, Avance Neo, Torino's first live run
2026-09-22 with v0.7.3; both with a 1D template).**
TopSpin keeps each dimension's acquisition parameters in its own file
(`acqus` for the direct dimension, `acqu2` for F1). This console accepted
the script's switch of the dataset to 2D (`PARMODE`) but did **not**
create `acqu2` — your template dataset was 1D, so there was nothing to
inherit. A dataset that says 2D without `acqu2` is broken at TopSpin's
parameter layer: this dialog appeared once per run, naming expno 12
(what exactly triggers it — an F1 parameter read, the pulse-program
change on expno 12, or displaying a 2D dataset without the file — is not
established), and
**every parameter write into that dataset is silently lost** — no error,
nothing in the script's own failure count. First seen at Oulu
(2026-09-25, v0.7.4 simulate and desktest): the bundle's `meta.json`
showed the three pseudo-2D blocks (expnos 11, 12, 13) with `td` 16384,
`rg` 1.0 and the noise block's `pulprog` as `zg2d` — the values inherited
from the 1D setup expno — although the script had written 262144, the
rga result and `zgnoise2d`; `software.param_api.f1_td_verified` was 0
with an empty `f1_td_readback_source`, and no `acqu2` appeared in the
`checksums`. A **live** run on this console with v0.7.4 would have
acquired pulsed 1.2 s rows in place of the pulse-free 19 s noise rows,
with an unknown row count, under a `meta.json` declaring 89 pulse-free
rows (the report flags exactly that mismatch, `rows declared vs read`,
from the data file itself). **The same on TopSpin 4.4.0** (Torino,
Avance Neo 400, first live attempt 2026-09-22 with v0.7.3 and a 1D
template; Torino's earlier desktests had a 2D dataset open and never met
it): `PARMODE` 2D accepted and logged in the audit trail, no `acqu2`
created; the F1 write `1 TD` itself popped this dialog (naming expno 11,
the probe) while no exception reached the script (`putpar_failures` 0);
the audit trail shows nothing written after the `PARMODE` change
although the script wrote TD, RG and the pulse program — the writes were
lost on disk, not merely read back stale — and `zg` refused every
pseudo-2D block with `Cannot run single acquisition: inconsistent
PARMODE 2D: Parameter set acqu2: ... Unable to open file .../11/acqu2`,
leaving no `ser`. If that happens, answer the script's **acquisition
check** dialog ("cannot see a raw-data file") with **Cancel**: the run
cannot record anything useful and OK only carries the fault into the
next block (Torino's bundle declares 8/89/8 rows with no raw data behind
them). Do not run live on such a console before installing v0.7.5.
Fixed in
v0.7.5: right after the first 2D switch (at the attended probe, before
tuning) the script checks for `acqu2` and, when the console did not
create it, copies it from your template dataset if that is 2D, else from
another expno of the session, else from the console's own standard
parameter library (`<TSHOME>/exp/stan/nmr/par/COSYGPSW/acqu2` or the
first 2D set found there), sets `FnMODE` to `0` (*undefined*) in the
copy by editing the file — Bruker's rule is that *undefined* must be used
when the pulse program has no `mc` statement, which is true of `zg2d` and
`zgnoise2d`; the library set carried the mode of its own experiment; the
script still never writes `FnMODE` through the parameter API, the v0.7.3
rule — then reloads the dataset; the later pseudo-2D datasets inherit the
file. An `acqu2` a pseudo-2D expno *inherited* instead — from your 2D
template or from a sibling expno — gets the same edit when it says
anything but `0` (one reload per file edited, none otherwise); your
template dataset itself is never touched. Only the parameter files
(`acqu2`, `proc2`) are copied, never the
status files (`acqu2s`, `proc2s`: they describe acquired data, `zg`
writes them). F1 parameters are never read while the file is absent,
and the file exists from the attended probe onward, so if this dialog
appears at all with v0.7.5 it can only be once, at the probe, right after
the first switch — please tell us if it does. TD and RG are read back
after every write — a write that still does not take is counted and
named in `meta.json`. What to check in a v0.7.5 `meta.json`
from such a console: `software.param_api.f1_files_created` = 1 and
`f1_files_source` = `par:COSYGPSW` (or `template` / `expno:<n>`),
`f1_fnmode_edits` = 1 (the one copy) with `f1_fnmode_copied` = the value
the library set carried (e.g. `6`; the copy itself reads `0`) — with a
2D template open instead, `f1_files_created` = 0 and `f1_fnmode_edits`
= 1 counts the inherited file, `f1_fnmode_copied` its original mode —
`f1_td_verified` = 1, `acq_write_mismatch` = 0,
`td` = 262144 for expnos 11/12/13, expno 12's `pulprog` = `zgnoise2d`,
and `data/11/acqu2`, `data/12/acqu2`, `data/13/acqu2` present in
`checksums`. If `f1_files_source` reads `operator`, the script found
nothing to copy and asked you to type `parmode` in TopSpin (or use the
`eda` toolbar button *Change data dimensionality*) and select 2D — once,
at the attended probe; it never asks again during the unattended part —
send us the bundle and the TopSpin version. The terminal log prints what
the copy reads (`the copy reads TD=... FnMODE=0 (the source set carried
FnMODE=...)`) — please include that line in your report if `zg` complains
about the noise block.

**The RG ladder rungs take minutes instead of seconds, or the ladder
expnos hold `ser` files (script v0.7.3 or earlier).**
The dataset open when you typed `xpy spin_noise_run` was a 2D one. The
script uses that dataset as the parameter template, and every dataset it
creates by copying inherits the template's dimensionality — the setup
expno and the four gain-ladder rungs included, which then acquire the
template's full row count with `zg`. Torino's first v0.7.3 desktest bundle
(2026-09-21) showed exactly this inheritance (no harm done in a desk test).
Fixed in v0.7.4: the setup expno and the rungs are switched to 1D by the
script whatever the template was, and the pseudo-2D datasets to 2D. On
an older script, open a 1D ¹H dataset before starting.

**`program_version` in meta.json reads `<function PROGRAM_VERSION at 0x3>`
(script v0.7.3 or earlier on a real console).**
Cosmetic. TopSpin's Python API exports a function of that name, and the
script's own constant was overwritten when the API was imported. The
value the pipeline uses is `software.script_version`, which is correct;
the report notes the collision. Fixed in v0.7.4.

**TopSpin pops its own errors about `atma` / `topshim` / `pulsecal`.**
Normal on consoles without an ATM unit or those licences: the script
detects the failure and degrades to an operator dialog asking you to
do that step by hand (wobb / your usual shim / your known P90). One of
them is expected on every console: `topshim` refuses to run while the
lock is off (`getLockAndSweepStatus - lock is off: please lock in prior
to shimming` — Torino, 2026-09-22) and this protocol keeps the lock off.
So the intended sequence is: **lock, shim, tune** as you usually do
(lock on), then **lock OFF and BSMS field sweep OFF**, then start the
script — and press OK at its shimming dialog, which says so since
v0.7.5. Tuning and P90 calibration done beforehand are likewise harmless
no-ops on top of a good state.

---

## 2. During / after the run (Bruker TopSpin)

**"When can I walk away?"**
After the **90-degree pulse confirmation** dialog — the last question.
(Since v0.7.3 the one step a TopSpin 4 console may need your help with —
switching the first dataset to 2D — happens at the start of the setup
step, before tuning, and the script tells you then if anything will be
asked again.)
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
