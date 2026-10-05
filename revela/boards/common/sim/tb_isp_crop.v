// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// A line wider than the pack was built for is CROPPED, not reinterpreted.
//
// The receiver upstream refuses only what its own buffers cannot hold --
// 3276 samples at 10-bit -- while the pack's line buffers are built for
// 1920. Everything between those two numbers arrives perfectly well
// formed and is the pack's to survive. It cannot: its pointwise cores
// get neither the stream's end-of-line nor the header's width, so they
// reframe every line from start-of-frame plus their built width, declare
// a new row part-way through the sensor's, and invert the CFA phase on
// alternate rows. The picture shears and the colours swap, with no
// status bit anywhere.
//
// So the shim crops. What is checked here is that cropping is not itself
// a new way to lose things:
//
//   NARROWER IS STILL WHOLE   a line at or under the built width passes
//                             through untouched -- the ordinary case
//                             must cost nothing.
//   THE SURPLUS IS DRAINED    dropped samples are ACCEPTED, never
//                             stalled. A stalled surplus never reaches
//                             its line end and the frame stops.
//   THE PACK SEES ITS WIDTH   exactly WIDTH valid samples per line,
//                             so its own counter wraps where the line
//                             really ends and the phase stays intact.
//   THE FRAME END SURVIVES    the pack COMMITS COEFFICIENTS on the
//                             incoming end-of-frame. On a cropped line
//                             that sample is one the pack never sees, so
//                             the flag is re-timed onto the last kept
//                             sample. Gated away instead, every
//                             calibration write would sit in the shadow
//                             forever -- on exactly the frames where
//                             something is already wrong.
//   AND IT SAYS SO            `truncated` is sticky, because a narrower
//                             picture must not read as a zoomed sensor.
//
// Run:  iverilog -g2012 -o /tmp/crop.vvp sim/tb_isp_crop.v \
//           hdl/isp_shim.v $OUT/hdl/revela_isp_core.v && /tmp/crop.vvp
`timescale 1ns/1ps
module tb;
  localparam W = 64;                 // the pack's built width, scaled down
  localparam H = 4;

  reg clk = 0, rst = 1;
  always #5 clk = ~clk;

  reg  [15:0] hdr_w = W, hdr_h = H;
  reg  [1:0]  hdr_ph = 2'd2;
  reg  [4:0]  hdr_bits = 5'd10;
  reg         in_valid = 0, in_sof = 0, in_eol = 0, in_last = 0;
  reg  [11:0] in_data = 0;
  wire        in_ready, out_valid, out_sof, out_eol, out_last, truncated;
  wire [23:0] out_data;

  // The pack MUST be made to push back, in bursts. Without it
  // `core_ready` never falls, `in_ready` is high whatever drives it, and
  // the drain check below cannot fail -- a shim that stalls the surplus
  // on `core_ready` passes every other check here.
  //
  // The stall is IMPOSED rather than provoked. What is under test is the
  // shim, and the pack instantiated beside it was generated for a
  // full-width line: it swallows these 64-sample frames whole and would
  // never push back on its own. So its ready is held low from outside,
  // and the pack's own output is not examined in this file.
  // Driven on the FALLING edge so the pack's ready is settled and stable
  // across every rising one. Changing it at the same edge the counters
  // below sample would be a race inside the harness, and a harness that
  // races reports the design's behaviour as noise.
  reg [2:0] stall = 0;
  always @(negedge clk) begin
    stall <= stall + 3'd1;
    if (stall == 3'd4) force dut.core_ready = 1'b0;
    if (stall == 3'd7) release dut.core_ready;
  end

  revela_isp #(.WIDTH(W), .SAMPLE_BITS(12), .DATA_BITS(24)) dut (
      .clk(clk), .rst(rst),
      .hdr_width(hdr_w), .hdr_height(hdr_h),
      .hdr_phase(hdr_ph), .hdr_bits(hdr_bits),
      .s_axi_aclk(clk), .s_axi_aresetn(1'b1),
      .s_axi_awaddr(15'd0), .s_axi_awvalid(1'b0), .s_axi_awready(),
      .s_axi_wdata(32'd0), .s_axi_wstrb(4'd0), .s_axi_wvalid(1'b0),
      .s_axi_wready(), .s_axi_bresp(), .s_axi_bvalid(), .s_axi_bready(1'b1),
      .s_axi_araddr(15'd0), .s_axi_arvalid(1'b0), .s_axi_arready(),
      .s_axi_rdata(), .s_axi_rresp(), .s_axi_rvalid(), .s_axi_rready(1'b1),
      .in_valid(in_valid), .in_ready(in_ready), .in_data(in_data),
      .in_sof(in_sof), .in_eol(in_eol), .in_last(in_last),
      .out_valid(out_valid), .out_ready(1'b1), .out_data(out_data),
      .out_sof(out_sof), .out_eol(out_eol), .out_last(out_last),
      .truncated(truncated));

  integer fails = 0;
  task check(input cond, input [1023:0] what);
    if (!cond) begin $display("FAIL: %0s", what); fails = fails + 1; end
    else $display("  ok: %0s", what);
  endtask

  // What the PACK is handed, counted at its own port. Every count is of
  // an ACCEPTED sample: under backpressure a flag stays raised for as
  // long as the sample is held, and counting cycles instead of transfers
  // would multiply one line end into several.
  integer kept, eols, lasts, stalled_surplus, pushed_back;
  always @(posedge clk) if (!rst) begin
    if (in_valid && dut.keep && dut.core_ready) begin
      kept = kept + 1;
      if (dut.line_end)  eols  = eols + 1;
      if (dut.frame_end) lasts = lasts + 1;
    end
    // A dropped sample must never be held: the line would never end.
    if (in_valid && !dut.keep && !in_ready) stalled_surplus = stalled_surplus + 1;
    // ...and this is what makes that check mean anything.
    if (in_valid && !dut.core_ready) pushed_back = pushed_back + 1;
  end

  task send_frame(input integer width, input integer height);
    integer r, c;
    begin
      for (r = 0; r < height; r = r + 1)
        for (c = 0; c < width; c = c + 1) begin
          in_valid <= 1; in_data <= c[11:0];
          in_sof  <= (r == 0) && (c == 0);
          in_eol  <= (c == width - 1);
          in_last <= (r == height - 1) && (c == width - 1);
          // Ready is sampled mid-cycle, where the combinational path
          // has settled, so the following rising edge is the one that
          // transfers. Sampling it AT the edge races the edge.
          @(negedge clk); #1;
          while (!in_ready) begin @(negedge clk); #1; end
          @(posedge clk);
        end
      in_valid <= 0; in_sof <= 0; in_eol <= 0; in_last <= 0;
      repeat (60) @(posedge clk);
    end
  endtask

  initial begin
    kept = 0; eols = 0; lasts = 0; stalled_surplus = 0; pushed_back = 0;
    repeat (4) @(posedge clk);
    rst = 0;
    repeat (4) @(posedge clk);

    // THE ORDINARY CASE COSTS NOTHING.
    hdr_w = W; hdr_h = H;
    send_frame(W, H);
    check(kept == W * H, "a line at the built width passes whole");
    check(!truncated, "and nothing is reported as cropped");
    check(eols == H, "one line end per line");
    check(lasts == 1, "exactly one frame end");

    // A WIDER LINE IS CROPPED.
    kept = 0; eols = 0; lasts = 0;
    hdr_w = 2 * W;
    send_frame(2 * W, H);
    check(kept == W * H,
          "a line of twice the built width still delivers exactly the width");
    check(truncated, "and the crop is reported, stickily");
    // The pack windows and phases against the width it is HANDED. Told
    // the header's number while receiving the cropped one, it would
    // window past the end of every line -- cropping the stream and not
    // the geometry just moves the shear somewhere harder to see.
    check(dut.ctx_w == W, "and it was told the width it actually receives");
    check(pushed_back > 0,
          "the pack pushed back, so the drain check is not vacuous");
    check(stalled_surplus == 0,
          "the surplus was drained, never stalled");
    check(eols == H, "the line end was re-timed onto a kept sample");
    check(lasts == 1,
          "and so was the frame end -- the coefficient commit survives");

    if (fails) $display("\n%0d FAILED", fails);
    else $display("\nisp crop: all checks passed");
    $finish;
  end

  initial begin
    #20_000_000;
    $display("FAIL: timeout");
    $finish;
  end
endmodule
