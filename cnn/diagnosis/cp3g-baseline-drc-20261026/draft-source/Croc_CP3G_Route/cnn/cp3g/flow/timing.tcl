if {[catch {
 source $::env(CP3G_FLOW)/load.tcl
 # Use the same nominal LEF RC at both original cell-library corners.
 # Never substitute global-route or placement estimates for missing SPEF.
 read_spef -name cp3g_tt -corner tt $::env(CP3G_SPEF)
 read_spef -name cp3g_ff -corner ff $::env(CP3G_SPEF)
 report_parasitic_annotation -name cp3g_tt -report_unannotated > annotation-tt.rpt
 report_parasitic_annotation -name cp3g_ff -report_unannotated > annotation-ff.rpt
 report_parasitic_annotation -name cp3g_ff -report_unannotated > annotation.rpt
 cp3g_reports final
 write_sdc -no_timestamp propagated.sdc
 puts CP3G_SPEF_TIMING_COMPLETE
} message options]} {
 puts stderr $message
 puts stderr [dict get $options -errorinfo]
 exit 1
}
exit 0
