proc cp3e_reports {stage} {
    file mkdir reports
    report_worst_slack -max -digits 6 > reports/${stage}-worst-max.rpt
    report_worst_slack -min -digits 6 > reports/${stage}-worst-min.rpt
    report_tns -max -digits 6 > reports/${stage}-tns-max.rpt
    report_tns -min -digits 6 > reports/${stage}-tns-min.rpt
    foreach corner {tt ff} {
        report_checks -scenes $corner -path_delay max -group_path_count 10 -format full_clock_expanded -fields {slew capacitance fanout} -digits 6 > reports/${stage}-${corner}-max.rpt
        report_checks -scenes $corner -path_delay min -group_path_count 10 -format full_clock_expanded -fields {slew capacitance fanout} -digits 6 > reports/${stage}-${corner}-min.rpt
        report_check_types -scenes $corner -max_slew -max_capacitance -max_fanout -min_pulse_width -violators -digits 6 > reports/${stage}-${corner}-electrical.rpt
    }
    report_clock_properties > reports/${stage}-clocks.rpt
    report_clock_skew -setup -digits 6 > reports/${stage}-skew-setup.rpt
    report_clock_skew -hold -digits 6 > reports/${stage}-skew-hold.rpt
    check_setup -verbose > reports/${stage}-coverage.rpt
    report_design_area > reports/${stage}-area.rpt
}
