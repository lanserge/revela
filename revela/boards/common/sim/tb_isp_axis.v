// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
`timescale 1ns/1ps
// The fence's one law: tvalid, once raised, falls only after tready.
// The consumer's reset is not this module's reset -- a VDMA keeps its
// clock and its memory of promises through a link drop, and a promise
// broken mid-beat wedged a processor on 2026-08-18. So the testbench
// holds tready LOW, asserts rst mid-offer, and watches whether the
// offer survives. The combinational version fails in one cycle.
//
// Run:  iverilog -g2012 -DSIMULATION -o /tmp/ia.vvp sim/tb_isp_axis.v \
//           hdl/isp_axis.v && /tmp/ia.vvp
module tb;
  reg clk = 0; always #5 clk = ~clk;
  reg rst = 0;

  // The width is stated ONCE here and passed down, never left to the
  // module's default and restated in the stimulus. This bench drove 30
  // bits at a 24-bit port for as long as it took the pipeline's gamma to
  // start narrowing to 8, and reported the mismatch as twelve failures
  // of the fence. Everything below is derived from it, so the next
  // change to the ISP's boundary moves one line.
  localparam DATA_BITS = 24;
  localparam CH = DATA_BITS / 3;

  reg        in_valid = 0;
  wire       in_ready;
  reg [DATA_BITS-1:0] in_data = 0;
  reg in_sof = 0, in_eol = 0, in_last = 0;
  wire tvalid; reg tready = 0;
  wire [31:0] tdata; wire [3:0] tkeep; wire tuser, tlast;

  // What the cable expects of a beat: the top eight of each lane, in
  // rgb2dvi's byte order through the little-endian framebuffer word.
  function [31:0] beat(input [DATA_BITS-1:0] d);
    beat = {8'h00, d[0*CH + CH-1 -: 8],      // R
                   d[2*CH + CH-1 -: 8],      // B
                   d[1*CH + CH-1 -: 8]};     // G
  endfunction

  isp_axis #(.DATA_BITS(DATA_BITS)) dut (.clk(clk), .rst(rst),
    .in_valid(in_valid), .in_ready(in_ready), .in_data(in_data),
    .in_sof(in_sof), .in_eol(in_eol), .in_last(in_last),
    .m_axis_tvalid(tvalid), .m_axis_tready(tready),
    .m_axis_tdata(tdata), .m_axis_tkeep(tkeep),
    .m_axis_tuser(tuser), .m_axis_tlast(tlast));

  // The protocol monitor: a drop of tvalid without a handshake, or
  // data moving while an offer stands un-taken, is the violation.
  integer chops = 0, wobbles = 0;
  reg tv_q = 0; reg [31:0] td_q;
  always @(posedge clk) begin
    if (tv_q && !tvalid && !(tv_q && tready)) chops = chops + 1;
    if (tv_q && tvalid && !tready && tdata !== td_q) wobbles = wobbles + 1;
    tv_q <= tvalid; td_q <= tdata;
  end

  integer got = 0, errs = 0, i;
  reg [31:0] expect_q [0:63];
  integer eq_w = 0, eq_r = 0;
  always @(posedge clk) if (tvalid && tready) begin
    if (tdata !== expect_q[eq_r]) errs = errs + 1;
    eq_r = eq_r + 1; got = got + 1;
  end

  task offer(input [DATA_BITS-1:0] d);
    begin
      in_data = d; in_valid = 1;
      expect_q[eq_w] = beat(d);
      eq_w = eq_w + 1;
      @(negedge clk); #1;
      while (!in_ready) begin @(negedge clk); #1; end
      // Cleared just AFTER the transferring edge. Cleared at it, the
      // blocking assignment lands before the DUT samples and the beat
      // is never taken at all.
      @(posedge clk); #1;
      in_valid = 0;
    end
  endtask

  // A beat built lane by lane, so the stimulus does not assume a lane
  // width either: the eight bits that reach the cable are the top eight
  // of each lane, whatever the lane is.
  task offer3(input [7:0] r, input [7:0] g, input [7:0] b);
    reg [DATA_BITS-1:0] d;
    begin
      d = 0;
      d[0*CH + CH-1 -: 8] = r;
      d[1*CH + CH-1 -: 8] = g;
      d[2*CH + CH-1 -: 8] = b;
      offer(d);
    end
  endtask

  integer survived = 0, held_stable = 0, quiet_in_rst = 0;
  initial begin
    repeat (4) @(posedge clk);
    // --- plain flow, sink willing --------------------------------
    tready = 1;
    for (i = 0; i < 8; i = i + 1)
      offer3(i[7:0] + 8'h11, i[7:0] + 8'h22, i[7:0] + 8'h33);
    repeat (2) @(posedge clk);

    // --- THE test: offer a beat, sink stalls, reset lands --------
    tready = 0;
    offer3(8'hAA, 8'hAA, 8'hAA);
    // the beat is now offered and un-taken; the link drops:
    rst = 1;
    repeat (10) @(posedge clk);
    survived = tvalid;                 // the offer must still stand
    held_stable = (tdata === {8'h00, 8'hAA, 8'hAA, 8'hAA});
    // the sink finally takes it, mid-reset:
    tready = 1; @(posedge clk); #1;
    repeat (2) @(posedge clk);
    quiet_in_rst = !tvalid;            // and then: silence, while rst
    in_valid = 1; in_data = {DATA_BITS{1'b1}};  // upstream noise in reset
    repeat (4) @(posedge clk);
    quiet_in_rst = quiet_in_rst && !tvalid;
    in_valid = 0;
    eq_w = eq_r;                       // discard the noise expectation
    // --- release, stream resumes ---------------------------------
    rst = 0; repeat (2) @(posedge clk);
    for (i = 0; i < 4; i = i + 1)
      offer3(i[7:0] + 8'h44, i[7:0] + 8'h55, i[7:0] + 8'h66);
    repeat (4) @(posedge clk);
    $display("RESULT chops=%0d wobbles=%0d errs=%0d got=%0d survived=%0d stable=%0d quiet=%0d",
             chops, wobbles, errs, got, survived, held_stable, quiet_in_rst);
    // Stated as a verdict, not left as numbers to be read by eye: this
    // bench printed errs=12 for long enough to be walked past.
    if (chops || wobbles || errs || got != 13 || !survived || !held_stable
        || !quiet_in_rst)
      $display("isp axis: FAILED");
    else
      $display("isp axis: all checks passed");
    $finish;
  end
  initial begin #8000; $display("RESULT TIMEOUT"); $finish; end
endmodule
