# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""White balance: equalise the CFA colours so neutral scene content is grey.

An illuminant is not white. Tungsten is heavily red, overcast daylight is
blue, and the sensor's channels do not respond equally in any case. This
block applies a per-CFA-colour gain so that a grey card reads equal in R, G
and B -- in the Bayer domain, BEFORE demosaic, because interpolating across
channels that are still mismatched paints coloured fringes on every edge.

The arithmetic, exactly as the hardware does it
-----------------------------------------------

    out = clip((pixel * gain[colour]) >> 8, 0, 2**bit_depth - 1)

``gain`` is unsigned Q8.8: 256 is unity, and the truncating shift is the
hardware's rounding -- a floor, not round-to-nearest, because it is one wire
selection instead of an adder, and its worst-case bias of one LSB sits far
below the noise the AWB loop is already integrating over. The loop corrects
the average; the datapath stays minimal. Saturation is to full scale: a gain
above unity pushes bright pixels past the top, and clipping to white is the
correct behaviour -- a highlight balanced BEFORE it clips stays neutral,
which is part of why this block sits where it does.

Which gain applies to which pixel
---------------------------------

``gain`` is indexed BY COLOUR, exactly as blacklevel's offsets are::

    gain[0, 0] = R      gain[0, 1] = Gr
    gain[1, 0] = Gb     gain[1, 1] = B

The CFA phase arrives as the same two context bits, used as stride-2 slice
starts, so ONE bitstream serves every sensor orientation. Gr and Gb are
separate registers deliberately: green imbalance is real on real sensors,
and a single G gain would leave it uncorrectable. Convention holds green at
unity and scales R and B relative to it, which keeps the signal from
clipping earlier than it must; :func:`registers_from_gains` encodes that
convention once.

Where the values come from
--------------------------

The AWB loop in :mod:`revela.control.awb`, at frame rate, from the statistics
block's per-zone colour sums -- gains are a property of the SCENE and its
illuminant. There is deliberately no ``sensor_values`` hook here: a sensor
description cannot know what light the camera is standing in, and a hook
returning a plausible default would be a guess wearing provenance.

How this is built
-----------------

Entirely by tracing :func:`whitebalance` with np2hw, like blacklevel: the
four disjoint phase planes lower to ONE full-rate datapath -- a multiplier,
a shift and a clip, with the coefficient selected by pixel position.
"""
from __future__ import annotations

import numpy as np

from revela.blocks import BAYER, ContextBit, StreamPort, ispblock
from revela.params import Param

# The gain's Q format lives ON THE DECLARATION below, nowhere else: the
# model reads its shift from p.decl.gain.frac, the register map derives
# q_format from the same Param, and a design may override frac/bits per
# instance. Q8.8 is the DEFAULT because it matches the control loop's
# arithmetic; a 16-bit pipeline wanting Q4.12 overrides it in its design
# JSON rather than copying this file.


@ispblock(
    version=(1, 0),
    description="Per-CFA-colour white balance gain, Q8.8, with saturation.",
    inputs=(StreamPort("in", BAYER,
                       "Raw Bayer, black level already removed -- a gain "
                       "applied on top of a pedestal scales the pedestal."),),
    outputs=(StreamPort("out", BAYER,
                        "Balanced Bayer, saturated to the datapath range."),),
    consumes=("bayer_phase",),
    context=(
        ContextBit("phase_row", "bayer_phase", bit=1,
                   description="Row parity R sits on, from the pipeline's "
                               "bayer_phase context register -- the same two "
                               "bits every CFA-indexed block consumes"),
        ContextBit("phase_col", "bayer_phase", bit=0,
                   description="Column parity R sits on, from bayer_phase"),
    ),
    params=[
        Param(
            name="gain",
            bits=16,
            frac=8,
            shape=(2, 2),
            labels=(("R", "Gr"), ("Gb", "B")),
            default_unity=True,
            configurable=("bits", "frac"),
            description=(
                "White balance gain, unsigned fixed point (reset is unity; "
                "see this register's q_format for the scale), indexed by CFA "
                "colour [R, Gr; Gb, B]. Multiplied then truncated by frac "
                "bits, saturated to full scale. Written by the "
                "AWB loop; convention holds green at unity and scales R and B. "
                "Reset 256 is a pass-through, so an unconfigured pipeline "
                "shows the illuminant's cast rather than a wrong image"
            ),
        ),
    ],
)
def whitebalance(pixel, p, ctx, bit_depth: int):
    """THE model. Four CFA planes, each scaled by its own colour's gain.

    The same shape as blacklevel with the adder replaced by a multiplier:
    stride-2 slices are CFA planes, their register-valued starts make the
    orientation programmable, and np2hw lowers the four planes to one
    datapath with a position-selected coefficient.

    The accumulator is ``uint32``: a 12-bit pixel times a 16-bit gain needs
    28 bits, and everything in this block is non-negative, so the datapath
    never grows the sign bit that cost blacklevel its midpoint. np2hw sizes
    the multiplier from the traced range, not from 32.
    """
    value = pixel.astype(np.uint32)
    out = np.empty_like(value)
    top = (1 << bit_depth) - 1
    # The shift comes from the CONFIGURED declaration -- the same Param the
    # register map derives q_format from -- so a design that overrides frac
    # changes the model, the RTL and the documentation together, or not at
    # all. This is a Python int at trace time, exactly like bit_depth.
    one = 1 << p.decl.gain.frac
    for i, rows in enumerate((ctx.phase_row, 1 - ctx.phase_row)):
        for j, cols in enumerate((ctx.phase_col, 1 - ctx.phase_col)):
            # (i, j) indexes the COLOUR: [R, Gr; Gb, B]. The phase decides
            # which positions that colour occupies.
            out[rows::2, cols::2] = (
                (value[rows::2, cols::2] * p.gain[i, j]) // one
            ).clip(0, top)
    return out.astype(np.uint16)


# Unity for the BASE declaration, derived from it rather than declared beside
# it. A variant's unity is `1 << variant.params.declaration("gain").frac`.
GAIN_ONE = whitebalance.params.declaration("gain").default


def registers_from_gains(r: int, b: int, g: int = GAIN_ONE) -> dict[str, int]:
    """AWB loop output -> register values, keyed by register name.

    The loop reasons in three colours; the block holds four registers. What
    this states -- and ALL it states -- is the convention: green at unity,
    both greens equal, R and B scaled relative to them. The register names,
    the tile shape and the representable range come from the declaration,
    via :meth:`revela.params.Param.values`.

    Args:
        r, b, g: gains in the BASE declaration's Q8.8, as
            :mod:`revela.control.awb` produces them. A variant with a
            different frac scales its own values; this helper serves the
            base convention the loop speaks.
    """
    return whitebalance.params.declaration("gain").values(
        [[int(r), int(g)], [int(g), int(b)]])
