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

**"Could not write the pulse program(s) ... to: ..." during the first
run** (scripts before v0.7.6: "Could not write the pulse program to:").
TopSpin was installed by IT as root/admin and `pp/user` is not
writable. Copy `topspin/pp/zgnoise2d` **and** `topspin/pp/zgref2d` there
by hand with elevated rights, exactly as the dialog asks, then press OK.
Since v0.7.6 the script installs two pulse programs: `zgnoise2d` for the
noise block and `zgref2d` for the two reference blocks.

**A TopSpin error names `zgnoise2d` right when the noise block starts
(or `zgref2d` when a reference block starts, v0.7.6 or later), then the
script asks about a missing raw-data file.**
The pulse program is absent or did not compile (very old TopSpin
without `Avance.incl`: open the pp file and comment out the
`#include` line — neither sequence uses any of its macros). Press
**Cancel** at the acquisition-check dialog, fix the pp, rerun. Never
press OK through that dialog: it would finish the session with no
noise (or no reference) data in the bundle. A message about a
*negative duration* in `zg2d` is a different fault — see section 2.

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
when the pulse program has no `mc` statement, which is true of the
reference program (`zg2d` then, `zgref2d` since v0.7.6) and of
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

**TopSpin shows `TCube: Cannot interpret pulse program: Cannot load line:
duration is negative (-21166512.000000 us) In 'zg2d': line 24` (then the
same for `FCube2`, then `CubeManager: Cannot run experiment ... TCube: is
not ready`) as the opening reference (expno 11) starts, and the script
asks about a missing raw-data file (script v0.7.5 or earlier).**
Bruker's library `zg2d` (avance-version 12/01/11), which the older
script used for the two small-flip reference blocks (expnos 11 and 13),
paces successive rows with a computed delay,
`"DELTA=d20-((d1+aq)*(ns+ds))-30m"` — `d20` being, in Bruker's own
comment, the "delay between start of different 1D spectra". The script
never sets `D20` (it is 0 in every parameter set it starts from), so
with D1 = 2 s and AQ = 19.137 s the delay is 0 − (2 + 19.137) − 0.03 =
−21.167 s — the value in the message — and the console refuses to
compile the sequence; no `ser` is written. First met at Torino (Avance
Neo 400, TopSpin 4.4.0, 2026-09-25, the first live run of v0.7.5): the
noise block (expno 12, `zgnoise2d`) acquired normally — 89 rows, and
the report resolves the water spin-noise dip on it — but both reference
blocks failed this way; the operator answered the script's acquisition
check with OK, so the session completed and packed a valid bundle
without reference data, and the report has no transduction constant
and no exclusion curve. The same `zg2d` ships with every TopSpin
2.x–4.x, so every console will do this. **Fixed in v0.7.6:** the
references use the project's own `zgref2d` — Bruker's `zg2d` without the
`DELTA` line and the `d20` dependence: one `d1` per row, the pulse, the
acquisition, 30 ms before the row is written; nothing is computed, so
nothing can come out negative — installed into `pp/user` next to
`zgnoise2d` by the script (or copied by hand from `topspin/pp/`).
Install v0.7.6 before a live run. **Workaround only**, for a run that
must go ahead on an older script: give `D20` a value of at least
`d1 + aq + 0.03 s` — 21.2 s with the default parameters (AQ is
19.0–19.2 s depending on how the console rounds SWH) — in the template
dataset *before* typing `xpy spin_noise_run` (`d20 21.2` in the TopSpin
command line with the template open; every dataset the script creates
copies it, and `zg` ignores it). Each reference row then lasts exactly
`d20`, so the clock audit's recorded expectation for those two blocks
(`aq + d1` per row on v0.7.5) is ~1% short at 21.2 s and ~5% short at
22 s — the report's wall/OCXO gate drops a block off by more than 5%.
The workaround is not a substitute for the update.

