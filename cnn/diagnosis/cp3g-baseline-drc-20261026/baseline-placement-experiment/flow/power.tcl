if {[catch {
 set_thread_count 2
 read_db $::env(CP3G_INPUT_DB)
 set block [ord::get_db_block]
 set checked 0
 foreach inst [$block getInsts] {
  set master [[$inst getMaster] getName]
  if {![string match sg13g2_* $master]} {continue}
  foreach supply {VDD VSS} {
   set pin [$inst findITerm $supply]
   if {$pin eq "NULL" || [$pin getNet] eq "NULL" || [[$pin getNet] getName] ne $supply} {
    error "Unconnected or misassigned supply: [$inst getName]/$supply"
   }
  }
  incr checked
 }
 puts "CP3G_STANDARD_CELL_SUPPLIES_OK $checked"
 foreach net {VDD VSS VDDIO VSSIO} {
  check_power_grid -net $net -error_file ${net}-power.rpt
  puts "CP3G_POWER_GRID_OK $net"
 }
 puts CP3G_POWER_COMPLETE
} message options]} {
 puts stderr $message
 puts stderr [dict get $options -errorinfo]
 exit 1
}
exit 0
