# 19F pilot nights (adopted 2026-09-01)

Every fluorine-capable probe adds essentially free proton-band mass
points: 19F sits at 0.941x the 1H frequency, rides the 1H/19F coil at
or near cryo sensitivity, and needs NO X-channel calibration work.
This checklist is for a facility (or its agent) running the first 19F
noise night. The science rationale lives in docs/science_roadmap.md,
section 3.2.

## Is this probe eligible?

- The probe tunes its high-band channel to 19F (most 1H/19F combination
  probes, many broadband inverse probes; ask the facility manager or
  check the probe datasheet). ATMA makes the retune one command;
  manual tune/match works too.
- Nothing else changes: same console, same pulse program, same
  software.

## Sample

Hexafluorobenzene (C6F6), neat, in a standard 5 mm tube, ~550 uL --
cheap, inert, one strong 19F line. Add ~10% C6D6 if the facility wants
a lock during setup (the lock is off for acquisition as usual).
Perfluoro compounds with several lines (e.g. TFA) also work; C6F6 is
preferred because a single line keeps the verification and analysis
identical to the water case.

## Running the session

19F pilots run through the TopSpin ORCHESTRATOR ONLY for now: the
converter/packer paths (JEOL, Agilent, Magritek, Nanalysis) do not yet
carry the observe-nucleus metadata, so a 19F night packed through them
would record a wrong field coordinate. (The V5 nameplate check in the
validation campaign is the safety net either way.)

1. The operator prepares a TEMPLATE experiment on the 19F channel
   (new dataset, NUC1 = 19F, tune/match, shim, AND the carrier O1 set
   on or near the C6F6 line, around -165 ppm) exactly as they would
   for any 19F experiment. The orchestrator inherits the template's
   carrier on X-nucleus sessions -- the water default it uses for 1H
   would sit ~95 kHz off-resonance here.
2. Start the orchestrator from that template: `xpy spin_noise_run`
   (all the usual options compose). The script inherits the observed
   nucleus from the template -- v0.6 records
   `spectrometer.observe_nucleus = "19F"`, stores the ACTUAL carrier
   in `observe_freq_mhz`, and keeps `h1_freq_mhz` as the magnet's
   1H-equivalent frequency.
3. P90 calibration: `pulsecal` is a water/1H tool and will usually
   fail on 19F -- the script's manual-P90 dialog path handles that;
   enter the facility's known 19F 90-degree pulse (or calibrate one
   the standard way beforehand).
4. Everything else -- RG ladder, references, noise blocks, sweep,
   bundling, upload -- is nucleus-blind. The lock-shift sweep math is
   already exact for any observed nucleus (1 ppm of lock shift =
   BF1 Hz at that nucleus).

## What the analysis does with it

The axion-mass coordinate of a 19F session is the 19F carrier
(`observe_freq_mhz`), NOT the magnet's 1H frequency: a 600 MHz magnet
contributes a second mass point at ~565 MHz (2.335 ueV) when it runs
fluorine. The facility report's mass bookkeeping handles this from the
v0.6 metadata automatically.

## Pilot goal

2-3 sites, one C6F6 night each, through the standard validation
checklist (docs/CLAUDE_INSTALL.md, step 8 -- V1..V7 apply unchanged).
After the pilots, fold 19F into the standard rotation at every
F-capable site.
