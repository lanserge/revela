# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Designs: pipelines described in JSON, as composites of blocks.

A design is a NETLIST: named block instances, and the connections between
their ports. It is an **input** -- written by hand today, and by a pipeline
builder later, since a graph of named ports is exactly what a GUI edits --
and it is a composite like any other block, so designs are built of designs:
see :mod:`revela.designs.composite`, which loads them, and
:mod:`revela.designs.profile`, which holds a sensor's values for one.

    from revela import designs
    pipeline = designs.load("revela.pipelines.stereo:revela_stereo")
    open("build/isp.v", "w").write(pipeline.generate().verilog)

A netlist rather than a list, because a real ISP is not a chain. Statistics TAP
the datapath and produce no pixels; a preview path FORKS off the main one; luma
and chroma SPLIT and only luma is sharpened; HDR MERGES two exposures. Forcing
that into an ordered list puts blocks in the datapath that are only watching
it.

Two kinds of JSON, pointing opposite ways
-----------------------------------------

    design         INPUT    structure, no addresses    (this package)
    register map   OUTPUT   addresses, generated       (revela.compose)

Keeping them apart is not tidiness. A design **cannot express an address**:
blocks declare local offsets, and revela assigns each block instance a base at
composition time. If a design could pin an address, a builder GUI could produce
a map that silently disagreed with the hardware it generated -- which is the
exact failure rule 2 exists to prevent. Addresses are allocated, never
declared.

Nor does a design name a sensor. What an ISP is built for is the stream
entering it -- a depth, a widest and a tallest frame -- stated on its input
port; geometry and Bayer phase ride the stream, so one bitstream serves every
sensor whose mode fits.
"""
from __future__ import annotations

import re
from pathlib import Path


def is_reference(text) -> bool:
    """Whether ``text`` is the shape of a reference,
    ``package.module[:block]``, and names no file."""
    return not Path(str(text)).exists() and re.fullmatch(
        r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+(:[A-Za-z_]\w*)?", str(text)) is not None


def load(ref: str):
    """Build a :class:`revela.compose.Pipeline` from the composite ``ref``
    (``package.module:block``)."""
    from revela.designs import composite
    if not is_reference(ref):
        raise ValueError(
            f"{ref!r} is not a design reference: a design is a composite, "
            "named package.module:block (a library folder goes on the import "
            "path with --library)")
    return composite.to_pipeline(composite.load(str(ref)))
