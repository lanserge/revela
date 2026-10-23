# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Depth view: millimetres to a rainbow a display shows, red near, violet far.

A depth map is not a picture. This makes one a television can show and a
person can read at a glance: distance as a smooth rainbow across a range,
red at its near end through orange, yellow, green, cyan and blue to violet
at its far end, the way depth viewers commonly show it.

The arithmetic, exactly as the hardware does it
-----------------------------------------------

    d    = max(depth - start, 0)
    k    = FRAC_BITS + shift                          bits below the segment
    seg  = min(d >> k, SEGMENTS - 1)                  which two key colours
    frac = d - (seg << k)                             every bit below them
    rgb  = near + ((far - near) * frac >> k),  near, far = TABLE[seg]

``start`` is where the range begins and ``shift`` where the segment
boundary falls in the depth: the range is SEGMENTS segments of
``1 << k`` millimetres, so 0 is 4.095 m, the reference module's whole
window in twelve bits, and 1 is 8.2 m for a sensor that reaches farther.
Every bit below the segment feeds the blend -- none is dropped before the
multiply, whatever the range -- and only the blended colour is rounded, to
the eight bits of a display's channel. A power of two, so shifts and not a
divide. The high bits pick a pair of key colours from a table of KNOTS and
the rest blend between them -- the same knots-and-interpolation shape as
:mod:`revela.blocks.tofrange`'s curve -- so the rainbow is continuous, and
the table is eight words, each holding a segment's near and far colours so
one read gives both. Nearer than the range is its first colour, farther
the end of its last.

A depth of 0 is ``tofrange``'s "no measurement" -- the amplitude below its
threshold -- and shows black, so what the sensor could not see is not
painted as anything.

The output is three 8-bit channels, R low, G mid, B high: a display's word.
"""
from __future__ import annotations

import numpy as np

from np2hw import Rom
from np2hw.blocks import StreamPort
from np2hw.blocks.params import Param
from revela.blocks import ispblock

#: Segments of the rainbow, and the bits of the depth below the segment at
#: ``shift`` 0: eight of 512 mm make the reference module's whole window,
#: 0 to 4.095 m, its twelve bits the segment's three and the blend's nine.
SEGMENTS = 8
FRAC_BITS = 9

#: Stages of the shifter: shifts up to ``2**SHIFT_STAGES - 1`` places.
SHIFT_STAGES = 3

#: The key colours, near to far: red, orange, yellow, yellow-green, green,
#: cyan, sky blue, blue, violet -- one more than the segments between them.
#: R, G, B each, BAKED as integer literals so the model holds no float.
KNOTS_RGB = [
    (220, 20, 20),
    (240, 120, 20),
    (240, 225, 30),
    (150, 240, 30),
    (30, 220, 60),
    (30, 215, 215),
    (30, 130, 240),
    (40, 40, 230),
    (130, 30, 200),
]
assert len(KNOTS_RGB) == SEGMENTS + 1

#: Each segment's two ends in one word: its near colour low, its far colour
#: above it, each R low, G mid, B high -- one read a pixel gives both. Read
#: at a traced index the table is a memory, run in NumPy an array lookup --
#: one object, both roles.
_WORDS = [r | g << 8 | b << 16 for r, g, b in KNOTS_RGB]
TABLE = Rom([near | far << 24 for near, far in zip(_WORDS, _WORDS[1:])],
            name="depthview_segments")


@ispblock(
    version=(4, 0),
    description="Depth in millimetres to a smooth rainbow, red near.",
    inputs=(StreamPort("in",
                       "Two channels from tofrange: [0] depth in "
                       "millimetres, 0 where nothing was measured; [1] the "
                       "amplitude, not read here."),),
    outputs=(StreamPort("out",
                        "Three 8-bit channels, R low, G mid, B high: red "
                        "near through the rainbow to violet far; black "
                        "where nothing was measured."),),
    params=[
        Param(name="start", bits=16, default=0,
              description="Millimetres where the range begins: nearer is "
                          "its first colour, red"),
        Param(name="shift", bits=SHIFT_STAGES, default=0,
              description="Where the segment boundary falls in the depth, "
                          "so how wide the range is: 8 segments of "
                          "512 << shift millimetres -- 0 is 4.095 m, 1 is "
                          "8.2 m; every bit below a segment blends"),
    ],
)
def depthview(pixel, p, bit_depth: int):
    """THE model: the segment, its two key colours, every bit below it
    blending them."""
    depth = pixel[..., 0].astype(np.int64)
    zero = depth * 0
    d = depth - p.start
    d = np.where(d > 0, d, zero)
    seg = _shift_right(d >> FRAC_BITS, p.shift)
    past = seg > SEGMENTS - 1
    seg = np.where(past, zero + (SEGMENTS - 1), seg)
    # every bit below the segment; past the range, the last segment's end
    span = _shift_left(zero + (1 << FRAC_BITS), p.shift)
    frac = np.where(past, span - 1, d - _shift_left(seg << FRAC_BITS, p.shift))
    # The mask changes no value that can occur, but a ROM index must be
    # provably in its table, and range analysis does not narrow through
    # the clamp above.
    ends = TABLE[seg & (SEGMENTS - 1)]
    channels = []
    for lane in range(3):
        lo = (ends >> (8 * lane)) & 255
        hi = (ends >> (24 + 8 * lane)) & 255
        blend = _shift_right(((hi - lo) * frac) >> FRAC_BITS, p.shift)
        # Between its two ends a blend never leaves 0..255, but range
        # analysis cannot see that through the product, and a cast it
        # cannot prove would be a truncation: the mask changes no value.
        channels.append((lo + blend) & 255)
    seen = depth > 0
    return np.stack([np.where(seen, c, zero) for c in channels],
                    axis=-1).astype(np.uint8)


def _shift_left(v, amount):
    """``v << amount`` for a register amount: a log shifter of fixed shifts."""
    for stage in range(SHIFT_STAGES):
        v = np.where(((amount >> stage) & 1) > 0, v << (1 << stage), v)
    return v


def _shift_right(v, amount):
    """``v >> amount`` for a register amount: a log shifter of fixed shifts."""
    for stage in range(SHIFT_STAGES):
        v = np.where(((amount >> stage) & 1) > 0, v >> (1 << stage), v)
    return v
