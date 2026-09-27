// Original Ergodex DDR + DOT128 shell test. Fixture bytes are generated outside
// Git from verified canonical weights; see compact_fixture.json for relocation.
module test_first_slice;
  import tb_type_defines_pkg::*;
  `include "first_slice_fixture.svh"
  logic [511:0] observed;
  logic [63:0] value;
  logic [15:0] leds;
  int assertions = 0;
  task automatic check(input bit condition, input string label);
    if (!condition) $fatal(1, "FAIL %s", label);
    assertions++;
    $display("PASS %s", label);
  endtask
  task automatic poll(input logic [63:0] addr, input logic [31:0] mask, expected);
    for (int i = 0; i < 10000; i++) begin
      tb.peek_ocl(.addr(addr), .data(value));
      if ((value[31:0] & mask) == expected) return;
      #100ns;
    end
    $fatal(1, "OCL poll timeout addr=%h value=%h", addr, value);
  endtask
  initial begin
    #10ms;
    $fatal(1, "whole-test watchdog");
  end
  initial begin
    initialize_fixture();
    tb.power_up();
    for (int i = 0; i < 10000; i++) begin
      leds = tb.get_virtual_led();
      if (leds[1:0] == 3) break;
      #100ns;
    end
    check(leds[1:0] == 3, "DDR calibrated and bridge ready");
    tb.peek_ocl(.addr('h1000), .data(value));
    check(value[31:0] == 'h444f5431, "custom DOT1 ABI");
    tb.poke_ocl(.addr('h110), .data(0));
    tb.poke_ocl(.addr('h114), .data(0));
    tb.poke_ocl(.addr('h118), .data(FIXTURE_BYTES));
    tb.poke_ocl(.addr('h11c), .data(0));
    for (int i = 0; i < 8; i++) tb.poke_ocl(.addr(64'h120 + 64'(4 * i)), .data(fixture_digest[i]));
    tb.poke_ocl(.addr('h100), .data(1));
    poll('h004, 7, 1);
    for (int i = 0; i < FIXTURE_LINES; i++)
    tb.poke(.addr(64'(i) * 64), .data(fixture_lines[i]), .size(DataSize::UINT512));
    tb.poke_ocl(.addr('h100), .data(2));
    poll('h104, 1, 1);
    for (int i = 0; i < FIXTURE_LINES; i++) begin
      tb.peek(.addr(64'(i) * 64), .data(observed), .size(DataSize::UINT512));
      check(observed === fixture_lines[i], "complete compact-fixture DDR readback");
    end
    // The generated digest was independently computed from every byte just
    // compared above. It is the compact fixture digest, not the model digest.
    for (int i = 0; i < 8; i++) tb.poke_ocl(.addr(64'h140 + 64'(4 * i)), .data(fixture_digest[i]));
    tb.poke_ocl(.addr('h100), .data(3));
    poll('h004, 'h307, 'h303);
    tb.peek_ocl(.addr('h00c), .data(value));
    tb.poke_ocl(.addr('h102c), .data(value[31:0]));
    for (int job = 0; job < FIXTURE_CASES; job++) begin
      for (int word_index = 0; word_index < 32; word_index++)
      tb.poke_ocl(.addr(64'h1080 + 64'(4 * word_index)),
                  .data(fixture_activations[job][32*word_index+:32]));
      tb.poke_ocl(.addr('h1018), .data(32'(job + 1)));
      tb.poke_ocl(.addr('h101c), .data(fixture_opcode[job]));
      tb.poke_ocl(.addr('h1020), .data(128));
      tb.poke_ocl(.addr('h1024), .data(fixture_offset[job]));
      tb.poke_ocl(.addr('h1028), .data(0));
      tb.poke_ocl(.addr('h1010), .data(1));
      poll('h100c, 3, 2);
      tb.peek_ocl(.addr('h1014), .data(value));
      check(value[31:0] == 0, "dot completed without error");
      tb.peek_ocl(.addr('h1030), .data(value));
      check(value[31:0] === fixture_dot[job], "independent signed int8 dot oracle");
      $display("DOT_RESULT case=%0d opcode=%0d offset=%0d signed_dot=%0d", job,
               fixture_opcode[job], fixture_offset[job], $signed(value[31:0]));
      tb.peek_ocl(.addr('h1034), .data(value));
      check(value[15:0] === fixture_scale[job], "exact raw FP16 scale from DDR payload");
      $display("DOT_SCALE case=%0d raw_fp16=%04h", job, value[15:0]);
      tb.peek_ocl(.addr('h1038), .data(value));
      check(value[31:0] == 32'(job + 1), "completion cookie");
      tb.peek_ocl(.addr('h1050), .data(value));
      check(value[31:0] != 0, "actual weight reads counted");
      tb.poke_ocl(.addr('h1010), .data(2));
    end
    $display(
        "FIRST_SLICE_TEST_PASS assertions=%0d real_q1_cases=3 synthetic_ternary_cases=1 compact_bytes=%0d canonical_full_image=0",
        assertions, FIXTURE_BYTES);
    tb.power_down();
    $finish;
  end
endmodule
