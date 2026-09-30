# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
# Genlock builds only (bd.tcl adds this file when the display has
# its own MMCM). Strict XDC: no Tcl control flow -- an `if` in an
# .xdc is silently skipped by some stages and the gray-pointer
# crossing then gets timed to picosecond requirements between
# unrelated MMCMs (bench-paid, twice).
#
# The display clock is asynchronous to both possible island clocks:
# the FIFO's gray discipline is the synchronizer; the timer stands
# down. -quiet: only one island clock exists per build.
set_clock_groups -asynchronous \
    -group [get_clocks clk_out1_rx_clk_out74_0] \
    -group [get_clocks -quiet {clk_out1_rx_clk_out_0 clk_fpga_0}]
