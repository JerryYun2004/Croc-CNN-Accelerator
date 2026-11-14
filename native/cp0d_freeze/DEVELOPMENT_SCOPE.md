# Croc development baseline

Decision: proceed with CNN development while preserving CP0C electrical signoff as PENDING.
This scope follows the user's acceptance on 2026-10-23. It is not a pad-model waiver.

The baseline is a frozen working-state archive plus evidence and tool identities.
It is suitable as a reference for accelerator integration and controlled comparisons.
Creating the archive does not demonstrate a clean rebuild or prove that the current
source tree regenerates the saved physical implementation. Those require a separate
reproduction run. The snapshot records this limitation explicitly.

## Preserved results and their limits

- Final physical reference: SRAM power-via candidate, with exact ODB and GDS hashes.
- Internal setup/hold: the recorded TT/FF checks passed under the documented RC model.
- RC: approximate LEF-derived model, shared across TT/FF; 80 pad-driver exceptions.
  No foundry-calibrated multi-corner RC or package extraction is claimed.
- Full-chip LVS: diagnostic comparison with annotated references and negative controls;
  not vendor-CDL signoff.
- Prior DRC: interconnect checking only. FEOL/density coverage is not upgraded here.
- I/O: original 15 pF constraints stay unchanged. 3 pF and synthetic input transitions
  remain diagnostic settings. Dynamic GPIO operation remains in the design.
- Slow-corner GPIO model: 0.668662 pF maximum capacitance versus 1 pF table minimum;
  minimum normal-data falling slew 1.31851 ns versus 1.2 ns limit. Disable-slew issues
  also remain. These are model findings, not measured silicon defects.
- Board/package requirements and GPIO release/turnaround requirements remain undefined.
- IR drop, electromigration, ESD/package qualification and complete foundry signoff
  are not established by these development results.

## Responsibility and control

We own RTL, bus integration, constraints, floorplanning and internal timing fixes.
We can define an intended external interface and negotiate its load, timing and
voltage budgets with the board/package or shuttle team. A board need not be built
before conditional timing analysis, but the assumptions must be documented and
later checked against the real integration.

The PDK/library provider owns pad characterization and authoritative model correction.
We can reproduce a suspected inconsistency and propose or evaluate a correction.
Editing a Liberty limit to make a report pass would not prove physical correctness.
No source is sent to a maintainer by this package.

## Baseline comparison rules

Keep the same PDK/tool pins, clock/SDC, cell corners, RC method and macro/pad placement
when comparing baseline and CNN implementations. Re-run the appropriate functional,
timing and physical checks on the changed design. Report area/cycles/timing with this
scope attached. Existing I/O findings must remain visible; detect any new ones.

Electrical closure means satisfying timing and electrical constraints across the
specified operating cases. Slew, capacitance and fanout limits are distinct from
setup/hold timing. Complete fabrication signoff additionally needs the process's
physical verification and reliability requirements.

Sources for general tool behavior:
https://openroad.readthedocs.io/en/latest/main/src/rsz/README.html
https://openroad.readthedocs.io/en/latest/main/src/sta/doc/Examples.html
Exact model findings and evidence remain in the frozen I/O audit.
