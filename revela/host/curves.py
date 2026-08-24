# Copyright 2026 Serge Rabyking
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
"""Tone curves -> gamma knot registers. Host-side, and float on purpose.

Float is banned in the DATAPATH, where it would be a second arithmetic; the
host designing a curve is exactly where it belongs. A curve is sampled at
the knot positions, quantised once, and written to the registers -- the
knots are the whole contract between the float world and the integer one.

The scale convention matches the block's reset ramp: an input fraction x
in [0, 1] maps to round(curve(x) * 2**out_bits), so curve(x) = x lands
every knot exactly on the ramp -- including the top knot at 2**out_bits
itself, which is why the registers are one bit wider than the output.

OUT_BITS, not the datapath width. The curve is where the pipeline stops
being linear light and becomes display values, so the knots are in the
units that LEAVE the block -- 8 bits for a display, by default. The input
width does not appear here at all: it chooses which knot a pixel lands
between, never what the knot is worth.
"""
from __future__ import annotations

import numpy as np


def knots_from_curve(curve, count: int = 33, out_bits: int = 8) -> np.ndarray:
    """Quantised knot VALUES for ``curve`` -- an array, not register writes.

    Sampling and quantisation are this module's business; naming is the
    declaration's. Bind the result with the instance's own declaration,
    ``gamma.params.declaration("knots").values(...)``, which also checks the
    count against the declared shape instead of trusting ``count`` here.

    Args:
        curve: a callable [0, 1] -> [0, 1]. Values are clipped to the range,
            because a register cannot hold an opinion outside it.
        count: knot count, matching the block instance's declared shape.
        out_bits: the depth the block OUTPUTS at -- the knot declaration's
            width minus one, since the top knot is full scale plus one.
    """
    scale = 1 << out_bits
    positions = np.linspace(0.0, 1.0, count)
    values = np.clip([float(curve(float(x))) for x in positions], 0.0, 1.0)
    return np.array([int(round(v * scale)) for v in values], dtype=np.int64)


def knots_from_table(x, y, count: int = 33, out_bits: int = 8) -> np.ndarray:
    """The same, from measured (x, y) samples in [0, 1] -- resampled onto the
    uniform knot positions with ``np.interp``, which is precisely where
    numpy's own linear interpolation belongs in this project: on the host,
    preparing the values the integer datapath will interpolate between."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    return knots_from_curve(lambda v: float(np.interp(v, x, y)),
                            count=count, out_bits=out_bits)


def srgb(x: float) -> float:
    """The sRGB opto-electronic transfer function."""
    return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055


def bt709(x: float) -> float:
    """The BT.709 OETF."""
    return 4.5 * x if x < 0.018 else 1.099 * x ** 0.45 - 0.099


def power(exponent: float):
    """A plain power law, e.g. power(1/2.2)."""
    return lambda x: x ** exponent
