# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# The AXI bus of a Zynq-7000 design, built by Vivado's automation and then
# REPAIRED -- because the automation picks resets and clocks by what it
# likes the look of, and every rule below is a board hang already paid for
# once. It runs after every cell and connection of the design exists.
#
# `spec` is a dict:
#   island_clock   the clock the bus's slaves share, as the automation names
#                  it (/ps7/FCLK_CLK0); %island% in a step's config is it
#   bus_reset      the bus's own reset output (rst_bus/peripheral_aresetn),
#                  deliberately blind to the link
#   link_reset     the link's reset output, which must reset nothing a host
#                  can reach but the pins in link_reset_keeps
#   link_reset_keeps  pins allowed on the link's reset, by path
#   control_interconnect  the interconnect the automation builds for the
#                  control bus (ps7_axi_periph), whose ports are repaired
#   (any key)      a step with `only KEY` runs where the spec's KEY is
#                  true -- the design sets it, from its own parameters
#   steps          what to do, in order:
#     {automate PIN config CFG repair 0|1}   automation for one interface
#     {second_master SLAVE master M name N segment SEG range R}
#                  put a 2x1 interconnect in front of SLAVE for master M
#     {memory_master INTERCONNECT master M clock C port K}
#                  add master M as port K of the memory interconnect
#     {reset_polarity CELL}   state an automation reset's input polarity
#     {sweep}                 move every bus pin off the link's reset
#     {addresses SEGMENTS}    assign the map, then each [space segment] pair

proc zynq7_axi_bus {name spec} {
    foreach step [dict get $spec steps] {
        if {[dict exists $step only]
                && ![dict get $spec [dict get $step only]]} {
            continue
        }
        set kind [lindex [dict keys $step] 0]
        switch -- $kind {
            automate {
                set cfg [string map [list %island% \
                    [dict get $spec island_clock]] [dict get $step config]]
                apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config \
                    $cfg [get_bd_intf_pins [dict get $step automate]]
                if {[dict exists $step repair] && [dict get $step repair]} {
                    _zynq7_repair_control $spec
                }
            }
            second_master { _zynq7_second_master $spec $step }
            memory_master { _zynq7_memory_master $step }
            reset_polarity {
                set rc [get_bd_cells -quiet [dict get $step reset_polarity]]
                if {$rc ne ""} {
                    set_property -dict [list CONFIG.C_EXT_RST_ACTIVE_HIGH {1} \
                        CONFIG.C_AUX_RST_ACTIVE_HIGH {1}] $rc
                } else {
                    puts "zynq7_axi_bus: no automation reset generator to\
 repair (one clock)"
                }
            }
            sweep { _zynq7_sweep $spec }
            addresses {
                assign_bd_address
                foreach pair [dict get $step addresses] {
                    lassign $pair space segment only
                    if {$only ne "" && ![dict get $spec $only]} { continue }
                    assign_bd_address -target_address_space $space \
                        [get_bd_addr_segs $segment] -force
                }
            }
            default { error "zynq7_axi_bus: unknown step $kind" }
        }
    }
}

