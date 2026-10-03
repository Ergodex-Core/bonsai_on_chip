module pch_fixture (
    input logic clk,
    input logic reset,
    input logic enable,
    input logic [7:0] data,
    input logic [7:0] mask,
    output logic [7:0] state,
    output logic [15:0] result
);
  always_ff @(posedge clk) begin
    if (reset) state <= 8'd0;
    else if (enable) state <= state + data;
  end
  assign result = {8'd0, (state ^ mask)} + {8'd0, data};
endmodule
