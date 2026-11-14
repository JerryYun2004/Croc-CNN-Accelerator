# Implemented CP2B interface

The model, arithmetic and buffer layout are unchanged from CP1 and CP2A. The
engine is imported from the pinned CP2A 1.2 directory; it is not duplicated or edited.
This document resolves the earlier proposed interface where specified below.

| Address | Register/window | Access |
| --- | --- | --- |
| 0x20001000 | ID = 0x434e4e31 | RO |
| 0x20001004 | Interface VERSION = 0x00010000 | RO |
| 0x20001008 | CONTROL: bit 0 START, bit 1 CLEAR | WO, reads zero |
| 0x2000100c | STATUS: bit 0 BUSY, bit 1 DONE, bit 2 ERROR | RO |
| 0x20001010 | ERROR_CODE | RO |
| 0x20001014 | CYCLES, saturating engine counter | RO |
| 0x20001018 | CONV_SHIFT, reset 9, valid 0..30 | RW while idle |
| 0x2000101c | PREDICTION, valid after DONE | RO |
| 0x20002000..0x20002fff | Accelerator-local 4 KiB buffer | RW while idle |

All bus addresses must be word-aligned. The pinned CVE2 LSU aligns OBI addresses
and carries subword selection in BE. Buffer writes accept all 16 byte-enable masks,
including zero as a no-op; reads return the complete aligned word. Control-register
writes require BE=1111. A CPU's misaligned load/store may be split by its LSU;
the peripheral cannot detect the original instruction's alignment from aligned bus requests.

The 0x20000000 page remains reserved; CP2B does not implement an information ROM.
Unlisted control offsets, wrong pages/aliases, unaligned bus addresses, RO writes,
partial control writes and reserved CONTROL bits return one OBI error response
and set sticky error 1. Error responses return zero data from the CNN peripheral.
Croc's separate default error subordinate retains its existing 0xBADCAB1E response.

| Code | Meaning |
| --- | --- |
| 0 | No error |
| 1 | Invalid MMIO access |
| 2 | Invalid CONV_SHIFT write (31 or greater); previous shift retained |
| 3 | Buffer access or START/CLEAR/shift write while busy |
| 4 | Local-memory response error; inference aborts if it was an engine access |
| 5 | Engine int32 accumulation overflow; inference aborts |

ERROR and its **first** code remain until an accepted idle START/CLEAR or reset.
An earlier CPU access error therefore remains visible even if a later engine error
aborts inference; DONE remains clear after an abort. When a newly observed engine
error and a new invalid CPU transaction coincide, the engine error has priority.
This wrapper implements CP1's first-error policy without changing CP2A's internal
error-register policy. Bad busy commands never reach the engine's command inputs.
START/CLEAR+START clears old errors and starts from the beginning. A CONTROL write
of zero is a legal no-op even while busy. CLEAR leaves buffer, prediction and cycle
counter unchanged; its effect is to clear DONE/ERROR. The counter resets on START.

## OBI and ownership

- 32-bit address/data, 4 byte enables, 3-bit AID/RID for the frozen Croc configuration.
  The peripheral parameter also supports another positive ID width.
- `gnt` is independent of `req`. A request is accepted only on `req && gnt`.
- One outstanding request. Exactly one response, including writes; no `rready`
  (`UseRReady=0`). The response cannot coincide with the accepting grant.
- No new grant in the response cycle. The requester may hold the next request
  while waiting. Address/data/BE/AID are latched at acceptance.
- While BUSY, only the engine can issue SRAM operations. Status, error and cycle
  reads continue without using SRAM. CPU buffer accesses fail without touching memory.
- A CPU buffer write finishes before the next START can be accepted. DONE is set
  only after the engine's final score-write acknowledgement.
- Shared clock, asynchronous active-low reset; deassertion must be synchronized
  by Croc's reset tree. Reset flushes pending responses and aborts the engine.
  Writes already committed before reset are not rolled back. SRAM is not cleared.
  Software must reload its model/input after reset; partially generated outputs are invalid.

## SRAM adapter

Two `RM_IHPSG13_1P_512x32_c2_bm_bist` macros provide 1024 words. Byte-address bit
11 selects the bank; bits 10:2 select its row. BE expands to an active-high bit
write mask. Normal read/write enables are active high, as in the frozen IHP model.
All BIST inputs and A_DLY are tied low; synthesis checks every tie and both macro
instances. Successful writes commit on grant; the registered response follows.
There is no ECC or physical SRAM-failure detection. Fault injection exists only
in the testbench to verify the interface error behavior.

The adapter's one-cycle read/write response makes inference take **302,451** engine
busy cycles: 181,903 CP2A zero-response-delay cycles + 120,548 memory transactions.
There are 120,394 reads, 154 writes and 59,040 MACs. CPU copies/polling are excluded.
At a hypothetical 100 MHz this is 3.02451 ms; chip clock closure and speedup have
not been demonstrated.

| Buffer offset | Content | Bytes |
| --- | --- | ---: |
| 0x000 | Raw uint8 image | 784 |
| 0x310 | Signed INT8 convolution weights | 100 |
| 0x374 | Four INT32 convolution biases | 16 |
| 0x384 | Signed INT8 FC weights | 1,440 |
| 0x924 | Ten INT32 FC biases | 40 |
| 0x94c | Reserved gap | 4 |
| 0x950 | Pooled HWC activations | 144 |
| 0x9e0 | Ten signed INT32 scores | 40 |
| 0xa08 | Unused | 1,528 |

`sw/cnn_driver.h` copies the contiguous 1,596-byte CP1 model at offset 0x310,
then a 784-byte image at offset zero. Reuse model bytes for subsequent images.
The driver uses ordered volatile MMIO and RISC-V fences. Its timeout bounds polling
iterations, not a bus access with no response. It does not abort hardware on timeout
or error, handle CPU bus-fault traps, or arbitrate multiple software callers; use
one exclusive software owner. It is exercised through host MMIO callbacks in CP2B,
not executed as CVE2 machine code.
