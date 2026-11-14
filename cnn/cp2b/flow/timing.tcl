# Standalone, pre-layout estimate. Includes SRAM Liberty, with no wire parasitics.
# OpenROAD links through OpenDB and needs technology and cell masters first.
# Standalone OpenSTA has no read_lef command and links directly from Liberty.
if {[llength [info commands read_lef]]} {
    foreach name {CP2B_TECH_LEF CP2B_CELL_LEF CP2B_SRAM_LEF} {
        if {![info exists ::env($name)] || ![file isfile $::env($name)]} {
            error "Required OpenROAD timing input is missing: $name"
        }
    }
    read_lef -tech $::env(CP2B_TECH_LEF)
    read_lef -library $::env(CP2B_CELL_LEF)
    read_lef -library $::env(CP2B_SRAM_LEF)
    puts {CP2B_OPENROAD_LEF_LOADED}
}
read_liberty $::env(CP2B_LIB)
read_liberty $::env(CP2B_SRAM_LIB)
read_verilog $::env(CP2B_NETLIST)
link_design cnn_peripheral
create_clock -name clk -period 10.0 [get_ports clk_i]
set_clock_uncertainty 0.1 [get_clocks clk]
set_clock_transition 0.2 [get_clocks clk]
set data_inputs [get_ports {req_i we_i addr_i* wdata_i* be_i* aid_i*}]
set_input_delay -max 1.0 -clock clk $data_inputs
set_input_delay -min 0.0 -clock clk $data_inputs
set_input_transition 0.2 $data_inputs
set_output_delay -max 1.0 -clock clk [all_outputs]
set_output_delay -min 0.0 -clock clk [all_outputs]
set_load 0.01 [all_outputs]
# Reset must be synchronized/deasserted safely in the integration wrapper.
set_false_path -from [get_ports rst_ni]
set_input_delay 0.0 -clock clk [get_ports rst_ni]
puts {CP2B_UNITS}
report_units
puts {CP2B_TIMING_CHECKS}
check_setup -verbose
puts {CP2B_SETUP_PATHS}
set group_option -group_count
if {[info exists ::sta::cmd_args(report_checks)] &&
    [string match *-group_path_count* $::sta::cmd_args(report_checks)]} {
    set group_option -group_path_count
}
report_checks -path_delay max $group_option 5 -format full_clock_expanded -digits 4
puts {CP2B_HOLD_ESTIMATE_ONLY}
report_checks -path_delay min $group_option 2 -digits 4
puts {CP2B_MAX_SLEW_CAP_FANOUT}
report_check_types -max_slew -max_capacitance -max_fanout -violators
puts {CP2B_WORST_MAX_BEGIN}
report_worst_slack -max -digits 6
puts {CP2B_WORST_MAX_END}
puts {CP2B_STA_COMPLETE}
exit
