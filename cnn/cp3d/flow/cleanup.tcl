if {[catch {
    set_thread_count 2
    source $::env(CP3D_FLOW)/reports.tcl
    source scripts/init_tech_sg13cmos5l.tcl
    read_db refined.odb
    read_sdc src/constraints.sdc
    source scripts/power_connect.tcl
    setDefaultParasitics
    set_dont_use $dont_use_cells
    set_propagated_clock [all_clocks]
    set f [open sram-input-targets.txt r]
    set targets [dict create]
    foreach n [split [string trim [read $f]] \n] {if {$n ne ""} {dict set targets $n 1}}
    close $f
    set count 0
    foreach cell [get_cells *] {
        set inst [sta::sta_to_db_inst $cell]
        if {[[$inst getMaster] getName] ne "RM_IHPSG13_1P_512x32_c2_bm_bist"} {continue}
        foreach pin [get_pins -of_objects $cell -filter {direction == input}] {
            set name [string map [list "\\" ""] [get_full_name $pin]]
            if {![dict exists $targets $name]} {continue}
            lassign [sta::sta_to_db_pin $pin] iterm bterm
            lassign [$iterm getAvgXY] valid x y
            if {!$valid} {error "SRAM input geometry unavailable"}
            insert_buffer -buffer_cell sg13g2_buf_4 -load_pins [list $pin] -location [list [expr {$x/1000.0}] [expr {$y/1000.0}]] -buffer_name cp3d_sram_input_$count -net_name cp3d_sram_input_net_$count
            dict unset targets $name
            incr count
        }
    }
    if {[dict size $targets]} {error "SRAM slew targets missing or not inputs"}
    puts "CP3D_SRAM_INPUT_BUFFERS $count"
    detailed_placement
    check_placement -verbose
    puts CP3D_FINAL_PLACEMENT_OK
    global_connect -force
    set_global_routing_layer_adjustment TopMetal1 0.20
    set_routing_layers -signal Metal2-TopMetal1 -clock Metal2-TopMetal1
    global_route -guide_file global.guide -verbose
    estimate_parasitics -global_routing
    cp3d_reports final
    write_def global.def
    write_db global.odb
    write_sdc -no_timestamp propagated.sdc
    write_verilog -include_pwr_gnd final.v
    puts CP3D_CTS_GRT_COMPLETE
} message options]} {
    puts stderr $message
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
