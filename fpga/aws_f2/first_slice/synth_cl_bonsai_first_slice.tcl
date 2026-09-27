# The public AWS header/footer supply shell IO/clock/DDR constraints and DCP flow.
source ${HDK_SHELL_DIR}/build/scripts/synth_cl_header.tcl
read_verilog -sv [glob ${src_post_enc_dir}/*.sv]
read_verilog -sv ${src_post_enc_dir}/cl_id_defines.vh
set_property file_type {Verilog Header} [get_files ${src_post_enc_dir}/cl_id_defines.vh]
# Public DDR wrapper dependencies; omit HBM, traffic generators and scrubbers.
foreach ip {cl_ddr4_32g cl_ddr4_32g_ap cl_ddr4 cl_ddr4_64g_ap axi_register_slice axi_register_slice_light cl_axi_register_slice_light cl_axi_clock_converter cl_axi_clock_converter_light cl_axi_interconnect cl_axi_interconnect_64G_ddr} {
  read_ip ${HDK_IP_SRC_DIR}/${ip}/${ip}.xci
}
read_xdc ${constraints_dir}/cl_synth_user.xdc
read_xdc ${constraints_dir}/cl_timing_user.xdc
set_property PROCESSING_ORDER LATE [get_files cl_synth_user.xdc]
set_property PROCESSING_ORDER LATE [get_files cl_timing_user.xdc]
update_compile_order -fileset sources_1
synth_design -mode out_of_context -top ${CL} -part ${DEVICE_TYPE} -verilog_define XSDB_SLV_DIS -keep_equivalent_registers
source ${HDK_SHELL_DIR}/build/scripts/synth_cl_footer.tcl
