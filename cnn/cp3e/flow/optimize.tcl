if {[catch {
 source $::env(CP3E_FLOW)/common.tcl
 detailed_placement
 check_placement -verbose
 # Preserve intentional delay cells and SRAM-local buffers during setup repair.
 set protected {}
 foreach cell [get_cells *] {
  set inst [sta::sta_to_db_inst $cell]
  if {[string match sg13g2_dlygate* [[$inst getMaster] getName]] || [string match cp3e_sram_* [$inst getName]]} {lappend protected $cell}
 }
 set_dont_touch $protected
 global_route -guide_file optimizer-initial.guide -verbose
 estimate_parasitics -global_routing
 repair_timing -setup -phases WNS -repair_tns 100 -setup_margin 0.08 -max_passes 300 -max_repairs_per_pass 1 -skip_gate_cloning -skip_buffer_removal -skip_last_gasp -max_utilization 65 -verbose
 detailed_placement
 check_placement -verbose
 global_connect -force
 write_db optimized.odb
 write_def optimized.def
 puts CP3E_OPTIMIZER_COMPLETE
} message options]} {
 puts stderr $message
 puts stderr [dict get $options -errorinfo]
 exit 1
}
exit 0
