# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
# Strict XDC: no Tcl control flow -- an `if` in an .xdc is silently
# skipped by some stages and the gray-pointer crossing then gets timed to
# picosecond requirements between unrelated MMCMs (bench-paid, twice).
#
# The display's clocks are asynchronous to the board's: the FIFO's gray
# discipline is the synchronizer; the timer stands down. The four rows
# reach the raster through the clock select one at a time -- they never
# meet in a path -- and so do the serializer clocks made from each.
set_clock_groups -asynchronous \
    -group [get_clocks -include_generated_clocks {clk_raster27_clk_out clk_raster_clk_out clk_raster_clk_out2 clk_raster27_clk_out2}] \
    -group [get_clocks clk_fpga_0]
set_clock_groups -logically_exclusive \
    -group [get_clocks -include_generated_clocks clk_raster27_clk_out] \
    -group [get_clocks -include_generated_clocks clk_raster_clk_out] \
    -group [get_clocks -include_generated_clocks clk_raster_clk_out2] \
    -group [get_clocks -include_generated_clocks clk_raster27_clk_out2]
# The serializer's clocks are the raster's frequency at a phase chance
# sets -- a regional divider starts on any of five bit-clock edges -- so
# the raster reaches them through video_cdc's ring, and the timer, which
# would assume the one aligned edge, stands down between them.
set_clock_groups -asynchronous \
    -group [get_clocks {clk_raster27_clk_out clk_raster_clk_out clk_raster_clk_out2 clk_raster27_clk_out2}] \
    -group [get_clocks tmds_*]
# The clock select's own select pins. The raster switches its clock from
# the copy it takes in vertical blanking, on the clock being switched, so
# the select is a path inside that clock -- but a BUFGMUX_CTRL is built to
# take its select at any moment, glitch-free, waiting out both clocks
# itself; timed, it is a half-period demand on an event that happens once
# a mode change, between frames. S* is the macro's S in synthesis and the
# BUFGCTRL's S0 and S1 after it, and no other pin of either.
set_false_path -to [get_pins {clk_sel/first/S* clk_sel/second/S* clk_sel/third/S*}]
# The serializer's MMCM takes the display clock from the clock select, so
# every row's clock reaches its input and the tools check its VCO against
# the fastest, 148.5 MHz, whatever clock it is built to start on. It starts
# on the reset row's clock with that row's recipe, in range, and the
# firmware loads each row's recipe before its clock switches; the x5
# relation every recipe keeps makes each row's derived clock exact, so
# timing is analysed at the real rates. The check alone is wrong here, and
# for this cell only.
create_waiver -type DRC -id {AVAL-46} -user {revela} -objects [get_cells -hierarchical -filter {REF_NAME == MMCME2_ADV && NAME =~ "*tmds_pll*"}] -description {The serializer MMCM's input is the display clock select: its VCO is checked against the fastest row, not the reset row its recipe is for.}
