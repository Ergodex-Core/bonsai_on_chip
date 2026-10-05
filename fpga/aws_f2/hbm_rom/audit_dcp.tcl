# Read-only post-route evidence extraction; missing/negative paths fail closed.
if {$argc != 2} { error "Usage: audit_dcp.tcl routed.dcp report_directory" }
set routed [lindex $argv 0]
set output [lindex $argv 1]
open_checkpoint $routed
report_timing_summary -delay_type min_max -check_timing_verbose -file ${output}/timing_summary.rpt
report_utilization -hierarchical -file ${output}/utilization.rpt
report_clocks -file ${output}/clocks.rpt
report_drc -file ${output}/drc.rpt
report_cdc -details -file ${output}/cdc.rpt
report_exceptions -coverage -file ${output}/exceptions.rpt
if {[llength [get_timing_paths -quiet -delay_type max -max_paths 1]] == 0} {
  error "No constrained setup path in routed DCP"
}
foreach delay {max min} {
  if {[llength [get_timing_paths -quiet -delay_type $delay -slack_lesser_than 0 -max_paths 1]] > 0} {
    error "Negative $delay timing slack in routed DCP"
  }
}
foreach box {write_command read_command write_response read_response} {
  set payload [get_cells -hier -quiet -regexp ".*HBM_LINES/${box}/payload_q_reg\\\[.*\\\]"]
  if {[llength $payload] == 0} { error "Missing bundled-data CDC source: $box" }
  set paths [get_timing_paths -quiet -from $payload -max_paths 1]
  if {[llength $paths] == 0} { error "No constrained bundled-data path: $box" }
  if {[get_property REQUIREMENT $paths] > 2.001} { error "CDC maximum delay missing: $box" }
}
set bad_drc [get_drc_violations -quiet -filter {SEVERITY == Error}]
if {[llength $bad_drc]} { error "DRC errors in routed DCP" }
puts "HBM_ROM_DCP_TIMING_PASS_CDC_REVIEW_REQUIRED"
close_design
