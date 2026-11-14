set_thread_count 2
source $::env(CP3G_FLOW)/reports.tcl
source scripts/init_tech_sg13cmos5l.tcl
read_def $::env(CP3G_INPUT_DEF)
read_sdc src/constraints.sdc
set_propagated_clock [all_clocks]
set_routing_layers -signal Metal2-TopMetal1 -clock Metal2-TopMetal1
