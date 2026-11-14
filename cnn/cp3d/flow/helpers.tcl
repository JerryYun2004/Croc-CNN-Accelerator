# Deterministic buffer trees for large non-clock fanout, without new constraints.
# Existing signal polarities and leaf functions are audited after implementation.
proc cp3d_cluster {rows depth} {
    if {[llength $rows] <= 8} {return [list $rows]}
    set sorted [lsort -integer -index [expr {$depth % 2}] $rows]
    set mid [expr {[llength $sorted]/2}]
    return [concat [cp3d_cluster [lrange $sorted 0 [expr {$mid-1}]] [expr {$depth+1}]] \
        [cp3d_cluster [lrange $sorted $mid end] [expr {$depth+1}]]]
}
proc cp3d_pinrow {pin} {
    lassign [sta::sta_to_db_pin $pin] iterm bterm
    if {$iterm eq "NULL"} {error "Large fanout load must be an instance terminal"}
    set inst [$iterm getInst]
    lassign [$inst getLocation] x y
    set master [$inst getMaster]
    return [list [expr {$x+[$master getWidth]/2}] [expr {$y+[$master getHeight]/2}] $pin]
}
