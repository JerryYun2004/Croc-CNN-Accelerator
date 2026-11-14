if {[catch {
 source $::env(CP3F_FLOW)/common.tcl
 detailed_placement
 check_placement -verbose
 set fh [open targets.txt r]
 set targets [split [string trim [read $fh]] \n]
 close $fh
 set count 0
 foreach cell [get_cells *] {
  set inst [sta::sta_to_db_inst $cell]
  if {[$inst getName] ni $targets} {continue}
  if {![string match sg13g2_dfrbpq_* [[$inst getMaster] getName]]} {error "Unexpected endpoint master"}
  foreach sta_pin [get_pins -of_objects $cell] {
   lassign [sta::sta_to_db_pin $sta_pin] pin bterm
   if {[[$pin getMTerm] getName] ne "CLK"} {continue}
   if {[[$pin getNet] getSigType] ne "CLOCK"} {error "Target is not on an existing clock net"}
   lassign [$pin getAvgXY] valid x y
   if {!$valid} {error "Missing clock geometry"}
   insert_buffer -buffer_cell sg13g2_buf_1 -load_pins [list $sta_pin] -location [list [expr {$x/1000.0}] [expr {$y/1000.0}]] -buffer_name $::env(CP3F_PREFIX)capture_$count -net_name $::env(CP3F_PREFIX)capture_net_$count
   [$pin getNet] setSigType CLOCK
   incr count
  }
 }
 if {$count!=[llength $targets]} {error "Target count mismatch"}
 puts "CP3F_CAPTURE_CLOCKS $count"
 detailed_placement
 check_placement -verbose
 puts CP3F_FINAL_PLACEMENT_OK
 cp3f_route_report
 puts CP3F_STAGE_COMPLETE
} message options]} {
 puts stderr $message
 puts stderr [dict get $options -errorinfo]
 exit 1
}
exit 0
