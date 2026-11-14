# Saved-checkpoint PG repair and GPIO-enable diagnostics. No placement/routing.
proc pc_require {ok message} {if {!$ok} {error $message}}
proc pc_net_name {pin} {
    set net [$pin getNet]
    if {$net eq "NULL" || $net eq ""} {return "UNCONNECTED"}
    return [$net getName]
}
proc pc_expected_rail {name} {
    if {$name in {VDD vdd VDDARRAY VDDARRAY! VDD!}} {return VDD}
    if {$name in {VSS vss VSS!}} {return VSS}
    if {$name eq "iovdd" || [string match *vdd_RING $name]} {return VDDIO}
    if {$name eq "iovss" || [string match *vss_RING $name]} {return VSSIO}
    return UNMAPPED
}
proc pc_targets {} {
    set f [open $::env(CP0P_TARGETS) r]
    gets $f header
    pc_require [expr {[lrange [split $header "\t"] 0 2] eq {requested_name actual_name master}}] "Unexpected insertion-log header"
    set result [dict create]
    while {[gets $f line] >= 0} {
        if {$line eq ""} {continue}
        set row [split $line "\t"]
        set name [lindex $row 1]
        set master [lindex $row 2]
        pc_require [expr {![dict exists $result $name]}] "Duplicate target $name"
        set inst [[ord::get_db_block] findInst $name]
        pc_require [expr {$inst ne "NULL" && $inst ne ""}] "Missing target $name"
        pc_require [expr {$master in {sg13g2_buf_4 sg13g2_buf_16} && [[$inst getMaster] getName] eq $master}] "Unexpected target master $name"
        dict set result $name $inst
    }
    close $f
    pc_require [expr {[dict size $result] == 85}] "Expected 85 targeted buffers"
    return $result
}
proc pc_signal_signature {} {
    set rows {}
    foreach inst [[ord::get_db_block] getInsts] {
        lappend rows [list I [$inst getName] [[$inst getMaster] getName] [$inst getLocation] [$inst getOrient] [$inst isDoNotTouch]]
        foreach pin [$inst getITerms] {
            if {[$pin getSigType] ni {POWER GROUND}} {
                lappend rows [list P [$pin getId] [pc_net_name $pin]]
            }
        }
    }
    foreach pin [[ord::get_db_block] getBTerms] {
        lappend rows [list B [$pin getName] [pc_net_name $pin]]
    }
    return [lsort $rows]
}
proc pc_pg_audit {label targets} {
    set f [open "reports/${label}-pg.tsv" w]
    puts $f "instance\tmaster\tpin\texpected\tactual\tstatus"
    set checked 0
    set bad 0
    set target_pins 0
    foreach inst [[ord::get_db_block] getInsts] {
        set name [$inst getName]
        set target [dict exists $targets $name]
        foreach pin [$inst getITerms] {
            if {[$pin getSigType] ni {POWER GROUND}} {continue}
            incr checked
            if {$target} {incr target_pins}
            set port [[$pin getMTerm] getName]
            set expected [pc_expected_rail $port]
            set actual [pc_net_name $pin]
            set status OK
            if {$expected eq "UNMAPPED" || $actual ne $expected} {
                set status REVIEW
                incr bad
            } else {
                set expected_type [expr {$expected in {VDD VDDIO} ? "POWER" : "GROUND"}]
                if {[[$pin getNet] getSigType] ne $expected_type} {set status REVIEW; incr bad}
            }
            if {$target || $status ne "OK"} {
                puts $f [join [list $name [[$inst getMaster] getName] $port $expected $actual $status] "\t"]
            }
        }
    }
    close $f
    set f [open "reports/${label}-pg-counts.tsv" w]
    puts $f "checked\t$checked\ntarget_pins\t$target_pins\nfindings\t$bad"
    close $f
    pc_require [expr {$target_pins == 170}] "Expected 170 supply pins on the 85 buffers"
    return $bad
}
proc pc_fix_target_pg {targets} {
    set block [ord::get_db_block]
    # Validate every planned connection before modifying any instance.
    set actions {}
    dict for {name inst} $targets {
        foreach rail {VDD VSS} {
            set net [$block findNet $rail]
            pc_require [expr {$net ne "NULL" && $net ne ""}] "Missing supply net $rail"
            pc_require [expr {[$net getSigType] eq ($rail eq "VDD" ? "POWER" : "GROUND")}] "Wrong supply-net type: $rail"
            set pin [$inst findITerm $rail]
            pc_require [expr {$pin ne "NULL" && $pin ne ""}] "Missing $rail pin on $name"
            set actual [pc_net_name $pin]
            pc_require [expr {$actual eq "UNCONNECTED" || $actual eq $rail}] "Unexpected existing rail on $name/$rail: $actual"
            if {$actual eq "UNCONNECTED"} {lappend actions [list $inst $pin $net]}
        }
    }
    set f [open reports/pg-edits.tsv w]
    puts $f "instance\tpin\tconnected_net"
    foreach action $actions {
        lassign $action inst pin net
        set protected [$inst isDoNotTouch]
        $inst setDoNotTouch false
        try {$pin connect $net} finally {$inst setDoNotTouch $protected}
        puts $f [join [list [$inst getName] [[$pin getMTerm] getName] [$net getName]] "\t"]
    }
    close $f
    return [llength $actions]
}
proc pc_snapshot {} {
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
    report_check_types -max_slew -max_capacitance -max_fanout -violators -digits 6 > reports/erc.rpt
    report_checks -path_delay min_max -group_path_count 5 -format full_clock_expanded -fields {slew capacitance fanout} -digits 6 > reports/timing.rpt
    check_setup -verbose > reports/check-setup.rpt
}
if {[catch {
    source scripts/startup.tcl
    set_thread_count $::env(CP0P_THREADS)
    read_db $::env(CP0P_ODB)
    set targets [pc_targets]
    pc_pg_audit before $targets
    set before [pc_signal_signature]
    set edits 0
    if {$::env(CP0P_FIX_PG) eq "1"} {set edits [pc_fix_target_pg $targets]}
    pc_require [expr {[pc_signal_signature] eq $before}] "Signal connectivity, placement or protection flags changed"
    pc_require [expr {[pc_pg_audit after $targets] == 0}] "Supply-pin findings remain; inspect reports/after-pg.tsv"
    puts "CP0C_POSTCHECK_PG_OK edits=$edits target_pins=170"
    if {$::env(CP0P_FIX_PG) eq "1"} {write_db out/pg_fixed.odb}
    read_sdc $::env(CP0P_SDC)
    set_propagated_clock [all_clocks]
    # Import the actual saved global-route estimates, not reconstructed guides.
    foreach corner {tt ff} {
        read_spef -corner $corner "$::env(CP0P_SPEF_DIR)/estimated_${corner}"
    }
    report_parasitic_annotation -report_unannotated > reports/parasitic-annotation.rpt
    tee -file reports/units.rpt {report_units}
    set mode $::env(CP0P_GPIO_OE)
    pc_require [expr {$mode in {dynamic 0 1}}] "Invalid GPIO-enable mode"
    set enables [get_pins pad_gpio*_io/c2p_en]
    pc_require [expr {[llength $enables] == 32}] "Expected 32 GPIO output-enable pins"
    foreach cell [get_cells pad_gpio*_io] {
        set inst [sta::sta_to_db_inst $cell]
        pc_require [expr {[[$inst getMaster] getName] eq "sg13cmos5l_IOPadInOut30mA"}] "Unexpected GPIO pad master"
    }
    if {$mode ne "dynamic"} {set_case_analysis $mode $enables}
    pc_snapshot
    for {set bit 0} {$bit < 32} {incr bit} {
        set names [list "gpio${bit}_io" "pad_gpio${bit}_io/pad" "pad_gpio${bit}_io/c2p" "pad_gpio${bit}_io/c2p_en" "pad_gpio${bit}_io/p2c"]
        foreach name $names {
            tee -quiet -append -file reports/gpio-slews.rpt [list report_slews -digits 6 $name]
        }
    }
    foreach bit {0 24} {
        report_net -digits 6 "gpio${bit}_io" >> reports/gpio-nets.rpt
    }
    report_checks -through $enables -path_delay max -group_path_count 10 -format full_clock_expanded -fields {slew capacitance fanout} -digits 6 > reports/gpio-enable-paths.rpt
    write_sdc out/diagnostic.sdc
} message options]} {
    puts stderr "CP0C_POSTCHECK_FAILED: $message"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    exit 1
}
puts "CP0C_POSTCHECK_CASE_COMPLETE"
exit 0
