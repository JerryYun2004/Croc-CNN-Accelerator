set cp0c_stage $::env(CP0C_STAGE)
set cp0c_file [open "reports/cp0c-${cp0c_stage}.tsv" w]
proc cp0c_metric {key value} {
    global cp0c_file
    if {![string is double -strict $value]} {error "Nonnumeric metric $key: $value"}
    puts $cp0c_file "$key\t$value"
    flush $cp0c_file
}

if {$cp0c_stage eq "preflight"} {
    foreach command {
        initialize_floorplan global_placement detailed_placement
        clock_tree_synthesis global_route detailed_route detailed_route_num_drvs
        filler_placement estimate_parasitics report_tns_metric
        report_worst_slack_metric sta::worst_slack sta::endpoint_violation_count
        sta::max_slew_violation_count sta::max_capacitance_violation_count
        sta::max_fanout_violation_count dpl::get_row_site grt::set_verbose
    } {
        if {![llength [info commands $command]]} {error "Required API missing: $command"}
    }
    tee -file reports/cp0c-units.rpt { report_units }
    check_setup -verbose > reports/cp0c-preflight-check-setup.rpt
    foreach {clock period} {clk_sys 10.0 clk_jtg 25.0 clk_rtc 50.0} {
        set object [get_clocks $clock]
        if {[llength $object] != 1} {error "Missing or ambiguous clock $clock"}
        set actual [get_property $object period]
        if {abs($actual - $period) > 1e-6} {error "Unexpected $clock period $actual"}
        cp0c_metric "${clock}_period" $actual
    }
    cp0c_metric preflight_complete 1
} else {
    set block [ord::get_db_block]
    set dbu [expr {double([$block getDbUnitsPerMicron])}]
    set die [$block getDieArea]
    set core [$block getCoreArea]
    cp0c_metric die_width_um [expr {[$die dx] / $dbu}]
    cp0c_metric die_height_um [expr {[$die dy] / $dbu}]
    cp0c_metric core_area_um2 [expr {double([$core dx]) * [$core dy] / ($dbu*$dbu)}]
    array set area {stdcell 0.0 macro 0.0 pad 0.0 cover 0.0}
    array set count {stdcell 0 macro 0 pad 0 cover 0}
    foreach instance [$block getInsts] {
        set master [$instance getMaster]
        if {[$master isFiller]} {continue}
        if {[$master isBlock]} {set type macro} elseif {[$master isPad]} {
            set type pad
        } elseif {[$master isCover]} {set type cover} else {set type stdcell}
        set area($type) [expr {$area($type) + double([$master getWidth])*[$master getHeight]/($dbu*$dbu)}]
        incr count($type)
    }
    foreach type {stdcell macro pad cover} {
        cp0c_metric "${type}_area_um2" $area($type)
        cp0c_metric "${type}_count" $count($type)
    }
    if {$count(macro) != 2} {error "Expected two SRAM macros; found $count(macro)"}
    if {abs([$die dx]/$dbu - 1916.0) > 0.001 || abs([$die dy]/$dbu - 1916.0) > 0.001} {
        error "Unexpected die dimensions"
    }
    if {$cp0c_stage in {place cts route finish}} {
        check_placement -verbose
    }
    if {$cp0c_stage eq "route"} {
        # Must run in the SAME process as detailed_route; the router's live
        # violation count is not reconstructed by just loading an ODB later.
        cp0c_metric router_drvs [detailed_route_num_drvs]
        setDefaultParasitics
        set_propagated_clock [all_clocks]
        # Explicitly refresh global-route RC after the routing/antenna work.
        # This is an estimate, not detailed-route OpenRCX extraction.
        estimate_parasitics -global_routing
        set clean [check_setup -verbose > reports/cp0c-check-setup.rpt]
        cp0c_metric check_setup_clean $clean
        foreach corner {tt ff} {
            cp0c_metric "setup_${corner}" [sta::worst_slack -corner $corner -max]
            cp0c_metric "hold_${corner}" [sta::worst_slack -corner $corner -min]
        }
        cp0c_metric setup_violations [sta::endpoint_violation_count max]
        cp0c_metric hold_violations [sta::endpoint_violation_count min]
        cp0c_metric max_slew_violations [sta::max_slew_violation_count]
        cp0c_metric max_cap_violations [sta::max_capacitance_violation_count]
        cp0c_metric max_fanout_violations [sta::max_fanout_violation_count]
        report_tns -max -digits 6 > reports/cp0c-timing.rpt
        report_tns -min -digits 6 >> reports/cp0c-timing.rpt
        report_worst_slack -max -digits 6 >> reports/cp0c-timing.rpt
        report_worst_slack -min -digits 6 >> reports/cp0c-timing.rpt
        report_checks -path_delay min_max -group_path_count 10 \
            -format full_clock_expanded -fields {slew capacitance fanout} \
            -digits 6 >> reports/cp0c-timing.rpt
        report_checks -unconstrained -group_path_count 10 >> reports/cp0c-timing.rpt
        report_check_types -max_slew -max_capacitance -max_fanout -violators \
            >> reports/cp0c-timing.rpt
        tee -file reports/cp0c-wire-length.rpt { report_wire_length -summary -detailed_route }
    }
}
close $cp0c_file
