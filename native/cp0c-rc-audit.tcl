# Classify missing parasitic annotation. Read-only physical database audit.
proc rc_require {ok message} {if {!$ok} {error $message}}
proc rc_null {object} {expr {$object eq "NULL" || $object eq ""}}
proc rc_pad_master {name} {
    expr {[string match sg13cmos5l_IOPad* $name] || $name eq "bondpad5l_70x70"}
}
proc rc_category {connected other_sta_loads other_db_loads bterms pad_only} {
    if {!$connected} {return NO_NET}
    if {$other_sta_loads == 0 && $other_db_loads == 0} {return NO_OTHER_LOADS}
    if {$bterms > 0 && $pad_only} {return LOADED_PAD_NET}
    return LOADED_INTERNAL_OR_OTHER
}
proc rc_physical_pin {pin} {
    if {[$pin is_top_level_port]} {
        set dbpin [sta::sta_to_db_port [$pin port]]
        rc_require [expr {![rc_null $dbpin]}] "No physical top-level pin"
        return [list $dbpin [$dbpin getName]]
    }
    set dbpin [sta::sta_to_db_pin $pin]
    rc_require [expr {![rc_null $dbpin]}] "No physical pin for [sta::get_full_name $pin]"
    return [list $dbpin "[[$dbpin getInst] getName]/[[$dbpin getMTerm] getName]"]
}
proc rc_driver_index {names} {
    # Enumerate objects once. Never resolve a reported name through find_pin:
    # escaped register names need not round-trip through that path parser.
    set wanted [dict create]
    foreach name $names {dict set wanted $name 1}
    set pins [get_pins -hierarchical *]
    foreach port [get_ports *] {lappend pins [sta::get_port_pin $port]}
    set index [dict create]
    set f [open reports/pin-resolution.tsv w]
    puts $f "reported_name\tsta_full_name\tphysical_name\tdb_id"
    foreach pin $pins {
        if {![$pin is_driver] || [$pin is_hierarchical]} {continue}
        set full_name [sta::get_full_name $pin]
        lassign [rc_physical_pin $pin] dbpin physical_name
        # Both forms must identify the same physical object; ambiguous aliases fail.
        foreach name [lsort -unique [list $full_name $physical_name]] {
            if {![dict exists $wanted $name]} {continue}
            if {[dict exists $index $name]} {
                rc_require [expr {[dict get $index $name] eq $pin}] "Ambiguous driver name: $name"
            } else {
                dict set index $name $pin
                puts $f [join [list $name $full_name $physical_name [$dbpin getId]] "\t"]
            }
        }
    }
    close $f
    set missing {}
    foreach name $names {if {![dict exists $index $name]} {lappend missing $name}}
    rc_require [expr {[llength $missing] == 0}] "Unresolved annotation drivers: $missing; inspect reports/pin-resolution.tsv"
    return $index
}
proc rc_inventory {names} {
    set index [rc_driver_index $names]
    set f [open reports/unannotated.tsv w]
    puts $f [join {driver category net master sta_other_loads db_other_loads bterms iterms wire_object special_wire_shapes guides} "\t"]
    set p [open reports/connections.tsv w]
    puts $p "driver\tnet\tconnected_pin\tmaster\tsta_driver\tsta_load"
    set seen_nets [dict create]
    foreach name $names {
        set pin [dict get $index $name]
        lassign [rc_physical_pin $pin] dbpin physical_name
        if {[$pin is_top_level_port]} {
            set master TOP_PORT
        } else {
            set master [[[$dbpin getInst] getMaster] getName]
        }
        rc_require [expr {![rc_null $dbpin]}] "No physical pin for $name"
        set net [$dbpin getNet]
        set netname UNCONNECTED
        set sta_loads 0; set db_loads 0; set nbt 0; set nit 0
        set wire 0; set shapes 0; set guides 0; set pad_only 1
        if {![rc_null $net]} {
            set netname [$net getName]
            set bterms [$net getBTerms]; set iterms [$net getITerms]
            set nbt [llength $bterms]; set nit [llength $iterms]
            foreach iterm $iterms {
                if {![rc_pad_master [[[$iterm getInst] getMaster] getName]]} {set pad_only 0}
                if {$iterm ne $dbpin && [$iterm getIoType] in {INPUT INOUT}} {incr db_loads}
            }
            foreach bterm $bterms {
                if {$bterm ne $dbpin && [$bterm getIoType] in {OUTPUT INOUT}} {incr db_loads}
            }
            set sta_net [sta::db_net_to_sta $net]
            foreach other [sta::net_connected_pins_sorted $sta_net] {
                set other_name [sta::get_full_name $other]
                if {$other ne $pin && [$other is_load] && ![$other is_hierarchical]} {incr sta_loads}
                set other_master TOP_PORT
                if {[$other is_leaf]} {set other_master [sta::get_name [[$other instance] cell]]}
                puts $p [join [list $name $netname $other_name $other_master [$other is_driver] [$other is_load]] "\t"]
            }
            set wire [expr {![rc_null [$net getWire]]}]
            foreach swire [$net getSWires] {incr shapes [llength [$swire getWires]]}
            set guides [llength [$net getGuides]]
            if {![dict exists $seen_nets $netname]} {
                dict set seen_nets $netname 1
                # The pinned report implementation accepts objects. Avoid another
                # escaped-name round trip through report_net/find_net.
                tee -quiet -append -file reports/unannotated-nets.rpt \
                    [list sta::report_net1 $sta_net [sta::cmd_scene] 9]
            }
        }
        set category [rc_category [expr {![rc_null $net]}] $sta_loads $db_loads $nbt $pad_only]
        puts $f [join [list $name $category $netname $master $sta_loads $db_loads $nbt $nit $wire $shapes $guides] "\t"]
    }
    close $f; close $p
}
if {[catch {
    source scripts/startup.tcl
    set_thread_count 2
    read_db $::env(CP0R_ODB)
    read_sdc $::env(CP0R_SDC)
    set_propagated_clock [all_clocks]
    foreach corner {tt ff} {read_spef -corner $corner "$::env(CP0R_SPEF_DIR)/estimated_${corner}"}
    report_parasitic_annotation -report_unannotated > reports/parasitic-annotation.rpt
    tee -file reports/units.rpt {report_units}
    set f [open $::env(CP0R_DRIVERS) r]
    set names [split [string trim [read $f]] "\n"]
    close $f
    rc_inventory $names
    set f [open reports/metrics.tsv w]
    foreach {key command} {
        setup_violations {sta::endpoint_violation_count max}
        hold_violations {sta::endpoint_violation_count min}
        max_slew_violations {sta::max_slew_violation_count}
        max_cap_violations {sta::max_capacitance_violation_count}
        max_fanout_violations {sta::max_fanout_violation_count}
    } {puts $f "$key\t[uplevel #0 $command]"}
    foreach corner {tt ff} {
        puts $f "setup_${corner}\t[sta::worst_slack -corner $corner -max]"
        puts $f "hold_${corner}\t[sta::worst_slack -corner $corner -min]"
    }
    close $f
    check_setup -verbose > reports/check-setup.rpt
} message options]} {
    puts stderr "CP0C_RC_AUDIT_FAILED: $message"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    exit 1
}
puts "CP0C_RC_AUDIT_COMPLETE"
exit 0
