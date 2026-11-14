# Fixed eight-net buffer ECO. Candidate only; no constraint relaxation.
proc eo_require {ok msg} {if {!$ok} {error $msg}}
proc eo_name {pin} {return "[[$pin getInst] getName]/[[$pin getMTerm] getName]"}
proc eo_ids {pins} {set ids {}; foreach p $pins {lappend ids [$p getId]}; return [lsort -integer $ids]}
proc eo_net {net} {
    eo_require [expr {$net ne "NULL" && ![$net isSpecial] && ![$net isDoNotTouch]}] "Invalid or protected target net"
    eo_require [expr {[llength [$net getBTerms]]==0}] "Unexpected top-level connection"
    set drivers {}; set ordinary {}; set diodes {}
    foreach p [$net getITerms] {
        switch -- [$p getIoType] {
            OUTPUT {lappend drivers $p}
            INPUT {
                if {[[[$p getInst] getMaster] getType] eq "CORE_ANTENNACELL"} {lappend diodes $p} else {lappend ordinary $p}
            }
            default {error "Unexpected direction: [eo_name $p]"}
        }
    }
    eo_require [expr {[llength $drivers]==1}] "Expected single driver on [$net getName]"
    return [list [lindex $drivers 0] $ordinary $diodes]
}
proc eo_read_plan {} {
    set f [open $::env(CP0E_ACTIONS) r]
    gets $f header
    set fields {buffer_name kind root_net driver driver_master expected_ordinary expected_diodes total_groups load_pin}
    eo_require [expr {[split $header "\t"] eq $fields}] "Unexpected action header"
    set groups [dict create]; set roots [dict create]; set wanted [dict create]
    while {[gets $f line]>=0} {
        if {$line eq ""} {continue}
        set values [split $line "\t"]
        eo_require [expr {[llength $values]==[llength $fields]}] "Malformed action row"
        set row [dict create]
        foreach k $fields v $values {dict set row $k $v}
        dict lappend groups [dict get $row buffer_name] $row
        dict lappend roots [dict get $row root_net] $row
        foreach k {driver load_pin} {dict set wanted [dict get $row $k] 1}
    }
    close $f
    eo_require [expr {[dict size $groups]==13 && [dict size $roots]==8}] "Unexpected action scope"
    set index [dict create]; set sta_index [dict create]
    foreach pin [get_pins -hierarchical *] {
        if {[$pin is_hierarchical]} {continue}
        set dbpin [sta::sta_to_db_pin $pin]
        if {$dbpin eq "NULL"} {continue}
        set name [eo_name $dbpin]
        if {![dict exists $wanted $name]} {continue}
        eo_require [expr {![dict exists $index $name]}] "Ambiguous target $name"
        dict set index $name $dbpin; dict set sta_index $name $pin
    }
    foreach name [dict keys $wanted] {eo_require [dict exists $index $name] "Unresolved physical pin $name"}
    dict for {netname rows} $roots {
        set first [lindex $rows 0]
        set net [[ord::get_db_block] findNet $netname]
        lassign [eo_net $net] driver ordinary diodes
        eo_require [expr {[eo_name $driver] eq [dict get $first driver]}] "Driver changed: $netname"
        eo_require [expr {[[[$driver getInst] getMaster] getName] eq [dict get $first driver_master]}] "Driver master changed"
        eo_require [expr {[llength $ordinary]==[dict get $first expected_ordinary] && [llength $diodes]==[dict get $first expected_diodes]}] "Load count changed: $netname"
        set planned {}; set group_names {}
        foreach row $rows {
            foreach k {kind root_net driver driver_master expected_ordinary expected_diodes total_groups} {
                eo_require [expr {[dict get $row $k] eq [dict get $first $k]}] "Inconsistent plan field $k"
            }
            lappend planned [dict get $index [dict get $row load_pin]]
            lappend group_names [dict get $row buffer_name]
        }
        eo_require [expr {[eo_ids $ordinary] eq [eo_ids $planned]}] "Ordinary load set changed: $netname"
        set n [llength [lsort -unique $group_names]]
        eo_require [expr {$n==[dict get $first total_groups] && $n+[llength $diodes]<=8}] "Invalid root fanout plan"
    }
    return [list $groups $roots $index $sta_index]
}
proc eo_logic {names} {
    set result [dict create]
    set block [ord::get_db_block]
    foreach name $names {
        set inst [$block findInst $name]
        eo_require [expr {$inst ne "NULL"}] "Original instance disappeared: $name"
        dict set result "I:$name" [list [[$inst getMaster] getName] [$inst isDoNotTouch]]
        foreach p [$inst getITerms] {
            if {[$p getSigType] ni {POWER GROUND}} {dict set result "P:[$p getId]" [pc_net_name $p]}
        }
    }
    foreach p [$block getBTerms] {dict set result "B:[$p getName]" [pc_net_name $p]}
    return $result
}
proc eo_check_logic {expected names} {
    set current [eo_logic $names]
    eo_require [expr {[dict size $current]==[dict size $expected]}] "Original connectivity inventory changed"
    dict for {key value} $expected {
        eo_require [expr {[dict exists $current $key] && [dict get $current $key] eq $value}] "Unplanned change: $key"
    }
}
proc eo_location {kind loads} {
    set scale [expr {double([[ord::get_db_block] getDbUnitsPerMicron])}]
    if {$kind eq "slew"} {
        eo_require [expr {[llength $loads]==1}] "Expected one SRAM load"
        set p [lindex $loads 0]; set inst [$p getInst]; set b [$p getBBox]; set ib [$inst getBBox]
        eo_require [expr {[[$inst getMaster] getName] eq "RM_IHPSG13_1P_512x32_c2_bm_bist" && [$inst getOrient] eq "R0"}] "Unexpected SRAM orientation/master"
        return [list [expr {([$b xMin]+[$b xMax])/(2*$scale)}] [expr {[$ib yMin]/$scale-20.0}]]
    }
    set x 0.0; set y 0.0
    foreach p $loads {
        set b [$p getBBox]
        set x [expr {$x+([$b xMin]+[$b xMax])/2.0}]
        set y [expr {$y+([$b yMin]+[$b yMax])/2.0}]
    }
    set scale [expr {$scale*[llength $loads]}]
    return [list [expr {$x/$scale}] [expr {$y/$scale}]]
}
proc eo_check_buffers {buffers group_loads} {
    set block [ord::get_db_block]
    set f [open reports/new-buffer-pg.tsv w]
    puts $f "instance\tpin\tnet"
    set nf [open reports/buffer-nets.tsv w]
    puts $nf "instance\troot_net\tleaf_net"
    set count 0
    dict for {name inst} $buffers {
        eo_require [expr {[[$inst getMaster] getName] eq "sg13g2_buf_4"}] "Buffer master changed: $name"
        foreach rail {VDD VSS} {
            set p [$inst findITerm $rail]
            eo_require [expr {$p ne "NULL" && [pc_net_name $p] eq $rail}] "New buffer supply mismatch: $name/$rail"
            puts $f "$name\t$rail\t[pc_net_name $p]"; incr count
        }
        lassign [eo_net [[$inst findITerm X] getNet]] driver ordinary diodes
        eo_require [expr {[eo_ids $ordinary] eq [eo_ids [dict get $group_loads $name]]}] "Buffer leaf loads changed: $name"
        eo_require [expr {$driver eq [$inst findITerm X]}] "Leaf driver changed"
        puts $nf "$name\t[pc_net_name [$inst findITerm A]]\t[pc_net_name [$inst findITerm X]]"
    }
    close $f; close $nf
    eo_require [expr {$count==26}] "Expected 26 supply pins on 13 new buffers"
}
if {[catch {
    source scripts/startup.tcl
    source $::env(CP0E_HELPERS)/cp0c_pg_audit.tcl
    foreach command {insert_buffer remove_fillers detailed_placement global_route detailed_route repair_antennas} {
        eo_require [expr {[llength [info commands $command]]==1}] "Missing command $command"
    }
    set_thread_count 2
    read_db $::env(CP0E_ODB)
    read_sdc $::env(CP0E_SDC)
    set_propagated_clock [all_clocks]
    tee -file reports/units.rpt {report_units}
    set targets [pc_targets]
    eo_require [expr {[pc_pg_audit initial $targets]==0}] "Initial supply assignments failed"
    check_placement -verbose
    eo_require [expr {[check_antennas -report_file reports/initial-antenna.rpt]==0}] "Original antenna count changed"
    lassign [eo_read_plan] groups roots index sta_index
    # The source remains untouched; remove only the candidate's standard fillers.
    remove_fillers
    set block [ord::get_db_block]
    set original_names {}; set original_positions [dict create]; set fixed [dict create]
    foreach inst [$block getInsts] {
        set name [$inst getName]; set m [$inst getMaster]
        eo_require [expr {![$m isFiller] || [$m isPad] || [$m isCover]}] "Standard fillers remain"
        lappend original_names $name
        dict set original_positions $name [list [$inst getLocation] [$inst getOrient]]
        if {[$m isBlock] || [$m isPad] || [$m isCover]} {dict set fixed $name [dict get $original_positions $name]}
    }
    set expected [eo_logic $original_names]
    setDefaultParasitics
    set_dont_use $dont_use_cells
    set_global_routing_layer_adjustment TopMetal1 0.20
    set_routing_layers -signal Metal2-TopMetal1 -clock Metal2-TopMetal1
    global_route -start_incremental
    set buffers [dict create]; set group_loads [dict create]; set root_buffer_pins [dict create]
    set f [open reports/inserted-buffers.tsv w]
    puts $f "requested_name\tactual_name\tmaster\tkind\troot_net\tx_um\ty_um\tloads"
    dict for {requested rows} $groups {
        set first [lindex $rows 0]; set kind [dict get $first kind]; set root [dict get $first root_net]
        eo_require [expr {[$block findInst $requested] eq "NULL"}] "Buffer name already exists"
        set loads {}; set sta_loads {}
        foreach row $rows {
            set name [dict get $row load_pin]
            lappend loads [dict get $index $name]; lappend sta_loads [dict get $sta_index $name]
        }
        eo_require [expr {[llength $loads]>=1 && [llength $loads]<=3}] "Unexpected group size"
        set location [eo_location $kind $loads]
        set si [insert_buffer -buffer_cell sg13g2_buf_4 -buffer_name $requested \
            -net_name "${requested}_net" -load_pins $sta_loads -location $location]
        eo_require [expr {$si ne "NULL" && $si ne ""}] "Buffer insertion returned no instance"
        set inst [sta::sta_to_db_inst $si]
        eo_require [expr {$inst ne "NULL" && [[$inst getMaster] getName] eq "sg13g2_buf_4"}] "Unexpected inserted buffer"
        set actual [$inst getName]
        set_dont_touch $si
        dict set buffers $actual $inst; dict set group_loads $actual $loads
        set a [$inst findITerm A]; set x [$inst findITerm X]
        eo_require [expr {[pc_net_name $a] eq $root}] "Buffer input not on original root"
        dict lappend root_buffer_pins $root $a
        lassign [eo_net [$x getNet]] driver ordinary diodes
        eo_require [expr {$driver eq $x && [eo_ids $ordinary] eq [eo_ids $loads] && [llength $diodes]==0}] "Inserted buffer connection mismatch"
        foreach p $loads {dict set expected "P:[$p getId]" [pc_net_name $p]}
        puts $f [join [list $requested $actual sg13g2_buf_4 $kind $root {*}$location [llength $loads]] "\t"]
        flush $f
    }
    close $f
    eo_require [expr {[dict size $buffers]==13}] "Insertion count differs"
    dict for {netname rows} $roots {
        lassign [eo_net [$block findNet $netname]] driver ordinary diodes
        eo_require [expr {[eo_ids $ordinary] eq [eo_ids [dict get $root_buffer_pins $netname]]}] "Root load set mismatch"
        eo_require [expr {[llength $ordinary]+[llength $diodes]<=8}] "Root fanout still exceeds eight"
    }
    eo_check_logic $expected $original_names
    pc_fix_target_pg $buffers
    global_connect
    eo_check_buffers $buffers $group_loads
    detailed_placement -incremental
    check_placement -verbose
    global_route -end_incremental -guide_file reports/eco.guide
    eo_check_logic $expected $original_names
    write_db out/eco_ready.odb
    set f [open reports/route-passes.tsv w]
    puts $f "pass\trouter_drvs\tantenna_violating_nets"
    # One ECO route, at most two post-route antenna repair passes. No timing sweeps.
    set prior_antenna -1
    for {set pass 0} {$pass<=2} {incr pass} {
        if {$pass>0} {
            repair_antennas -ratio_margin 30 -iterations 1 -diode_only
            global_connect
            check_placement -verbose
        }
        puts "CP0C_INTERNAL_ROUTE_START pass=$pass threads=2"
        detailed_route -output_drc "reports/pass${pass}-drc.rpt" -drc_report_iter_step 5 \
            -save_guide_updates -clean_patches -verbose 1
        set drvs [detailed_route_num_drvs]
        set ant [check_antennas -report_file "reports/pass${pass}-antenna.rpt"]
        puts $f "$pass\t$drvs\t$ant"; flush $f
        write_db "out/pass${pass}_no_fill.odb"
        eo_require [expr {$drvs==0}] "Detailed-route DRVs remain: $drvs; candidate retained"
        if {$ant==0} {break}
        if {$prior_antenna>=0} {eo_require [expr {$ant<$prior_antenna}] "Antenna count did not improve; candidate retained"}
        set prior_antenna $ant
    }
    close $f
    eo_require [expr {$ant==0}] "Antenna findings remain after bounded repair; candidate retained"
    set added_diodes 0; set diode_area 0.0; set buffer_area 0.0
    set area_scale [expr {double([$block getDbUnitsPerMicron])**2}]
    dict for {name inst} $buffers {
        set m [$inst getMaster]
        set buffer_area [expr {$buffer_area+double([$m getWidth])*[$m getHeight]/$area_scale}]
    }
    set old_set [dict create]; foreach name $original_names {dict set old_set $name 1}
    foreach inst [$block getInsts] {
        set name [$inst getName]
        if {[dict exists $old_set $name] || [dict exists $buffers $name]} {continue}
        eo_require [expr {[[$inst getMaster] getType] eq "CORE_ANTENNACELL"}] "Unplanned added cell: $name"
        incr added_diodes
        set m [$inst getMaster]
        set diode_area [expr {$diode_area+double([$m getWidth])*[$m getHeight]/$area_scale}]
    }
    eo_check_logic $expected $original_names
    eo_check_buffers $buffers $group_loads
    write_db out/routed_no_fill.odb
    filler_placement $stdfill
    global_connect
    check_placement -verbose
    set pg [pc_pg_audit final $targets]
    eo_require [expr {$pg==0}] "Final supply assignments failed"
    eo_check_buffers $buffers $group_loads
    eo_check_logic $expected $original_names
    dict for {name position} $fixed {
        set inst [$block findInst $name]
        eo_require [expr {[list [$inst getLocation] [$inst getOrient]] eq $position}] "Macro/pad moved: $name"
    }
    set f [open reports/moved-original-instances.tsv w]
    puts $f "instance\tbefore\tafter"
    set moved 0
    dict for {name before} $original_positions {
        set inst [$block findInst $name]; set after [list [$inst getLocation] [$inst getOrient]]
        if {$before ne $after} {puts $f "$name\t$before\t$after"; incr moved}
    }
    close $f
    set ant [check_antennas -report_file reports/final-antenna.rpt]
    eo_require [expr {$ant==0 && [design_is_routed -verbose]==1}] "Final routing or antenna check failed"
    write_def out/croc_internal.def
    write_db out/croc_internal.odb
    file copy $::env(CP0E_SDC) out/croc_internal.sdc
    write_verilog out/croc_internal.v
    write_verilog -include_pwr_gnd -remove_cells "$stdfill bondpad*" out/croc_internal_lvs.v
    set f [open reports/physical.tsv w]
    puts $f "router_drvs\t$drvs\nantenna_violating_nets\t$ant\nsupply_assignment_findings\t$pg\ninserted_buffers\t13\nnew_buffer_supply_pins\t26\nadded_diodes\t$added_diodes\nbuffer_area_um2\t$buffer_area\nadded_diode_area_um2\t$diode_area\nmoved_original_instances\t$moved\nstructural_buffer_check\t1"
    close $f
} msg options]} {
    puts stderr "CP0C_INTERNAL_ECO_FAILED: $msg"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    exit 1
}
puts "CP0C_INTERNAL_ECO_COMPLETE"
exit 0
