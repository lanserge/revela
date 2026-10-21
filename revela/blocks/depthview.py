# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Depth view: millimetres to a colour a display shows, red near, blue far.

A depth map is not a picture. This makes one a television can show and a
person can read at a glance: distance as a hue, along a ramp from red
through green to blue, two straight segments blended by the position's low
bits -- no table, no multiplier.

The arithmetic, exactly as the hardware does it
-----------------------------------------------

    v  = min(depth >> shift, 511)          the ramp's position, 9 bits
    a  = v & 255                           the blend inside a segment
    v < 256:   R = 255 - a,  G = a,        B = 0      red to green
    v >= 256:  R = 0,        G = 255 - a,  B = a      green to blue

``shift`` sets the range the ramp spans: 512 << shift millimetres, so 3 is
4.1 m at 8 mm a step, 2 is 2 m, 1 is 1 m. A power of two, so a shift and
not a divide. ``255 - a`` is ``a`` with its bits inverted. The ramp is
continuous where its segments meet -- pure green either side of 256 -- and
R + G + B is 255 everywhere along it, so no part of the range reads
brighter than another. Anything past the range is blue.

A depth of 0 is ``tofrange``'s "no measurement" -- the amplitude below its
threshold -- and shows black, so what the sensor could not see is not
painted as near.

The output is three 8-bit channels, R low, G mid, B high: a display's word.
"""
from __future__ import annotations

import numpy as np

from np2hw.blocks import StreamPort
from np2hw.blocks.params import Param
from revela.blocks import ispblock

#: Stages of the shifter: shifts up to ``2**SHIFT_STAGES - 1`` places.
SHIFT_STAGES = 3

#: The ramp's last position: two segments of 256.
TOP = 511


@ispblock(
    version=(1, 0),
    description="Depth in millimetres to a red-green-blue ramp, red near.",
    inputs=(StreamPort("in",
                       "Two channels from tofrange: [0] depth in "
                       "millimetres, 0 where nothing was measured; [1] the "
                       "amplitude, not read here."),),
    outputs=(StreamPort("out",
                        "Three 8-bit channels, R low, G mid, B high: red "
                        "near, through green, to blue far; black where "
                        "nothing was measured."),),
    params=[
        Param(name="shift", bits=SHIFT_STAGES, default=3,
              description="The range the ramp spans: 512 << shift "
                          "millimetres, red at 0 and blue at its end -- 3 "
                          "is 4.1 m, 2 is 2 m, 1 is 1 m"),
    ],
)
def depthview(pixel, p, bit_depth: int):
    """THE model: the ramp's position, its segment, the blend."""
    depth = pixel[..., 0].astype(np.int64)
    v = _shift_right(depth, p.shift)
    v = np.where(v > TOP, v * 0 + TOP, v)
    a = v & 255
    far = v > 255
    zero = a * 0
    red = np.where(far, zero, 255 - a)
    green = np.where(far, 255 - a, a)
    blue = np.where(far, a, zero)
    seen = depth > 0
    return np.stack([np.where(seen, red, zero),
                     np.where(seen, green, zero),
                     np.where(seen, blue, zero)], axis=-1).astype(np.uint8)


def _shift_right(v, amount):
    """``v >> amount`` for a register amount: a log shifter of fixed shifts."""
    for stage in range(SHIFT_STAGES):
        v = np.where(((amount >> stage) & 1) > 0, v >> (1 << stage), v)
    return v
