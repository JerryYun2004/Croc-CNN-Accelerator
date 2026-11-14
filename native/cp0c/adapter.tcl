# Loaded immediately after Croc startup. No cell, floorplan, or SDC edits.
# Images require a GUI-enabled OpenROAD; keep this native baseline CLI-only.
rename report_image cp0c_upstream_report_image
proc report_image {args} {
    puts "CP0C: image skipped in CLI build: [lindex $args 0]"
}

# Upstream hardcodes eight threads in placement and routing. Use one recorded
# setting throughout this native run, including when upstream calls it again.
rename set_thread_count cp0c_upstream_set_thread_count
proc set_thread_count {requested} {
    cp0c_upstream_set_thread_count $::env(CP0C_THREADS)
}
set_thread_count $::env(CP0C_THREADS)

# ODB/SDC checkpoint loading does not preserve the previous process's STA RC
# estimates. Avoid presenting unannotated final-stage timing as closure data.
# The dedicated routing audit captures timing with explicit RC estimation.
if {$::env(CP0C_STAGE) eq "finish"} {
    rename report_metrics cp0c_upstream_report_metrics
    proc report_metrics {when args} {
        puts "CP0C: final-stage area only; timing is in reports/cp0c-timing.rpt"
        report_area_hierarchical -csv -filenameCSV "reports/${when}.area.csv"
    }
}
