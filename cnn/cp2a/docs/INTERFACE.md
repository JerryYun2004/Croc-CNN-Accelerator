# CP2A standalone engine interface

The engine implements CP1's fixed shape, with runtime weights and biases. It has
one signed 8×8 multiplier, an int32 accumulator, fused convolution/ReLU/max-pool,
and a fully connected output stage. It does not contain SRAM or an OBI wrapper.

## Commands and status

All commands are sampled on the rising edge of clk_i. rst_ni is an asynchronous,
active-low reset. Its deassertion must be synchronized in the eventual SoC wrapper.

| Signal | Meaning |
| --- | --- |
| start_i | One-cycle START pulse while idle; starts computation and clears sticky status and cycle count |
| conv_shift_i[4:0] | Latched by accepted START; 0…30 valid, frozen model uses 9 |
| clear_i | One-cycle pulse; clears sticky DONE/ERROR while idle; START takes precedence |
| busy_o | An inference is active |
| done_o | Sticky completion after all ten score writes have been acknowledged |
| error_o, error_code_o | Sticky error and its reason |
| prediction_o[3:0] | Valid when DONE; signed argmax, smallest index wins ties |
| cycles_o[31:0] | Number of busy cycles after accepted START, including the finish cycle; saturates at UINT32_MAX |
| mac_valid_o | High for a MAC cycle, for verification/performance counting |

| Error code | Meaning |
| --- | --- |
| 0 | No error |
| 1 | Reserved for CP1's future MMIO access error |
| 2 | Invalid shift (31); no memory request is issued |
| 3 | START or CLEAR while busy; operation continues without restart or clearing status |
| 4 | Memory response error; operation aborts with DONE clear |
| 5 | Int32 accumulation overflow; operation aborts with DONE clear |

Codes 4 and 5 extend the proposed CP1 MMIO error encoding. A future wrapper must
expose them. CP1's original proposal is preserved on disk. An accepted START clears
previous errors; an error response or overflow takes precedence over a busy-command
error. After an abort or reset, output bytes can be partial and must be ignored.

## Local memory protocol

This interface is a local memory contract, not an implemented OBI port.

* req_o is held with stable we_o, addr_o, wdata_o and be_o until gnt_i.
* addr_o is a byte address in a 4096-byte buffer and is always 32-bit aligned.
* be_o selects little-endian byte lanes; reads use all four lanes.
* Exactly one request may be outstanding. Every accepted request, including a
  write, receives exactly one rvalid_i response. err_i is sampled with that response.
* A response may occur with the grant in the same cycle or later. The engine
  makes no new request while it waits. rdata_i is meaningful for read responses.
* The memory wrapper must commit a successful write before acknowledging it.
  On reset it must flush/cancel any pending response along with the engine.
* The CPU must not access or modify this buffer while BUSY. Arbitration/ownership
  enforcement belongs in CP2B. An absent grant/response leaves BUSY asserted;
  there is no hardware timeout in this version. Tests have a watchdog.
* The clock is shared; no clock-domain crossing is implemented.

| Offset | Bytes | Content |
| --- | ---: | --- |
| 0x000 | 784 | Raw uint8 input, 28×28, row major |
| 0x310 | 100 | Signed int8 convolution weights, OC/KY/KX |
| 0x374 | 16 | Four little-endian int32 convolution biases |
| 0x384 | 1440 | Signed int8 FC weights, class then HWC feature |
| 0x924 | 40 | Ten little-endian int32 FC biases |
| 0x94c | 4 | Reserved; never written by the engine |
| 0x950 | 144 | Pooled HWC activations |
| 0x9e0 | 40 | Ten little-endian signed int32 scores |
| 0xa08 | 1528 | Unused; never written by the engine |

Convolution reads each raw pixel and divides it by two with an unsigned right
shift before multiplication. It rounds the biased sum to nearest, ties away from
zero, then applies ReLU and saturation to 127. Because ReLU follows rounding,
negative sums can map directly to zero. A 33-bit rounding intermediate prevents
INT32_MAX overflow. The final FC stage does not requantize scores.

The engine deliberately rereads operands for each MAC. This establishes a small,
checkable implementation; buffering/reuse is a later performance improvement.
There are 59,040 MACs, 120,394 reads and 154 writes per inference. The memory model
provides all-zero through three-cycle response/grant waits in the tests. Zero-wait
completion takes 181,903 cycles. No trained parameter is compiled into the RTL.
