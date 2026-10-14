// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// tmds_clocking -- a TMDS serializer's clocks, made beside it from the
// display's pixel clock by a PLL the firmware reprograms per display
// clock. At 148.5 MHz the bit clock is 742.5 MHz, past what a global
// buffer carries, so it is regional: the PLL's x5 output drives a BUFIO
// for the bit clock and a BUFR dividing it by five for the serializer's
// pixel clock -- the two in step, the divider cleared when lock is
// gained -- and the PLL's feedback returns through a BUFR so the regional
// clocks are the ones de-skewed. The arrangement rgb2dvi uses inside, with
// the PLL outside so it can change.
module tmds_clocking (
    input  wire pclk,        // the PLL's input: the display's pixel clock
    input  wire pclk_locked, // that clock is steady
    input  wire x5,          // the PLL's x5 output, unbuffered
    input  wire fb_out,      // the PLL's feedback out
    output wire fb_in,       // and back in, through the deskew buffer
    input  wire locked,      // the PLL's
    output wire sclk,        // the bit clock
    output wire pclk_io,     // the serializer's pixel clock
    output wire rst          // the transmitter's reset: high until both lock
);
    wire lock_gained;
    BUFIO bit_clock (.O(sclk), .I(x5));
    BUFR #(.BUFR_DIVIDE("5"), .SIM_DEVICE("7SERIES")) pixel_clock (
        .O(pclk_io), .CE(1'b1), .CLR(lock_gained), .I(x5));
    BUFR #(.BUFR_DIVIDE("1"), .SIM_DEVICE("7SERIES")) deskew (
        .O(fb_in), .CE(1'b1), .CLR(1'b0), .I(fb_out));

    // lock gained, seen in the pixel clock: one pulse clears the divider
    // so its phase follows the bit clock's from the start
    (* ASYNC_REG = "TRUE" *) reg ls0 = 1'b0, ls1 = 1'b0;
    reg ls2 = 1'b0;
    always @(posedge pclk) begin
        ls0 <= locked;
        ls1 <= ls0;
        ls2 <= ls1;
    end
    assign lock_gained = ls1 && !ls2;

    assign rst = !(locked && pclk_locked);
endmodule
