# CP2A acceptance and limits

Run from the existing cnn/bringup branch, after CP1_PASS. The runner uses installed
Verilator, Yosys and OpenROAD/OpenSTA; it does not download tools or modify the PDK.
It adds results only under cnn/results/cp2a-*, without using the CP0C lock.

Required pass conditions:

1. CP1 source and native evidence match their recorded SHA-256 manifests.
2. Strict Verilator lint passes with no warning suppression.
3. Arithmetic tests pass: every signed int8 operand pair with zero, minimum and
   maximum int32 accumulators, plus rounding/saturation boundary cases.
4. All 48 CP1 vectors and seven directed fixtures match the independent scalar
   Python oracle at every pooled byte, every score and the final prediction, under
   immediate, one-cycle and randomized delayed memory responses (165 full runs).
5. Request stability, one outstanding transaction, output byte strobes, write
   counts, input/canary preservation and MAC/read/write counts pass. Reset at four
   transaction/computation points, invalid shift, busy commands, read/write errors,
   overflow and three output-checker corruption controls pass.
6. Yosys records synthesis statistics before and after removing only `$scopeinfo`
   hierarchy metadata. The cell counts and area of the circuit must remain unchanged.
   The final mapping check still rejects every non-library cell. Yosys maps all engine cells to the pinned IHP SG13G2 TT library, with no memory
   array or generic cells remaining. Check -assert passes before and after mapping.
7. OpenROAD loads the pinned technology and standard-cell LEFs before linking
   the netlist. OpenSTA completes with constrained paths and nonnegative worst setup slack at
   10 ns, using the provisional standalone budget below.
8. CP1 files, checked Croc integration files, PDK inputs, prerequisite evidence and
   Git state are unchanged after the run.

The 100 MHz target follows the frozen Croc constraints. Standalone setup uses TT
1.20 V/25 C, 0.1 ns uncertainty, 0.2 ns clock/input transition, 1 ns maximum input
and output delay, and 0.01 pF output load. These port budgets are provisional. There
is an ABC mapping driver assumption of sg13g2_buf_4 and a 10 fF output load; this
enables ABC's buffer and sizing passes. The load equals STA's 0.01 pF. These
mapping assumptions are recorded in flow/abc.constr. There
are no wire parasitics, clock-tree effects or SRAM timing arcs. Reset is excluded
from data timing; integration must provide safe reset deassertion. Hold and
electrical constraint reports are informational at this stage, not closure.

Area reports distinguish engine standard-cell area from the footprint of two
candidate 512×32 SRAM macros. This is not placed area or available die capacity.
Macro mapping, byte-write support, power pins, memory adapter, CPU arbitration,
firmware, routing, multi-corner STA, full physical verification and whole-chip fit
remain to be implemented or checked. Existing Croc SRAM is not assumed free.

CP0D remains the development baseline. CP0C electrical status stays PENDING.
CP2A_PASS is a standalone functional/synthesis checkpoint, not tapeout signoff.

## Next checkpoint: CP2B

Implement the OBI subordinate control page and buffer adapter in a separate commit
on cnn/bringup. Define ownership while BUSY, map the additional 4 KiB storage,
connect reset and error handling, run bus-level tests, then execute the frozen
vectors from Croc firmware. Recheck whole-SoC synthesis before any backend rerun.
DMA, custom instructions and a custom compiler are unnecessary for this first
CPU-loaded, polling implementation.

References:

* https://verilator.org/guide/latest/exe_verilator.html
* https://yosyshq.readthedocs.io/projects/yosys/en/latest/cmd/index_passes_techmap.html
* https://github.com/The-OpenROAD-Project/OpenSTA/tree/f7febae35ba9675f4ff08839623e3aa8d319ce58
* https://github.com/pulp-platform/croc/blob/29b2e748e72c592a5a895c2b11fa85bc9080f67a/openroad/src/constraints.sdc
