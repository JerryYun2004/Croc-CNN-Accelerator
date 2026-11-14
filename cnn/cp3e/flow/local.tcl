set targets [dict create]
set f [open local-targets.tsv r]
foreach line [split [string trim [read $f]] \n] {
 if {$line eq ""} {continue}
 lassign [split $line \t] type name
 if {$type ni {hold sram} || $name eq ""} {error "Invalid local target"}
 dict set targets $name $type
}
close $f
set hold_count 0
set sram_count 0
foreach cell [get_cells *] {
 set inst [sta::sta_to_db_inst $cell]
 foreach pin [get_pins -of_objects $cell -filter {direction == input}] {
  set name [string map [list "\\" ""] [get_full_name $pin]]
  if {![dict exists $targets $name]} {continue}
  set type [dict get $targets $name]
  lassign [sta::sta_to_db_pin $pin] iterm bterm
  lassign [$iterm getAvgXY] valid x y
  if {!$valid} {error "Missing local pin geometry"}
  if {$type eq "hold"} {
   if {[[$iterm getMTerm] getName] ne "D" || ![string match sg13g2_df* [[$inst getMaster] getName]]} {error "Hold target is not a supported flip-flop D pin"}
   set master sg13g2_dlygate4sd3_1
   set number $hold_count
   incr hold_count
  } else {
   if {[[$inst getMaster] getName] ne "RM_IHPSG13_1P_512x32_c2_bm_bist"} {error "Slew target is not SRAM"}
   set master sg13g2_buf_8
   set number $sram_count
   incr sram_count
  }
  insert_buffer -buffer_cell $master -load_pins [list $pin] -location [list [expr {$x/1000.0}] [expr {$y/1000.0}]] -buffer_name cp3e_${type}_${number} -net_name cp3e_${type}_net_${number}
  dict unset targets $name
 }
}
if {[dict size $targets]} {error "Unmatched local targets"}
puts "CP3E_LOCAL_HOLD_BUFFERS $hold_count"
puts "CP3E_SRAM_BUFFERS $sram_count"
