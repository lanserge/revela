# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
## The design's own constraints: every one names a cell the block design
## instantiates. The jacks' pins and clocks belong to the board's
## jacks (hdmi_in.xdc, hdmi_out.xdc), each named by the composite that
## owns it, and the build collects every one the design uses.

## The software reset is quasi-static: held for milliseconds by a GPIO
## write, absorbed by the receiver re-anchoring on the next vsync. Scoped
## to the one register so the clock converter's own CDC stays timed.
## The software reset is quasi-static everywhere by design: held for
## milliseconds by a GPIO write, absorbed by re-anchoring on the next
## frame. False-path it wholesale rather than per destination clock.
set_false_path -from [get_cells -hier -filter {NAME =~ */ctrl_gpio/*gpio_Data_Out_reg*}]

## The coefficient crossing is GONE, and so are the three false paths
## that used to describe it. The register file and the datapath both
## live on clk_out1 now -- the board's own 148.5, no cable in its
## ancestry -- so register-to-datapath paths are ordinary same-domain
## timing, and a false path left here would not be conservative, it
## would HIDE real paths from analysis. A constraint file describes
## the design it was written against; the design changed, so it did.

## What remains crossing INTO the island is the header's facts:
## blrx latches width/height/phase/bits in the pixel domain, the ISP
## wrapper latches them again at each frame's SOF in its own. Safe by
## CONTRACT, not by timing: they change at header-accept, a full line
## (~2200 clocks) before the first pixel, and the SOF that triggers
## the wrapper's latch travels through the same converter behind them
## -- by the time it arrives they have been still for thousands of
## cycles. Between two unrelated 148.5MHz clocks the setup window is
## effectively zero, so timing these paths is not strict, it is
## meaningless. The receiver's diagnostic counters cross to the PS
## GPIOs under the same contract: counts, read at leisure.
set_false_path -from [get_cells -hier -filter {NAME =~ */blrx/*hdr_*_reg*}]
## The HDMI receiver's own account of the link -- alignments, eye widths,
## bit slips, the bond -- crosses to rx_gpio the same way: states that
## change once per search, read at leisure.
set_false_path -from [get_cells -hier -filter {NAME =~ */dvi_rx/*u_align/eye_reg* || NAME =~ */dvi_rx/*u_align/slips_reg* || NAME =~ */dvi_rx/*u_align/aligned_reg || NAME =~ */dvi_rx/*u_bond/bonded_reg}] -to [get_clocks clk_fpga_0]

## link_reset: two paths here are asynchronous BY CONSTRUCTION, and
## timing them is not a conservative choice, it is a meaningless one.
##
## 1. The lock signal. dvi2rgb's `aLocked` is named for what it is --
##    asynchronous -- and lk0/lk1 are the synchroniser that makes it
##    usable. Timing the launch into lk0 asks the tool to guarantee a
##    relationship the design explicitly does not rely on.
##
## 2. The reset's ASSERTION. `arst` drives the asynchronous preset of
##    the r0/r1 pair, and asserting with no pixel clock at all is the
##    entire reason this module exists -- an unplug takes the clock
##    away. Its DEASSERTION is synchronous, through those same two
##    flops, and that path stays timed, which is the half that matters.
##
## Left timed and this fails by 3.2ns on a route the tool has no
## reason to keep short, since nothing depends on its length.
set_false_path -to [get_pins -hier -filter {NAME =~ *link_rst*/lk0_reg*/D}]
set_false_path -to [get_pins -hier -filter {NAME =~ *link_rst*/r0_reg*/PRE}]
set_false_path -to [get_pins -hier -filter {NAME =~ *link_rst*/r1_reg*/PRE}]


# The ISP clock island is BACK -- not for timing, which np2hw solved,
# but because the receiver's clock stops with the cable and the
# island's does not. Its one exception lives above, with the header
# facts it belongs to.
