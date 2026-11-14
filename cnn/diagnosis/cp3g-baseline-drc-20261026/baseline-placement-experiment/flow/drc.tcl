if {[catch {
 source $::env(CP3G_FLOW)/load.tcl
 drt::check_drc -output_file independent-drc.rpt
 set report_handle [open independent-drc.rpt r]
 set report_text [read $report_handle]
 close $report_handle
 puts "CP3G_INDEPENDENT_DRC_COUNT [regexp -all -line {^violation type: } $report_text]"
 puts CP3G_INDEPENDENT_DRC_COMPLETE
} message options]} {
 puts stderr $message
 puts stderr [dict get $options -errorinfo]
 exit 1
}
exit 0
