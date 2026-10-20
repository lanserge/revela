# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Crop: keep the part of the frame its registers place.

    out = pixel[y0:, x0:][:h, :w]

NumPy's own slice, and the hardware is that slice: the frame streams
through and only the beats inside the window go on, the window's lines
and frame ending at its edge -- or at the frame's, where the frame runs
out first, exactly as the slice does. Nothing is stored; a crop costs
four comparisons.

The window is run-time, four registers, because what to keep is a fact
about where the picture goes, not about the sensor: a display shows the
middle of a frame bigger than itself, and whatever sets the display mode
sets the window. The size the block hands on is the part of the window
the frame holds, so every block after it is sized by the crop, not by
the sensor.

At reset the window is the whole of any frame: from the origin, and as
long as a 16-bit extent can be -- the frame's own edges end it.
"""
from __future__ import annotations

from np2hw.blocks import StreamPort
from np2hw.blocks.params import Param
from revela.blocks import ispblock

WHOLE = (1 << 16) - 1          # a length no frame reaches: the frame ends it


@ispblock(
    version=(1, 0),
    description="Keep the window of the frame its registers place.",
    inputs=(StreamPort("in", "Any stream: the window keeps whole words."),),
    outputs=(StreamPort("out",
                        "The window's pixels, its lines and frame ending at "
                        "its edges or the frame's, whichever comes first."),),
    params=[
        Param(name="y0", bits=16, default=0,
              description="The window's top row, in lines from the frame start"),
        Param(name="x0", bits=16, default=0,
              description="The window's left column, in pixels from the line start"),
        Param(name="h", bits=16, default=WHOLE,
              description="The window's height in lines: at most this many "
                          "are kept, fewer where the frame ends first"),
        Param(name="w", bits=16, default=WHOLE,
              description="The window's width in pixels: at most this many "
                          "are kept, fewer where the line ends first"),
    ],
)
def crop(pixel, p, bit_depth: int):
    """THE model: the slice."""
    return pixel[p.y0:, p.x0:][:p.h, :p.w]
