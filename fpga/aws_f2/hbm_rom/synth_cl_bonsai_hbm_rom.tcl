# Pinned AWS small-shell flow; native HBM and fixed H2 clock only.
source ${HDK_SHELL_DIR}/build/scripts/synth_cl_header.tcl
if {$clock_recipe_hbm ne "H2"} { error "HBM ROM requires fixed H2 (450MHz)" }
read_verilog -sv [glob ${src_post_enc_dir}/*.sv]
read_verilog -sv ${src_post_enc_dir}/cl_id_defines.vh
set_property file_type {Verilog Header} [get_files ${src_post_enc_dir}/cl_id_defines.vh]
foreach ip {cl_hbm clk_mmcm_hbm} {
  read_ip ${HDK_IP_SRC_DIR}/${ip}/${ip}.xci
}
read_xdc ${constraints_dir}/cl_synth_user.xdc
read_xdc ${constraints_dir}/cl_timing_user.xdc
set_property PROCESSING_ORDER LATE [get_files cl_synth_user.xdc]
set_property PROCESSING_ORDER LATE [get_files cl_timing_user.xdc]
update_compile_order -fileset sources_1
synth_design -mode out_of_context -top ${CL} -part ${DEVICE_TYPE} -verilog_define XSDB_SLV_DIS -keep_equivalent_registers
source ${HDK_SHELL_DIR}/build/scripts/synth_cl_footer.tcl
