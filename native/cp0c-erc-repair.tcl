# Experimental physical repair. Original CP0C flow remains the reference.
# This file runs only inside a private case directory, starting from CTS.
proc erc_require {condition message} {
    if {!$condition} {error $message}
}
proc erc_pin_name {pin} {
    return "[[$pin getInst] getName]/[[$pin getMTerm] getName]"
}
proc erc_sta_pin {pin} {
    global erc_pin_map
    set id [$pin getId]
    erc_require [dict exists $erc_pin_map $id] "No STA pin for [erc_pin_name $pin]"
    return [dict get $erc_pin_map $id]
}
proc erc_net_check {net} {
    erc_require [expr {$net ne "NULL" && $net ne ""}] "Missing target net"
    erc_require [expr {![$net isDoNotTouch]}] "Protected target net: [$net getName]"
    erc_require [expr {[llength [$net getBTerms]] == 0}] "Target reaches a top-level port: [$net getName]"
    set drivers {}
    set loads {}
    foreach pin [$net getITerms] {
        switch -- [$pin getIoType] {
            OUTPUT {lappend drivers $pin}
            INPUT {lappend loads $pin}
            default {error "Unexpected direction on [erc_pin_name $pin]"}
        }
    }
    erc_require [expr {[llength $drivers] == 1}] "Target net must have one driver: [$net getName]"
    return [list [lindex $drivers 0] $loads]
}
proc erc_ids {pins} {
    set ids {}
    foreach pin $pins {lappend ids [$pin getId]}
    return [lsort -integer $ids]
}
proc erc_buffer {name cell args} {
    global erc_edits erc_buffers
    set block [ord::get_db_block]
    erc_require [expr {[$block findInst $name] eq "NULL"}] "Buffer already exists: $name"
    # The pinned API always uniquifies -buffer_name, even if the requested
    # name is unused. Follow the returned STA instance rather than guessing
    # its ODB name or suffix.
    set sta_inst [insert_buffer -buffer_cell $cell -buffer_name $name -net_name "${name}_net" {*}$args]
    erc_require [expr {$sta_inst ne "NULL" && $sta_inst ne ""}] "No instance returned for $name"
    set inst [sta::sta_to_db_inst $sta_inst]
    erc_require [expr {$inst ne "NULL" && $inst ne ""}] "No ODB instance returned for $name"
    set actual_name [$inst getName]
    erc_require [expr {[[$inst getMaster] getName] eq $cell}] "Wrong master for $actual_name"
    # Keep these intentional isolation and clock-stage buffers through the
    # experimental timing-repair passes. Their nets remain repairable.
    set_dont_touch $sta_inst
    lappend erc_buffers $inst
    puts $erc_edits [join [list $name $actual_name $cell {*}$args] "\t"]
    flush $erc_edits
    return $inst
}
proc erc_macro_buffer_location {inst pin} {
    set dbu [expr {double([[ord::get_db_block] getDbUnitsPerMicron])}]
    set box [$pin getBBox]
    set ibox [$inst getBBox]
    # Pinned baseline uses R0 SRAMs with pins facing the core below them.
    erc_require [expr {[$inst getOrient] eq "R0"}] "Unexpected SRAM orientation"
    set x [expr {([$box xMin] + [$box xMax]) / (2.0*$dbu)}]
    set y [expr {[$ibox yMin]/$dbu - 20.0}]
    return [list $x $y]
}
proc erc_apply_repairs {} {
    global erc_pin_map erc_edits erc_buffers erc_macro_positions
    set block [ord::get_db_block]
    set erc_pin_map [dict create]
    # Map STA objects to ODB IDs; avoid guessing how escaped SRAM names are
    # interpreted by get_pins wildcard matching.
    foreach pin [get_pins -of_objects [get_cells -hierarchical *]] {
        set dbpin [sta::sta_to_db_pin $pin]
        if {$dbpin ne "NULL"} {dict set erc_pin_map [$dbpin getId] $pin}
    }
    set macros {}
    foreach inst [$block getInsts] {
        if {[[$inst getMaster] isBlock]} {
            erc_require [expr {[[$inst getMaster] getName] eq "RM_IHPSG13_1P_512x32_c2_bm_bist"}] "Unexpected SRAM master"
            lappend macros [list [lindex [$inst getLocation] 0] $inst]
        }
    }
    set macros [lsort -integer -index 0 $macros]
    erc_require [expr {[llength $macros] == 2}] "Expected two SRAMs"
    set erc_buffers {}
    set erc_macro_positions [dict create]
    set erc_edits [open reports/inserted-buffers.tsv w]
    puts $erc_edits "requested_name\tactual_name\tmaster\targuments"
    set bank 0
    set output_count 0
    set input_count 0
    foreach pair $macros {
        set inst [lindex $pair 1]
        dict set erc_macro_positions [$inst getName] [list [$inst getLocation] [$inst getOrient]]
        # Snapshot before edits so the iterator is independent of insertions.
        set pins [$inst getITerms]
        foreach pin $pins {
            set port [[$pin getMTerm] getName]
            if {[regexp {^A_DOUT\[([0-9]+)\]$} $port -> bit]} {
                set net [$pin getNet]
                lassign [erc_net_check $net] driver loads
                erc_require [expr {[$driver getId] == [$pin getId] && [llength $loads] == 4}] "Unexpected SRAM output connectivity: [erc_pin_name $pin]"
                set buffer [erc_buffer "cp0c_eco_sram${bank}_dout${bit}" sg13g2_buf_4 \
                    -net [sta::db_net_to_sta $net] -location [erc_macro_buffer_location $inst $pin]]
                set a [$buffer findITerm A]
                set x [$buffer findITerm X]
                lassign [erc_net_check [$pin getNet]] new_driver new_loads
                erc_require [expr {[erc_ids $new_loads] eq [erc_ids [list $a]]}] "SRAM output was not isolated"
                lassign [erc_net_check [$x getNet]] new_driver new_loads
                erc_require [expr {[erc_ids $new_loads] eq [erc_ids $loads]}] "SRAM output loads changed unexpectedly"
                incr output_count
            } elseif {$port eq "A_DLY" || ($bank == 1 && $port in {{A_DIN[20]} {A_DIN[22]} A_MEN})} {
                erc_net_check [$pin getNet]
                set suffix [string map {[ _ ] {}} $port]
                set buffer [erc_buffer "cp0c_eco_sram${bank}_${suffix}" sg13g2_buf_4 \
                    -load_pins [list [erc_sta_pin $pin]] -location [erc_macro_buffer_location $inst $pin]]
                lassign [erc_net_check [$pin getNet]] driver loads
                erc_require [expr {[[$driver getInst] getId] == [$buffer getId] && [erc_ids $loads] eq [erc_ids [list $pin]]}] "SRAM input buffer is not connected to its target"
                incr input_count
            }
        }
        incr bank
    }
    set clock_count 0
    foreach index {0 1 2 3} {
        set inst [$block findInst "clkbuf_2_${index}_0_soc_clk_i_regs"]
        erc_require [expr {$inst ne "NULL"}] "Missing clock branch $index"
        set pin [$inst findITerm X]
        lassign [erc_net_check [$pin getNet]] driver loads
        erc_require [expr {[llength $loads] == 16}] "Clock branch $index no longer has 16 loads"
        set ranked {}
        foreach load $loads {
            set box [$load getBBox]
            lappend ranked [list [$box yMin] [erc_pin_name $load] $load]
        }
        set ranked [lsort -real -index 0 [lsort -index 1 $ranked]]
        set root_loads {}
        for {set group 0} {$group < 4} {incr group} {
            set targets {}
            set sta_targets {}
            set x 0.0
            set y 0.0
            foreach row [lrange $ranked [expr {$group*4}] [expr {$group*4+3}]] {
                set target [lindex $row 2]
                lappend targets $target
                lappend sta_targets [erc_sta_pin $target]
                set box [$target getBBox]
                set x [expr {$x + ([$box xMin]+[$box xMax])/2.0}]
                set y [expr {$y + ([$box yMin]+[$box yMax])/2.0}]
            }
            set scale [expr {4.0*[$block getDbUnitsPerMicron]}]
            set buffer [erc_buffer "cp0c_eco_clk${index}_group${group}" sg13g2_buf_16 \
                -load_pins $sta_targets -location [list [expr {$x/$scale}] [expr {$y/$scale}]]]
            lassign [erc_net_check [[$buffer findITerm X] getNet]] new_driver new_loads
            erc_require [expr {[erc_ids $new_loads] eq [erc_ids $targets]}] "Clock subgroup loads changed unexpectedly"
            lappend root_loads [$buffer findITerm A]
            incr clock_count
        }
        lassign [erc_net_check [$pin getNet]] new_driver new_loads
        erc_require [expr {[erc_ids $new_loads] eq [erc_ids $root_loads]}] "Clock root does not drive its four subgroups"
    }
    close $erc_edits
    erc_require [expr {$output_count == 64 && $input_count == 5 && $clock_count == 16}] "Unexpected insertion counts"
    puts "CP0C_ERC_INSERTIONS SRAM_outputs=$output_count SRAM_inputs=$input_count clock=$clock_count"
}
proc erc_snapshot {label} {
    set prefix "reports/$label"
    set f [open "${prefix}.tsv" w]
    foreach {key command} {
        setup_violations {sta::endpoint_violation_count max}
        hold_violations {sta::endpoint_violation_count min}
        max_slew_violations {sta::max_slew_violation_count}
        max_cap_violations {sta::max_capacitance_violation_count}
        max_fanout_violations {sta::max_fanout_violation_count}
    } {puts $f "$key\t[uplevel #0 $command]"}
    foreach corner {tt ff} {
        puts $f "setup_${corner}\t[sta::worst_slack -corner $corner -max]"
        puts $f "hold_${corner}\t[sta::worst_slack -corner $corner -min]"
    }
    set area 0.0
    set block [ord::get_db_block]
    set scale [expr {double([$block getDbUnitsPerMicron])**2}]
    foreach inst [$block getInsts] {
        set m [$inst getMaster]
        if {![$m isBlock] && ![$m isPad] && ![$m isCover] && ![$m isFiller]} {
            set area [expr {$area + double([$m getWidth])*[$m getHeight]/$scale}]
        }
    }
    puts $f "stdcell_area_um2\t$area"
    close $f
    report_check_types -max_slew -max_capacitance -max_fanout -violators -digits 6 > "${prefix}-erc.rpt"
    report_checks -path_delay min_max -group_path_count 5 -format full_clock_expanded \
        -fields {slew capacitance fanout} -digits 6 > "${prefix}-timing.rpt"
    check_setup -verbose > "${prefix}-check-setup.rpt"
}
if {[catch {
    source scripts/startup.tcl
    set_thread_count $::env(CP0C_ERC_THREADS)
    read_db $::env(CP0C_ERC_ODB)
    read_sdc $::env(CP0C_ERC_SDC)
    setDefaultParasitics
    set_dont_use $dont_use_cells
    set_propagated_clock [all_clocks]
    tee -file reports/units.rpt {report_units}
    # Set the scenario before the first delay calculation. Each scenario uses
    # a separate OpenROAD process to avoid reuse of bidirectional slew caches.
    set_load $::env(CP0C_ERC_LOAD_PF) [all_outputs]
    if {$::env(CP0C_ERC_REPAIR) eq "1"} {erc_apply_repairs}
    detailed_placement
    check_placement -verbose
    set_global_routing_layer_adjustment TopMetal1 0.20
    set_routing_layers -signal Metal2-TopMetal1 -clock Metal2-TopMetal1
    global_route -guide_file reports/initial.guide
    estimate_parasitics -global_routing
    erc_snapshot initial
    # Bounded repair, then fresh RC estimates after each legalization/reroute.
    # All three scenarios use the same two passes and timing targets.
    for {set pass 1} {$pass <= 2} {incr pass} {
        global_route -start_incremental
        repair_design -slew_margin 20 -cap_margin 20 -verbose
        repair_timing -setup -setup_margin 0.05 -repair_tns 100 -verbose
        repair_timing -hold -hold_margin 0.1 -repair_tns 100 -verbose
        detailed_placement
        global_route -end_incremental -guide_file "reports/pass${pass}.guide"
        estimate_parasitics -global_routing
        erc_snapshot "pass${pass}"
    }
    check_placement -verbose
    if {$::env(CP0C_ERC_REPAIR) eq "1"} {
        dict for {name expected} $erc_macro_positions {
            set inst [[ord::get_db_block] findInst $name]
            erc_require [expr {[list [$inst getLocation] [$inst getOrient]] eq $expected}] "SRAM moved: $name"
        }
        foreach inst $erc_buffers {
            erc_require [expr {[[$inst getMaster] getName] in {sg13g2_buf_4 sg13g2_buf_16}}] "Intentional buffer master changed"
        }
    }
    # Save estimates while the fresh router state is live. These are NOT
    # OpenRCX-extracted parasitics and are labelled accordingly.
    estimate_parasitics -global_routing -spef_file out/estimated
    erc_snapshot final
    global_connect
    write_db out/trial.odb
    write_sdc out/trial.sdc
    write_verilog out/trial.v
} message options]} {
    puts stderr "CP0C_ERC_REPAIR_FAILED: $message"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    exit 1
}
puts "CP0C_ERC_REPAIR_CASE_COMPLETE"
exit 0
