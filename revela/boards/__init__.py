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

A design is JSON through and through; vivado.py renders it as the block
design Vivado builds, checking every wire between our own modules against
their RTL (rtl.py) first. bdtrace reads such a script back without Vivado,
and checkhwh audits the design as built.
"""
from __future__ import annotations

from pathlib import Path


def resolve(ref: str) -> tuple[dict, Path]:
    """A JSON block by reference, `package.module:block`, and the folder it
    lives in (where the files it names are). The one resolver is the
    composition loader's; this is it, for a primitive."""
    from revela.designs.composite import find
    found = find(ref)
    if found.kind not in ("primitive", "composite"):
        raise ValueError(f"{ref} is a {found.kind}, not a JSON block")
    return found.obj, found.folder
