if {[catch {
 source $::env(CP3F_FLOW)/common.tcl
 check_placement -verbose
 puts CP3F_FINAL_PLACEMENT_OK
 cp3f_route_report
 puts CP3F_STAGE_COMPLETE
} message options]} {
 puts stderr $message
 puts stderr [dict get $options -errorinfo]
 exit 1
}
exit 0
