# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Our modules' ports, read from their RTL, whatever the comments say."""
from revela.boards.rtl import module_ports

HEADER = """module m #(parameter W = 8) (
    input  wire         clk,
    // 1: follow the stream (the direct path);
    input  wire         follow,
    /* a block comment ); too */
    output wire [W-1:0] q
);
endmodule
"""


def test_a_comment_holding_a_close_paren_does_not_end_the_port_list():
    ports = module_ports(HEADER, "m", {"W": 5})
    assert ports == {"clk": ("input", 1), "follow": ("input", 1),
                     "q": ("output", 5)}
