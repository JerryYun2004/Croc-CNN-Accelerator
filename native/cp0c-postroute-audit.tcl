# Reload the routed candidate in a fresh process; no physical repair/rerouting.
proc pr_require {ok message} {if {!$ok} {error $message}}
proc pr_inventory {} {
    set f [open reports/diode-nets.tsv w]
    puts $f "net\tdrivers\tother_input_iterms\tdiode_input_iterms\tdiodes\twire_object\tguides"
    set p [open reports/diode-connections.tsv w]
    puts $p "net\tinstance\tmaster\tpin\tdirection\tis_diode"
    set total_diodes 0
    foreach inst [[ord::get_db_block] getInsts] {
        if {[[$inst getMaster] getType] eq "CORE_ANTENNACELL"} {incr total_diodes}
    }
    set nets 0
    foreach net [[ord::get_db_block] getNets] {
        if {[$net getSigType] in {POWER GROUND}} {continue}
        set diodes {}; set drivers {}; set rows {}; set ordinary_loads 0; set diode_loads 0
        foreach pin [$net getITerms] {
            set inst [$pin getInst]; set m [$inst getMaster]
            set is_diode [expr {[$m getType] eq "CORE_ANTENNACELL"}]
            set name "[$inst getName]/[[$pin getMTerm] getName]"
            set direction [$pin getIoType]
            if {$is_diode} {lappend diodes [$inst getName]}
            if {$direction in {INPUT INOUT}} {
                if {$is_diode} {incr diode_loads} else {incr ordinary_loads}
            }
            if {$direction in {OUTPUT INOUT}} {lappend drivers $name}
            lappend rows [list [$net getName] [$inst getName] [$m getName] [[$pin getMTerm] getName] $direction $is_diode]
        }
        if {[llength $diodes] == 0} {continue}
        incr nets
        foreach row $rows {puts $p [join $row "\t"]}
        puts $f [join [list [$net getName] [join $drivers {;}] $ordinary_loads $diode_loads \
            [join $diodes {;}] [expr {[$net getWire] ne "NULL"}] [llength [$net getGuides]]] "\t"]
    }
    close $f; close $p
    return [list $total_diodes $nets]
}
if {[catch {
    source scripts/startup.tcl
    source $::env(CP0Q_HELPERS)/cp0c_pg_audit.tcl
    source $::env(CP0Q_HELPERS)/cp0c_route_metrics.tcl
    source $::env(CP0Q_HELPERS)/cp0c_postroute_annotation.tcl
    source $::env(CP0Q_HELPERS)/cp0c_annotation_sections.tcl
    set_thread_count 2
    read_db $::env(CP0Q_ODB)
    read_sdc $::env(CP0Q_SDC)
    set_propagated_clock [all_clocks]
    tee -file reports/units.rpt {report_units}
    foreach {clock period} {clk_sys 10.0 clk_jtg 25.0 clk_rtc 50.0} {
        set object [get_clocks $clock]
        pr_require [expr {[llength $object] == 1 && abs([get_property $object period]-$period) < 1e-6}] "Unexpected clock: $clock"
    }
    set targets [pc_targets]
    pr_require [expr {[pc_pg_audit initial $targets] == 0}] "Supply assignment findings"
    check_placement -verbose
    set original [pc_signal_signature]
    set antenna [check_antennas -report_file reports/antenna-before.rpt]
    pr_require [expr {$antenna == 0}] "Reloaded candidate is not antenna-clean"
    set routed [design_is_routed -verbose]
    lassign [pr_inventory] diode_count diode_nets
    set f [open reports/physical.tsv w]
    puts $f "antenna_violating_nets\t$antenna\nsupply_assignment_findings\t0\ndesign_has_routing\t$routed\nantenna_cells\t$diode_count\ndiode_nets\t$diode_nets"
    close $f
    if {$::env(CP0Q_MODE) eq "saved_spef"} {
        foreach corner {tt ff} {read_spef -corner $corner "$::env(CP0Q_INPUT)/estimated_${corner}"}
    } elseif {$::env(CP0Q_MODE) eq "fresh_guides"} {
        # No old SPEF, no repair_antennas, global_route or detailed_route.
        # have_routes initializes the route/pin model from this ODB's saved guides.
        setDefaultParasitics
        set_global_routing_layer_adjustment TopMetal1 0.20
        set_routing_layers -signal Metal2-TopMetal1 -clock Metal2-TopMetal1
        pr_require [grt::have_routes] "Saved guide coverage is insufficient for RC estimation"
        tee -file reports/layer-rc.rpt {report_layer_rc}
        estimate_parasitics -global_routing -spef_file out/estimated
    } else {error "Unknown audit mode"}
    # Query timing before using endpoint counters (pinned OpenSTA requirement).
    erc_snapshot final
    report_checks -path_delay min -group_path_count 20 -slack_max 0 \
        -format full_clock_expanded -fields {slew capacitance fanout} -digits 9 > reports/hold-violations.rpt
    set setup_clean [check_setup -verbose > reports/check-setup.rpt]
    report_parasitic_annotation -report_unannotated > reports/parasitic-annotation.rpt
    set f [open reports/parasitic-annotation.rpt r]
    set annotation [pa_annotation_sections [read $f]]
    close $f
    set names [dict get $annotation full]
    set n [llength $names]
    set partial_names [dict get $annotation partial]
    set partial_loads [dict get $annotation loads]
    set f [open reports/partially-unannotated-drivers.txt w]
    foreach name $partial_names {puts $f $name}
    close $f
    set f [open reports/partially-unannotated-loads.tsv w]
    puts $f "driver\tmissing_load"
    foreach row $partial_loads {puts $f [join $row "\t"]}
    close $f
    rc_inventory $names
    pr_require [expr {[pc_signal_signature] eq $original}] "Cell placement, masters or connections changed during the audit"
    pr_require [expr {[check_antennas -report_file reports/antenna-after.rpt] == 0}] "Antenna count changed during the audit"
    pr_require [expr {[pc_pg_audit final $targets] == 0}] "Final supply assignment findings"
    set f [open reports/audit.tsv w]
    puts $f "check_setup_clean\t$setup_clean\nunannotated_drivers\t$n\npartially_unannotated_drivers\t[llength $partial_names]\npartially_unannotated_loads\t[llength $partial_loads]\nphysical_signature_unchanged\t1"
    close $f
} message options]} {
    puts stderr "CP0C_POSTROUTE_AUDIT_FAILED: $message"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    exit 1
}
puts "CP0C_POSTROUTE_AUDIT_COMPLETE $::env(CP0Q_MODE)"
exit 0
