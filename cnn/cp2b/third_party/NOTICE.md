# Upstream provenance

All `.sv`, `.svh` and `.v` files under this directory are unchanged copies from
the frozen Croc source at commit `29b2e748e72c592a5a895c2b11fa85bc9080f67a`.
Their SHA-256 values were compared against CP0D's `files.json` and are recorded,
with original repository paths, in `../pins.json`. The Mac runner verifies those
same source files in the user's checkout before compiling the bundled copies.

Source: https://github.com/pulp-platform/croc/tree/29b2e748e72c592a5a895c2b11fa85bc9080f67a

The IHP SRAM models carry Apache-2.0 notices. The Croc/OBI/common-cells sources
carry the license notices preserved in each file (Solderpad Hardware License
0.51 or Apache-2.0 as specified there). This package does not redistribute PDK
Liberty, LEF or GDS files; the runner reads the user's existing pinned technology
files. No network download is needed on the Mac.

The proposed `../integration/user_domain.sv` is a modified copy of the frozen
user domain, with the original license header preserved. It instantiates the
new CNN peripheral in place of the placeholder error subordinate.
