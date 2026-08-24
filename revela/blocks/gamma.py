# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Gamma: encode linear light for a display, and shape the tone curve.

A display expects gamma-encoded values, and a straight power law wastes the
codes the eye cares about; every real pipeline applies some tone curve. The
hardware for "some curve" has been the same for decades: a piecewise-linear
lookup table -- knot registers, a segment picked by the input's top bits, a
linear interpolation on the bottom bits. Any monotone curve (or deliberately
non-monotone one: solarisation is a register write away) at a cost of one
table read pair, one multiply and one shift per pixel.

The arithmetic, exactly as the hardware does it
-----------------------------------------------

    seg  = pixel >> S              S = bit_depth - log2(knots - 1)
    frac = pixel & (2**S - 1)
    out  = knot[seg] + ((knot[seg+1] - knot[seg]) * frac) >> S

Uniform knot spacing, deliberately: the segment index is a bit-slice, no
comparators -- the same argument as the register map's aligned bases. The
classic objection is that gamma curves bend hardest near black where uniform
spacing is coarsest; the answer is the knot COUNT, which is a build-time
override (``"registers": {"knots": {"shape": [65]}}``) trading registers for
shadow fidelity per design, not a different block.

The knots and the datapath
--------------------------

Gamma is where the pipeline stops being linear light and becomes DISPLAY
values, and it is the last block that makes RGB. So it is also where the
datapath narrows: the wide linear signal the sensor chain carries -- headroom
for black level, white balance, demosaic and the colour matrix -- lands on the
display's 8 bits HERE, chosen by the curve, rather than being truncated
somewhere downstream. Truncation after a curve is a second, linear
quantisation applied on top of the one the curve already made; doing it in the
knots means the curve decides which levels survive.

The output depth therefore has ONE owner: the knot declaration. ``knots``
holds ``2**K + 1`` values, one bit WIDER than the OUTPUT, because the top
knot is ``2**out_bits`` itself -- one past full scale -- and storing it
exactly is what keeps the top segment honest (the classic LUT off-by-one
lives there). Default is ``bits = 9``, so gamma outputs 8 bits. A design
driving a deeper display says ``{"knots": {"bits": 11}}`` and gets 10-bit
output; nothing else states the number.

The INPUT depth is unrelated and stays whatever the chain traced: it picks
the segment (``S = bit_depth - log2(knots - 1)``), so a wider input buys
finer interpolation within the same table.

At reset the table is the ramp (``default_ramp``), which spans 0 to
``2**out_bits`` across the knots -- so an unconfigured pipeline passes the
image through, scaled to the output range and otherwise untouched. Same
bring-up argument as blacklevel's zero offsets and whitebalance's unity
gains: the picture is there before anything is configured.

Where the values come from
--------------------------

The host: :func:`revela.host.curves.knots_from_curve` samples any target
curve -- sRGB, BT.709, a plain power law -- and quantises it into these
registers, in float, legally, because float is banned in the DATAPATH, not
on the host. Curve design in float on a computer; curve application in
integers in silicon; the knots are the contract between them.

Ordering
--------

