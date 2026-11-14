if {[catch {
 source $::env(CP3G_FLOW)/load.tcl
 check_antennas -verbose -report_file antenna.rpt
 puts CP3G_ANTENNA_CHARACTERIZATION_COMPLETE
} message options]} {
 puts stderr $message
 puts stderr [dict get $options -errorinfo]
 exit 1
}
exit 0
