# Draft technical questions — not submitted

Subject: SG13CMOS5L GPIO disable transition tables and SS capacitance domain

We are reviewing `sg13cmos5l_IOPadInOut30mA` at PDK commit
`5958eb38973d5c56e7a26f65fc23d9f3a740ccd2` using OpenROAD
`c463e998615210dc322c999fc9a9f56885dbd664` / OpenSTA
`f7febae35ba9675f4ff08839623e3aa8d319ce58`.

A one-pad native reproducer with explicit input transitions and zero wire
capacitance matches an independent bilinear interpolation of the TT/FF Liberty
tables. At TT, external load 3 pF and enable transition 0.33 ns, the 0Z/1Z
transition reports are 1.387124 / 1.982774 ns. The inherited output maximum
transition is 1.2 ns. Data and enable-on transitions meet that limit in this case.

The disable delay tables equal the corresponding transition tables at every
entry in TT, FF and SS. Their nearly load-independent behavior and the generic
transition check motivate these questions; we are not assuming a model error.

The SS file `sg13cmos5l_io_slow_1p08V_3p0V_125C.lib` additionally specifies
0.668662 pF maximum capacitance for the GPIO, while its data tables begin at
1 pF. Its smallest tabulated data falling transition is 1.31851 ns, above the
1.2 ns inherited maximum transition. No native SS or transistor characterization
has been performed in this reproducer.

Questions:

1. What characterization/measurement procedure defines the 0Z/1Z delay and
   transition tables, including release threshold and load/termination?
2. Is equality between the disable delay and transition tables intentional?
   How should a 1.2 ns generic max-transition check be applied to these arcs?
3. Is the SS 0.668662 pF maximum capacitance intentional, and what supported
   characterization covers its range below the table's 1 pF lower bound?
4. Is a revised qualified model or a documented checking procedure available?

The minimal fixture is the previous `Croc_CP0C_Pad_Model` package. The native
evidence is `cp0c-pad-model-20261023-001400-94eq6zzc`. The current contract-audit
package includes exact unmodified Liberty snapshots and computed table bounds.
No model changes, timing waivers or physical design edits have been applied.
