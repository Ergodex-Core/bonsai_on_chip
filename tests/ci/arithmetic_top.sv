// Instantiate production RTL unchanged; expose scalar ports for both simulators.
module arithmetic_top (
  input logic [31:0] a, b, c,
  input logic cin,
  input logic [4:0] shift,
  input logic [1:0] mode,
  output logic [31:0] sum, shifted, compressed_sum, compressed_carry,
  output logic cout,
  output logic [7:0] sum8,
  output logic cout8
);
  adder #(.ADD_NUM(1), .ADD_WIDTH(32)) add32 (.a(a), .b(b), .cin(cin), .sum(sum), .cout(cout));
  adder #(.ADD_NUM(1), .ADD_WIDTH(8)) add8 (.a(a[7:0]), .b(b[7:0]), .cin(cin), .sum(sum8), .cout(cout8));
  barrel_shifter #(.DATA_WIDTH(32)) shifter (.din(a), .shift_amount(shift), .shift_mode(mode), .dout(shifted));
  compressor_3_2 #(.WIDTH(32)) compressor (.src1(a), .src2(b), .src3(c), .result_sum(compressed_sum), .result_carry(compressed_carry));
endmodule
