# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
# Fresh heap: open the placed checkpoint, route, write the bitstream.
if {[llength $argv] != 1} {
    error "impl_b.tcl: give the build directory: -tclargs <build dir>"
}
set out [file normalize [lindex $argv 0]]
# Threaded route is the last suspect standing: the crash survives
# allocator swaps and fresh processes, but always follows the one
# heavily threaded command. One thread, one truth.
set_param general.maxThreads 1
open_checkpoint [file join $out build placed.dcp]
route_design
# Post-route physical optimization: the last tens of picoseconds are
# placement's to give back, not the RTL's to chase. Escalate until the
# slack is met or the directives run out.
foreach directive {Default AggressiveExplore AggressiveFanoutOpt} {
    if {[get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]] >= 0} {
        break
    }
    phys_opt_design -directive $directive
}
puts "WNS: [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]"
# When timing fails, the path IS the diagnosis: print the worst three.
report_timing -max_paths 3 -nworst 1 -setup
file mkdir [file join $out out]
# How much of the part this design uses, which this build reported for
# its whole history only as "it fit". Whether it fits is not the
# question a port asks -- how much ROOM is left, and how much of what
# is used belongs to the design rather than to this board, are. So the
# hierarchical report goes beside the flat one: on another part the PS,
# the DMAs and the interconnect are gone, and what has to fit is the
# receiver and the ISP. A number nobody records is a number nobody can
# plan against.
report_utilization -file [file join $out out utilization.rpt]
report_utilization -hierarchical -hierarchical_depth 3 \
    -file [file join $out out utilization-hier.rpt]
puts "UTILIZATION: [file join $out out utilization.rpt] (+ -hier)"
file mkdir /tmp/rxout
write_bitstream -force /tmp/rxout/rx.bit
file copy -force /tmp/rxout/rx.bit [file join $out out rx.bit]
file copy -force [glob [file join $out build rx *.gen sources_1 bd rx hw_handoff rx.hwh]] \
    [file join $out out rx.hwh]
puts "BITSTREAM_DONE"
