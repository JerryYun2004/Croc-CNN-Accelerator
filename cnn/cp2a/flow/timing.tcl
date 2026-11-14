# Standalone, pre-layout estimate. No wire parasitics and no SRAM timing model.
# OpenROAD links through OpenDB and needs technology and cell masters first.
# Standalone OpenSTA has no read_lef command and links directly from Liberty.
if {[llength [info commands read_lef]]} {
    foreach name {CP2A_TECH_LEF CP2A_CELL_LEF} {
        if {![info exists ::env($name)] || ![file isfile $::env($name)]} {
            error "Required OpenROAD timing input is missing: $name"
        }
    }
    read_lef -tech $::env(CP2A_TECH_LEF)
    read_lef -library $::env(CP2A_CELL_LEF)
    puts {CP2A_OPENROAD_LEF_LOADED}
}
read_liberty $::env(CP2A_LIB)
read_verilog $::env(CP2A_NETLIST)
link_design cnn_engine
create_clock -name clk -period 10.0 [get_ports clk_i]
set_clock_uncertainty 0.1 [get_clocks clk]
set_clock_transition 0.2 [get_clocks clk]
set data_inputs [get_ports {start_i clear_i conv_shift_i* gnt_i rvalid_i rdata_i* err_i}]
set_input_delay -max 1.0 -clock clk $data_inputs
set_input_delay -min 0.0 -clock clk $data_inputs
set_input_transition 0.2 $data_inputs
set_output_delay -max 1.0 -clock clk [all_outputs]
set_output_delay -min 0.0 -clock clk [all_outputs]
set_load 0.01 [all_outputs]
# Reset must be synchronized/deasserted safely in the integration wrapper.
set_false_path -from [get_ports rst_ni]
set_input_delay 0.0 -clock clk [get_ports rst_ni]
puts {CP2A_UNITS}
report_units
puts {CP2A_TIMING_CHECKS}
check_setup -verbose
puts {CP2A_SETUP_PATHS}
report_checks -path_delay max -group_path_count 5 -format full_clock_expanded -digits 4
puts {CP2A_HOLD_ESTIMATE_ONLY}
report_checks -path_delay min -group_path_count 2 -digits 4
puts {CP2A_MAX_SLEW_CAP_FANOUT}
report_check_types -max_slew -max_capacitance -max_fanout -violators
puts {CP2A_WORST_MAX_BEGIN}
report_worst_slack -max -digits 6
puts {CP2A_WORST_MAX_END}
puts {CP2A_STA_COMPLETE}
exit
