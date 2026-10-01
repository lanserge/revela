# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# The Zynq-7000 processing system, with the board's preset under the
# design's settings.

# Create the PS as `name`, apply the board preset -- DDR, MIO, the fixed
# I/O made external -- and then `config`, the design's settings, which a
# preset applied afterwards would overwrite.
proc zynq7_ps7 {name config} {
    set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7 $name]
    apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
        -config {make_external "FIXED_IO, DDR" apply_board_preset "1"} $ps
    set props {}
    dict for {k v} $config { lappend props CONFIG.$k $v }
    set_property -dict $props [get_bd_cells $name]
}
