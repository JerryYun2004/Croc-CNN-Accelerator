# Diagnostic extraction from actual wires. Never save a modified physical DB.
proc rr_require {ok message} {if {!$ok} {error $message}}
proc rr_lef_values {} {
    set f [open reports/lef-rc.tsv w]
    puts $f "layer\trouting_level\twidth_dbu\tresistance_ohm_per_square_or_cut\tarea_cap_pf_per_um2\tedge_cap_pf_per_um"
    set names {}
    foreach layer [[ord::get_db_tech] getLayers] {
        set level [$layer getRoutingLevel]
        set name [$layer getName]
        if {$level > 0} {
            lappend names $name
            rr_require [expr {[$layer getWidth] > 0 && [$layer getResistance] > 0 &&
                ([$layer getCapacitance] > 0 || [$layer getEdgeCapacitance] > 0)}] "Missing LEF RC for $name"
        }
        puts $f [join [list $name $level [$layer getWidth] [$layer getResistance] \
            [$layer getCapacitance] [$layer getEdgeCapacitance]] "\t"]
    }
    close $f
    rr_require [expr {[lsort $names] eq [lsort {Metal1 Metal2 Metal3 Metal4 TopMetal1}]}] "Expected the SG13CMOS5L five-metal stack"
}
if {[catch {
    source scripts/startup.tcl
    source $::env(CP0R_HELPERS)/cp0c_pg_audit.tcl
    source $::env(CP0R_HELPERS)/cp0c_route_metrics.tcl
    source $::env(CP0R_HELPERS)/cp0c_postroute_annotation.tcl
    source $::env(CP0R_HELPERS)/cp0c_annotation_sections.tcl
    set_thread_count 2
    read_db $::env(CP0R_ODB)
    read_sdc $::env(CP0R_SDC)
    set_propagated_clock [all_clocks]
    tee -file reports/units.rpt {report_units}
    foreach {clock period} {clk_sys 10.0 clk_jtg 25.0 clk_rtc 50.0} {
        set object [get_clocks $clock]
        rr_require [expr {[llength $object] == 1 && abs([get_property $object period]-$period) < 1e-6}] "Unexpected clock: $clock"
    }
    set targets [pc_targets]
    rr_require [expr {[pc_pg_audit initial $targets] == 0}] "Initial supply assignments failed"
    check_placement -verbose
    set original [pc_signal_signature]
    rr_require [expr {[check_antennas -report_file reports/antenna-before.rpt] == 0}] "Candidate is not antenna-clean"
    set routed [design_is_routed -verbose]
    rr_require [expr {$routed == 1}] "Candidate has nets without routing"
    rr_lef_values
    set mode $::env(CP0R_MODE)
    if {$mode eq "extract"} {
        foreach command {extract_parasitics write_spef} {
            rr_require [expr {[llength [info commands $command]] == 1}] "Missing command $command"
        }
        puts "CP0C_ROUTED_RC_EXTRACT_START LEF_RC approximate single interconnect corner"
        # Pinned OpenRCX v2 creates its own LEF_RC process corner. No rules file.
        # This is the option set in its gcd_v2_lef_rc_max_res_0 regression.
        # orderWires runs internally; only the disposable in-memory DB is affected.
        extract_parasitics -version 2.0 -lef_rc -skip_over_cell \
            -no_merge_via_res -max_res 0 -coupling_threshold 0.1
        write_spef out/routed-lef.spef
        rr_require [expr {[file exists out/routed-lef.spef] && [file size out/routed-lef.spef] > 1024}] "Missing or empty SPEF"
    } elseif {$mode eq "replay"} {
        # A fresh process, original ODB, no guide/placement estimate or fallback.
        # TT/FF below are CELL corners; both use this one LEF interconnect model.
        foreach corner {tt ff} {read_spef -corner $corner $::env(CP0R_SPEF)}
        erc_snapshot final
        report_checks -path_delay min -group_path_count 20 -slack_max 0 \
            -format full_clock_expanded -fields {slew capacitance fanout} -digits 9 > reports/hold-violations.rpt
        report_checks -path_delay max -group_path_count 20 -slack_max 0 \
            -format full_clock_expanded -fields {slew capacitance fanout} -digits 9 > reports/setup-violations.rpt
        set clean [check_setup -verbose > reports/check-setup.rpt]
        report_parasitic_annotation -report_unannotated > reports/parasitic-annotation.rpt
        set f [open reports/parasitic-annotation.rpt r]
        set annotation [pa_annotation_sections [read $f]]
        close $f
        set names [dict get $annotation full]
        set partial [dict get $annotation partial]
        set loads [dict get $annotation loads]
        rc_inventory $names
        set f [open reports/partially-unannotated-drivers.txt w]
        foreach name $partial {puts $f $name}
        close $f
        set f [open reports/partially-unannotated-loads.tsv w]
        puts $f "driver\tmissing_load"
        foreach row $loads {puts $f [join $row "\t"]}
        close $f
        set f [open reports/audit.tsv w]
        puts $f "check_setup_clean\t$clean\nunannotated_drivers\t[llength $names]\npartially_unannotated_drivers\t[llength $partial]\npartially_unannotated_loads\t[llength $loads]"
        close $f
    } else {error "Unknown mode $mode"}
    rr_require [expr {[pc_signal_signature] eq $original}] "Cell masters, placement or connectivity changed"
    rr_require [expr {[check_antennas -report_file reports/antenna-after.rpt] == 0}] "Antenna findings after $mode"
    rr_require [expr {[pc_pg_audit final $targets] == 0}] "Final supply assignments failed"
    set f [open reports/physical.tsv w]
    puts $f "antenna_violating_nets\t0\nsupply_assignment_findings\t0\ndesign_has_routing\t$routed\nphysical_signature_unchanged\t1"
    close $f
} message options]} {
    puts stderr "CP0C_ROUTED_RC_FAILED: $message"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    exit 1
}
puts "CP0C_ROUTED_RC_COMPLETE $::env(CP0R_MODE)"
exit 0
