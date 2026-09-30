# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
# Bitstream in ONE process. launch_runs spawns children through Vivado's
# own wrappers, which shed the LD_PRELOAD that keeps the allocator sane
# in this container -- so no children: global synthesis (no per-IP OOC
# runs), synth/place/route/bitstream all in this Tcl session.
if {[llength $argv] != 1} {
    error "impl_a.tcl: give the build directory: -tclargs <build dir>"
}
set out [file normalize [lindex $argv 0]]
source [file join $out generated paths.tcl]
source [file join $out generated receiver.tcl]
open_project [file join $out build rx rx.xpr]
set bd [get_files rx.bd]
set_property synth_checkpoint_mode None $bd
generate_target all $bd
update_compile_order -fileset sources_1
# Whatever the front end's vendor IP expects beside the run at synth
# time: the launch_runs flow would have put it there. We are the run.
bayerlink_frontend_synth_prep $p_vivado_library
synth_design -top rx_wrapper -part xc7z020clg400-1
opt_design
# Explore: the last tens of picoseconds live in the placer's seed.
# Explore is the general-purpose choice; ExtraTimingOpt spends its
# effort where this design needs it. The post-route physopt loop in
# impl_b can give back tens of picoseconds, but only what placement
# left on the table -- when it exhausts its directives at -0.027, the
# next lever is here, not there.
place_design -directive ExtraTimingOpt
# Stop HERE: this process has a long allocation history, and heavy
# commands' teardowns are where the container heap detonates. The
# placed checkpoint hands a fresh process the short half.
file mkdir /tmp/rxout
write_checkpoint -force /tmp/rxout/placed.dcp
file copy -force /tmp/rxout/placed.dcp [file join $out build placed.dcp]
puts "PLACED_DONE"
