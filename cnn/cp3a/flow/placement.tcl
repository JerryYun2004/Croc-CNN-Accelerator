# Placement feasibility only: no resizing, CTS, PDN generation or routing.
write_db floorplan.odb
write_def floorplan.def
set_thread_count 2
global_placement -density 0.60
detailed_placement
check_placement -verbose
write_db placed.odb
write_def placed.def
set f [open instances.tsv w]
puts $f "name\tmaster\ttype\tstatus\torient\tx0\ty0\tx1\ty1"
foreach inst [[ord::get_db_block] getInsts] {
  set m [$inst getMaster]; set b [$inst getBBox]
  puts $f [join [list [$inst getName] [$m getName] [$m getType] [$inst getPlacementStatus] [$inst getOrient] [$b xMin] [$b yMin] [$b xMax] [$b yMax]] "\t"]
}
close $f
set f [open geometry.tsv w]
puts $f "dbu\t[[ord::get_db_block] getDbUnitsPerMicron]"
set die [[ord::get_db_block] getDieArea]
puts $f [join [concat die [list [$die xMin] [$die yMin] [$die xMax] [$die yMax]]] "\t"]
puts $f [join [concat core [ord::get_core_area]] "\t"]
set rowArea 0
foreach row [[ord::get_db_block] getRows] {
  set box [$row getBBox]
  set rowArea [expr {$rowArea + ([$box xMax]-[$box xMin])*wide([$box yMax]-[$box yMin])}]
}
puts $f "row_area_dbu2\t$rowArea"
close $f
puts "CP3A_PLACEMENT_PASS"
