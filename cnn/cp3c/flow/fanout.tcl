# Deterministic buffer trees for large non-clock fanout, without new constraints.
# Existing signal polarities and leaf functions are audited after implementation.
proc cp3c_cluster {rows depth} {
    if {[llength $rows] <= 32} {return [list $rows]}
    set sorted [lsort -integer -index [expr {$depth % 2}] $rows]
    set mid [expr {[llength $sorted]/2}]
    return [concat [cp3c_cluster [lrange $sorted 0 [expr {$mid-1}]] [expr {$depth+1}]] \
        [cp3c_cluster [lrange $sorted $mid end] [expr {$depth+1}]]]
}
proc cp3c_pinrow {pin} {
    lassign [sta::sta_to_db_pin $pin] iterm bterm
    if {$iterm eq "NULL"} {error "Large fanout load must be an instance terminal"}
    set inst [$iterm getInst]
    lassign [$inst getLocation] x y
    set master [$inst getMaster]
    return [list [expr {$x+[$master getWidth]/2}] [expr {$y+[$master getHeight]/2}] $pin]
}
set cp3c_fanout_file [open fanout.tsv w]
puts $cp3c_fanout_file "net\toriginal_loads\tbuffers"
set cp3c_fanout_serial 0
foreach cp3c_net [get_nets *] {
    set cp3c_dbnet [sta::sta_to_db_net $cp3c_net]
    if {[$cp3c_dbnet getSigType] ne "SIGNAL"} {continue}
    set cp3c_loads [get_pins -of_objects $cp3c_net -filter {direction == input}]
    if {[llength $cp3c_loads] < 256} {continue}
    set cp3c_rows {}
    foreach pin $cp3c_loads {lappend cp3c_rows [cp3c_pinrow $pin]}
    set count_before $cp3c_fanout_serial
    while {[llength $cp3c_rows] > 32} {
        set next_rows {}
        foreach group [cp3c_cluster $cp3c_rows 0] {
            set sx 0;set sy 0;set pins {}
            foreach row $group {lassign $row x y pin;incr sx $x;incr sy $y;lappend pins $pin}
            set n [llength $group]
            set x [expr {$sx/double($n)/1000.0}];set y [expr {$sy/double($n)/1000.0}]
            set name cp3c_fanout_buf_$cp3c_fanout_serial
            set wire cp3c_fanout_net_$cp3c_fanout_serial
            incr cp3c_fanout_serial
            set created [insert_buffer -buffer_cell sg13g2_buf_16 -load_pins $pins -location [list $x $y] -buffer_name $name -net_name $wire]
            set input [get_pins -of_objects $created -filter {direction == input}]
            if {[llength $input]!=1} {error "Fanout buffer input missing"}
            lappend next_rows [cp3c_pinrow $input]
        }
        set cp3c_rows $next_rows
    }
    puts $cp3c_fanout_file "[get_full_name $cp3c_net]\t[llength $cp3c_loads]\t[expr {$cp3c_fanout_serial-$count_before}]"
}
close $cp3c_fanout_file
puts "CP3C_FANOUT_BUFFERS $cp3c_fanout_serial"
