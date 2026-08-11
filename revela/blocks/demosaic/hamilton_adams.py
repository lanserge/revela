# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Hamilton-Adams demosaic: direction-adaptive, in two streaming stages.

IP: the method is Kodak's, from US5629734 (expired 2015) and US5652621
(expired 2016) -- verified on the register 2026-08-11. Implemented from the
patents' published method, clean-room, like everything here.

The first ADAPTIVE demosaic in the tree, and the reason the compiler grew
window expressions: at each missing-green site the horizontal and vertical
gradients (with Laplacian terms from the measured channel) are compared,
and the interpolation follows the direction with LESS activity -- edges are
interpolated along themselves, not across. That single decision removes
most of the zippering the channel-independent algorithms cannot avoid.

Two stages, one stream between them
-----------------------------------

    ha_green   Bayer in -> (raw, green) out. Green reconstructed with the
               adaptive rule; the raw sample rides along as the second
               channel of the word. 5x5 window, four line buffers.

    ha_rb      (raw, green) in -> RGB out. Red and blue interpolated as
               COLOUR DIFFERENCES against the reconstructed green -- the
               classic chroma-smoothness argument -- over a 3x3 window,
               two line buffers on the two-channel word.

The split is the streaming truth of the algorithm: red/blue need green
values at neighbouring sites, which exist only after the green pass. One
block would mean buffering the green reconstruction inside it anyway; two
blocks make the intermediate stream a first-class, separately verified
citizen (domain ``bayer+g``).

The arithmetic, exactly as the hardware does it (ha_green)
----------------------------------------------------------

At a site whose measured sample is X (R or B), with G neighbours at +-1
and X neighbours at +-2 in each axis::

    lh = 2X - X_ww - X_ee            lv = 2X - X_nn - X_ss
    dh = |G_w - G_e| + |lh|          dv = |G_n - G_s| + |lv|

    dh < dv:   G' = (G_w + G_e)/2 + lh/4
    dv < dh:   G' = (G_n + G_s)/2 + lv/4
    tie:       G' = (G_w+G_e+G_n+G_s)/4 + (lh+lv)/8

Divisions are FLOOR shifts on signed values; the estimate clips to the
datapath range (the Laplacian correction can overshoot either way). At
green sites the measured sample passes through untouched.

