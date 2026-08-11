# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Bicubic demosaic: 4-tap cubic interpolation per colour lattice.

The classic upgrade from bilinear: each missing colour is estimated with
Keys' cubic convolution (a = -1/2) on that colour's own lattice instead of
a 2-tap average. A missing sample sits exactly midway between its
neighbours in lattice units, and the half-phase Keys kernel there is::

    [-1, 9, 9, -1] / 16

Sharper than bilinear -- the negative lobes preserve edges the box average
smears -- but still CHANNEL-INDEPENDENT, so it fringes on high-contrast
detail for the same structural reason bilinear zippers. It earns its place
as the best a pipeline can do without cross-channel correction, and as the
honest measure of what malvar's correction (patent-gated until 2027-01-25)
actually buys.

The arithmetic, exactly as the hardware does it
-----------------------------------------------

One 7x7 window, five tap combinations, the same site table as bilinear::

    centre                              the measured sample
    horiz = ([-1,9,9,-1] . row +-1,+-3) / 16
    vert  = ([-1,9,9,-1] . col +-1,+-3) / 16
    cross = (horiz_sum + vert_sum) / 32     both axes available: average
    diag  = ([-1,9,9,-1] x [-1,9,9,-1] over the odd 4x4) / 256

    site   R out    G out    B out
    R      centre   cross    diag
    Gr     horiz    centre   vert
    Gb     vert     centre   horiz
    B      diag     cross    centre

Every kernel sums to its divisor, so a flat field passes through exactly.
Divisions are FLOOR shifts on a signed accumulator (>>>, matching NumPy's
//), and the result is clipped to the datapath range: the negative lobes
overshoot by up to 1/8 of full scale on a step edge, and the clip is where
that is decided, visibly, rather than by wrap-around.

Borders replicate, as in bilinear: three padded pixels each side (the
kernel reaches +-3), six line buffers in hardware -- window cost is the
price of the sharper kernel, and stating it is the point of having this
block next to bilinear.

Which combination applies where is decided exactly as in bilinear: the CFA
phase arrives as two context bits used as stride-2 slice STARTS, and np2hw
lowers the four planes to one full-rate datapath over one shared window
with the tap combination selected by pixel position.

The stream: three channels, said the NumPy way -- ``np.stack`` -- with the
wire word owned by the stream layer.
"""
from __future__ import annotations

import numpy as np

from revela.blocks import BAYER, RGB, ContextBit, StreamPort, ispblock

# Keys cubic convolution, a = -1/2, evaluated at the half-phase positions
# (+-0.5, +-1.5 lattice units): the one kernel this block needs.
CUBIC = (-1, 9, 9, -1)
OFFSETS = (-3, -1, 1, 3)                 # mosaic-domain reach of the kernel


@ispblock(
    version=(1, 0),
    description="Bicubic demosaic: 7x7 window, Keys half-phase kernel per "
                "colour lattice, phase-selected taps, clipped, RGB out.",
    inputs=(StreamPort("in", BAYER,
                       "Raw Bayer, black-levelled and white-balanced -- "
                       "interpolating unbalanced planes bakes the imbalance "
                       "into all three channels."),),
    outputs=(StreamPort("out", RGB,
                        "Linear RGB; interpolated channels are clipped to "
                        "the datapath range, absorbing the cubic kernel's "
                        "overshoot."),),
    consumes=("bayer_phase",),
    context=(
        ContextBit("phase_row", "bayer_phase", bit=1,
                   description="Row parity R sits on, from the pipeline's "
                               "bayer_phase context register. A register, "
                               "not a build option, so one bitstream serves "
                               "every CFA order"),
        ContextBit("phase_col", "bayer_phase", bit=0,
                   description="Column parity R sits on, from bayer_phase"),
    ),
)
def bicubic(pixel, p, ctx, bit_depth: int):
    """THE model. Five cubic tap combinations, routed by CFA site.

    Written the way the hardware reads the frame: window taps are shifted
    views of the edge-padded frame, the combinations are computed once at
    full rate, and each stride-2 CFA plane picks the combination the site
    table names. ``int32`` holds the widest intermediate (the diagonal
    sum reaches 400x a 16-bit sample); np2hw sizes the real adders from
    the exact range, not from 32.
    """
    height, width = pixel.shape[:2]
    value = pixel.astype(np.int32)
    x = np.pad(value, 3, mode="edge")

    def at(dr, dc):
        return x[3 + dr:3 + dr + height, 3 + dc:3 + dc + width]

    top = (1 << bit_depth) - 1
    centre = at(0, 0)
    h4 = sum(w * at(0, o) for w, o in zip(CUBIC, OFFSETS))
    v4 = sum(w * at(o, 0) for w, o in zip(CUBIC, OFFSETS))
    d16 = sum(wr * wc * at(r, c)
              for wr, r in zip(CUBIC, OFFSETS)
              for wc, c in zip(CUBIC, OFFSETS))
    horiz = (h4 // 16).clip(0, top)
    vert = (v4 // 16).clip(0, top)
    cross = ((h4 + v4) // 32).clip(0, top)
    diag = (d16 // 256).clip(0, top)

    red = np.empty_like(value)
    green = np.empty_like(value)
    blue = np.empty_like(value)
    pr, pc = ctx.phase_row, ctx.phase_col
    # The site table from the docstring -- bilinear's routing, cubic combos.
    sites = (
        ((pr, pc), centre, cross, diag),            # R site
        ((pr, 1 - pc), horiz, centre, vert),        # Gr: R left/right
        ((1 - pr, pc), vert, centre, horiz),        # Gb: R above/below
        ((1 - pr, 1 - pc), diag, cross, centre),    # B site
    )
    for (rows, cols), r_tap, g_tap, b_tap in sites:
        red[rows::2, cols::2] = r_tap[rows::2, cols::2]
        green[rows::2, cols::2] = g_tap[rows::2, cols::2]
        blue[rows::2, cols::2] = b_tap[rows::2, cols::2]

    return np.stack([red, green, blue], axis=-1).astype(np.uint16)