# Every port of the control interconnect the automation put on the link's
# reset moves to the bus's, and the build REFUSES if any survives: a cable
# pull would hold the bus port in reset and hang the processor.
proc _zynq7_repair_control {spec} {
    set busnet [get_bd_nets -of_objects [get_bd_pins [dict get $spec bus_reset]]]
    set ic [dict get $spec control_interconnect]
    set link [lindex [split [dict get $spec link_reset] /] 0]
    foreach pin [get_bd_pins -quiet $ic/*ARESETN*] {
        set n [get_bd_nets -quiet -of_objects $pin]
        if {$n ne "" && [string match "*$link*" [get_property NAME $n]]} {
            disconnect_bd_net $n $pin
            connect_bd_net -net $busnet $pin
            puts "zynq7_axi_bus: moved $pin off the link's reset"
        }
    }
    foreach pin [get_bd_pins -quiet $ic/*ARESETN*] {
        set n [get_bd_nets -quiet -of_objects $pin]
        if {$n ne "" && [string match "*$link*" [get_property NAME $n]]} {
            error "zynq7_axi_bus: $pin still rides the link's reset -- a\
 cable pull would hold the bus port in reset and hang the processor"
        }
    }
}

# A second master for one slave: a 2x1 interconnect in front of it,
# single-clock and single-reset (the bus's own -- the coupler IS the path,
# and a held coupler hangs exactly like a held slave), wired explicitly.
proc _zynq7_second_master {spec step} {
    set slave [dict get $step second_master]
    set ic [dict get $step name]
    set clock [string trimleft [dict get $spec island_clock] /]
    set busnet [get_bd_nets -of_objects [get_bd_pins [dict get $spec bus_reset]]]
    set net [get_bd_intf_nets -of_objects [get_bd_intf_pins $slave]]
    set mpin ""
    foreach ip [get_bd_intf_pins -of_objects $net] {
        if {$ip ne [get_bd_intf_pins $slave]} { set mpin $ip }
    }
    if {$mpin eq ""} { error "zynq7_axi_bus: no master found feeding $slave" }
    delete_bd_objs $net
    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect $ic
    set_property -dict [list CONFIG.NUM_SI {2} CONFIG.NUM_MI {1}] [get_bd_cells $ic]
    connect_bd_intf_net $mpin [get_bd_intf_pins $ic/S00_AXI]
    connect_bd_intf_net [get_bd_intf_pins [dict get $step master]] \
        [get_bd_intf_pins $ic/S01_AXI]
    connect_bd_intf_net [get_bd_intf_pins $ic/M00_AXI] [get_bd_intf_pins $slave]
    foreach c {ACLK S00_ACLK S01_ACLK M00_ACLK} {
        connect_bd_net [get_bd_pins $clock] [get_bd_pins $ic/$c]
    }
    foreach r {ARESETN S00_ARESETN S01_ARESETN M00_ARESETN} {
        connect_bd_net -net $busnet [get_bd_pins $ic/$r]
    }
    set space /[lindex [split [dict get $step master] /] 0]/[lindex [split [dict get $step master] /] 1]
    assign_bd_address -target_address_space $space \
        [get_bd_addr_segs [dict get $step segment]] -offset 0x00000000 \
        -range [dict get $step range]
}

# One more master on the memory interconnect the automation built:
# explicitly, because the automation's second pass built a private
# interconnect whose master port went nowhere and called it a warning.
proc _zynq7_memory_master {step} {
    set ic [dict get $step memory_master]
    set k [format %02d [dict get $step port]]
    set_property CONFIG.NUM_SI [expr {[dict get $step port] + 1}] [get_bd_cells $ic]
    connect_bd_intf_net [get_bd_intf_pins [dict get $step master]] \
        [get_bd_intf_pins $ic/S${k}_AXI]
    connect_bd_net [get_bd_pins [dict get $step clock]] [get_bd_pins $ic/S${k}_ACLK]
    connect_bd_net -net [get_bd_nets -of_objects [get_bd_pins $ic/S00_ARESETN]] \
        [get_bd_pins $ic/S${k}_ARESETN]
}

# THE RESET SWEEP: anything a host can reach answers to the board. The
# link's reset legitimately resets only the pins in link_reset_keeps; every
# other input the automations parked on it moves to the bus's reset.
proc _zynq7_sweep {spec} {
    set linknet [get_bd_nets -quiet -of_objects [get_bd_pins [dict get $spec link_reset]]]
    if {![llength $linknet]} { return }
    set keeps [dict get $spec link_reset_keeps]
    foreach p [get_bd_pins -quiet -of_objects $linknet] {
        set pn [get_property PATH $p]
        if {[get_property DIR $p] ne "I"} { continue }
        if {$pn in $keeps} { continue }
        disconnect_bd_net $linknet [get_bd_pins $pn]
        set bn [get_bd_nets -quiet -of_objects [get_bd_pins [dict get $spec bus_reset]]]
        if {[llength $bn]} {
            connect_bd_net -net $bn [get_bd_pins $pn]
        } else {
            connect_bd_net [get_bd_pins [dict get $spec bus_reset]] [get_bd_pins $pn]
        }
        puts "zynq7_axi_bus: reset sweep moved $pn to [dict get $spec bus_reset]"
    }
}