Classically gamma sits at the END, after demosaic and colour correction, on
RGB or luma -- applying it in the Bayer domain bakes a non-linearity under
the interpolation. It is declared Bayer here because the Bayer chain is the
chain that exists today, and a viewable image on a monitor (the FPGA demo)
needs display encoding wherever it can get it. When demosaic lands, this
block moves to its classical seat unchanged: nothing in a lookup table
cares what its samples mean.
"""
from __future__ import annotations

import numpy as np
from np2hw import saturate

from revela.blocks import StreamPort, ispblock
from revela.params import Param

# 33 knots = 32 segments: the classic budget. A design overrides shape for
# more shadow fidelity, and bits alongside bit_depth -- see the docstring.
KNOTS_DEFAULT = 33


def _knots_param() -> Param:
    """ONE declaration of the knot register, used by both curve blocks."""
    return Param(
        name="knots",
        bits=9,                        # out_bits + 1; see the docstring
        shape=(KNOTS_DEFAULT,),
        default_ramp=True,
        configurable=("bits", "shape"),
        description=(
            "Tone curve knots at uniform input spacing: knot i is the "
            "output for input i * 2**S, and between knots the hardware "
            "interpolates linearly on the input's low S bits. One bit "
            "wider than the OUTPUT so the ramp's top knot (full scale + "
            "1) is exact -- and it is this width that sets the output "
            "depth, 8 bits by default. Reset is the ramp: an unconfigured "
            "pipeline passes the image through, scaled. Written by "
            "the host from a target curve via knots_from_curve()"
        ),
    )


def _out_bits(paramset) -> int:
    """The output depth, read from the knot declaration that owns it.

    The same number ``_curve`` saturates to, reached from the declaration
    rather than restated, so a design that overrides the knot width moves
    the model and the hardware together."""
    for param in paramset.params:
        if param.name == "knots":
            return param.bits - 1
    raise ValueError("a curve block without a knots declaration")


def _curve(value, p, bit_depth):
    """The PWL machinery both blocks share: slice, gather, lerp, clip."""
    knots = p.knots
    count = p.decl.knots.shape[0]
    segments = count - 1
    if segments & (segments - 1):
        raise ValueError(
            f"gamma needs 2**k + 1 knots for a bit-slice segment index; "
            f"{count} knots is {segments} segments")
    # The knot width OWNS the output depth: one bit wider than what
    # leaves, so the top knot (full scale + 1) is storable exactly.
    out_bits = p.decl.knots.bits - 1
    if out_bits < 1:
        raise ValueError(
            f"knots are {p.decl.knots.bits}-bit, which leaves no output; "
            'knots are one bit wider than the output depth, so 8-bit '
            'display output wants {"knots": {"bits": 9}}')
    if out_bits > bit_depth:
        raise ValueError(
            f"knots are {p.decl.knots.bits}-bit, asking for {out_bits}-bit "
            f"output from a {bit_depth}-bit input; a curve shapes the levels "
            "it was given and cannot invent precision")
    shift = bit_depth - (segments.bit_length() - 1)
    if shift <= 0:
        raise ValueError(
            f"{count} knots means {segments} segments, more than a "
            f"{bit_depth}-bit input has values; reduce the knot count")
    seg = value >> shift
    frac = value & ((1 << shift) - 1)
    base = knots[seg].astype(np.int32)
    step = knots[seg + 1].astype(np.int32) - base
    out = base + ((step * frac) >> shift)
    return saturate(out, out_bits)


@ispblock(
    version=(1, 0),
    description="Piecewise-linear tone curve (gamma) over uniform segments.",
    inputs=(StreamPort("in",
                       "Linear samples. Classically this block sits post-CCM "
                       "on RGB; it is in the Bayer chain because that is the "
                       "chain that exists."),),
    outputs=(StreamPort("out",
                        "Display-encoded samples at the output depth the "
                        "knot declaration sets -- 8 bits by default, because "
                        "this is the block that makes RGB for a display."),),
    params=[_knots_param()],
    # The output depth, from the SAME declaration the arithmetic reads:
    # knots are one bit wider than what leaves. Stated here so the model
    # chain narrows exactly where the hardware does.
    out_depth=lambda bit_depth, paramset: _out_bits(paramset),
)
def gamma(pixel, p, ctx, bit_depth: int):
    """THE model. Segment by bit-slice, gather two knots, integer lerp.

    Plain NumPy both ways: on arrays the fancy index is a fancy index; traced,
    it is a register-array gather whose index range np2hw proves inside the
    table. The knot count and the shift both come from the CONFIGURED
    declaration, so a design that overrides the shape changes the model, the
    RTL and the map together -- there is no second copy of K anywhere.
    """
    value = pixel.astype(np.int32)
    return _curve(value, p, bit_depth).astype(np.uint16)



@ispblock(
    version=(1, 0),
    description="Piecewise-linear tone curve applied per RGB channel, "
                "one shared knot table.",
    inputs=(StreamPort("in",
                       "Linear RGB, post-CCM -- the classical seat for the "
                       "display curve."),),
    outputs=(StreamPort("out",
                        "Tone-mapped RGB, same width per channel."),),
    params=[_knots_param()],
    # The output depth, from the SAME declaration the arithmetic reads:
    # knots are one bit wider than what leaves. Stated here so the model
    # chain narrows exactly where the hardware does.
    out_depth=lambda bit_depth, paramset: _out_bits(paramset),
)
def rgb_gamma(pixel, p, ctx, bit_depth: int):
    """THE model. The same curve, once per channel, one knot table.

    One table for all three channels is the classical display-gamma
    choice (per-channel tables are a colour-cast instrument, a different
    block). The channels view hands each lane through the shared PWL
    machinery; np.stack says three channels the NumPy way.
    """
    value = pixel.astype(np.int32)
    return np.stack([_curve(value[..., k], p, bit_depth) for k in range(3)],
                    axis=-1).astype(np.uint16)
