# Native HBM hard block/IP constraints place the controller. Keep the bridge
# in SLR0 with HBM to limit the bundled-data crossing distance.
set hbm_logic [get_cells -quiet {WRAPPER/CL/HBM_LINES WRAPPER/CL/HBM_CONTROLLER}]
if {[llength $hbm_logic] != 2} { error "Missing HBM hierarchy in routed shell" }
set_property USER_SLR_ASSIGNMENT SLR0 $hbm_logic
