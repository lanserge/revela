// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// tb_raster_genlock -- the raster following a stream: a frame start made
// to arrive at a chosen place in the raster, and what the raster does
// about it -- how many fine-phase steps, which way, and whether it holds
// for the next frame start instead -- and whether any line or frame on
// the cable was cut short. One line per arrival, for the test to judge.
`timescale 1ns/1ps
module tb_raster_genlock;
    localparam HT = 100, VT = 50, HA = 80, VA = 40;
    localparam Y0 = 10, WH = 30, PS = 7;
    reg clk = 0, rst = 1;
    always #5 clk = !clk;
    reg tvalid = 0;
    wire tready;
    wire ps_en, ps_incdec;
    reg [3:0] done_sr = 0;
    wire ps_done = done_sr[3];
    always @(posedge clk) done_sr <= {done_sr[2:0], ps_en};
    wire [15:0] ax0, ay0;
    wire act, hs, vs;
    wire [23:0] data;
    wire [14:0] status;
    raster #(.XB(12), .YB(11), .G_LEAD(2)) dut (
        .clk(clk), .rst(rst), .locked(1'b1),
        .s_axis_tdata(24'h0), .s_axis_tvalid(tvalid),
        .s_axis_tready(tready), .s_axis_tuser(1'b1), .s_axis_tlast(1'b0),
        .h_total(12'd100), .h_active(12'd80), .hs_beg(12'd84),
        .hs_end(12'd90), .v_total(11'd50), .v_active(11'd40),
        .vs_beg(11'd42), .vs_end(11'd44), .hs_pos(1'b1), .vs_pos(1'b1),
        .ps_per_px(16'd7), .quiet_bit(5'd31), .sink(1'b0),
        .win_x0(16'd0), .win_y0(16'd10),
        .win_w(16'd80), .win_h(16'd30), .center(1'b0), .follow(1'b1),
        .ps_en(ps_en), .ps_incdec(ps_incdec), .ps_done(ps_done),
        .act_x0(ax0), .act_y0(ay0), .vid_active_video(act),
        .vid_data(data), .vid_hsync(hs), .vid_vsync(vs), .status(status));

    integer steps = 0, ret = 0, cuts = 0, holds = 0;
    // A CUT is what a sink would see as a broken line or frame: a line
    // that does not run to its last pixel, or a line counter that moves
    // other than on by one, round from the last line, or not at all (a
    // hold repeating its blank line).
    reg [11:0] x_d = 0;
    reg [10:0] y_d = 0;
    reg run_d = 0, wait_d = 0;
    always @(posedge clk) begin
        if (ps_en) begin
            steps = steps + 1;
            if (ps_incdec) ret = ret + 1;
        end
        if (run_d) begin
            if (dut.x != x_d + 1 && !(dut.x == 0 && x_d == HT - 1))
                cuts = cuts + 1;
            if (dut.y != y_d && dut.y != y_d + 1
                && !(dut.y == 0 && y_d == VT - 1))
                cuts = cuts + 1;
        end
        if (dut.holding && !wait_d) holds = holds + 1;
        x_d <= dut.x; y_d <= dut.y;
        run_d <= !rst && !dut.held;
        wait_d <= dut.holding;
    end
    // the frame start, offered when the raster reaches (x, y), held
    // until taken
    task arrive(input integer n, input integer x, input integer y);
        begin
            @(negedge clk);
            while (!(dut.x == x && dut.y == y)) @(negedge clk);
            steps = 0; ret = 0; cuts = 0; holds = 0;
            tvalid = 1;
            @(negedge clk);
            while (!tready) @(negedge clk);
            @(negedge clk);
            tvalid = 0;
            // two frames to drain the debt, inside the source-gone time
            repeat (2 * HT * VT) @(negedge clk);
            $display("ARRIVAL %0d at %0d,%0d: steps=%0d retard=%0d holds=%0d cuts=%0d",
                     n, x, y, steps, ret, holds, cuts);
        end
    endtask
    initial begin
        repeat (10) @(negedge clk);
        rst = 0;
        repeat (2 * HT * VT) @(negedge clk);
        arrive(1, 40, 8);     // on the target line, 40 px late
        arrive(2, 30, 7);     // 70 px early
        arrive(3, 5, 9);      // a line and 5 px late
        arrive(4, 1, 8);      // one pixel: within the dead band
        arrive(5, 20, 3);     // five lines early: beyond steering, a hold
        arrive(6, 20, 45);    // thirteen lines early, cooled: no hold
        $display("genlock: done");
        $finish;
    end
endmodule
