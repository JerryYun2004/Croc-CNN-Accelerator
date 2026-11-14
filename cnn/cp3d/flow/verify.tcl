# Independently rebuild final routes/parasitics in a fresh process.
if {[catch {
    set_thread_count 2
    source scripts/init_tech_sg13cmos5l.tcl
    read_db global.odb
    read_sdc propagated.sdc
    setDefaultParasitics
    set_global_routing_layer_adjustment TopMetal1 0.20
    set_routing_layers -signal Metal2-TopMetal1 -clock Metal2-TopMetal1
    global_route -guide_file independent.guide -verbose
    puts CP3D_INDEPENDENT_ROUTE_COMPLETE
    estimate_parasitics -global_routing
    puts CP3D_INDEPENDENT_PARASITICS_COMPLETE
    report_worst_slack -max -digits 6
    report_worst_slack -min -digits 6
    report_tns -max -digits 6
    report_tns -min -digits 6
} message options]} {
    puts stderr $message
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
