# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# The Zynq-7000 processing system, with the board's preset under the
# design's settings.

# Create the PS as `name`, apply the board preset -- DDR, MIO -- and then
# `config`, the design's settings, which a preset applied afterwards would
# overwrite. Its DDR and fixed I/O are the design's pins, wired where the
# design states them: the automation exports them whatever it is asked, so
# the ports it made are taken away again.
proc zynq7_ps7 {name config} {
    set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7 $name]
    apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
        -config {make_external "" apply_board_preset "1"} $ps
    foreach intf {DDR FIXED_IO} {
        set port [get_bd_intf_ports -quiet -of_objects \
            [get_bd_intf_nets -quiet -of_objects [get_bd_intf_pins $name/$intf]]]
        if {$port ne {}} {
            delete_bd_objs [get_bd_intf_nets -of_objects $port] $port
        }
    }
    set props {}
    dict for {k v} $config { lappend props CONFIG.$k $v }
    set_property -dict $props [get_bd_cells $name]
}
