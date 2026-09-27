# Read-only routed-DCP report extraction. Reject negative setup or hold paths.
if {$argc != 2} { error "Usage: audit_dcp.tcl routed.dcp report_directory" }
set routed [lindex $argv 0]
set output [lindex $argv 1]
open_checkpoint $routed
report_timing_summary -delay_type min_max -check_timing_verbose -file ${output}/timing_summary.rpt
report_utilization -hierarchical -file ${output}/utilization.rpt
report_clocks -file ${output}/clocks.rpt
report_drc -file ${output}/drc.rpt
if {[llength [get_timing_paths -quiet -delay_type max -max_paths 1]] == 0} {
  error "No constrained setup path in routed DCP"
}
foreach delay {max min} {
  if {[llength [get_timing_paths -quiet -delay_type $delay -slack_lesser_than 0 -max_paths 1]] > 0} {
    error "Negative $delay timing slack in routed DCP"
  }
}
puts "FIRST_SLICE_DCP_TIMING_PASS"
close_design
