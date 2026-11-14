if {[catch {
 source $::env(CP3G_FLOW)/load.tcl
 # Match the original Croc detailed-router thread setting.
 set_thread_count 8
 write_def imported.def
 write_sdc -no_timestamp imported.sdc
 check_placement -verbose
 puts CP3G_PLACEMENT_OK
 read_guides $::env(CP3G_INPUT_GUIDE)
 detailed_route -droute_end_iter 20 -output_drc route-drc.rpt -output_guide_coverage guide-coverage.csv -drc_report_iter_step 5 -save_guide_updates -clean_patches -verbose 1
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
