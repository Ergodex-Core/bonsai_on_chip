# Out-of-context leaf estimate. Run only in an allocated licensed remote worker.
# vivado -mode batch -source synth_leaf.tcl -tclargs RTL PART OUT PERIOD_NS MODE ?NAME=VALUE ...?
# MODE is synth or route. PART must come from the selected platform manifest.
# This does not include the shell, HBM, Coral Core, firmware memories or routing.
if {$argc < 5} { error "Required: RTL PART OUT PERIOD_NS synth|route ?NAME=VALUE ...?" }
set source_path [file normalize [lindex $argv 0]]
set target_part [lindex $argv 1]
set output_dir [file normalize [lindex $argv 2]]
set period_ns [lindex $argv 3]
set run_mode [lindex $argv 4]
if {![file exists $source_path]} { error "RTL file missing" }
if {![string is double -strict $period_ns] || $period_ns <= 0} { error "Invalid clock period" }
if {$run_mode ni {synth route}} { error "MODE must be synth or route" }
if {[file exists $output_dir]} { error "Output directory exists; preserve prior evidence" }
file mkdir $output_dir
set synth_args [list -top coral_weight_axi -part $target_part -mode out_of_context]
foreach setting [lrange $argv 5 end] {
  if {![regexp {^(DOT_LANES|FETCH_DEPTH)=[1-9][0-9]*$} $setting]} { error "Unsupported generic" }
  lappend synth_args -generic $setting
}
set_param general.maxThreads 2
read_verilog -sv $source_path
synth_design {*}$synth_args
create_clock -name engine_clk -period $period_ns [get_ports clk]
report_utilization -file [file join $output_dir synthesis_utilization.rpt]
report_timing_summary -file [file join $output_dir synthesis_timing_estimate.rpt]
write_checkpoint [file join $output_dir synthesized.dcp]
if {$run_mode eq "route"} {
  opt_design
  place_design
  route_design
  report_utilization -file [file join $output_dir routed_utilization.rpt]
  report_timing_summary -report_unconstrained -file [file join $output_dir routed_timing.rpt]
  report_route_status -file [file join $output_dir route_status.rpt]
  write_checkpoint [file join $output_dir routed.dcp]
}
set receipt [open [file join $output_dir scope.txt] w]
puts $receipt "tool=[version -short]"
puts $receipt "part=$target_part"
puts $receipt "period_ns=$period_ns"
puts $receipt "mode=$run_mode"
puts $receipt "scope=out-of-context engine leaf only; excludes shell, HBM, Coral Core, firmware memories"
puts $receipt "parameters=[lrange $argv 5 end]"
close $receipt
