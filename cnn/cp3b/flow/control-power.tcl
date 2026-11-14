# A real engine-level negative control on the unmodified generated grid.
if {[catch {
    set_thread_count 2
    read_db grid.odb
    if {[catch {check_power_grid -net VDD -error_file control-uncontacted-vdd.rpt} message]} {
        if {![string match {*PSM-0069*} $message]} {error "Unexpected negative-control failure: $message"}
        puts CP3B_EXPECTED_UNCONTACTED_SRAM_REJECTION
    } else {
        error "Uncontacted SRAM grid unexpectedly passed VDD connectivity"
    }
} message options]} {
    puts stderr $message
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
