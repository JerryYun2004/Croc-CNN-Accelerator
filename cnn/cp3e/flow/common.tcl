set_thread_count 2
source $::env(CP3E_FLOW)/reports.tcl
source scripts/init_tech_sg13cmos5l.tcl
if {$::env(CP3E_INPUT_KIND) eq "DEF"} {
 read_def $::env(CP3E_INPUT)
} elseif {$::env(CP3E_INPUT_KIND) eq "DB"} {
 read_db $::env(CP3E_INPUT)
} else {error "Invalid input type"}
read_sdc src/constraints.sdc
source scripts/power_connect.tcl
setDefaultParasitics
set_dont_use $dont_use_cells
set_propagated_clock [all_clocks]
set clocks {}
foreach cell [get_cells *] {
 set inst [sta::sta_to_db_inst $cell]
 foreach pin [$inst getITerms] {
  set net [$pin getNet]
  if {$net ne "NULL" && [$net getSigType] eq "CLOCK" && [$pin getIoType] eq "OUTPUT"} {lappend clocks $cell;break}
 }
}
set_dont_touch $clocks
set_global_routing_layer_adjustment TopMetal1 0.20
set_routing_layers -signal Metal2-TopMetal1 -clock Metal2-TopMetal1
proc cp3e_route_report {} {
 global_connect -force
 global_route -guide_file global.guide -verbose
 estimate_parasitics -global_routing
 cp3e_reports final
 write_def global.def
 write_db global.odb
 write_sdc -no_timestamp propagated.sdc
 write_verilog -include_pwr_gnd final.v
}
