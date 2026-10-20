# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Mark: composite a small bitmap into the corner of the picture.

A watermark is the simplest member of a family that ends in an on-screen
display, and it is worth building for what it proves rather than for what
it shows: a pixel that knows WHERE it is, and a table too large to be
registers. Everything an OSD adds on top -- a character map feeding a font
ROM, per-element positions, alpha blending -- is this block with one more
indirection.

The arithmetic, exactly as the hardware does it
-----------------------------------------------

    dy, dx = y - y0, x - x0           # y0, x0: the placement registers
    inside = (0 <= dy < SIZE) and (0 <= dx < SIZE)
    entry  = shape[(dy & (SIZE-1)) * SIZE + (dx & (SIZE-1))]
    out    = ink[entry] if (inside and entry != 0) else pixel

The window test is four comparisons; the address is a shift and an OR,
because SIZE is a power of two -- that is the whole reason the mark is
128 wide rather than a rounder-looking 160. Outside the window the table
is still read (hardware reads it every pixel, whatever the model wants),
so the index is MASKED into range rather than clipped: the model must
agree with the hardware about what is read where it does not matter.

Where the picture comes from
----------------------------

``assets/revela_mark.npz``, derived from the vector artwork by
``docs/logo/prep.py``: an index per pixel plus a small palette. Indexed
storage is not a cleverness, it is the difference between 48 kbit and 393
-- five design colours and two antialiasing blends fit in three bits, so
the smoothing is free. Entry 0 is transparent.

The palette is a ROM here because this build bakes everything. It WANTS to
be a register array -- seven entries is nothing, and then the mark can be
dimmed, tinted or blanked live -- and that is one line's change when the
register file lands. The shape stays a ROM: it is 16384 entries, and a
register per pixel is what this block exists to avoid.

Position
--------

A register pair, ``x0`` and ``y0``: the mark's top-left corner. Where it
goes is a choice -- the corner, a margin, out of the way of something --
and the frame is a run-time fact, so placement is set at run time, by
whatever sets the mode: the firmware puts it in the frame's corner as it
applies one. The corner rule itself is here, once: ``corner(extent)``, the
mark's offset in a frame of that extent, which a design calls for its
reset values (the build's own frame) and the firmware's map carries as
``INSET`` for the frame it is given.

Ordering
--------

LAST. This block writes display values, not scene values: it must land
after the tone curve, or the curve would gamma-encode the logo's own
colours and the mark would come out wrong on purpose.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from np2hw import Rom, coords

from np2hw.blocks import StreamPort
from np2hw.blocks.params import Param
from revela.blocks import ispblock

SIZE = 128                     # power of two: the address is wiring
MARGIN = 32
INSET = SIZE + MARGIN          # the mark's corner, from the frame's


def corner(extent: int) -> int:
    """Where the mark starts along a frame `extent` long: in from its end
    by the mark and its margin."""
    return int(extent) - INSET


corner.expression = True

_ASSET = np.load(Path(__file__).with_name("assets") / "revela_mark.npz")
_INDEX = _ASSET["index"].astype(np.int64)
_PALETTE = _ASSET["palette"].astype(np.int64)      # (entries, 3), row 0 unused

SHAPE = Rom(_INDEX.ravel(), name="mark_shape")

_INK: dict[int, tuple] = {}


def _ink(bit_depth: int) -> tuple:
    """The palette in DATAPATH units, one table per channel.

    The artwork is 8-bit sRGB and the datapath is whatever the stream
    declares, so the scaling happens HERE, in the constants, where it
    costs nothing -- a shift in the pixel path would be hardware bought
    to move numbers a build already knows.
    """
    if bit_depth not in _INK:
        shift = bit_depth - 8
        scaled = (_PALETTE << shift) if shift >= 0 else (_PALETTE >> -shift)
        _INK[bit_depth] = tuple(
            Rom(scaled[:, k], name=f"mark_ink{k}") for k in range(3))
    return _INK[bit_depth]


@ispblock(
    version=(1, 1),
    description="Composite the project's mark into the corner of the frame.",
    inputs=(StreamPort("in",
                       "Display-encoded RGB -- after the tone curve, because "
                       "the mark's colours are display values already."),),
    outputs=(StreamPort("out", "The same RGB, with the mark composited."),),
    params=[
        Param(name="x0", bits=16, default=0,
              description="The mark's left edge, in pixels from the line "
                          "start: the corner is corner(width)"),
        Param(name="y0", bits=16, default=0,
              description="The mark's top edge, in lines from the frame "
                          "start: the corner is corner(height)"),
    ],
)
def logo(pixel, p, bit_depth: int):
    """THE model. Position, one table read, one palette read, a select --
    the position from its registers.

    Plain NumPy both ways: on arrays ``coords`` is ``np.indices`` and the
    table reads are fancy indexing; traced, the coordinates become the
    counters every core already keeps and the tables become memories read
    through a register.
    """
    y, x = coords(pixel)
    # the pixel's place in the mark: inside when both offsets are in
    # 0..SIZE-1, and their low bits address the table
    dy, dx = y - p.y0, x - p.x0
    inside = (dy >= 0) & (dy < SIZE) & (dx >= 0) & (dx < SIZE)
    entry = SHAPE[(dy & (SIZE - 1)) * SIZE + (dx & (SIZE - 1))]
    paint = inside & (entry > 0)

    ink = _ink(bit_depth)
    lanes = []
    for k in range(3):
        lanes.append(np.where(paint, ink[k][entry],
                              pixel[..., k].astype(np.int32)))
    return np.stack(lanes, axis=-1).astype(np.uint16)
