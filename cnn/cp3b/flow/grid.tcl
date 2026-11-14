if {[catch {
set_thread_count 2
source scripts/init_tech_sg13cmos5l.tcl
read_def $::env(CP3B_DEF)
read_sdc src/constraints.sdc
source scripts/power_connect.tcl
write_def before.def
add_pdn_ring -grid core_grid -layers {TopMetal1 Metal4} -widths {8 8} -spacings {4 4} -core_offsets {7.5 7.5} -connect_to_pads -connect_to_pad_layers TopMetal1
add_pdn_stripe -grid core_grid -layer Metal1 -width 0.32 -offset 0 -followpins -extend_to_core_ring
add_pdn_stripe -grid core_grid -layer TopMetal1 -width 3 -spacing 4 -pitch 90 -offset -14.28 -starts_with GROUND -extend_to_core_ring -snap_to_grid
add_pdn_stripe -grid core_grid -layer Metal4 -width 1 -spacing 89 -pitch 180 -offset 45 -starts_with POWER -extend_to_core_ring -snap_to_grid
add_pdn_connect -grid core_grid -layers {TopMetal1 Metal4}
add_pdn_connect -grid core_grid -layers {Metal4 Metal1}
pdngen -failed_via_report failed-vias.rpt
write_db grid.odb
write_def grid.def
puts "CP3B_GRID_CREATED"

} msg opts]} {puts stderr $msg; puts stderr [dict get $opts -errorinfo];exit 1}
exit 0
