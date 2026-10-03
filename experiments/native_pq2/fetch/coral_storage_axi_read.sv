// E1-fetch read-only HBM/DDR adapter. No bandwidth or FPGA timing claim is made.
// Offset space belongs to the immutable model image; use the target shell map
// for HBM_BASE and channel mapping. Reset must also flush the AXI read channel.
module coral_storage_axi_read #(
  parameter int DATA_BITS=128, ID_BITS=6,
  parameter logic [63:0] HBM_BASE=64'h0,
  parameter int unsigned FETCH_DEPTH=4
)(
  input logic clk,reset,
  input logic request_valid, output logic request_ready,
  input logic [31:0] request_addr,
  output logic response_valid,response_error,
  output logic [DATA_BITS-1:0] response_data,
  input logic response_ready,
  output logic [63:0] m_araddr,output logic [ID_BITS-1:0] m_arid,
  output logic [7:0] m_arlen,output logic [2:0] m_arsize,
  output logic [1:0] m_arburst,
  output logic m_arvalid,input logic m_arready,
  input logic [DATA_BITS-1:0] m_rdata,input logic [1:0] m_rresp,
  input logic m_rlast,m_rvalid,output logic m_rready
);
  localparam int COUNT_BITS=$clog2(FETCH_DEPTH+1);
  logic [COUNT_BITS-1:0] outstanding;
  logic credit_available,request_fire,response_fire;

  initial begin
    if(DATA_BITS!=128) $fatal(1,"E1-fetch adapter requires 128-bit storage beats");
    if(FETCH_DEPTH<1 || FETCH_DEPTH>32) $fatal(1,"FETCH_DEPTH must be in [1,32]");
  end

  // There is no hidden address buffer: a storage request and its AXI AR are
  // accepted on the same edge. The request producer must hold valid/address
  // while !request_ready, as required by the native storage handshake.
  assign credit_available=int'(outstanding)<FETCH_DEPTH;
  assign request_ready=credit_available&&m_arready;
  assign m_arvalid=request_valid&&credit_available;
  assign m_araddr=HBM_BASE+{32'b0,request_addr};
  assign m_arid='0;
  assign m_arlen=0;
  assign m_arsize=3'($clog2(DATA_BITS/8));
  assign m_arburst=1;

  // All requests use ID zero and length zero; the backing AXI system must
  // return one beat for each request in order. AXI response backpressure
  // propagates directly to storage, without a second data staging buffer.
  assign response_valid=(outstanding!=0)&&m_rvalid;
  assign response_data=m_rdata;
  assign response_error=(m_rresp!=0)||!m_rlast;
  assign m_rready=(outstanding!=0)&&response_ready;
  assign request_fire=request_valid&&request_ready;
  assign response_fire=response_valid&&response_ready;

  always_ff @(posedge clk) begin
    if(reset) outstanding<='0;
    else begin
      case({request_fire,response_fire})
        2'b10: outstanding<=outstanding+1'b1;
        2'b01: outstanding<=outstanding-1'b1;
        default: ;
      endcase
    end
  end
endmodule
