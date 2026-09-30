# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Boards the revela ISP is built onto, one package each.

A board is the harness that tests the ISP on silicon: what carries the
stream to it, what shows the picture, and the block design joining them.
The ISP itself is not described here -- a board names the pipeline it
builds and reads that pipeline's published contract, never restating it.

A board folder is a library: its primitives -- one JSON block per jack
or resource, each carrying its own facts and constraints fragment -- and
the designs built from them. A design names its primitives by reference,
`revela.boards.<board>.<file>:<block>`, so a second design on the same
board reuses them and restates nothing.

The checks beside this file hold for any Vivado block design: checkbd
reads the script before Vivado runs, checknets the widths of every wire
drawn between our own blocks, checkhwh the design as built.
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
