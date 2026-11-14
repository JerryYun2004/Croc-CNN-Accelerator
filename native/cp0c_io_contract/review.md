# CP0C I/O contract and model review

Date: 2026-10-23. Status: **audit complete; electrical closure blocked**.

The completed native single-pad experiment establishes that the disable-slew
reports follow the pinned Liberty model in isolation. This review adds the
available SS I/O model, checks both output master types, traces the OpenSTA
implementation, and records the interface requirements that remain undefined.

## New finding: a 3 pF profile does not close the available slow corner

These are **total output capacitance** limits from the original PDK files.
External load is only part of total capacitance. No value is an approved board
budget and no SS native timing run was performed here.

| Output master | Instances | TT limit (pF) | FF limit (pF) | SS limit (pF) |
|---|---:|---:|---:|---:|
| IOPadInOut30mA | 32 GPIOs | 4.83452 | 9.99915 | 0.668662 |
| IOPadOut16mA | 7 outputs | 4.21305 | 6.49552 | 2.09528 |

The existing 15 pF external load exceeds every listed limit before adding any
other pin or wire capacitance. A 3 pF external load also exceeds both SS limits.

For the GPIO's SS model, the data tables begin at 1 pF, above its 0.668662 pF
maximum capacitance. There is no overlap between those two specified ranges.
The smallest tabulated normal-data falling transition is 1.31851 ns, exceeding
the inherited 1.2 ns limit. Thus an output-disable-only exception would still
leave an SS normal-data problem. Extrapolating below 1 pF does not establish
characterized operation and is not used to claim closure.

The TT and FF single-pad results remain valid for their tested conditions.
The SS findings are a **static model consistency audit**, not a new measurement
of the chip, and do not prove that fabricated pads cannot operate.

## Why OpenSTA reports the disable transition

The reviewed implementation is OpenSTA commit
`f7febae35ba9675f4ff08839623e3aa8d319ce58`, matching the installed OpenROAD pin.

1. `LibertyBuilder.cc` builds separate enable and disable timing arcs.
2. `Transition.cc` associates 0Z with rise and 1Z with fall for its timing graph.
3. `GraphDelayCalc.cc` merges the calculated arc slew into the driver vertex.
4. `CheckSlews.cc` checks that vertex against the applicable limit, including the
   library's default output transition limit when the pin has no override.

This source path is consistent with the native reproducer. It explains the
reported value; it does not establish whether the pad's disable-transition
tables have the intended physical characterization semantics.

For GPIO disable arcs, the delay and transition tables are identical at all
three reviewed corners. At TT their minimum transition values are 1.31961 ns
and 1.91034 ns; at FF the 1Z minimum is 1.27373 ns. These exceed 1.2 ns within
the characterized table domain. No library patch or timing exception is justified
solely by this numerical observation.

## What the public sources do and do not supply

The pinned CMOS5L tree supplies the Liberty and SPICE views, but the tree review
did not find a disable-characterization testbench, measurement threshold or
generation recipe for these timing tables. The related SG13G2 README identifies
its timing Liberty files as external contributions. Its layout-generation
recipe is not evidence of the timing-characterization method, and its published
drive-strength numbers are not substituted for the CMOS5L models.

The queried history of the SS CMOS5L Liberty path returned its introduction in
commit `55c33b26c1dec9f4730da888cfd0c52e2986d1ee`, whose message describes an
alpha I/O release. No correction of this file appeared in that returned history.
This is a scoped source search, not a claim that no characterization or fix exists
elsewhere. Exact files, commits, hashes and source paths are in source-trace.json.

## Interface contract

`io-contract.draft.json` records the facts and unknowns separately:

- The 32 GPIOs retain dynamic bidirectional support; no output-enable mode is
  tied off in the design.
- The original 15 pF constraints remain unchanged.
- The 3 pF load and 0.2 ns external transition are diagnostic settings only.
- Package, board, external receiver/driver, timing corner scope and electrical
  operating limits have not been supplied.
- Turnaround requires a definition of release, a limit on residual drive, and
  a handoff schedule for both drivers. No dead time is invented from the Liberty
  delay values, and no slow-corner physical bound is inferred.

## Recommended decision before CNN integration

For the stated CNN development goal, preserve the current implementation and
proceed under an explicitly documented **development baseline with open I/O
issues**, if that scope is acceptable. Use it for simulation, core timing and
area comparisons. Carry the unresolved electrical items forward and avoid
claiming CP0C electrical signoff or fabrication readiness. This package does not
change the accepted checkpoint definition or label the baseline qualified.

If complete electrical/tapeout closure is required before CNN work, the next
work is pad characterization and interface definition. The supplied
characterization-plan.md gives the required measurements; maintainer-reproducer.md
is a local draft of the model questions. Nothing is sent or posted.

With either scope, a later constraint/model change needs fresh full-chip STA
and preservation checks. The existing DRC/LVS scope remains as previously
reported; this audit does not upgrade it to foundry signoff.
