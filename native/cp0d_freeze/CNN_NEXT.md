# Next checkpoint: CNN specification and software reference

Use the same Croc repository on a new `cnn/bringup` branch. Keep the frozen baseline
archive as the comparison reference. Creating the branch preserves current local
changes; it does not automatically commit them. Do not push generated archives or
large PDK/evidence trees to Git.

The next implementation checkpoint should establish:

1. One fixed tensor-shape CNN workload, dataset and accuracy target. Treat any model
   from earlier coursework as a candidate until its current weights and dimensions
   are supplied or reproduced.
2. Quantization and a bit-exact integer reference, including signedness, accumulator
   width, rounding, saturation, padding and tensor layout.
3. A MAC count, weight/activation memory budget, and a baseline software test.
4. A memory-mapped register interface and completion/error protocol. A custom ISA
   and custom compiler are not required for a memory-mapped accelerator; ordinary C
   can configure and launch it.
5. A deliberate data-movement choice: begin with CPU-driven buffer transfers for
   bring-up, then add an OBI bus-master/DMA path if its measured benefit justifies it.

Acceptance: deterministic expected outputs and a documented interface/memory budget
before integrating accelerator RTL. No CNN RTL or dataset is introduced by CP0D.
