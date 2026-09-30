// Copyright 2026 Ergodex. Licensed under the Apache License, Version 2.0.
// AXI-Lite BAR0: weight-store page 0, DOT128 mailbox page 1.
module first_slice_top (
    input logic clk_i,
    rst_ni,
    input logic [31:0] s_axi_awaddr,
    input logic s_axi_awvalid,
    output logic s_axi_awready,
    input logic [31:0] s_axi_wdata,
    input logic [3:0] s_axi_wstrb,
    input logic s_axi_wvalid,
    output logic s_axi_wready,
    output logic [1:0] s_axi_bresp,
    output logic s_axi_bvalid,
    input logic s_axi_bready,
    input logic [31:0] s_axi_araddr,
    input logic s_axi_arvalid,
    output logic s_axi_arready,
    output logic [31:0] s_axi_rdata,
    output logic [1:0] s_axi_rresp,
    output logic s_axi_rvalid,
    input logic s_axi_rready,
    output logic mem_req_valid_o,
    input logic mem_req_ready_i,
    output logic [63:0] mem_req_addr_o,
    input logic mem_rsp_valid_i,
    output logic mem_rsp_ready_o,
    input logic [511:0] mem_rsp_data_i,
    input logic mem_rsp_error_i,
    output logic loader_enable_o,
    output logic [63:0] image_base_o,
    image_bytes_o,
    input logic loader_idle_i,
    backend_ready_i,
    loader_write_rejected_i
);
  logic aw_hold, w_hold;
  logic [31:0] aw_addr, w_data;
  logic [3:0] w_strb;
  logic wr_fire, store_wr, store_werror, store_rerror, mb_werror, mb_rerror;
  logic [31:0] store_rdata, mb_rdata;
  logic store_ready, store_fault;
  logic [31:0] store_epoch;
  logic busy, done, cmd_pending, dot_fatal, job_fault;
  logic [31:0] cookie, opcode, elements, weight_lo, weight_hi, job_epoch;
  logic [1023:0] activations;
  logic [31:0] result_acc, completed_cookie;
  logic [15:0] result_scale;
  logic [ 2:0] result_status;
  logic [63:0] result_cycles, result_stalls, result_reads;
  logic dot_cmd_ready, dot_result_valid;
  logic signed [31:0] dot_result_acc;
  logic [15:0] dot_result_scale;
  logic [2:0] dot_result_status;
  logic [63:0] dot_cycles, dot_stalls, dot_reads;
  logic req_valid, req_ready, rsp_valid, rsp_ready;
  logic [47:0] req_offset;
  logic [7:0] req_tag, rsp_tag;
  logic [31:0] req_epoch, rsp_epoch;
  logic [511:0] rsp_data;
  logic [  2:0] rsp_status;

  assign s_axi_awready = !aw_hold && !s_axi_bvalid;
  assign s_axi_wready = !w_hold && !s_axi_bvalid;
  assign s_axi_arready = !s_axi_rvalid;
  assign wr_fire = aw_hold && w_hold && !s_axi_bvalid;
  assign store_wr = wr_fire && aw_addr[31:12] == 0;

  weight_store store (
      .clk_i,
      .rst_ni,
      .cfg_write_i(store_wr),
      .cfg_waddr_i(aw_addr[11:0]),
      .cfg_raddr_i(s_axi_araddr[11:0]),
      .cfg_wdata_i(w_data),
      .cfg_wstrb_i(w_strb),
      .cfg_werror_o(store_werror),
      .cfg_rerror_o(store_rerror),
      .cfg_rdata_o(store_rdata),
      .loader_idle_i,
      .backend_ready_i,
      .loader_write_rejected_i,
      .client_fault_i(dot_fatal),
      .loader_enable_o,
      .ready_o(store_ready),
      .fault_o(store_fault),
      .image_base_o,
      .image_bytes_o,
      .epoch_o(store_epoch),
      .req_valid_i(req_valid),
      .req_ready_o(req_ready),
      .req_offset_i(req_offset),
      .req_tag_i(req_tag),
      .req_epoch_i(req_epoch),
      .rsp_valid_o(rsp_valid),
      .rsp_ready_i(rsp_ready),
      .rsp_data_o(rsp_data),
      .rsp_tag_o(rsp_tag),
      .rsp_epoch_o(rsp_epoch),
      .rsp_status_o(rsp_status),
      .mem_req_valid_o,
      .mem_req_ready_i,
      .mem_req_addr_o,
      .mem_rsp_valid_i,
      .mem_rsp_ready_o,
      .mem_rsp_data_i,
      .mem_rsp_error_i
  );
  dot128 dot (
      .clk_i,
      .rst_ni,
      .cmd_valid_i(cmd_pending),
      .cmd_ready_o(dot_cmd_ready),
      .cmd_weight_offset_i({weight_hi[15:0], weight_lo}),
      .cmd_format_i(opcode == 2),
      .cmd_epoch_i(job_epoch),
      .cmd_activations_i(activations),
      .mem_req_valid_o(req_valid),
      .mem_req_ready_i(req_ready),
      .mem_req_offset_o(req_offset),
      .mem_req_tag_o(req_tag),
      .mem_req_epoch_o(req_epoch),
      .mem_rsp_valid_i(rsp_valid),
      .mem_rsp_ready_o(rsp_ready),
      .mem_rsp_data_i(rsp_data),
      .mem_rsp_tag_i(rsp_tag),
      .mem_rsp_epoch_i(rsp_epoch),
      .mem_rsp_status_i(rsp_status),
      .result_valid_o(dot_result_valid),
      .result_ready_i(busy || job_fault),
      .result_acc_o(dot_result_acc),
      .result_scale_o(dot_result_scale),
      .result_status_o(dot_result_status),
      .result_cycles_o(dot_cycles),
      .result_mem_stall_cycles_o(dot_stalls),
      .result_read_requests_o(dot_reads),
      .fatal_o(dot_fatal)
  );

  always_comb begin
    mb_werror = 1;
    if (aw_addr[1:0] == 0 && w_strb == 0) mb_werror = 0;
    else if (aw_addr[1:0] == 0 && w_strb == 4'hf) begin
      case (aw_addr[11:0])
        12'h010: begin
          if (w_data == 1) mb_werror = busy || done || dot_fatal || job_fault || !dot_cmd_ready;
          else if (w_data == 2) mb_werror = busy || !done;
        end
        12'h018, 12'h01c, 12'h020, 12'h024, 12'h028, 12'h02c: mb_werror = busy || done;
        default: if (aw_addr[11:0] >= 12'h080 && aw_addr[11:0] <= 12'h0fc) mb_werror = busy || done;
      endcase
    end
    mb_rerror = s_axi_araddr[1:0] != 0;
    mb_rdata  = 0;
    case (s_axi_araddr[11:0])
      12'h000: mb_rdata = 32'h444f5431;  // DOT1
      12'h004: mb_rdata = 32'h00010000;
      12'h008: mb_rdata = 7;  // native Q1_0, ternary2, external sealed DDR
      12'h00c:
      mb_rdata = {26'b0, job_fault, store_fault, dot_fatal, (result_status != 0), done, busy};
      12'h014: mb_rdata = {29'b0, result_status};
      12'h018: mb_rdata = cookie;
      12'h01c: mb_rdata = opcode;
      12'h020: mb_rdata = elements;
      12'h024: mb_rdata = weight_lo;
      12'h028: mb_rdata = weight_hi;
      12'h02c: mb_rdata = job_epoch;
      12'h030: mb_rdata = result_acc;
      12'h034: mb_rdata = {16'b0, result_scale};
      12'h038: mb_rdata = completed_cookie;
      12'h040: mb_rdata = result_cycles[31:0];
      12'h044: mb_rdata = result_cycles[63:32];
      12'h048: mb_rdata = result_stalls[31:0];
      12'h04c: mb_rdata = result_stalls[63:32];
      12'h050: mb_rdata = result_reads[31:0];
      12'h054: mb_rdata = result_reads[63:32];
      default: begin
        if (s_axi_araddr[11:0] >= 12'h080 && s_axi_araddr[11:0] <= 12'h0fc)
          mb_rdata = activations[32*s_axi_araddr[6:2]+:32];
        else mb_rerror = 1;
      end
    endcase
  end

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      aw_hold <= 0;
      w_hold <= 0;
      aw_addr <= 0;
      w_data <= 0;
      w_strb <= 0;
      s_axi_bvalid <= 0;
      s_axi_bresp <= 0;
      s_axi_rvalid <= 0;
      s_axi_rdata <= 0;
      s_axi_rresp <= 0;
      busy <= 0;
      done <= 0;
      cmd_pending <= 0;
      job_fault <= 0;
      cookie <= 0;
      opcode <= 1;
      elements <= 128;
      weight_lo <= 0;
      weight_hi <= 0;
      job_epoch <= 0;
      activations <= 0;
      result_acc <= 0;
      result_scale <= 0;
      result_status <= 0;
      completed_cookie <= 0;
      result_cycles <= 0;
      result_stalls <= 0;
      result_reads <= 0;
    end else begin
      if (s_axi_awvalid && s_axi_awready) begin
        aw_hold <= 1;
        aw_addr <= s_axi_awaddr;
      end
      if (s_axi_wvalid && s_axi_wready) begin
        w_hold <= 1;
        w_data <= s_axi_wdata;
        w_strb <= s_axi_wstrb;
      end
      if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 0;
      if (wr_fire) begin
        aw_hold <= 0;
        w_hold <= 0;
        s_axi_bvalid <= 1;
        if (aw_addr[31:12] == 0) s_axi_bresp <= store_werror ? 2'b10 : 2'b00;
        else if (aw_addr[31:12] == 1) s_axi_bresp <= mb_werror ? 2'b10 : 2'b00;
        else s_axi_bresp <= 2'b11;
      end
      if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 0;
      if (s_axi_arvalid && s_axi_arready) begin
        s_axi_rvalid <= 1;
        if (s_axi_araddr[31:12] == 0) begin
          s_axi_rdata <= store_rdata;
          s_axi_rresp <= store_rerror ? 2'b10 : 2'b00;
        end else if (s_axi_araddr[31:12] == 1) begin
          s_axi_rdata <= mb_rdata;
          s_axi_rresp <= mb_rerror ? 2'b10 : 2'b00;
        end else begin
          s_axi_rdata <= 0;
          s_axi_rresp <= 2'b11;
        end
      end
      if (cmd_pending && dot_cmd_ready) cmd_pending <= 0;
      if (busy && dot_result_valid) begin
        busy <= 0;
        done <= 1;
        completed_cookie <= cookie;
        result_acc <= dot_result_acc;
        result_scale <= dot_result_scale;
        result_status <= dot_result_status;
        result_cycles <= dot_cycles;
        result_stalls <= dot_stalls;
        result_reads <= dot_reads;
      end
      if (wr_fire && aw_addr[31:12] == 1 && !mb_werror && w_strb == 4'hf) begin
        case (aw_addr[11:0])
          12'h018: cookie <= w_data;
          12'h01c: opcode <= w_data;
          12'h020: elements <= w_data;
          12'h024: weight_lo <= w_data;
          12'h028: weight_hi <= w_data;
          12'h02c: job_epoch <= w_data;
          12'h010: begin
            if (w_data == 2) begin
              done <= 0;
              result_status <= 0;
            end else begin
              result_acc <= 0;
              result_scale <= 0;
              result_status <= 0;
              result_cycles <= 0;
              result_stalls <= 0;
              result_reads <= 0;
              if (elements != 128 || (opcode != 1 && opcode != 2) || weight_hi[31:16] != 0) begin
                done <= 1;
                result_status <= 7;
                completed_cookie <= cookie;
              end else if (!store_ready) begin
                done <= 1;
                result_status <= 6;
                completed_cookie <= cookie;
              end else begin
                busy <= 1;
                cmd_pending <= 1;
              end
            end
          end
          default: activations[32*aw_addr[6:2]+:32] <= w_data;
        endcase
      end
      // A backend can fail after START but before either logical line request
      // is accepted. The store then rejects new admissions, so waiting only
      // for the dot result would deadlock the mailbox. Report one failed job
      // without cancelling any held memory request or outstanding response.
      // Those drain through the original owner; a result can retire silently,
      // but this sticky abort latch prohibits every new START until reset.
      // Zero counters mean this aborted job has no completed-dot measurement.
      if (busy && store_fault) begin
        busy <= 0;
        done <= 1;
        cmd_pending <= 0;
        job_fault <= 1;
        completed_cookie <= cookie;
        result_acc <= 0;
        result_scale <= 0;
        result_status <= 4;
        result_cycles <= 0;
        result_stalls <= 0;
        result_reads <= 0;
      end
    end
  end
  // ABI exposes the store epoch through its own control page; consuming code
  // must copy that value into each job descriptor, never assume a fixed epoch.
  logic unused_epoch;
  assign unused_epoch = ^store_epoch;
endmodule
