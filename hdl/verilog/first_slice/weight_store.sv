// Copyright 2026 Ergodex. Licensed under the Apache License, Version 2.0.
// Version 1 read-only image control and 16-credit logical-line frontend.
module weight_store #(
    parameter logic [31:0] BACKEND_ID = 2,
    parameter logic [63:0] PHYSICAL_BYTES = 64'h400000000
) (
    input logic clk_i,
    rst_ni,
    input logic cfg_write_i,
    input logic [11:0] cfg_waddr_i,
    cfg_raddr_i,
    input logic [31:0] cfg_wdata_i,
    input logic [3:0] cfg_wstrb_i,
    output logic cfg_werror_o,
    cfg_rerror_o,
    output logic [31:0] cfg_rdata_o,
    input logic loader_idle_i,
    backend_ready_i,
    client_fault_i,
    loader_write_rejected_i,
    output logic loader_enable_o,
    ready_o,
    fault_o,
    output logic [63:0] image_base_o,
    image_bytes_o,
    output logic [31:0] epoch_o,
    input logic req_valid_i,
    output logic req_ready_o,
    input logic [47:0] req_offset_i,
    input logic [7:0] req_tag_i,
    input logic [31:0] req_epoch_i,
    output logic rsp_valid_o,
    input logic rsp_ready_i,
    output logic [511:0] rsp_data_o,
    output logic [7:0] rsp_tag_o,
    output logic [31:0] rsp_epoch_o,
    output logic [2:0] rsp_status_o,
    output logic mem_req_valid_o,
    input logic mem_req_ready_i,
    output logic [63:0] mem_req_addr_o,
    input logic mem_rsp_valid_i,
    output logic mem_rsp_ready_o,
    input logic [511:0] mem_rsp_data_i,
    input logic mem_rsp_error_i
);
  localparam logic [2:0] EMPTY = 0, LOADING = 1, VERIFYING = 2, SEALED = 3, RUNNING = 4, FAULT = 5;
  localparam logic [2:0] OK=0, ALIGNMENT=1, RANGE=2, STALE_EPOCH=3, BACKEND=4, PROTOCOL=7;
  localparam logic [2:0] FREE = 0, WAIT_READ = 1, ON_BUS = 2, COMPLETE = 3, PRESENTED = 4;
  logic [2:0] state;
  logic [31:0] expected_hash[8], readback_hash[8];
  logic [7:0] expected_mask, readback_mask;
  logic hash_equal, valid_range, stopping;
  logic [31:0] rejected_writes;
  logic rejected_saturated;
  logic [2:0] fault_code;
  logic [47:0] fault_offset;
  logic [63:0] reads_completed, stall_cycles, reads_snapshot, stalls_snapshot;
  logic [2:0] slot_state[16], slot_status[16];
  logic [47:0] slot_offset[16];
  logic [7:0] slot_tag[16];
  logic [31:0] slot_epoch[16];
  logic [511:0] slot_data[16];
  logic free_found, read_found, complete_found, duplicate, any_slot;
  logic [3:0] free_index, read_index, complete_index, presented_index;
  logic issue_valid, bus_pending;
  logic [3:0] issue_index, read_cursor, response_cursor;
  logic [63:0] issue_addr;
  logic write_effect, fatal_event;
  logic [ 1:0] rejection_events;
  logic [32:0] rejected_sum;
  logic [ 2:0] incoming_status;

  logic [63:0] image_base, image_bytes;
  assign image_base_o = image_base;
  assign image_bytes_o = image_bytes;
  assign loader_enable_o = state == LOADING && backend_ready_i;
  assign ready_o = ((state == SEALED) || (state == RUNNING)) && backend_ready_i && !stopping;
  assign fault_o = state == FAULT;
  assign req_ready_o = ready_o && free_found && !duplicate && !client_fault_i;
  assign mem_req_valid_o = issue_valid;
  assign mem_req_addr_o = issue_addr;
  assign mem_rsp_ready_o = bus_pending;
  assign rejection_events = {1'b0, (cfg_write_i && cfg_werror_o)} + {1'b0, loader_write_rejected_i};
  assign rejected_sum = {1'b0, rejected_writes} + {31'b0, rejection_events};
  assign write_effect = cfg_write_i && !cfg_werror_o && cfg_wstrb_i == 4'hf;
  // Hold an already-presented memory request stable through a fault until its
  // handshake. IDs are not reused until its eventual response is drained.
  assign fatal_event = client_fault_i ||
      ((state != EMPTY && state != FAULT) && !backend_ready_i) ||
      (bus_pending && mem_rsp_valid_i && mem_rsp_error_i);

  always_comb begin
    free_found = 0;
    read_found = 0;
    complete_found = 0;
    duplicate = 0;
    any_slot = 0;
    free_index = 0;
    read_index = 0;
    complete_index = 0;
    for (int i = 0; i < 16; i++) begin
      if (slot_state[i] == FREE && !free_found) begin
        free_found = 1;
        free_index = 4'(i);
      end
      if (slot_state[i] != FREE) any_slot = 1;
      if (slot_state[i] != FREE && slot_tag[i] == req_tag_i) duplicate = 1;
      if (slot_state[(i+int'(read_cursor))&15] == WAIT_READ && !read_found) begin
        read_found = 1;
        read_index = 4'(i + int'(read_cursor));
      end
      if (slot_state[(i+int'(response_cursor))&15] == COMPLETE && !complete_found) begin
        complete_found = 1;
        complete_index = 4'(i + int'(response_cursor));
      end
    end
    hash_equal = expected_mask == 8'hff && readback_mask == 8'hff;
    for (int i = 0; i < 8; i++) hash_equal = hash_equal && expected_hash[i] == readback_hash[i];
    // Backend capacity is a build-time bound, never a mutable address alias.
    // Every backend retains the agreed 256 MiB logical aperture.
    valid_range=image_bytes >= 64 && image_bytes <= 64'h10000000 &&
        image_bytes[5:0] == 0 && image_base[5:0] == 0 &&
        image_base < PHYSICAL_BYTES && image_bytes <= PHYSICAL_BYTES-image_base;
    incoming_status = OK;
    if (req_offset_i[5:0] != 0) incoming_status = ALIGNMENT;
    else if (image_bytes < 64 || {16'b0, req_offset_i} > image_bytes - 64) incoming_status = RANGE;
    else if (req_epoch_i != epoch_o) incoming_status = STALE_EPOCH;
  end

  always_comb begin
    cfg_werror_o = 1;
    if (cfg_waddr_i[1:0] == 0 && cfg_wstrb_i == 0) cfg_werror_o = 0;
    else if (cfg_waddr_i[1:0] == 0 && cfg_wstrb_i == 4'hf) begin
      case (cfg_waddr_i)
        12'h100:
        case (cfg_wdata_i)
          1:
          cfg_werror_o=!(state == EMPTY && valid_range && expected_mask == 8'hff && backend_ready_i && loader_idle_i);
          2: cfg_werror_o = state != LOADING;
          3: cfg_werror_o = !(state == VERIFYING && loader_idle_i && hash_equal && backend_ready_i);
          4:
          cfg_werror_o=!(state == SEALED && !any_slot && !issue_valid && !bus_pending && !stopping && backend_ready_i);
          5: cfg_werror_o = !(state == RUNNING && !stopping);
          default: cfg_werror_o = 1;
        endcase
        12'h110, 12'h114, 12'h118, 12'h11c: cfg_werror_o = state != EMPTY;
        default: begin
          if (cfg_waddr_i >= 12'h120 && cfg_waddr_i <= 12'h13c) cfg_werror_o = state != EMPTY;
          else if (cfg_waddr_i >= 12'h140 && cfg_waddr_i <= 12'h15c)
            cfg_werror_o = !(state == VERIFYING && loader_idle_i);
        end
      endcase
    end
    cfg_rdata_o  = 0;
    cfg_rerror_o = cfg_raddr_i[1:0] != 0;
    case (cfg_raddr_i)
      12'h000: cfg_rdata_o = 32'h00010000;
      12'h004: begin
        cfg_rdata_o[2:0] = state;
        cfg_rdata_o[8]   = ready_o;
        cfg_rdata_o[9]   = state != LOADING;
        cfg_rdata_o[10]  = (state == RUNNING) || any_slot || issue_valid || bus_pending;
        cfg_rdata_o[11]  = fault_o;
        cfg_rdata_o[12]  = rejected_saturated;
      end
      12'h008: cfg_rdata_o = BACKEND_ID;
      12'h00c: cfg_rdata_o = epoch_o;
      12'h010: cfg_rdata_o = image_bytes[31:0];
      12'h014: cfg_rdata_o = image_bytes[63:32];
      12'h040: cfg_rdata_o = {29'b0, fault_code};
      12'h044: cfg_rdata_o = fault_offset[31:0];
      12'h048: cfg_rdata_o = {16'b0, fault_offset[47:32]};
      12'h050: cfg_rdata_o = reads_snapshot[31:0];
      12'h054: cfg_rdata_o = reads_snapshot[63:32];
      12'h058: cfg_rdata_o = stalls_snapshot[31:0];
      12'h05c: cfg_rdata_o = stalls_snapshot[63:32];
      12'h060: cfg_rdata_o = rejected_writes;
      12'h104: cfg_rdata_o = {31'b0, state == VERIFYING && loader_idle_i};
      12'h110: cfg_rdata_o = image_base[31:0];
      12'h114: cfg_rdata_o = image_base[63:32];
      12'h118: cfg_rdata_o = image_bytes[31:0];
      12'h11c: cfg_rdata_o = image_bytes[63:32];
      default: begin
        if (cfg_raddr_i >= 12'h020 && cfg_raddr_i <= 12'h03c)
          cfg_rdata_o = expected_hash[cfg_raddr_i[4:2]];
        else if (cfg_raddr_i >= 12'h120 && cfg_raddr_i <= 12'h13c)
          cfg_rdata_o = expected_hash[cfg_raddr_i[4:2]];
        else if (cfg_raddr_i >= 12'h140 && cfg_raddr_i <= 12'h15c)
          cfg_rdata_o = readback_hash[cfg_raddr_i[4:2]];
        else cfg_rerror_o = 1;
      end
    endcase
  end

  always_ff @(posedge clk_i) begin
    if (!rst_ni) begin
      state <= EMPTY;
      image_base <= 0;
      image_bytes <= 0;
      epoch_o <= 0;
      expected_mask <= 0;
      readback_mask <= 0;
      stopping <= 0;
      rejected_writes <= 0;
      rejected_saturated <= 0;
      fault_code <= 0;
      fault_offset <= 0;
      reads_completed <= 0;
      stall_cycles <= 0;
      reads_snapshot <= 0;
      stalls_snapshot <= 0;
      rsp_valid_o <= 0;
      rsp_data_o <= 0;
      rsp_tag_o <= 0;
      rsp_epoch_o <= 0;
      rsp_status_o <= 0;
      presented_index <= 0;
      read_cursor <= 0;
      response_cursor <= 0;
      issue_valid <= 0;
      issue_index <= 0;
      issue_addr <= 0;
      bus_pending <= 0;
      for (int i = 0; i < 8; i++) begin
        expected_hash[i] <= 0;
        readback_hash[i] <= 0;
      end
      for (int i = 0; i < 16; i++) begin
        slot_state[i] <= FREE;
        slot_status[i] <= 0;
        slot_offset[i] <= 0;
        slot_tag[i] <= 0;
        slot_epoch[i] <= 0;
        slot_data[i] <= 0;
      end
    end else begin
      if (rejection_events != 0) begin
        if (rejected_sum >= 33'h0ffffffff) begin
          rejected_writes <= 32'hffffffff;
          rejected_saturated <= 1;
        end else rejected_writes <= rejected_sum[31:0];
      end
      if (write_effect) begin
        case (cfg_waddr_i)
          12'h110: image_base[31:0] <= cfg_wdata_i;
          12'h114: image_base[63:32] <= cfg_wdata_i;
          12'h118: image_bytes[31:0] <= cfg_wdata_i;
          12'h11c: image_bytes[63:32] <= cfg_wdata_i;
          12'h100:
          case (cfg_wdata_i)
            1: begin
              state <= LOADING;
              epoch_o <= epoch_o + 1;
              readback_mask <= 0;
            end
            2: state <= VERIFYING;
            3: state <= SEALED;
            4: begin
              state <= RUNNING;
              reads_completed <= 0;
              stall_cycles <= 0;
            end
            5: stopping <= 1;
            default: ;
          endcase
          default: begin
            if (cfg_waddr_i >= 12'h120 && cfg_waddr_i <= 12'h13c) begin
              expected_hash[cfg_waddr_i[4:2]] <= cfg_wdata_i;
              expected_mask[cfg_waddr_i[4:2]] <= 1;
            end else if (cfg_waddr_i >= 12'h140 && cfg_waddr_i <= 12'h15c) begin
              readback_hash[cfg_waddr_i[4:2]] <= cfg_wdata_i;
              readback_mask[cfg_waddr_i[4:2]] <= 1;
            end
          end
        endcase
      end
      if (stopping && !any_slot && !issue_valid && !bus_pending) begin
        stopping <= 0;
        state <= SEALED;
        reads_snapshot <= reads_completed;
        stalls_snapshot <= stall_cycles;
      end
      if (!any_slot && !issue_valid && !bus_pending && state == SEALED) begin
        reads_snapshot  <= reads_completed;
        stalls_snapshot <= stall_cycles;
      end
      if ((req_valid_i && !req_ready_o && ready_o) || (rsp_valid_o && !rsp_ready_i) ||
          (issue_valid && !mem_req_ready_i) || (bus_pending && !mem_rsp_valid_i))
        stall_cycles <= stall_cycles + 1;
      if (req_valid_i && duplicate && ready_o && fault_code == 0) begin
        fault_code   <= PROTOCOL;
        fault_offset <= req_offset_i;
      end
      if (req_valid_i && req_ready_o) begin
        slot_tag[free_index] <= req_tag_i;
        slot_epoch[free_index] <= req_epoch_i;
        slot_offset[free_index] <= req_offset_i;
        slot_status[free_index] <= incoming_status;
        slot_data[free_index] <= 0;
        slot_state[free_index] <= incoming_status == OK ? WAIT_READ : COMPLETE;
      end
      if (!issue_valid && !bus_pending && read_found && !fault_o && !fatal_event) begin
        issue_index <= read_index;
        read_cursor <= read_index + 1'b1;
        issue_addr <= image_base + {16'b0, slot_offset[read_index]};
        issue_valid <= 1;
        slot_state[read_index] <= ON_BUS;
      end
      if (issue_valid && mem_req_ready_i) begin
        issue_valid <= 0;
        bus_pending <= 1;
      end
      if (bus_pending && mem_rsp_valid_i) begin
        bus_pending <= 0;
        // A fault may already have completed this slot. Drain the physical read
        // without overwriting a queued/presented response or reusing its ID.
        if (slot_state[issue_index] == ON_BUS) begin
          slot_data[issue_index]<=(mem_rsp_error_i || fault_o || client_fault_i) ? '0 : mem_rsp_data_i;
          slot_status[issue_index] <= (mem_rsp_error_i || fault_o || client_fault_i) ? BACKEND : OK;
          slot_state[issue_index] <= COMPLETE;
        end
      end
      if (rsp_valid_o && rsp_ready_i) begin
        rsp_valid_o <= 0;
        slot_state[presented_index] <= FREE;
        reads_completed <= reads_completed + 1;
      end
      if (!rsp_valid_o && complete_found) begin
        rsp_valid_o <= 1;
        presented_index <= complete_index;
        response_cursor <= complete_index + 1'b1;
        rsp_data_o <= slot_data[complete_index];
        rsp_tag_o <= slot_tag[complete_index];
        rsp_epoch_o <= slot_epoch[complete_index];
        rsp_status_o <= slot_status[complete_index];
        slot_state[complete_index] <= PRESENTED;
      end
      // A controller reset/loss of readiness during load or verification also
      // invalidates the volatile image. Recovery requires coordinated reset,
      // complete reload, full readback and fresh hashes; readiness cannot revive it.
      if (fatal_event && state != EMPTY) begin
        state <= FAULT;
        stopping <= 0;
        if (fault_code == 0) begin
          fault_code   <= BACKEND;
          fault_offset <= (issue_valid || bus_pending) ? slot_offset[issue_index] : 48'b0;
        end
        for (int i = 0; i < 16; i++) begin
          if (slot_state[i] == WAIT_READ || slot_state[i] == ON_BUS) begin
            slot_state[i]  <= COMPLETE;
            slot_data[i]   <= 0;
            slot_status[i] <= BACKEND;
          end
        end
        // If a request was accepted on the fault edge, it also gets one error.
        if (req_valid_i && req_ready_o) begin
          slot_state[free_index]  <= COMPLETE;
          slot_data[free_index]   <= 0;
          slot_status[free_index] <= BACKEND;
        end
      end
    end
  end
endmodule
