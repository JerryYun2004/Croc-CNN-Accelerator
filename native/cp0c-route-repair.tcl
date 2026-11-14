# Continue the repaired candidate through detailed routing and fillers.
proc cr_require {ok message} {if {!$ok} {error $message}}
proc cr_signal_signature {instances} {
    set rows {}
    foreach inst $instances {
        lappend rows [list I [$inst getId] [[$inst getMaster] getName]]
        foreach pin [$inst getITerms] {
            if {[$pin getSigType] ni {POWER GROUND}} {
                lappend rows [list P [$pin getId] [pc_net_name $pin]]
            }
        }
    }
    foreach pin [[ord::get_db_block] getBTerms] {lappend rows [list B [$pin getName] [pc_net_name $pin]]}
    return [lsort $rows]
}
proc cr_core_gate {prefix} {
    set f [open "reports/${prefix}-erc.rpt" r]
    set errors {}
    foreach line [split [read $f] "\n"] {
        if {[string first {(VIOLATED)} $line] < 0} {continue}
        set pin [lindex $line 0]
        if {![string match gpio* $pin] && ![string match pad_* $pin]} {lappend errors $line}
    }
    close $f
    cr_require [expr {[llength $errors] == 0}] "Internal ERC findings at $prefix: $errors"
    cr_require [expr {[sta::endpoint_violation_count max] == 0 && [sta::endpoint_violation_count min] == 0}] "Setup/hold violation at $prefix"
    set clean [check_setup -verbose > "reports/${prefix}-check-setup.rpt"]
    cr_require [expr {$clean == 1}] "check_setup findings at $prefix"
}
if {[catch {
    source scripts/startup.tcl
    source $::env(CP0D_HELPERS)/cp0c_pg_audit.tcl
    source $::env(CP0D_HELPERS)/cp0c_route_metrics.tcl
    foreach command {global_route repair_antennas detailed_route detailed_route_num_drvs check_antennas filler_placement write_def write_db write_verilog} {
        cr_require [expr {[llength [info commands $command]] == 1}] "Missing command: $command"
    }
    set_thread_count 2
    read_db $::env(CP0D_ODB)
    read_sdc $::env(CP0D_SDC)
    set_propagated_clock [all_clocks]
    tee -file reports/units.rpt {report_units}
    set targets [pc_targets]
    cr_require [expr {[pc_pg_audit initial $targets] == 0}] "Initial supply assignments failed"
    check_placement -verbose
    set block [ord::get_db_block]
    set instances [$block getInsts]
    set original_signals [cr_signal_signature $instances]
    set macro_positions [dict create]
    foreach inst $instances {
        set m [$inst getMaster]
        if {[$m isBlock] || [$m isPad] || [$m isCover]} {
            dict set macro_positions [$inst getId] [list $inst [$inst getLocation] [$inst getOrient]]
        }
    }
    foreach {clock period} {clk_sys 10.0 clk_jtg 25.0 clk_rtc 50.0} {
        set object [get_clocks $clock]
        cr_require [expr {[llength $object] == 1 && abs([get_property $object period]-$period) < 1e-6}] "Unexpected clock: $clock"
    }
    if {$::env(CP0D_STAGE) eq "preflight"} {
        foreach corner {tt ff} {read_spef -corner $corner "$::env(CP0D_SPEF_DIR)/estimated_${corner}"}
        erc_snapshot preflight
        cr_core_gate preflight
        report_parasitic_annotation -report_unannotated > reports/parasitic-annotation.rpt
    } elseif {$::env(CP0D_STAGE) eq "route"} {
        # This is a fresh process: no saved SPEF annotation is loaded here.
        setDefaultParasitics
        set_dont_use $dont_use_cells
        set_global_routing_layer_adjustment TopMetal1 0.20
        set_routing_layers -signal Metal2-TopMetal1 -clock Metal2-TopMetal1
        global_route -guide_file reports/initial.guide
        estimate_parasitics -global_routing
        erc_snapshot grt
        cr_core_gate grt
        repair_antennas -ratio_margin 30 -iterations 5
        check_placement -verbose
        global_connect
        cr_require [expr {[pc_pg_audit antenna $targets] == 0}] "Supply assignments failed after antenna repair"
        estimate_parasitics -global_routing
        erc_snapshot antenna
        cr_core_gate antenna
        write_db out/antenna_ready.odb
        write_sdc out/antenna_ready.sdc
        puts "CP0C_REPAIRED_ROUTE_START detailed_route threads=2"
        detailed_route -output_drc reports/route-drc.rpt -drc_report_iter_step 5 \
            -save_guide_updates -clean_patches -verbose 1
        set drvs [detailed_route_num_drvs]
        set ant_count [check_antennas -report_file reports/route-antenna.rpt]
        cr_require [expr {[string is integer -strict $drvs] && [string is integer -strict $ant_count]}] "Nonnumeric routing/antenna count"
        write_db out/routed.odb
        filler_placement $stdfill
        global_connect
        check_placement -verbose
        set pg_findings [pc_pg_audit final $targets]
        cr_require [expr {$pg_findings == 0}] "Final supply assignments failed"
        cr_require [expr {[cr_signal_signature $instances] eq $original_signals}] "Existing signal connectivity or cell masters changed"
        dict for {id spec} $macro_positions {
            lassign $spec inst location orient
            cr_require [expr {[$inst getLocation] eq $location && [$inst getOrient] eq $orient}] "Macro/pad moved: [$inst getName]"
        }
        # Preserve physical artifacts before refreshing the estimated RC model.
        write_def out/croc_repaired.def
        write_db out/croc_repaired.odb
        write_sdc out/croc_repaired.sdc
        write_verilog out/croc_repaired.v
        write_verilog -include_pwr_gnd -remove_cells "$stdfill bondpad*" out/croc_repaired_lvs.v
        set f [open reports/physical.tsv w]
        puts $f "router_drvs\t$drvs\nantenna_violating_nets\t$ant_count\nsupply_assignment_findings\t$pg_findings"
        close $f
        puts "CP0C_REPAIRED_PHYSICAL_OUTPUTS_WRITTEN"
        # Same estimation method as the original CP0C flow, not OpenRCX extraction.
        estimate_parasitics -global_routing -spef_file out/estimated
        erc_snapshot final
        report_parasitic_annotation -report_unannotated > reports/parasitic-annotation.rpt
        tee -file reports/wire-length.rpt {report_wire_length -summary -detailed_route}
        cr_core_gate final
        cr_require [expr {$drvs == 0 && $ant_count == 0}] "Detailed-router or antenna findings remain: DRVs=$drvs antenna_nets=$ant_count"
    } else {error "Unknown continuation stage"}
} message options]} {
    puts stderr "CP0C_REPAIRED_ROUTE_FAILED: $message"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    exit 1
}
puts "CP0C_REPAIRED_STAGE_COMPLETE $::env(CP0D_STAGE)"
exit 0
