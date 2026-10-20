// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// The raster's mode and placement are run-time facts. These are the
// claims that came with them, checked rather than asserted.
//
// The register file runs on the bus clock and the raster runs on its
// own, so the interesting cases are all about the gap between them: a
// value written but not yet taken, a value taken in the wrong part of a
// frame, and a bus talking to a domain whose clock has gone away. That
// last one is why the file is on this side at all -- an AXI slave with
// no clock never answers.
//
// tests/test_scanout_sim.py runs it: the register file generated for
// 1080p60, its addresses in scanout_regs_map.vh beside it, and the
// raster's fixed Verilog.
`timescale 1ns/1ps
module tb;
  // The bus clock: the board's own, and it never stops.
  reg aclk = 0;
  always #5 aclk = ~aclk;
  reg aresetn = 0;

  // The pixel clock: recovered from the cable, and gated here so the
  // testbench can pull the cable.
  reg pix_en = 1, pix_raw = 0;
  wire clk = pix_en & pix_raw;
  always #3.367 pix_raw = ~pix_raw;          // ~148.5 MHz
  reg rst = 1, locked = 0;

  `include "scanout_regs_map.vh"
  localparam OKAY = 2'b00, SLVERR = 2'b10;

  reg  [7:0]  awaddr = 0, araddr = 0;
  reg  [31:0] wdata = 0;
  reg  [3:0]  wstrb = 4'hf;
  reg  awvalid = 0, wvalid = 0, bready = 0, arvalid = 0, rready = 0;
  wire awready, wready, bvalid, arready, rvalid;
  wire [1:0] bresp, rresp;
  wire [31:0] rdata;
  wire [14:0] status;
  wire vid_active_video, vid_hsync, vid_vsync, s_axis_tready;
  wire [23:0] vid_data;
  wire ps_en, ps_incdec;

  wire [15:0] h_total, h_active, hs_beg, hs_end;
  wire [15:0] v_total, v_active, vs_beg, vs_end, ps_per_px;
  wire [15:0] win_x0, win_y0;
  // the size of the frame arriving: the stream's, not the file's
  reg  [15:0] pic_w = 16'd1920, pic_h = 16'd1080;
  wire hs_pos, vs_pos, center, arm_req, arm_ack;

  scanout_regs regs (
      .aclk(aclk), .aresetn(aresetn),
      .arm_req(arm_req), .arm_ack(arm_ack),
      .s_axil_awaddr(awaddr), .s_axil_awvalid(awvalid),
      .s_axil_awready(awready), .s_axil_wdata(wdata),
      .s_axil_wstrb(wstrb), .s_axil_wvalid(wvalid), .s_axil_wready(wready),
      .s_axil_bresp(bresp), .s_axil_bvalid(bvalid), .s_axil_bready(bready),
      .s_axil_araddr(araddr), .s_axil_arvalid(arvalid),
      .s_axil_arready(arready), .s_axil_rdata(rdata), .s_axil_rresp(rresp),
      .s_axil_rvalid(rvalid), .s_axil_rready(rready),
      .param_h_total(h_total), .param_h_active(h_active),
      .param_hs_beg(hs_beg), .param_hs_end(hs_end),
      .param_v_total(v_total), .param_v_active(v_active),
      .param_vs_beg(vs_beg), .param_vs_end(vs_end),
      .param_hs_pos(hs_pos), .param_vs_pos(vs_pos),
      .param_ps_per_px(ps_per_px),
      .param_win_x0(win_x0), .param_win_y0(win_y0),
      .param_center(center), .param_arm());

  scanout dut (
      .clk(clk), .rst(rst), .locked(locked),
      .reg_h_total(h_total), .reg_h_active(h_active),
      .reg_hs_beg(hs_beg), .reg_hs_end(hs_end),
      .reg_v_total(v_total), .reg_v_active(v_active),
      .reg_vs_beg(vs_beg), .reg_vs_end(vs_end),
      .reg_hs_pos(hs_pos), .reg_vs_pos(vs_pos),
      .reg_ps_per_px(ps_per_px),
      .reg_win_x0(win_x0), .reg_win_y0(win_y0),
      .reg_center(center), .pic_w(pic_w), .pic_h(pic_h),
      .arm_req(arm_req), .arm_ack(arm_ack),
      .param_follow(1'b0), .param_steer(2'd0), .ps_en(ps_en), .ps_incdec(ps_incdec),
      .ps_done(1'b1),
      // The stream is not what is under test: the raster holds its mode
      // and paints fill, which still gives real blanking to land in.
      .s_axis_tdata(24'd0), .s_axis_tvalid(1'b0),
      .s_axis_tready(s_axis_tready), .s_axis_tuser(1'b0),
      .s_axis_tlast(1'b0),
      .vid_active_video(vid_active_video), .vid_data(vid_data),
      .vid_hsync(vid_hsync), .vid_vsync(vid_vsync), .status(status));

  integer fails = 0;
  task check(input cond, input [1023:0] what);
    if (!cond) begin
      $display("FAIL: %0s", what);
      fails = fails + 1;
    end else $display("  ok: %0s", what);
  endtask

  reg [1:0] last_resp;
  reg [31:0] last_read;

  task wr(input [7:0] a, input [31:0] d);
    begin
      @(posedge aclk);
      awaddr <= a; wdata <= d; awvalid <= 1; wvalid <= 1; bready <= 1;
      @(posedge aclk);
      while (!(awready && wready)) @(posedge aclk);
      awvalid <= 0; wvalid <= 0;
      while (!bvalid) @(posedge aclk);
      last_resp = bresp;
      @(posedge aclk);
      bready <= 0;
    end
  endtask

  task rd(input [7:0] a);
    begin
      @(posedge aclk);
      araddr <= a; arvalid <= 1; rready <= 1;
      @(posedge aclk);
      while (!arready) @(posedge aclk);
      arvalid <= 0;
      while (!rvalid) @(posedge aclk);
      last_read = rdata;
      @(posedge aclk);
      rready <= 0;
    end
  endtask

  // A PICTURE MOVES ONLY BETWEEN FRAMES. Placement and size decide
  // where every pixel of a frame lands, so a copy taken mid-frame would
  // move the picture halfway down itself. Watch the raster's own copies:
  // every time one changes, the raster must be in vertical blanking.
  reg [15:0] seen_x0, seen_w;
  initial begin seen_x0 = 16'd0; seen_w = 16'd1920; end
  // the line the raster scans, hsync edge to hsync edge
  integer line_len = 0, line_run = 0;
  reg hs_d = 0;
  always @(posedge clk) begin
    hs_d <= vid_hsync;
    line_run <= line_run + 1;
    if (vid_hsync && !hs_d) begin line_len <= line_run; line_run <= 1; end
  end

  always @(posedge clk) begin
    if (dut.have && dut.win_x0 !== seen_x0) begin
      if (!dut.in_vblank) begin
        $display("FAIL: placement changed outside vertical blanking");
        fails = fails + 1;
      end
      seen_x0 <= dut.win_x0;
    end
    if (dut.have && dut.win_w !== seen_w) begin
      if (!dut.in_vblank) begin
        $display("FAIL: the picture's size changed outside vertical blanking");
        fails = fails + 1;
      end
      seen_w <= dut.win_w;
    end
  end

  integer waited;
  initial begin
    repeat (8) @(posedge aclk);
    aresetn = 1;
    repeat (8) @(posedge clk);
    rst = 0; locked = 1;
    repeat (200) @(posedge clk);

    // AN UNWRITTEN BITSTREAM SHOWS THE MODE IT WAS BUILT WITH. The
    // file resets armed, so the raster takes the build's mode and
    // window without anyone writing a word.
    check(dut.have === 1'b1 && dut.h_total === 12'd2200 &&
          dut.v_total === 11'd1125 && dut.h_active === 12'd1920,
          "reset takes the build's own mode");
    check(dut.win_w === 16'd1920 && dut.win_h === 16'd1080 &&
          dut.win_x0 === 16'd0 && dut.win_y0 === 16'd0 &&
          dut.center === 1'b1,
          "reset places the build's own window");
    rd(A_CENTER);
    check(last_read[0] === 1'b1, "the file agrees, read back");
    rd(A_ID);
    check(last_read === LAYOUT_ID, "the file reads back its layout's ID");
    wr(A_ID, 32'd0);
    check(last_resp === SLVERR, "and refuses a write to it");

    // A WRITE ALONE DOES NOT MOVE THE PICTURE. The frame in flight
    // keeps the placement it started with until the arm says take.
    wr(A_WIN_X0, 32'd100); check(last_resp === OKAY, "origin write accepted");
    wr(A_WIN_Y0, 32'd50);  check(last_resp === OKAY, "line write accepted");
    wr(A_CENTER, 32'd0);
    repeat (400) @(posedge clk);
    check(dut.win_x0 === 16'd0 && dut.center === 1'b1,
          "written but unarmed: the raster has not moved");

    // ARMED, THE COPY IS TAKEN -- in blanking, which the monitor above
    // is watching for, and within a frame.
    wr(A_ARM, 32'd1); check(last_resp === OKAY, "arm accepted");
    waited = 0;
    while (dut.win_x0 !== 16'd100 && waited < 4_000_000) begin
      @(posedge clk); waited = waited + 1;
    end
    check(dut.win_x0 === 16'd100 && dut.win_y0 === 16'd50 &&
          dut.center === 1'b0,
          "armed: the raster took the new placement");
    check(waited < 2_475_000, "taken within one frame");

    // THE ARM CLEARS ITSELF, so software polls rather than guesses.
    // (Poll from a value that is not already the answer, or the loop
    // never runs and the check passes on stale data.)
    last_read = 32'hffff_ffff;
    waited = 0;
    while (last_read[0] !== 1'b0 && waited < 1000) begin
      rd(A_ARM); waited = waited + 1;
    end
    check(waited > 0 && last_read[0] === 1'b0,
          "the acknowledgement cleared the arm");

    // Let the handshake finish returning to rest. Where the cable is
    // pulled matters, and both places are checked below.
    waited = 0;
    while (dut.arm_ack && waited < 1000) begin
      @(posedge clk); waited = waited + 1;
    end
    check(!dut.arm_ack, "the acknowledgement dropped again");

    // NOW PULL THE CABLE, with the handshake at rest. The pixel clock
    // stops, so nothing can take a copy -- and the bus must stay
    // answerable anyway. This is the whole reason the file lives on
    // this side: an AXI slave with no clock never answers, and the
    // processor waiting for it has hung.
    pix_en = 0;
    wr(A_WIN_X0, 32'd640);
    check(last_resp === OKAY, "clock gone: a write is still answered");
    rd(A_WIN_X0);
    check(last_read[15:0] === 16'd640, "clock gone: and a read is too");
    wr(A_ARM, 32'd1);
    check(last_resp === OKAY, "clock gone: the arm is still answered");

    // ARMED MEANS FROZEN, and says so -- even with no reader running,
    // because the file cannot know that; it only knows nobody has
    // acknowledged.
    wr(A_WIN_X0, 32'd320);
    check(last_resp === SLVERR, "armed: a placement write is REFUSED");
    rd(A_WIN_X0);
    check(last_read[15:0] === 16'd640, "armed: and the value did not change");

    // THE ESCAPE HATCH. With no pixel clock the acknowledgement never
    // comes, so nothing would ever release the file. The arm word
    // itself stays writable: software times out and releases itself
    // rather than being locked out until someone reboots the board.
    rd(A_ARM);
    check(last_read[0] === 1'b1, "still armed, with no reader to ack");
    wr(A_ARM, 32'd0);
    check(last_resp === OKAY, "the arm can always be written back");
    wr(A_WIN_X0, 32'd800);
    check(last_resp === OKAY, "released: placement writes flow again");

    // Plug it back in and the pending placement lands.
    pix_en = 1;
    repeat (50) @(posedge clk);
    wr(A_ARM, 32'd1);
    waited = 0;
    while (dut.win_x0 !== 16'd800 && waited < 4_000_000) begin
      @(posedge clk); waited = waited + 1;
    end
    check(dut.win_x0 === 16'd800, "cable back: the placement lands");

    // THE OTHER PLACE THE CABLE CAN GO. Stopping the clock in the
    // middle of the handshake leaves the acknowledgement raised, and
    // the file reads that stale ack as "taken" -- so the arm will not
    // latch and writes are never refused. That is safe, because a
    // reader that is not clocking cannot take a torn copy; it is
    // recorded here so it is a known behaviour and not a surprise.
    wr(A_ARM, 32'd1);
    waited = 0;
    while (!dut.arm_ack && waited < 4_000_000) begin
      @(posedge clk); waited = waited + 1;
    end
    check(dut.arm_ack, "the reader raised its acknowledgement");
    pix_en = 0;                        // pulled with the ack still up
    repeat (4) @(posedge aclk);
    wr(A_ARM, 32'd1);
    wr(A_WIN_X0, 32'd42);
    check(last_resp === OKAY,
          "pulled mid-handshake: writes flow, nothing is locked out");
    pix_en = 1;
    repeat (50) @(posedge clk);
    wr(A_ARM, 32'd1);
    waited = 0;
    while (dut.win_x0 !== 16'd42 && waited < 4_000_000) begin
      @(posedge clk); waited = waited + 1;
    end
    check(dut.win_x0 === 16'd42, "and the handshake recovers by itself");

    // THE PICTURE'S SIZE IS THE STREAM'S, and moves only between frames
    // too: a new size arriving in the middle of a picture is held until
    // the raster blanks -- the monitor above is watching -- and taken
    // there, with no write and no arm.
    waited = 0;
    while (!(vid_active_video && dut.core.y == 11'd500) && waited < 4_000_000) begin
      @(posedge clk); waited = waited + 1;
    end
    pic_w = 16'd1600; pic_h = 16'd900;
    repeat (20) @(posedge clk);
    check(dut.win_w === 16'd1920, "a size mid-picture is not taken there");
    waited = 0;
    while (dut.win_w !== 16'd1600 && waited < 4_000_000) begin
      @(posedge clk); waited = waited + 1;
    end
    check(dut.win_w === 16'd1600 && dut.win_h === 16'd900,
          "it is taken in the next blanking");
    check(waited < 2_475_000, "within one frame");

    // A MODE CHANGE IS A WRITE LIKE ANY OTHER: the raster takes the new
    // totals whole, in blanking, and scans them. 720p's line is 1650.
    wr(A_H_TOTAL, 32'd1650); wr(A_H_ACTIVE, 32'd1280);
    wr(A_HS_BEG, 32'd1390);  wr(A_HS_END, 32'd1430);
    wr(A_V_TOTAL, 32'd750);  wr(A_V_ACTIVE, 32'd720);
    wr(A_VS_BEG, 32'd725);   wr(A_VS_END, 32'd730);
    wr(A_WIN_X0, 32'd0);
    wr(A_ARM, 32'd1);
    waited = 0;
    while (dut.h_total !== 12'd1650 && waited < 4_000_000) begin
      @(posedge clk); waited = waited + 1;
    end
    check(dut.h_total === 12'd1650 && dut.v_total === 11'd750,
          "armed: the raster took the new mode");
    repeat (8000) @(posedge clk);         // a few of its lines
    check(line_len == 1650, "and scans a 1650-pixel line");

    if (fails) $display("\n%0d FAILED", fails);
    else $display("\nscanout: all checks passed");
    $finish;
  end

  initial begin
    #200_000_000;
    $display("FAIL: timeout");
    $finish;
  end
endmodule
