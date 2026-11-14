if {[catch {
 source $::env(CP3E_FLOW)/common.tcl
 detailed_placement
 check_placement -verbose
 source $::env(CP3E_FLOW)/local.tcl
 source $::env(CP3E_FLOW)/drivers.tcl
 detailed_placement
 check_placement -verbose
 puts CP3E_FINAL_PLACEMENT_OK
 cp3e_route_report
 puts CP3E_STAGE_COMPLETE
} message options]} {
 puts stderr $message
 puts stderr [dict get $options -errorinfo]
 exit 1
}
exit 0
