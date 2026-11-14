# Explicit catch/exit ensures Tcl errors cannot be mistaken for success.
if {[catch {
    source $::env(CP0C_STAGE_FILE)
    source $::env(CP0C_HELPERS)/audit.tcl
} message options]} {
    puts stderr "CP0C_STAGE_FAILED $::env(CP0C_STAGE): $message"
    if {[dict exists $options -errorinfo]} {
        puts stderr [dict get $options -errorinfo]
    }
    exit 1
}
puts "CP0C_STAGE_COMPLETE $::env(CP0C_STAGE)"
exit 0
