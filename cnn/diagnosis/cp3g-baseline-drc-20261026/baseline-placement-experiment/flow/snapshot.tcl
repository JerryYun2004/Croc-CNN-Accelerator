proc snapshot_connections {file} {
 set out [open $file w]
 set block [ord::get_db_block]
 set records {}
 foreach net [$block getNets] {
  set terms {}
  foreach p [$net getITerms] {lappend terms [list I [[$p getInst] getName] [[$p getMTerm] getName]]}
  foreach p [$net getBTerms] {lappend terms [list B [$p getName]]}
  set rule [$net getNonDefaultRule]
  if {$rule ne "NULL"} {set rule [$rule getName]}
  lappend records [list [$net getName] [$net getSigType] $rule [lsort $terms]]
 }
 foreach r [lsort $records] {puts $out $r}
 close $out
}
