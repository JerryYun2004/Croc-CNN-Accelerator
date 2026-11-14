# CP2B acceptance and next step

The Mac runner verifies the exact accepted CP2A archive manifest, all prerequisite
checksums, the unchanged engine, baseline Croc sources, upstream simulation sources,
and technology files. It regenerates all 55 fixtures from CP1's scalar reference
and requires byte equality with accepted CP2A fixtures.

Functional acceptance comprises strict lint of new RTL (with narrow, documented
waivers for inherited upstream warnings), 120 checked peripheral inferences,
4,336 protocol checks and 10 driver checks, plus 55 complete inferences through
the staged Croc user-domain decoder and demultiplexer. Every pooled byte and score
is compared. Peripheral tests also compare the entire 4 KiB buffer for canary and
parameter preservation. All eight IDs, every BE mask, bank endpoints, random writes,
held requests, changed fields after grant, denied grants/responses, busy conflicts,
first-error semantics, arithmetic overflow, memory errors and reset cancellation
are covered. Two accepted CPU transactions are deliberately cancelled by reset;
the request/response accounting must differ by exactly those two.

Synthesis must contain exactly two IHP SRAM macros, mapped standard cells only,
no inferred memory or RTL processes, and all SRAM BIST/test controls tied low.
Removal of Yosys scope metadata must preserve circuit counts and cell area.
The TT timing estimate includes cell and SRAM Liberty and must meet the provisional
10 ns setup period. Hold/fanout findings are reported, not treated as electrical
closure. No wires, floorplan, routing, CTS, extraction or corner sweep is included.
Older OpenSTA versions list tied BIST pins as unconstrained; the checker accepts
only explicitly listed BIST endpoints proved tied to zero in the mapped netlist.
Other constraint warnings fail. No timing exceptions are added for functional paths.

The installer stages `integration/user_domain.sv`; it replaces only the old
placeholder with `cnn_croc` and leaves the baseline decoder, address map, default
error subordinate, manager tie-off and interrupt tie-off intact. These sources
are compiled and simulated together, but the user's live RTL/Bender lists are not
overwritten. CP2B results are peripheral/user-domain evidence, not full-SoC evidence.

After the Mac archive passes review, CP2C will integrate this tested overlay into
the working Croc build, add source lists and a small CVE2 firmware workload, and
measure CPU copies plus end-to-end inference. Croc still has only 4 KiB firmware
SRAM, separate from the 4 KiB accelerator buffer: code/stack/test-data placement
must be budgeted explicitly. A later whole-chip physical pass must verify fit and
repair hold/fanout/electrical issues. `CP0C_ELECTRICAL_PENDING` remains in every result.
