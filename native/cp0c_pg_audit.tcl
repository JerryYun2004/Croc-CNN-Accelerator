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
