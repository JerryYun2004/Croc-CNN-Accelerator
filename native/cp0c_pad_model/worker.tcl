# Every invocation builds a new one-pad model; no Croc database is opened.
proc pm_assert {condition message} {if {!$condition} {error $message}}
proc pm_main {} {
    source $::env(PM_CONFIG)
    file mkdir reports/arcs reports/paths reports/slews out
    set_thread_count 2
    puts "PHASE: read pinned I/O libraries and LEFs"
    define_corners tt ff
    foreach corner {tt ff} {read_liberty -corner $corner $::pm_lib($corner)}
    read_lef $::pm_tech
    read_lef $::pm_io_lef
    read_verilog $::env(PM_HELPERS)/pad_probe.v
    link_design pad_probe
    set block [ord::get_db_block]
    pm_assert [expr {[llength [$block getInsts]]==1}] "Expected exactly one instance"
    set inst [lindex [$block getInsts] 0]
    pm_assert [expr {[[$inst getMaster] getName] eq "sg13cmos5l_IOPadInOut30mA"}] "Wrong pad master"
    foreach pin {c2p c2p_en pad p2c} {
        pm_assert [expr {[llength [get_pins u_pad/$pin]]==1}] "Missing pad pin"
    }
    # A virtual clock gives the combinational fixture defined arrival/required
    # times. Its period is a test convenience, not an accepted Croc requirement.
    create_clock -name fixture_clock -period 20
    set_input_delay 0 -clock fixture_clock [get_ports {data_i en_i pad_io}]
    set_output_delay 0 -clock fixture_clock [get_ports {pad_io recv_o}]
    set_input_transition $::pm_slew [get_ports {data_i en_i}]
    set_input_transition 0.2 [get_ports pad_io]
    # Explicit zero net wire capacitance overrides the library wireload model.
    # This is a lumped cell model, not extracted on-chip/package/board wiring.
    set_load 0 [get_nets *]
    set_load -pin_load $::pm_load [get_ports pad_io]
    set_load -pin_load 0.05 [get_ports recv_o]
    if {$::pm_oe ne "dynamic"} {set_case_analysis $::pm_oe [get_ports en_i]}
    tee -quiet -file reports/units.rpt {report_units}
    write_sdc out/runtime.sdc
    puts "PHASE: compute data, enable and receive paths"
    foreach corner {tt ff} {
        foreach {kind options} [list \
            data [list -through [get_pins u_pad/c2p] -to [get_ports pad_io]] \
            enable [list -through [get_pins u_pad/c2p_en] -to [get_ports pad_io]] \
            receive [list -from [get_ports pad_io] -through [get_pins u_pad/p2c]]] {
            report_checks {*}$options -corner $corner -path_delay min_max -group_path_count 8 \
                -endpoint_path_count 4 -format full_clock_expanded -fields {slew capacitance fanout} \
                -digits 9 > reports/paths/${kind}-${corner}.rpt
        }
        report_check_types -corner $corner -max_slew -max_capacitance -violators \
            -no_line_splits -digits 9 > reports/${corner}-erc.rpt
        # A complete net report supplies both MET and VIOLATED pad checks.
        report_check_types -net pad_io -corner $corner -max_slew -max_capacitance \
            -max_count 100 -no_line_splits -digits 9 > reports/${corner}-pad-checks.rpt
        foreach pin {u_pad/pad u_pad/c2p u_pad/c2p_en u_pad/p2c pad_io} {
            set name [string map {/ _} $pin]
            tee -quiet -file reports/slews/${corner}-${name}.rpt \
                [list report_slews -corner $corner -digits 9 $pin]
        }
        # These reports describe structural arcs, not whether a fixed mode
        # propagates a path. Only dynamic-mode arc calculations are oracle-gated.
        foreach {kind from} {data c2p enable c2p_en} {
            report_dcalc -from u_pad/$from -to u_pad/pad -corner $corner -max -digits 9 \
                > reports/arcs/${corner}-${kind}.rpt
        }
    }
    check_setup -verbose > reports/check-setup.rpt
    puts "CP0C_PAD_MODEL_WORKER_COMPLETE $::env(PM_CASE)"
}
if {[catch {pm_main} message options]} {
    puts stderr "CP0C_PAD_MODEL_WORKER_FAILED: $message"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    exit 1
}
exit 0
