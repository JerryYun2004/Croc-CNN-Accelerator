if {[catch {
    set_thread_count 2
    source $::env(CP3D_FLOW)/reports.tcl
    source scripts/init_tech_sg13cmos5l.tcl
    read_db tree.odb
    read_sdc src/constraints.sdc
    source scripts/power_connect.tcl
    setDefaultParasitics
    set_dont_use $dont_use_cells
    set_propagated_clock [all_clocks]
    set_global_routing_layer_adjustment TopMetal1 0.20
    set_routing_layers -signal Metal2-TopMetal1 -clock Metal2-TopMetal1
    global_route -guide_file setup-initial.guide -verbose
    estimate_parasitics -global_routing
    repair_design -verbose -max_utilization 65
    repair_timing -setup -phases WNS -repair_tns 20 -max_passes 50 -max_repairs_per_pass 1 -skip_pin_swap -skip_gate_cloning -skip_buffer_removal -skip_last_gasp -max_utilization 65 -verbose
    detailed_placement
    check_placement -verbose
    global_connect -force
    global_route -guide_file setup.guide -verbose
    estimate_parasitics -global_routing
    cp3d_reports setup
    write_def setup.def
    write_db setup.odb
    puts CP3D_OPTIMIZE_COMPLETE
} message options]} {
    puts stderr $message
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