ha_rb: with D = raw - green at the sites where raw is the wanted colour,
the missing colour is green + mean(D) over 2 row/column neighbours (at
green sites) or 4 diagonals (at the opposite colour's site), floored and
clipped. Green passes through.

Both stages take the CFA phase as context bits, exactly like every
CFA-indexed block: one bitstream serves every order, and np2hw lowers each
stage to one shared window with per-site selection -- the adaptive select
is a comparator and a mux inside the plane, not a second datapath.
"""
from __future__ import annotations

import numpy as np

from revela.blocks import ContextBit, StreamPort, ispblock

_PHASE_CONTEXT = (
    ContextBit("phase_row", "bayer_phase", bit=1,
               description="Row parity R sits on, from the pipeline's "
                           "bayer_phase context register. A register, not "
                           "a build option, so one bitstream serves every "
                           "CFA order"),
    ContextBit("phase_col", "bayer_phase", bit=0,
               description="Column parity R sits on, from bayer_phase"),
)


@ispblock(
    version=(1, 0),
    description="Hamilton-Adams green reconstruction: direction-adaptive, "
                "Laplacian-corrected; emits (raw, green).",
    inputs=(StreamPort("in",
                       "Raw Bayer, black-levelled and white-balanced."),),
    outputs=(StreamPort("out",
                        "The raw sample and the reconstructed green, one "
                        "word: what the chroma stage needs and nothing "
                        "it does not."),),
    consumes=("bayer_phase",),
    context=_PHASE_CONTEXT,
)
def ha_green(pixel, p, ctx, bit_depth: int):
    """THE model. Adaptive estimate at non-green sites, passthrough at green."""
    height, width = pixel.shape[:2]
    top = (1 << bit_depth) - 1
    value = pixel.astype(np.int32)
    x = np.pad(value, 2, mode="edge")

    def at(r, c):
        return x[2 + r:2 + r + height, 2 + c:2 + c + width]

    centre = at(0, 0)
    gw, ge, gn, gs = at(0, -1), at(0, 1), at(-1, 0), at(1, 0)
    lh = 2 * centre - at(0, -2) - at(0, 2)
    lv = 2 * centre - at(-2, 0) - at(2, 0)
    dh = np.abs(gw - ge) + np.abs(lh)
    dv = np.abs(gn - gs) + np.abs(lv)
    gh = ((gw + ge) // 2 + lh // 4).clip(0, top)
    gv = ((gn + gs) // 2 + lv // 4).clip(0, top)
    ga = ((gw + ge + gn + gs) // 4 + (lh + lv) // 8).clip(0, top)
    est = np.where(dh < dv, gh, np.where(dv < dh, gv, ga))

    green = np.empty_like(value)
    pr, pc = ctx.phase_row, ctx.phase_col
    green[pr::2, pc::2] = est[pr::2, pc::2]                # R site
    green[pr::2, 1 - pc::2] = centre[pr::2, 1 - pc::2]     # Gr measured
    green[1 - pr::2, pc::2] = centre[1 - pr::2, pc::2]     # Gb measured
    green[1 - pr::2, 1 - pc::2] = est[1 - pr::2, 1 - pc::2]  # B site
    return np.stack([centre, green], axis=-1).astype(np.uint16)


@ispblock(
    version=(1, 0),
    description="Hamilton-Adams chroma: red and blue as colour differences "
                "against the reconstructed green.",
    inputs=(StreamPort("in",
                       "The green stage's word: raw sample plus "
                       "reconstructed green."),),
    outputs=(StreamPort("out",
                        "Linear RGB; interpolated channels clipped to the "
                        "datapath range."),),
    consumes=("bayer_phase",),
    context=_PHASE_CONTEXT,
)
def ha_rb(pixel, p, ctx, bit_depth: int):
    """THE model. D = raw - green, interpolated per site, added back to green."""
    height, width = pixel.shape[:2]
    top = (1 << bit_depth) - 1
    raw = np.pad(pixel[..., 0].astype(np.int32), 1, mode="edge")
    grn = np.pad(pixel[..., 1].astype(np.int32), 1, mode="edge")

    def R(r, c):
        return raw[1 + r:1 + r + height, 1 + c:1 + c + width]

    def G(r, c):
        return grn[1 + r:1 + r + height, 1 + c:1 + c + width]

    def d(r, c):
        return R(r, c) - G(r, c)

    gc = G(0, 0)
    cent = R(0, 0)
    horiz = (gc + (d(0, -1) + d(0, 1)) // 2).clip(0, top)
    vert = (gc + (d(-1, 0) + d(1, 0)) // 2).clip(0, top)
    diag = (gc + (d(-1, -1) + d(-1, 1) + d(1, -1) + d(1, 1)) // 4).clip(0, top)

    red = np.empty_like(cent)
    blue = np.empty_like(cent)
    pr, pc = ctx.phase_row, ctx.phase_col
    sites = (((pr, pc), cent, diag),           # R site
             ((pr, 1 - pc), horiz, vert),      # Gr: R left/right, B above/below
             ((1 - pr, pc), vert, horiz),      # Gb: mirrored
             ((1 - pr, 1 - pc), diag, cent))   # B site
    for (rows, cols), r_tap, b_tap in sites:
        red[rows::2, cols::2] = r_tap[rows::2, cols::2]
        blue[rows::2, cols::2] = b_tap[rows::2, cols::2]
    return np.stack([red, gc, blue], axis=-1).astype(np.uint16)
