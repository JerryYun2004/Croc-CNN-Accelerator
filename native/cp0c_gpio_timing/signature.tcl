proc pc_net_name {pin} {
 set net [$pin getNet]
 if {$net eq "NULL" || $net eq ""} {return UNCONNECTED}
 return [$net getName]
}
proc pc_signal_signature {} {
    set rows {}
    foreach inst [[ord::get_db_block] getInsts] {
        lappend rows [list I [$inst getName] [[$inst getMaster] getName] [$inst getLocation] [$inst getOrient] [$inst isDoNotTouch]]
        foreach pin [$inst getITerms] {
            if {[$pin getSigType] ni {POWER GROUND}} {
                lappend rows [list P [$pin getId] [pc_net_name $pin]]
            }
        }
    }
    foreach pin [[ord::get_db_block] getBTerms] {
        lappend rows [list B [$pin getName] [pc_net_name $pin]]
    }
    return [lsort $rows]
}
