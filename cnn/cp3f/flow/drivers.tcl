set f [open drivers.txt r]
set targets [dict create]
foreach n [split [string trim [read $f]] \n] {if {$n ne ""} {dict set targets [string map [list "\\" ""] $n] 1}}
close $f
set count 0
foreach cell [get_cells *] {
 set inst [sta::sta_to_db_inst $cell]
 set name [string map [list "\\" ""] [$inst getName]]
 if {![dict exists $targets $name]} {continue}
 foreach sta_pin [get_pins -of_objects $cell -filter {direction == output}] {
  lassign [sta::sta_to_db_pin $sta_pin] pin bterm
  set net [$pin getNet]
  if {$net eq "NULL" || [$net getSigType] ne "SIGNAL"} {error "Unexpected critical driver net"}
  lassign [$pin getAvgXY] valid x y
  if {!$valid} {error "Missing driver geometry"}
  insert_buffer -buffer_cell sg13g2_buf_4 -net [lindex [get_nets -of_objects $sta_pin] 0] -location [list [expr {$x/1000.0}] [expr {$y/1000.0}]] -buffer_name $::env(CP3F_PREFIX)driver_$count -net_name $::env(CP3F_PREFIX)driver_net_$count
  incr count
 }
 dict unset targets $name
}
if {[dict size $targets]} {error "Critical driver targets absent"}
puts "CP3F_TARGETED_DRIVERS $count"
