# CP3A addition: lower-left/lower-right CNN banks, with pins facing the core.
# The original firmware banks and all pad locations remain unchanged.
set extra [list]
foreach inst [[ord::get_db_block] getInsts] {
  if {[[$inst getMaster] getName] eq "RM_IHPSG13_1P_512x32_c2_bm_bist" && [string first "i_user/" [$inst getName]] >= 0} {
    lappend extra [$inst getName]
  }
}
set extra [lsort $extra]
if {[llength $extra] != $::env(CP3A_EXTRA_MACROS)} {error "Wrong CNN SRAM count"}
if {[llength $extra] == 2} {
  placeInstance [lindex $extra 0] $bank0X $floor_bottomY MX
  placeInstance [lindex $extra 1] $bank1X $floor_bottomY MX
}
