// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// tb_raster_smooth -- the raster genlocked to a source whose frame runs
// DRIFT fine steps shorter than the raster's, the loop closed: every step
// the raster makes moves the source's next arrival by one step the other
// way, as the steered clock would. One line per frame -- where the frame
// start arrived, the steps the raster made and how evenly -- for the
// test to judge.
`timescale 1ns/1ps
module tb_raster_smooth #(
    parameter STEER = 0,                // raster.v's steer
    parameter DRIFT = 20,               // steps a frame the source gains
    parameter START = 0,                // the first arrival, pixels late
    parameter FRAMES = 300,
    parameter QUIET = 0                 // frames of raster.v's quiet
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
        .win_w(16'd80), .win_h(16'd30), .center(1'b0), .follow(1'b1),
        .steer(STEER[1:0]),
        .ps_en(ps_en), .ps_incdec(ps_incdec), .ps_done(ps_done),
        .act_x0(ax0), .act_y0(ay0), .vid_active_video(act),
        .vid_data(data), .vid_hsync(hs), .vid_vsync(vs), .status(status));

    // time in fine steps of the raster's clock; the source's next frame
    // start at `due_at`. A retarding step makes the raster later, so the
    // source comes one step sooner by its clock.
    longint now = 0, due_at = 0;
    integer frame = 0, steps = 0, jams = 0, since = 0, gap_min = 0, gap_max = 0;
    integer err_px = 0;
    // pixels of the stream's own (0) that reached the sink this frame: the
    // fill is 101010
    integer shown = 0;
    always @(posedge clk) if (act && data == 24'h0) shown = shown + 1;
    reg [10:0] y_d = 0;
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
            y_d <= dut.y;
            if (dut.y != y_d && dut.y != y_d + 1 && dut.y != 0)
                jams = jams + 1;
            if (!tvalid && now >= due_at) begin
                tvalid <= 1; tuser <= 1;
                // where the raster is as the frame start arrives, past the
                // target, the shorter way round
                err_px = (dut.y - TARGET) * HT + dut.x;
                if (err_px > FPX / 2) err_px = err_px - FPX;
                if (err_px < -FPX / 2) err_px = err_px + FPX;
                $display("FRAME %0d err=%0d steps=%0d jams=%0d gap=%0d..%0d shown=%0d",
                         frame, err_px, steps, jams, gap_min, gap_max, shown);
                shown = 0;
                frame = frame + 1;
                steps = 0; gap_min = 0; gap_max = 0;
                due_at = due_at + FPX * PS - DRIFT;
                if (frame > FRAMES) begin
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
        // the first arrival START pixels past the target, after two frames
        due_at = (2 * FPX + TARGET * HT + START) * PS;
    end
endmodule
