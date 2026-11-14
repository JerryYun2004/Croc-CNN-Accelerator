# Add geometrically checked SRAM supply arrays to an isolated PDN database.
# Via dimensions inherited from the accepted CP0C SRAM-power repair.
proc cp3b_assert {ok message} { if {!$ok} { error $message } }
proc cp3b_run {} {
    set_thread_count 2
    foreach name {odb::dbVia_create odb::dbBox_create odb::dbSWire_create odb::dbSBox_create} {
        cp3b_assert [expr {[llength [info commands $name]] == 1}] "Missing OpenDB API: $name"
    }
    read_db $::env(CP3B_ODB)
    set block [ord::get_db_block]
    set tech [$block getTech]
    cp3b_assert [expr {[$block getDbUnitsPerMicron] == 1000}] "Expected 1000 DBU/um"
    cp3b_assert [expr {[$block getName] eq "croc_chip"}] "Unexpected top block"
    cp3b_assert [expr {[$block findVia CP3B_SRAM_TV1_3X3] eq "NULL"}] "ECO via already exists"
    write_def $::env(CP3B_BEFORE_DEF)
    set layers [dict create]
    foreach name {Metal4 TopVia1 TopMetal1} {
        set layer [$tech findLayer $name]
        cp3b_assert [expr {$layer ne "NULL"}] "Missing layer $name"
        dict set layers $name $layer
    }
    set file [open $::env(CP3B_PLAN) r]
    cp3b_assert [expr {[gets $file] eq "net\tinstance\tpin\tx\ty"}] "Wrong plan header"
    set rows {}
    set resolved [dict create]
    while {[gets $file line] >= 0} {
        if {$line eq ""} { continue }
        set row [split $line "\t"]
        cp3b_assert [expr {[llength $row] == 5}] "Malformed placement record"
        lassign $row netname instname pinname x y
        cp3b_assert [expr {$netname in {VDD VSS} && [string is integer -strict $x] && [string is integer -strict $y]}] "Invalid placement"
        set inst [$block findInst $instname]
        cp3b_assert [expr {$inst ne "NULL"}] "Missing SRAM instance $instname"
        cp3b_assert [expr {[$inst getName] eq $instname}] "SRAM lookup returned a different name"
        cp3b_assert [expr {[[$inst getMaster] getName] eq "RM_IHPSG13_1P_512x32_c2_bm_bist"}] "Unexpected SRAM master"
        set iterm [$inst findITerm $pinname]
        cp3b_assert [expr {$iterm ne "NULL"}] "Missing SRAM supply pin $pinname"
        cp3b_assert [expr {[$iterm getNet] ne "NULL"}] "Unassigned SRAM supply"
        cp3b_assert [expr {[[$iterm getNet] getName] eq $netname}] "SRAM supply assignment differs"
        dict set resolved $instname $inst
        lappend rows $row
    }
    close $file
    cp3b_assert [expr {[llength $rows] == $::env(CP3B_ARRAYS)}] "Wrong array count"
    cp3b_assert [expr {[dict size $resolved] == $::env(CP3B_MACROS)}] "Wrong macro count"
    dict for {name inst} $resolved { puts "SRAM_INSTANCE_RESOLVED: $name" }
    puts "SRAM_SUPPLY_PLAN_VALIDATED: instances=[dict size $resolved] arrays=[llength $rows]"
    set via [odb::dbVia_create $block CP3B_SRAM_TV1_3X3]
    cp3b_assert [expr {$via ne "NULL"}] "Cannot create ECO via"
    set b [odb::dbBox_create $via [dict get $layers Metal4] -1150 -1150 1150 1150]
    cp3b_assert [expr {$b ne "NULL"}] "Cannot create Metal4 enclosure"
    set b [odb::dbBox_create $via [dict get $layers TopMetal1] -1470 -1470 1470 1470]
    cp3b_assert [expr {$b ne "NULL"}] "Cannot create TopMetal1 enclosure"
    foreach x {-840 0 840} {
        foreach y {-840 0 840} {
            set b [odb::dbBox_create $via [dict get $layers TopVia1] [expr {$x-210}] [expr {$y-210}] [expr {$x+210}] [expr {$y+210}]]
            cp3b_assert [expr {$b ne "NULL"}] "Cannot create cut"
        }
    }
    set wires [dict create]
    foreach name {VDD VSS} {
        set net [$block findNet $name]
        cp3b_assert [expr {$net ne "NULL"}] "Missing power net $name"
        set sw [odb::dbSWire_create $net ROUTED]
        cp3b_assert [expr {$sw ne "NULL"}] "Cannot create special wire"
        dict set wires $name $sw
    }
    foreach row $rows {
        lassign $row netname instname pinname x y
        set box [odb::dbSBox_create [dict get $wires $netname] $via $x $y STRIPE]
        cp3b_assert [expr {$box ne "NULL"}] "Cannot create via at $x $y"
    }
    write_def $::env(CP3B_AFTER_DEF)
    write_db $::env(CP3B_AFTER_ODB)
    puts "CP3B_SRAM_POWER_ECO_COMPLETE arrays=[llength $rows] cuts=[expr {9*[llength $rows]}]"
}
if {[catch {cp3b_run} message options]} {
    puts stderr "CP3B_SRAM_POWER_ECO_FAILED: $message"
    if {[dict exists $options -errorinfo]} { puts stderr [dict get $options -errorinfo] }
    exit 1
}
exit 0
