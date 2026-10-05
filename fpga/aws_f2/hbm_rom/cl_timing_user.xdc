# Explicit CDC exceptions only on the first stages of two-flop handshakes.
set hs_sync [get_cells -hier -quiet -regexp {.*HBM_LINES/.*(request_sync_q_reg|acknowledge_sync_q_reg)\[0\]}]
if {[llength $hs_sync] != 8} { error "Expected eight first-stage mailbox synchronizers" }
set_false_path -to [get_pins -of_objects $hs_sync -filter {REF_PIN_NAME == D}]
set status_sync [get_cells -hier -quiet -regexp {.*HBM_LINES/(ready_sync_q_reg|fault_sync_q_reg|core_fault_sync_q_reg)\[0\]}]
if {[llength $status_sync] != 3} { error "Expected three first-stage HBM status synchronizers" }
set_false_path -to [get_pins -of_objects $status_sync -filter {REF_PIN_NAME == D}]
# Bundled data stays stable until acknowledgement. Bound every source payload
# register to all of its capture endpoints to less than the shortest (HBM)
# clock period (2.222ns). Do not replace this with set_clock_groups/false_path.
foreach box {write_command read_command write_response read_response} {
  set payload [get_cells -hier -quiet -regexp ".*HBM_LINES/${box}/payload_q_reg\\\[.*\\\]"]
  if {[llength $payload] == 0} { error "Missing bundled-data CDC source: $box" }
  set_max_delay 2.000 -datapath_only -from $payload
  set_bus_skew 2.000 -from $payload
}
