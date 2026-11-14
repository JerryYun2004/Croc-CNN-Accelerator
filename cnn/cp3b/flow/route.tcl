if {[catch {
set_thread_count 2
source scripts/init_tech_sg13cmos5l.tcl
read_db pdn.odb
read_sdc src/constraints.sdc
foreach net {VDD VSS VDDIO VSSIO} {
    check_power_grid -net $net -error_file ${net}-power.rpt
    puts "CP3B_POWER_GRID_OK $net"
}
setDefaultParasitics
set_global_routing_layer_adjustment TopMetal1 0.20
set_routing_layers -signal Metal2-TopMetal1 -clock Metal2-TopMetal1
global_route -critical_nets_percentage 0 -guide_file global.guide -congestion_report_file congestion.rpt -verbose
write_db global.odb
write_def global.def
puts CP3B_GLOBAL_ROUTE_COMPLETE
} message options]} {puts stderr $message;puts stderr [dict get $options -errorinfo];exit 1}
exit 0
