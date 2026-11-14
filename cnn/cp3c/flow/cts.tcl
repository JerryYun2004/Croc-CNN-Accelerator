if {[catch {
    set_thread_count 2
    source scripts/init_tech_sg13cmos5l.tcl
    read_def $::env(CP3C_DEF)
    read_sdc src/constraints.sdc
    source scripts/power_connect.tcl
    source $::env(CP3C_FLOW)/reports.tcl
    setDefaultParasitics
    set_dont_use $dont_use_cells
    write_def imported.def
    write_sdc -no_timestamp original.sdc
    estimate_parasitics -placement
    cp3c_reports prects
    repair_clock_inverters
    clock_tree_synthesis -buf_list $ctsBuf -root_buf $ctsBufRoot -sink_clustering_enable -repair_clock_nets
    detailed_placement
    check_placement -verbose
    puts CP3C_CTS_PLACEMENT_OK
    set_propagated_clock [all_clocks]
    global_connect -force
    estimate_parasitics -placement
    cp3c_reports postcts
    report_cts -out_file cts.rpt
    write_def cts.def
    write_db cts.odb
    puts CP3C_CTS_COMPLETE
} message options]} {
    puts stderr $message
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
