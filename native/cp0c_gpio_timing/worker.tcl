# Read-only diagnostic. Every case starts in a fresh OpenROAD process.
proc gt_reports {records} {
    file mkdir reports/slews reports/pads reports/arcs reports/paths
    foreach corner {tt ff} {
        # No -net filter here: retain top-level external-driver violations too.
        report_check_types -corner $corner -max_slew -max_capacitance -max_fanout \
            -violators -no_line_splits -digits 9 > reports/global-${corner}-erc.rpt
        foreach record $records {
            lassign $record port net pad
            if {![regexp {^gpio[0-9]+_io$} $port]} {continue}
            set inst "pad_$port"
            report_check_types -net $net -corner $corner -max_slew -max_capacitance \
                -max_count 100 -no_line_splits -digits 9 > reports/pads/${port}-${corner}.rpt
            foreach {suffix object} [list pad "$inst/pad" port $port data "$inst/c2p" \
                                        enable "$inst/c2p_en" receive "$inst/p2c"] {
                tee -quiet -file reports/slews/${port}-${corner}-${suffix}.rpt \
                    [list report_slews -corner $corner -digits 9 $object]
            }
            # Record all data and enable arcs for every bank bit. report_dcalc
            # is the public command in this exact OpenSTA pin.
            foreach {arc from to} [list data "$inst/c2p" "$inst/pad" \
                                     enable "$inst/c2p_en" "$inst/pad" \
                                     receive "$inst/pad" "$inst/p2c"] {
                foreach delay {min max} {
                    report_dcalc -from $from -to $to -corner $corner -$delay -digits 9 \
                        > reports/arcs/${port}-${corner}-${arc}-${delay}.rpt
                }
            }
        }
        # Separate data, receive and direction-control paths. Empty path reports
        # in a fixed mode mean absent paths, never evidence of transition safety.
        foreach {kind args} [list \
            data [list -through [get_pins pad_gpio*_io/c2p] -to [get_ports gpio*_io]] \
            enable [list -through [get_pins pad_gpio*_io/c2p_en] -to [get_ports gpio*_io]] \
            receive [list -from [get_ports gpio*_io] -through [get_pins pad_gpio*_io/p2c]]] {
            report_checks {*}$args -corner $corner -path_delay min_max -group_path_count 32 \
                -endpoint_path_count 2 -format full_clock_expanded -fields {slew capacitance fanout} \
                -digits 9 > reports/paths/${kind}-${corner}.rpt
        }
    }
}
proc gt_main {} {
    source $::env(GT_CONFIG)
    foreach name {signature.tcl cp0c_route_metrics.tcl cp0c_postroute_annotation.tcl cp0c_annotation_sections.tcl inventory.tcl} {
        source $::env(GT_HELPERS)/$name
    }
    file mkdir reports out
    set_thread_count 2
    puts "PHASE: load pinned libraries, final ODB, diagnostic SDC and existing SPEF"
    define_corners tt ff
    foreach corner {tt ff} {foreach lib $::gt_libs($corner) {read_liberty -corner $corner $lib}}
    read_db $::env(GT_ODB)
    set before [pc_signal_signature]
    # All loads/modes were set in this SDC copy BEFORE the first timing query.
    read_sdc $::env(GT_SDC)
    set_propagated_clock [all_clocks]
    ft_require [expr {[llength [all_clocks]]==3}] "Unexpected clock count"
    foreach corner {tt ff} {read_spef -corner $corner $::env(GT_SPEF)}
    lassign [ft_inventory] ports records
    ft_require [expr {[llength [get_pins pad_gpio*_io/c2p_en]]==32}] "GPIO enable inventory differs"
    foreach record $records {
        lassign $record port net pad
        if {[regexp {^gpio[0-9]+_io$} $port]} {
            foreach pin {pad c2p c2p_en p2c} {
                ft_require [expr {[llength [get_pins pad_${port}/$pin]]==1}] "Missing GPIO pin"
            }
        }
    }
    tee -file reports/units.rpt {report_units}
    write_sdc out/runtime.sdc
    puts "PHASE: compute timing once with final diagnostic constraints"
    erc_snapshot case
    ft_annotation
    report_check_types -min_pulse_width -min_period -violators -digits 9 > reports/clock-checks.rpt
    puts "PHASE: collect both GPIO vertices, data/enable/receive arcs and path reports"
    gt_reports $records
    ft_require [expr {[pc_signal_signature] eq $before}] "Physical design changed"
    set f [open reports/physical.tsv w]
    puts $f "signal_signature_unchanged\t1\nsaved_design_written\t0"
    close $f
    puts "CP0C_GPIO_TIMING_WORKER_COMPLETE $::env(GT_CASE)"
}
if {[catch {gt_main} message options]} {
    puts stderr "CP0C_GPIO_TIMING_WORKER_FAILED: $message"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    exit 1
}
exit 0
