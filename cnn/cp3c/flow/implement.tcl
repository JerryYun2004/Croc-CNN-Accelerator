if {[catch {
    set_thread_count 2
    source scripts/init_tech_sg13cmos5l.tcl
    read_db cts.odb
    read_sdc src/constraints.sdc
    source $::env(CP3C_FLOW)/reports.tcl
    setDefaultParasitics
    set_dont_use $dont_use_cells
    set_propagated_clock [all_clocks]
    source $::env(CP3C_FLOW)/fanout.tcl
    detailed_placement
    check_placement -verbose
    global_connect -force
    write_def buffered.def
    set_global_routing_layer_adjustment TopMetal1 0.20
    set_routing_layers -signal Metal2-TopMetal1 -clock Metal2-TopMetal1
    global_route -guide_file initial.guide -congestion_report_file initial-congestion.rpt -verbose
    estimate_parasitics -global_routing
    cp3c_reports prerepair
    repair_design -verbose -max_utilization 65
    repair_timing -setup -phases WNS -repair_tns 1 -skip_pin_swap -skip_gate_cloning -skip_buffer_removal -skip_last_gasp -max_passes 10 -max_utilization 65 -verbose
    repair_timing -hold -hold_margin 0.1 -max_passes 30 -max_iterations 200 -max_utilization 65 -verbose
    detailed_placement
    check_placement -verbose
    puts CP3C_FINAL_PLACEMENT_OK
    global_connect -force
    global_route -guide_file global.guide -congestion_report_file congestion.rpt -verbose
    estimate_parasitics -global_routing
    cp3c_reports final
    write_sdc -no_timestamp propagated.sdc
    write_def global.def
    write_db global.odb
    write_verilog -include_pwr_gnd final.v
    puts CP3C_CTS_GRT_COMPLETE
} message options]} {
    puts stderr $message
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
