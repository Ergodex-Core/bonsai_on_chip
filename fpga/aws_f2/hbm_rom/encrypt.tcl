# Original CL file staging. AWS framework provides variables and encryption keys.
foreach path [glob -nocomplain -directory $src_post_enc_dir *] {
  file delete -force $path
}
foreach path [glob -directory $CL_DIR/design *.sv *.vh] {
  file copy -force $path $src_post_enc_dir
}
if {$ENCRYPT} {
  encrypt -k ${HDK_SHELL_DIR}/build/scripts/vivado_keyfile.txt -lang verilog -quiet \
    [glob ${src_post_enc_dir}/*.sv ${src_post_enc_dir}/*.vh]
}
