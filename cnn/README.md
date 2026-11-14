# CP1 — fixed CNN and integer software reference

A trained MNIST classifier and a precise software contract for a Croc accelerator.
This adds only cnn/ project files. Croc RTL, physical constraints, PDK and installed
EDA tools are not modified. CP0C electrical signoff remains PENDING.

The initial workload is chosen for a compact first implementation: 28x28 input,
5x5 convolution with four output channels, ReLU, 4x4 max pool, and a 10-class FC.
It has 1,596 parameter bytes and 59,040 MACs/image. The measured integer test-set
accuracy is 97.11% (9,711/10,000), above the 95% initial target.

Read docs/SPEC.md for exact arithmetic and memory accounting. Read
docs/INTERFACE.md for the proposed OBI register/buffer interface. The proposed
4 KiB accelerator-local buffer is additional to Croc's 4 KiB shared SRAM; actual
SRAM mapping, firmware fit, area, routing, timing and speedup remain unverified.

## Run on your native Mac

Use the existing Croc Python and Apple command-line C compiler:

```bash
bash <<'BASH'
set -euo pipefail
cd "$HOME/projects/croc-cnn-native"
caffeinate -i bash cnn/run.sh --baseline \
  "$HOME/projects/croc-cnn-baselines/cp0d-20261023-125611-6o876n9f"
BASH
```

The runner checks the frozen baseline metadata and three Croc integration-source
hashes, runs arithmetic tests, compiles a native C reference and compares all
pooled activations, logits and decisions for 48 fixtures against scalar Python.
It records that the 10,000-image accuracy evaluation was performed during package
validation, not repeated on the Mac. The native run needs no downloads or additional
Python packages. It does not run Croc firmware or hardware simulation.

On success: CP1_PASS and CP0C_ELECTRICAL_PENDING. Upload the printed cnn/results/
archive. Results and host executables are ignored by cnn/.gitignore. Preserve the
full CP0D baseline separately; see docs/BASELINE_BACKUP.md.

## Project files to keep in Git

- model/model.json and model.bin: frozen integer parameters and scales.
- model/float-model.npz: selected float weights for provenance; not used in native checks.
- model/training-report.json and c-validation.json: measured software results.
- reference/: scalar Python and portable C implementations.
- vectors/vectors.json: 48 fixed images and exact intermediate/score expectations.
- tests/, tools/, docs/, baseline-pin.json and this README.

Stage only cnn/ after reviewing the native result. The results/, data/ and .venv/
subdirectories are ignored. Do not stage the full baseline or PDK/output directories.

## Optional training reproduction

Normal CP1 checks do not need training. To reproduce training, create a separate
Python 3.13 virtual environment, install the wheel for numpy==2.3.5, then run:

```bash
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2 \
  python tools/train.py --data data/mnist.npz --out results/retrained --epochs 15
```

The output directory must not already exist. The script downloads the 11 MB MNIST
archive from the Keras-used URL and checks its fixed SHA256 before reading it.
It uses 55,000 training examples, a separate 5,000-image validation split, seed
20261023, 15 epochs and validation-only checkpoint selection. See train.py for the
complete optimizer and quantization implementation. NumPy/BLAS/platform differences
can change trained float weights; the shipped model.json and its hash define the
bit-exact RTL target, not a promise of cross-platform bit-identical retraining.

No custom ISA/compiler is part of this checkpoint. CPU-driven MMIO copies and
polling are the initial integration plan. DMA is a later measured design choice.
