-define CL_NAME=cl_bonsai_first_slice
-define DISABLE_VJTAG_DEBUG
-include $CL_DIR/verif/tests
-f ${HDK_COMMON_DIR}/verif/tb/filelists/tb.${SIMULATOR}.f
${TEST_NAME}
-include $CL_DIR/design
${CL_DIR}/design/dot128.sv
${CL_DIR}/design/weight_store.sv
${CL_DIR}/design/first_slice_top.sv
${CL_DIR}/design/f2_memory_bridge.sv
${CL_DIR}/design/cl_bonsai_first_slice.sv
