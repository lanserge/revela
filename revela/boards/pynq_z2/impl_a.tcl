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
source [file join $out generated flow.tcl]
# The board part lives outside Vivado's own store; without its path the
# project reopens with the board unset, and says so.
set_param board.repoPaths [list $p_board_files]
open_project [file join $out build rx rx.xpr]
update_compile_order -fileset sources_1
# The data files the design's IP reads by RELATIVE path at synthesis
# (dvi2rgb's EDID ROM, today): the launch_runs flow copies them beside
# its run. We are the run, so they come here, whichever IP they are.
foreach f [get_files -all -filter {FILE_TYPE == "Data Files"}] {
    file copy -force $f [pwd]
}
# The top is the design's own module; the part is the project's.
synth_design -top $p_top -part [get_property PART [current_project]]
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
