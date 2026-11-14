# Preserve optimization locks while explicitly applying supply connections.
if {[catch {
    set_thread_count 2
    read_db global.odb
    set block [ord::get_db_block]
    set locked {}
    foreach inst [$block getInsts] {
        if {[$inst isDoNotTouch]} {lappend locked $inst;$inst setDoNotTouch false}
    }
    source scripts/power_connect.tcl
    global_connect -force
    foreach inst $locked {$inst setDoNotTouch true}
    foreach inst $locked {if {![$inst isDoNotTouch]} {error "Optimization lock was not restored"}}
    puts "CP3E_OPTIMIZATION_LOCKS_RESTORED [llength $locked]"
    set checked 0
    foreach inst [$block getInsts] {
        set master [[$inst getMaster] getName]
        if {![string match sg13g2_* $master]} {continue}
        foreach supply {VDD VSS} {
            set pin [$inst findITerm $supply]
            if {$pin eq "NULL" || [$pin getNet] eq "NULL" || [[$pin getNet] getName] ne $supply} {
                error "Unconnected/misassigned standard-cell supply: [$inst getName]/$supply"
            }
        }
        incr checked
    }
    puts "CP3E_STANDARD_CELL_SUPPLIES_OK $checked"
    foreach net {VDD VSS VDDIO VSSIO} {
        check_power_grid -net $net -error_file ${net}-power.rpt
        puts "CP3E_POWER_GRID_OK $net"
    }
    write_def global.def
    write_db global.odb
    write_verilog -include_pwr_gnd final.v
    puts CP3E_POWER_COMPLETE
} message options]} {
    puts stderr $message
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
