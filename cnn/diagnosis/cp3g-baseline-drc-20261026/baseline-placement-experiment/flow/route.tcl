if {[catch {
 source $::env(CP3G_FLOW)/load.tcl
 source $::env(CP3G_FLOW)/snapshot.tcl
 set_thread_count 8
 check_placement -verbose -report_file_name initial-placement.rpt
 snapshot_connections before-connections.tcllist
 write_def imported.def
 write_sdc -no_timestamp imported.sdc
 # Regenerate guides from moved pins using the accepted CP3F routing settings.
 set_global_routing_layer_adjustment TopMetal1 0.20
 global_route -guide_file moved.guide -verbose
 write_db moved-global.odb
 puts EXP_MOVED_GUIDES_COMPLETE
 detailed_route -droute_end_iter 20 -output_drc route-drc.rpt -output_guide_coverage guide-coverage.csv -drc_report_iter_step 5 -save_guide_updates -clean_patches -verbose 1
 # Normal detailed routing includes native signal-connectivity checks per completed iteration.
 check_placement -verbose -report_file_name final-placement.rpt
 snapshot_connections after-connections.tcllist
 write_def routed.def
 write_db routed.odb
 write_verilog -include_pwr_gnd routed.v
 write_sdc -no_timestamp propagated.sdc
 puts "CP3G_ROUTER_DRC [drt::detailed_route_num_drvs]"
 puts CP3G_DETAIL_ROUTE_COMPLETE
} message options]} {
 puts stderr $message
 puts stderr [dict get $options -errorinfo]
 exit 1
}
exit 0
