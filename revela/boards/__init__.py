# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Boards the revela ISP is built onto, one package each.

A board is the harness that tests the ISP on silicon: what carries the
stream to it, what shows the picture, and the block design joining them.
The ISP itself is not described here -- a board names the pipeline it
builds and reads that pipeline's published contract, never restating it.

Three layers, each a library of JSON blocks:

  zynq7/        the family: vendor IP, the processing system, the bus --
                what any Zynq-7000 board builds from
  <board>/      a board: its jacks (composites owning their cells, pins
                and constraints), its glue modules, and the designs built
                from them, e.g. pynq_z2.pynq_demo
  (receiver)    bayerlink_fpga's modules, the receiver's Verilog

A design is JSON through and through; np2hw loads it, checks its wires
and renders it for Vivado (np2hw.toolchain.vivado).
"""
