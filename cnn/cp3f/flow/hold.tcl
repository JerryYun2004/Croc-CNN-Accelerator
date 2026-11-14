if {[catch {
 source $::env(CP3F_FLOW)/common.tcl
 detailed_placement
 check_placement -verbose
 global_route -guide_file optimizer-initial.guide -verbose
 estimate_parasitics -global_routing
 repair_timing -hold -hold_margin 0.1005 -setup_margin 0 -max_buffer_percent 1
 detailed_placement
 check_placement -verbose
 global_connect -force
 write_db optimized.odb
 write_def optimized.def
 puts CP3F_OPTIMIZER_COMPLETE
} message options]} {
 puts stderr $message
 puts stderr [dict get $options -errorinfo]
 exit 1
}
exit 0
