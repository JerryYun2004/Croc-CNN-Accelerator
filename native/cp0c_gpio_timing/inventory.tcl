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
