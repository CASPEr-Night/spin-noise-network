# Bench checklist: validating programmatic lock-shift actuation

One supervised hour on a partner console. Outcome: either the sweep's
"autostep" tier (unattended field laddering) is validated for that
console generation, or we know precisely which link fails. Research
provenance: three-lens documentation/wild-code review plus a MAS-DNP
survey, 2026-09-03 (sources in the section notes; extracted manual
texts retained by the maintainer).

Nothing here pulses or touches the sample. Every step is reversible
and the operator watches the lock display throughout. Do this OUTSIDE
network sessions, on a lockable standard sample (e.g. 90/10 H2O/D2O).

## A. Read the shipped low-level example (5 min, no hardware)

    edau bsms_exam        (also: bsms_getlock)

- It ships with every TopSpin under exp/stan/nmr/au/src.exam (or
  prog/au). It is Bruker's own worked example of low-level BSMS
  parameter reads/writes ("bsmscmd").
- Record: which BSN_* parameters it demonstrates; whether FIELD or
  LOCK_SHIFT appear; the include it uses (inc/bsms_program).
- Also: `grep -rn "BSN_" <tshome>/prog/include/ <tshome>/exp/stan/nmr/au/`
  and save the BSN identifier table it finds.

## B. The documented chain: edlock table + lopo + lock (20 min)

Background (documented, TopSpin 2.1 and v014/2020 acquisition
references, identical wording): the per-solvent "Distance"/"Shift
[ppm]" in the edlock table IS the BSMS LOCK SHIFT; `lopo <solvent>`
sets it "on the BSMS unit without performing lock-in"; `lock
<solvent>` autolocks and adjusts the field to the new setpoint
(capture ~1000 field units ~ 8 kHz at 1H).

1. Locate and copy the table: `<tshome>/conf/instr/<instrum>/2Hlock`.
   Save the original AND note the column layout of this TopSpin
   version (undocumented; differs across generations).
2. In edlock (GUI), duplicate the session solvent's row as ZZDUMMY
   (same lock power / loop gain / loop time / filter / phase); close.
   Confirm the row landed in the file.
3. With the sample locked on the real solvent, note the lock display.
   Run `lopo ZZDUMMY` from the command line, with ZZDUMMY's shift
   +0.500 ppm from the solvent's. EXPECTED: the lock signal moves off
   resonance (shift pushed without lock-in). Record what happens
   while LOCKED -- does the servo track the moved reference (field
   follows, ~0.5 ppm), or does the lock just sit off-resonance?
4. `lock ZZDUMMY` (or `lock -acqu` after `lopo`): EXPECTED autolock
   carries the field +0.500 ppm. Verify with a 1D of the sample line
   (the network script's pulse-pair verification does exactly this).
5. EDIT THE FILE directly (script path): change ZZDUMMY's shift to
   +1.000 ppm with a text editor while TopSpin runs; `lopo ZZDUMMY`
   again. KEY QUESTION: does lopo re-read the file per invocation, or
   is the table cached (need `edlock` refresh / TopSpin restart)?
6. From Jython (`xpy` test stub): `XCMD("noqu lopo ZZDUMMY")` then
   `XCMD("noqu lock ZZDUMMY")` -- the `noqu` prefix matters (lock
   returns before completion otherwise; observed on TopSpin 4.0.5).
   Time how long lock-in takes; note any dialog popped.
7. Restore: delete ZZDUMMY from the table, `lopo <solvent>`,
   `lock <solvent>`, verify the line is back at baseline.

## C. The low-level tier: bsmscmd / PUTBSMSVAL (20 min)

Background: Bruker's inc/bsms_program include defines
GETBSMSVAL/PUTBSMSVAL over the internal `bsmscmd` command. READS of
BSN_LOCK_SHIFT ship in Bruker's own pulsecal; WRITES of BSN_FIELD run
in production elsewhere (Cambridge facility, TopSpin 3.6.5, github.com/
CamNMRService/cam-ts3 au/service/setfield). The LOCK_SHIFT WRITE is
the one untested link.

1. Compile a 5-line AU: GETBSMSVAL(BSN_LOCK_SHIFT, v); Proc_err
   print v. Does it compile on this generation? Does the value match
   the BSMS display?
2. If A's BSN table has LOCK_SHIFT writable: PUTBSMSVAL(
   BSN_LOCK_SHIFT, v + 0.5); re-read; watch the display. Does the
   write take? Does the servo respond while locked?
3. GETBSMSVAL(BSN_FIELD, f); print. (Read-only test of the FIELD
   fallback; do not write FIELD while locked.)
4. Sweep control: does SWEEP_OFF / SWEEP_ON (documented AU macros)
   actuate the BSMS sweep, and does GETBSMSVAL read the sweep state
   back? Record exact behavior after LOCK_OFF (the 2022 lesson:
   unlocking re-enables the sweep on many consoles -- does it here,
   and does the read-back see it?).

## D. Record for the maintainer

TopSpin version; console/BSMS generation (ELCB?); the 2Hlock column
layout; answers to B3/B5 (servo-tracks-shift?, lopo re-read?); C1-C4
outcomes; bsms_exam's parameter list; timings (lock-in seconds per
0.5 ppm step). This decides the autostep tier order for that console
family:

- B works fully  -> autostep = file edit + lopo + lock (documented path)
- C2 works       -> autostep = PUTBSMSVAL AU (faster; no file edits)
- neither        -> operator dialogs remain (autostep declines itself)

The orchestrator ships the edlock+lopo tier as the opt-in `autostep`
flag (xpy spin_noise_run sweep autostep) -- this checklist is its
per-console-family gate before first live use.

The sweep feature's verification 1D and hop discipline stay in force
regardless of tier -- MEASURED offsets, never targets, are the record.
