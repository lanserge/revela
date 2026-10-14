// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// clock_select -- the display's pixel clock, one of three at run time,
// through two cascaded glitch-free global multiplexers, chosen by one
// register.
//
// The clock managers run their recipes all the time -- one VCO per family
// of display clocks -- so a switch is a multiplexer's, not a
// reconfiguration: no lock or filter tables, and a fractional recipe as
// good as a whole one. Only PIXEL clocks pass here, all within a global
// buffer's reach; the serializer's bit clock is made beside the
// serializer from whichever passes (tmds_clocking). The genlock steers
// every manager, so whichever row is chosen follows the stream; its lock
// and its step-done are the ones passed on. After a switch the lock is
// held low a while, so the raster restarts on the new clock.
module clock_select #(
    // raster clocks held in reset after a switch: the multiplexers' own
    // settling, with margin
    parameter HOLD = 1024
) (
    input  wire [1:0] sel,       // the build's clock table row: 0, 1 or 2
    input  wire       clk_0,
    input  wire       locked_0,
    input  wire       psdone_0,
    input  wire       clk_1,
    input  wire       locked_1,
    input  wire       psdone_1,
    input  wire       clk_2,
    input  wire       locked_2,
    input  wire       psdone_2,
    output wire       clk,       // the display's pixel clock
    output wire       locked,
    output wire       psdone
);
    wire low;
    BUFGMUX_CTRL first (.O(low), .I0(clk_0), .I1(clk_1), .S(sel[0]));
    BUFGMUX_CTRL second (.O(clk), .I0(low), .I1(clk_2), .S(sel[1]));

    // the choice, as the display clock sees it
    (* ASYNC_REG = "TRUE" *) reg [1:0] sel_s0 = 2'd0, sel_s1 = 2'd0;
    reg [1:0] sel_d = 2'd0;
    reg [$clog2(HOLD + 1)-1:0] hold = HOLD;
    always @(posedge clk) begin
        sel_s0 <= sel;
        sel_s1 <= sel_s0;
        sel_d <= sel_s1;
        if (sel_d != sel_s1) hold <= HOLD;
        else if (hold != 0) hold <= hold - 1'b1;
    end

    wire lock_sel = sel_s1[1] ? locked_2 : sel_s1[0] ? locked_1 : locked_0;
    assign psdone = sel_s1[1] ? psdone_2 : sel_s1[0] ? psdone_1 : psdone_0;
    assign locked = lock_sel && hold == 0;
endmodule
