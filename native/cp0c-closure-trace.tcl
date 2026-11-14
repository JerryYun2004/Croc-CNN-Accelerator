# Read-only probe of the preserved candidate and its already-generated SPEF.
proc ct_require {ok msg} {if {!$ok} {error $msg}}
proc ct_name {pin} {return "[[$pin getInst] getName]/[[$pin getMTerm] getName]"}
proc ct_box {box} {return [list [$box xMin] [$box yMin] [$box xMax] [$box yMax]]}
proc ct_index {wanted} {
    set index [dict create]
    foreach pin [get_pins -hierarchical *] {
        if {[$pin is_hierarchical]} {continue}
        set dbpin [sta::sta_to_db_pin $pin]
        if {$dbpin eq "NULL"} {continue}
        foreach name [lsort -unique [list [sta::get_full_name $pin] [ct_name $dbpin]]] {
            if {![dict exists $wanted $name]} {continue}
            if {[dict exists $index $name]} {
                ct_require [expr {[dict get $index $name] eq $dbpin}] "Ambiguous pin alias $name"
            }
            dict set index $name $dbpin
        }
    }
    foreach name [dict keys $wanted] {ct_require [dict exists $index $name] "Unresolved target $name"}
    return $index
}
proc ct_inventory {} {
    set f [open "$::env(CP0T_INPUT)/targets.tsv" r]
    gets $f header
    ct_require [expr {$header eq "kind\tdriver\tload"}] "Unexpected targets header"
    set rows {}; set wanted [dict create]
    while {[gets $f line] >= 0} {
        if {$line eq ""} {continue}
        set row [split $line "\t"]
        ct_require [expr {[llength $row] == 3}] "Malformed target row"
        lassign $row kind driver load
        foreach name [list $driver $load] {if {$name ne ""} {dict set wanted $name 1}}
        lappend rows $row
    }
    close $f
    set index [ct_index $wanted]
    set selected [dict create]
    set f [open reports/target-map.tsv w]
    puts $f "kind\treported_driver\treported_load\tphysical_driver\tphysical_load\tnet"
    foreach row $rows {
        lassign $row kind driver load
        set dp ""; set lp ""; set net "NULL"
        if {$driver ne ""} {set dp [dict get $index $driver]; set net [$dp getNet]}
        if {$load ne ""} {
            set lp [dict get $index $load]
            if {$net eq "NULL"} {set net [$lp getNet]} else {
                ct_require [expr {$net eq [$lp getNet]}] "Driver/load net mismatch: $driver / $load"
            }
        }
        ct_require [expr {$net ne "NULL" && ![$net isSpecial]}] "Expected a regular signal net"
        dict set selected [$net getName] $net
        set dn ""; set ln ""
        if {$dp ne ""} {set dn [ct_name $dp]}
        if {$lp ne ""} {set ln [ct_name $lp]}
        puts $f [join [list $kind $driver $load $dn $ln [$net getName]] "\t"]
    }
    close $f
    set f [open reports/net-pins.tsv w]
    puts $f "net\tpin\tinstance\tport\tmaster\tdirection\tis_diode\tinst_dont_touch\tnet_dont_touch\tconnected_flag\twire_junction_id\txmin_dbu\tymin_dbu\txmax_dbu\tymax_dbu"
    set n [open reports/nets.tsv w]
    puts $n "net\todb_id\twire\tordered_flag\twire_length_dbu\twire_entries\tbterms\tinputs\toutputs\tdiodes\tdbu_per_um"
    foreach name [lsort [dict keys $selected]] {
        set net [dict get $selected $name]
        set wire [$net getWire]
        set has_wire [expr {$wire ne "NULL"}]
        set ordered -1; set length 0; set entries 0
        if {$has_wire} {set ordered [$wire isOrdered]; set length [$wire getLength]; set entries [$wire length]}
        set inputs 0; set outputs 0; set diodes 0
        foreach pin [$net getITerms] {
            set inst [$pin getInst]; set master [$inst getMaster]
            set diode [expr {[$master getType] eq "CORE_ANTENNACELL"}]
            set direction [$pin getIoType]
            if {$direction eq "INPUT"} {incr inputs}
            if {$direction eq "OUTPUT"} {incr outputs}
            if {$diode} {incr diodes}
            set jid 0
            if {$has_wire} {set jid [$wire getTermShapeJunctionId [$pin getId]]}
            puts $f [join [list $name [ct_name $pin] [$inst getName] [[$pin getMTerm] getName] \
                [$master getName] $direction $diode [$inst isDoNotTouch] [$net isDoNotTouch] \
                [$pin isConnected] $jid {*}[ct_box [$pin getBBox]]] "\t"]
        }
        puts $n [join [list $name [$net getId] $has_wire $ordered $length $entries \
            [llength [$net getBTerms]] $inputs $outputs $diodes [[ord::get_db_block] getDbUnitsPerMicron]] "\t"]
        tee -quiet -append -file reports/target-nets.rpt \
            [list sta::report_net1 [sta::db_net_to_sta $net] [sta::cmd_scene] 9]
    }
    close $f; close $n
    set f [open reports/trace-counts.tsv w]
    puts $f "targets\t[llength $rows]\nresolved_pins\t[dict size $index]\nselected_nets\t[dict size $selected]"
    close $f
}
if {[catch {
    source scripts/startup.tcl
    source $::env(CP0T_HELPERS)/cp0c_pg_audit.tcl
    source $::env(CP0T_HELPERS)/cp0c_route_metrics.tcl
    source $::env(CP0T_HELPERS)/cp0c_annotation_sections.tcl
    set_thread_count 2
    read_db $::env(CP0T_ODB)
    read_sdc $::env(CP0T_SDC)
    set_propagated_clock [all_clocks]
    foreach corner {tt ff} {read_spef -corner $corner $::env(CP0T_SPEF)}
    tee -file reports/units.rpt {report_units}
    set original [pc_signal_signature]
    set targets [pc_targets]
    ct_require [expr {[pc_pg_audit initial $targets] == 0}] "Initial supply assignments failed"
    check_placement -verbose
    ct_require [expr {[check_antennas -report_file reports/antenna-before.rpt] == 0}] "Antenna findings"
    erc_snapshot final
    report_parasitic_annotation -report_unannotated > reports/parasitic-annotation.rpt
    set f [open reports/parasitic-annotation.rpt r]
    set annotation [pa_annotation_sections [read $f]]
    close $f
    set f [open reports/observed-partial.tsv w]
    puts $f "driver\tmissing_load"
    foreach row [dict get $annotation loads] {puts $f [join $row "\t"]}
    close $f
    set f [open reports/annotation-counts.tsv w]
    puts $f "full\t[llength [dict get $annotation full]]\npartial\t[llength [dict get $annotation partial]]\nloads\t[llength [dict get $annotation loads]]"
    close $f
    ct_inventory
    ct_require [expr {[pc_signal_signature] eq $original}] "Cell placement, master or signal connections changed"
    ct_require [expr {[pc_pg_audit final $targets] == 0}] "Final supply assignments failed"
    ct_require [expr {[check_antennas -report_file reports/antenna-after.rpt] == 0}] "Antenna findings changed"
    set f [open reports/physical.tsv w]
    puts $f "physical_signature_unchanged\t1\nantenna_violating_nets\t0\nsupply_assignment_findings\t0"
    close $f
} msg options]} {
    puts stderr "CP0C_CLOSURE_TRACE_FAILED: $msg"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    exit 1
}
puts "CP0C_CLOSURE_TRACE_COMPLETE"
exit 0
