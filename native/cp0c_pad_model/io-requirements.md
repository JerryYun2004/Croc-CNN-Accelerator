# I/O closure decision record — pending

This is a draft for reviewing the one-pad model evidence. No values below are
approved changes to Croc's constraints or PDK libraries.

## What the checkpoint establishes

The existing native 3 pF GPIO reports can be compared independently with the
pinned TT/FF Liberty tables. The new fixture tests whether the same numerical
disable-slew behavior occurs with one pad and explicit input transitions.
`PAD_DISABLE_MODEL_REPRODUCED` means those checks and the fault controls passed.
It does not establish that the high-impedance waveform is physically correct.

At TT, the tabulated minimum disable transitions are 1.31961 ns (0Z) and
1.91034 ns (1Z), versus the inherited 1.2 ns maximum-transition limit. Within
the table axes, bilinear interpolation cannot be lower than the smallest
table entry. Faster enable logic or lower total capacitance within that domain
therefore cannot make these particular TT model checks pass. This statement
does not cover extrapolation, a different model, or physical characterization.

The disable delay and transition tables are identical in the supplied models.
That warrants investigation; it does not by itself demonstrate a PDK error.

## Decisions still needed

| Item | Evidence required | Current state |
|---|---|---|
| GPIO function | Which pins are input, output, or dynamically bidirectional in the intended system | Unspecified; keep dynamic support |
| External output load | Package, bond wire, board trace, receiver and probe capacitance, including margin | 3 pF is diagnostic only; original 15 pF remains unchanged |
| External input transitions | Receiving-interface limits and the external driver's timing over intended corners | 0.2 ns is synthetic, not a board specification |
| Data and enable-on slew | Applicable library/receiver limits and evidence that the selected loading meets them | TT/FF model samples only |
| Output disable/release | Definition of high-Z threshold, disable-to-release time, turn-on time and bus-contention margin | No approved turnaround requirement |
| Disable table semantics | PDK characterization source or transistor-level characterization with defined external pull/driver conditions | Open |
| Timing corner coverage | Required voltage, temperature and process cases, plus validated I/O/package RC | TT/FF only; no slow-corner or package signoff |

## What would justify the next action

1. If model intent/characterization confirms an error, prepare a separately
   versioned correction with source evidence and regression checks. Never
   overwrite the pinned library silently.
2. If the model is intentional but a general transition constraint is
   inapplicable to high-Z release, document the exact scope and validate a
   separate turnaround/contending-driver requirement before considering an
   exception. Preserve data, receive, load and enable-on checks.
3. If external loading is physically excessive, select a supportable interface
   or buffering arrangement and revalidate its load and timing.
4. Any accepted change requires a fresh full-chip timing run and unchanged
   DRC/LVS/geometry evidence before claiming CP0C closure.

This package performs none of those design, model or constraint changes.
