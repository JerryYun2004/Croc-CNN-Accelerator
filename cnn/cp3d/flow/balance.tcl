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
set cp3d_fanout_file [open fanout.tsv w]
puts $cp3d_fanout_file "net\toriginal_loads\tbuffers"
set cp3d_fanout_serial 0
foreach cp3d_net [get_nets *] {
    set cp3d_dbnet [sta::sta_to_db_net $cp3d_net]
    if {[$cp3d_dbnet getSigType] ne "SIGNAL"} {continue}
    set cp3d_loads [get_pins -of_objects $cp3d_net -filter {direction == input}]
    if {[llength $cp3d_loads] <= 8} {continue}
    set cp3d_rows {}
    foreach pin $cp3d_loads {lappend cp3d_rows [cp3d_pinrow $pin]}
    set count_before $cp3d_fanout_serial
    while {[llength $cp3d_rows] > 8} {
        set next_rows {}
        foreach group [cp3d_cluster $cp3d_rows 0] {
            set sx 0;set sy 0;set pins {}
            foreach row $group {lassign $row x y pin;incr sx $x;incr sy $y;lappend pins $pin}
            set n [llength $group]
            set x [expr {$sx/double($n)/1000.0}];set y [expr {$sy/double($n)/1000.0}]
            set name cp3d_fanout_buf_$cp3d_fanout_serial
            set wire cp3d_fanout_net_$cp3d_fanout_serial
            incr cp3d_fanout_serial
            set created [insert_buffer -buffer_cell sg13g2_buf_4 -load_pins $pins -location [list $x $y] -buffer_name $name -net_name $wire]
            set input [get_pins -of_objects $created -filter {direction == input}]
            if {[llength $input]!=1} {error "Fanout buffer input missing"}
            lappend next_rows [cp3d_pinrow $input]
        }
        set cp3d_rows $next_rows
    }
    puts $cp3d_fanout_file "[get_full_name $cp3d_net]\t[llength $cp3d_loads]\t[expr {$cp3d_fanout_serial-$count_before}]"
}
close $cp3d_fanout_file
puts "CP3D_FANOUT_BUFFERS $cp3d_fanout_serial"
