# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Bilinear demosaic: separable linear interpolation per colour plane.

IP: patent-checked 2026-08-11 -- nothing to clear: interpolation by
neighbour averaging is the field's foundational prior art (it appears
in Bayer's own US3971065, filed 1975, expired decades ago) and no
in-force claim covers it.

The baseline algorithm, and the honest floor: it is what "just interpolate"
looks like. Each missing colour at a pixel is the average of the nearest
measured samples of that colour, each channel interpolated independently.
Its weakness is structural, not a tuning problem -- ignoring inter-channel
correlation zippers on any edge that is not axis-aligned -- and having it in
the tree makes the case for the better algorithms concrete rather than
asserted.

The arithmetic, exactly as the hardware does it
-----------------------------------------------

One 3x3 window, five tap combinations, all divisors powers of two::

    centre                       the measured sample, passed through
    cross  = (N + S + W + E) / 4     4 orthogonal neighbours
    diag   = (NW + NE + SW + SE) / 4 4 diagonal neighbours
    horiz  = (W + E) / 2             2 horizontal neighbours
    vert   = (N + S) / 2             2 vertical neighbours

Every division is a shift and the inputs are unsigned, so there is no
rounding decision to get wrong and no clip: an average of in-range samples
is in range. Which combination feeds which output channel depends on WHICH
CFA COLOUR THE PIXEL SITE MEASURES::

    site   R out    G out    B out
    R      centre   cross    diag
    Gr     horiz    centre   vert
    Gb     vert     centre   horiz
    B      diag     cross    centre

(Gr is the green pixel in R's row: its red neighbours sit left and right,
its blue neighbours above and below. Gb is the mirror.)

Borders replicate (``np.pad`` edge): a zero border would be demosaiced as a
coloured fringe on every frame edge, whereas a replicated border errs
toward the neighbouring colour, which is what every shipping ISP does.

Which combination applies where
-------------------------------

The site table is indexed by CFA position, and the CFA phase arrives as two
one-bit context values -- which row parity R sits on, and which column
parity -- used as the STARTS of stride-2 slices, exactly as in blacklevel.
The starts are registers, so one bitstream serves every CFA order; the
tracer lowers the four disjoint planes to ONE full-rate datapath whose tap
combination is selected by pixel position.

The stream, three channels
--------------------------

First block whose output is not the input's shape of data: Bayer in (one
measured value per pixel), RGB out. The model says so the NumPy way --
``np.stack([r, g, b], axis=-1)``, an ``(h, w, 3)`` frame -- and how those
channels share one data word on the wire is the stream's business alone
(:meth:`revela.stream.StreamSpec.pack`, channel 0 in the low bits). The
generated hardware emits that word; no model packs channels by hand.

How this is built
-----------------

Entirely by tracing :func:`bilinear` with np2hw: the window (two line
buffers), the five tap sums, the position mux and the packing all fall out
of the model. No hand-written Verilog, no per-block generator.
"""
from __future__ import annotations

import numpy as np

from revela.blocks import ContextBit, StreamPort, ispblock


@ispblock(
    version=(1, 0),
    description="Bilinear demosaic: 3x3 window, per-plane linear "
                "interpolation, phase-selected taps, packed RGB out.",
    inputs=(StreamPort("in",
                       "Raw Bayer, black-levelled and white-balanced -- "
                       "interpolating unbalanced planes bakes the imbalance "
                       "into all three channels."),),
    outputs=(StreamPort("out",
                        "Packed linear RGB, R in the low bits; interpolated "
                        "channels carry no headroom beyond the input's "
                        "range."),),
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
def bilinear(pixel, p, ctx, bit_depth: int):
    """THE model. Five tap combinations, routed to channels by CFA site.

    Written the way the hardware reads the frame: the window taps are
    shifted views of the edge-padded frame, the tap combinations are
    computed once at full rate, and each stride-2 CFA plane picks the
    combination the site table names. The slice starts are the phase
    context values, so this is ordinary NumPy when run and the position
    mux when traced -- one function, both roles.

    ``uint32`` holds the widest intermediate (a sum of four 16-bit
    samples); np2hw sizes the real adders from the range, not from 32.
    """
    value = pixel.astype(np.uint32)
    x = np.pad(value, 1, mode="edge")
    centre = x[1:-1, 1:-1]
    north = x[:-2, 1:-1]
    south = x[2:, 1:-1]
    west = x[1:-1, :-2]
    east = x[1:-1, 2:]
    cross = (north + south + west + east) // 4
    diag = (x[:-2, :-2] + x[:-2, 2:] + x[2:, :-2] + x[2:, 2:]) // 4
    horiz = (west + east) // 2
    vert = (north + south) // 2

    red = np.empty_like(value)
    green = np.empty_like(value)
    blue = np.empty_like(value)
    pr, pc = ctx.phase_row, ctx.phase_col
    # The site table from the docstring, one row per CFA position.
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

    # Three channels, said the NumPy way. How they share one data word is
    # the STREAM's business (StreamSpec.pack, channel 0 in the low bits),
    # stated there once -- a model never shift-and-masks channels together.
    return np.stack([red, green, blue], axis=-1).astype(np.uint16)
