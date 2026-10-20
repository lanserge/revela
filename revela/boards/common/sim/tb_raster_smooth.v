// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// tb_raster_smooth -- the raster genlocked to a source whose frame runs
// DRIFT fine steps shorter than the raster's, the loop closed: every step
// the raster makes moves the source's next arrival by one step the other
// way, as the steered clock would. The source's first frame start comes
// while the raster still holds after reset, and at frame JUMP_AT its phase
// jumps JUMP_PX pixels. One line per frame -- where the frame start
// arrived, the steps the raster made and how evenly, whether it held, any
// line or frame cut short, and what reached the sink -- for the test to
// judge.
`timescale 1ns/1ps
module tb_raster_smooth #(
    parameter FOLLOW = 1,               // raster.v's follow
    parameter DRIFT = 20,               // steps a frame the source gains
    parameter START = 0,                // the first arrival, pixels late
    parameter FRAMES = 300,
    parameter QUIET = 0,                // frames of raster.v's quiet
    parameter JUMP_AT = -1,             // the frame the source's phase jumps
    parameter JUMP_PX = 0               // ...by this many pixels, later
);
    localparam HT = 100, VT = 50, PS = 7, FPX = HT * VT, TARGET = 8;
    reg clk = 0, rst = 1;
    always #5 clk = !clk;
    reg tvalid = 0, tuser = 1;
    wire tready;
    wire ps_en, ps_incdec;
    reg [3:0] done_sr = 0;
    wire ps_done = done_sr[3];
    always @(posedge clk) done_sr <= {done_sr[2:0], ps_en};
    wire [15:0] ax0, ay0;
    wire act, hs, vs;
    wire [23:0] data;
    wire [14:0] status;
    raster #(.XB(12), .YB(11), .G_LEAD(2),
             .G_QUIET_STEPS(QUIET * FPX * PS)) dut (
        .clk(clk), .rst(rst), .locked(1'b1),
        .s_axis_tdata(24'h0), .s_axis_tvalid(tvalid),
        .s_axis_tready(tready), .s_axis_tuser(tuser), .s_axis_tlast(1'b0),
        .h_total(12'd100), .h_active(12'd80), .hs_beg(12'd84),
        .hs_end(12'd90), .v_total(11'd50), .v_active(11'd40),
        .vs_beg(11'd42), .vs_end(11'd44), .hs_pos(1'b1), .vs_pos(1'b1),
        .ps_per_px(16'd7), .win_x0(16'd0), .win_y0(16'd10),
        .win_w(16'd80), .win_h(16'd30), .center(1'b0), .follow(FOLLOW[0]),
        .ps_en(ps_en), .ps_incdec(ps_incdec), .ps_done(ps_done),
        .act_x0(ax0), .act_y0(ay0), .vid_active_video(act),
        .vid_data(data), .vid_hsync(hs), .vid_vsync(vs), .status(status));

    // time in fine steps of the raster's clock; the source's next frame
    // start at `due_at`. A retarding step makes the raster later, so the
    // source comes one step sooner by its clock.
    longint now = 0, due_at = 0;
    integer frame = 0, steps = 0, cuts = 0, since = 0, gap_min = 0, gap_max = 0;
    integer err_px = 0, held = 0;
    // pixels of the stream's own (0) that reached the sink this frame, and
    // whether the first of them was not where the window begins -- a frame
    // shown from somewhere in its middle. The fill is 101010.
    integer shown = 0, astray = 0;
    reg first_d = 0;
    // clocks from reset, and the one the stream's first pixel reached the
    // sink on
    longint clocks = 0, first_seen = -1;
    always @(posedge clk) if (!rst) clocks = clocks + 1;
    always @(posedge clk) begin
        if (act && data == 24'h0) begin
            if (first_seen < 0) first_seen = clocks;
            if (shown == 0 && !first_d) astray = astray + 1;
            shown = shown + 1;
        end
        first_d <= dut.first_px;
    end
    // A CUT is what a sink would see as a broken line or frame: a line
    // that does not run to its last pixel, or a line counter that moves
    // other than on by one, round from the last line, or not at all (a
    // hold repeating its blank line).
    reg [11:0] x_d = 0;
    reg [10:0] y_d = 0;
    reg run_d = 0;
    always @(posedge clk) begin
        if (run_d) begin
            if (dut.x != x_d + 1 && !(dut.x == 0 && x_d == HT - 1))
                cuts = cuts + 1;
            if (dut.y != y_d && dut.y != y_d + 1
                && !(dut.y == 0 && y_d == VT - 1))
                cuts = cuts + 1;
        end
        if (dut.holding) held = 1;
        x_d <= dut.x; y_d <= dut.y;
        run_d <= !rst && !dut.held;
    end
    always @(posedge clk) begin
        if (!rst) begin
            now = now + PS;
            since = since + 1;
            if (ps_en) begin
                due_at = due_at + (ps_incdec ? -1 : 1);
                steps = steps + (ps_incdec ? 1 : -1);
                if (gap_min == 0 || since < gap_min) gap_min = since;
                if (since > gap_max) gap_max = since;
                since = 0;
            end
            if (!tvalid && now >= due_at) begin
                tvalid <= 1; tuser <= 1;
                // where the raster is as the frame start arrives, past the
                // target, the shorter way round
                err_px = (dut.y - TARGET) * HT + dut.x;
                if (err_px > FPX / 2) err_px = err_px - FPX;
                if (err_px < -FPX / 2) err_px = err_px + FPX;
                $display("FRAME %0d err=%0d steps=%0d cuts=%0d gap=%0d..%0d shown=%0d astray=%0d held=%0d",
                         frame, err_px, steps, cuts, gap_min, gap_max, shown,
                         astray, held);
                shown = 0; astray = 0; held = 0;
                frame = frame + 1;
                steps = 0; gap_min = 0; gap_max = 0;
                due_at = due_at + FPX * PS - DRIFT
                       + ((frame == JUMP_AT) ? JUMP_PX * PS : 0);
                if (frame > FRAMES) begin
                    $display("FIRST SHOWN at %0d", first_seen);
                    $display("smooth: done");
                    $finish;
                end
            end
        end
    end
    // the frame start is held until the raster takes it, then one pixel
    // of the frame's own, for the sink to show or not
    always @(posedge clk)
        if (tvalid && tready) begin
            if (tuser) tuser <= 0;
            else tvalid <= 0;
        end
    initial begin
        repeat (10) @(negedge clk);
        rst = 0;
        // the first arrival START pixels past the target, a frame in --
        // while the raster still holds for it
        due_at = (FPX + TARGET * HT + START) * PS;
    end
endmodule
