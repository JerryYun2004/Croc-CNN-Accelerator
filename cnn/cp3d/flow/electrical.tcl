# Repair clock branching and isolate fixed SRAM output loads.
unset_dont_touch [get_cells *]
set clock_serial 0
set memory_serial 0
foreach net [get_nets *] {
    set dbnet [sta::sta_to_db_net $net]
    if {[$dbnet getSigType] ne "CLOCK"} {continue}
    set loads [get_pins -of_objects $net -filter {direction == input}]
    if {[llength $loads]<=8} {continue}
    set rows {}
    foreach pin $loads {lappend rows [cp3d_pinrow $pin]}
    foreach group [cp3d_cluster $rows 0] {
        set sx 0;set sy 0;set pins {}
        foreach row $group {lassign $row x y pin;incr sx $x;incr sy $y;lappend pins $pin}
        set n [llength $pins]
        set x [expr {$sx/double($n)/1000.0}];set y [expr {$sy/double($n)/1000.0}]
        set cell [insert_buffer -buffer_cell sg13g2_buf_8 -load_pins $pins -location [list $x $y] -buffer_name cp3d_clock_$clock_serial -net_name cp3d_clock_net_$clock_serial]
        set op [get_pins -of_objects $cell -filter {direction == output}]
        foreach newnet [get_nets -of_objects $op] {[sta::sta_to_db_net $newnet] setSigType CLOCK}
        incr clock_serial
    }
}
foreach cell [get_cells *] {
    set inst [sta::sta_to_db_inst $cell]
    if {[[$inst getMaster] getName] ne "RM_IHPSG13_1P_512x32_c2_bm_bist"} {continue}
    foreach pin [get_pins -of_objects $cell -filter {direction == output}] {
        set net [get_nets -of_objects $pin]
        set loads [get_pins -of_objects $net -filter {direction == input}]
        if {[llength $loads]==0} {continue}
        lassign [sta::sta_to_db_pin $pin] iterm bterm
        lassign [$iterm getAvgXY] valid x y
        if {!$valid} {error "SRAM output geometry unavailable"}
        insert_buffer -buffer_cell sg13g2_buf_4 -load_pins $loads -location [list [expr {$x/1000.0}] [expr {$y/1000.0}]] -buffer_name cp3d_sram_$memory_serial -net_name cp3d_sram_net_$memory_serial
        incr memory_serial
    }
}
set clock_cells {}
foreach cell [get_cells *] {
    set inst [sta::sta_to_db_inst $cell]
    foreach pin [$inst getITerms] {
        set net [$pin getNet]
        if {$net ne "NULL" && [$net getSigType] eq "CLOCK" && [$pin getIoType] eq "OUTPUT"} {lappend clock_cells $cell;break}
    }
}
global_connect -force
set_dont_touch $clock_cells
puts "CP3D_CLOCK_BRANCH_BUFFERS $clock_serial"
puts "CP3D_SRAM_OUTPUT_BUFFERS $memory_serial"
