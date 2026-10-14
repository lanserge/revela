// Copyright 2026 Serge Rabyking
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// Behavioural stand-ins for the AMD primitives the board's hand-written
// modules instantiate, for elaboration and simulation only: Vivado has the
// real ones, and this file is never a build source.
module BUFGMUX_CTRL (output wire O, input wire I0, input wire I1,
                     input wire S);
    assign O = S ? I1 : I0;
endmodule

module BUFIO (output wire O, input wire I);
    assign O = I;
endmodule

module BUFR #(parameter BUFR_DIVIDE = "BYPASS",
              parameter SIM_DEVICE = "7SERIES") (
    output wire O, input wire CE, input wire CLR, input wire I);
    assign O = I;               // elaboration only: not a divider model
endmodule
