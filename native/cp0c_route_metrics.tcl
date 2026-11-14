proc erc_snapshot {label} {
    set prefix "reports/$label"
    # OpenSTA at f7febae reads Search::endpoints() directly in
    # endpointViolationCount(), without first ensuring the timing graph.
    # A fresh read_db/read_sdc/read_spef process therefore needs a public
    # timing query before these internal counters. Keep the useful report
    # first on every snapshot, including after routing changes.
    report_checks -path_delay min_max -group_path_count 5 -format full_clock_expanded \
        -fields {slew capacitance fanout} -digits 6 > "${prefix}-timing.rpt"
    set f [open "${prefix}.tsv" w]
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
    set area 0.0
    set block [ord::get_db_block]
    set scale [expr {double([$block getDbUnitsPerMicron])**2}]
    foreach inst [$block getInsts] {
        set m [$inst getMaster]
        if {![$m isBlock] && ![$m isPad] && ![$m isCover] && ![$m isFiller]} {
            set area [expr {$area + double([$m getWidth])*[$m getHeight]/$scale}]
        }
    }
    puts $f "stdcell_area_um2\t$area"
    close $f
    report_check_types -max_slew -max_capacitance -max_fanout -violators -digits 6 > "${prefix}-erc.rpt"
    check_setup -verbose > "${prefix}-check-setup.rpt"
}
