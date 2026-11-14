# Pure Tcl parser for the pinned OpenSTA report_parasitic_annotation format.
# One space introduces a driver; two spaces introduce a missing load under a
# partially annotated driver. Preserve both sections and all reported names.
proc pa_require {ok message} {if {!$ok} {error $message}}
proc pa_annotation_sections {text} {
    set mode ""
    set expected_full -1
    set expected_partial -1
    set full {}; set partial {}; set loads {}
    set driver ""
    foreach line [split $text "\n"] {
        if {[string trim $line] eq ""} {continue}
        if {[regexp {^Found ([0-9]+) unannotated drivers\.$} $line -> count]} {
            pa_require [expr {$expected_full == -1 && $mode eq ""}] "Duplicate or misplaced unannotated-driver header"
            set expected_full $count
            set mode full
        } elseif {[regexp {^Found ([0-9]+) partially unannotated drivers\.$} $line -> count]} {
            pa_require [expr {$expected_full >= 0 && $expected_partial == -1}] "Duplicate or misplaced partial-annotation header"
            set expected_partial $count
            set mode partial
        } elseif {[regexp {^  ([^ ].*)$} $line -> name]} {
            pa_require [expr {$mode eq "partial" && $driver ne ""}] "Missing load without a partial driver: $line"
            lappend loads [list $driver $name]
        } elseif {[regexp {^ ([^ ].*)$} $line -> name]} {
            if {$mode eq "full"} {
                lappend full $name
            } elseif {$mode eq "partial"} {
                set driver $name
                lappend partial $name
            } else {error "Driver before annotation header: $line"}
        } else {error "Unexpected annotation report line: $line"}
    }
    pa_require [expr {$expected_full >= 0 && $expected_partial >= 0}] "Missing annotation report section"
    pa_require [expr {[llength $full] == $expected_full}] "Fully unannotated driver count mismatch"
    pa_require [expr {[llength $partial] == $expected_partial}] "Partially unannotated driver count mismatch"
    pa_require [expr {[llength [lsort -unique $full]] == [llength $full]}] "Duplicate fully unannotated driver"
    pa_require [expr {[llength [lsort -unique $partial]] == [llength $partial]}] "Duplicate partially unannotated driver"
    # A partial driver can have no printed loads: OpenSTA only prints its load
    # list when findParasiticNetwork() returns an explicit network object.
    return [dict create full $full partial $partial loads $loads]
}
