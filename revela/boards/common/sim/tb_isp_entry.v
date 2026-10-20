// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// The ISP's entry: each frame's header facts held for the frame, and a
// line wider than the ISP was built for CROPPED, not reinterpreted.
//
//   THE FACTS ARE THE FRAME'S  width, height, phase, depth and frame
//                             count are taken as the SOF enters and held
//                             to the next one. A header that moves
//                             mid-frame -- the receiver's outputs follow
//                             the cable, not the ISP -- reaches the ISP
//                             on the next frame boundary, never inside
//                             a picture.
//
// The receiver upstream refuses only what its own buffers cannot hold --
// 3276 samples at 10-bit -- while the ISP's line buffers are built for
// 1920. Everything between those two numbers arrives perfectly well
// formed and is the entry's to crop. What is checked is that cropping is
// not itself a new way to lose things:
//
//   NARROWER IS STILL WHOLE   a line at or under the built width passes
//                             through untouched -- the ordinary case
//                             must cost nothing.
//   THE SURPLUS IS DRAINED    dropped samples are ACCEPTED, never
//                             stalled. A stalled surplus never reaches
//                             its line end and the frame stops.
//   THE ISP SEES ITS WIDTH    exactly WIDTH samples per line, and the
//                             width it is TOLD is the one it receives.
//   THE FRAME END SURVIVES    the ISP COMMITS COEFFICIENTS on the
//                             end-of-frame. On a cropped line that sample
//                             is one the ISP never sees, so the flag is
//                             re-timed onto the last kept sample.
//   AND IT SAYS SO            `truncated` is sticky, because a narrower
//                             picture must not read as a zoomed sensor.
`timescale 1ns/1ps
module tb;
  localparam W = 64;                 // the ISP's built width, scaled down
  localparam H = 4;

  reg clk = 0, rst = 1;
  always #5 clk = ~clk;

  reg  [15:0] hdr_w = W, hdr_h = H;
  reg  [1:0]  hdr_ph = 2'd2;
  reg  [4:0]  hdr_bits = 5'd10;
  reg  [31:0] hdr_seq = 32'd0;
  reg         in_valid = 0, in_sof = 0, in_eol = 0, in_last = 0;
  reg  [11:0] in_data = 0;
  wire        in_ready, out_valid, out_sof, out_eol, out_last, truncated;
  wire [11:0] out_data;
  wire [15:0] ctx_width, ctx_height;
  wire [1:0]  ctx_bayer_phase;
  wire [4:0]  ctx_bit_depth;
  wire [31:0] ctx_frame_seq;

  // The ISP MUST push back, in bursts. Without it `out_ready` never
  // falls, `in_ready` is high whatever drives it, and the drain check
  // below cannot fail -- an entry that stalls the surplus on the ISP's
  // ready passes every other check here. Driven on the FALLING edge so
  // it is settled across every rising one: changing it at the edge the
  // counters sample would be a race inside the harness.
  reg       out_ready = 1;
  reg [2:0] stall = 0;
  always @(negedge clk) begin
    stall <= stall + 3'd1;
    out_ready <= !(stall >= 3'd4 && stall < 3'd7);
  end

  isp_entry #(.SAMPLE_BITS(12), .WIDTH(W)) dut (
      .clk(clk), .rst(rst),
      .hdr_width(hdr_w), .hdr_height(hdr_h),
      .hdr_phase(hdr_ph), .hdr_bits(hdr_bits), .hdr_frame_seq(hdr_seq),
      .in_valid(in_valid), .in_ready(in_ready), .in_data(in_data),
      .in_sof(in_sof), .in_eol(in_eol), .in_last(in_last),
      .out_valid(out_valid), .out_ready(out_ready), .out_data(out_data),
      .out_sof(out_sof), .out_eol(out_eol), .out_last(out_last),
      .ctx_width(ctx_width), .ctx_height(ctx_height),
      .ctx_bayer_phase(ctx_bayer_phase), .ctx_bit_depth(ctx_bit_depth),
      .ctx_frame_seq(ctx_frame_seq),
      .truncated(truncated));

  integer fails = 0;
  task check(input cond, input [1023:0] what);
    if (!cond) begin $display("FAIL: %0s", what); fails = fails + 1; end
    else $display("  ok: %0s", what);
  endtask

  // What the ISP is handed, counted at its port. Every count is of an
  // ACCEPTED sample: under backpressure a flag stays raised for as long
  // as the sample is held, and counting cycles instead of transfers
  // would multiply one line end into several.
  integer kept, eols, lasts, stalled_surplus, pushed_back, moved;
  always @(posedge clk) if (!rst) begin
    if (out_valid && out_ready) begin
      kept = kept + 1;
      if (out_eol)  eols  = eols + 1;
      if (out_last) lasts = lasts + 1;
    end
    // A dropped sample must never be held: the line would never end.
    if (in_valid && !dut.keep && !in_ready) stalled_surplus = stalled_surplus + 1;
    // ...and this is what makes that check mean anything.
    if (in_valid && !out_ready) pushed_back = pushed_back + 1;
  end

  // The ISP's facts, as they stood once the frame's SOF was taken -- read
  // on the falling edge after it, where the latch has landed. Any change
  // of them before the frame's end is a fact moving in a picture.
  reg [69:0] at_sof;
  wire [69:0] facts = {ctx_width, ctx_height, ctx_bayer_phase,
                       ctx_bit_depth, ctx_frame_seq};
  reg entered = 0, left = 0, in_frame = 0;
  always @(posedge clk) begin
    if (in_frame && facts !== at_sof) moved = moved + 1;
    entered <= in_valid && in_ready && in_sof;
    left    <= in_valid && in_ready && in_last;
  end
  always @(negedge clk) begin
    if (entered) at_sof <= facts;
    if (entered) in_frame <= 1;
    else if (left) in_frame <= 0;
  end

  // `scramble` moves every header fact once the frame's SOF is in, as
  // a header that follows the cable may.
  reg scramble = 0;
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
          if (scramble && r == 0 && c == 0) begin
            hdr_w <= hdr_w + 16'd8; hdr_h <= hdr_h + 16'd1;
            hdr_ph <= ~hdr_ph; hdr_bits <= hdr_bits + 5'd2;
            hdr_seq <= hdr_seq + 32'd1;
          end
        end
      in_valid <= 0; in_sof <= 0; in_eol <= 0; in_last <= 0;
      repeat (60) @(posedge clk);
    end
  endtask

  task clear;
    begin kept = 0; eols = 0; lasts = 0; stalled_surplus = 0;
          pushed_back = 0; moved = 0; end
  endtask

  initial begin
    clear;
    repeat (4) @(posedge clk);
    rst = 0;
    repeat (4) @(posedge clk);

    // THE ORDINARY CASE COSTS NOTHING.
    hdr_w = W; hdr_h = H; hdr_seq = 32'd7;
    send_frame(W, H);
    check(kept == W * H, "a line at the built width passes whole");
    check(!truncated, "and nothing is reported as cropped");
    check(eols == H, "one line end per line");
    check(lasts == 1, "exactly one frame end");
    check(ctx_width == W && ctx_height == H && ctx_bayer_phase == 2'd2
          && ctx_bit_depth == 5'd10, "the ISP is told the header's geometry");
    check(ctx_frame_seq == 32'd7, "and the header's frame count");

    // A HEADER THAT MOVES MID-FRAME WAITS FOR THE NEXT ONE.
    clear;
    hdr_seq = 32'hfffffffe;          // the count's whole width, wrapping
    scramble = 1;
    send_frame(W, H);
    scramble = 0;
    check(moved == 0, "no fact moved inside the frame");
    check(ctx_frame_seq == 32'hfffffffe,
          "the frame's count is the one its SOF entered with");
    check(lasts == 1,
          "and its end is found at the height it entered with");
    hdr_w = W; hdr_h = H; hdr_ph = 2'd2; hdr_bits = 5'd10;
    send_frame(W, H);
    check(ctx_frame_seq == 32'hffffffff, "the next frame takes the next count");
    check(moved == 0, "and holds it");

    // A WIDER LINE IS CROPPED.
    clear;
    hdr_w = 2 * W;
    send_frame(2 * W, H);
    check(kept == W * H,
          "a line of twice the built width still delivers exactly the width");
    check(truncated, "and the crop is reported, stickily");
    // The ISP windows and phases against the width it is TOLD. Told the
    // header's number while receiving the cropped one, it would window
    // past the end of every line.
    check(ctx_width == W, "and it was told the width it actually receives");
    check(pushed_back > 0,
          "the ISP pushed back, so the drain check is not vacuous");
    check(stalled_surplus == 0, "the surplus was drained, never stalled");
    check(eols == H, "the line end was re-timed onto a kept sample");
    check(lasts == 1,
          "and so was the frame end -- the coefficient commit survives");

    if (fails) $display("\n%0d FAILED", fails);
    else $display("\nisp entry: all checks passed");
    $finish;
  end
endmodule