**TopSpin shows `Exception in DRUCONTR 1: Your pulse program produces
too much data for the LAN capacity. ->Experiment aborted by DRU1!` as a
reference or noise block runs, the block ends after ~30-40 s, and the
session runs on to a bundle (script v0.7.6 or earlier).**
Seen at Oulu (University of Oulu, Avance III HD 500, AQS DRU-E
Z102520/04001, TopSpin 3.7.0, 2026-09-30, the first live run there) for
all three pseudo-2D blocks -- expnos 11, 12 and 13 -- while everything
before them worked: the F1 parameter file, the TD/RG readbacks, both
pulse programs compiled, and the four 1D rungs (TD 16384, 64 kB each)
acquired normally. **What the bundle establishes:** each block acquired
its **first row completely** (non-zero samples from the group delay to
the end of the 262144-point row) and was then aborted by the receiver
unit **12-18 s into the second row** (39.9 s, 32.4 s and 34.7 s of wall
time against 168 s, 1700 s and 168 s expected); every later row is **all
zeros**, and the status file `acqu2s` says `TD= 1`. TopSpin 3.x
pre-allocates the whole `ser` when `zg` starts (8,388,608 bytes for 8
rows, 93,323,264 for 89), so a file of exactly the right size exists
although nothing beyond the first row was acquired -- and the v0.7.6
script, which checked only that a raw-data file existed, took each block
for done, asked nothing, and packed and uploaded a bundle of zeros.
**What explains it, in the order of the evidence.** (1) The
**acquisition mode** -- the explanation, **confirmed on the hardware on
2026-10-02** (the manual check below). Every Oulu acquisition
ran `DIGMOD` 3 (`baseopt`) with `DSPFIRM` 4 (`rectangle`), carried over
from the operator's parameter set; Torino's Avance Neo, which acquired
the same `zgnoise2d` rows (262144 points, 89 of them) without complaint
on 2026-09-25, ran `DIGMOD` 1 (`digital`) / `DSPFIRM` 0 (`sharp`). Bruker
documents that `baseopt` "needs some more internal memory ... for larger
TD, the memory on the DRU (RCU) may be a limiting factor" (TopSpin 3
Acquisition Reference, `DSPFIRM`) and that with `baseopt` "16 times more
data points are internally processed" (TopSpin 4.2 Acquisition
Reference); the DRU's direct scan memory is 4M samples -- 16 x 262144
exactly -- and its sustained LAN rate 45 Mbit/s (AQS technical manuals).
A 16-fold internal data volume explains everything seen: the 64 kB rungs
pass, both pseudo-2D pulse programs fail the same way, Torino passes.
(2) The **write window** -- the reading v0.7.7 was first built on, and
the weakest: each row was handed to the workstation in the 50 ms
(`zgnoise2d`, `d1 wr`) or 30 ms (`zgref2d`, `30m wr`) between `go` and
`wr`, and 1 MB in 50 ms asks for 20 MB/s where the rungs proved 2.2 MB/s.
But the abort came in the middle of the second row, not at its write,
and `zgref2d` already had 2 s of `d1` between its rows. (3) Something
else in the DRU firmware path -- excluded by the manual check of
2026-10-02 (below): the same row, the same pulse program, the same
console, `digital` instead of `baseopt`, acquired.
**Fixed in v0.7.7**, in four parts, ordered the same way. (1) The script
sets **`DIGMOD` `digital` and `DSPFIRM` `sharp`** on every experiment it
acquires with (the setup expno, the rungs, the row probe, the
references, the noise block, the sweep), by the documented enum names --
as it writes `PARMODE` -- and reads both back (`GETPAR` returns the
ordinal on 3.x/4.x: `1` and `0`); since v0.7.8 it reads both FIRST and
writes only what the readback says is missing -- one `DIGMOD` write per
session, no `DSPFIRM` write on a console that couples the two (see the
`GetEnuOrd[DSPFIRM]` entry below). Digital mode is also what a pulse-free
record wants: no baseline optimisation of noise, and `DE` returns to its
plain value (Oulu ran `DE` 13.55 us in `baseopt`, Torino 6.5 us in
`digital`). A console that rejects the names, or whose readback keeps
another mode, is recorded in `meta.json` (`software.param_api.
digmod_form`, `digmod_readback`, `dspfirm_readback`, `digmod_mismatch`,
`failed_forms`) and never insisted on. (2) Both pulse programs write
their row during a dedicated data-transfer delay, `d11 wr #0 if #0 ze`
(`D11` = 1 s by default, set and read back with `TD` and `RG`) --
**insurance**: it costs 1 s per row and rules the write window out for
good. (3) The **row probe** at expno 17, attended, right after the RG
ladder -- the **safety net**: a short `zgnoise2d` pseudo-2D at RG 1 is
acquired with each setting of the ladder (TD 262144 with `d11` 1 s --
the default geometry, in digital mode -- then `d11` 3 s, then TD 131072,
65536, 32768 with 1 s) until the **last row comes back with non-zero
data** -- by content, never by file size; two rows at least, because the
first row always arrived at Oulu -- and that setting is the row geometry
of every pseudo-2D block of the session, block durations kept (rows
recomputed). It runs whether or not the console took the `DIGMOD` write.
If no setting passes, the script says a WARNING on the status line,
records it, and continues with the default so the session completes and
the report shows the failure. (4) After **every** pseudo-2D block the
same content check runs on its first and last row; a reference block
without data is a WARNING, the noise block gets one retry with the next
ladder setting and then a WARNING; nothing after the row probe is a
dialog. `meta.json` records all of it under `calibration.row_probe`
(`attempts`, `passed`, `attempt_passed`, `td_row`, `transfer_delay_s`,
`blocks` with `data_first` / `data_last` / `acquired` per block,
`block_retries`) and lists the blocks whose last row held no data in
`software.param_api.blocks_without_data`; the probe's expno 17 travels
in the bundle with role `row_probe`. The report (v0.7.7) reads the files
themselves as well: a row that is all zeros is dropped and counted
(`raw_data_read.by_expno.<n>.n_rows_zero`), a block with no data row is
EXCLUDED, and every block with zero rows is a **FAIL** `raw data all
zeros` naming it; `rows declared vs read` says how many rows with data
remain -- for a desktest bundle too (`report.json` `raw_data_check`).
**The manual check on the old dataset** -- done at Oulu on 2026-10-02,
and it settled it. The operator opened expno 12 of
`SPINNOISE_20260930_1201` (the refused noise block: `zgnoise2d` as
installed by v0.7.6, TD 262144, its `d1 wr` line intact), typed `1 td`
and set it to `1` (one row: the check costs 19 s and cannot fill the
disk), typed `digmod` and chose `digital` (TopSpin then sets `DSPFIRM`
to `sharp` by itself; `dspfirm` shows it), then `zg` -- and **the row
that the receiver unit had refused in `baseopt` two days earlier
acquired cleanly at the first try**, without the `DRUCONTR` message.
Same console, same pulse program, same row length, same 50 ms write
window: the acquisition mode was the cause; the write window was not.
The recipe stands for any other facility that meets the message on a
pre-v0.7.7 dataset (if the console still refuses the row in `digital`,
also type `d1 1` and `zg` once more, then send TopSpin's error text);
do not run it on a v0.7.7+ session's dataset -- the script has done it.
**What to look for in a v0.7.8 `meta.json`** from a console like Oulu's:
`software.param_api.digmod_form` `name` (the mode switch took at the
setup expno; `already` if the template was `digital` to begin with) with
`dspfirm_form` `coupled` (the console moved `DSPFIRM` to `sharp` by
itself when `DIGMOD` changed, as Oulu's 3.7.0 did -- `sharp(standard)`
or `sharp` would mean the console did not couple and that name of the
script's ladder took) and `digmod_readback` `1` / `dspfirm_readback`
`0`; `calibration.row_probe.attempt_passed` `1` (the default row
accepted in digital mode) -- or which attempt it took; `td_row` /
`transfer_delay_s` (the geometry the blocks used -- a shorter row is
recorded, not a fault); `blocks[].acquired` true for 11/12/13;
`blocks_without_data` empty. The 12-18 s the DRU ran into the second row
before aborting is what makes a two-row probe conclusive at the default
19 s row; at shorter rows the probe acquires more rows so that at least
40 s of acquisition follow the first row. Please send the bundle:
`digmod_form` / `dspfirm_form` together with the `attempts` list say
what the console kept and what it needed.

**TopSpin shows `GetEnuOrd[DSPFIRM]: enumeration name sharp not found`
-- several times over a session, one dialog per experiment the script
acquires with (script v0.7.7).**
Seen at Oulu (Avance III HD 500, TopSpin 3.7.0, 2026-10-02) during the
v0.7.7 desktest: one dialog each for the setup expno, the four rungs,
the row probe and the three pseudo-2D blocks, while the run went on to
a bundle whose `meta.json` says `putpar_failures` `0`, `failed_forms`
`[]`, `digmod_form` `name`, `digmod_readback` `1`, `dspfirm_readback`
`0`. **Meaning:** v0.7.7 wrote `DIGMOD` `digital` and then `DSPFIRM`
`sharp` on every experiment. The console accepted the first and, as
Bruker documents (`rectangle` selects `baseopt` and vice versa), moved
`DSPFIRM` to `sharp` by itself. The second write named the enum the way
Bruker's Acquisition Reference spells it, `sharp`, which this console's
enum table does not contain (TopSpin 3.x's parameter editor spells it
`sharp(standard)`), so the console answered with its own dialog and
dropped the write -- without raising into Jython. The script therefore
counted no failure, marked nothing as failed, and repeated the write on
the next expno. **Harmless:** every dataset was acquired in `digital` /
`sharp` (the readbacks say so and the data agree), the dialogs are not
modal, the run completes. Close them. **Fixed in v0.7.8:** the script
reads `DIGMOD` and `DSPFIRM` before writing anything. From the second
expno on `WR` has copied the mode, so nothing is written; at the setup
expno it writes `DIGMOD` `digital` only, reads both back, and writes
`DSPFIRM` only if the console did not couple -- then `sharp(standard)`
first and `sharp` second, each at most once per session, each judged by
its readback (a name that does not take goes to `failed_forms` as
`DSPFIRM:<name>` and is never tried again). On Oulu's console that is
one `DIGMOD` write per session and no dialog.
`software.param_api.dspfirm_form` records how `DSPFIRM` came to read
`sharp` (`coupled` on a console like Oulu's). The rule behind the fix,
learned since v0.7.4's `acqu2` dialog: **a console dialog is not a
Python exception, so the script decides by readback, never by the
absence of an exception.**

**TopSpin shows `rga: acqt0 not set in pulse program, result may be
incorrect!` as the noise block's receiver-gain optimisation runs (script
v0.7.6 or earlier).**
Harmless. `rga` derives the FID's time origin from the pulse program's
`acqt0` definition to judge the first points, and the v0.7.6 `zgnoise2d`
defined none. Since v0.7.7 it carries `"acqt0=0"` before `1 ze` --
Bruker's own idiom for a pulse-free acquisition (the library programs
`cp`, `zgesgppe`, `cosyetgp` and `hmqcet` carry the same line): the time
origin is the start of the acquisition, there being no pulse to measure
it from -- so the warning no longer appears. On the older script the
gain `rga` returned was still the one that keeps the pulse-free rows
clear of the ADC's full scale (203 at Oulu, the console's maximum,
2026-09-30), which is what the noise block needs; the report's ADC check
verifies the outcome from the data. Close the message if it is modal;
the run continues. (`zgref2d`, which pulses, carries the same `acqt0`
line as Bruker's `zg2d`.)

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
- **"raw data all zeros" (FAIL) and "rows declared vs read ... are all
  zeros" (WARN)** — the block's `ser` has the declared size but its later
  rows were never written: TopSpin 3.x pre-allocates the file when `zg`
  starts and the receiver unit aborted the block after its first row
  (section 2, `DRUCONTR`; the acquisition mode `baseopt` is the cause,
  confirmed at Oulu on 2026-10-02). The rows with data were analysed on
  their own and the numbers rest on them alone; a block without a single
  data row is EXCLUDED. Acquire again with v0.7.8, which sets `DIGMOD`
  `digital` and
  whose row probe finds the row geometry the console accepts. A desktest
  bundle gets the same lines in `report.json` under `raw_data_check`.
- **"row probe (acquisition script)" WARN** — the console needed a row
  setting other than the default (a longer transfer delay or a shorter
  row); `calibration.row_probe` records which. The blocks' rows were
  resized to keep their durations. A console property, recorded, not a
  fault of the session.

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
