# CP1 fixed workload: Croc MNIST C4K5-P4-FC10

Version 1.0. This is the software and interface contract for the first accelerator.
It is a deliberately small starting workload, not the user's earlier coursework
model. The provided weights were trained for this checkpoint. The model can be
replaced through a new specification revision before RTL integration if needed.

## Workload and measured acceptance

MNIST handwritten digits: 28 x 28 grayscale, ten classes. Train on original training
indices 0..54999; select the best epoch using 55000..59999. Evaluate the frozen
integer model once on all 10,000 official test images. Initial target: >=95%.

The delivered model scores 9,711/10,000 (97.11%) with integer inference and 97.06%
in floating point. Best epoch is 14, chosen by validation accuracy 97.76%. Test
examples were not used to choose weights, quantization ranges or training duration.
The small positive quantization difference is a measured outcome, not a general
claim that integer arithmetic improves accuracy. See model/training-report.json.

| Stage | Input | Operation | Output | MACs |
|---|---|---|---|---:|
| Input | 28x28x1 uint8 | Shift each pixel right by 1 | 28x28x1, 0..127 | 0 |
| Convolution | 28x28x1 | 4 kernels, 5x5, stride 1, no padding | 24x24x4 int32 accumulators | 57,600 |
| Requantize + ReLU | Accumulator | Round right shift 9, clamp 0..127 | 24x24x4 int8 values | 0 |
| Max pool | 24x24x4 | 4x4, stride 4, no padding | 6x6x4 | 0 |
| Fully connected | 144 elements | 10 dot products plus biases | 10 int32 scores | 1,440 |
| Decision | 10 scores | Argmax; lowest index wins a tie | Digit 0..9 | 0 |

Total: 59,040 MACs/image, plus bias, rounding, comparisons and transfers. This count
is not a cycle count or a speedup measurement. Softmax is unnecessary for argmax;
these scores are not normalized probabilities.

## Exact integer contract

Input is raw image bytes in row-major order. Preprocess `x = pixel >> 1`; this is
unsigned 0..127 stored in one byte, and is nonnegative as signed int8 too. No image
normalization, resizing or inversion is done on the accelerator.

Convolution is cross-correlation (kernels are not flipped). Weights are symmetric
signed int8 in [-127,127], zero point zero. Bias and accumulators are signed int32.
For the frozen weights: `conv_shift = 9`. Round `a / 2^shift` to nearest, with ties
away from zero, then clamp to [0,127]. Define it without relying on C signed shift:
`sign(a) * ((abs(a) + 2^(shift-1)) >> shift)`; shift zero is an identity. Use a wider
temporary for absolute value and rounding to avoid INT_MIN overflow.

Pool with maximum over each nonoverlapping 4x4 region. The output tensor is HWC:
`index = ((y * 6) + x) * 4 + channel`. FC weights use [class][144] in this same order.
Each score is `fc_bias[class] + sum(pooled[i] * fc_weight[class][i])`; no output
requantization, saturation or softmax. Compare signed scores for argmax.

Every partial sum is bounded for valid models. Conv worst product sum magnitude
is <=25*127*127 = 403,225; FC <=144*127*127 = 2,322,576. Model validation constrains
bias magnitude to keep these worst-case bounds inside int32. It rejects -128 weights,
invalid dimensions, unsupported shifts and unsafe biases.

Quantization uses a global power-of-two scale for each weight tensor and one
activation scale calibrated from the first 2,048 training examples. Biases are in
accumulator units. Real scales are recorded in model/model.json; integer operations
above, the frozen model hash and golden vectors define the hardware contract.

## Storage and execution order

| Data | Bytes |
|---|---:|
| Conv weights | 100 |
| Conv biases | 16 |
| FC weights | 1,440 |
| FC biases | 40 |
| Total parameters | 1,596 |
| Raw input image | 784 |
| Pooled activation | 144 |
| Output scores | 40 |
| Parameters + input + pooled + scores | 2,564 |

Fuse convolution, requantization, ReLU and pooling. Keep only the current maximum
for a pool/channel and discard each convolution output. Do not allocate the 2,304
byte intermediate convolution map in the hardware buffer. Accumulators, FSM state,
bus holding registers and any pipeline/line buffers are additional implementation
state and require their own area accounting.

Proposed accelerator-local scratch storage: 4 KiB, separate from Croc's existing
4 KiB shared SRAM. The aligned software map occupies 2,568 bytes, leaving 1,528
bytes unused. The existing Croc SRAM must still hold its executable, data and stack;
firmware fit has not been proven by a host C test. Local SRAM macro mapping and die
fit are CP2 synthesis/floorplan gates. Two 512x32 macros are one candidate for 4 KiB,
not a validated physical implementation. No clock constraint is changed by CP1.

The physical baseline's standard-cell area is not a measure of free chip area:
SRAMs, I/O, routing, placement utilization and power distribution also consume space.

## Checkpoints from here

- CP1 (this package): fixed trained model, exact arithmetic, Python/C agreement,
  operation/storage budgets and proposed MMIO protocol. Run the native check.
- CP2A: standalone parameterized MAC/round/activation/pool/FC RTL with these vectors,
  backpressure/reset tests and a first synthesis area/timing estimate. Choose lane
  count based on this result; CP1 does not promise a four-MAC implementation.
- CP2B: OBI subordinate integration, local-memory mapping and minimal polling driver;
  check the firmware linker map and compare against the frozen Croc baseline.
- CP3: measure end-to-end cycles and transfer overhead; decide whether adding DMA
  improves the system. Preserve existing tool/PDK/clock/physical constraints.

## Sources

- Croc configuration and integration ports at the frozen source revision:
  https://github.com/pulp-platform/croc/blob/29b2e748e72c592a5a895c2b11fa85bc9080f67a/rtl/croc_pkg.sv
  https://github.com/pulp-platform/croc/blob/29b2e748e72c592a5a895c2b11fa85bc9080f67a/rtl/user_domain.sv
- MNIST description, split and attribution: https://keras.io/api/datasets/mnist/
- Dataset archive and SHA256 are in tools/train.py and the training report.
