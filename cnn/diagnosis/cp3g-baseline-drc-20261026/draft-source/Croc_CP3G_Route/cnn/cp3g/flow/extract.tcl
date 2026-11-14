if {[catch {
 source $::env(CP3G_FLOW)/load.tcl
 # No calibrated five-metal RC table is present in the frozen PDK.
 # This is explicitly LEF RC extraction from detailed wires, not signoff PEX.
 extract_parasitics -lef_rc -version 2.0
 write_spef routed.spef
 puts CP3G_LEF_RC_EXTRACTION_COMPLETE
} message options]} {
 puts stderr $message
 puts stderr [dict get $options -errorinfo]
 exit 1
}
exit 0
