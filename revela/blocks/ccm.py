# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Colour correction matrix: map sensor RGB into a standard colour space.

A sensor's colour filters are not the CIE observer's. Even perfectly white
balanced, sensor RGB is not sRGB: saturated colours land in the wrong place
and skin tones look wrong. A 3x3 matrix maps one to the other.

The arithmetic, exactly as the hardware does it
-----------------------------------------------

    out[c] = clip((sum_k m[c][k] * in[k] + half) >> frac, 0, 2**bit_depth - 1)

Nine signed Q2.8 coefficients (see the register's q_format for a variant's
scale). ``half`` is ``(1 << frac) >> 1``, added before the shift: this block
ROUNDS where whitebalance truncates, deliberately. A truncating gain sits
under an AWB loop that integrates its bias away; a matrix has no loop behind
it, and truncating all three channels biases every pixel half an LSB toward
dark -- and, because the green row usually carries the largest coefficients,
toward green. One adder buys that off.

Rows conventionally sum to unity (256 in Q2.8) so neutral stays neutral. The
block does NOT enforce that: a deliberately non-unity row is how a global
gain or a creative look is applied, and enforcing the convention would
remove a control that is genuinely wanted.

The stream, three channels in one word
--------------------------------------

This is the first block whose pixel is not one value. The RGB stream packs
its channels into one data word, R in the low bits -- the same convention
:meth:`revela.stream.StreamSpec.pack` states -- and the model unpacks,
mixes and repacks exactly as the wire carries them. The model IS the wire
format; there is no per-channel view that exists only in Python.

Where the values come from
--------------------------

CRITICAL: a specific unit's CCM is CALIBRATION OUTPUT, measured against a
colour chart under known illuminants, and it does NOT live in this repo.
Typically two matrices are calibrated and interpolated by correlated colour
temperature at runtime -- host work, upstream of these registers; the
resulting matrix becomes writes via the declaration itself,
``ccm.params.declaration("m").values(matrix)``, so no helper here restates
the register names or the shape. The reset
is the identity matrix, so an uncalibrated pipeline shows the sensor's
colours rather than wrong ones.

Ordering: after demosaic (it needs three channels per pixel), before gamma
(it is a linear-light operation). No example design carries it yet -- the
Bayer chain has no demosaic to feed it -- so it ships as a proven block
awaiting its seat, like ``stats``.
"""
from __future__ import annotations

import numpy as np

from revela.blocks import RGB, StreamPort, ispblock
from revela.params import Param


@ispblock(
    version=(1, 0),
    description="3x3 colour correction matrix, signed Q2.8, rounding, "
                "with saturation.",
    inputs=(StreamPort("in", RGB,
                       "Linear RGB, white balanced and demosaiced -- the "
                       "matrix is calibrated against balanced input, and "
                       "applying it to gamma-encoded values mixes a "
                       "nonlinearity into a linear correction."),),
    outputs=(StreamPort("out", RGB,
                        "Corrected RGB, each channel saturated to the "
                        "datapath range."),),
    params=[
        Param(
            name="m",
            bits=11,
            frac=8,
            signed=True,
            shape=(3, 3),
            labels=(("R'<-R", "R'<-G", "R'<-B"),
                    ("G'<-R", "G'<-G", "G'<-B"),
                    ("B'<-R", "B'<-G", "B'<-B")),
            default_identity=True,
            configurable=("bits", "frac"),
            description=(
                "Colour matrix coefficient, signed fixed point (see this "
                "register's q_format), row = output channel, column = input "
                "channel. Each output is the row's dot product with the "
                "input pixel, rounded by half an LSB then shifted, "
                "saturated to full scale. Calibration output, interpolated "
                "by colour temperature on the host. Reset is the identity "
                "matrix, so an uncalibrated pipeline passes sensor RGB "
                "through unchanged"
            ),
        ),
    ],
)
def ccm(pixel, p, ctx, bit_depth: int):
    """THE model. Unpack three channels, one dot product per output, repack.

    ``pixel`` is the packed data word: R in the low ``bit_depth`` bits, then
    G, then B. Unpacking is shift-and-mask, repacking is multiply-by-power-
    of-two and add -- all of it hardware the tracer sizes from exact ranges,
    with the nine coefficients entering the expression as register ports.

    The shift and the rounding constant come from the CONFIGURED
    declaration: a design that overrides ``frac`` changes the model, the
    RTL and the register map together, or not at all.
    """
    value = pixel.astype(np.int64)
    top = (1 << bit_depth) - 1
    frac = p.decl.m.frac
    half = (1 << frac) >> 1          # 0 when frac is 0: nothing to round
    channels = (value & top,
                (value >> bit_depth) & top,
                (value >> (2 * bit_depth)) & top)
    packed = None
    for row in range(3):
        acc = (channels[0] * p.m[row, 0]
               + channels[1] * p.m[row, 1]
               + channels[2] * p.m[row, 2])
        out = ((acc + half) // (1 << frac)).clip(0, top)
        shifted = out if row == 0 else out * (1 << (row * bit_depth))
        packed = shifted if packed is None else packed + shifted
    return packed.astype(np.uint64)
