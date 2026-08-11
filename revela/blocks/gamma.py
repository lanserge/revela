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

``knots`` holds ``2**K + 1`` values, one bit WIDER than the datapath: the
identity ramp's top knot is ``2**bit_depth`` itself, one past full scale,
and storing it exactly is what makes reset a true pass-through (the classic
LUT off-by-one lives at the top segment). The declaration therefore requires
``bits == bit_depth + 1`` and the model REFUSES a mismatch, naming the
override to write -- a 10-bit design says ``{"knots": {"bits": 11}}``.

At reset the table IS the identity ramp (``default_ramp``), so an
unconfigured pipeline passes the image through untouched, whatever the knot
count -- the same bring-up argument as blacklevel's zero offsets and
whitebalance's unity gains.

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

from revela.blocks import StreamPort, ispblock
from revela.params import Param

# 33 knots = 32 segments: the classic budget. A design overrides shape for
# more shadow fidelity, and bits alongside bit_depth -- see the docstring.
KNOTS_DEFAULT = 33


def _knots_param() -> Param:
    """ONE declaration of the knot register, used by both curve blocks."""
    return Param(
        name="knots",
        bits=13,                       # bit_depth + 1; see the docstring
        shape=(KNOTS_DEFAULT,),
        default_ramp=True,
        configurable=("bits", "shape"),
        description=(
            "Tone curve knots at uniform input spacing: knot i is the "
            "output for input i * 2**S, and between knots the hardware "
            "interpolates linearly on the input's low S bits. One bit "
            "wider than the datapath so the identity ramp's top knot "
            "(full scale + 1) is exact. Reset is the identity ramp: an "
            "unconfigured pipeline passes the image through. Written by "
            "the host from a target curve via knots_from_curve()"
        ),
    )


def _curve(value, p, bit_depth):
    """The PWL machinery both blocks share: slice, gather, lerp, clip."""
    knots = p.knots
    count = p.decl.knots.shape[0]
    segments = count - 1
    if segments & (segments - 1):
        raise ValueError(
            f"gamma needs 2**k + 1 knots for a bit-slice segment index; "
            f"{count} knots is {segments} segments")
    if p.decl.knots.bits != bit_depth + 1:
        raise ValueError(
            f"knots are {p.decl.knots.bits}-bit but the datapath is "
            f"{bit_depth}-bit; declare knots one bit wider so the identity "
            f"ramp's top knot is exact -- this design wants "
            f'{{"knots": {{"bits": {bit_depth + 1}}}}}')
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
    return out.clip(0, (1 << bit_depth) - 1)


@ispblock(
    version=(1, 0),
    description="Piecewise-linear tone curve (gamma) over uniform segments.",
    inputs=(StreamPort("in",
                       "Linear samples. Classically this block sits post-CCM "
                       "on RGB; it is in the Bayer chain because that is the "
                       "chain that exists."),),
    outputs=(StreamPort("out",
                        "Tone-mapped samples, same width as the input."),),
    params=[_knots_param()],
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
