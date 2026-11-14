if {[catch {
    set_thread_count 2
    source $::env(CP3D_FLOW)/reports.tcl
    source scripts/init_tech_sg13cmos5l.tcl
    read_def $::env(CP3D_INPUT)
    read_sdc src/constraints.sdc
    source scripts/power_connect.tcl
    setDefaultParasitics
    set_dont_use $dont_use_cells
    set_propagated_clock [all_clocks]
    write_def imported.def
    write_sdc -no_timestamp imported.sdc
    set removable {}
    set clock_cells {}
    foreach cell [get_cells *] {
        set inst [sta::sta_to_db_inst $cell]
        set master [[$inst getMaster] getName]
        set clock 0
        foreach pin [$inst getITerms] {
            set net [$pin getNet]
            if {$net ne "NULL" && [$net getSigType] eq "CLOCK" && [$pin getIoType] eq "OUTPUT"} {set clock 1}
        }
        if {$clock} {lappend clock_cells $cell;continue}
        if {[regexp {^sg13g2_buf_(1|2|4|8|16)$} $master] && ![$inst isFixed]} {
            lappend removable $cell
        }
    }
    set_dont_touch $clock_cells
    puts "CP3D_REMOVABLE_BUFFERS [llength $removable]"
    remove_buffers {*}$removable
    source $::env(CP3D_FLOW)/balance.tcl
    detailed_placement
    check_placement -verbose
    global_connect -force
    set_global_routing_layer_adjustment TopMetal1 0.20
    set_routing_layers -signal Metal2-TopMetal1 -clock Metal2-TopMetal1
    global_route -guide_file tree.guide -verbose
    estimate_parasitics -global_routing
    cp3d_reports tree
    write_def tree.def
    write_db tree.odb
    puts CP3D_PILOT_COMPLETE
} message options]} {
    puts stderr $message
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
