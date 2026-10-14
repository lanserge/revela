// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// tb_video_cdc -- a raster into a same-frequency clock of unknown phase:
// at each phase, after the reader's reset, every beat arrives once, in
// order, a few cycles late and clear of the writer; and a second reset at another
// phase lands it again.
`timescale 1ps/1ps
module tb_video_cdc;
    localparam integer P = 13468;       // 74.25 MHz
    reg wclk = 0, rclk = 0, rrst = 1;
    integer phase = 0;
    always #(P/2) wclk = !wclk;
    initial begin
        #1;
        forever begin
            #(P/2) rclk = !rclk;
        end
    end
    reg [26:0] beat = 0;
    always @(posedge wclk) beat <= beat + 1'b1;
    wire de, hs, vs;
    wire [23:0] d;
    video_cdc dut (
        .wclk(wclk), .vid_in_active_video(beat[26]), .vid_in_data(beat[23:0]),
        .vid_in_hsync(beat[25]), .vid_in_vsync(beat[24]),
        .rclk(rclk), .rrst(rrst), .vid_out_active_video(de),
        .vid_out_data(d), .vid_out_hsync(hs), .vid_out_vsync(vs));
    wire [26:0] got = {de, hs, vs, d};

    integer fails = 0, runs = 0, n;
    reg [26:0] prev;
    task run(input integer shift);
        begin
            // move the reader's clock by `shift` ps, then reset it there
            rrst = 1;
            #(shift);
            force rclk = 0; #(P); release rclk;
            repeat (4) @(posedge rclk);
            rrst = 0;
            repeat (12) @(posedge rclk);
            prev = got;
            for (n = 0; n < 3000; n = n + 1) begin
                @(posedge rclk);
                // and a few beats behind the writer -- clear of the entry
                // being written, far from the one about to be
                if (beat - got < 8 || beat - got > 11) begin
                    if (fails < 5)
                        $display("FAIL: shift %0d: %0d beats behind", shift,
                                 beat - got);
                    fails = fails + 1;
                end
                if (got !== prev + 1'b1) begin
                    if (fails < 5)
                        $display("FAIL: shift %0d: %h after %h", shift, got, prev);
                    fails = fails + 1;
                end
                prev = got;
            end
            runs = runs + 1;
        end
    endtask
    initial begin
        repeat (8) @(posedge wclk);
        run(0); run(P/5); run(2*P/5); run(3*P/5); run(4*P/5); run(P/3);
        if (fails == 0) $display("video_cdc: all checks passed (%0d phases)", runs);
        $finish;
    end
endmodule
