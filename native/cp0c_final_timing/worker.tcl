# Final-candidate analysis only. No placement, routing, repair, or write_db.
proc ft_require {ok message} {if {!$ok} {error $message}}
proc ft_annotation {} {
    report_parasitic_annotation -report_unannotated > reports/parasitic-annotation.rpt
    set f [open reports/parasitic-annotation.rpt r]
    set a [pa_annotation_sections [read $f]]
    close $f
    rc_inventory [dict get $a full]
    set f [open reports/annotation-counts.tsv w]
    foreach key {full partial loads} {puts $f "$key\t[llength [dict get $a $key]]"}
    close $f
    set f [open reports/partial-drivers.txt w]
    foreach row [dict get $a partial] {puts $f $row}
    close $f
    set f [open reports/partial-loads.tsv w]
    puts $f "driver\tmissing_load"
    foreach row [dict get $a loads] {puts $f [join $row "\t"]}
    close $f
}
proc ft_inventory {} {
    set block [ord::get_db_block]
    set ports {}; set records {}; set expected {jtag_tdo_o status_o uart_tx_o unused0_o unused1_o unused2_o unused3_o}
    for {set bit 0} {$bit<32} {incr bit} {lappend expected "gpio${bit}_io"}
    set names {}
    set f [open reports/io-ports.tsv w]
    puts $f "port\tnet\tpad_instance\tpad_master\tpad_pin"
    foreach port [all_outputs] {
        set name [sta::get_name $port]
        # The original SDC also applied 15 pF to four supply ports. Leave them alone.
        if {$name in {VDD VDDIO VSS VSSIO}} {continue}
        lappend names $name; lappend ports $port
        set bterm [sta::sta_to_db_port $port]
        set net [$bterm getNet]
        ft_require [expr {$net ne "NULL"}] "Unconnected output port $name"
        set pads {}
        foreach pin [$net getITerms] {
            set inst [$pin getInst]; set master [[$inst getMaster] getName]
            if {[string match sg13cmos5l_IOPad* $master] && [[$pin getMTerm] getName] eq "pad"} {
                lappend pads [list [$inst getName] $master "[$inst getName]/pad"]
            }
        }
        ft_require [expr {[llength $pads]==1}] "Expected one signal pad for $name; found $pads"
        set row [concat [list $name [$net getName]] [lindex $pads 0]]
        puts $f [join $row "\t"]
        lappend records [list $name [sta::db_net_to_sta $net] [lindex [lindex $pads 0] 2]]
    }
    close $f
    ft_require [expr {[lsort $names] eq [lsort $expected]}] "Unexpected output/inout signal-port set: $names"
    set f [open reports/io-pins.txt w]
    foreach port [get_ports *] {puts $f [sta::get_name $port]}
    foreach inst [$block getInsts] {
        if {[string match sg13cmos5l_IOPad* [[$inst getMaster] getName]]} {
            foreach p [$inst getITerms] {puts $f "[$inst getName]/[[$p getMTerm] getName]"}
        }
    }
    close $f
    return [list $ports $records]
}
proc ft_pad_reports {label records} {
    set folder "reports/${label}-pads"
    file mkdir $folder
    foreach item $records {
        lassign $item port net pad
        foreach corner {tt ff} {
            # A net-scoped report includes both pad and top-port driver checks.
            report_check_types -net $net -corner $corner -max_slew -max_capacitance \
                -max_count 100 -no_line_splits -digits 9 > "$folder/${port}-${corner}.rpt"
            tee -quiet -file "$folder/${port}-${corner}-slews.rpt" \
                [list report_slews -corner $corner -digits 9 $pad]
        }
    }
}
proc ft_main {} {
    source $::env(FT_CONFIG)
    source $::env(FT_HELPERS)/signature.tcl
    source $::env(FT_HELPERS)/cp0c_route_metrics.tcl
    source $::env(FT_HELPERS)/cp0c_postroute_annotation.tcl
    source $::env(FT_HELPERS)/cp0c_annotation_sections.tcl
    file mkdir reports out save
    set_thread_count 2
    puts "PHASE: load pinned Liberty libraries and physical database"
    define_corners tt ff
    foreach corner {tt ff} {
        foreach lib $::ft_libs($corner) {read_liberty -corner $corner $lib}
    }
    read_db $::env(FT_ODB)
    set before [pc_signal_signature]
    tee -file reports/units.rpt {report_units}
    set mode $::env(FT_MODE)
    if {$mode eq "extract"} {
        set metals {}
        set f [open reports/lef-rc.tsv w]
        puts $f "layer\tlevel\twidth_dbu\tresistance\tcapacitance\tedge_capacitance"
        foreach layer [[ord::get_db_tech] getLayers] {
            set level [$layer getRoutingLevel]
            if {$level>0} {
                lappend metals [$layer getName]
                ft_require [expr {[$layer getWidth]>0 && [$layer getResistance]>0 &&
                    ([$layer getCapacitance]>0 || [$layer getEdgeCapacitance]>0)}] "Missing LEF RC"
            }
            puts $f [join [list [$layer getName] $level [$layer getWidth] [$layer getResistance] \
                [$layer getCapacitance] [$layer getEdgeCapacitance]] "\t"]
        }
        close $f
        ft_require [expr {[lsort $metals] eq [lsort {Metal1 Metal2 Metal3 Metal4 TopMetal1}]}] "Unexpected metal stack"
        ft_require [expr {[design_is_routed -verbose]==1}] "Candidate contains unrouted nets"
        puts "PHASE: extract repaired candidate using corrected LEF RC"
        extract_parasitics -version 2.0 -lef_rc -skip_over_cell \
            -no_merge_via_res -max_res 0.000001 -coupling_threshold 0.1
        write_spef out/final-lef.spef
        ft_require [expr {[file size out/final-lef.spef]>1024}] "Empty extracted SPEF"
    } elseif {$mode in {control sweep}} {
        read_sdc $::env(FT_SDC)
        set_propagated_clock [all_clocks]
        ft_require [expr {[llength [all_clocks]]==3}] "Unexpected clock count"
        foreach {name period} {clk_sys 10.0 clk_jtg 25.0 clk_rtc 50.0} {
            set c [get_clocks $name]
            ft_require [expr {[llength $c]==1 && abs([get_property $c period]-$period)<1e-6}] "Unexpected clock $name"
        }
        ft_require [expr {[llength [get_pins pad_gpio*_io/c2p_en]]==32}] "GPIO enable coverage differs"
        foreach corner {tt ff} {read_spef -corner $corner $::env(FT_SPEF)}
        lassign [ft_inventory] signal_ports pad_records
        write_sdc out/original-runtime.sdc
        puts "PHASE: original 15 pF timing and annotation coverage ($mode)"
        # This public timing query must precede internal endpoint counters.
        erc_snapshot original_15pf
        ft_annotation
        report_check_types -min_pulse_width -min_period -violators -digits 9 > reports/clock-checks.rpt
        report_checks -path_delay max -group_path_count 20 -slack_max 0 \
            -format full_clock_expanded -fields {slew capacitance fanout} -digits 9 > reports/setup-violations.rpt
        report_checks -path_delay min -group_path_count 20 -slack_max 0 \
            -format full_clock_expanded -fields {slew capacitance fanout} -digits 9 > reports/hold-violations.rpt
        if {$mode eq "sweep"} {
            ft_pad_reports original_15pf $pad_records
            foreach {label load} $::ft_samples {
                puts "PHASE: load sweep $load pF; GPIO enables remain dynamic"
                set_load -pin_load $load $signal_ports
                erc_snapshot $label
                ft_pad_reports $label $pad_records
            }
            puts "PHASE: restore original 15 pF and verify repeatability"
            set_load -pin_load 15.0 $signal_ports
            erc_snapshot restored_15pf
            write_sdc out/restored-runtime.sdc
        }
    } else {error "Unknown mode $mode"}
    ft_require [expr {[pc_signal_signature] eq $before}] "Signal connectivity, cell masters or placement changed"
    set f [open reports/physical.tsv w]
    puts $f "signal_signature_unchanged\t1\nsaved_design_written\t0"
    close $f
    puts "CP0C_FINAL_TIMING_WORKER_COMPLETE $mode"
}
if {[catch {ft_main} message options]} {
    puts stderr "CP0C_FINAL_TIMING_WORKER_FAILED: $message"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    exit 1
}
exit 0
