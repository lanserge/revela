// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Placement became a run-time fact. These are the claims that came
// with it, checked rather than asserted.
//
// The register file runs on the bus clock and the raster runs on the
// cable's, so the interesting cases are all about the gap between
// them: a value written but not yet taken, a value taken in the wrong
// part of a frame, and a bus talking to a domain whose clock has gone
// away. That last one is why the file is on this side at all -- an
// unplug takes the pixel clock with it, and an AXI slave with no clock
// never answers.
//
// Run:  iverilog -g2012 -o /tmp/sr.vvp sim/tb_scanout_regs.v \
//           $OUT/hdl/scanout.v && /tmp/sr.vvp
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

  localparam [7:0] A_X0 = 8'h00, A_Y0 = 8'h04, A_W = 8'h08,
                   A_H  = 8'h0c, A_CEN = 8'h10, A_ARM = 8'h14;
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

  scanout_top dut (
      .clk(clk), .rst(rst), .locked(locked),
      .s_axi_aclk(aclk), .s_axi_aresetn(aresetn),
      .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid),
      .s_axi_awready(awready), .s_axi_wdata(wdata),
      .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
      .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
      .s_axi_araddr(araddr), .s_axi_arvalid(arvalid),
      .s_axi_arready(arready), .s_axi_rdata(rdata), .s_axi_rresp(rresp),
      .s_axi_rvalid(rvalid), .s_axi_rready(rready),
      .param_follow(1'b0), .ps_en(ps_en), .ps_incdec(ps_incdec),
      .ps_done(1'b1),
      // The stream is not what is under test: the raster free-runs and
      // paints fill, which still gives real blanking to land in.
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

  // A PICTURE MOVES ONLY BETWEEN FRAMES. Placement decides where every
  // pixel of a frame lands, so a copy taken mid-frame would move the
  // picture halfway down itself. Watch the raster's own copy: every
  // time it changes, the raster must be in vertical blanking.
  reg [15:0] seen_w;
  initial seen_w = 16'd1920;
  always @(posedge clk) begin
    if (dut.win_w !== seen_w) begin
      if (!dut.in_vblank) begin
        $display("FAIL: placement changed outside vertical blanking");
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

    // AN UNCONFIGURED BUILD SHOWS WHAT THE BAKED ONE SHOWED. The
    // reset values are this build's placement, on both sides.
    check(dut.win_w === 16'd1920 && dut.win_h === 16'd1080 &&
          dut.win_x0 === 16'd0 && dut.win_y0 === 16'd0 &&
          dut.center === 1'b1,
          "reset places the build's own window");
    rd(A_W);
    check(last_read[15:0] === 16'd1920, "the file agrees, read back");

    // A WRITE ALONE DOES NOT MOVE THE PICTURE. The frame in flight
    // keeps the placement it started with until the arm says take.
    wr(A_W, 32'd1280); check(last_resp === OKAY, "width write accepted");
    wr(A_H, 32'd720);  check(last_resp === OKAY, "height write accepted");
    wr(A_CEN, 32'd1);
    repeat (400) @(posedge clk);
    check(dut.win_w === 16'd1920,
          "written but unarmed: the raster has not moved");

    // ARMED, THE COPY IS TAKEN -- in blanking, which the monitor above
    // is watching for, and within a frame.
    wr(A_ARM, 32'd1); check(last_resp === OKAY, "arm accepted");
    waited = 0;
    while (dut.win_w !== 16'd1280 && waited < 4_000_000) begin
      @(posedge clk); waited = waited + 1;
    end
    check(dut.win_w === 16'd1280 && dut.win_h === 16'd720,
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
    wr(A_W, 32'd640);
    check(last_resp === OKAY, "clock gone: a write is still answered");
    rd(A_W);
    check(last_read[15:0] === 16'd640, "clock gone: and a read is too");
    wr(A_ARM, 32'd1);
    check(last_resp === OKAY, "clock gone: the arm is still answered");

    // ARMED MEANS FROZEN, and says so -- even with no reader running,
    // because the file cannot know that; it only knows nobody has
    // acknowledged.
    wr(A_W, 32'd320);
    check(last_resp === SLVERR, "armed: a placement write is REFUSED");
    rd(A_W);
    check(last_read[15:0] === 16'd640, "armed: and the value did not change");

    // THE ESCAPE HATCH. With no pixel clock the acknowledgement never
    // comes, so nothing would ever release the file. The arm word
    // itself stays writable: software times out and releases itself
    // rather than being locked out until someone reboots the board.
    rd(A_ARM);
    check(last_read[0] === 1'b1, "still armed, with no reader to ack");
    wr(A_ARM, 32'd0);
    check(last_resp === OKAY, "the arm can always be written back");
    wr(A_W, 32'd800);
    check(last_resp === OKAY, "released: placement writes flow again");

    // Plug it back in and the pending placement lands.
    pix_en = 1;
    repeat (50) @(posedge clk);
    wr(A_ARM, 32'd1);
    waited = 0;
    while (dut.win_w !== 16'd800 && waited < 4_000_000) begin
      @(posedge clk); waited = waited + 1;
    end
    check(dut.win_w === 16'd800, "cable back: the placement lands");

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
    wr(A_X0, 32'd42);
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

    if (fails) $display("\n%0d FAILED", fails);
    else $display("\nscanout placement: all checks passed");
    $finish;
  end

  initial begin
    #200_000_000;
    $display("FAIL: timeout");
    $finish;
  end
endmodule
