if {[catch {
 source init_tech.tcl
 read_def work/full/baseline/routed.def
 set_routing_layers -signal Metal2-TopMetal1 -clock Metal2-TopMetal1
 set_thread_count 2
 drt::check_drc -box {555.5 838.5 560.0 843.5} -output_file native-local-drc.rpt
 puts CP3G_FOCUSED_CHECK_COMPLETE
} message options]} {
 puts stderr $message
 puts stderr [dict get $options -errorinfo]
 exit 1
}
exit 0
