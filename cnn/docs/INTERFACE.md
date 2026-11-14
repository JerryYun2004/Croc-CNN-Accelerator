# Proposed OBI/MMIO contract (RTL not yet implemented)

Use Croc's user-domain OBI subordinate for CPU configuration, data copies and polling.
No custom instruction or modified compiler is needed for this design: normal C
loads/stores can control it. Initially keep the user-domain OBI manager tied off.
DMA is deferred until measured CPU-copy overhead justifies adding a bus manager.
The baseline's optional iDMA is disabled; CP1 does not enable it.

Reserve 0x2000_0000..0x2000_0fff for the upstream-suggested user information ROM.
Proposed control page: 0x2000_1000..0x2000_1fff. Proposed buffer window:
0x2000_2000..0x2000_2fff. These addresses require an explicit user-domain decode
change at integration; no decode or RTL is modified now.

| Register offset | Name | Access | Contract |
|---:|---|---|---|
| 0x00 | ID | RO | 0x434e4e31 (CNN1) |
| 0x04 | VERSION | RO | 0x00010000 |
| 0x08 | CONTROL | WO | Bit 0 START, bit 1 CLEAR; reserved bits zero |
| 0x0c | STATUS | RO | Bit 0 BUSY, bit 1 DONE, bit 2 ERROR; remaining bits zero |
| 0x10 | ERROR_CODE | RO | 0 none, 1 invalid access, 2 invalid shift, 3 busy conflict |
| 0x14 | CYCLES | RO | Compute cycles for last/current invocation; saturates at UINT32_MAX |
| 0x18 | CONV_SHIFT | RW, idle | 0..30; reset 9 for this model |
| 0x1c | PREDICTION | RO | 0..9 after DONE; otherwise undefined |

CONTROL reads return zero. Unlisted control offsets, misaligned word accesses,
partial control-register writes, writes to RO fields and reserved CONTROL bits
return an OBI error and set sticky ERROR_CODE. Buffer writes honor byte enables;
reads return a full aligned 32-bit little-endian word. Buffer contents are undefined
after reset and must be loaded by software. Memory access while BUSY is rejected
with a busy-conflict error; STATUS/ERROR_CODE/CYCLES remain readable.

START when idle clears DONE and prior error, sets BUSY and clears CYCLES. Software
must first load the complete model and input and set CONV_SHIFT; this version does
not track per-byte validity. At completion, finish writing scores before setting
DONE and clearing BUSY. START/CONV_SHIFT/CLEAR writes while BUSY are rejected and
must not abort or corrupt the running operation. Idle CLEAR clears DONE/ERROR and
ERROR_CODE without changing weights or buffer contents. CLEAR+START executes CLEAR
then START when idle. On error, retain the first error code until an accepted
CLEAR or START. Reset clears BUSY, DONE, ERROR, CYCLES and control state; in-flight
computation is discarded. IRQ stays inactive in the initial implementation.

Count compute cycles from the cycle following accepted START through the completion
cycle, inclusive. This excludes CPU transfers/polling; end-to-end cycle measurements
must separately include those costs. Record counter saturation explicitly in tests.

Initially support at most one outstanding OBI request: latch a request only on
req && gnt, preserve aid in rid, issue exactly one rvalid response per accepted
request, and do not accept another until that response is delivered. Croc uses
UseRReady=0, so the response cannot depend on an rready signal. Grant and response
latency may vary; do not assume immediate responses. Test held req and backpressure.

## Local 4 KiB buffer offsets

| Offset | Bytes | Content/layout |
|---:|---:|---|
| 0x000 | 784 | Raw input, uint8 [28][28] |
| 0x310 | 100 | Conv weights, int8 [4][5][5] |
| 0x374 | 16 | Conv biases, little-endian int32 [4] |
| 0x384 | 1,440 | FC weights, int8 [10][144] |
| 0x924 | 40 | FC biases, little-endian int32 [10] |
| 0x94c | 4 | Reserved alignment gap |
| 0x950 | 144 | Pooled output, uint8 HWC [6][6][4] |
| 0x9e0 | 40 | Output scores, little-endian int32 [10] |
| 0xa08 | 1,528 | Unused/reserved in version 1 |

model/model.bin is exactly the 1,596 bytes starting at buffer offset 0x310.
Load it once; retain it across inferences. Each new image requires 784 input bytes
and, if all scores are read, 40 result bytes, plus control/polling accesses. These
counts exclude instruction fetches and all other bus traffic. Software requires
ordered MMIO operations, a bounded timeout and explicit status/error checking.
A future DMA must not race CPU accesses to accelerator-owned buffers.
