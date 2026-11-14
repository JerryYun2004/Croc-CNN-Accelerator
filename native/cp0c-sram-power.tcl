# Add only the reviewed SRAM supply vias to an in-memory copy of the source ODB.
proc cp0_assert {ok message} { if {!$ok} { error $message } }
proc cp0_run {} {
    set_thread_count 2
    foreach name {odb::dbVia_create odb::dbBox_create odb::dbSWire_create odb::dbSBox_create} {
        cp0_assert [expr {[llength [info commands $name]] == 1}] "Missing OpenDB API: $name"
    }
    read_db $::env(CP0P_ODB)
    set block [ord::get_db_block]
    set tech [$block getTech]
    cp0_assert [expr {[$block getDbUnitsPerMicron] == 1000}] "Expected 1000 DBU/um"
    cp0_assert [expr {[$block getName] eq "croc_chip"}] "Unexpected top block"
    cp0_assert [expr {[$block findVia CP0C_SRAM_TV1_3X3] eq "NULL"}] "ECO via already exists"
    write_def $::env(CP0P_BEFORE_DEF)
    set layers [dict create]
    foreach name {Metal4 TopVia1 TopMetal1} {
        set layer [$tech findLayer $name]
        cp0_assert [expr {$layer ne "NULL"}] "Missing layer $name"
        dict set layers $name $layer
    }
    set file [open $::env(CP0P_PLAN) r]
    cp0_assert [expr {[gets $file] eq "net\tinstance\tpin\tx\ty"}] "Wrong plan header"
    set rows {}
    set resolved [dict create]
    while {[gets $file line] >= 0} {
        if {$line eq ""} { continue }
        set row [split $line "\t"]
        cp0_assert [expr {[llength $row] == 5}] "Malformed placement record"
        lassign $row netname instname pinname x y
        cp0_assert [expr {$netname in {VDD VSS} && [string is integer -strict $x] && [string is integer -strict $y]}] "Invalid placement"
        set inst [$block findInst $instname]
        cp0_assert [expr {$inst ne "NULL"}] "Missing SRAM instance $instname"
        cp0_assert [expr {[$inst getName] eq $instname}] "SRAM lookup returned a different name"
        cp0_assert [expr {[[$inst getMaster] getName] eq "RM_IHPSG13_1P_512x32_c2_bm_bist"}] "Unexpected SRAM master"
        set iterm [$inst findITerm $pinname]
        cp0_assert [expr {$iterm ne "NULL"}] "Missing SRAM supply pin $pinname"
        cp0_assert [expr {[$iterm getNet] ne "NULL"}] "Unassigned SRAM supply"
        cp0_assert [expr {[[$iterm getNet] getName] eq $netname}] "SRAM supply assignment differs"
        dict set resolved $instname $inst
        lappend rows $row
    }
    close $file
    cp0_assert [expr {[llength $rows] == 288}] "Expected exactly 288 arrays"
    cp0_assert [expr {[dict size $resolved] == 2}] "Expected exactly two SRAM instances"
    dict for {name inst} $resolved { puts "SRAM_INSTANCE_RESOLVED: $name" }
    puts "SRAM_SUPPLY_PLAN_VALIDATED: instances=2 arrays=288"
    set via [odb::dbVia_create $block CP0C_SRAM_TV1_3X3]
    cp0_assert [expr {$via ne "NULL"}] "Cannot create ECO via"
    set b [odb::dbBox_create $via [dict get $layers Metal4] -1150 -1150 1150 1150]
    cp0_assert [expr {$b ne "NULL"}] "Cannot create Metal4 enclosure"
    set b [odb::dbBox_create $via [dict get $layers TopMetal1] -1470 -1470 1470 1470]
    cp0_assert [expr {$b ne "NULL"}] "Cannot create TopMetal1 enclosure"
    foreach x {-840 0 840} {
        foreach y {-840 0 840} {
            set b [odb::dbBox_create $via [dict get $layers TopVia1] [expr {$x-210}] [expr {$y-210}] [expr {$x+210}] [expr {$y+210}]]
            cp0_assert [expr {$b ne "NULL"}] "Cannot create cut"
        }
    }
    set wires [dict create]
    foreach name {VDD VSS} {
        set net [$block findNet $name]
        cp0_assert [expr {$net ne "NULL"}] "Missing power net $name"
        set sw [odb::dbSWire_create $net ROUTED]
        cp0_assert [expr {$sw ne "NULL"}] "Cannot create special wire"
        dict set wires $name $sw
    }
    foreach row $rows {
        lassign $row netname instname pinname x y
        set box [odb::dbSBox_create [dict get $wires $netname] $via $x $y STRIPE]
        cp0_assert [expr {$box ne "NULL"}] "Cannot create via at $x $y"
    }
    write_def $::env(CP0P_AFTER_DEF)
    write_db $::env(CP0P_AFTER_ODB)
    puts "CP0C_SRAM_POWER_ECO_COMPLETE arrays=288 cuts=2592"
}
if {[catch {cp0_run} message options]} {
    puts stderr "CP0C_SRAM_POWER_ECO_FAILED: $message"
    if {[dict exists $options -errorinfo]} { puts stderr [dict get $options -errorinfo] }
    exit 1
}
exit 0
