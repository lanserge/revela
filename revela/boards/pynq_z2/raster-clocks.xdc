# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
# Strict XDC: no Tcl control flow -- an `if` in an .xdc is silently
# skipped by some stages and the gray-pointer crossing then gets timed to
# picosecond requirements between unrelated MMCMs (bench-paid, twice).
#
# The raster's clock is asynchronous to the board's: the FIFO's gray
# discipline is the synchronizer; the timer stands down.
set_clock_groups -asynchronous \
    -group [get_clocks clk_out1_clk_raster] \
    -group [get_clocks clk_fpga_0]
