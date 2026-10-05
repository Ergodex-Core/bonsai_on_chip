ifndef HDK_COMMON_DIR
$(error Source the pinned hdk_setup.sh first)
endif
include $(HDK_COMMON_DIR)/verif/tb/scripts/Makefile.header.inc

# The AWS shell BFM links this DPI support even for SystemVerilog-only tests.
C_FILES = $(C_TEST_NAME) $(C_SDK_USR_UTILS_DIR)/sh_dpi_tasks.c $(C_COMMON_SRC_DIR)/fpga_pci_sv.c
C_INCLUDE_DIRS = $(C_SDK_USR_INC_DIR) $(C_SDK_USR_UTILS_DIR) $(C_COMMON_DIR)/include $(C_COMMON_SRC_DIR)

include $(HDK_COMMON_DIR)/verif/tb/scripts/Makefile.common.inc
