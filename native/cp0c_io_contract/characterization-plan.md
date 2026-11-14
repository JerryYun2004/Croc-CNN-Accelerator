# Characterization plan required for electrical closure

This is an experiment specification, not an executed SPICE testbench or an
approved foundry characterization flow. It deliberately leaves physical
acceptance values undefined until the intended interface and model conventions
are known. No simulator installation is required for the current audit.

## Inputs to fix first

1. Target package/board, external drivers and receivers, operating supplies,
   temperatures and process-corner coverage.
2. Capacitance budget: package/bond-wire, PCB, receiver input, optional probe,
   and margin. Keep intrinsic pad and on-chip wiring terms separate to avoid
   counting capacitance twice.
3. Receiver voltage thresholds and allowable input transition. Clock, JTAG,
   UART and GPIO need their own timing assumptions.
4. Output-release definition: a specified current/conductance threshold,
   voltage condition and timeout. Do not equate a 0Z/1Z delay to a rail-to-rail
   output transition without confirming the characterization method.

## Simulation requirements

Use the pinned pad SPICE hierarchy and a complete, versioned matching device
model set. First audit every subcircuit/model reference, unit, bulk connection
and supply domain. Resolve any simulator syntax translation with local
device-level controls before trusting a pad result.

For each required PVT point, simulate normal data, output-enable and output-disable
separately. Measure 10–90% output transitions for driven data/enable waveforms,
consistent with these Liberty files. Also retain the measured voltage waveforms
and drive currents so a timing number cannot mask an electrical failure.

For output disable, test both initially-high and initially-low states. A pad
with only a capacitor can retain charge after its driver releases; that voltage
alone does not establish release time. Use an explicit opposing pull/driver
model and measure the releasing driver's current or output conductance according
to the selected release definition.

For bidirectional handoff, simulate both directions: Croc-to-external and
external-to-Croc. Include the two drivers' earliest turn-on and latest release,
control skew and margin. Bound contention current and duration, final receiver
voltage and sampling time. A test with output-enable permanently high or low
does not cover this behavior.

## Controls and outputs

- Baseline data and enable timing should reproduce selected characterized
  table points before investigating disable semantics.
- Excessive load, slow input transition, overlapping drivers, swapped enable
  polarity, wrong supply and missing output-driver devices should be detected.
- Retain all source hashes, netlists, model versions, settings, raw waveforms,
  convergence messages and measurement definitions.
- A mismatch against the original Liberty file remains visible. Never adjust
  tables or measurement thresholds merely to obtain a pass.
- A corrected model requires separately versioned characterization evidence;
  an exception requires a specific applicability argument and replacement check.

Completion requires evidence and an interface specification. Merely running
SPICE successfully or obtaining a plausible waveform is insufficient.
