-define CL_NAME=cl_bonsai_hbm_rom
-define DISABLE_VJTAG_DEBUG
-include $CL_DIR/verif/tests
-f ${HDK_COMMON_DIR}/verif/tb/filelists/tb.${SIMULATOR}.f
${TEST_NAME}
-include $CL_DIR/design
${CL_DIR}/design/dot128.sv
${CL_DIR}/design/weight_store.sv
${CL_DIR}/design/first_slice_top.sv
${CL_DIR}/design/f2_memory_bridge.sv
${CL_DIR}/design/hbm_cdc_mailbox.sv
${CL_DIR}/design/hbm_line_bridge.sv
${CL_DIR}/design/hbm_fixed_clock.sv
${CL_DIR}/design/hbm_rom_controller.sv
${CL_DIR}/design/cl_bonsai_hbm_rom.sv
